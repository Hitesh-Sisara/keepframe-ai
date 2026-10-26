"use client";

import { Fragment } from "react";
import { GLOSSARY, POLICIES, glossParts, policyTitle } from "@/lib/glossary";
import { Tooltip } from "./ui";

/** A jargon word with a dotted underline and a plain-language tooltip (hover or keyboard focus). */
export function Term({ term, policy, children }: { term?: string; policy?: number; children: React.ReactNode }) {
  const g = term ? GLOSSARY[term] : undefined;
  const p = policy ? POLICIES[policy] : undefined;
  if (!g && !p) return <>{children}</>;
  const title = g ? g.title : policyTitle(policy!);
  const body = g ? g.body : p!.body;
  return (
    <Tooltip
      label={
        <span className="block text-left font-normal">
          <span className="block font-semibold">{title}</span>
          <span className="mt-0.5 block text-white/85">{body}</span>
        </span>
      }
    >
      <span
        tabIndex={0}
        role="term"
        aria-label={`${typeof children === "string" ? children : title}. ${title}: ${body}`}
        className="cursor-help rounded-[2px] underline decoration-current/40 decoration-dotted underline-offset-[3px] focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-[var(--kf-accent)]"
      >
        {children}
      </span>
    </Tooltip>
  );
}

/** Free text with every policy code and measurement name turned into a Term. `plain` shows each measurement's
 *  everyday word ("sharpness") instead of its raw name ("lap_var"); the tooltip is the same either way. */
export function Glossed({ text, plain = false }: { text: string; plain?: boolean }) {
  return (
    <>
      {glossParts(text).map((p, i) =>
        p.term || p.policy ? (
          <Term key={i} term={p.term} policy={p.policy}>
            {(plain && p.term && GLOSSARY[p.term]?.plain) || p.text}
          </Term>
        ) : (
          <Fragment key={i}>{p.text}</Fragment>
        ),
      )}
    </>
  );
}
