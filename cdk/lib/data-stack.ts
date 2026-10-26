import * as cdk from 'aws-cdk-lib';
import * as dynamodb from 'aws-cdk-lib/aws-dynamodb';
import * as ecr from 'aws-cdk-lib/aws-ecr';
import * as iam from 'aws-cdk-lib/aws-iam';
import * as logs from 'aws-cdk-lib/aws-logs';
import * as s3 from 'aws-cdk-lib/aws-s3';
import * as secretsmanager from 'aws-cdk-lib/aws-secretsmanager';
import * as ssm from 'aws-cdk-lib/aws-ssm';
import * as cr from 'aws-cdk-lib/custom-resources';
import { forceArm64 } from './arm64';
import { NagSuppressions } from './nag';
import { Construct } from 'constructs';
import { ANTHROPIC_KEYS, NAMES, ProjectContext, SOURCE_KEYS } from './config';

/**
 * Work-bucket lifecycle (docs/RESPONSIBLE_USE.md, docs/SECURITY_REVIEW.md #3). One list for both paths: the
 * create path renders it on the bucket, the adopt path writes it to the hand-made bucket with
 * PutBucketLifecycleConfiguration (which REPLACES the whole configuration, so every rule must be here).
 * Proxies live under `proxy/` (keepframe/io/proxies.py proxy_key); there is no `proxies/` prefix.
 */
export const WORK_LIFECYCLE: { id: string; prefix?: string; days?: number; abortMpuDays?: number }[] = [
  { id: 'expire-raw-7d', prefix: 'raw/', days: 7 },
  { id: 'expire-originals-7d', prefix: 'originals/', days: 7 },
  { id: 'expire-proxy-7d', prefix: 'proxy/', days: 7 },
  // Contact sheets carry faces: same 7 days as the pixels they are built from.
  { id: 'expire-sheets-7d', prefix: 'sheets/', days: 7 },
  // Enhancement previews (enhance_keeper) are full photos with faces: 7 days too.
  { id: 'expire-preview-7d', prefix: 'preview/', days: 7 },
  // 5,000 real processed customer images for the benchmark. 30 days keeps reruns possible until ~2026-10-25.
  // Agent session snapshots (JSON, no pixels; carry photo ids): 30 days covers any paused run's resume window.
  { id: 'expire-sessions-30d', prefix: 'sessions/', days: 30 },
  { id: 'expire-bench-5k-30d', prefix: 'bench/5k/', days: 30 },
  { id: 'abort-mpu-1d', abortMpuDays: 1 },
];

/** cdk-nag acknowledgment for the CDK-generated AwsCustomResource provider (it is created at stack scope). */
export function suppressCustomResourceProvider(stack: cdk.Stack): void {
  NagSuppressions.addStackSuppressions(stack, [
    { id: 'AwsSolutions-IAM4', appliesTo: ['Policy::arn:<AWS::Partition>:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole'],
      reason: 'CDK-generated AwsCustomResource provider role: AWSLambdaBasicExecutionRole grants CloudWatch Logs only; its API grant is a separate scoped policy.' },
  ]);
}

export interface DataStackProps extends cdk.StackProps {
  ctx: ProjectContext;
}

/** ECR repos by role. */
export interface Repos {
  worker: ecr.IRepository;
  agent: ecr.IRepository;
  sync: ecr.IRepository;
  api: ecr.IRepository;
}

/**
 * Work bucket, the two DynamoDB tables, ECR repos, secrets and SSM parameters.
 *
 * `adoptExisting=true` references the bucket, tables and ECR repos that were created by hand on day 1
 * (same names, same settings) instead of creating them. The create path is what `make deploy` uses in a
 * fresh account and reproduces those settings exactly. Secrets and SSM parameters are always CDK-owned.
 */
export class DataStack extends cdk.Stack {
  readonly bucket: s3.IBucket;
  /** Literal bucket name (known from account/region). Used instead of the token so other stacks need no export. */
  readonly bucketName: string;
  readonly decisions: dynamodb.ITableV2;
  readonly traces: dynamodb.ITableV2;
  readonly repos: Repos;
  /** Optional SyncSource import: read-only key for the S3-compatible source. */
  readonly sourceSecret: secretsmanager.ISecret;
  readonly judgeToken: secretsmanager.ISecret;
  readonly teamToken: secretsmanager.ISecret;
  readonly aliasKey: secretsmanager.ISecret;
  readonly anthropicSecret: secretsmanager.ISecret;
  readonly params: {
    modelProvider: ssm.IStringParameter;
    plannerModel: ssm.IStringParameter;
    reasonsModel: ssm.IStringParameter;
  };

