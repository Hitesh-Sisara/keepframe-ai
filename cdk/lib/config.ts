/**
 * Shared names and context for every KeepFrame stack.
 *
 * Names are fixed so that stacks can reference each other by name and so that
 * resources created outside CDK on day 1 (work bucket, DynamoDB tables, ECR repos, CloudTrail) can be
 * adopted with `-c adoptExisting=true` instead of re-created.
 */
import * as cdk from 'aws-cdk-lib';
import { Construct } from 'constructs';

export const PROJECT = 'keepframe';

export const NAMES = {
  decisionsTable: 'keepframe-decisions',
  tracesTable: 'keepframe-traces',
  cluster: 'keepframe',
  stateMachine: 'CullEvent',
  /** AgentCore Runtime name (letters, digits, underscores only). */
  agentRuntime: 'keepframe_agent',
  apiFunction: 'keepframe-api',
  trail: 'keepframe-trail',
  dashboard: 'KeepFrame',
  metricNamespace: 'KeepFrame',
  alertsTopic: 'keepframe-alerts',
  families: {
    worker: 'cull-worker',
    workerBaseline: 'cull-worker-x86-baseline',
    sync: 'cull-sync',
    ingest: 'cull-ingest',
    agent: 'cull-agent',
    probe: 'cull-probe',
  },
  repos: {
    worker: 'keepframe',
    agent: 'keepframe-agent',
    sync: 'keepframe-sync',
    api: 'keepframe-api',
  },
  secrets: {
    source: 'keepframe/source',
    judgeToken: 'keepframe/judge-token',
    teamToken: 'keepframe/team-token',
    anthropic: 'keepframe/anthropic',
    aliasKey: 'keepframe/alias-key',
  },
  params: {
    workBucket: '/keepframe/work-bucket',
    modelProvider: '/keepframe/model-provider',
    plannerModel: '/keepframe/planner-model',
    reasonsModel: '/keepframe/reasons-model',
    agentRuntimeArn: '/keepframe/agent-runtime-arn',
    apiUrl: '/keepframe/api-url',
    uiUrl: '/keepframe/ui-url',
  },
} as const;

/** Optional S3-compatible import source: credential key NAMES (values set out-of-band; see docs/DEPLOY.md). */
export const SOURCE_KEYS = ['SOURCE_ACCESS_KEY_ID', 'SOURCE_SECRET_ACCESS_KEY'] as const;
export const ANTHROPIC_KEYS = ['ANTHROPIC_API_KEY', 'ANTHROPIC_BASE_URL'] as const;

export interface ProjectContext {
  adoptExisting: boolean;
  /** Optional, labelled x86 benchmark baseline (c7i). Default false; never used for the product path. */
  enableX86Baseline: boolean;
  imageTag: string;
  srcRoot: string;
  githubRepo: string;
  workBucketName: string;
  /**
   * Optional SyncSource import: bucket names in the S3-compatible source (SOURCE_BUCKET_* in the container).
   * Operator config with no default: context `sourceBucketProcessed` etc., else the SOURCE_BUCKET_* env var at
   * synth time. Empty when unset; ComputeStack then warns at synth and the sync entrypoint refuses to start.
   * Not needed for the default path (photographer uploads to the work bucket's raw/ prefix).
   */
  sourceBuckets: Record<(typeof SOURCE_BUCKET_ENV)[number], string>;
  /** SOURCE_S3_ENDPOINT_URL / SOURCE_REGION for SyncSource (context `sourceEndpointUrl`, `sourceRegion`; empty = AWS S3). */
  sourceEndpoint: { SOURCE_S3_ENDPOINT_URL: string; SOURCE_REGION: string };
  /** Model provider for the agent: `bedrock` (default) | `anthropic` (any Anthropic-compatible endpoint) | `fake`. */
  llmProvider: LlmProvider;
  /** Bedrock planner / reasons model ids (inference profile ids), context `bedrockModelId` / `bedrockReasonsModelId`. */
  bedrockModelId: string;
  bedrockReasonsModelId: string;
}

