"use client";

import { useEffect, useMemo, useState } from "react";
import { marginsToPts, nameIdsInText, offsetText, traceRows } from "@/lib/cull";
import { fmtGap } from "@/lib/closecall";
import { ANSWER_WORD } from "@/lib/decision";
import { cleanText, fmtNum } from "@/lib/format";
import type { Pivot, TraceLine, DecisionSource } from "@/lib/types";
import { SignedImg } from "./Frame";
import { Glossed, Term } from "./Glossed";
import { policyTitle } from "@/lib/glossary";

/** Rename photo ids everywhere inside a trace line (text, args, results), so the trace names frames the way the
 *  filmstrip does: "Frame 3", never a raw p_0069 or a 13-char alias. Keys are left alone; only string values. */
export function renameIds<T>(v: T, names: Record<string, string>, margin?: number): T {
  if (typeof v === "string") return (names[v] ?? marginsToPts(nameIdsInText(v, names), margin)) as T;
  if (Array.isArray(v)) return v.map((x) => renameIds(x, names, margin)) as T;
  if (v && typeof v === "object") {
    const out: Record<string, unknown> = {};
    for (const [k, x] of Object.entries(v as Record<string, unknown>)) out[k] = k.endsWith("_url") || k.endsWith("_urls") ? x : renameIds(x, names, margin);
    return out as T;
  }
  return v;
}

// The agent's trace for one group as a vertical timeline. Each step is a tool call with its OpenCV result
// folded in, or a line of agent reasoning. Steps where an OpenCV result changed the agent's next tool call are
// bracketed in red with a compact "Plan changed" marker: that red bracket is the award evidence
// (docs/AGENT.md "Trace schema") and keeps its meaning across the app.

// Theme tokens, so the plan-change card keeps its contrast in dark mode (reject red mixed into the surface).
export const PIVOT_RED = "var(--kf-reject)";
export const PIVOT_TINT = "color-mix(in srgb, var(--kf-reject) 9%, var(--kf-surface))";
export const PIVOT_RULE = "color-mix(in srgb, var(--kf-reject) 32%, var(--kf-surface))";
export const PIVOT_INK = "color-mix(in srgb, var(--kf-reject) 72%, var(--kf-text))";

const PIVOT_TITLE: Record<string, string> = {
  sharpness_rescore: "Borderline, re-measured full size",
  eyes_compare_in_group: "Blink, compared burst",
  small_margin_to_human: "Too close, asked you",
  tagged: "Changed course",
};

const TOOL_WORD: Record<string, string> = {
  next_work_item: "Picked up the next burst",
  score_sharpness: "Measured sharpness",
  score_exposure: "Checked exposure",
  detect_faces: "Found faces",
  score_eyes_open: "Checked eyes",
  score_face_quality: "Scored face quality",
  pick_best_in_cluster: "Ranked the burst",
  make_contact_sheet: "Made a contact sheet",
  request_human_approval: "Asked for a decision",
  write_decision: "Wrote the verdicts",
};

/** Unknown tools still read as words: "score_noise" → "Score noise". */
function toolWords(tool?: string): string | undefined {
  if (!tool) return undefined;
  const t = tool.replace(/_/g, " ");
  return t[0].toUpperCase() + t.slice(1);
}

/** knownSource: who answered this group's approval, when the verdicts record it. Trace lines that say "unknown"
 *  inherit it, so the trace never says "source not recorded" while the verdict card knows. */
