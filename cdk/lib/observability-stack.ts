import * as cdk from 'aws-cdk-lib';
import * as budgets from 'aws-cdk-lib/aws-budgets';
import * as iam from 'aws-cdk-lib/aws-iam';
import * as cloudwatch from 'aws-cdk-lib/aws-cloudwatch';
import * as cwActions from 'aws-cdk-lib/aws-cloudwatch-actions';
import * as sns from 'aws-cdk-lib/aws-sns';
import * as subs from 'aws-cdk-lib/aws-sns-subscriptions';
import { NagSuppressions } from './nag';
import { Construct } from 'constructs';
import { NAMES, PROJECT } from './config';

/**
 * docs/ARCHITECTURE.md "Components": dashboard `KeepFrame` + alarms.
 *
 * Custom metrics are EMF (aws-embedded-metrics) in namespace `KeepFrame` with the contract names
 * photos_processed, stage_latency_ms{stage}, tool_calls{tool}, escalations, run_duration_s,
 * opencv_flavor, cost_estimate_usd. Widgets use SEARCH expressions so they work for any extra dimension set
 * the writers add (event_id, flavor, ...). AWS metrics (Step Functions, Lambda, ECS) are addressed by fixed
 * resource names, so this stack does not depend on the workflow/API stacks and can deploy first.
 * Log groups (30-day retention) are created by the stacks that own each workload.
 */
export interface ObservabilityStackProps extends cdk.StackProps {
  /** Optional e-mail for budget and alarm notifications (cdk context `budgetEmail`). No subscription when unset. */
  budgetEmail?: string;
}

export const BUDGETS = {
  /** Gross usage (credits NOT netted off), so it trips while credits still pay the bill. */
  gross: { name: `${PROJECT}-competition-gross`, limitUsd: 120 },
  /** Net spend after credits: anything credits do not cover (Marketplace, tax, support).
   *  Changing notificationsWithSubscribers REPLACES an AWS::Budgets::Budget; with a fixed name the create
   *  collides ("same name but a different internalId"), so rename the budget when you change its notifications. */
  net: { name: `${PROJECT}-net-spend-actual`, limitUsd: 1 },
} as const;

export class ObservabilityStack extends cdk.Stack {
  readonly dashboard: cloudwatch.Dashboard;

