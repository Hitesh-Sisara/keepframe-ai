import * as cdk from 'aws-cdk-lib';
import * as iam from 'aws-cdk-lib/aws-iam';
import { NagSuppressions } from './nag';
import { Construct } from 'constructs';
import type { ComputeStack } from './compute-stack';
import { NAMES, PROJECT, ProjectContext } from './config';

export interface CiStackProps extends cdk.StackProps {
  ctx: ProjectContext;
  /** bench.yml runs these task definitions; PassRole is limited to exactly their task + execution roles. */
  compute: ComputeStack;
}

/**
 * CI (GitHub OIDC): GitHub Actions OIDC provider and two roles, no long-lived keys.
 *  - keepframe-gh-deploy (main branch only): assume the CDK bootstrap roles (deploy/publish/lookup),
 *    push the project's ECR repos, read /keepframe/* SSM parameters.
 *  - keepframe-gh-bench (workflow_dispatch on main): run the worker task definitions and read/write bench/.
 */
export class CiStack extends cdk.Stack {
  constructor(scope: Construct, id: string, props: CiStackProps) {
    super(scope, id, props);
    const repo = props.ctx.githubRepo;
    const qualifier = String(this.node.tryGetContext('@aws-cdk/core:bootstrapQualifier') ?? 'hnb659fds');

    const provider = new iam.OidcProviderNative(this, 'GitHubOidc', {
      url: 'https://token.actions.githubusercontent.com',
      clientIds: ['sts.amazonaws.com'],
    });
    const principal = (subs: string[]) =>
      new iam.OpenIdConnectPrincipal(provider, {
        StringEquals: { 'token.actions.githubusercontent.com:aud': 'sts.amazonaws.com' },
        StringLike: { 'token.actions.githubusercontent.com:sub': subs },
      });
    // GitHub immutable OIDC subjects (repo setting use_immutable_subject=true) prefix the sub with owner/repo ids:
    // repo:<owner>@<ownerId>/<repo>@<repoId>:... . Accept that form (pins this exact repo) and the legacy name form.
    const immutable = String(this.node.tryGetContext('githubSubjectPrefix') ?? 'repo:Hitesh-Sisara@72780174/keepframe@1433262140');
    const subs = (suffix: string) => [`repo:${repo}:${suffix}`, `${immutable}:${suffix}`];
    const mainOnly = [...subs('ref:refs/heads/main'), ...subs('environment:production')];

    const deploy = new iam.Role(this, 'DeployRole', {
      roleName: `${PROJECT}-gh-deploy`,
      assumedBy: principal(mainOnly),
      description: `GitHub Actions deploy for ${repo} (main): CDK bootstrap roles + project ECR push`,
      maxSessionDuration: cdk.Duration.hours(2),
    });
    deploy.addToPolicy(new iam.PolicyStatement({
      actions: ['sts:AssumeRole', 'sts:TagSession'],
      resources: ['deploy', 'file-publishing', 'image-publishing', 'lookup'].map(
        (r) => `arn:aws:iam::${this.account}:role/cdk-${qualifier}-${r}-role-${this.account}-${this.region}`),
    }));
    deploy.addToPolicy(new iam.PolicyStatement({ actions: ['ecr:GetAuthorizationToken'], resources: ['*'] }));
    deploy.addToPolicy(new iam.PolicyStatement({
      actions: ['ecr:BatchCheckLayerAvailability', 'ecr:BatchGetImage', 'ecr:CompleteLayerUpload', 'ecr:InitiateLayerUpload',
        'ecr:PutImage', 'ecr:UploadLayerPart', 'ecr:DescribeImages'],
      resources: Object.values(NAMES.repos).map((r) => `arn:aws:ecr:${this.region}:${this.account}:repository/${r}`),
    }));
    deploy.addToPolicy(new iam.PolicyStatement({
      actions: ['ssm:GetParameter', 'ssm:GetParameters'],
      resources: [`arn:aws:ssm:${this.region}:${this.account}:parameter/${PROJECT}/*`],
    }));

    const bench = new iam.Role(this, 'BenchRole', {
      roleName: `${PROJECT}-gh-bench`,
      assumedBy: principal(subs('ref:refs/heads/main')),
      description: `GitHub Actions bench.yml for ${repo}: run worker tasks, read/write bench/`,
      maxSessionDuration: cdk.Duration.hours(4),
    });
    const td = (family: string) => `arn:aws:ecs:${this.region}:${this.account}:task-definition/${family}:*`;
    bench.addToPolicy(new iam.PolicyStatement({
      actions: ['ecs:RunTask'],
      resources: [td(NAMES.families.worker), td(NAMES.families.workerBaseline), td(NAMES.families.probe)],
    }));
    bench.addToPolicy(new iam.PolicyStatement({ actions: ['ecs:DescribeTasks', 'ecs:DescribeTaskDefinition', 'ecs:ListTasks'], resources: ['*'] }));
    // Exactly the roles of the families RunTask allows (SECURITY_REVIEW #19), not every ComputeStack-* role.
    const passable = [props.compute.workerTask, props.compute.probeTask]
      .flatMap((td) => [td.taskRole.roleArn, td.obtainExecutionRole().roleArn]);
    bench.addToPolicy(new iam.PolicyStatement({
      actions: ['iam:PassRole'],
      resources: passable,
      conditions: { StringEquals: { 'iam:PassedToService': 'ecs-tasks.amazonaws.com' } },
    }));
    bench.addToPolicy(new iam.PolicyStatement({
      actions: ['s3:GetObject', 's3:PutObject'],
      resources: [`arn:aws:s3:::${props.ctx.workBucketName}/bench/*`],
    }));
    bench.addToPolicy(new iam.PolicyStatement({
      actions: ['s3:ListBucket'], resources: [`arn:aws:s3:::${props.ctx.workBucketName}`], conditions: { StringLike: { 's3:prefix': ['bench/*'] } },
    }));
    bench.addToPolicy(new iam.PolicyStatement({
      actions: ['logs:GetLogEvents', 'logs:FilterLogEvents'],
      resources: [`arn:aws:logs:${this.region}:${this.account}:log-group:/${PROJECT}/*`],
    }));
    bench.addToPolicy(new iam.PolicyStatement({ actions: ['cloudtrail:LookupEvents', 'cloudformation:DescribeStacks'], resources: ['*'] }));
    bench.addToPolicy(new iam.PolicyStatement({
      actions: ['ssm:GetParameter'], resources: [`arn:aws:ssm:${this.region}:${this.account}:parameter/${PROJECT}/*`],
    }));

    new cdk.CfnOutput(this, 'DeployRoleArn', { value: deploy.roleArn });
    new cdk.CfnOutput(this, 'BenchRoleArn', { value: bench.roleArn });
    new cdk.CfnOutput(this, 'OidcProviderArn', { value: provider.oidcProviderArn });

    NagSuppressions.addResourceSuppressions([deploy, bench], [
      { id: 'AwsSolutions-IAM5', reason: 'ecr:GetAuthorizationToken, ECS Describe*/ListTasks and cloudtrail:LookupEvents have no resource scoping; other wildcards are prefix-scoped (task-definition revisions, /keepframe/* params, bench/*); PassRole lists exact role ARNs.' },
    ], true);
  }
}
