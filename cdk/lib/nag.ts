/**
 * cdk-nag 3.x glue.
 *
 * cdk-nag 3 reads acknowledgments from CDK's native `Validations.of(x).acknowledge()` metadata and matches
 * finding ids EXACTLY (e.g. `AwsSolutions-IAM5[Resource::arn:aws:s3:::bucket/raw/*]`). Many findings embed
 * CloudFormation tokens (`<DataStackWork...Arn>`), so exact ids are brittle. `suppress()` records each
 * reason with the native API (so it lands in the policy-validation report / CloudFormation metadata) and
 * also keeps an optional `appliesTo` list of exact strings or /regex/ patterns that `ProjectNagChecks`
 * honours. A rule id without `appliesTo` acknowledges every finding of that rule under the construct.
 * Every suppression must carry a reason; they are listed in docs/DEPLOY.md.
 */
import { Validations } from 'aws-cdk-lib';
import { AwsSolutionsChecks, NagPackProps } from 'cdk-nag';
import { IConstruct } from 'constructs';

export interface Suppression {
  id: string;
  reason: string;
  appliesTo?: (string | { regex: string })[];
}

const KEY = 'keepframe:nag-suppressions';

export function suppress(scopes: IConstruct | IConstruct[], rules: Suppression[]): void {
  for (const scope of Array.isArray(scopes) ? scopes : [scopes]) {
    for (const r of rules) {
      if (!r.reason || r.reason.length < 10) throw new Error(`nag suppression ${r.id} needs a real reason`);
      Validations.of(scope).acknowledge({ id: r.appliesTo ? `${r.id}[*]` : r.id, reason: r.reason });
      scope.node.addMetadata(KEY, r);
    }
  }
}

/** Compatibility shim with the cdk-nag v2 call shape used across the stacks. */
export const NagSuppressions = {
  addResourceSuppressions(scope: IConstruct | IConstruct[], rules: Suppression[], _applyToChildren = true): void {
    suppress(scope, rules);
  },
  addStackSuppressions(scope: IConstruct, rules: Suppression[], _applyToNestedStacks = true): void {
    suppress(scope, rules);
  },
};

function toRegex(p: string): RegExp {
  const m = /^\/(.*)\/([a-z]*)$/.exec(p);
  return m ? new RegExp(m[1], m[2]) : new RegExp(`^${p.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}$`);
}

function matches(s: Suppression, ruleId: string): boolean {
  const bracket = ruleId.indexOf('[');
  const base = bracket === -1 ? ruleId : ruleId.slice(0, bracket);
  if (base !== s.id) return false;
  if (!s.appliesTo) return true;
  if (bracket === -1) return false;
  const finding = ruleId.slice(bracket + 1, -1);
  return s.appliesTo.some((a) => toRegex(typeof a === 'string' ? a : a.regex).test(finding));
}

/** AwsSolutionsChecks that also honours `suppress()` patterns on the resource or any ancestor. */
export class ProjectNagChecks extends AwsSolutionsChecks {
  constructor(scope: IConstruct, props?: NagPackProps) {
    super(scope, props);
    // cdk-nag keeps isAcknowledged private; wrap it rather than fork the pack.
    const self = this as unknown as { isAcknowledged: (r: IConstruct, id: string) => boolean };
    const original = self.isAcknowledged.bind(this);
    self.isAcknowledged = (resource: IConstruct, ruleId: string): boolean => {
      for (let cur: IConstruct | undefined = resource; cur; cur = cur.node.scope) {
        for (const entry of cur.node.metadata) {
          if (entry.type === KEY && matches(entry.data as Suppression, ruleId)) return true;
        }
      }
      return original(resource, ruleId);
    };
  }
}
