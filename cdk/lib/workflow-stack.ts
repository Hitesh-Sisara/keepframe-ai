import * as path from 'node:path';
import * as cdk from 'aws-cdk-lib';
import * as iam from 'aws-cdk-lib/aws-iam';
import * as lambda from 'aws-cdk-lib/aws-lambda';
import * as logs from 'aws-cdk-lib/aws-logs';
import * as sns from 'aws-cdk-lib/aws-sns';
import * as sfn from 'aws-cdk-lib/aws-stepfunctions';
import * as tasks from 'aws-cdk-lib/aws-stepfunctions-tasks';
import * as ecs from 'aws-cdk-lib/aws-ecs';
import { NagSuppressions } from './nag';
import { Construct } from 'constructs';
import { NAMES, PROJECT } from './config';
import { ComputeStack } from './compute-stack';
import { DataStack, grantList, grantObjects } from './data-stack';
import { NetworkStack } from './network-stack';

export interface WorkflowStackProps extends cdk.StackProps {
  network: NetworkStack;
  data: DataStack;
  compute: ComputeStack;
  /** AgentCoreStack runtime ARN (cross-stack); scopes the invoke grant. */
  agentRuntimeArn: string;
}

/** Total agent loop budget (start → last status poll), as with the ECS task timeout before AgentCore. */
const AGENT_BUDGET = cdk.Duration.hours(2);

/**
 * docs/ARCHITECTURE.md "Components": Step Functions Standard `CullEvent`.
 *
 *   input {event_id, run_id, flavor?: stock, sync_source?: bool, skip_originals?: bool, skip_agent?: bool}
 *   The photographer uploads the event's photos to s3://<work bucket>/raw/<eventId>/ first.
 *   BuildCommands (Pass)
 *   → SyncSource   (optional, ECS RunTask .sync, Fargate ARM64 cull-sync: import from any S3-compatible
 *                   store; runs only when sync_source is true and a manifest already exists)
 *   → Ingest       (ECS RunTask .sync, Fargate ARM64 cull-ingest: raw/ → exif/ sidecars + manifest,
 *                   a no-op when the manifest exists)
 *   → ValidateManifest (Lambda arm64)
 *   → CullWorkerStock (ECS RunTask .sync, Fargate ARM64 cull-worker)
 *   → PlanOriginals (Lambda): originals for group members (size ≥ 2) + needs_approval photos
 *   → SyncOriginals (ECS RunTask .sync, cull-sync SYNC_KINDS=originals)    [skipped: skip_originals / 0 with original_key]
 *   → RunAgent (Lambda invoke_agent: InvokeAgentRuntime mode=start, wait=false)   [skipped when skip_agent]
 *       → WaitForAgent 30 s → AgentStatus (Lambda, mode=status) → AgentDone?
 *           running → WaitForAgent; paused|done → Summarise; error → MarkRunFailed
 *           (2 h budget enforced by agent_status; a stuck runtime fails the run)
 *   → Summarise    (Lambda arm64) → Notify (SNS) → Succeed
 *   Catch on every step → MarkRunFailed (Lambda arm64) → Notify → Fail
 *
 * Human-in-the-loop: when the agent hits an approval interrupt it persists its session to
 * s3://…/sessions/<runId>.json and sets RUN# status `awaiting_approval`; status polls then report `paused`
 * and this execution finishes. POST /approvals/{id} on the API resumes it via InvokeAgentRuntime
 * mode=resume on the same session id.
 */
export class WorkflowStack extends cdk.Stack {
  readonly stateMachine: sfn.StateMachine;
  readonly topic: sns.Topic;
  readonly functions: lambda.IFunction[] = [];

