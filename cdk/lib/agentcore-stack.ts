import * as cdk from 'aws-cdk-lib';
import * as agentcore from 'aws-cdk-lib/aws-bedrockagentcore';
import * as iam from 'aws-cdk-lib/aws-iam';
import * as logs from 'aws-cdk-lib/aws-logs';
import * as ssm from 'aws-cdk-lib/aws-ssm';
import * as cr from 'aws-cdk-lib/custom-resources';
import { Construct } from 'constructs';
import { forceArm64 } from './arm64';
import { bedrockModelArns, NAMES, ProjectContext } from './config';
import { DataStack, grantList, grantObjects, suppressCustomResourceProvider } from './data-stack';
import { NagSuppressions } from './nag';

/** Work-bucket prefixes the agent reads / writes (keepframe/agent, keepframe/tools, keepframe/io). */
export const AGENT_S3_READ = ['manifests/', 'raw/', 'exif/', 'originals/', 'proxy/', 'results/', 'sessions/', 'sheets/', 'preview/'];
export const AGENT_S3_WRITE = ['proxy/', 'results/', 'sessions/', 'sheets/', 'preview/'];

export interface AgentCoreStackProps extends cdk.StackProps {
  ctx: ProjectContext;
  data: DataStack;
}

/**
 * Agent hosting (docs/AGENT.md "AgentCore Runtime"): the Strands agent on Amazon Bedrock AgentCore Runtime (ARM64 container from ECR
 * `keepframe-agent:<imageTag>`, HTTP protocol `/invocations` + `/ping`, PUBLIC network mode).
 *
 * Model provider (context `llmProvider`): `bedrock` (default) calls Claude on Amazon Bedrock with this role,
 * granted bedrock:InvokeModel(+WithResponseStream) on exactly the configured inference profiles and the
 * foundation models they route to. `anthropic` (optional, any Anthropic-compatible endpoint): the runtime has no
 * secret injection, so `environmentVariables` carry names and ids only and the container reads
 * `keepframe/anthropic` itself at startup (`CULL_ANTHROPIC_SECRET_ID`, services/agent_runtime/app.py).
 * This stack owns SSM `/keepframe/agent-runtime-arn` (the runtime ARN), which the CullEvent `RunAgent` Lambdas
 * read; the API gets the ARN as a cross-stack value. The Fargate `cull-agent` fallback was removed 2026-10-08.
 */
export class AgentCoreStack extends cdk.Stack {
  readonly runtime: agentcore.CfnRuntime;
  readonly role: iam.Role;
  /** Exact runtime ARN (cross-stack). Invoke grants use it plus `/*` for the runtime's endpoints. */
  readonly runtimeArn: string;