export function TraceChain({ lines: raw, pivots: rawPivots, knownSource, names = {}, margin, emptyText }: { lines: TraceLine[]; pivots: Pivot[]; knownSource?: DecisionSource; names?: Record<string, string>; margin?: number; emptyText?: string }) {
  const pivots = useMemo(() => rawPivots.map((p) => ({ ...p, summary: renameIds(p.summary, names, margin) })), [rawPivots, names, margin]);
  const lines = useMemo(() => {
    const named = raw.length ? raw.map((l) => ({ ...l, text: l.text && renameIds(l.text, names, margin), args: l.args && renameIds(l.args, names, margin), result: l.result && renameIds(l.result, names, margin), decisions: l.decisions && renameIds(l.decisions, names, margin) })) : raw;
    return knownSource && knownSource !== "unknown" && knownSource !== "agent"
      ? named.map((l) => (l.type === "interrupt_resumed" && (!l.answered_by || l.answered_by === "unknown") ? { ...l, answered_by: knownSource } : l))
      : named;
  }, [raw, knownSource, names, margin]);
  const rows = useMemo(() => traceRows(lines, pivots), [lines, pivots]);
  const faces = useMemo(() => faceCounts(lines), [lines]);
  // Small screens open on the plan changes only; every step is one tap away.
  const [all, setAll] = useState(true);
  useEffect(() => {
    if (pivots.length && window.matchMedia("(max-width: 767px)").matches) setAll(false);
  }, [pivots.length]);
  const t0 = useMemo(() => {
    const ts = lines.map((l) => Date.parse(l.ts ?? "")).filter((t) => !Number.isNaN(t));
    return ts.length ? Math.min(...ts) : undefined;
  }, [lines]);
  if (!lines.length) {
    return (
      <p className="t-small text-[var(--kf-muted)]">{emptyText ?? "No steps yet."}</p>
    );
  }
  const steps = rows.filter((r) => r.kind === "line" && !r.attached).length;
  return (
    <>
    {pivots.length > 0 && (
      <div className="mb-1 flex justify-end md:hidden">
        <button type="button" className="btn btn-ghost btn-sm" aria-expanded={all} onClick={() => setAll((a) => !a)}>
          {all ? "Plan changes only" : `All ${steps + pivots.length} steps`}
        </button>
      </div>
    )}
    <ol className="relative before:absolute before:top-4 before:bottom-4 before:left-[25px] before:w-px before:bg-[var(--kf-border)]" aria-label="Agent trace, in order">
      {rows.map((r, i) => {
        if (r.kind === "pivot") {
          return <PivotBlock key={`p${r.pivot.result_seq}`} pivot={r.pivot} index={r.index} total={pivots.length} rows={r.rows} t0={t0} faces={faces} />;
        }
        if (r.attached || !all) return null;
        const next = rows[i + 1];
        const result = next && next.kind === "line" && next.attached ? next.line : undefined;
        const resultInPivot = r.line.type === "tool_call" && next?.kind === "pivot" && next.rows[0]?.line.tool === r.line.tool;
        const below = resultInPivot && next?.kind === "pivot" ? next.rows[0]?.line : undefined;
        return <Step key={r.line.seq} line={r.line} result={result} t0={t0} last={i === rows.length - 1} resultBelow={resultInPivot} peek={below} faces={faces} />;
      })}
    </ol>
    </>
  );
}

function PivotBlock({
  pivot,
  index,
  total,
  rows,
  t0,
  faces,
}: {
  pivot: Pivot;
  index: number;
  total: number;
  rows: { line: TraceLine; attached: boolean }[];
  t0?: number;
  faces: Record<string, number>;
}) {
  const first = rows[0]?.line;
  const rest = rows.slice(1);
  const reasoning = rest.filter((r) => r.line.type === "model_reasoning").map((r) => cleanText(r.line.text ?? ""));
  const changedCall = [...rest].reverse().find((r) => r.line.type === "tool_call")?.line;
  return (
    <li className="relative my-3 -ml-1 pl-1" aria-label={`Changed plan, ${index} of ${total}: ${pivot.summary}`}>
      {/* Red bracket in the gutter spanning the whole changed block (CSS borders, so it always reaches the end). */}
      <span
        aria-hidden="true"
        className="kf-bracket pointer-events-none absolute top-0 bottom-0 -left-2 w-2.5 rounded-l-[3px] border-y-[2.5px] border-l-[2.5px]"
        style={{ borderColor: PIVOT_RED, animationDelay: `${index * 120}ms` }}
      />
      <div className="rounded-[10px] border pb-1" style={{ borderColor: PIVOT_RULE, background: PIVOT_TINT }}>
        <div className="flex flex-wrap items-center gap-x-2.5 gap-y-1 border-b px-3 py-2 sm:px-4" style={{ borderColor: PIVOT_RULE }}>
          <span
            className="t-data inline-flex h-5 shrink-0 items-center rounded-full px-2 text-[0.6875rem] font-semibold text-white"
            style={{ background: PIVOT_RED }}
            aria-hidden="true"
          >
            {index}/{total}
          </span>
          <h4 className="min-w-0 text-[0.875rem] font-semibold" style={{ color: PIVOT_INK }} title={cleanText(pivot.summary)}>
            {PIVOT_TITLE[pivot.kind] ?? "Changed course"}
          </h4>
          {pivot.policy && <PolicyChip policy={pivot.policy} />}
        </div>
        <ol className="px-1 pt-1">
          {first && <Step line={first} result={undefined} t0={t0} inPivot faces={faces} />}
          {reasoning.length > 0 && (
            <li className="relative grid grid-cols-[36px_1fr] gap-x-2 px-2 py-1.5">
              <span className="flex justify-center pt-0.5" style={{ color: PIVOT_RED }} aria-hidden="true">
                <svg viewBox="0 0 16 16" className="h-4 w-4" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
                  <path d="M8 2v10M4 8.5l4 4 4-4" />
                </svg>
              </span>
              <p className="text-[0.8125rem] font-medium" style={{ color: PIVOT_INK }}>
                <Glossed text={reasoning.join(" ")} />
              </p>
            </li>
          )}
          {rest
            .filter((r) => r.line.type !== "model_reasoning")
            .map((r, i, arr) => {
              if (r.attached) return null;
              const nx = arr[i + 1];
              const res = nx && nx.attached ? nx.line : undefined;
              return <Step key={r.line.seq} line={r.line} result={res} t0={t0} inPivot highlight={r.line === changedCall} />;
            })}
        </ol>
      </div>
    </li>
  );
}

