import * as fs from 'node:fs';
import * as path from 'node:path';
import * as cdk from 'aws-cdk-lib';
import * as apigwv2 from 'aws-cdk-lib/aws-apigatewayv2';
import * as integrations from 'aws-cdk-lib/aws-apigatewayv2-integrations';
import * as iam from 'aws-cdk-lib/aws-iam';
import * as lambda from 'aws-cdk-lib/aws-lambda';
import * as logs from 'aws-cdk-lib/aws-logs';
import * as ssm from 'aws-cdk-lib/aws-ssm';
import { NagSuppressions } from './nag';
import { Construct } from 'constructs';
import { NAMES, PROJECT, ProjectContext } from './config';

export const UI_BASE_PATH = '/ui';
/** Where `make ui-build-interim` puts the basePath=/ui export (kept apart from ui/out, the CloudFront build). */
export const UI_INTERIM_OUT = path.join('ui', 'out-interim');

export interface UiInterimStackProps extends cdk.StackProps {
  ctx: ProjectContext;
}

/**
 * Interim review-UI hosting while CloudFront is blocked by new-account verification (docs/DEPLOY.md).
 *
 * The Next.js static export, built with basePath /ui, is baked into a small arm64 Python zip Lambda
 * (services/ui_static/handler.py) that the EXISTING HTTP API serves on GET|HEAD `/`, `/ui`, `/ui/{proxy+}`.
 * There is no bucket (nothing to make public) and the UI shares the API's origin, so the browser needs no
 * CORS. The UI routes have no auth: they return the same static JS/HTML a CDN would and hold no data. Every
 * data route stays on the API Lambda, which still requires the bearer token entered in the app.
 *
 * The API id is read from SSM /keepframe/api-url (written by ApiStack), so this stack creates no
 * cross-stack export and ApiStack can be redeployed independently. The stage auto-deploys new routes.
 * UiStack (S3 + CloudFront OAC) stays in the repo for when the account is verified; both stacks own
 * /keepframe/ui-url, so destroy this one before `make ui-deploy` (the Makefile checks).
 */
export class UiInterimStack extends cdk.Stack {
  readonly fn: lambda.Function;