  constructor(scope: Construct, id: string, props: AgentCoreStackProps) {
    super(scope, id, props);
    const { ctx, data } = props;
    const { decisions, traces } = data;
    const arn = (service: string, resource: string) => `arn:aws:${service}:${this.region}:${this.account}:${resource}`;

    this.role = new iam.Role(this, 'AgentRuntimeRole', {
      description: `KeepFrame agent on AgentCore Runtime: ECR pull, logs, X-Ray, worker grants, model provider ${ctx.llmProvider}`,
      assumedBy: new iam.ServicePrincipal('bedrock-agentcore.amazonaws.com', {
        conditions: {
          StringEquals: { 'aws:SourceAccount': this.account },
          ArnLike: { 'aws:SourceArn': arn('bedrock-agentcore', '*') },
        },
      }),
    });
    const role = this.role;
    // Image pull
    role.addToPolicy(new iam.PolicyStatement({
      actions: ['ecr:BatchGetImage', 'ecr:GetDownloadUrlForLayer'],
      resources: [arn('ecr', `repository/${NAMES.repos.agent}`)],
    }));
    role.addToPolicy(new iam.PolicyStatement({ actions: ['ecr:GetAuthorizationToken'], resources: ['*'] }));
    // Runtime logs: /aws/bedrock-agentcore/runtimes/<runtimeId>-<endpoint>
    const runtimeLogs = arn('logs', 'log-group:/aws/bedrock-agentcore/runtimes/*');
    role.addToPolicy(new iam.PolicyStatement({
      actions: ['logs:CreateLogGroup', 'logs:CreateLogStream', 'logs:PutLogEvents', 'logs:DescribeLogStreams'],
      resources: [runtimeLogs, `${runtimeLogs}:*`],
    }));
    role.addToPolicy(new iam.PolicyStatement({ actions: ['logs:DescribeLogGroups'], resources: [arn('logs', 'log-group:*')] }));
    role.addToPolicy(new iam.PolicyStatement({
      actions: ['xray:PutTraceSegments', 'xray:PutTelemetryRecords', 'xray:GetSamplingRules', 'xray:GetSamplingTargets'],
      resources: ['*'],
    }));
    role.addToPolicy(new iam.PolicyStatement({
      actions: ['cloudwatch:PutMetricData'],
      resources: ['*'],
      conditions: { StringEquals: { 'cloudwatch:namespace': [NAMES.metricNamespace, 'bedrock-agentcore'] } },
    }));
    role.addToPolicy(new iam.PolicyStatement({
      actions: ['bedrock-agentcore:GetWorkloadAccessToken', 'bedrock-agentcore:GetWorkloadAccessTokenForJWT', 'bedrock-agentcore:GetWorkloadAccessTokenForUserId'],
      resources: [
        arn('bedrock-agentcore', 'workload-identity-directory/default'),
        arn('bedrock-agentcore', `workload-identity-directory/default/workload-identity/${NAMES.agentRuntime}-*`),
      ],
    }));
    // S3 by prefix (SECURITY_REVIEW #9), from the agent's actual key use:
    //  read  manifests/ (ingest), raw/ exif/ originals/ (pixels, EXIF), proxy/ results/ sessions/ sheets/ preview/
    //  write proxy/ (load_p1024 uploads a missing proxy), results/ (decisions, trace, summaries, exif_index),
    //        sessions/ (resume state), sheets/ (contact sheets), preview/ (optional enhance_keeper)
    // No access to bench/, runs/, originals-requests/; no writes to manifests/ raw/ exif/ originals/.
    grantObjects(data.bucketName, role, ['s3:GetObject'], AGENT_S3_READ.map((p) => `${p}*`));
    grantObjects(data.bucketName, role, ['s3:PutObject'], AGENT_S3_WRITE.map((p) => `${p}*`));
    grantList(data.bucketName, role, AGENT_S3_READ.map((p) => `${p}*`));
    for (const t of [decisions, traces]) {
      role.addToPolicy(new iam.PolicyStatement({
        actions: ['dynamodb:PutItem', 'dynamodb:BatchWriteItem', 'dynamodb:UpdateItem', 'dynamodb:GetItem', 'dynamodb:Query'],
        resources: [t.tableArn, `${t.tableArn}/index/*`],
      }));
    }
    const provider = ctx.llmProvider;
    if (provider === 'bedrock') {
      // Claude on Amazon Bedrock: the planner and reasons models only (inference profile + routed foundation models).
      role.addToPolicy(new iam.PolicyStatement({
        actions: ['bedrock:InvokeModel', 'bedrock:InvokeModelWithResponseStream'],
        resources: [...new Set([ctx.bedrockModelId, ctx.bedrockReasonsModelId]
          .flatMap((m) => bedrockModelArns(this.account, this.region, m)))],
      }));
    } else if (provider === 'anthropic') {
      // Optional Anthropic-compatible endpoint: its key, read once at container start. This one secret only.
      role.addToPolicy(new iam.PolicyStatement({
        actions: ['secretsmanager:GetSecretValue'],
        resources: [arn('secretsmanager', `secret:${NAMES.secrets.anthropic}-??????`)],
      }));
    }

    const modelEnv: Record<string, string> = provider === 'bedrock'
      ? { KEEPFRAME_BEDROCK_MODEL_ID: ctx.bedrockModelId, KEEPFRAME_BEDROCK_REASONS_MODEL_ID: ctx.bedrockReasonsModelId, KEEPFRAME_BEDROCK_REGION: this.region }
      : provider === 'anthropic'
        ? {
          CULL_PLANNER_MODEL: ssm.StringParameter.valueForStringParameter(this, NAMES.params.plannerModel),
          CULL_REASONS_MODEL: ssm.StringParameter.valueForStringParameter(this, NAMES.params.reasonsModel),
          CULL_ANTHROPIC_SECRET_ID: NAMES.secrets.anthropic,
        }
        : {};
    this.runtime = new agentcore.CfnRuntime(this, 'AgentRuntime', {
      agentRuntimeName: NAMES.agentRuntime,
      description: `KeepFrame culling agent (Strands, model provider ${provider}), image keepframe-agent:${ctx.imageTag}`,
      roleArn: role.roleArn,
      // Literal URI (like DataStack.bucketName): no cross-stack export, works with adopted repos too.
      agentRuntimeArtifact: { containerConfiguration: { containerUri: `${this.account}.dkr.ecr.${this.region}.amazonaws.com/${NAMES.repos.agent}:${ctx.imageTag}` } },
      protocolConfiguration: 'HTTP',
      networkConfiguration: { networkMode: 'PUBLIC' },
      lifecycleConfiguration: { idleRuntimeSessionTimeout: 900, maxLifetime: 28800 },
      // Names and ids only. NEVER a secret value here .
      environmentVariables: {
        AWS_REGION: this.region,
        KEEPFRAME_LLM_PROVIDER: provider,
        ...modelEnv,
        CULL_TOOLS: 'real',
        WORK_BUCKET: data.bucketName,
        CULL_DECISIONS_TABLE: decisions.tableName,
        CULL_TRACES_TABLE: traces.tableName,
        // keepframe/store/tables.py reads the un-prefixed names.
        DECISIONS_TABLE: decisions.tableName,
        TRACES_TABLE: traces.tableName,
        CULL_METRICS_NAMESPACE: NAMES.metricNamespace,
        CULL_MODELS_DIR: '/opt/models',
        CULL_CACHE_DIR: '/tmp/cull',
        CULL_INSTANCE_TYPE: 'agentcore-arm64',
        CULL_AGENT_HOST: 'agentcore',
        // The agent stamps run_kind=deployed only under ECS or when this is set (keepframe/store/dynamodb.py).
        CULL_RUN_KIND: 'deployed',
        OPENCV_FORCE_DNN_ENGINE: '3',
      },
    });
    // The role's inline policy must exist before AgentCore validates the image pull.
    this.runtime.node.addDependency(role);
    this.runtimeArn = this.runtime.attrAgentRuntimeArn;

    new ssm.StringParameter(this, 'AgentRuntimeArnParam', {
      parameterName: NAMES.params.agentRuntimeArn,
      stringValue: this.runtime.attrAgentRuntimeArn,
      description: 'AgentCore runtime ARN for the KeepFrame agent (CullEvent RunAgent Lambdas read it)',
    });

    // Runtime log group: AgentCore creates /aws/bedrock-agentcore/runtimes/<runtimeId>-DEFAULT itself with no
    // retention (SECURITY_REVIEW #17). Create it if absent (fresh account: no invocation yet), then set 30 days.
    const runtimeLogGroup = `/aws/bedrock-agentcore/runtimes/${this.runtime.attrAgentRuntimeId}-DEFAULT`;
    const runtimeLogGroupArn = arn('logs', `log-group:/aws/bedrock-agentcore/runtimes/${NAMES.agentRuntime}-*`);
    const crLogs = new logs.LogGroup(this, 'RuntimeLogRetentionCrLogs', {
      retention: logs.RetentionDays.ONE_MONTH, removalPolicy: cdk.RemovalPolicy.DESTROY,
    });
    const ensureGroup = new cr.AwsCustomResource(this, 'RuntimeLogGroupEnsure', {
      onCreate: {
        service: 'CloudWatchLogs', action: 'createLogGroup', parameters: { logGroupName: runtimeLogGroup },
        physicalResourceId: cr.PhysicalResourceId.of(runtimeLogGroup),
        ignoreErrorCodesMatching: 'ResourceAlreadyExistsException',
      },
      policy: cr.AwsCustomResourcePolicy.fromStatements([
        new iam.PolicyStatement({ actions: ['logs:CreateLogGroup'], resources: [runtimeLogGroupArn] }),
      ]),
      installLatestAwsSdk: false,
      logGroup: crLogs,
    });
    const setRetention: cr.AwsSdkCall = {
      service: 'CloudWatchLogs', action: 'putRetentionPolicy',
      parameters: { logGroupName: runtimeLogGroup, retentionInDays: 30 },
      physicalResourceId: cr.PhysicalResourceId.of(`${runtimeLogGroup}-retention`),
    };
    const retention = new cr.AwsCustomResource(this, 'RuntimeLogRetention', {
      onCreate: setRetention,
      onUpdate: setRetention,
      policy: cr.AwsCustomResourcePolicy.fromStatements([
        new iam.PolicyStatement({ actions: ['logs:PutRetentionPolicy'], resources: [runtimeLogGroupArn] }),
      ]),
      installLatestAwsSdk: false,
      logGroup: crLogs,
    });
    retention.node.addDependency(ensureGroup);
    forceArm64(this);
    suppressCustomResourceProvider(this);
    NagSuppressions.addResourceSuppressions([ensureGroup, retention], [
      { id: 'AwsSolutions-IAM5', appliesTo: [{ regex: '/^Resource::arn:aws:logs:.*:log-group:\\/aws\\/bedrock-agentcore\\/runtimes\\/keepframe_agent-\\*$/' }],
        reason: 'The runtime log group is named after the runtime id (name + random suffix), assigned by AgentCore at create time.' },
    ], true);

    new cdk.CfnOutput(this, 'AgentRuntimeArn', { value: this.runtime.attrAgentRuntimeArn });
    new cdk.CfnOutput(this, 'AgentRuntimeId', { value: this.runtime.attrAgentRuntimeId });
    new cdk.CfnOutput(this, 'AgentRuntimeLogGroup', { value: `/aws/bedrock-agentcore/runtimes/${this.runtime.attrAgentRuntimeId}-DEFAULT` });

    NagSuppressions.addResourceSuppressions(role, [
      { id: 'AwsSolutions-IAM5', appliesTo: [{ regex: '/^Resource::arn:aws:s3:::.*\\/\\*$/' }, { regex: '/^Resource::<.*Arn>\\/\\*$/' }, { regex: '/^Resource::.*\\/index\\/\\*$/' }],
        reason: 'Object-level S3 on one bucket, per key prefix the agent uses (AGENT_S3_READ/WRITE); index/* covers the table\'s own GSIs.' },
      { id: 'AwsSolutions-IAM5', appliesTo: ['Resource::*'],
        reason: 'ecr:GetAuthorizationToken and the X-Ray put/sampling actions have no resource type; cloudwatch:PutMetricData is limited by the cloudwatch:namespace condition.' },
      { id: 'AwsSolutions-IAM5', appliesTo: [{ regex: '/^Resource::arn:aws:logs:.*:log-group:(\\/aws\\/bedrock-agentcore\\/runtimes\\/\\*(:\\*)?|\\*)$/' }],
        reason: 'AgentCore names the runtime log group after the runtime id (unknown before create); DescribeLogGroups only lists.' },
      { id: 'AwsSolutions-IAM5', appliesTo: [{ regex: '/^Resource::arn:aws:bedrock:\\*::foundation-model\\/anthropic\\..+$/' }],
        reason: 'A geo inference profile routes to the same foundation model in any region of its geography; the grant names the one model id.' },
      { id: 'AwsSolutions-IAM5', appliesTo: [{ regex: '/^Resource::arn:aws:secretsmanager:.*:secret:keepframe\\/anthropic-\\?\\?\\?\\?\\?\\?$/' }],
        reason: 'One secret; the six ? match the random suffix Secrets Manager appends to the name.' },
      { id: 'AwsSolutions-IAM5', appliesTo: [{ regex: '/^Resource::arn:aws:bedrock-agentcore:.*:workload-identity-directory\\/default\\/workload-identity\\/keepframe_agent-\\*$/' }],
        reason: 'The workload identity is named after the runtime id (name + random suffix), created by AgentCore.' },
    ], true);
  }
}
