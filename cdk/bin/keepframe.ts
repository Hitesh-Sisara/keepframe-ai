#!/usr/bin/env node
/**
 * KeepFrame CDK app. ARM64 everywhere: Lambda ARM_64, Fargate ARM64, AgentCore Runtime (ARM64 container).
 * The only x86 resource is the optional, labelled benchmark baseline (c7i) behind -c enableX86Baseline=true.
 *
 * Context (cdk.json or -c): adoptExisting, enableX86Baseline, imageTag (ECS images and the AgentCore runtime),
 * corsOrigins, githubRepo, budgetEmail (optional alarm/budget e-mail). See docs/DEPLOY.md.
 */
import * as cdk from 'aws-cdk-lib';
import { AgentCoreStack } from '../lib/agentcore-stack';
import { ApiStack } from '../lib/api-stack';
import { CiStack } from '../lib/ci-stack';
import { ComputeStack } from '../lib/compute-stack';
import { projectContext, tagProject } from '../lib/config';
import { DataStack } from '../lib/data-stack';
import { ProjectNagChecks } from '../lib/nag';
import { NetworkStack } from '../lib/network-stack';
import { ObservabilityStack } from '../lib/observability-stack';
import { UiInterimStack } from '../lib/ui-interim-stack';
import { UiStack } from '../lib/ui-stack';
import { WorkflowStack } from '../lib/workflow-stack';

const app = new cdk.App();
const account = process.env.CDK_DEFAULT_ACCOUNT;
const region = process.env.CDK_DEFAULT_REGION ?? 'ap-south-1';
if (!account) throw new Error('CDK_DEFAULT_ACCOUNT is not set (run with AWS credentials loaded)');
const env = { account, region };
const ctx = projectContext(app, account, region);
const desc = (s: string) => `KeepFrame (keepframe): ${s}`;

const network = new NetworkStack(app, 'NetworkStack', { env, description: desc('VPC, public subnets, gateway endpoints') });
const data = new DataStack(app, 'DataStack', { env, ctx, description: desc('work bucket, DynamoDB, ECR, secrets, SSM') });
const compute = new ComputeStack(app, 'ComputeStack', { env, ctx, network, data, description: desc('ECS cluster, Fargate ARM64 task definitions') });
const agentCore = new AgentCoreStack(app, 'AgentCoreStack', { env, ctx, data, description: desc('AgentCore Runtime for the agent (ARM64 container)') });
const workflow = new WorkflowStack(app, 'WorkflowStack', { env, network, data, compute, agentRuntimeArn: agentCore.runtimeArn, description: desc('Step Functions CullEvent + Lambdas') });
const corsOrigins = String(app.node.tryGetContext('corsOrigins') ?? '').split(',').map((s) => s.trim()).filter(Boolean);
new ApiStack(app, 'ApiStack', { env, ctx, network, data, compute, workflow, agentCore, corsOrigins, description: desc('HTTP API + Lambda arm64') });
new UiStack(app, 'UiStack', { env, ctx, description: desc('review UI on S3 + CloudFront (OAC)') });
// Interim while CloudFront needs account verification: the UI served by the HTTP API under /ui (docs/DEPLOY.md).
new UiInterimStack(app, 'UiInterimStack', { env, ctx, description: desc('interim review UI served by the HTTP API under /ui (arm64 Lambda)') });
const budgetEmail = String(app.node.tryGetContext('budgetEmail') ?? '').trim() || undefined;
new ObservabilityStack(app, 'ObservabilityStack', { env, budgetEmail, description: desc('dashboard KeepFrame + alarms + AWS Budgets') });
new CiStack(app, 'CiStack', { env, ctx, compute, description: desc('GitHub OIDC deploy/bench roles') });

tagProject(app);
cdk.Validations.of(app).addPlugins(new ProjectNagChecks(app, { verbose: true, writeSuppressionsToCloudFormation: true }));
