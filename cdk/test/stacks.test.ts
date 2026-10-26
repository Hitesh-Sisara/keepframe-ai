/**
 * Synth-level assertions for the hard rules: Arm64 only, no public buckets, private secrets, routes all
 * served by the authenticating Lambda. Run: `npm test` (no AWS credentials needed).
 */
import { test } from 'node:test';
import * as assert from 'node:assert/strict';
import * as cdk from 'aws-cdk-lib';
import { Template, Match } from 'aws-cdk-lib/assertions';
import { projectContext } from '../lib/config';
import { NetworkStack } from '../lib/network-stack';
import { DataStack } from '../lib/data-stack';
import { ComputeStack } from '../lib/compute-stack';
import { WorkflowStack } from '../lib/workflow-stack';
import { ApiStack } from '../lib/api-stack';
import { AgentCoreStack } from '../lib/agentcore-stack';
import { CiStack } from '../lib/ci-stack';

function build(context: Record<string, unknown> = {}) {
  const app = new cdk.App({ context: { adoptExisting: false, ...context } });
  const env = { account: '111111111111', region: 'ap-south-1' };
  const ctx = projectContext(app, env.account, env.region);
  const network = new NetworkStack(app, 'NetworkStack', { env });
  const data = new DataStack(app, 'DataStack', { env, ctx });
  const compute = new ComputeStack(app, 'ComputeStack', { env, ctx, network, data });
  const agentCore = new AgentCoreStack(app, 'AgentCoreStack', { env, ctx, data });
  const workflow = new WorkflowStack(app, 'WorkflowStack', { env, network, data, compute, agentRuntimeArn: agentCore.runtimeArn });
  const api = new ApiStack(app, 'ApiStack', { env, ctx, network, data, compute, workflow, agentCore });
  return { network, data, compute, agentCore, workflow, api, t: (s: cdk.Stack) => Template.fromStack(s) };
}

test('every Fargate task definition is ARM64/Linux', () => {
  const { compute, t } = build();
  const defs = t(compute).findResources('AWS::ECS::TaskDefinition', { Properties: { RequiresCompatibilities: ['FARGATE'] } });
  assert.ok(Object.keys(defs).length >= 3);
  for (const d of Object.values(defs)) {
    assert.deepEqual(d.Properties.RuntimePlatform, { CpuArchitecture: 'ARM64', OperatingSystemFamily: 'LINUX' });
  }
});

test('optional cull-sync (SyncSource) takes source bucket names and endpoint from context, with no built-in default', () => {
  const ctxBuckets = {
    sourceBucketProcessed: 'src-processed',
    sourceBucketOriginals: 'src-originals',
    sourceBucketExifRich: 'src-exif-rich',
    sourceBucketExifSmall: 'src-exif-small',
  };
  const { compute, t } = build({ ...ctxBuckets, sourceEndpointUrl: 'https://s3.example.test' });
  const env = t(compute).findResources('AWS::ECS::TaskDefinition', { Properties: { Family: 'cull-sync' } });
  const vars = Object.values(env)[0].Properties.ContainerDefinitions[0].Environment as { Name: string; Value: string }[];
  const got = Object.fromEntries(vars.map((v) => [v.Name, v.Value]));
  assert.equal(got.SOURCE_BUCKET_PROCESSED, 'src-processed');
  assert.equal(got.SOURCE_BUCKET_ORIGINALS, 'src-originals');
  assert.equal(got.SOURCE_BUCKET_EXIF_RICH, 'src-exif-rich');
  assert.equal(got.SOURCE_BUCKET_EXIF_SMALL, 'src-exif-small');
  assert.equal(got.SOURCE_S3_ENDPOINT_URL, 'https://s3.example.test');
  const secrets = Object.values(env)[0].Properties.ContainerDefinitions[0].Secrets as { Name: string }[];
  assert.deepEqual(secrets.map((x) => x.Name).sort(), ['SOURCE_ACCESS_KEY_ID', 'SOURCE_SECRET_ACCESS_KEY']);

  for (const k of ['SOURCE_BUCKET_PROCESSED', 'SOURCE_BUCKET_ORIGINALS', 'SOURCE_BUCKET_EXIF_RICH', 'SOURCE_BUCKET_EXIF_SMALL']) delete process.env[k];
  const bare = build();
  const bareDef = Object.values(bare.t(bare.compute).findResources('AWS::ECS::TaskDefinition', { Properties: { Family: 'cull-sync' } }))[0];
  const bareVars = bareDef.Properties.ContainerDefinitions[0].Environment as { Name: string; Value: string }[];
  assert.ok(bareVars.filter((v) => v.Name.startsWith('SOURCE_BUCKET_')).every((v) => v.Value === ''));
});