/** "(Policy 5: close call → ask a person)" with the full rule on hover. */
function PolicyChip({ policy }: { policy: string }) {
  const n = Number(/(\d)/.exec(policy)?.[1]);
  return (
    <span className="ml-1.5 text-[color-mix(in_srgb,var(--kf-reject)_60%,var(--kf-muted))]">
      (<Term policy={n || undefined}>{n ? `Policy ${n}` : policy}</Term>
      {n ? policyTitle(n).slice(`Policy ${n}`.length) : ""})
    </span>
  );
}

function Step({
  line,
  result,
  t0,
  inPivot = false,
  highlight = false,
  last = false,
  resultBelow = false,
  peek,
  faces = {},
}: {
  line: TraceLine;
  result?: TraceLine;
  t0?: number;
  inPivot?: boolean;
  highlight?: boolean;
  last?: boolean;
  resultBelow?: boolean;
  /** This call's result when it opens the plan-change box below: summarised here so the step isn't empty. */
  peek?: TraceLine;
  faces?: Record<string, number>;
}) {
  const kind = line.type;
  const reasoning = kind === "model_reasoning";
  const scripted = line.answered_by === "scripted" || result?.answered_by === "scripted";
  const title =
    kind === "tool_call"
      ? (TOOL_WORD[line.tool ?? ""] ?? toolWords(line.tool) ?? "Tool call")
      : kind === "tool_result"
        ? `${TOOL_WORD[line.tool ?? ""] ?? toolWords(line.tool) ?? "Result"}`
        : kind === "interrupt_resumed"
          ? line.answered_by
            ? ANSWER_WORD[line.answered_by]
            : "Answer received"
          : kind === "interrupt"
            ? "Paused for a person"
            : kind === "run_header" || kind === "run_end"
              ? cleanText(line.text ?? kind)
              : "Agent reasoning";
  const latency = result?.latency_ms ?? line.latency_ms ?? peek?.latency_ms;
  return (
    <li id={`seq-${line.seq}`} className={`group/step relative grid scroll-mt-24 grid-cols-[36px_1fr] gap-x-2 px-2 ${reasoning ? "py-1" : "py-2"}`}>
      {/* Rail + node */}
      <span className="relative flex justify-center pt-0.5">
        <ToolIcon line={line} highlight={highlight} inPivot={inPivot} />
      </span>
      <div className="min-w-0 pb-0.5">
        {reasoning ? (
          <p className="text-[0.8125rem] leading-relaxed text-[var(--kf-muted)] italic">
            <Glossed text={cleanText(line.text ?? "")} />
          </p>
        ) : (
          <>
            <div className="flex flex-wrap items-baseline gap-x-2 gap-y-0.5">
              <span className="text-[0.875rem] font-semibold" style={{ color: highlight ? PIVOT_INK : "var(--kf-text)" }}>{title}</span>
              {highlight && (
                <span className="rounded-full px-1.5 text-[0.6875rem] font-semibold text-white" style={{ background: PIVOT_RED }}>
                  new step
                </span>
              )}
              {scripted && line.type !== "interrupt_resumed" && <span className="text-[0.75rem] font-semibold text-[var(--kf-review)]">test run</span>}
              <span className="t-data ml-auto shrink-0 text-[0.72rem] text-[var(--kf-faint)]">
                {typeof latency === "number" && `took ${fmtNum(latency, latency < 10 ? 1 : 0)} ms · `}
                {offsetText(line.ts, t0) && `at ${offsetText(line.ts, t0)}`}
              </span>
            </div>
            {peek?.tool === "score_sharpness" && <SharpnessPeek result={peek.result} faces={faces} />}
            <Body line={line} result={result} faces={faces} />
            {resultBelow && <p className="mt-0.5 text-[0.75rem] font-medium" style={{ color: PIVOT_RED }}>Results and plan change ↓</p>}
          </>
        )}
      </div>
    </li>
  );
}

