import * as fs from 'node:fs';
import * as path from 'node:path';
import * as cdk from 'aws-cdk-lib';
import * as apigw from 'aws-cdk-lib/aws-apigateway';
import * as apigwv2 from 'aws-cdk-lib/aws-apigatewayv2';
import * as integrations from 'aws-cdk-lib/aws-apigatewayv2-integrations';
import * as ecrAssets from 'aws-cdk-lib/aws-ecr-assets';
import * as iam from 'aws-cdk-lib/aws-iam';
import * as lambda from 'aws-cdk-lib/aws-lambda';
import * as logs from 'aws-cdk-lib/aws-logs';
import * as ssm from 'aws-cdk-lib/aws-ssm';
import { NagSuppressions } from './nag';
import { Construct } from 'constructs';
import { NAMES, PROJECT, ProjectContext } from './config';
import { AgentCoreStack } from './agentcore-stack';
import { ComputeStack } from './compute-stack';
import { DataStack, grantList, grantObjects } from './data-stack';
import { NetworkStack } from './network-stack';
import { WorkflowStack } from './workflow-stack';

export interface ApiStackProps extends cdk.StackProps {
  ctx: ProjectContext;
  network: NetworkStack;
  data: DataStack;
  compute: ComputeStack;
  workflow: WorkflowStack;
  agentCore: AgentCoreStack;
  /** CloudFront origin(s) allowed by CORS, e.g. https://dxxxx.cloudfront.net. */
  corsOrigins?: string[];
}

/**
 * HTTP API → one Lambda (arm64 container image from services/api, FastAPI + Mangum).
 *
 * Authentication: every route is authenticated INSIDE the app (services/api/app/auth.py): a Bearer token
 * that must match `keepframe/team-token` (read-write) or `keepframe/judge-token` (GET only; any other
 * method is 403 before the route runs). Missing/unknown tokens are 401, and an unreadable secret fails
 * closed. The API Gateway HTTP API therefore has no authorizer (by design); it forwards Authorization unchanged.
 * Throttling at the stage caps abuse of the unauthenticated 401 path.
 */
export class ApiStack extends cdk.Stack {
  readonly api: apigwv2.HttpApi;
  readonly fn: lambda.DockerImageFunction;

