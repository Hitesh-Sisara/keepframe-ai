import * as fs from 'node:fs';
import * as path from 'node:path';
import * as cdk from 'aws-cdk-lib';
import * as cloudfront from 'aws-cdk-lib/aws-cloudfront';
import * as origins from 'aws-cdk-lib/aws-cloudfront-origins';
import * as s3 from 'aws-cdk-lib/aws-s3';
import * as s3deploy from 'aws-cdk-lib/aws-s3-deployment';
import * as ssm from 'aws-cdk-lib/aws-ssm';
import { NagSuppressions } from './nag';
import { Construct } from 'constructs';
import { NAMES, PROJECT, ProjectContext } from './config';

export interface UiStackProps extends cdk.StackProps {
  ctx: ProjectContext;
}

/**
 * docs/ARCHITECTURE.md "Components": Next.js static export (ui/out) → private S3 bucket → CloudFront with Origin Access Control.
 * The UI holds no secrets: the user pastes a judge/team token, and NEXT_PUBLIC_API_BASE (the HTTP API URL)
 * is baked in at build time (`make ui-build` reads it from SSM /keepframe/api-url). Two-step deploy:
 * ApiStack first, then build the UI, then UiStack (docs/DEPLOY.md).
 *
 * The UI stack is created before the API knows the CloudFront origin; `make deploy` redeploys ApiStack
 * with `-c corsOrigins=<url>` from SSM /keepframe/ui-url so CORS is locked to this distribution.
 */
export class UiStack extends cdk.Stack {
  readonly distribution: cloudfront.Distribution;