test('cull-worker is 4 vCPU / 8 GiB, cull-sync and cull-ingest 1 vCPU / 2 GiB', () => {
  const { compute, t } = build();
  t(compute).hasResourceProperties('AWS::ECS::TaskDefinition', { Family: 'cull-ingest', Cpu: '1024', Memory: '2048',
    ContainerDefinitions: [Match.objectLike({ EntryPoint: ['python', '-m', 'keepframe.io.ingest_upload'] })] });
  t(compute).hasResourceProperties('AWS::ECS::TaskDefinition', { Family: 'cull-worker', Cpu: '4096', Memory: '8192' });
  t(compute).hasResourceProperties('AWS::ECS::TaskDefinition', { Family: 'cull-sync', Cpu: '1024', Memory: '2048' });
});

test('every Lambda is arm64', () => {
  const { workflow, api, t } = build();
  for (const s of [workflow, api]) {
    for (const f of Object.values(t(s).findResources('AWS::Lambda::Function'))) {
      assert.deepEqual(f.Properties.Architectures, ['arm64'], JSON.stringify(f.Properties.FunctionName));
    }
  }
});

test('no EC2 capacity unless the labelled x86 baseline is enabled; COOL is gone', () => {
  const { compute, data, t } = build();
  t(compute).resourceCountIs('AWS::EC2::LaunchTemplate', 0);
  t(compute).resourceCountIs('AWS::AutoScaling::AutoScalingGroup', 0);
  const families = Object.values(t(compute).findResources('AWS::ECS::TaskDefinition')).map((d) => d.Properties.Family);
  assert.deepEqual(families.sort(), ['cull-ingest', 'cull-probe', 'cull-sync', 'cull-worker']);
  const repos = Object.values(t(data).findResources('AWS::ECR::Repository')).map((r) => r.Properties.RepositoryName);
  assert.ok(!repos.includes('keepframe-cool'), repos.join(','));
  const params = Object.values(t(data).findResources('AWS::SSM::Parameter')).map((p) => p.Properties.Name);
  for (const gone of ['/keepframe/cool-image-uri', '/keepframe/bedrock-region', '/keepframe/agent-runtime-arn']) {
    assert.ok(!params.includes(gone), gone);
  }
  const withX86 = build({ enableX86Baseline: true });
  const types = Object.values(withX86.t(withX86.compute).findResources('AWS::EC2::LaunchTemplate')).map((l) => l.Properties.LaunchTemplateData.InstanceType);
  assert.deepEqual(types, ['c7i.4xlarge']);
});

/** IAM statements of a stack's policies. */
function statements(tpl: Template): { Action: string | string[]; Resource: unknown }[] {
  return Object.values(tpl.findResources('AWS::IAM::Policy')).flatMap((p) => p.Properties.PolicyDocument.Statement);
}

test('bedrock:InvokeModel only on the AgentCore role, scoped to the configured models; no Marketplace', () => {
  const b = build();
  for (const s of [b.compute, b.workflow, b.api, b.agentCore]) {
    for (const st of statements(b.t(s))) {
      for (const a of [st.Action].flat()) {
        assert.ok(!/^aws-marketplace:/.test(a), `${s.stackName}: ${a}`);
        if (/^bedrock:/.test(a)) {
          assert.equal(s, b.agentCore, `${s.stackName}: ${a}`);
          assert.ok(['bedrock:InvokeModel', 'bedrock:InvokeModelWithResponseStream'].includes(a), a);
        }
      }
    }
  }
  const invoke = statements(b.t(b.agentCore)).filter((st) => [st.Action].flat().includes('bedrock:InvokeModel'));
  assert.equal(invoke.length, 1);
  assert.deepEqual([...[invoke[0].Resource].flat()].sort(), [
    'arn:aws:bedrock:*::foundation-model/anthropic.claude-haiku-4-5-20251001-v1:0',
    'arn:aws:bedrock:*::foundation-model/anthropic.claude-sonnet-5',
    'arn:aws:bedrock:ap-south-1:111111111111:inference-profile/in.anthropic.claude-haiku-4-5-20251001-v1:0',
    'arn:aws:bedrock:ap-south-1:111111111111:inference-profile/in.anthropic.claude-sonnet-5',
  ]);
  assert.deepEqual([...[invoke[0].Action].flat()].sort(), ['bedrock:InvokeModel', 'bedrock:InvokeModelWithResponseStream']);
});