// ------------------------------------------------------------------ body

function Body({ line, result, faces = {} }: { line: TraceLine; result?: TraceLine; faces?: Record<string, number> }) {
  if (line.type === "tool_call") {
    const args = summarizeArgs(line.tool, line.args);
    return (
      <>
        {args && <p className="t-small mt-0.5 text-[var(--kf-muted)]">{args}</p>}
        {line.contact_sheet_urls?.map((u, i) => (
          <div key={i} className="relative mt-2 aspect-[5/2] w-full max-w-[420px] overflow-hidden rounded-md border border-[var(--kf-border)] bg-[var(--kf-surface-2)]">
            <SignedImg src={u} alt="Contact sheet sent with the approval request" fit="contain" eager compact={false} />
          </div>
        ))}
        {result && <ResultView tool={result.tool} result={result.result} />}
        {(line.args || result?.result) && <Raw obj={{ ...(line.args ? { args: line.args } : {}), ...(result?.result ? { result: result.result } : {}) }} />}
      </>
    );
  }
  if (line.type === "tool_result") {
    const scope = resultScope(line.tool, line.result, faces);
    return (
      <>
        {scope && <p className="t-small mt-0.5 text-[var(--kf-muted)]">{scope}</p>}
        <ResultView tool={line.tool} result={line.result} />
        {line.result && <Raw obj={line.result} />}
      </>
    );
  }
  if (line.type === "interrupt_resumed") {
    return (
      <>
        {line.answered_by === "scripted" && (
          <p className="t-small mt-0.5 font-medium text-[var(--kf-review)]">Test run: accepted automatically.</p>
        )}
        {line.answered_by === "lead_test" && (
          <p className="t-small mt-0.5 font-medium text-[var(--kf-muted)]">Lead test answer.</p>
        )}
        {line.answered_by === "unknown" && <p className="t-small mt-0.5 text-[var(--kf-muted)]">Answerer not recorded.</p>}
        <VerdictLine decisions={line.decisions} />
      </>
    );
  }
  if (line.text)
    return (
      <p className="t-small mt-0.5 text-[var(--kf-muted)]">
        <Glossed text={cleanText(line.text)} />
      </p>
    );
  return null;
}

function summarizeArgs(tool?: string, args?: Record<string, unknown>): string {
  if (!args) return "";
  const ids = Array.isArray(args.photo_ids) ? (args.photo_ids as string[]) : undefined;
  const bits: string[] = [];
  if (ids) bits.push(ids.length === 1 ? ids[0] : `${ids.length} frames`);
  if (args.roi === "faces") bits.push("on the faces");
  else if (args.roi === "saliency") bits.push("on the subject");
  else if (args.roi === "full" && tool === "score_sharpness") bits.push("whole frame");
  if (args.variant === "original") bits.push("from the full-resolution original");
  if (tool === "request_human_approval" && Array.isArray(args.items)) {
    const it = (args.items as Record<string, unknown>[])[0];
    if (it?.reason) bits.push(cleanText(String(it.reason)));
  }
  if (tool === "write_decision" && Array.isArray(args.decisions)) bits.push(`${(args.decisions as unknown[]).length} verdicts`);
  return bits.join(" · ");
}

type PerPhoto = { photo_id?: string; roi_used?: string; roi?: string; variant?: string; lap_var?: number; label?: string; blur_kind?: string };

/** Faces found per frame, from the trace's detect_faces results (latest wins). */
function faceCounts(lines: TraceLine[]): Record<string, number> {
  const out: Record<string, number> = {};
  for (const l of lines) {
    if (l.type !== "tool_result" || l.tool !== "detect_faces" || !Array.isArray(l.result?.per_photo)) continue;
    for (const p of l.result.per_photo as { photo_id?: string; n_faces?: number; faces?: unknown[] }[]) {
      if (p.photo_id) out[p.photo_id] = p.n_faces ?? p.faces?.length ?? 0;
    }
  }
  return out;
}

function facesText(per: PerPhoto[], faces: Record<string, number>): string {
  if (!per.some((p) => (p.roi_used ?? p.roi) === "faces")) return "";
  const known = per.filter((p) => p.photo_id && faces[p.photo_id] !== undefined);
  if (!known.length) return "on the faces";
  const n = known.reduce((a, p) => a + faces[p.photo_id!], 0);
  return `measured on ${n} ${n === 1 ? "face" : "faces"}`;
}

