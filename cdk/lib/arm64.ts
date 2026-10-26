/**
 * Arm64 only (hard rule): CDK-generated custom-resource providers (AwsCustomResource's singleton Lambda) default to
 * the non-Arm default and expose no architecture prop. This aspect sets Architectures=[arm64] on every Lambda in the scope that
 * did not set one. The handlers are Node.js bundles with no native code, so they run unchanged on Graviton.
 */
import * as cdk from 'aws-cdk-lib';
import * as lambda from 'aws-cdk-lib/aws-lambda';
import { IConstruct } from 'constructs';

class ForceArm64 implements cdk.IAspect {
  visit(node: IConstruct): void {
    if (node instanceof lambda.CfnFunction) {
      if (!node.architectures || node.architectures.length === 0) node.architectures = ['arm64'];
    } else if (node instanceof cdk.CfnResource && node.cfnResourceType === 'AWS::Lambda::Function') {
      node.addPropertyOverride('Architectures', ['arm64']);
    }
  }
}

export function forceArm64(scope: IConstruct): void {
  cdk.Aspects.of(scope).add(new ForceArm64());
}