test('llmProvider=anthropic: no Bedrock grant, the one secret, model names from SSM', () => {
  const b = build({ llmProvider: 'anthropic' });
  const tpl = b.t(b.agentCore);
  const sts = statements(tpl);
  assert.ok(!sts.some((st) => [st.Action].flat().some((a) => a.startsWith('bedrock:'))));
  const secret = sts.filter((st) => [st.Action].flat().includes('secretsmanager:GetSecretValue'));
  assert.deepEqual(secret.flatMap((st) => [st.Resource].flat()),
    ['arn:aws:secretsmanager:ap-south-1:111111111111:secret:keepframe/anthropic-??????']);
  const env = Object.values(tpl.findResources('AWS::BedrockAgentCore::Runtime'))[0].Properties.EnvironmentVariables;
  assert.equal(env.KEEPFRAME_LLM_PROVIDER, 'anthropic');
  assert.equal(env.CULL_ANTHROPIC_SECRET_ID, 'keepframe/anthropic');
  assert.ok(env.CULL_PLANNER_MODEL && !('KEEPFRAME_BEDROCK_MODEL_ID' in env));
  b.t(b.data).hasResourceProperties('AWS::SSM::Parameter', { Name: '/keepframe/model-provider', Value: 'anthropic' });
  b.t(b.data).hasResourceProperties('AWS::SSM::Parameter', { Name: '/keepframe/planner-model', Value: 'claude-sonnet-5' });
  assert.throws(() => build({ llmProvider: 'other' }), /llmProvider must be/);
});

test('bedrockModelId context changes the env and the grant together', () => {
  const b = build({ bedrockModelId: 'global.anthropic.claude-sonnet-5-5' });
  const tpl = b.t(b.agentCore);
  const env = Object.values(tpl.findResources('AWS::BedrockAgentCore::Runtime'))[0].Properties.EnvironmentVariables;
  assert.equal(env.KEEPFRAME_BEDROCK_MODEL_ID, 'global.anthropic.claude-sonnet-5-5');
  const res = statements(tpl).filter((st) => [st.Action].flat().includes('bedrock:InvokeModel')).flatMap((st) => [st.Resource].flat());
  assert.ok(res.includes('arn:aws:bedrock:ap-south-1:111111111111:inference-profile/global.anthropic.claude-sonnet-5-5'));
  assert.ok(res.includes('arn:aws:bedrock:*::foundation-model/anthropic.claude-sonnet-5-5'));
  b.t(b.data).hasResourceProperties('AWS::SSM::Parameter', { Name: '/keepframe/planner-model', Value: 'global.anthropic.claude-sonnet-5-5' });
});

test('AgentCore runtime: ECR container, HTTP, PUBLIC, lifecycle, names-only env', () => {
  const { agentCore, t } = build({ imageTag: 'main-abc1234' });
  const tpl = t(agentCore);
  const rts = Object.values(tpl.findResources('AWS::BedrockAgentCore::Runtime'));
  assert.equal(rts.length, 1);
  const p = rts[0].Properties;
  assert.equal(p.AgentRuntimeName, 'keepframe_agent');
  assert.equal(p.ProtocolConfiguration, 'HTTP');
  assert.deepEqual(p.NetworkConfiguration, { NetworkMode: 'PUBLIC' });
  assert.deepEqual(p.LifecycleConfiguration, { IdleRuntimeSessionTimeout: 900, MaxLifetime: 28800 });
  assert.match(JSON.stringify(p.AgentRuntimeArtifact.ContainerConfiguration.ContainerUri), /keepframe-agent:main-abc1234/);
  const env = p.EnvironmentVariables;
  for (const [k, v] of Object.entries({
    KEEPFRAME_LLM_PROVIDER: 'bedrock', KEEPFRAME_BEDROCK_MODEL_ID: 'in.anthropic.claude-sonnet-5',
    KEEPFRAME_BEDROCK_REASONS_MODEL_ID: 'in.anthropic.claude-haiku-4-5-20251001-v1:0', KEEPFRAME_BEDROCK_REGION: 'ap-south-1',
    CULL_TOOLS: 'real', CULL_MODELS_DIR: '/opt/models',
    CULL_CACHE_DIR: '/tmp/cull', CULL_INSTANCE_TYPE: 'agentcore-arm64',
    OPENCV_FORCE_DNN_ENGINE: '3', CULL_AGENT_HOST: 'agentcore', WORK_BUCKET: 'keepframe-work-111111111111-ap-south-1', CULL_METRICS_NAMESPACE: 'KeepFrame',
  })) assert.equal(env[k], v, k);
  for (const k of Object.keys(env)) assert.ok(!/API_KEY|SECRET$|TOKEN|PASSWORD/.test(k), `secret-looking env ${k}`);
  assert.ok(!('ANTHROPIC_API_KEY' in env) && !('ANTHROPIC_BASE_URL' in env));
  assert.ok(!('CULL_ANTHROPIC_SECRET_ID' in env) && !Object.keys(env).some((k) => /NO_TOOL/.test(k)));
  tpl.hasResourceProperties('AWS::SSM::Parameter', { Name: '/keepframe/agent-runtime-arn', Value: { 'Fn::GetAtt': [Match.anyValue(), 'AgentRuntimeArn'] } });
});