/** "4 frames · measured on 12 faces · full-resolution original" for a result shown on its own. */
function resultScope(tool: string | undefined, result: Record<string, unknown> | undefined, faces: Record<string, number>): string {
  if (tool !== "score_sharpness" || !Array.isArray(result?.per_photo)) return "";
  const per = result.per_photo as PerPhoto[];
  const bits = [per.length === 1 ? String(per[0].photo_id ?? "1 frame") : `${per.length} frames`];
  const f = facesText(per, faces);
  if (f) bits.push(f);
  else if (per.some((p) => (p.roi_used ?? p.roi) === "full")) bits.push("whole frame");
  if (per.some((p) => p.variant === "original")) bits.push("full-resolution original");
  return bits.join(" · ");
}

/** One-line result for a sharpness call whose bars sit in the plan-change box below. */
function SharpnessPeek({ result, faces }: { result?: Record<string, unknown>; faces: Record<string, number> }) {
  const per = (Array.isArray(result?.per_photo) ? result.per_photo : []) as PerPhoto[];
  const vals = per.filter((p) => typeof p.lap_var === "number");
  if (!vals.length) return null;
  const top = vals.reduce((a, b) => (b.lap_var! > a.lap_var! ? b : a));
  const low = vals.reduce((a, b) => (b.lap_var! < a.lap_var! ? b : a));
  const flagged = vals.filter((p) => p.label === "borderline" || p.label === "blurry");
  const f = facesText(per, faces);
  return (
    <p className="t-small mt-0.5 text-[var(--kf-muted)]">
      {f && <>{f[0].toUpperCase() + f.slice(1)} · </>}
      sharpest <span className="t-data text-[var(--kf-text)]">{fmtNum(top.lap_var!, 0)}</span> ({String(top.photo_id)})
      {vals.length > 1 && (
        <>
          {" "}· lowest <span className={`t-data ${flagged.includes(low) ? "font-semibold text-[var(--kf-review)]" : "text-[var(--kf-text)]"}`}>{fmtNum(low.lap_var!, 0)}</span> ({String(low.photo_id)}
          {flagged.includes(low) ? `, ${low.label}${low.blur_kind ? ` ${low.blur_kind} blur` : ""}` : ""})
        </>
      )}
    </p>
  );
}

const STATE_WORD: Record<string, string> = { open: "open", partial: "half open", closed: "closed", unknown: "not readable" };

