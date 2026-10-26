import * as cdk from 'aws-cdk-lib';
import * as ec2 from 'aws-cdk-lib/aws-ec2';
import * as logs from 'aws-cdk-lib/aws-logs';
import { NagSuppressions } from './nag';
import { Construct } from 'constructs';
import { PROJECT } from './config';

/**
 * docs/ARCHITECTURE.md "Components": one VPC, two public subnets, no NAT gateway (saves ~$32/month),
 * S3 + DynamoDB gateway endpoints so bulk pixel and decision traffic never leaves the AWS network, and one egress-only security
 * group shared by every task (no ingress rules at all).
 */
export class NetworkStack extends cdk.Stack {
  readonly vpc: ec2.Vpc;
  readonly egressOnlySg: ec2.SecurityGroup;

  /** Pinned AZs (both offer c8g and Fargate ARM64): synth needs no AZ context lookup or credentials. */
  get availabilityZones(): string[] {
    return [`${this.region}a`, `${this.region}b`];
  }

  constructor(scope: Construct, id: string, props?: cdk.StackProps) {
    super(scope, id, props);

    const flowLogs = new logs.LogGroup(this, 'FlowLogs', {
      logGroupName: `/${PROJECT}/vpc-flow-logs`,
      retention: logs.RetentionDays.ONE_MONTH,
      removalPolicy: cdk.RemovalPolicy.DESTROY,
    });

    this.vpc = new ec2.Vpc(this, 'Vpc', {
      vpcName: PROJECT,
      ipAddresses: ec2.IpAddresses.cidr('10.42.0.0/16'),
      availabilityZones: this.availabilityZones,
      natGateways: 0,
      subnetConfiguration: [{ name: 'public', subnetType: ec2.SubnetType.PUBLIC, cidrMask: 20 }],
      gatewayEndpoints: {
        S3: { service: ec2.GatewayVpcEndpointAwsService.S3 },
        DynamoDB: { service: ec2.GatewayVpcEndpointAwsService.DYNAMODB },
      },
      flowLogs: {
        rejected: { destination: ec2.FlowLogDestination.toCloudWatchLogs(flowLogs), trafficType: ec2.FlowLogTrafficType.REJECT },
      },
      restrictDefaultSecurityGroup: true,
    });

    this.egressOnlySg = new ec2.SecurityGroup(this, 'EgressOnly', {
      vpc: this.vpc,
      securityGroupName: `${PROJECT}-egress-only`,
      description: 'KeepFrame tasks: no ingress; egress HTTPS to AWS APIs (Bedrock, S3, ECR), optional source store and model endpoint',
      allowAllOutbound: true,
    });

    new cdk.CfnOutput(this, 'VpcId', { value: this.vpc.vpcId });
    new cdk.CfnOutput(this, 'PublicSubnets', { value: this.vpc.publicSubnets.map((s) => s.subnetId).join(',') });
    new cdk.CfnOutput(this, 'EgressOnlySgId', { value: this.egressOnlySg.securityGroupId });

    NagSuppressions.addResourceSuppressions(this.egressOnlySg, [
      {
        id: 'AwsSolutions-EC23',
        reason: 'No ingress rules exist; the finding is about 0.0.0.0/0 egress, which tasks need for AWS APIs, the optional import source and the optional Anthropic-compatible endpoint (no NAT).',
      },
    ]);
  }
}