test('AgentCore execution role: trust conditions; no secret read with the default Bedrock provider', () => {
  const { agentCore, t } = build();
  const tpl = t(agentCore);
  tpl.hasResourceProperties('AWS::IAM::Role', { AssumeRolePolicyDocument: { Statement: [Match.objectLike({
    Principal: { Service: 'bedrock-agentcore.amazonaws.com' },
    Condition: { StringEquals: { 'aws:SourceAccount': '111111111111' }, ArnLike: { 'aws:SourceArn': 'arn:aws:bedrock-agentcore:ap-south-1:111111111111:*' } },
  })] } });
  const sts = Object.values(tpl.findResources('AWS::IAM::Policy')).flatMap((p) => p.Properties.PolicyDocument.Statement);
  const secret = sts.filter((st: { Action: unknown }) => [st.Action].flat().includes('secretsmanager:GetSecretValue'));
  assert.deepEqual(secret, []);
  const ecrPull = sts.find((st: { Action: unknown }) => [st.Action].flat().includes('ecr:BatchGetImage'));
  assert.match(JSON.stringify(ecrPull.Resource), /keepframe-agent/);
  assert.ok(sts.some((st: { Action: unknown }) => [st.Action].flat().includes('bedrock-agentcore:GetWorkloadAccessToken')));
});

test('InvokeAgentRuntime is granted on the CDK runtime only (API + workflow)', () => {
  const { api, workflow, t } = build();
  for (const s of [api, workflow]) {
    const sts = Object.values(t(s).findResources('AWS::IAM::Policy')).flatMap((p) => p.Properties.PolicyDocument.Statement)
      .filter((st: { Action: unknown }) => [st.Action].flat().includes('bedrock-agentcore:InvokeAgentRuntime'));
    assert.ok(sts.length >= 1, s.stackName);
    for (const st of sts) {
      const raw = JSON.stringify(st.Resource);
      assert.ok(!raw.includes('runtime/*'), raw);
      assert.match(raw, /AgentRuntimeArn/);
    }
  }
});

/** The state machine definition with every CloudFormation token replaced by a placeholder string. */
function states(tpl: Template): Record<string, any> {
  const sm = Object.values(tpl.findResources('AWS::StepFunctions::StateMachine'))[0];
  const parts = sm.Properties.DefinitionString['Fn::Join'][1] as unknown[];
  return JSON.parse(parts.map((x) => (typeof x === 'string' ? x : 'TOKEN')).join('')).States;
}

test('CullEvent RunAgent: invoke_agent -> Wait 30 s -> agent_status loop, no Fargate agent, no COOL', () => {
  const { workflow, t } = build();
  const st = states(t(workflow));
  assert.equal(st.RunAgent.Next, 'AgentStarted?');
  assert.equal(st.WaitForAgent.Seconds, 30);
  assert.equal(st.WaitForAgent.Next, 'AgentStatus');
  assert.equal(st.AgentStatus.Next, 'AgentDone?');
  const done = st['AgentDone?'];
  assert.deepEqual(done.Choices.map((c: { StringEquals: string; Next: string }) => [c.StringEquals, c.Next]),
    [['running', 'WaitForAgent'], ['error', 'AgentErrored']]);
  assert.equal(done.Default, 'Summarise');
  assert.equal(st.AgentErrored.Next, 'MarkRunFailed');
  assert.deepEqual(st['AgentStarted?'].Choices.map((c: { StringEquals: string; Next: string }) => [c.StringEquals, c.Next]), [['running', 'WaitForAgent']]);
  assert.equal(st['AgentStarted?'].Default, 'Summarise');
  assert.ok(!('RunAgentFargate' in st));
  // no ECS cull-agent task (the Lambda keepframe-agent-status is fine)
  for (const s of Object.values(st)) assert.ok(!/task-definition\/cull-agent|"cull-agent"/.test(JSON.stringify(s)));
  assert.ok(st['SkipAgent?']);
  for (const k of Object.keys(st)) assert.ok(!/cool|flavor/i.test(k), k);
  const fns = Object.values(t(workflow).findResources('AWS::Lambda::Function')).map((f) => f.Properties);
  const status = fns.find((f) => f.FunctionName === 'keepframe-agent-status');
  assert.equal(status.Environment.Variables.CULL_AGENT_BUDGET_S, '7200');
  assert.ok(fns.find((f) => f.FunctionName === 'keepframe-invoke-agent'));
});