/** Tool-specific, photographer-readable result: the numbers that drove the decision first. */
function ResultView({ tool, result }: { tool?: string; result?: Record<string, unknown> }) {
  if (!result) return null;
  const per = Array.isArray(result.per_photo) ? (result.per_photo as Record<string, unknown>[]) : undefined;

  if (tool === "score_sharpness" && per) {
    const max = Math.max(...per.map((p) => Number(p.lap_var) || 0), 1);
    return (
      <PhotoBars
        caption={
          <>
            <Term term="lap_var">Sharpness</Term>
          </>
        }
        rows={per.map((p) => ({
          id: String(p.photo_id),
          v: (Number(p.lap_var) || 0) / max,
          label: `${String(p.label ?? "")}${p.blur_kind ? `, ${p.blur_kind} blur` : ""}`,
          bad: p.label === "borderline" || p.label === "blurry",
          num: fmtNum(Number(p.lap_var), 0),
        }))}
      />
    );
  }
  if (tool === "score_exposure" && per) {
    if (per.length > 1 && per.every((p) => p.label === "ok")) return <p className="t-small mt-0.5 text-[var(--kf-muted)]">All {per.length} frames well exposed</p>;
    return (
      <ul className="mt-1 flex flex-wrap gap-1.5">
        {per.map((p) => {
          const ok = p.label === "ok";
          return (
            <li
              key={String(p.photo_id)}
              className={`t-small inline-flex items-center gap-1.5 rounded-md px-2 py-0.5 ring-1 ring-inset ${
                ok ? "bg-[var(--kf-surface-2)] text-[var(--kf-muted)] ring-[var(--kf-border)]" : "bg-[var(--kf-review-soft)] text-[var(--kf-review)] ring-[color-mix(in_srgb,var(--kf-review)_30%,var(--kf-surface))]"
              }`}
            >
              <span>{String(p.photo_id)}</span>
              <span className={ok ? "" : "font-semibold"}>{ok ? "well exposed" : String(p.label ?? "")}</span>
            </li>
          );
        })}
      </ul>
    );
  }
  if (tool === "detect_faces" && per) {
    return (
      <p className="t-small mt-1 text-[var(--kf-muted)]">
        {per.map((p) => `${String(p.photo_id)}: ${String(p.n_faces ?? (p.faces as unknown[] | undefined)?.length ?? 0)}`).join(" · ")}
        <span className="text-[var(--kf-faint)]"> faces</span>
      </p>
    );
  }
  if (tool === "score_eyes_open" && (per || Array.isArray(result.per_face))) {
    const photos = per ?? [result];
    return (
      <ul className="mt-1 flex flex-wrap gap-1.5">
        {photos.map((p, i) => {
          const faces = (p.per_face as { face_id: string; state?: string; is_main?: boolean }[] | undefined) ?? [];
          const main = faces.find((f) => f.is_main) ?? faces[0];
          const closed = p.any_main_subject_closed === true || main?.state === "closed";
          return (
            <li
              key={String(p.photo_id ?? i)}
              className={`t-small inline-flex items-center gap-1.5 rounded-md px-2 py-0.5 ring-1 ring-inset ${
                closed ? "bg-[var(--kf-reject-soft)] text-[var(--kf-reject)] ring-[color-mix(in_srgb,var(--kf-reject)_30%,var(--kf-surface))]" : "bg-[var(--kf-surface-2)] text-[var(--kf-muted)] ring-[var(--kf-border)]"
              }`}
            >
              {p.photo_id !== undefined && <span className="t-mono text-[0.72rem]">{String(p.photo_id)}</span>}
              <span className={closed ? "font-semibold" : ""}>main {STATE_WORD[main?.state ?? "unknown"] ?? main?.state}</span>
            </li>
          );
        })}
      </ul>
    );
  }
  if (tool === "score_face_quality" && per) {
    return (
      <PhotoBars
        caption="Face quality"
        rows={per.map((p) => {
          const faces = (p.per_face as { fiqa?: number }[] | undefined) ?? [];
          const vals = faces.map((f) => f.fiqa).filter((v): v is number => typeof v === "number");
          const mean = vals.length ? vals.reduce((a, b) => a + b, 0) / vals.length : 0;
          return { id: String(p.photo_id), v: mean, label: `${faces.length} faces`, bad: mean < 0.4, num: `${Math.round(mean * 100)}%` };
        })}
      />
    );
  }
  if (tool === "pick_best_in_cluster" && Array.isArray(result.ranking)) {
    const ranking = result.ranking as { photo_id: string; score: number }[];
    const max = Math.max(...ranking.map((r) => r.score), 1e-6);
    const margin = Number(result.margin);
    return (
      <>
        <p className="t-small mt-0.5 text-[var(--kf-muted)]">
          Best <span className="t-mono font-semibold text-[var(--kf-text)]">{String(result.best_photo_id)}</span>
          {Number.isFinite(margin) && (
            <>
              {", "}
              <Term term="margin">ahead by</Term>{" "}
              <span className={`t-data font-semibold ${margin < 0.05 ? "text-[var(--kf-review)]" : "text-[var(--kf-text)]"}`}>{fmtGap(margin)}</span>
              {margin < 0.05 ? " (under 5, too close to call)" : ""}
            </>
          )}
        </p>
        <PhotoBars caption="Rank score /100" rows={ranking.map((r, i) => ({ id: r.photo_id, v: r.score / max, label: i === 0 ? "best" : "", bad: false, num: (r.score * 100).toFixed(1), top: i === 0 }))} />
      </>
    );
  }
  if (tool === "next_work_item") {
    const photos = Array.isArray(result.photos) ? (result.photos as unknown[]).length : undefined;
    const why = typeof result.why === "string" ? cleanText(result.why) : "";
    return (
      <p className="t-small mt-0.5 text-[var(--kf-muted)]">
        {photos !== undefined && `${photos} frames`}
        {why && `${photos !== undefined ? " · " : ""}${why[0].toUpperCase()}${why.slice(1)}`}
      </p>
    );
  }
  if (tool === "make_contact_sheet") {
    return <p className="t-small mt-0.5 text-[var(--kf-muted)]">{result.n_tiles !== undefined ? `${String(result.n_tiles)} tiles` : "Ready"}</p>;
  }
  if (tool === "request_human_approval") {
    return <VerdictLine decisions={result.decisions as unknown[] | undefined} />;
  }
  if (tool === "write_decision" && result.verdict_counts) {
    const c = result.verdict_counts as Record<string, number>;
    return (
      <p className="t-small mt-0.5 text-[var(--kf-muted)]">
        {["keep", "review", "reject"].filter((k) => c[k]).map((k) => `${c[k]} ${k}`).join(" · ")}
      </p>
    );
  }
  return null;
}

