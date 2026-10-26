import * as cdk from 'aws-cdk-lib';
import * as autoscaling from 'aws-cdk-lib/aws-autoscaling';
import * as ec2 from 'aws-cdk-lib/aws-ec2';
import * as ecs from 'aws-cdk-lib/aws-ecs';
import * as iam from 'aws-cdk-lib/aws-iam';
import * as logs from 'aws-cdk-lib/aws-logs';
import { NagSuppressions } from './nag';
import { Construct } from 'constructs';
import { NAMES, PROJECT, ProjectContext, SOURCE_KEYS } from './config';
import { DataStack, grantList, grantObjects } from './data-stack';
import { NetworkStack } from './network-stack';

export interface ComputeStackProps extends cdk.StackProps {
  ctx: ProjectContext;
  network: NetworkStack;
  data: DataStack;
}

/**
 * ECS cluster with:
 *  - Fargate ARM64 `cull-worker` (4 vCPU / 8 GiB, stock OpenCV 5 image) — the default worker;
 *  - Fargate ARM64 `cull-ingest` (1 vCPU / 2 GiB, worker image) — the Ingest step: reads the photographer's
 *    upload in raw/<eventId>/, writes exif/ sidecars and the manifest;
 *  - Fargate ARM64 `cull-sync` (1 vCPU / 2 GiB, rclone) — optional SyncSource import from any S3-compatible store;
 *  (the agent runs on AgentCore Runtime, AgentCoreStack; the Fargate `cull-agent` fallback was removed 2026-10-08
 *  after a real event completed on AgentCore);
 *  - optional, labelled x86 benchmark baseline (c7i, EC2 capacity provider) behind `-c enableX86Baseline=true`
 *    (default off). COOL (Marketplace-billed) was removed 2026-10-08.
 */
export class ComputeStack extends cdk.Stack {
  readonly cluster: ecs.Cluster;
  readonly workerTask: ecs.FargateTaskDefinition;
  readonly syncTask: ecs.FargateTaskDefinition;
  readonly ingestTask: ecs.FargateTaskDefinition;
  readonly probeTask: ecs.FargateTaskDefinition;
  readonly logGroups: logs.ILogGroup[] = [];
  readonly containers = { worker: 'worker', sync: 'sync', ingest: 'ingest' } as const;