test('work bucket: private, SSE-S3, TLS-only, lifecycle per docs/ARCHITECTURE.md "S3 layout"', () => {
  const { data, t } = build();
  const tpl = t(data);
  tpl.hasResourceProperties('AWS::S3::Bucket', {
    BucketName: 'keepframe-work-111111111111-ap-south-1',
    PublicAccessBlockConfiguration: { BlockPublicAcls: true, BlockPublicPolicy: true, IgnorePublicAcls: true, RestrictPublicBuckets: true },
    BucketEncryption: { ServerSideEncryptionConfiguration: [{ ServerSideEncryptionByDefault: { SSEAlgorithm: 'AES256' }, BucketKeyEnabled: true }] },
    LifecycleConfiguration: { Rules: Match.arrayWith([
      Match.objectLike({ Id: 'expire-raw-7d', Prefix: 'raw/', ExpirationInDays: 7 }),
      Match.objectLike({ Id: 'expire-originals-7d', Prefix: 'originals/', ExpirationInDays: 7 }),
      Match.objectLike({ Id: 'expire-proxy-7d', Prefix: 'proxy/', ExpirationInDays: 7 }),
      Match.objectLike({ Id: 'expire-sheets-7d', Prefix: 'sheets/', ExpirationInDays: 7 }),
      Match.objectLike({ Id: 'expire-preview-7d', Prefix: 'preview/', ExpirationInDays: 7 }),
      Match.objectLike({ Id: 'expire-sessions-30d', Prefix: 'sessions/', ExpirationInDays: 30 }),
      Match.objectLike({ Id: 'expire-bench-5k-30d', Prefix: 'bench/5k/', ExpirationInDays: 30 }),
      Match.objectLike({ Id: 'abort-mpu-1d', AbortIncompleteMultipartUpload: { DaysAfterInitiation: 1 } }),
    ]) },
  });
  tpl.hasResourceProperties('AWS::S3::BucketPolicy', {
    PolicyDocument: { Statement: Match.arrayWith([Match.objectLike({ Effect: 'Deny', Condition: { Bool: { 'aws:SecureTransport': 'false' } } })]) },
  });
});

test('tables: gsi1/gsi2/gsi3, PITR on decisions, TTL on traces', () => {
  const { data, t } = build();
  const tpl = t(data);
  tpl.hasResourceProperties('AWS::DynamoDB::GlobalTable', {
    TableName: 'keepframe-decisions',
    GlobalSecondaryIndexes: [
      Match.objectLike({ IndexName: 'gsi1' }), Match.objectLike({ IndexName: 'gsi2' }), Match.objectLike({ IndexName: 'gsi3' }),
    ],
    Replicas: [Match.objectLike({ PointInTimeRecoverySpecification: { PointInTimeRecoveryEnabled: true } })],
  });
  tpl.hasResourceProperties('AWS::DynamoDB::GlobalTable', {
    TableName: 'keepframe-traces', TimeToLiveSpecification: { AttributeName: 'ttl', Enabled: true },
  });
});

test('adoptExisting=true creates no bucket, tables or ECR repos', () => {
  const { data, t } = build({ adoptExisting: true });
  const tpl = t(data);
  tpl.resourceCountIs('AWS::S3::Bucket', 0);
  tpl.resourceCountIs('AWS::DynamoDB::GlobalTable', 0);
  tpl.resourceCountIs('AWS::ECR::Repository', 0);
  tpl.resourceCountIs('AWS::SecretsManager::Secret', 5); // source (optional), anthropic (optional), judge, team, alias-key
});