  constructor(scope: Construct, id: string, props: ApiStackProps) {
    super(scope, id, props);
    const { ctx, network, data, compute, workflow, agentCore } = props;
    const { decisions, traces } = data;

    const logGroup = new logs.LogGroup(this, 'ApiLogs', {
      logGroupName: `/aws/lambda/${NAMES.apiFunction}`,
      retention: logs.RetentionDays.ONE_MONTH,
      removalPolicy: cdk.RemovalPolicy.DESTROY,
    });

    // CullApiLambdaRole (docs/ARCHITECTURE.md "IAM summary")
    const role = new iam.Role(this, 'CullApiLambdaRole', {
      assumedBy: new iam.ServicePrincipal('lambda.amazonaws.com'),
      description: 'KeepFrame API: read results, start runs, resume the agent',
    });
    role.addToPolicy(new iam.PolicyStatement({ actions: ['logs:CreateLogStream', 'logs:PutLogEvents'], resources: [logGroup.logGroupArn, `${logGroup.logGroupArn}:*`] }));
    grantObjects(data.bucketName, role, ['s3:GetObject'], ['manifests/*', 'proxy/*', 'sheets/*', 'results/*', 'bench/*', 'preview/*']);
    grantList(data.bucketName, role, ['manifests/*', 'results/*', 'bench/*']);
    for (const t of [decisions, traces]) {
      role.addToPolicy(new iam.PolicyStatement({
        actions: ['dynamodb:GetItem', 'dynamodb:BatchGetItem', 'dynamodb:Query', 'dynamodb:UpdateItem', 'dynamodb:PutItem'],
        resources: [t.tableArn, `${t.tableArn}/index/*`],
      }));
    }
    role.addToPolicy(new iam.PolicyStatement({ actions: ['states:StartExecution'], resources: [workflow.stateMachine.stateMachineArn] }));
    role.addToPolicy(new iam.PolicyStatement({
      actions: ['states:DescribeExecution'],
      resources: [cdk.Arn.format({ service: 'states', resource: 'execution', resourceName: `${NAMES.stateMachine}:*`, arnFormat: cdk.ArnFormat.COLON_RESOURCE_NAME }, this)],
    }));
    data.judgeToken.grantRead(role);
    data.teamToken.grantRead(role);
    data.aliasKey.grantRead(role); // S-1 judge-facing aliases
    // Approval resume, primary path: InvokeAgentRuntime mode=resume on the CDK-managed runtime (and its endpoints).
    role.addToPolicy(new iam.PolicyStatement({
      actions: ['bedrock-agentcore:InvokeAgentRuntime'],
      resources: [agentCore.runtimeArn, `${agentCore.runtimeArn}/*`],
    }));
    this.addStackDependency(agentCore);
    const srcRoot = path.resolve(__dirname, '..', ctx.srcRoot);
    const hasApp = fs.existsSync(path.join(srcRoot, 'services', 'api', 'Dockerfile'));
    const code = hasApp
      ? lambda.DockerImageCode.fromImageAsset(srcRoot, {
          file: 'services/api/Dockerfile',
          platform: ecrAssets.Platform.LINUX_ARM64,
          exclude: ['cdk', 'ui', 'bench', 'eval', 'docs', 'tests', '.git', '**/node_modules', '**/.venv', 'data', 'models', 'keepframe', 'config', 'scripts'],
        })
      : lambda.DockerImageCode.fromImageAsset(path.join(__dirname, '..', 'placeholders', 'api'), { platform: ecrAssets.Platform.LINUX_ARM64 });
    this.fn = new lambda.DockerImageFunction(this, 'ApiFn', {
      functionName: NAMES.apiFunction,
      description: hasApp ? 'KeepFrame review API (FastAPI + Mangum)' : 'PLACEHOLDER KeepFrame API (503 on every route)',
      code,
      architecture: lambda.Architecture.ARM_64,
      memorySize: 1024,
      timeout: cdk.Duration.seconds(29),
      role,
      logGroup,
      tracing: lambda.Tracing.ACTIVE,
      environment: {
        WORK_BUCKET: data.bucketName,
        CULL_DECISIONS_TABLE: decisions.tableName,
        CULL_TRACES_TABLE: traces.tableName,
        CULL_STATE_MACHINE_ARN: workflow.stateMachine.stateMachineArn,
        // Approval resume: InvokeAgentRuntime on the CDK runtime (the Fargate cull-agent fallback is removed).
        CULL_AGENT_RUNTIME_ARN: agentCore.runtimeArn,
        CULL_JUDGE_SECRET_ID: data.judgeToken.secretName,
        CULL_TEAM_SECRET_ID: data.teamToken.secretName,
        CULL_ALIAS_SECRET_ID: data.aliasKey.secretName,
        CULL_SHOW_EVENT_NAMES: '0',
        CULL_PRESIGN_TTL_S: '600',
        CULL_CORS_ORIGINS: (props.corsOrigins ?? []).join(','),
        // LLM provider facts for display only; the API never calls a model.
        CULL_MODEL_PROVIDER: ssm.StringParameter.valueForStringParameter(this, NAMES.params.modelProvider),
        CULL_PLANNER_MODEL: ssm.StringParameter.valueForStringParameter(this, NAMES.params.plannerModel),
        CULL_REASONS_MODEL: ssm.StringParameter.valueForStringParameter(this, NAMES.params.reasonsModel),
      },
    });

    this.api = new apigwv2.HttpApi(this, 'HttpApi', {
      apiName: `${PROJECT}-api`,
      description: 'KeepFrame API; every route is bearer-token authenticated in the Lambda (judge = GET only)',
      createDefaultStage: false,
      corsPreflight: props.corsOrigins?.length
        ? {
            allowOrigins: props.corsOrigins,
            allowHeaders: ['authorization', 'content-type'],
            allowMethods: [apigwv2.CorsHttpMethod.GET, apigwv2.CorsHttpMethod.POST, apigwv2.CorsHttpMethod.OPTIONS],
            maxAge: cdk.Duration.hours(1),
          }
        : undefined,
    });
    const integration = new integrations.HttpLambdaIntegration('ApiIntegration', this.fn, {
      payloadFormatVersion: apigwv2.PayloadFormatVersion.VERSION_2_0,
    });
    // Explicit routes instead of a greedy proxy, so unknown paths 404 at API Gateway.
    const routes: [string, apigwv2.HttpMethod[]][] = [
      ['/health', [apigwv2.HttpMethod.GET]],
      ['/events', [apigwv2.HttpMethod.GET]],
      ['/events/{event_id}/groups', [apigwv2.HttpMethod.GET]],
      ['/events/{event_id}/photos/{photo_id}', [apigwv2.HttpMethod.GET]],
      ['/runs', [apigwv2.HttpMethod.GET, apigwv2.HttpMethod.POST]],
      ['/runs/{run_id}', [apigwv2.HttpMethod.GET]],
      ['/runs/{run_id}/trace', [apigwv2.HttpMethod.GET]],
      ['/runs/{run_id}/agent', [apigwv2.HttpMethod.GET]],
      ['/approvals', [apigwv2.HttpMethod.GET]],
      ['/approvals/{approval_id}', [apigwv2.HttpMethod.POST]],
      ['/bench', [apigwv2.HttpMethod.GET]],
      ['/img/{proxy+}', [apigwv2.HttpMethod.GET]], // S-1: signed alias image links streamed by the API
    ];
    for (const [p, methods] of routes) this.api.addRoutes({ path: p, methods, integration });

    const accessLogs = new logs.LogGroup(this, 'ApiAccessLogs', {
      logGroupName: `/${PROJECT}/api-access`,
      retention: logs.RetentionDays.ONE_MONTH,
      removalPolicy: cdk.RemovalPolicy.DESTROY,
    });
    const stage = new apigwv2.HttpStage(this, 'DefaultStage', {
      httpApi: this.api,
      stageName: '$default',
      autoDeploy: true,
      // An events page lazy-loads ~96 thumbnails through /img, so the stage limit must absorb a judge opening one.
      throttle: { rateLimit: 100, burstLimit: 200 },
      accessLogSettings: {
        destination: new apigwv2.LogGroupLogDestination(accessLogs),
        format: apigw.AccessLogFormat.custom(JSON.stringify({
          requestId: '$context.requestId', ip: '$context.identity.sourceIp', method: '$context.httpMethod',
          route: '$context.routeKey', status: '$context.status', latencyMs: '$context.responseLatency',
          integrationError: '$context.integrationErrorMessage',
        })),
      },
    });
    const url = stage.url.replace(/\/$/, '');
    new ssm.StringParameter(this, 'ApiUrlParam', { parameterName: NAMES.params.apiUrl, stringValue: url, description: 'KeepFrame HTTP API base URL' });
    new cdk.CfnOutput(this, 'ApiUrl', { value: url });
    new cdk.CfnOutput(this, 'ApiFunctionName', { value: this.fn.functionName });
    new cdk.CfnOutput(this, 'ApiImage', { value: hasApp ? 'services/api' : 'PLACEHOLDER (services/api absent at synth)' });

    NagSuppressions.addResourceSuppressions(this, [
      { id: 'AwsSolutions-APIG4', reason: 'No API Gateway authorizer by design: the Lambda authenticates every route with judge/team bearer tokens from Secrets Manager and fails closed (services/api/app/auth.py).' },
      { id: 'AwsSolutions-IAM5', reason: 'S3 object grants are prefix-scoped on one bucket; index/* covers the table GSIs; X-Ray has no resource scoping; execution ARNs end in * by construction; <runtime ARN>/* covers the runtime endpoints.' },
    ], true);
  }
}