  constructor(scope: Construct, id: string, props: DataStackProps) {
    super(scope, id, props);
    const { ctx } = props;
    this.bucketName = ctx.workBucketName;

    // ---- S3 work bucket (private, SSE-S3 + bucket key, TLS-only, 7-day pixel expiry) ----
    if (ctx.adoptExisting) {
      this.bucket = s3.Bucket.fromBucketName(this, 'Work', ctx.workBucketName);
      const bucketArn = `arn:aws:s3:::${ctx.workBucketName}`;
      const put: cr.AwsSdkCall = {
        service: 'S3',
        action: 'putBucketLifecycleConfiguration',
        parameters: {
          Bucket: ctx.workBucketName,
          LifecycleConfiguration: {
            Rules: WORK_LIFECYCLE.map((r) => ({
              ID: r.id,
              Status: 'Enabled',
              Filter: { Prefix: r.prefix ?? '' },
              ...(r.days ? { Expiration: { Days: r.days } } : {}),
              ...(r.abortMpuDays ? { AbortIncompleteMultipartUpload: { DaysAfterInitiation: r.abortMpuDays } } : {}),
            })),
          },
        },
        // Fixed id: rule changes are an in-place update (the parameters change), not a replacement.
        physicalResourceId: cr.PhysicalResourceId.of(`${ctx.workBucketName}-lifecycle`),
      };
      const lifecycle = new cr.AwsCustomResource(this, 'WorkLifecycle', {
        onCreate: put,
        onUpdate: put,
        // No onDelete: removing this stack must not strip the pixel expiry from the retained bucket.
        policy: cr.AwsCustomResourcePolicy.fromStatements([
          new iam.PolicyStatement({ actions: ['s3:PutLifecycleConfiguration', 's3:GetLifecycleConfiguration'], resources: [bucketArn] }),
        ]),
        installLatestAwsSdk: false,
        logGroup: new logs.LogGroup(this, 'WorkLifecycleLogs', { retention: logs.RetentionDays.ONE_MONTH, removalPolicy: cdk.RemovalPolicy.DESTROY }),
      });
      lifecycle.node.addMetadata('keepframe:purpose', 'work bucket lifecycle (adopted bucket)');
      forceArm64(this);
      suppressCustomResourceProvider(this);
    } else {
      const work = new s3.Bucket(this, 'Work', {
        bucketName: ctx.workBucketName,
        blockPublicAccess: s3.BlockPublicAccess.BLOCK_ALL,
        objectOwnership: s3.ObjectOwnership.BUCKET_OWNER_ENFORCED,
        encryption: s3.BucketEncryption.S3_MANAGED,
        bucketKeyEnabled: true,
        enforceSSL: true,
        versioned: false,
        lifecycleRules: WORK_LIFECYCLE.map((r) => ({
          id: r.id,
          prefix: r.prefix,
          expiration: r.days ? cdk.Duration.days(r.days) : undefined,
          abortIncompleteMultipartUploadAfter: r.abortMpuDays ? cdk.Duration.days(r.abortMpuDays) : undefined,
        })),
        // Customer pixels: never keep after `make destroy` unless emptied on purpose.
        removalPolicy: cdk.RemovalPolicy.RETAIN,
      });
      this.bucket = work;
      NagSuppressions.addResourceSuppressions(work, [
        {
          id: 'AwsSolutions-S1',
          reason: 'Access is audited by CloudTrail S3 data events on this bucket (trail keepframe-trail, docs/RESPONSIBLE_USE.md) instead of server access logs.',
        },
      ]);
    }

    // ---- DynamoDB ----
    if (ctx.adoptExisting) {
      this.decisions = dynamodb.TableV2.fromTableAttributes(this, 'Decisions', {
        tableName: NAMES.decisionsTable,
        globalIndexes: ['gsi1', 'gsi2', 'gsi3'],
      });
      this.traces = dynamodb.TableV2.fromTableName(this, 'Traces', NAMES.tracesTable);
    } else {
      const S = dynamodb.AttributeType.STRING;
      this.decisions = new dynamodb.TableV2(this, 'Decisions', {
        tableName: NAMES.decisionsTable,
        partitionKey: { name: 'pk', type: S },
        sortKey: { name: 'sk', type: S },
        billing: dynamodb.Billing.onDemand(),
        pointInTimeRecoverySpecification: { pointInTimeRecoveryEnabled: true },
        encryption: dynamodb.TableEncryptionV2.awsManagedKey(),
        globalSecondaryIndexes: [
          { indexName: 'gsi1', partitionKey: { name: 'gsi1pk', type: S }, sortKey: { name: 'sk', type: S } },
          { indexName: 'gsi2', partitionKey: { name: 'gsi2pk', type: S }, sortKey: { name: 'sk', type: S } },
          { indexName: 'gsi3', partitionKey: { name: 'gsi3pk', type: S }, sortKey: { name: 'gsi3sk', type: S } },
        ],
        removalPolicy: cdk.RemovalPolicy.RETAIN,
      });
      const traces = new dynamodb.TableV2(this, 'Traces', {
        tableName: NAMES.tracesTable,
        partitionKey: { name: 'pk', type: S },
        sortKey: { name: 'sk', type: S },
        billing: dynamodb.Billing.onDemand(),
        timeToLiveAttribute: 'ttl',
        encryption: dynamodb.TableEncryptionV2.awsManagedKey(),
        removalPolicy: cdk.RemovalPolicy.RETAIN,
      });
      this.traces = traces;
      NagSuppressions.addResourceSuppressions(traces, [
        {
          id: 'AwsSolutions-DDB3',
          reason: 'Trace items are reproducible from results/<eventId>/trace.jsonl in S3 and expire after 90 days (TTL); PITR adds cost without value.',
        },
      ]);
    }

    // ---- ECR (docs/ARCHITECTURE.md "Components"): scan on push, keep last 10 ----
    const repo = (key: keyof Repos, name: string): ecr.IRepository => {
      if (ctx.adoptExisting) return ecr.Repository.fromRepositoryName(this, `Repo-${key}`, name);
      return new ecr.Repository(this, `Repo-${key}`, {
        repositoryName: name,
        imageScanOnPush: true,
        imageTagMutability: ecr.TagMutability.MUTABLE,
        encryption: ecr.RepositoryEncryption.AES_256,
        lifecycleRules: [{ description: 'keep last 10 images', maxImageCount: 10 }],
        removalPolicy: cdk.RemovalPolicy.DESTROY,
        emptyOnDelete: true,
      });
    };
    this.repos = {
      worker: repo('worker', NAMES.repos.worker),
      agent: repo('agent', NAMES.repos.agent),
      sync: repo('sync', NAMES.repos.sync),
      api: repo('api', NAMES.repos.api),
    };

    // ---- Secrets (names only in the repo; values set out-of-band, docs/DEPLOY.md) ----
    const placeholder = (keys: readonly string[]) =>
      JSON.stringify(Object.fromEntries(keys.map((k) => [k, 'SET_OUT_OF_BAND'])));
    const source = new secretsmanager.Secret(this, 'SourceSecret', {
      secretName: NAMES.secrets.source,
      description: 'Optional SyncSource: read-only key for an S3-compatible import source (empty = task role). Keys: ' + SOURCE_KEYS.join(', '),
      secretStringValue: cdk.SecretValue.unsafePlainText(placeholder(SOURCE_KEYS)),
    });
    const anthropic = new secretsmanager.Secret(this, 'AnthropicSecret', {
      secretName: NAMES.secrets.anthropic,
      description: 'Optional: Anthropic-compatible model endpoint for the agent (KEEPFRAME_LLM_PROVIDER=anthropic). Keys: ' + ANTHROPIC_KEYS.join(', '),
      secretStringValue: cdk.SecretValue.unsafePlainText(placeholder(ANTHROPIC_KEYS)),
    });
    const token = (cid: string, name: string, what: string) =>
      new secretsmanager.Secret(this, cid, {
        secretName: name,
        description: `${what} bearer token for the KeepFrame API ({"token": ...}; rotate by moving token to "previous")`,
        generateSecretString: {
          secretStringTemplate: JSON.stringify({}),
          generateStringKey: 'token',
          passwordLength: 48,
          excludePunctuation: true,
        },
      });
    const judge = token('JudgeToken', NAMES.secrets.judgeToken, 'Read-only judge');
    const team = token('TeamToken', NAMES.secrets.teamToken, 'Team (read-write)');
    // S-1: server-side key for the opaque photo aliases the judge sees (HMAC of the real id). Never leaves the API.
    const aliasKey = new secretsmanager.Secret(this, 'AliasKey', {
      secretName: NAMES.secrets.aliasKey,
      description: 'KeepFrame API: HMAC key for judge-facing photo/event aliases ({"key": ...}); rotating it changes every alias',
      generateSecretString: {
        secretStringTemplate: JSON.stringify({}),
        generateStringKey: 'key',
        passwordLength: 64,
        excludePunctuation: true,
      },
    });
    this.sourceSecret = source;
    this.anthropicSecret = anthropic;
    this.judgeToken = judge;
    this.teamToken = team;
    this.aliasKey = aliasKey;
    for (const s of [source, anthropic, judge, team, aliasKey]) {
      NagSuppressions.addResourceSuppressions(s, [
        {
          id: 'AwsSolutions-SMG4',
          reason: 'Optional external credentials (import source, Anthropic-compatible endpoint) are rotated at their provider; API tokens rotate manually via the {"token","previous"} shape. Hackathon lifetime is 3 weeks.',
        },
      ]);
    }

    // ---- SSM parameters (docs/ARCHITECTURE.md "Components"; docs/AGENT.md "Model provider") ----
    // /keepframe/agent-runtime-arn is owned by AgentCoreStack (the runtime ARN), not here.
    const param = (cid: string, name: string, value: string, description: string) =>
      new ssm.StringParameter(this, cid, { parameterName: name, stringValue: value, description });
    param('WorkBucketParam', NAMES.params.workBucket, this.bucketName, 'Work bucket name');
    this.params = {
      modelProvider: param('ModelProvider', NAMES.params.modelProvider, ctx.llmProvider, 'KEEPFRAME_LLM_PROVIDER: bedrock (default) | anthropic (optional Anthropic-compatible endpoint) | fake'),
      plannerModel: param('PlannerModel', NAMES.params.plannerModel, ctx.llmProvider === 'bedrock' ? ctx.bedrockModelId : 'claude-sonnet-5', 'Planner model id for the configured provider'),
      reasonsModel: param('ReasonsModel', NAMES.params.reasonsModel, ctx.llmProvider === 'bedrock' ? ctx.bedrockReasonsModelId : 'claude-haiku-4.5', 'Reasons model id for the configured provider'),
    };

    new cdk.CfnOutput(this, 'WorkBucket', { value: this.bucketName });
    new cdk.CfnOutput(this, 'DecisionsTable', { value: this.decisions.tableName });
    new cdk.CfnOutput(this, 'TracesTable', { value: this.traces.tableName });
    new cdk.CfnOutput(this, 'SourceSecretArn', { value: source.secretArn });
    new cdk.CfnOutput(this, 'AnthropicSecretArn', { value: anthropic.secretArn });
    new cdk.CfnOutput(this, 'JudgeTokenSecretArn', { value: judge.secretArn });
    new cdk.CfnOutput(this, 'TeamTokenSecretArn', { value: team.secretArn });
  }
}

/** Grants used by more than one stack, kept here so the IAM shape matches docs/ARCHITECTURE.md "IAM summary" in one place. */
export function grantObjects(bucketName: string, grantee: iam.IGrantable, actions: string[], prefixes: string[]): void {
  grantee.grantPrincipal.addToPrincipalPolicy(
    new iam.PolicyStatement({ actions, resources: prefixes.map((p) => `arn:aws:s3:::${bucketName}/${p}`) }),
  );
}

export function grantList(bucketName: string, grantee: iam.IGrantable, prefixes?: string[]): void {
  grantee.grantPrincipal.addToPrincipalPolicy(
    new iam.PolicyStatement({
      actions: ['s3:ListBucket'],
      resources: [`arn:aws:s3:::${bucketName}`],
      conditions: prefixes ? { StringLike: { 's3:prefix': prefixes } } : undefined,
    }),
  );
}