test('adoptExisting=true writes the full lifecycle to the hand-made bucket (arm64 provider, no delete)', () => {
  const { data, t } = build({ adoptExisting: true });
  const tpl = t(data);
  const crs = Object.values(tpl.findResources('Custom::AWS'));
  assert.equal(crs.length, 1);
  const create = JSON.parse(crs[0].Properties.Create);
  assert.equal(crs[0].Properties.Delete, undefined, 'deleting the stack must not strip pixel expiry');
  assert.equal(create.action, 'putBucketLifecycleConfiguration');
  assert.equal(create.parameters.Bucket, 'keepframe-work-111111111111-ap-south-1');
  const rules = Object.fromEntries(create.parameters.LifecycleConfiguration.Rules.map((r: any) => [r.ID, r]));
  const exp = (id: string) => [rules[id].Filter.Prefix, rules[id].Expiration?.Days];
  assert.deepEqual(exp('expire-raw-7d'), ['raw/', 7]);
  assert.deepEqual(exp('expire-originals-7d'), ['originals/', 7]);
  assert.deepEqual(exp('expire-proxy-7d'), ['proxy/', 7]);
  assert.deepEqual(exp('expire-sheets-7d'), ['sheets/', 7]);
  assert.deepEqual(exp('expire-preview-7d'), ['preview/', 7]);
  assert.deepEqual(exp('expire-sessions-30d'), ['sessions/', 30]);
  assert.deepEqual(exp('expire-bench-5k-30d'), ['bench/5k/', 30]);
  assert.equal(rules['abort-mpu-1d'].AbortIncompleteMultipartUpload.DaysAfterInitiation, 1);
  for (const r of Object.values(rules) as any[]) assert.equal(r.Status, 'Enabled');
  for (const f of Object.values(tpl.findResources('AWS::Lambda::Function'))) assert.deepEqual(f.Properties.Architectures, ['arm64']);
  const pol = Object.values(tpl.findResources('AWS::IAM::Policy')).map((p) => p.Properties.PolicyDocument.Statement).flat();
  assert.deepEqual(pol.map((st: any) => [[...st.Action].sort(), st.Resource]),
    [[['s3:GetLifecycleConfiguration', 's3:PutLifecycleConfiguration'], 'arn:aws:s3:::keepframe-work-111111111111-ap-south-1']]);
});

test('no secret value is a literal except the out-of-band placeholders', () => {
  const { data, t } = build();
  for (const s of Object.values(t(data).findResources('AWS::SecretsManager::Secret'))) {
    const v = s.Properties.SecretString;
    if (v !== undefined) assert.ok(Object.values(JSON.parse(v)).every((x) => x === 'SET_OUT_OF_BAND'), v);
    else assert.ok(s.Properties.GenerateSecretString);
  }
});

test('API routes all go to the authenticating Lambda; no API Gateway route is unintegrated', () => {
  const { api, t } = build();
  const routes = Object.values(t(api).findResources('AWS::ApiGatewayV2::Route'));
  assert.equal(routes.length, 13); // + GET /img/{proxy+} (S-1 alias image links)
  for (const r of routes) assert.ok(r.Properties.Target, r.Properties.RouteKey);
});

test('RunTask grants are family-wide (any revision) and cluster-scoped', () => {
  const { api, workflow, t } = build();
  const runTask = (s: cdk.Stack) => Object.values(t(s).findResources('AWS::IAM::Policy'))
    .flatMap((p) => p.Properties.PolicyDocument.Statement)
    .filter((st: { Action: string | string[] }) => [st.Action].flat().includes('ecs:RunTask'));
  const apiRes = runTask(api).flatMap((st: { Resource: unknown }) => [st.Resource].flat());
  assert.deepEqual(apiRes, [], 'the API no longer starts ECS tasks');
  assert.ok(!JSON.stringify(runTask(workflow)).includes('cull-worker-cool'));
  const wfRes = runTask(workflow).flatMap((st: { Resource: unknown }) => [st.Resource].flat()).filter((r: unknown) => typeof r === 'string');
  assert.ok(!JSON.stringify(runTask(workflow)).includes('cull-agent'));
  for (const f of ['cull-sync', 'cull-ingest', 'cull-worker']) {
    assert.ok(wfRes.includes(`arn:aws:ecs:ap-south-1:111111111111:task-definition/${f}:*`), f);
  }
});

test('CullEvent starts from S3: BuildCommands -> SyncSource? -> Ingest -> ValidateManifest -> worker', () => {
  const { workflow, t } = build();
  const st = states(t(workflow));
  assert.equal(st.BuildCommands.Next, 'SyncSource?');
  const choice = st['SyncSource?'];
  assert.equal(choice.Default, 'Ingest');
  assert.equal(choice.Choices[0].Next, 'SyncSource');
  assert.equal(choice.Choices[0].And[1].Variable, '$.sync_source');
  assert.equal(st.SyncSource.Next, 'Ingest');
  assert.equal(st.Ingest.Next, 'ValidateManifest');
  assert.equal(st.ValidateManifest.Next, 'CullWorkerStock');
  assert.match(JSON.stringify(st.Ingest), /cull-ingest|TOKEN/);
  assert.equal(st.Ingest.Parameters.Overrides.ContainerOverrides[0]['Command.$'], '$.cmd.ingest');
  for (const k of Object.keys(st)) assert.ok(!/skipsync|fromsource/i.test(k), k);
});