  constructor(scope: Construct, id: string, props: UiInterimStackProps) {
    super(scope, id, props);
    const srcRoot = path.resolve(__dirname, '..', props.ctx.srcRoot);
    const handlerDir = path.join(srcRoot, 'services', 'ui_static');
    const outDir = path.join(srcRoot, UI_INTERIM_OUT);
    const built = fs.existsSync(path.join(outDir, 'index.html'));
    if (built && !fs.readFileSync(path.join(outDir, 'index.html'), 'utf8').includes(`${UI_BASE_PATH}/_next/`)) {
      throw new Error(`${UI_INTERIM_OUT} was not built with NEXT_PUBLIC_BASE_PATH=${UI_BASE_PATH}; run make ui-build-interim`);
    }

    // Zip = handler.py + site/ (the export, or a placeholder page so synth and CI work without a UI build).
    const code = lambda.Code.fromAsset(handlerDir, {
      assetHashType: cdk.AssetHashType.OUTPUT,
      bundling: {
        image: cdk.DockerImage.fromRegistry('unused-local-bundling-only'),
        local: {
          tryBundle(outputDir: string): boolean {
            fs.copyFileSync(path.join(handlerDir, 'handler.py'), path.join(outputDir, 'handler.py'));
            const site = path.join(outputDir, 'site');
            if (built) {
              fs.cpSync(outDir, site, { recursive: true });
            } else {
              fs.mkdirSync(site, { recursive: true });
              fs.writeFileSync(path.join(site, 'index.html'), '<!doctype html><title>KeepFrame</title><p>PLACEHOLDER: UI not built yet. Run <code>make ui-build-interim ui-deploy-interim</code>.</p>');
              fs.writeFileSync(path.join(site, '404.html'), '<!doctype html><title>Not found</title><p>Not found</p>');
            }
            return true;
          },
        },
      },
    });

    const logGroup = new logs.LogGroup(this, 'UiStaticLogs', {
      logGroupName: `/aws/lambda/${PROJECT}-ui-static`,
      retention: logs.RetentionDays.ONE_MONTH,
      removalPolicy: cdk.RemovalPolicy.DESTROY,
    });
    const role = new iam.Role(this, 'UiStaticRole', {
      assumedBy: new iam.ServicePrincipal('lambda.amazonaws.com'),
      description: 'KeepFrame interim UI: logs only (the site is inside the zip)',
    });
    role.addToPolicy(new iam.PolicyStatement({ actions: ['logs:CreateLogStream', 'logs:PutLogEvents'], resources: [logGroup.logGroupArn, `${logGroup.logGroupArn}:*`] }));

    this.fn = new lambda.Function(this, 'UiStaticFn', {
      functionName: `${PROJECT}-ui-static`,
      description: built ? 'KeepFrame review UI (static export, basePath /ui)' : 'PLACEHOLDER KeepFrame UI (ui/out-interim absent at synth)',
      runtime: lambda.Runtime.PYTHON_3_12,
      architecture: lambda.Architecture.ARM_64,
      handler: 'handler.handler',
      code,
      memorySize: 256,
      timeout: cdk.Duration.seconds(10),
      role,
      logGroup,
      environment: { UI_BASE_PATH },
    });

    // https://<apiId>.execute-api.<region>.amazonaws.com -> <apiId>
    const apiUrl = ssm.StringParameter.valueForStringParameter(this, NAMES.params.apiUrl);
    const apiId = cdk.Fn.select(0, cdk.Fn.split('.', cdk.Fn.select(2, cdk.Fn.split('/', apiUrl))));
    const httpApi = apigwv2.HttpApi.fromHttpApiAttributes(this, 'ReviewApi', { httpApiId: apiId });
    const integration = new integrations.HttpLambdaIntegration('UiStaticIntegration', this.fn, {
      payloadFormatVersion: apigwv2.PayloadFormatVersion.VERSION_2_0,
    });
    for (const [rid, p] of [['Root', '/'], ['Base', UI_BASE_PATH], ['Proxy', `${UI_BASE_PATH}/{proxy+}`]] as const) {
      for (const method of [apigwv2.HttpMethod.GET, apigwv2.HttpMethod.HEAD]) {
        new apigwv2.HttpRoute(this, `Ui${rid}${method}`, {
          httpApi,
          routeKey: apigwv2.HttpRouteKey.with(p, method),
          integration,
        });
      }
    }

    const origin = cdk.Fn.join('', ['https://', apiId, '.execute-api.', this.region, '.', this.urlSuffix]);
    const url = cdk.Fn.join('', [origin, `${UI_BASE_PATH}/`]);
    new ssm.StringParameter(this, 'UiUrlParam', { parameterName: NAMES.params.uiUrl, stringValue: url, description: 'KeepFrame review UI URL (interim: served by the HTTP API under /ui)' });
    new cdk.CfnOutput(this, 'UiUrl', { value: url });
    new cdk.CfnOutput(this, 'UiBuilt', { value: built ? UI_INTERIM_OUT : `PLACEHOLDER (${UI_INTERIM_OUT} absent at synth)` });
    new cdk.CfnOutput(this, 'UiFunctionName', { value: this.fn.functionName });

    NagSuppressions.addResourceSuppressions(this, [
      { id: 'AwsSolutions-APIG4', reason: 'UI routes serve the public static export (HTML/JS/CSS/fonts, no data, no secrets), like a CDN would. All data routes stay on the token-checking API Lambda.' },
      { id: 'AwsSolutions-IAM5', reason: 'CloudWatch Logs stream ARNs under the function log group end in :* by construction.' },
      { id: 'AwsSolutions-L1', reason: 'The project pins Python 3.12 for every Lambda (same runtime as the worker and API images).' },
    ], true);
  }
}