function VerdictLine({ decisions }: { decisions?: unknown[] }) {
  const flat = (decisions ?? []).flat() as { human_verdict?: Record<string, string[] | string>; note?: string }[];
  const d = flat.find((x) => x && typeof x === "object" && x.human_verdict);
  if (!d?.human_verdict) return null;
  const hv = d.human_verdict;
  const list = (v?: string[] | string) => (Array.isArray(v) ? v : v ? [v] : []);
  return (
    <p className="t-small mt-0.5 text-[var(--kf-muted)]">
      {(["keep", "review", "reject"] as const)
        .filter((k) => list(hv[k]).length)
        .map((k) => `${k} ${list(hv[k]).join(", ")}`)
        .join(" · ")}
      {d.note && <span className="text-[var(--kf-faint)]"> · “{cleanText(d.note)}”</span>}
    </p>
  );
}

function PhotoBars({ rows, caption }: { rows: { id: string; v: number; label: string; bad: boolean; num: string; top?: boolean }[]; caption?: React.ReactNode }) {
  return (
    <>
    {caption && <p className="mt-1 text-[0.6875rem] text-[var(--kf-faint)]">{caption}</p>}
    <ul className="mt-1 grid max-w-[720px] gap-1">
      {rows.map((r) => (
        <li key={r.id} className="t-data grid grid-cols-[60px_minmax(48px,1fr)_minmax(0,112px)] sm:grid-cols-[64px_minmax(80px,1fr)_minmax(0,1fr)] items-center gap-2.5 text-[0.75rem]">
          <span className={`truncate ${r.top ? "font-semibold text-[var(--kf-text)]" : "text-[var(--kf-muted)]"}`} title={r.id}>{r.id}</span>
          <span className="relative h-1.5 overflow-hidden rounded-full bg-[var(--kf-surface-2)] ring-1 ring-inset ring-[var(--kf-border)]">
            <span
              className="absolute inset-y-0 left-0 rounded-full"
              style={{ width: `${Math.max(2, Math.min(100, r.v * 100))}%`, background: r.bad ? "var(--kf-review)" : r.top ? "var(--kf-keep)" : "var(--kf-accent)", opacity: r.top || r.bad ? 1 : 0.7 }}
            />
          </span>
          <span className={`truncate whitespace-nowrap ${r.bad ? "font-semibold text-[var(--kf-review)]" : "text-[var(--kf-muted)]"}`} title={r.label}>
            {r.num}
            {r.bad && r.label && <span className="ml-1.5">{r.label}</span>}
          </span>
        </li>
      ))}
    </ul>
    </>
  );
}

function Raw({ obj }: { obj: Record<string, unknown> }) {
  const [open, setOpen] = useState(false);
  return (
    <div className="mt-1">
      <button
        type="button"
        aria-expanded={open}
        onClick={() => setOpen((o) => !o)}
        className="t-small inline-flex items-center gap-1 rounded text-[0.72rem] font-medium text-[var(--kf-faint)] opacity-0 transition-opacity group-hover/step:opacity-100 hover:text-[var(--kf-text)] focus-visible:opacity-100 aria-expanded:opacity-100"
      >
        <svg viewBox="0 0 12 12" className={`h-2.5 w-2.5 transition-transform ${open ? "rotate-90" : ""}`} aria-hidden="true">
          <path d="M4 2.5l3.5 3.5L4 9.5" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" />
        </svg>
        {open ? "Hide data" : "Show data"}
      </button>
      {open && (
        <pre className="t-mono mt-1 max-h-72 overflow-auto rounded-md bg-[var(--kf-surface-2)] p-2.5 text-[0.72rem] leading-relaxed whitespace-pre-wrap text-[var(--kf-muted)] ring-1 ring-inset ring-[var(--kf-border)]">
          {cleanText(JSON.stringify(scrub(obj), null, 2))}
        </pre>
      )}
    </div>
  );
}

/** Drop storage keys, file paths and interrupt ids from the raw view. */
function scrub(v: unknown): unknown {
  if (Array.isArray(v)) return v.map(scrub);
  if (v && typeof v === "object") {
    const out: Record<string, unknown> = {};
    for (const [k, x] of Object.entries(v as Record<string, unknown>)) {
      if (/(^|_)key$|cv2_file|interrupt_(id|name)|^item_id$|contact_sheet_key/.test(k)) continue;
      out[k] = scrub(x);
    }
    return out;
  }
  return v;
}

// ------------------------------------------------------------------ icons