test('CullEvent: originals pass sits between the worker and the agent, skippable', () => {
  const { workflow, t } = build();
  const sm = Object.values(t(workflow).findResources('AWS::StepFunctions::StateMachine'))[0];
  const raw = JSON.stringify(sm.Properties.DefinitionString);
  for (const st of ['PlanOriginals', 'SyncOriginals', 'SkipOriginals?', 'AnyOriginals?']) assert.ok(raw.includes(st), st);
  // the (single, stock) worker leads to SkipOriginals?, and SyncOriginals runs cull-sync with SYNC_KINDS=originals
  assert.equal((raw.match(/\\"Next\\":\\"SkipOriginals\?\\"/g) ?? []).length, 1);
  assert.ok(raw.includes('SYNC_KINDS') && raw.includes('originals') && raw.includes('PHOTO_IDS_S3'));
  assert.ok(raw.includes('$.skip_originals'));
});

test('budgets: $120 gross (credits excluded) + $1 net, both to the alarm topic; e-mail only from context', async () => {
  const { ObservabilityStack } = await import('../lib/observability-stack');
  const env = { account: '111111111111', region: 'ap-south-1' };
  const none = Template.fromStack(new ObservabilityStack(new cdk.App(), 'Obs', { env }));
  none.resourceCountIs('AWS::Budgets::Budget', 2);
  none.resourceCountIs('AWS::SNS::Subscription', 0);
  none.hasResourceProperties('AWS::Budgets::Budget', {
    Budget: Match.objectLike({
      BudgetName: 'keepframe-competition-gross', BudgetLimit: { Amount: 120, Unit: 'USD' }, TimeUnit: 'MONTHLY',
      CostTypes: Match.objectLike({ IncludeCredit: false }),
    }),
    NotificationsWithSubscribers: Match.arrayWith([Match.objectLike({
      Notification: { NotificationType: 'ACTUAL', ComparisonOperator: 'GREATER_THAN', Threshold: 100, ThresholdType: 'PERCENTAGE' },
      Subscribers: [{ SubscriptionType: 'SNS', Address: Match.anyValue() }],
    })]),
  });
  none.hasResourceProperties('AWS::Budgets::Budget', {
    Budget: Match.objectLike({ BudgetName: 'keepframe-net-spend-actual', CostTypes: Match.objectLike({ IncludeCredit: true }) }),
    NotificationsWithSubscribers: [Match.objectLike({ Notification: Match.objectLike({ NotificationType: 'ACTUAL', Threshold: 10 }) })],
  });
  none.hasResourceProperties('AWS::SNS::TopicPolicy', {
    PolicyDocument: { Statement: Match.arrayWith([Match.objectLike({ Principal: { Service: 'budgets.amazonaws.com' }, Action: 'sns:Publish' })]) },
  });
  const withMail = Template.fromStack(new ObservabilityStack(new cdk.App(), 'Obs', { env, budgetEmail: 'ops@example.com' }));
  withMail.hasResourceProperties('AWS::SNS::Subscription', { Protocol: 'email', Endpoint: 'ops@example.com' });
});

test('UiInterimStack: arm64 zip Lambda on GET|HEAD /, /ui, /ui/{proxy+} of the existing API; writes ui-url', async () => {
  const { UiInterimStack } = await import('../lib/ui-interim-stack');
  const app = new cdk.App({ context: { adoptExisting: false } });
  const env = { account: '111111111111', region: 'ap-south-1' };
  const stack = new UiInterimStack(app, 'UiInterimStack', { env, ctx: projectContext(app, env.account, env.region) });
  const tpl = Template.fromStack(stack);
  const fns = Object.values(tpl.findResources('AWS::Lambda::Function'));
  assert.equal(fns.length, 1);
  assert.deepEqual(fns[0].Properties.Architectures, ['arm64']);
  assert.equal(fns[0].Properties.Handler, 'handler.handler');
  const keys = Object.values(tpl.findResources('AWS::ApiGatewayV2::Route')).map((r) => r.Properties.RouteKey).sort();
  assert.deepEqual(keys, ['GET /', 'GET /ui', 'GET /ui/{proxy+}', 'HEAD /', 'HEAD /ui', 'HEAD /ui/{proxy+}']);
  for (const r of Object.values(tpl.findResources('AWS::ApiGatewayV2::Route'))) {
    assert.ok(r.Properties.Target, r.Properties.RouteKey);
    assert.ok(!r.Properties.AuthorizationType || r.Properties.AuthorizationType === 'NONE');
  }
  // No bucket, no API of its own: it attaches to the ApiStack API found via SSM /keepframe/api-url.
  assert.equal(Object.keys(tpl.findResources('AWS::S3::Bucket')).length, 0);
  assert.equal(Object.keys(tpl.findResources('AWS::ApiGatewayV2::Api')).length, 0);
  tpl.hasResourceProperties('AWS::SSM::Parameter', { Name: '/keepframe/ui-url' });
  // The role can only write its own logs.
  const actions = Object.values(tpl.findResources('AWS::IAM::Policy'))
    .flatMap((p) => p.Properties.PolicyDocument.Statement)
    .flatMap((s: { Action: string | string[] }) => ([] as string[]).concat(s.Action));
  assert.deepEqual([...new Set(actions)].sort(), ['logs:CreateLogStream', 'logs:PutLogEvents']);
});