export type LlmProvider = 'bedrock' | 'anthropic' | 'fake';
/** Claude Sonnet 5 / Haiku 4.5 inference profiles valid in ap-south-1 (the `in.` geo profiles route within India). */
export const DEFAULT_BEDROCK_MODEL_ID = 'in.anthropic.claude-sonnet-5';
export const DEFAULT_BEDROCK_REASONS_MODEL_ID = 'in.anthropic.claude-haiku-4-5-20251001-v1:0';

export const SOURCE_BUCKET_ENV = [
  'SOURCE_BUCKET_PROCESSED',
  'SOURCE_BUCKET_ORIGINALS',
  'SOURCE_BUCKET_EXIF_RICH',
  'SOURCE_BUCKET_EXIF_SMALL',
] as const;

/** SOURCE_BUCKET_EXIF_RICH -> sourceBucketExifRich */
function contextKey(envName: string): string {
  return 'source' + envName.replace(/^SOURCE_/, '').toLowerCase().split('_').map((w) => w[0].toUpperCase() + w.slice(1)).join('');
}

function flag(scope: Construct, key: string, dflt: boolean): boolean {
  const v = scope.node.tryGetContext(key);
  if (v === undefined || v === null || v === '') return dflt;
  return v === true || String(v).toLowerCase() === 'true' || v === '1';
}

function llmProvider(v: string): LlmProvider {
  const p = v.trim().toLowerCase();
  if (p !== 'bedrock' && p !== 'anthropic' && p !== 'fake') throw new Error(`llmProvider must be bedrock|anthropic|fake, got ${v}`);
  return p;
}

/**
 * IAM resources for bedrock:InvokeModel on an inference profile: the profile ARN (account-scoped) and the
 * foundation-model ARNs it routes to. A geo/global profile (`in.`, `apac.`, `global.`, ...) can route to any
 * region of its geography, so the foundation model is granted in every region (`*`) but for that model id only.
 */
export function bedrockModelArns(account: string, region: string, modelId: string): string[] {
  const m = /^(?:([a-z]+)\.)?(anthropic\..+)$/.exec(modelId);
  if (!m) throw new Error(`not a Bedrock Anthropic model id: ${modelId}`);
  const [, geo, base] = m;
  if (!geo) return [`arn:aws:bedrock:${region}::foundation-model/${base}`];
  return [
    `arn:aws:bedrock:${region}:${account}:inference-profile/${modelId}`,
    `arn:aws:bedrock:*::foundation-model/${base}`,
  ];
}

export function projectContext(scope: Construct, account: string, region: string): ProjectContext {
  const get = (k: string, d: string) => String(scope.node.tryGetContext(k) ?? d);
  return {
    adoptExisting: flag(scope, 'adoptExisting', false),
    enableX86Baseline: flag(scope, 'enableX86Baseline', false),
    imageTag: get('imageTag', 'dev'),
    srcRoot: get('srcRoot', '..'),
    githubRepo: get('githubRepo', 'Hitesh-Sisara/keepframe'),
    workBucketName: `keepframe-work-${account}-${region}`,
    sourceBuckets: Object.fromEntries(
      SOURCE_BUCKET_ENV.map((k) => [k, String(scope.node.tryGetContext(contextKey(k)) ?? process.env[k] ?? '').trim()]),
    ) as ProjectContext['sourceBuckets'],
    sourceEndpoint: {
      SOURCE_S3_ENDPOINT_URL: get('sourceEndpointUrl', process.env.SOURCE_S3_ENDPOINT_URL ?? '').trim(),
      SOURCE_REGION: get('sourceRegion', process.env.SOURCE_REGION ?? '').trim(),
    },
    llmProvider: llmProvider(get('llmProvider', 'bedrock')),
    bedrockModelId: get('bedrockModelId', DEFAULT_BEDROCK_MODEL_ID),
    bedrockReasonsModelId: get('bedrockReasonsModelId', DEFAULT_BEDROCK_REASONS_MODEL_ID),
  };
}

/** Tags every resource with project=keepframe. */
export function tagProject(scope: Construct): void {
  cdk.Tags.of(scope).add('project', PROJECT);
}