  constructor(scope: Construct, id: string, props: WorkflowStackProps) {
    super(scope, id, props);
    const { network, data, compute, agentRuntimeArn } = props;
    const { decisions } = data;

    this.topic = new sns.Topic(this, 'Alerts', { topicName: NAMES.alertsTopic, displayName: 'KeepFrame run events and alarms', enforceSSL: true });

    // ---------------- Lambdas (Python 3.12, ARM_64, zip) ----------------
    const code = lambda.Code.fromAsset(path.join(__dirname, '..', 'lambdas', 'workflow'), {
      exclude: ['__pycache__', '*.pyc', 'test_*'],
    });
    const fn = (name: string, handler: string, description: string, timeout = 60) => {
      const logGroup = new logs.LogGroup(this, `${name}Logs`, {
        logGroupName: `/aws/lambda/${PROJECT}-${name}`,
        retention: logs.RetentionDays.ONE_MONTH,
        removalPolicy: cdk.RemovalPolicy.DESTROY,
      });
      const role = new iam.Role(this, `${name}Role`, { assumedBy: new iam.ServicePrincipal('lambda.amazonaws.com') });
      role.addToPolicy(new iam.PolicyStatement({ actions: ['logs:CreateLogStream', 'logs:PutLogEvents'], resources: [logGroup.logGroupArn, `${logGroup.logGroupArn}:*`] }));
      role.addToPolicy(new iam.PolicyStatement({ actions: ['dynamodb:UpdateItem', 'dynamodb:GetItem'], resources: [decisions.tableArn] }));
      const f = new lambda.Function(this, name, {
        functionName: `${PROJECT}-${name}`,
        description,
        runtime: lambda.Runtime.PYTHON_3_12,
        architecture: lambda.Architecture.ARM_64,
        handler,
        code,
        role,
        memorySize: 256,
        timeout: cdk.Duration.seconds(timeout),
        logGroup,
        tracing: lambda.Tracing.ACTIVE,
        environment: { WORK_BUCKET: data.bucketName, CULL_DECISIONS_TABLE: decisions.tableName },
      });
      this.functions.push(f);
      return { f, role };
    };
    const validate = fn('validate-manifest', 'validate_manifest.handler', 'CullEvent: check manifest exists, mark run running', 120);
    grantObjects(data.bucketName, validate.role, ['s3:GetObject'], ['manifests/*']);
    grantList(data.bucketName, validate.role, ['manifests/*']);
    // Placeholder check only (values are compared, never logged): the optional Anthropic-compatible provider's key.
    data.anthropicSecret.grantRead(validate.role);
    validate.role.addToPolicy(new iam.PolicyStatement({
      actions: ['ssm:GetParameter'],
      resources: [`arn:aws:ssm:${this.region}:${this.account}:parameter${NAMES.params.modelProvider}`],
    }));
    validate.f.addEnvironment('CULL_ANTHROPIC_SECRET_ID', NAMES.secrets.anthropic);
    validate.f.addEnvironment('CULL_MODEL_PROVIDER_PARAM', NAMES.params.modelProvider);
    const summarise = fn('summarise', 'summarise.handler', 'CullEvent: copy summary.json counts onto RUN#');
    grantObjects(data.bucketName, summarise.role, ['s3:GetObject'], ['results/*']);
    grantList(data.bucketName, summarise.role, ['results/*']);
    const markFailed = fn('mark-run-failed', 'mark_run_failed.handler', 'CullEvent Catch: mark RUN# failed');
    const planOriginals = fn('plan-originals', 'plan_originals.handler', 'CullEvent: list group/approval candidates with originals', 120);
    grantObjects(data.bucketName, planOriginals.role, ['s3:GetObject'], ['results/*', 'manifests/*']);
    grantObjects(data.bucketName, planOriginals.role, ['s3:PutObject'], ['originals-requests/*']);
    grantList(data.bucketName, planOriginals.role, ['results/*', 'manifests/*']);
    // AgentCore RunAgent loop. The runtime ARN is read from SSM at call time;
    // InvokeAgentRuntime is granted on exactly the CDK-managed runtime (and its endpoints).
    const agentLambda = (name: string, handler: string, description: string) => {
      const a = fn(name, handler, description, 90);
      a.role.addToPolicy(new iam.PolicyStatement({
        actions: ['ssm:GetParameter'],
        resources: [`arn:aws:ssm:${this.region}:${this.account}:parameter${NAMES.params.agentRuntimeArn}`],
      }));
      a.role.addToPolicy(new iam.PolicyStatement({
        actions: ['bedrock-agentcore:InvokeAgentRuntime'],
        resources: [agentRuntimeArn, `${agentRuntimeArn}/*`],
      }));
      a.f.addEnvironment('CULL_AGENT_RUNTIME_PARAM', NAMES.params.agentRuntimeArn);
      return a;
    };
    const invokeAgent = agentLambda('invoke-agent', 'invoke_agent.handler', 'CullEvent RunAgent: InvokeAgentRuntime mode=start (or select Fargate)');
    const agentStatus = agentLambda('agent-status', 'agent_status.handler', 'CullEvent AgentStatus: InvokeAgentRuntime mode=status');
    agentStatus.f.addEnvironment('CULL_AGENT_BUDGET_S', String(AGENT_BUDGET.toSeconds()));

    // ---------------- states ----------------
    const lambdaRetry = { errors: ['Lambda.ServiceException', 'Lambda.AWSLambdaException', 'Lambda.SdkClientException', 'Lambda.TooManyRequestsException'], interval: cdk.Duration.seconds(2), maxAttempts: 3, backoffRate: 2 };
    const ecsRetry = { errors: ['ECS.AmazonECSException', 'ECS.ServerException', 'ECS.ThrottlingException'], interval: cdk.Duration.seconds(20), maxAttempts: 3, backoffRate: 2 };

    const failed = new sfn.Fail(this, 'RunFailed', { error: 'CullEventFailed', causePath: '$.failure.Payload.error' });
    const notifyFailed = new tasks.SnsPublish(this, 'NotifyFailed', {
      topic: this.topic,
      subject: 'KeepFrame run failed',
      message: sfn.TaskInput.fromObject({ run_id: sfn.JsonPath.stringAt('$.run_id'), event_id: sfn.JsonPath.stringAt('$.event_id'), status: 'failed', error: sfn.JsonPath.stringAt('$.failure.Payload.error') }),
      resultPath: sfn.JsonPath.DISCARD,
    });
    const markRunFailed = new tasks.LambdaInvoke(this, 'MarkRunFailed', {
      lambdaFunction: markFailed.f,
      payload: sfn.TaskInput.fromObject({
        event_id: sfn.JsonPath.stringAt('$.event_id'),
        run_id: sfn.JsonPath.stringAt('$.run_id'),
        error: sfn.JsonPath.objectAt('$.error'),
      }),
      resultPath: '$.failure',
      retryOnServiceExceptions: true,
    });
    markRunFailed.next(notifyFailed).next(failed);
    const catchTo = <T extends sfn.TaskStateBase>(t: T): T => {
      t.addCatch(markRunFailed, { errors: ['States.ALL'], resultPath: '$.error' });
      return t;
    };

    const validateTask = catchTo(new tasks.LambdaInvoke(this, 'ValidateManifest', {
      lambdaFunction: validate.f,
      payload: sfn.TaskInput.fromJsonPathAt('$'),
      resultSelector: { 'manifest_rows.$': '$.Payload.manifest_rows' },
      resultPath: '$.validated',
      retryOnServiceExceptions: false,
    }).addRetry(lambdaRetry));

    const net = { subnets: { subnets: network.vpc.publicSubnets }, securityGroups: [network.egressOnlySg], assignPublicIp: true };
    const fargate = new tasks.EcsFargateLaunchTarget({ platformVersion: ecs.FargatePlatformVersion.VERSION1_4 });
    const env = (name: string, p: string) => ({ name, value: sfn.JsonPath.stringAt(p) });

    const syncTask = catchTo(new tasks.EcsRunTask(this, 'SyncSource', {
      integrationPattern: sfn.IntegrationPattern.RUN_JOB,
      cluster: compute.cluster,
      taskDefinition: compute.syncTask,
      launchTarget: fargate,
      ...net,
      containerOverrides: [{ containerDefinition: compute.syncTask.findContainer(compute.containers.sync)!, environment: [env('EVENT_ID', '$.event_id'), env('RUN_ID', '$.run_id')] }],
      propagatedTagSource: ecs.PropagatedTagSource.TASK_DEFINITION,
      taskTimeout: sfn.Timeout.duration(cdk.Duration.hours(1)),
      resultPath: sfn.JsonPath.DISCARD,
    }).addRetry(ecsRetry));

    // Container command overrides cannot mix JsonPath into literal arrays; build them once with States.Array.
    const buildCommands = new sfn.Pass(this, 'BuildCommands', {
      parameters: {
        'worker_stock.$': "States.Array('--event-id', $.event_id, '--run-id', $.run_id, '--flavor', 'stock')",
        'ingest.$': "States.Array('--event-id', $.event_id)",
      },
      resultPath: '$.cmd',
    });
    // Ingest: the photographer's upload in raw/<eventId>/ -> exif/ sidecars + manifests/<eventId>.jsonl.
    const ingestTask = catchTo(new tasks.EcsRunTask(this, 'Ingest', {
      integrationPattern: sfn.IntegrationPattern.RUN_JOB,
      cluster: compute.cluster,
      taskDefinition: compute.ingestTask,
      launchTarget: fargate,
      ...net,
      containerOverrides: [{ containerDefinition: compute.ingestTask.findContainer(compute.containers.ingest)!, command: sfn.JsonPath.listAt('$.cmd.ingest') }],
      propagatedTagSource: ecs.PropagatedTagSource.TASK_DEFINITION,
      taskTimeout: sfn.Timeout.duration(cdk.Duration.hours(1)),
      resultPath: sfn.JsonPath.DISCARD,
    }).addRetry(ecsRetry));
    const stockWorker = catchTo(new tasks.EcsRunTask(this, 'CullWorkerStock', {
      integrationPattern: sfn.IntegrationPattern.RUN_JOB,
      cluster: compute.cluster,
      taskDefinition: compute.workerTask,
      launchTarget: fargate,
      ...net,
      containerOverrides: [{ containerDefinition: compute.workerTask.findContainer(compute.containers.worker)!, command: sfn.JsonPath.listAt('$.cmd.worker_stock'), environment: [env('CULL_RUN_ID', '$.run_id')] }],
      propagatedTagSource: ecs.PropagatedTagSource.TASK_DEFINITION,
      taskTimeout: sfn.Timeout.duration(cdk.Duration.hours(2)),
      resultPath: sfn.JsonPath.DISCARD,
    }).addRetry(ecsRetry));
    // InvokeAgentRuntime throttling, a cold session or a dropped stream surface as these Lambda error types (the
    // boto3 exception class name). AgentError (status=error), AgentTimeout and bad input are not retried.
    const agentTransientRetry = {
      errors: ['ThrottlingException', 'ServiceQuotaExceededException', 'InternalServerException', 'RuntimeClientError',
        'ReadTimeoutError', 'ConnectTimeoutError', 'EndpointConnectionError', 'Sandbox.Timedout'],
      interval: cdk.Duration.seconds(15), maxAttempts: 3, backoffRate: 2,
    };
    const runAgentTask = catchTo(new tasks.LambdaInvoke(this, 'RunAgent', {
      lambdaFunction: invokeAgent.f,
      payload: sfn.TaskInput.fromObject({ event_id: sfn.JsonPath.stringAt('$.event_id'), run_id: sfn.JsonPath.stringAt('$.run_id') }),
      resultSelector: { 'result.$': '$.Payload' },
      resultPath: '$.agent',
      retryOnServiceExceptions: false,
    }).addRetry(lambdaRetry).addRetry(agentTransientRetry));
    const agentStatusTask = catchTo(new tasks.LambdaInvoke(this, 'AgentStatus', {
      lambdaFunction: agentStatus.f,
      payload: sfn.TaskInput.fromObject({
        event_id: sfn.JsonPath.stringAt('$.event_id'),
        run_id: sfn.JsonPath.stringAt('$.run_id'),
        session_id: sfn.JsonPath.stringAt('$.agent.result.session_id'),
        started_epoch: sfn.JsonPath.numberAt('$.agent.result.started_epoch'),
      }),
      resultSelector: { 'result.$': '$.Payload' },
      resultPath: '$.agent_status',
      retryOnServiceExceptions: false,
    }).addRetry(lambdaRetry).addRetry(agentTransientRetry));
    const waitForAgent = new sfn.Wait(this, 'WaitForAgent', { time: sfn.WaitTime.duration(cdk.Duration.seconds(30)) });
    const agentErrored = new sfn.Pass(this, 'AgentErrored', {
      parameters: {
        Error: 'AgentError',
        'Cause.$': "States.Format('agent reported error: {}', $.agent_status.result.error)",
      },
      resultPath: '$.error',
    });
    agentErrored.next(markRunFailed);

    // Second sync pass (docs/ARCHITECTURE.md "Components"): originals for group members (size >= 2) and needs_approval photos only.
    const planOriginalsTask = catchTo(new tasks.LambdaInvoke(this, 'PlanOriginals', {
      lambdaFunction: planOriginals.f,
      payload: sfn.TaskInput.fromObject({ event_id: sfn.JsonPath.stringAt('$.event_id'), run_id: sfn.JsonPath.stringAt('$.run_id') }),
      resultSelector: { 'count.$': '$.Payload.count', 'candidates.$': '$.Payload.candidates', 'photo_ids_key.$': '$.Payload.photo_ids_key' },
      resultPath: '$.originals',
      retryOnServiceExceptions: false,
    }).addRetry(lambdaRetry));
    const syncOriginalsTask = catchTo(new tasks.EcsRunTask(this, 'SyncOriginals', {
      integrationPattern: sfn.IntegrationPattern.RUN_JOB,
      cluster: compute.cluster,
      taskDefinition: compute.syncTask,
      launchTarget: fargate,
      ...net,
      containerOverrides: [{
        containerDefinition: compute.syncTask.findContainer(compute.containers.sync)!,
        environment: [
          env('EVENT_ID', '$.event_id'), env('RUN_ID', '$.run_id'),
          { name: 'SYNC_KINDS', value: 'originals' },
          env('PHOTO_IDS_S3', '$.originals.photo_ids_key'),
        ],
      }],
      propagatedTagSource: ecs.PropagatedTagSource.TASK_DEFINITION,
      taskTimeout: sfn.Timeout.duration(cdk.Duration.hours(1)),
      resultPath: sfn.JsonPath.DISCARD,
    }).addRetry(ecsRetry));

    const summariseTask = catchTo(new tasks.LambdaInvoke(this, 'Summarise', {
      lambdaFunction: summarise.f,
      payload: sfn.TaskInput.fromObject({ event_id: sfn.JsonPath.stringAt('$.event_id'), run_id: sfn.JsonPath.stringAt('$.run_id') }),
      resultSelector: { 'status.$': '$.Payload.status', 'verdict_counts.$': '$.Payload.verdict_counts' },
      resultPath: '$.summary',
      retryOnServiceExceptions: false,
    }).addRetry(lambdaRetry));
    const notifyDone = new tasks.SnsPublish(this, 'Notify', {
      topic: this.topic,
      subject: 'KeepFrame run finished',
      message: sfn.TaskInput.fromObject({ run_id: sfn.JsonPath.stringAt('$.run_id'), event_id: sfn.JsonPath.stringAt('$.event_id'), status: sfn.JsonPath.stringAt('$.summary.status') }),
      resultPath: sfn.JsonPath.DISCARD,
    });
    const done = new sfn.Succeed(this, 'Done');

    const summaryChain = summariseTask.next(notifyDone).next(done);
    const agentDone = new sfn.Choice(this, 'AgentDone?')
      .when(sfn.Condition.stringEquals('$.agent_status.result.status', 'running'), waitForAgent)
      .when(sfn.Condition.stringEquals('$.agent_status.result.status', 'error'), agentErrored)
      .otherwise(summaryChain);
    waitForAgent.next(agentStatusTask).next(agentDone);
    const agentHost = new sfn.Choice(this, 'AgentStarted?')
      .when(sfn.Condition.stringEquals('$.agent.result.status', 'running'), waitForAgent)
      .otherwise(summaryChain);
    const agentChoice = new sfn.Choice(this, 'SkipAgent?')
      .when(sfn.Condition.and(sfn.Condition.isPresent('$.skip_agent'), sfn.Condition.booleanEquals('$.skip_agent', true)), summaryChain)
      .otherwise(runAgentTask.next(agentHost));
    const anyOriginals = new sfn.Choice(this, 'AnyOriginals?')
      .when(sfn.Condition.numberGreaterThan('$.originals.count', 0), syncOriginalsTask.next(agentChoice))
      .otherwise(agentChoice);
    const originalsChoice = new sfn.Choice(this, 'SkipOriginals?')
      .when(sfn.Condition.and(sfn.Condition.isPresent('$.skip_originals'), sfn.Condition.booleanEquals('$.skip_originals', true)), agentChoice)
      .otherwise(planOriginalsTask.next(anyOriginals));
    const ingestChain = ingestTask.next(validateTask).next(stockWorker).next(originalsChoice);
    const syncChoice = new sfn.Choice(this, 'SyncSource?')
      .when(sfn.Condition.and(sfn.Condition.isPresent('$.sync_source'), sfn.Condition.booleanEquals('$.sync_source', true)), syncTask.next(ingestChain))
      .otherwise(ingestChain);

    const smLogs = new logs.LogGroup(this, 'StateMachineLogs', {
      logGroupName: `/aws/vendedlogs/states/${NAMES.stateMachine}`,
      retention: logs.RetentionDays.ONE_MONTH,
      removalPolicy: cdk.RemovalPolicy.DESTROY,
    });
    this.stateMachine = new sfn.StateMachine(this, 'CullEvent', {
      stateMachineName: NAMES.stateMachine,
      stateMachineType: sfn.StateMachineType.STANDARD,
      definitionBody: sfn.DefinitionBody.fromChainable(buildCommands.next(syncChoice)),
      timeout: cdk.Duration.hours(6),
      tracingEnabled: true,
      logs: { destination: smLogs, level: sfn.LogLevel.ALL, includeExecutionData: false },
    });

    // EcsRunTask grants the exact revision it was synthesised with, but Step Functions passes the family, so
    // ECS runs the latest ACTIVE revision. Grant each family (any revision) so a new revision registered by a
    // ComputeStack deploy or by hand never breaks a run between WorkflowStack deploys.
    this.stateMachine.addToRolePolicy(new iam.PolicyStatement({
      actions: ['ecs:RunTask'],
      resources: [NAMES.families.sync, NAMES.families.ingest, NAMES.families.worker]
        .map((f) => `arn:aws:ecs:${this.region}:${this.account}:task-definition/${f}:*`),
      conditions: { ArnEquals: { 'ecs:cluster': `arn:aws:ecs:${this.region}:${this.account}:cluster/${NAMES.cluster}` } },
    }));

    new cdk.CfnOutput(this, 'StateMachineArn', { value: this.stateMachine.stateMachineArn });
    new cdk.CfnOutput(this, 'AlertsTopicArn', { value: this.topic.topicArn });

    NagSuppressions.addResourceSuppressions(this.topic, [
      { id: 'AwsSolutions-SNS2', reason: 'Messages carry run ids and status only (no customer data); CloudWatch alarms cannot publish to a topic encrypted with the AWS-managed SNS key.' },
    ]);
    NagSuppressions.addResourceSuppressions(this, [
      { id: 'AwsSolutions-L1', reason: 'The project pins Python 3.12 for every Lambda (same runtime as the worker and API images).' },
      { id: 'AwsSolutions-IAM5', reason: 'Object grants are scoped to one bucket prefix; X-Ray, ECS DescribeTasks/StopTask and log-delivery actions have no resource scoping; lambda :* covers function versions.' },
    ], true);
  }
}