test('AgentCore runtime log group: created if absent, then 30-day retention (arm64 providers)', () => {
  const { agentCore, t } = build();
  const tpl = t(agentCore);
  const calls = Object.values(tpl.findResources('Custom::AWS')).map((r) => JSON.parse(JSON.stringify(r.Properties)));
  const flat = JSON.stringify(calls);
  assert.ok(flat.includes('putRetentionPolicy') && flat.includes('createLogGroup'), flat.slice(0, 300));
  assert.ok(flat.includes('\\"retentionInDays\\":30'), 'retention 30 days');
  assert.ok(flat.includes('ResourceAlreadyExistsException'));
  for (const f of Object.values(tpl.findResources('AWS::Lambda::Function'))) assert.deepEqual(f.Properties.Architectures, ['arm64']);
});

test('gh-bench PassRole: exactly the worker + probe task/execution roles, ecs-tasks only', () => {
  const b = build();
  const ci = new CiStack(b.compute.node.root as cdk.App, 'CiStack', { env: { account: '111111111111', region: 'ap-south-1' }, ctx: projectContext(b.compute, '111111111111', 'ap-south-1'), compute: b.compute });
  const stmts = Object.values(Template.fromStack(ci).findResources('AWS::IAM::Policy'))
    .flatMap((p) => p.Properties.PolicyDocument.Statement);
  const pass = stmts.filter((s: any) => s.Action === 'iam:PassRole');
  assert.equal(pass.length, 1);
  assert.deepEqual(pass[0].Condition, { StringEquals: { 'iam:PassedToService': 'ecs-tasks.amazonaws.com' } });
  const res = JSON.stringify(pass[0].Resource);
  assert.equal(pass[0].Resource.length, 4, res);
  assert.ok(!res.includes('ComputeStack-*') && !res.includes('"*"'), res);
});

test('AgentCore role S3: prefix-scoped reads/writes, nothing bucket-wide, no bench/', () => {
  const { agentCore, t } = build();
  const stmts = Object.values(t(agentCore).findResources('AWS::IAM::Policy'))
    .flatMap((p) => p.Properties.PolicyDocument.Statement) as any[];
  const res = (a: string) => stmts.filter((s) => [s.Action].flat().includes(a)).flatMap((s) => [s.Resource].flat()).map(String).sort();
  const B = 'arn:aws:s3:::keepframe-work-111111111111-ap-south-1';
  assert.deepEqual(res('s3:GetObject'), ['exif/', 'manifests/', 'originals/', 'preview/', 'proxy/', 'raw/', 'results/', 'sessions/', 'sheets/'].map((p) => `${B}/${p}*`));
  assert.deepEqual(res('s3:PutObject'), ['preview/', 'proxy/', 'results/', 'sessions/', 'sheets/'].map((p) => `${B}/${p}*`));
  const list = stmts.find((s) => s.Action === 's3:ListBucket');
  assert.ok(list?.Condition?.StringLike?.['s3:prefix']?.length === 9, JSON.stringify(list));
  assert.ok(!JSON.stringify(stmts).includes(`${B}/*`) && !JSON.stringify(stmts).includes('bench/'));
});

test('GitHub OIDC trust: legacy and immutable subjects, main/production only', () => {
  const b = build();
  const ci = new CiStack(b.compute.node.root as cdk.App, 'CiStack2', { env: { account: '111111111111', region: 'ap-south-1' }, ctx: projectContext(b.compute, '111111111111', 'ap-south-1'), compute: b.compute });
  const roles = Object.values(Template.fromStack(ci).findResources('AWS::IAM::Role'));
  const subs = (name: string) => roles.find((r) => r.Properties.RoleName === name)!
    .Properties.AssumeRolePolicyDocument.Statement[0].Condition.StringLike['token.actions.githubusercontent.com:sub'];
  assert.deepEqual(subs('keepframe-gh-deploy'), [
    'repo:Hitesh-Sisara/keepframe:ref:refs/heads/main', 'repo:Hitesh-Sisara@72780174/keepframe@1433262140:ref:refs/heads/main',
    'repo:Hitesh-Sisara/keepframe:environment:production', 'repo:Hitesh-Sisara@72780174/keepframe@1433262140:environment:production']);
  assert.deepEqual(subs('keepframe-gh-bench'), [
    'repo:Hitesh-Sisara/keepframe:ref:refs/heads/main', 'repo:Hitesh-Sisara@72780174/keepframe@1433262140:ref:refs/heads/main']);
});