  constructor(scope: Construct, id: string, props: ComputeStackProps) {
    super(scope, id, props);
    const { ctx, network, data } = props;
    const { decisions, traces } = data;

    this.cluster = new ecs.Cluster(this, 'Cluster', {
      clusterName: NAMES.cluster,
      vpc: network.vpc,
      containerInsightsV2: ecs.ContainerInsights.ENABLED,
      enableFargateCapacityProviders: true,
    });

    const logGroup = (name: string) => {
      const lg = new logs.LogGroup(this, `Logs-${name}`, {
        logGroupName: `/${PROJECT}/${name}`,
        retention: logs.RetentionDays.ONE_MONTH,
        removalPolicy: cdk.RemovalPolicy.DESTROY,
      });
      this.logGroups.push(lg);
      return lg;
    };

    // ---------------- IAM (docs/ARCHITECTURE.md "IAM summary") ----------------
    // Execution roles: ECR pull + logs only (+ the specific secrets injected into that task).
    const executionRole = (name: string) =>
      new iam.Role(this, `${name}ExecRole`, {
        assumedBy: new iam.ServicePrincipal('ecs-tasks.amazonaws.com'),
        description: `KeepFrame ${name}: ECR pull + CloudWatch Logs only`,
      });
    const taskRole = (name: string, description: string) =>
      new iam.Role(this, name, { assumedBy: new iam.ServicePrincipal('ecs-tasks.amazonaws.com'), description });

    const putMetrics = new iam.PolicyStatement({
      actions: ['cloudwatch:PutMetricData'],
      resources: ['*'],
      conditions: { StringEquals: { 'cloudwatch:namespace': NAMES.metricNamespace } },
    });
    const ddbWrite = ['dynamodb:PutItem', 'dynamodb:BatchWriteItem', 'dynamodb:UpdateItem', 'dynamodb:GetItem', 'dynamodb:Query'];
    const grantWorker = (role: iam.Role) => {
      grantObjects(data.bucketName, role, ['s3:GetObject', 's3:PutObject'], ['*']);
      grantList(data.bucketName, role);
      for (const t of [decisions, traces]) {
        role.addToPolicy(new iam.PolicyStatement({ actions: ddbWrite, resources: [t.tableArn, `${t.tableArn}/index/*`] }));
      }
      role.addToPolicy(putMetrics);
    };

    // CullWorkerTaskRole
    const workerRole = taskRole('CullWorkerTaskRole', 'cull-worker (stock): S3 work bucket, both tables, EMF metrics');
    grantWorker(workerRole);

    // CullIngestTaskRole: reads the upload in raw/, writes safe-id copies to raw/, exif/ sidecars and the manifest.
    const ingestRole = taskRole('CullIngestTaskRole', 'cull-ingest: read raw/ exif/ manifests/, write raw/ exif/ manifests/');
    grantObjects(data.bucketName, ingestRole, ['s3:GetObject'], ['raw/*', 'exif/*', 'manifests/*']);
    grantObjects(data.bucketName, ingestRole, ['s3:PutObject'], ['raw/*', 'exif/*', 'manifests/*']);
    grantList(data.bucketName, ingestRole, ['raw/*', 'exif/*', 'manifests/*']);

    // CullSyncTaskRole (optional SyncSource): manifests read, pixels/EXIF write, list. The source key comes via the execution role.
    const syncRole = taskRole('CullSyncTaskRole', 'cull-sync: read manifests/, write raw/ exif/ originals/');
    grantObjects(data.bucketName, syncRole, ['s3:GetObject'], ['manifests/*', 'originals-requests/*']);
    grantObjects(data.bucketName, syncRole, ['s3:PutObject', 's3:AbortMultipartUpload'], ['raw/*', 'exif/*', 'originals/*']);
    // rclone --no-traverse --size-only HEADs each destination object before writing (delta re-sync);
    // HeadObject is authorised by s3:GetObject.
    grantObjects(data.bucketName, syncRole, ['s3:GetObject'], ['raw/*', 'exif/*', 'originals/*']);
    grantList(data.bucketName, syncRole);
    syncRole.addToPolicy(putMetrics);

    // ---------------- container plumbing ----------------
    const image = (repo: keyof typeof data.repos) =>
      ecs.ContainerImage.fromEcrRepository(data.repos[repo], ctx.imageTag);
    const sourceSecrets = Object.fromEntries(SOURCE_KEYS.map((k) => [k, ecs.Secret.fromSecretsManager(data.sourceSecret, k)]));
    const commonEnv = (flavor: string) => ({
      AWS_REGION: this.region,
      WORK_BUCKET: data.bucketName,
      DECISIONS_TABLE: decisions.tableName,
      TRACES_TABLE: traces.tableName,
      CULL_DECISIONS_TABLE: decisions.tableName,
      CULL_TRACES_TABLE: traces.tableName,
      CULL_FLAVOR: flavor,
      CULL_OPENCV_FLAVOR: flavor,
      CULL_METRICS_NAMESPACE: NAMES.metricNamespace,
      OPENCV_FORCE_DNN_ENGINE: '3',
      CULL_MODELS_DIR: '/opt/models',
      CULL_CACHE_DIR: '/tmp/cull',
      HOME: '/tmp',
    });
    const arm64 = { cpuArchitecture: ecs.CpuArchitecture.ARM64, operatingSystemFamily: ecs.OperatingSystemFamily.LINUX };

    // ---------------- Fargate ARM64: cull-worker (stock) ----------------
    this.workerTask = new ecs.FargateTaskDefinition(this, 'WorkerTask', {
      family: NAMES.families.worker,
      cpu: 4096,
      memoryLimitMiB: 8192,
      ephemeralStorageGiB: 50,
      runtimePlatform: arm64,
      taskRole: workerRole,
      executionRole: executionRole('Worker'),
    });
    this.workerTask.addContainer(this.containers.worker, {
      containerName: this.containers.worker,
      image: image('worker'),
      user: '1000',
      environment: { ...commonEnv('stock'), CULL_WORKERS: '4', CULL_INSTANCE_TYPE: 'fargate-arm64-4vcpu' },
      logging: ecs.LogDrivers.awsLogs({ streamPrefix: 'worker', logGroup: logGroup('worker') }),
      stopTimeout: cdk.Duration.seconds(60),
    });

    // ---------------- Fargate ARM64: cull-ingest (the Ingest step, worker image) ----------------
    this.ingestTask = new ecs.FargateTaskDefinition(this, 'IngestTask', {
      family: NAMES.families.ingest,
      cpu: 1024,
      memoryLimitMiB: 2048,
      runtimePlatform: arm64,
      taskRole: ingestRole,
      executionRole: executionRole('Ingest'),
    });
    this.ingestTask.addContainer(this.containers.ingest, {
      containerName: this.containers.ingest,
      image: image('worker'),
      user: '1000',
      entryPoint: ['python', '-m', 'keepframe.io.ingest_upload'],
      environment: { AWS_REGION: this.region, WORK_BUCKET: data.bucketName, HOME: '/tmp' },
      logging: ecs.LogDrivers.awsLogs({ streamPrefix: 'ingest', logGroup: logGroup('ingest') }),
    });

    // ---------------- Fargate ARM64: cull-sync (optional SyncSource) ----------------
    const missingSource = Object.entries(ctx.sourceBuckets).filter(([, v]) => !v).map(([k]) => k);
    if (missingSource.length) {
      cdk.Annotations.of(this).addWarningV2(
        'keepframe:source-buckets-unset',
        `Source bucket names not set (${missingSource.join(', ')}); the optional SyncSource task will refuse to start. ` +
          'Pass -c sourceBucketProcessed=... or export SOURCE_BUCKET_* before synth (operator-held, see config/source.example.env).',
      );
    }
    this.syncTask = new ecs.FargateTaskDefinition(this, 'SyncTask', {
      family: NAMES.families.sync,
      cpu: 1024,
      memoryLimitMiB: 2048,
      runtimePlatform: arm64,
      taskRole: syncRole,
      executionRole: executionRole('Sync'),
    });
    this.syncTask.addContainer(this.containers.sync, {
      containerName: this.containers.sync,
      image: image('sync'),
      environment: {
        AWS_REGION: this.region,
        WORK_BUCKET: data.bucketName,
        SYNC_KINDS: 'processed,exif',
        TRANSFERS: '32',
        ...ctx.sourceBuckets,
        ...ctx.sourceEndpoint,
      },
      secrets: sourceSecrets,
      logging: ecs.LogDrivers.awsLogs({ streamPrefix: 'sync', logGroup: logGroup('sync') }),
    });

    // ---------------- Fargate ARM64: cull-probe (proves cv2 5.x on Fargate ARM64; 0.25 vCPU) ----------------
    // RunTask overrides cannot change ENTRYPOINT, so the probe is its own family on the worker image.
    this.probeTask = new ecs.FargateTaskDefinition(this, 'ProbeTask', {
      family: NAMES.families.probe,
      cpu: 256,
      memoryLimitMiB: 512,
      runtimePlatform: arm64,
      executionRole: executionRole('Probe'),
    });
    this.probeTask.addContainer('probe', {
      containerName: 'probe',
      image: image('worker'),
      user: '1000',
      entryPoint: ['python', '-c'],
      command: ['import cv2, platform; print(cv2.__version__, cv2.__file__, platform.machine())'],
      environment: { OPENCV_FORCE_DNN_ENGINE: '3', HOME: '/tmp' },
      logging: ecs.LogDrivers.awsLogs({ streamPrefix: 'probe', logGroup: logGroup('probe') }),
    });

    // ---------------- optional x86 baseline (labelled benchmark only) ----------------
    if (ctx.enableX86Baseline) {
      const instanceRole = new iam.Role(this, 'X86BaselineInstanceRole', {
        assumedBy: new iam.ServicePrincipal('ec2.amazonaws.com'),
        description: 'x86 benchmark baseline ECS container instance: ECS agent registration + SSM Session Manager',
        managedPolicies: [
          iam.ManagedPolicy.fromAwsManagedPolicyName('service-role/AmazonEC2ContainerServiceforEC2Role'),
          iam.ManagedPolicy.fromAwsManagedPolicyName('AmazonSSMManagedInstanceCore'),
        ],
      });
      // LABELLED x86 BENCHMARK BASELINE ONLY (docs/COOL_BENCHMARK.md §1, cut order #4). Never on the product path.
      const baselineLt = new ec2.LaunchTemplate(this, 'X86BaselineLaunchTemplate', {
        launchTemplateName: `${PROJECT}-x86-baseline-c7i`,
        instanceType: new ec2.InstanceType('c7i.4xlarge'),
        machineImage: ecs.EcsOptimizedImage.amazonLinux2023(ecs.AmiHardwareType.STANDARD),
        userData: ec2.UserData.forLinux(),
        role: instanceRole,
        securityGroup: network.egressOnlySg,
        associatePublicIpAddress: true,
        requireImdsv2: true,
        httpPutResponseHopLimit: 2,
      });
      const baselineAsg = new autoscaling.AutoScalingGroup(this, 'X86BaselineAsg', {
        autoScalingGroupName: `${PROJECT}-x86-baseline-c7i`,
        vpc: network.vpc,
        vpcSubnets: { subnetType: ec2.SubnetType.PUBLIC },
        launchTemplate: baselineLt,
        minCapacity: 0,
        maxCapacity: 1,
      });
      const baselineCp = new ecs.AsgCapacityProvider(this, 'X86BaselineCapacityProvider', {
        capacityProviderName: `${PROJECT}-x86-baseline-c7i`,
        autoScalingGroup: baselineAsg,
        enableManagedTerminationProtection: false,
      });
      this.cluster.addAsgCapacityProvider(baselineCp);
      new cdk.CfnOutput(this, 'X86BaselineCapacityProviderName', { value: baselineCp.capacityProviderName });
      NagSuppressions.addResourceSuppressions(baselineAsg, [
        { id: 'AwsSolutions-AS3', reason: 'Benchmark-only ASG (min 0, max 1); scaling notifications not needed.' },
      ], true);
    }

    // ---------------- outputs ----------------
    new cdk.CfnOutput(this, 'ClusterArn', { value: this.cluster.clusterArn });
    new cdk.CfnOutput(this, 'WorkerTaskDefArn', { value: this.workerTask.taskDefinitionArn });
    new cdk.CfnOutput(this, 'SyncTaskDefArn', { value: this.syncTask.taskDefinitionArn });
    new cdk.CfnOutput(this, 'IngestTaskDefArn', { value: this.ingestTask.taskDefinitionArn });
    new cdk.CfnOutput(this, 'ProbeTaskDefArn', { value: this.probeTask.taskDefinitionArn });

    // ---------------- cdk-nag suppressions (documented) ----------------
    NagSuppressions.addResourceSuppressions(
      [workerRole, syncRole, ingestRole],
      [
        { id: 'AwsSolutions-IAM5', appliesTo: [{ regex: '/^Resource::arn:aws:s3:::.*\\/\\*$/' }, { regex: '/^Resource::<.*Arn>\\/\\*$/' }, { regex: '/^Resource::.*\\/index\\/\\*$/' }],
          reason: 'Object-level S3 grants are scoped to one bucket (and to prefixes where docs/ARCHITECTURE.md "IAM summary" defines them); index/* covers the table\'s own GSIs.' },
        { id: 'AwsSolutions-IAM5', appliesTo: ['Resource::*'],
          reason: 'cloudwatch:PutMetricData has no resource type (scoped by the cloudwatch:namespace condition).' },
      ],
      true,
    );
    NagSuppressions.addResourceSuppressions(this, [
      { id: 'AwsSolutions-IAM4', appliesTo: [
        'Policy::arn:<AWS::Partition>:iam::aws:policy/service-role/AmazonEC2ContainerServiceforEC2Role',
        'Policy::arn:<AWS::Partition>:iam::aws:policy/AmazonSSMManagedInstanceCore',
      ], reason: 'AWS-maintained minimal policies for an ECS container instance and Session Manager access.' },
      { id: 'AwsSolutions-IAM5', appliesTo: ['Resource::*', 'Action::ecs:Submit*'],
        reason: 'ecr:GetAuthorizationToken and logs/ECS agent actions have no resource scoping; CDK-generated execution-role and drain-hook statements.' },
      { id: 'AwsSolutions-ECS2', reason: 'Environment variables hold names, ARNs-free config and model ids only; credentials are injected as secrets.' },
      { id: 'AwsSolutions-AS3', reason: 'ASG is min 0 / max 1, scaled only by the ECS capacity provider; scaling notifications are not needed.' },
      { id: 'AwsSolutions-L1', reason: 'CDK-managed drain-hook Lambda; runtime chosen by aws-cdk-lib.' },
      { id: 'AwsSolutions-SNS2', reason: 'CDK-managed drain-hook topic carries ASG lifecycle events only (no data).' },
      { id: 'AwsSolutions-SNS3', reason: 'CDK-managed drain-hook topic; only the ASG publishes to it.' },
      { id: 'AwsSolutions-EC26', reason: 'Root volume is encrypted via the launch template block device (EBS default key).' },
    ], true);
  }
}