function ToolIcon({ line, highlight, inPivot }: { line: TraceLine; highlight?: boolean; inPivot?: boolean }) {
  const reasoning = line.type === "model_reasoning";
  const person = line.type === "interrupt_resumed" || line.type === "interrupt" || line.tool === "request_human_approval";
  const cls = highlight
    ? "text-white"
    : reasoning
      ? "text-[var(--kf-faint)]"
      : person
        ? "text-[var(--kf-review)]"
        : inPivot
          ? "text-[color-mix(in_srgb,var(--kf-reject)_72%,var(--kf-text))]"
          : "text-[var(--kf-accent)]";
  const bg = highlight
    ? { background: PIVOT_RED, boxShadow: `0 0 0 3px ${PIVOT_RULE}` }
    : reasoning
      ? { background: "transparent" }
      : { background: "var(--kf-surface)", boxShadow: "0 0 0 1px var(--kf-border), 0 1px 2px rgb(16 24 40 / 0.06)" };
  return (
    <span className={`relative z-[1] grid ${reasoning ? "h-5 w-5" : "h-7 w-7"} place-items-center rounded-full ${cls}`} style={bg} aria-hidden="true">
      <Glyph name={reasoning ? "thought" : person ? "person" : (line.tool ?? line.type)} small={reasoning} />
    </span>
  );
}

function Glyph({ name, small }: { name: string; small?: boolean }) {
  const p = { fill: "none", stroke: "currentColor", strokeWidth: 1.6, strokeLinecap: "round" as const, strokeLinejoin: "round" as const };
  const c = small ? "h-3.5 w-3.5" : "h-[15px] w-[15px]";
  return (
    <svg viewBox="0 0 16 16" className={c}>
      {name === "thought" && <path {...p} d="M3 9.5a4.5 4 0 1 1 3.2 3.8L3.5 14l.6-2.3A4 4 0 0 1 3 9.5z" />}
      {name === "person" && (
        <>
          <circle {...p} cx="8" cy="5.5" r="2.5" />
          <path {...p} d="M3 14c.6-2.7 2.6-4 5-4s4.4 1.3 5 4" />
        </>
      )}
      {name === "next_work_item" && (
        <>
          <rect {...p} x="2" y="4" width="12" height="9" rx="1.5" />
          <path {...p} d="M5 4V2.5h6V4" />
        </>
      )}
      {name === "score_sharpness" && (
        <>
          <circle {...p} cx="8" cy="8" r="5.5" />
          <path {...p} d="M8 2.5v3M8 10.5v3M2.5 8h3M10.5 8h3" />
        </>
      )}
      {name === "score_exposure" && (
        <>
          <circle {...p} cx="8" cy="8" r="2.7" />
          <path {...p} d="M8 1.8v1.4M8 12.8v1.4M1.8 8h1.4M12.8 8h1.4M3.6 3.6l1 1M11.4 11.4l1 1M3.6 12.4l1-1M11.4 4.6l1-1" />
        </>
      )}
      {name === "detect_faces" && (
        <>
          <path {...p} d="M2 5V3a1 1 0 0 1 1-1h2M11 2h2a1 1 0 0 1 1 1v2M14 11v2a1 1 0 0 1-1 1h-2M5 14H3a1 1 0 0 1-1-1v-2" />
          <circle {...p} cx="8" cy="7.5" r="2.6" />
        </>
      )}
      {name === "score_eyes_open" && (
        <>
          <path {...p} d="M1.5 8S4 3.8 8 3.8 14.5 8 14.5 8 12 12.2 8 12.2 1.5 8 1.5 8z" />
          <circle {...p} cx="8" cy="8" r="2" />
        </>
      )}
      {name === "score_face_quality" && (
        <>
          <circle {...p} cx="7" cy="8" r="4.5" />
          <path {...p} d="M5.4 9.3c.9.8 2.3.8 3.2 0M12.5 2l.5 1.2 1.2.5-1.2.5-.5 1.2-.5-1.2-1.2-.5 1.2-.5z" />
        </>
      )}
      {name === "pick_best_in_cluster" && <path {...p} d="M2 5.2l3.2 2.6L8 3l2.8 4.8L14 5.2l-1.3 7H3.3z" />}
      {name === "make_contact_sheet" && (
        <>
          <rect {...p} x="2" y="2" width="5" height="5" rx="1" />
          <rect {...p} x="9" y="2" width="5" height="5" rx="1" />
          <rect {...p} x="2" y="9" width="5" height="5" rx="1" />
          <rect {...p} x="9" y="9" width="5" height="5" rx="1" />
        </>
      )}
      {name === "write_decision" && <path {...p} d="M3 8.5l3 3 7-7" />}
      {!KNOWN.has(name) && <circle {...p} cx="8" cy="8" r="3" />}
    </svg>
  );
}

const KNOWN = new Set([
  "thought",
  "person",
  "next_work_item",
  "score_sharpness",
  "score_exposure",
  "detect_faces",
  "score_eyes_open",
  "score_face_quality",
  "pick_best_in_cluster",
  "make_contact_sheet",
  "write_decision",
]);