  constructor(scope: Construct, id: string, props: ObservabilityStackProps = {}) {
    super(scope, id, props);
    const ns = NAMES.metricNamespace;
    const smArn = cdk.Arn.format({ service: 'states', resource: 'stateMachine', resourceName: NAMES.stateMachine, arnFormat: cdk.ArnFormat.COLON_RESOURCE_NAME }, this);
    const period = cdk.Duration.minutes(5);

    // The workflow topic may not exist yet on first deploy; the alarm stack owns its own topic.
    const topic = new sns.Topic(this, 'AlarmTopic', { topicName: `${PROJECT}-alarms`, displayName: 'KeepFrame alarms', enforceSSL: true });
    const action = new cwActions.SnsAction(topic);

    const search = (expr: string, stat: string, label: string) =>
      new cloudwatch.MathExpression({ expression: `SEARCH('${expr}', '${stat}', ${period.toSeconds()})`, label, period });
    const sfnMetric = (metricName: string, statistic = 'Sum') =>
      new cloudwatch.Metric({ namespace: 'AWS/States', metricName, dimensionsMap: { StateMachineArn: smArn }, statistic, period });

    // ---- alarms ----
    const runFailures = new cloudwatch.Alarm(this, 'RunFailures', {
      alarmName: `${PROJECT}-run-failures`,
      alarmDescription: 'A CullEvent execution failed, timed out or was aborted',
      metric: new cloudwatch.MathExpression({
        expression: 'FILL(f,0) + FILL(t,0) + FILL(a,0)',
        usingMetrics: { f: sfnMetric('ExecutionsFailed'), t: sfnMetric('ExecutionsTimedOut'), a: sfnMetric('ExecutionsAborted') },
        label: 'failed + timed out + aborted',
        period,
      }),
      threshold: 1,
      evaluationPeriods: 1,
      comparisonOperator: cloudwatch.ComparisonOperator.GREATER_THAN_OR_EQUAL_TO_THRESHOLD,
      treatMissingData: cloudwatch.TreatMissingData.NOT_BREACHING,
    });
    const apiErrors = new cloudwatch.Alarm(this, 'ApiErrors', {
      alarmName: `${PROJECT}-api-errors`,
      alarmDescription: 'API Lambda errors (5xx inside the app or crashes)',
      metric: new cloudwatch.Metric({ namespace: 'AWS/Lambda', metricName: 'Errors', dimensionsMap: { FunctionName: NAMES.apiFunction }, statistic: 'Sum', period }),
      threshold: 5,
      evaluationPeriods: 1,
      treatMissingData: cloudwatch.TreatMissingData.NOT_BREACHING,
    });
    const workflowLambdaErrors = new cloudwatch.Alarm(this, 'WorkflowLambdaErrors', {
      alarmName: `${PROJECT}-workflow-lambda-errors`,
      alarmDescription: 'ValidateManifest / Summarise / MarkRunFailed errors',
      metric: new cloudwatch.MathExpression({
        expression: 'FILL(v,0) + FILL(s,0) + FILL(m,0)',
        usingMetrics: Object.fromEntries((
          [['v', 'validate-manifest'], ['s', 'summarise'], ['m', 'mark-run-failed']] as const
        ).map(([k, n]) => [k, new cloudwatch.Metric({ namespace: 'AWS/Lambda', metricName: 'Errors', dimensionsMap: { FunctionName: `${PROJECT}-${n}` }, statistic: 'Sum', period })])),
        label: 'workflow lambda errors',
        period,
      }),
      threshold: 1,
      evaluationPeriods: 1,
      treatMissingData: cloudwatch.TreatMissingData.NOT_BREACHING,
    });
    for (const a of [runFailures, apiErrors, workflowLambdaErrors]) a.addAlarmAction(action);

    // ---- dashboard ----
    this.dashboard = new cloudwatch.Dashboard(this, 'Dashboard', { dashboardName: NAMES.dashboard, defaultInterval: cdk.Duration.days(1) });
    const w = 8;
    this.dashboard.addWidgets(
      new cloudwatch.TextWidget({
        width: 24, height: 2,
        markdown: '# KeepFrame — OpenCV 5 on AWS Graviton (ARM64 only)\nEMF namespace `KeepFrame`. Throughput, per-stage latency, verdict mix, escalation %, tool calls, cost. Runs: Step Functions `CullEvent`.',
      }),
    );
    this.dashboard.addWidgets(
      new cloudwatch.GraphWidget({ title: 'Throughput: photos processed / 5 min', width: w, left: [search(`{${ns}} MetricName="photos_processed"`, 'Sum', '')] , stacked: true }),
      new cloudwatch.GraphWidget({ title: 'Stage latency p50 (ms)', width: w, left: [search(`{${ns},stage} MetricName="stage_latency_ms"`, 'p50', '')] }),
      new cloudwatch.GraphWidget({ title: 'Stage latency p95 (ms)', width: w, left: [search(`{${ns},stage} MetricName="stage_latency_ms"`, 'p95', '')] }),
    );
    this.dashboard.addWidgets(
      new cloudwatch.GraphWidget({ title: 'Verdict mix (keep / reject / review)', width: w, stacked: true, left: [search(`{${ns},verdict} MetricName="verdicts"`, 'Sum', '')] }),
      new cloudwatch.GraphWidget({
        title: 'Escalation % (escalations / photos)', width: w,
        left: [new cloudwatch.MathExpression({
          expression: `100 * SUM(SEARCH('{${ns}} MetricName="escalations"', 'Sum', 300)) / SUM(SEARCH('{${ns}} MetricName="photos_processed"', 'Sum', 300))`,
          label: 'escalation %', period,
        })],
        leftYAxis: { min: 0, max: 100 },
      }),
      new cloudwatch.GraphWidget({ title: 'Tool calls by tool', width: w, stacked: true, left: [search(`{${ns},tool} MetricName="tool_calls"`, 'Sum', '')] }),
    );
    this.dashboard.addWidgets(
      new cloudwatch.GraphWidget({ title: 'Run duration (s) by flavor', width: w, left: [search(`{${ns},opencv_flavor} MetricName="run_duration_s"`, 'Maximum', '')] }),
      new cloudwatch.GraphWidget({ title: 'Cost estimate (USD per run)', width: w, left: [search(`{${ns}} MetricName="cost_estimate_usd"`, 'Sum', '')] }),
      new cloudwatch.GraphWidget({ title: 'CullEvent executions', width: w, stacked: false, left: [
        sfnMetric('ExecutionsStarted'), sfnMetric('ExecutionsSucceeded'), sfnMetric('ExecutionsFailed'), sfnMetric('ExecutionsTimedOut'),
      ] }),
    );
    this.dashboard.addWidgets(
      new cloudwatch.GraphWidget({ title: 'ECS cluster CPU / memory (%)', width: w, left: [
        new cloudwatch.Metric({ namespace: 'AWS/ECS', metricName: 'CPUUtilization', dimensionsMap: { ClusterName: NAMES.cluster }, statistic: 'Average', period }),
        new cloudwatch.Metric({ namespace: 'AWS/ECS', metricName: 'MemoryUtilization', dimensionsMap: { ClusterName: NAMES.cluster }, statistic: 'Average', period }),
      ] }),
      new cloudwatch.GraphWidget({ title: 'API: requests, errors, p95 ms', width: w,
        left: [
          new cloudwatch.Metric({ namespace: 'AWS/Lambda', metricName: 'Invocations', dimensionsMap: { FunctionName: NAMES.apiFunction }, statistic: 'Sum', period }),
          new cloudwatch.Metric({ namespace: 'AWS/Lambda', metricName: 'Errors', dimensionsMap: { FunctionName: NAMES.apiFunction }, statistic: 'Sum', period }),
        ],
        right: [new cloudwatch.Metric({ namespace: 'AWS/Lambda', metricName: 'Duration', dimensionsMap: { FunctionName: NAMES.apiFunction }, statistic: 'p95', period })],
      }),
      new cloudwatch.AlarmStatusWidget({ title: 'Alarms', width: w, alarms: [runFailures, apiErrors, workflowLambdaErrors] }),
    );
    this.dashboard.addWidgets(
      new cloudwatch.LogQueryWidget({
        title: 'OpenCV build per process (cv2.__version__, cv2.__file__)', width: 24, height: 6,
        logGroupNames: [`/${PROJECT}/worker`, `/${PROJECT}/agent`, `/${PROJECT}/probe`],
        queryLines: [
          'fields @timestamp, @logStream, @message',
          'filter @message like /cv2|opencv/',
          'sort @timestamp desc',
          'limit 20',
        ],
      }),
    );

    // ---- AWS Budgets (alarm at $120) ----
    // Both budgets publish to the alarm topic. Budgets are account-wide (not filtered by the project tag):
    // the account also carries non-project resources, and the alarm is about the credit pool, not attribution.
    topic.addToResourcePolicy(new iam.PolicyStatement({
      sid: 'AllowBudgetsPublish',
      principals: [new iam.ServicePrincipal('budgets.amazonaws.com')],
      actions: ['sns:Publish'],
      resources: [topic.topicArn],
      conditions: {
        StringEquals: { 'aws:SourceAccount': this.account },
        ArnLike: { 'aws:SourceArn': `arn:${this.partition}:budgets::${this.account}:*` },
      },
    }));
    const snsSubscriber = [{ subscriptionType: 'SNS', address: topic.topicArn }];
    const costTypes = (includeCredit: boolean): budgets.CfnBudget.CostTypesProperty => ({
      includeCredit, includeRefund: includeCredit, includeTax: true, includeSupport: true,
      includeSubscription: true, includeOtherSubscription: true, includeRecurring: true, includeUpfront: true,
      includeDiscount: true, useBlended: false, useAmortized: false,
    });
    const notify = (type: 'ACTUAL' | 'FORECASTED', threshold: number) => ({
      notification: { notificationType: type, comparisonOperator: 'GREATER_THAN', threshold, thresholdType: 'PERCENTAGE' },
      subscribers: snsSubscriber,
    });
    const gross = new budgets.CfnBudget(this, 'CompetitionBudget', {
      budget: {
        budgetName: BUDGETS.gross.name,
        budgetType: 'COST',
        timeUnit: 'MONTHLY',
        budgetLimit: { amount: BUDGETS.gross.limitUsd, unit: 'USD' },
        costTypes: costTypes(false),
      },
      notificationsWithSubscribers: [notify('ACTUAL', 50), notify('ACTUAL', 80), notify('ACTUAL', 100), notify('FORECASTED', 100)],
    });
    const net = new budgets.CfnBudget(this, 'NetSpendBudget', {
      budget: {
        budgetName: BUDGETS.net.name,
        budgetType: 'COST',
        timeUnit: 'MONTHLY',
        budgetLimit: { amount: BUDGETS.net.limitUsd, unit: 'USD' },
        costTypes: costTypes(true),
      },
      // 10% of $1: any charge credits do not absorb (the project rule is "spend only AWS credits").
      // ACTUAL only: the Budgets forecast ignores credits (it forecast $12.93 net on day 8 with $0 net spend).
      notificationsWithSubscribers: [notify('ACTUAL', 10)],
    });
    // The topic policy must exist before Budgets validates the subscriber.
    for (const b of [gross, net]) b.node.addDependency(topic);
    const email = props.budgetEmail?.trim();
    if (email) topic.addSubscription(new subs.EmailSubscription(email));

    new cdk.CfnOutput(this, 'DashboardUrl', { value: `https://${this.region}.console.aws.amazon.com/cloudwatch/home?region=${this.region}#dashboards/dashboard/${NAMES.dashboard}` });
    new cdk.CfnOutput(this, 'AlarmTopicArn', { value: topic.topicArn });
    new cdk.CfnOutput(this, 'BudgetNames', { value: `${BUDGETS.gross.name},${BUDGETS.net.name}` });
    new cdk.CfnOutput(this, 'BudgetEmail', { value: email ? 'subscribed (pending confirmation)' : 'none (set -c budgetEmail=...)' });
    NagSuppressions.addResourceSuppressions(topic, [
      { id: 'AwsSolutions-SNS2', reason: 'Alarm notifications only (no customer data); CloudWatch alarms cannot publish to topics encrypted with the AWS-managed SNS key.' },
    ]);
  }
}