  constructor(scope: Construct, id: string, props: UiStackProps) {
    super(scope, id, props);
    const outDir = path.resolve(__dirname, '..', props.ctx.srcRoot, 'ui', 'out');

    const logs = new s3.Bucket(this, 'CdnLogs', {
      blockPublicAccess: s3.BlockPublicAccess.BLOCK_ALL,
      encryption: s3.BucketEncryption.S3_MANAGED,
      enforceSSL: true,
      objectOwnership: s3.ObjectOwnership.BUCKET_OWNER_PREFERRED, // CloudFront standard logging needs ACLs
      lifecycleRules: [{ expiration: cdk.Duration.days(30) }],
      removalPolicy: cdk.RemovalPolicy.DESTROY,
      autoDeleteObjects: true,
    });
    const site = new s3.Bucket(this, 'Site', {
      blockPublicAccess: s3.BlockPublicAccess.BLOCK_ALL,
      encryption: s3.BucketEncryption.S3_MANAGED,
      enforceSSL: true,
      objectOwnership: s3.ObjectOwnership.BUCKET_OWNER_ENFORCED,
      serverAccessLogsBucket: logs,
      serverAccessLogsPrefix: 'site-access/',
      removalPolicy: cdk.RemovalPolicy.DESTROY,
      autoDeleteObjects: true,
    });

    // trailingSlash export: /runs → /runs/index.html, /runs/ → /runs/index.html; files with a dot pass through.
    const rewrite = new cloudfront.Function(this, 'IndexRewrite', {
      functionName: `${PROJECT}-index-rewrite`,
      runtime: cloudfront.FunctionRuntime.JS_2_0,
      comment: 'Static export: append index.html to directory-style paths',
      code: cloudfront.FunctionCode.fromInline(`
function handler(event) {
  var r = event.request;
  var u = r.uri;
  if (u.endsWith('/')) { r.uri = u + 'index.html'; }
  else if (u.split('/').pop().indexOf('.') === -1) { r.uri = u + '/index.html'; }
  return r;
}`),
    });

    const headers = new cloudfront.ResponseHeadersPolicy(this, 'SecurityHeaders', {
      responseHeadersPolicyName: `${PROJECT}-ui-security`,
      securityHeadersBehavior: {
        contentTypeOptions: { override: true },
        frameOptions: { frameOption: cloudfront.HeadersFrameOption.DENY, override: true },
        referrerPolicy: { referrerPolicy: cloudfront.HeadersReferrerPolicy.NO_REFERRER, override: true },
        strictTransportSecurity: { accessControlMaxAge: cdk.Duration.days(365), includeSubdomains: true, override: true },
        xssProtection: { protection: true, modeBlock: true, override: true },
      },
    });

    this.distribution = new cloudfront.Distribution(this, 'Cdn', {
      comment: 'KeepFrame review UI',
      defaultRootObject: 'index.html',
      priceClass: cloudfront.PriceClass.PRICE_CLASS_200, // includes India edges
      enableLogging: true,
      logBucket: logs,
      logFilePrefix: 'cdn/',
      defaultBehavior: {
        origin: origins.S3BucketOrigin.withOriginAccessControl(site),
        viewerProtocolPolicy: cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
        cachePolicy: cloudfront.CachePolicy.CACHING_OPTIMIZED,
        responseHeadersPolicy: headers,
        functionAssociations: [{ function: rewrite, eventType: cloudfront.FunctionEventType.VIEWER_REQUEST }],
      },
      errorResponses: [
        { httpStatus: 404, responseHttpStatus: 404, responsePagePath: '/404.html', ttl: cdk.Duration.minutes(1) },
        { httpStatus: 403, responseHttpStatus: 404, responsePagePath: '/404.html', ttl: cdk.Duration.minutes(1) },
      ],
    });

    // Deploy ui/out if it has been built; otherwise a placeholder page so the stack still deploys.
    const built = fs.existsSync(path.join(outDir, 'index.html'));
    new s3deploy.BucketDeployment(this, 'DeploySite', {
      sources: built
        ? [s3deploy.Source.asset(outDir)]
        : [s3deploy.Source.data('index.html', '<!doctype html><title>KeepFrame</title><p>PLACEHOLDER: UI not built yet. Run <code>make ui-build ui-deploy</code>.</p>'),
           s3deploy.Source.data('404.html', '<!doctype html><title>Not found</title><p>Not found</p>')],
      destinationBucket: site,
      distribution: this.distribution,
      distributionPaths: ['/*'],
      prune: true,
      memoryLimit: 512,
    });

    const url = `https://${this.distribution.distributionDomainName}`;
    new ssm.StringParameter(this, 'UiUrlParam', { parameterName: NAMES.params.uiUrl, stringValue: url, description: 'KeepFrame review UI URL' });
    new cdk.CfnOutput(this, 'UiUrl', { value: url });
    new cdk.CfnOutput(this, 'UiBuilt', { value: built ? 'ui/out' : 'PLACEHOLDER (ui/out absent at synth)' });
    new cdk.CfnOutput(this, 'SiteBucket', { value: site.bucketName });
    new cdk.CfnOutput(this, 'DistributionId', { value: this.distribution.distributionId });

    NagSuppressions.addResourceSuppressions(logs, [
      { id: 'AwsSolutions-S1', reason: 'This is the access-log bucket itself.' },
    ]);
    NagSuppressions.addResourceSuppressions(this.distribution, [
      { id: 'AwsSolutions-CFR1', reason: 'No geo restriction: judges are worldwide; all data routes require a bearer token at the API.' },
      { id: 'AwsSolutions-CFR2', reason: 'Static, token-gated UI with no data in the bucket; WAF (~$5+/month) is out of scope for the hackathon budget.' },
      { id: 'AwsSolutions-CFR4', reason: 'Default *.cloudfront.net certificate (no custom domain for the hackathon); its security policy is fixed by CloudFront.' },
    ]);
    NagSuppressions.addResourceSuppressions(this, [
      { id: 'AwsSolutions-IAM4', reason: 'CDK-managed BucketDeployment / auto-delete custom-resource Lambdas use AWSLambdaBasicExecutionRole.' },
      { id: 'AwsSolutions-IAM5', reason: 'CDK-managed BucketDeployment needs object wildcards on the asset and site buckets.' },
      { id: 'AwsSolutions-L1', reason: 'CDK-managed custom-resource Lambda runtimes are chosen by aws-cdk-lib.' },
    ], true);
  }
}
