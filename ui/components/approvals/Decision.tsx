"use client";

import Link from "next/link";
import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { Glossed } from "@/components/Glossed";
import { toast, Tooltip } from "@/components/ui";
import { api, ApiError, useApi } from "@/lib/api";
import type { Group, GroupsPage, TracePage, Verdict } from "@/lib/types";
import s from "./approvals.module.css";
import { burstLabel } from "./Inbox";
import { routes } from "@/lib/routes";
import {
  applyRankScores,
  chooseCandidates,
  scoreLabel,
  fitColumns,
  keepAllForReview,
  keeperOf,
  leaders as leadersOf,
  pickFrame,
  planSummary,
  refineWhy,
  frameName,
  relAge,
  sameAssign,
  toVerdictMap,
  type ApprovalVM,
  type Assign,
  type FrameVM,
} from "./logic";
import { CAPTION_H, Candidate, Filmstrip, VerdictPill, type Zoom } from "./Stage";

export const READ_ONLY = "Read-only judge view";
/** The "top two scored X and Y" line: the frame chips already show both scores. */
const SCORE_LINE = /scored [\d.]+ and .*out of 100/;
const ADVANCE_MS = 1700;

export type Outcome = { title: string; detail: string; resumed: boolean };

type Mode = "proposal" | "pick" | "all-review";

function resumeOutcome(kind: "approve" | "pick" | "all-review", resume?: { via?: string; status?: string | number }, keeperN?: number): Outcome {
  const what =
    kind === "approve" ? "Proposal approved." : kind === "pick" ? `Frame ${keeperN} is the keeper.` : "Whole group kept for review.";
  if (resume?.via === "api-direct") {
    return { title: what, detail: "Verdicts recorded for this group.", resumed: false };
  }
  if (resume?.via === "agentcore" && resume.status === "paused") {
    return { title: "Agent resuming…", detail: `${what} The agent picked up and stopped at its next close call.`, resumed: true };
  }
  if (resume?.via === "agentcore" && resume.status === "done") {
    return { title: "Agent resuming…", detail: `${what} The agent picked up and finished the run.`, resumed: true };
  }
  return { title: "Agent resuming…", detail: `${what} The agent has your answer and is carrying on with the run.`, resumed: true };
}

/** Ranked members with score breakdowns for this burst (a judge gets aliased ids; same shape). */
function useGroup(vm: ApprovalVM): Group | undefined {
  const g = useApi<GroupsPage>(vm.groupId ? `/events/${encodeURIComponent(vm.eventId)}/groups` : null, { group_id: vm.groupId });
  const item = g.data?.items[0];
  return item && item.group_id === vm.groupId ? item : undefined;
}

/** The agent's own ranking for this burst: the last pick_best_in_cluster result in the group's trace. */
function useAgentRanking(vm: ApprovalVM): { photo_id: string; score: number }[] | undefined {
  const t = useApi<TracePage>(vm.runId && vm.groupId ? `/runs/${encodeURIComponent(vm.runId)}/trace` : null, { group_id: vm.groupId, limit: 500 });
  return useMemo(() => {
    const res = [...(t.data?.items ?? [])].reverse().find((l) => l.type === "tool_result" && l.tool === "pick_best_in_cluster");
    const ranking = (res?.result as { ranking?: unknown } | undefined)?.ranking;
    return Array.isArray(ranking)
      ? ranking.filter((x): x is { photo_id: string; score: number } => typeof x?.photo_id === "string" && typeof x?.score === "number")
      : undefined;
  }, [t.data]);
}

function useMediaQuery(q: string): boolean {
  const [m, setM] = useState(false);
  useEffect(() => {
    const mq = window.matchMedia(q);
    const on = () => setM(mq.matches);
    on();
    mq.addEventListener("change", on);
    return () => mq.removeEventListener("change", on);
  }, [q]);
  return m;
}

function useElementSize<T extends HTMLElement>(): [React.RefObject<T | null>, { w: number; h: number }] {
  const ref = useRef<T>(null);
  const [size, setSize] = useState({ w: 0, h: 0 });
  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return;
    const ro = new ResizeObserver(([e]) => setSize({ w: e.contentRect.width, h: e.contentRect.height }));
    ro.observe(el);
    return () => ro.disconnect();
  }, []);
  return [ref, size];
}

export function Decision({
  vm,
  readOnly,
  position,
  onAnswered,
  onAdvance,
  onMove,
  answeredLine,
  answeredNote,
}: {
  vm: ApprovalVM;
  readOnly: boolean;
  position: { index: number; total: number };
  onAnswered?: (id: string) => void;
  onAdvance?: (id: string) => void;
  onMove: (delta: 1 | -1) => void;
  /** Set for an already-answered request: who answered it (never "photographer"). */
  answeredLine?: string;
  answeredNote?: string;
}) {
  const group = useGroup(vm);
  const ranking = useAgentRanking(vm);
  // Scores: the agent's rank scores when its ranking step is in the trace (the numbers the trace quotes), else
  // the worker's stored group score. The label on every score says which.
  const { frames, scoreSource } = useMemo(() => {
    const byId = new Map((group?.members ?? []).map((m) => [m.photo_id, m]));
    const worker = vm.frames.map((f) => {
      const m = byId.get(f.photo_id);
      return m ? { ...f, rank: m.rank ?? f.rank, score: m.score ?? f.score, breakdown: m.breakdown ?? f.breakdown } : f;
    });
    const agent = applyRankScores(worker, ranking);
    return agent ? { frames: agent, scoreSource: "agent" as const } : { frames: worker, scoreSource: "worker" as const };
  }, [vm.frames, group, ranking]);

  const answered = Boolean(answeredLine);
  const locked = readOnly || answered;
  const proposed = vm.proposed;
  const agentKeeper = keeperOf(frames, proposed);

  const [mode, setMode] = useState<Mode>("proposal");
  const [assign, setAssign] = useState<Assign>(proposed);
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState<false | "approve" | "pick" | "all-review">(false);
  const [error, setError] = useState<string>();
  const [outcome, setOutcome] = useState<Outcome>();
  const [zoom, setZoom] = useState<Zoom>(null);
  const [zoomOn, setZoomOn] = useState(true);
  const [pinned, setPinned] = useState<string>();
  /** The frame open at full stage size (click a photo or press Z), else the side-by-side comparison. */
  const [focused, setFocused] = useState<string>();
  const hovered = useRef<string>(undefined);
  /** The photos' true width / height, from the first one that loads (a burst shares one camera and orientation). */
  const [aspect, setAspect] = useState(1.5);
  const onAspect = useCallback((a: number) => setAspect((cur) => (Math.abs(cur - a) > 0.01 && a > 0.3 && a < 4 ? a : cur)), []);
  const noteRef = useRef<HTMLInputElement>(null);

  const yourKeeper = keeperOf(frames, assign);
  const changed = !sameAssign(frames, assign, proposed);
  const [stageRef, measured] = useElementSize<HTMLDivElement>();
  // The desk is viewport-height only from xl up; below that the stage grows with its content, so frames are
  // sized by width alone (using the measured height there would feed back into itself).
  const tall = useMediaQuery("(min-width: 1280px)");
  // The measured box holds only the frames (the filmstrip is its own row below), so its height is the budget.
  const stage = { w: measured.w, h: tall ? measured.h : Number.POSITIVE_INFINITY };
  const anyMetrics = frames.some((f) => f.breakdown);
  const capH = anyMetrics ? CAPTION_H.metrics : CAPTION_H.bare;
  const GAP = 16;
  // Frame width for n candidates: the best grid in the stage box (xl+), or one row across the width (below xl,
  // where the stage has no height budget).
  const widthFor = useCallback(
    (k: number) => (tall ? fitColumns(k, stage.w, stage.h, aspect, GAP, capH, k).frameW : Math.max(0, (stage.w - GAP * (k - 1)) / Math.max(1, k))),
    [tall, stage.w, stage.h, aspect, capH],
  );
  // How many frames go side by side. Two by default; three or four only when each stays nearly as large as with
  // two (≥ 90% of that width and never under 600px), so more frames never cost the detail needed to judge eyes.
  const maxOnStage = useMemo(() => {
    if (!stage.w || stage.w < 560) return Math.min(2, frames.length); // phones: the two best, stacked
    const two = widthFor(Math.min(2, frames.length));
    for (const k of [4, 3]) {
      if (frames.length < k) continue;
      if (widthFor(k) >= Math.max(600, two * 0.9)) return k;
    }
    return Math.min(2, frames.length);
  }, [frames.length, stage.w, widthFor]);
  const compared = useMemo(() => chooseCandidates(frames, proposed, maxOnStage, [pinned, yourKeeper]), [frames, proposed, maxOnStage, pinned, yourKeeper]);
  const focusFrame = focused ? frames.find((f) => f.photo_id === focused) : undefined;
  const candidates = focusFrame ? [focusFrame] : compared;
  const onStage = useMemo(() => new Set(candidates.map((c) => c.photo_id)), [candidates]);
  // "Best" is across the whole burst, so a frame off the stage can't be beaten by one on it.
  const lead = useMemo(() => leadersOf(frames), [frames]);
  // Two decimals on the cards when the top two are under a point apart, so equal-looking scores never hide a gap.
  const fineScores = useMemo(() => {
    const sc = frames.map((f) => f.score).filter((x): x is number => typeof x === "number").sort((a, b) => b - a);
    return sc.length >= 2 && (sc[0] - sc[1]) * 100 < 1;
  }, [frames]);
  const hasMetrics = anyMetrics && candidates.some((c) => c.breakdown);
  const verdictOf = useCallback((id: string): Verdict => (mode === "proposal" ? proposed[id] : assign[id]) ?? "review", [assign, mode, proposed]);
  const n = candidates.length;
  const narrow = stage.w > 0 && stage.w < 560;
  const fit = narrow ? { cols: 1, frameW: stage.w } : !tall ? { cols: n, frameW: widthFor(n) } : fitColumns(n, stage.w, stage.h, aspect, GAP, capH, n);

  const pick = useCallback(
    (photoId: string) => {
      if (locked || busy || outcome) return;
      if (focused) setFocused(photoId);
      if (!onStage.has(photoId)) setPinned(photoId);
      if (photoId === agentKeeper && mode !== "all-review") {
        setMode("proposal");
        setAssign(proposed);
        return;
      }
      setMode("pick");
      setAssign(pickFrame(frames, proposed, photoId));
    },
    [agentKeeper, busy, focused, frames, locked, mode, onStage, outcome, proposed],
  );
  // A filmstrip click: pick the frame, or (read-only) show it large.
  const stripClick = useCallback((photoId: string) => (locked ? setFocused(photoId) : pick(photoId)), [locked, pick]);
  /** Z: open the frame under the pointer (else the keeper) at full stage size; Z again goes back. */
  const toggleFocus = useCallback(() => {
    setFocused((f) => (f ? undefined : (hovered.current ?? yourKeeper ?? agentKeeper ?? frames[0]?.photo_id)));
  }, [agentKeeper, frames, yourKeeper]);
  const stepFocus = useCallback(
    (d: 1 | -1) => {
      const i = frames.findIndex((f) => f.photo_id === focused);
      if (i < 0) return;
      setFocused(frames[Math.max(0, Math.min(frames.length - 1, i + d))].photo_id);
    },
    [focused, frames],
  );

  const reset = useCallback(() => {
    setMode("proposal");
    setAssign(proposed);
    setPinned(undefined);
  }, [proposed]);

  const allReview = useCallback(() => {
    if (locked || busy || outcome) return;
    setMode("all-review");
    setAssign(keepAllForReview(frames, mode === "pick" ? assign : proposed));
  }, [assign, busy, frames, locked, mode, outcome, proposed]);

  const submit = useCallback(
    async (kind: "approve" | "pick" | "all-review") => {
      if (locked || busy || outcome) return;
      setBusy(kind);
      setError(undefined);
      try {
        const body: Record<string, unknown> = { decision: kind === "approve" ? "approve" : "override", note: note.trim() };
        let plan = proposed;
        if (kind !== "approve") {
          plan = kind === "all-review" ? keepAllForReview(frames, assign) : assign;
          body.human_verdict = vm.isGroup ? toVerdictMap(frames, plan) : (plan[frames[0]?.photo_id] ?? "review");
        }
        const res = await api.post<{ resume?: { via?: string; status?: string | number } }>(`/approvals/${encodeURIComponent(vm.id)}`, body);
        const keeperN = frames.find((f) => f.photo_id === keeperOf(frames, plan))?.n;
        const out = resumeOutcome(kind, res.resume, keeperN);
        setOutcome(out);
        onAnswered?.(vm.id);
        toast(`${burstLabel(vm)} answered`, { description: planSummary(frames, plan, vm.order), tone: "success", duration: 3500 });
      } catch (e) {
        setError(e instanceof ApiError ? e.message : String(e));
      } finally {
        setBusy(false);
      }
    },
    [assign, busy, frames, locked, note, onAnswered, outcome, proposed, vm.id, vm.isGroup],
  );

  // After success: hold the confirmation briefly, then advance to the next request.
  useEffect(() => {
    if (!outcome) return;
    const id = window.setTimeout(() => onAdvance?.(vm.id), ADVANCE_MS);
    return () => window.clearTimeout(id);
  }, [outcome, onAdvance, vm.id]);

  const primary = useCallback(() => {
    if (mode === "proposal") void submit("approve");
    else void submit(mode);
  }, [mode, submit]);

  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      const t = e.target as HTMLElement;
      if (e.metaKey || e.ctrlKey || e.altKey) return;
      if (t.tagName === "TEXTAREA" || t.tagName === "INPUT") {
        if (e.key === "Escape") t.blur();
        if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) primary();
        return;
      }
      const k = e.key.toLowerCase();
      if (k === "j" || e.key === "ArrowDown") {
        e.preventDefault();
        onMove(1);
      } else if (k === "k" || e.key === "ArrowUp") {
        e.preventDefault();
        onMove(-1);
      } else if (k === "z") {
        e.preventDefault();
        toggleFocus();
      } else if (focused && e.key === "Escape") {
        setFocused(undefined);
      } else if (focused && (e.key === "ArrowRight" || e.key === "ArrowLeft")) {
        e.preventDefault();
        stepFocus(e.key === "ArrowRight" ? 1 : -1);
      } else if (locked || outcome) {
        return;
      } else if (/^[1-9]$/.test(e.key) && Number(e.key) <= frames.length) {
        // The digit is the frame number (Frame 3 = 3), the same number shown on the stage and the strip.
        e.preventDefault();
        pick(frames[Number(e.key) - 1].photo_id);
      } else if (e.key === "Enter" && t.tagName !== "BUTTON" && t.tagName !== "A") {
        e.preventDefault();
        primary();
      } else if (k === "u") {
        e.preventDefault();
        allReview();
      } else if (k === "n") {
        e.preventDefault();
        noteRef.current?.focus();
      } else if (e.key === "Escape" || e.key === "0") {
        reset();
      }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [allReview, focused, frames, locked, onMove, outcome, pick, primary, reset, stepFocus, toggleFocus]);

  const why = refineWhy(vm.why, frames, vm.order).filter((w) => !SCORE_LINE.test(w));
  const conf = typeof vm.confidence === "number" ? Math.round(vm.confidence * 100) : undefined;
  const frameN = (id?: string) => frames.find((f) => f.photo_id === id)?.n;
  const nameOf = (n?: number) => (n === undefined ? "a frame" : frameName(vm.order, n));
  const byRank = vm.order === "rank";
  const plan = mode === "proposal" ? proposed : assign;
  const primaryLabel =
    readOnly ? "Approve" : mode === "proposal" ? "Approve" : mode === "pick" ? `Keep Frame ${frameN(yourKeeper)}` : "Send all to review";
  const runHref = vm.groupId && vm.runId
    ? routes.group(vm.eventId, vm.groupId, vm.runId)
    : undefined;

  const lockedReason = readOnly ? "Read-only judge view" : "Already answered";
  const showStrip = frames.length > compared.length || Boolean(focusFrame);
  const focusIdx = focusFrame ? frames.indexOf(focusFrame) : -1;

  return (
    <section aria-labelledby="ap-title" className={`${s.card} flex min-h-0 flex-1 flex-col xl:overflow-hidden`}>
      {/* Toolbar: what, the agent's question, where in the queue. One row at desk widths. */}
      <header className="flex flex-wrap items-center gap-x-4 gap-y-1 border-b border-[var(--ap-border)] px-4 py-2">
        <h2 id="ap-title" className="shrink-0 text-[1rem] font-semibold whitespace-nowrap">
          {burstLabel(vm)}
          <span className="font-normal text-[var(--ap-muted)]">
            {" "}
            · {vm.frames.length} frames{vm.spanS ? ` in ${vm.spanS.toFixed(1)} s` : ""}
          </span>
        </h2>
        <div className="relative flex min-w-0 flex-1 basis-[16rem] items-center gap-2">
          <p className="line-clamp-2 min-w-0 text-[0.92rem] leading-snug font-medium" title={vm.question}>
            {vm.question}
          </p>
          {why.length > 0 && (
            // The agent's raw reasoning (metric values) sits behind a disclosure that floats over the stage, so
            // opening it never moves the photos.
            <details className="group/why shrink-0 text-[0.84rem] text-[var(--ap-muted)]">
              <summary className="inline-flex cursor-pointer list-none items-center gap-1 rounded-[4px] font-medium whitespace-nowrap hover:text-[var(--ap-text)] focus-visible:outline-2 focus-visible:outline-[var(--ap-accent)] [&::-webkit-details-marker]:hidden">
                <svg aria-hidden="true" viewBox="0 0 12 12" className="h-3 w-3 transition-transform group-open/why:rotate-90 motion-reduce:transition-none">
                  <path d="M4.5 2.5L8 6l-3.5 3.5" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" />
                </svg>
                Agent&apos;s reasoning
              </summary>
              <p className="absolute top-full left-0 z-20 mt-2 max-w-[36rem] rounded-[8px] border border-[var(--ap-border)] bg-[var(--ap-surface)] p-3 text-pretty text-[var(--ap-text)] shadow-[var(--kf-shadow-lg,0_8px_24px_rgb(0_0_0/0.18))]">
                {why.map((w, i) => (
                  <span key={i}>
                    {i > 0 && " "}
                    <Glossed text={w} plain />
                  </span>
                ))}
              </p>
            </details>
          )}
        </div>
        <span className="flex shrink-0 flex-wrap items-center gap-x-4 gap-y-1 text-[0.8rem] whitespace-nowrap text-[var(--ap-muted)]">
          {vm.createdAt && !answered && <span>{relAge(vm.createdAt, Date.now())}</span>}
          {conf !== undefined && (
            <span className="inline-flex items-center gap-2" title="Agent confidence in its pick">
              <span className="sr-only">Agent confidence </span>
              {conf}%
              <span aria-hidden="true" className="h-1.5 w-12 overflow-hidden rounded-full bg-[var(--ap-surface-2)] ring-1 ring-[var(--ap-border)] ring-inset">
                <span className="block h-full rounded-full bg-[var(--ap-review)]" style={{ width: `${conf}%` }} />
              </span>
            </span>
          )}
          <span className="flex items-center gap-1">
            <span className="tabular-nums">
              {position.index + 1} of {position.total}
            </span>
            <NavButton label="Previous request (K)" disabled={position.index <= 0} onClick={() => onMove(-1)} dir={-1} />
            <NavButton label="Next request (J)" disabled={position.index >= position.total - 1} onClick={() => onMove(1)} dir={1} />
          </span>
          {runHref && (
            <Link href={runHref} className="font-medium text-[var(--ap-accent)] hover:underline">
              Open group
            </Link>
          )}
        </span>
      </header>

      {/* Comparison stage: dark, so the photos carry the colour. It takes every pixel the toolbar and bar leave. */}
      <div className="relative flex min-h-0 flex-1 flex-col bg-[var(--ap-stage)] px-1 pt-1.5 pb-2 text-white">
        <div className="mb-1.5 flex h-7 shrink-0 items-center justify-between gap-3 px-1 text-[0.78rem] text-white/80">
          {focusFrame ? (
            <span className="flex min-w-0 items-center gap-1">
              <button type="button" onClick={() => setFocused(undefined)} className="inline-flex items-center gap-1.5 rounded-[6px] px-2 py-1 font-medium text-white hover:bg-white/10 focus-visible:outline-2 focus-visible:outline-white">
                <svg aria-hidden="true" viewBox="0 0 16 16" className="h-3.5 w-3.5">
                  <path d="M10 3.5 5.5 8l4.5 4.5" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" />
                </svg>
                Back to compare <kbd className={s.kbd}>Z</kbd>
              </button>
              <span className="ml-2 tabular-nums">
                {nameOf(focusFrame.n)} · {focusIdx + 1} of {frames.length}
              </span>
              <StageNav label="Previous frame (←)" disabled={focusIdx <= 0} onClick={() => stepFocus(-1)} dir={-1} />
              <StageNav label="Next frame (→)" disabled={focusIdx >= frames.length - 1} onClick={() => stepFocus(1)} dir={1} />
            </span>
          ) : (
            <span className="min-w-0">
              {frames.length > candidates.length ? `Top ${candidates.length} of ${frames.length}` : `All ${frames.length}`}
              <span className="text-white/60"> · click a photo or press <kbd className={s.kbd}>Z</kbd> to enlarge</span>
            </span>
          )}
          <button
            type="button"
            onClick={() => setZoomOn((z) => !z)}
            aria-pressed={zoomOn}
            title="Hover a frame to zoom every frame to the same spot"
            className="inline-flex shrink-0 items-center gap-1.5 rounded-[6px] px-2 py-1 hover:bg-white/10 focus-visible:outline-2 focus-visible:outline-white"
          >
            <svg aria-hidden="true" viewBox="0 0 16 16" className="h-3.5 w-3.5">
              <circle cx="7" cy="7" r="4.25" fill="none" stroke="currentColor" strokeWidth="1.5" />
              <path d="m10.2 10.2 3.3 3.3" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
            </svg>
            Synced zoom {zoomOn ? "on" : "off"}
          </button>
        </div>
        <div ref={stageRef} className="flex min-h-0 min-w-0 flex-1 items-center justify-center xl:min-h-[240px]">
          <div
            className="grid justify-center gap-4"
            style={{ gridTemplateColumns: `repeat(${fit.cols}, ${fit.frameW > 0 ? `${Math.floor(fit.frameW)}px` : "minmax(0, 1fr)"})` }}
          >
            {candidates.map((f) => (
              <div key={f.photo_id} onMouseEnter={() => (hovered.current = f.photo_id)} onMouseLeave={() => (hovered.current = undefined)}>
                <Candidate
                  frame={f}
                  hotkey={f.n <= 9 ? f.n : undefined}
                  verdict={verdictOf(f.photo_id)}
                  agentPick={f.photo_id === agentKeeper}
                  yourPick={mode !== "proposal" && f.photo_id === yourKeeper && f.photo_id !== agentKeeper}
                  isKeeper={mode !== "all-review" && f.photo_id === (mode === "proposal" ? agentKeeper : yourKeeper)}
                  pickLabel={f.photo_id === agentKeeper ? (byRank ? "Keeper" : "Agent's pick") : "Your pick"}
                  name={nameOf(f.n)}
                  zoom={zoom}
                  zoomOn={zoomOn}
                  onZoom={setZoom}
                  onPick={() => pick(f.photo_id)}
                  onOpen={() => setFocused(f.photo_id)}
                  onAspect={onAspect}
                  aspect={aspect}
                  enlarged={Boolean(focusFrame)}
                  readOnly={locked}
                  leaders={lead}
                  width={fit.frameW > 0 ? Math.floor(fit.frameW) : undefined}
                  showMetrics={hasMetrics}
                  scoreLabel={scoreLabel(scoreSource)}
                  fineScores={fineScores}
                />
              </div>
            ))}
          </div>
        </div>
        {showStrip && (
          <div className="mt-2 shrink-0 border-t border-white/10 pt-1.5">
            <Filmstrip frames={frames} verdictOf={verdictOf} onStage={focusFrame ? new Set([focusFrame.photo_id]) : onStage} onPick={stripClick} readOnly={locked} lockedReason={lockedReason} nameOf={nameOf} />
          </div>
        )}

        {outcome && <SuccessOverlay outcome={outcome} last={position.total <= 1} />}
        {busy && !outcome && (
          <div className="absolute inset-0 grid place-items-center bg-black/55 backdrop-blur-[2px]" role="status">
            <p className="flex items-center gap-3 text-[1.05rem] font-semibold">
              <span className={`inline-block h-2.5 w-2.5 rounded-full bg-white ${s.pulse}`} aria-hidden="true" />
              Sending…
            </p>
          </div>
        )}
      </div>

      {/* Decision bar: one compact row, pinned to the bottom of the desk (sticky while the page scrolls below xl). */}
      <footer
        data-kf-decision-bar
        className="sticky bottom-0 z-10 flex flex-col gap-2 rounded-b-[var(--ap-radius)] border-t border-[var(--ap-border)] bg-[var(--ap-surface)] px-4 py-2.5"
      >
        {answered ? (
          <AnsweredBar line={answeredLine!} note={answeredNote} summary={planSummary(frames, proposed, vm.order)} />
        ) : (
          <>
            <div className="flex flex-wrap items-center gap-x-4 gap-y-2 xl:flex-nowrap">
              {!readOnly && agentKeeper && changed && (
                <BeforeAfter
                  before={frames.find((f) => f.photo_id === agentKeeper)}
                  after={mode === "all-review" ? undefined : frames.find((f) => f.photo_id === (mode === "proposal" ? agentKeeper : yourKeeper))}
                  allReview={mode === "all-review"}
                  nameOf={nameOf}
                  fine={fineScores}
                />
              )}
              <p className="min-w-0 flex-1 basis-[14rem] text-[0.86rem] leading-snug" aria-live="polite">
                <span className="text-[var(--ap-muted)]">{readOnly ? "Now: " : "Saves: "}</span>
                <span className="font-medium">{planSummary(frames, plan, vm.order)}</span>
                {note.trim() && !locked && <span className="text-[var(--ap-muted)]"> · with your note</span>}
                {changed && !locked && (
                  <button type="button" onClick={reset} className="ml-2 whitespace-nowrap text-[var(--ap-accent)] hover:underline">
                    Undo <kbd className={s.kbd}>Esc</kbd>
                  </button>
                )}
              </p>
              <div className="flex min-w-0 flex-wrap items-center justify-end gap-2 max-xl:basis-full xl:flex-nowrap">
                {readOnly && (
                  <span className="mr-auto inline-flex items-center gap-1.5 text-[0.8rem] whitespace-nowrap text-[var(--ap-muted)] xl:mr-1">
                    <svg aria-hidden="true" viewBox="0 0 16 16" className="h-3.5 w-3.5">
                      <rect x="3" y="7" width="10" height="7" rx="1.5" fill="none" stroke="currentColor" strokeWidth="1.4" />
                      <path d="M5.5 7V5a2.5 2.5 0 0 1 5 0v2" fill="none" stroke="currentColor" strokeWidth="1.4" />
                    </svg>
                    Read-only
                  </span>
                )}
                {!locked && (
                  <div className="relative min-w-0 flex-1 basis-[12rem] xl:w-[18rem] xl:flex-none 2xl:w-[22rem]">
                    <label htmlFor={`note-${vm.id}`} className="sr-only">
                      Note for the record (optional)
                    </label>
                    <input
                      id={`note-${vm.id}`}
                      ref={noteRef}
                      type="text"
                      maxLength={500}
                      value={note}
                      onChange={(e) => setNote(e.target.value)}
                      onKeyDown={(e) => {
                        if (e.key === "Enter") {
                          e.preventDefault();
                          primary();
                        }
                      }}
                      placeholder="Note (optional)"
                      className="h-10 w-full rounded-[8px] border border-[var(--ap-border)] bg-[var(--ap-surface)] pr-9 pl-3 text-[0.88rem] outline-none placeholder:text-[var(--ap-muted)] focus:border-[var(--ap-accent)] focus:ring-2 focus:ring-[var(--ap-accent-soft)]"
                    />
                    <kbd className={`${s.kbd} pointer-events-none absolute top-1/2 right-2.5 -translate-y-1/2 text-[var(--ap-muted)]`}>N</kbd>
                  </div>
                )}
                <ActionButton
                  className={s.quiet}
                  disabled={locked || Boolean(busy) || Boolean(outcome) || mode === "all-review"}
                  onClick={allReview}
                  kbd={readOnly ? undefined : "U"}
                  readOnly={readOnly}
                >
                  Send all to review
                </ActionButton>
                <ActionButton
                  className={s.primary}
                  disabled={locked || Boolean(busy) || Boolean(outcome) || (mode === "proposal" && !vm.hasProposal)}
                  onClick={primary}
                  kbd={readOnly ? undefined : "↵"}
                  readOnly={readOnly}
                  wide
                >
                  {busy ? "Sending…" : primaryLabel}
                </ActionButton>
              </div>
            </div>
            {error && (
              <p role="alert" className="rounded-[8px] bg-[#fdecea] px-3 py-2 text-[0.86rem] text-[var(--ap-reject)]">
                {error}
              </p>
            )}
            {vm.resumeError && !outcome && (
              <p className="text-[0.82rem] text-[var(--ap-muted)]">
                The last answer was saved but the agent didn&apos;t pick it up. Answer again to retry.
              </p>
            )}
          </>
        )}
      </footer>
    </section>
  );
}

function StageNav({ label, disabled, onClick, dir }: { label: string; disabled: boolean; onClick: () => void; dir: 1 | -1 }) {
  return (
    <button
      type="button"
      aria-label={label}
      title={label}
      disabled={disabled}
      onClick={onClick}
      className="grid h-7 w-7 place-items-center rounded-[6px] text-white hover:bg-white/10 focus-visible:outline-2 focus-visible:outline-white disabled:opacity-35"
    >
      <svg aria-hidden="true" viewBox="0 0 16 16" className="h-4 w-4">
        <path d={dir === 1 ? "M6 4l4 4-4 4" : "M10 4 6 8l4 4"} fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" />
      </svg>
    </button>
  );
}

/** The keeper choice as a before/after pair: the agent's pick, and what will be saved. */
function BeforeAfter({ before, after, allReview, nameOf, fine }: { before?: FrameVM; after?: FrameVM; allReview: boolean; nameOf: (n?: number) => string; fine: boolean }) {
  if (!before) return null;
  const same = !allReview && after?.photo_id === before.photo_id;
  // The delta is the difference of the scores as shown on the cards, so it always adds up.
  const dp = fine ? 2 : 1;
  const shown = (v: number) => Math.round(v * 10 ** (dp + 2)) / 10 ** dp;
  const delta = typeof before.score === "number" && typeof after?.score === "number" && !same ? shown(after.score) - shown(before.score) : undefined;
  return (
    <div className="flex items-center gap-3" aria-label="Keeper before and after your answer">
      <Thumb frame={before} caption="Before" sub={`Agent's pick · ${nameOf(before.n)}`} tone="accent" dp={dp} />
      <svg aria-hidden="true" viewBox="0 0 24 24" className="h-5 w-5 shrink-0 text-[var(--ap-muted)]">
        <path d="M4 12h15m-5-5 5 5-5 5" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" />
      </svg>
      {allReview ? (
        <div className="flex h-10 w-[96px] shrink-0 items-center justify-center rounded-[6px] border border-dashed border-[var(--ap-review)] bg-[var(--kf-review-soft,#fdf2e1)] px-2 text-center text-[0.75rem] font-semibold text-[var(--ap-review)]">
          No keeper: all to review
        </div>
      ) : after ? (
        <Thumb frame={after} caption="After" sub={same ? "Same frame, approved" : `Your pick · ${nameOf(after.n)}`} tone={same ? "accent" : "keep"} dp={dp} />
      ) : null}
      {delta !== undefined && (
        <p className="ml-1 text-[0.8rem] whitespace-nowrap text-[var(--ap-muted)]">
          <span className="t-data font-semibold text-[var(--ap-text)]">
            {delta >= 0 ? "+" : "−"}
            {Math.abs(delta).toFixed(dp)}
          </span>{" "}
          points vs the agent&apos;s pick
        </p>
      )}
    </div>
  );
}

function Thumb({ frame, caption, sub, tone, dp }: { frame: FrameVM; caption: string; sub: string; tone: "accent" | "keep"; dp: number }) {
  return (
    <figure className="m-0 flex min-w-0 items-center gap-2.5">
      <span className={`relative block h-10 w-[60px] shrink-0 overflow-hidden rounded-[6px] bg-black ring-[2px] ${tone === "keep" ? "ring-[var(--ap-keep)]" : "ring-[var(--ap-accent)]"}`}>
        {/* eslint-disable-next-line @next/next/no-img-element */}
        <img src={frame.proxy_url} alt="" referrerPolicy="no-referrer" className="h-full w-full object-cover" />
      </span>
      <figcaption className="min-w-0 leading-tight">
        <span className="t-eyebrow block">{caption}</span>
        <span className="block truncate text-[0.8rem] font-semibold">{sub}</span>
        {typeof frame.score === "number" && <span className="t-data block text-[0.76rem] text-[var(--ap-muted)]">{(frame.score * 100).toFixed(dp)}</span>}
      </figcaption>
    </figure>
  );
}

function NavButton({ label, disabled, onClick, dir }: { label: string; disabled: boolean; onClick: () => void; dir: 1 | -1 }) {
  return (
    <button
      type="button"
      aria-label={label}
      title={label}
      disabled={disabled}
      onClick={onClick}
      className="grid h-7 w-7 place-items-center rounded-[6px] text-[var(--ap-text)] hover:bg-[var(--ap-surface-2)] focus-visible:outline-2 focus-visible:outline-[var(--ap-accent)] disabled:opacity-35"
    >
      <svg aria-hidden="true" viewBox="0 0 16 16" className="h-4 w-4">
        <path d={dir === 1 ? "M4 6l4 4 4-4" : "M4 10l4-4 4 4"} fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" />
      </svg>
    </button>
  );
}

function ActionButton({
  children,
  className,
  disabled,
  onClick,
  kbd,
  wide,
  readOnly,
}: {
  children: React.ReactNode;
  className: string;
  disabled: boolean;
  onClick: () => void;
  kbd?: string;
  wide?: boolean;
  readOnly?: boolean;
}) {
  const btn = (
    <button
      type="button"
      className={`${s.btn} ${className} ${wide ? "min-w-[12rem]" : ""}`}
      aria-disabled={disabled || undefined}
      onClick={disabled ? undefined : onClick}
    >
      {children}
      {kbd && <kbd className={s.kbd}>{kbd}</kbd>}
    </button>
  );
  return readOnly ? <Tooltip label={READ_ONLY}>{btn}</Tooltip> : btn;
}

function AnsweredBar({ line, note, summary }: { line: string; note?: string; summary: string }) {
  return (
    <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
      <span className="inline-flex items-center gap-1.5 rounded-[6px] bg-[#fdf1de] px-2 py-1 text-[0.8rem] font-semibold text-[var(--ap-review)]">
        <svg aria-hidden="true" viewBox="0 0 12 12" className="h-3 w-3">
          <path d="M2 2h8v8H2z" fill="none" stroke="currentColor" strokeWidth="1.5" />
          <path d="M4 5h4M4 7.5h2.5" stroke="currentColor" strokeWidth="1.2" />
        </svg>
        {line}
      </span>
      <span className="sr-only">Saved: {summary}</span>
      {note && <p className="text-[0.86rem] text-[var(--ap-muted)]">Note: “{note}”</p>}
    </div>
  );
}

function SuccessOverlay({ outcome, last }: { outcome: Outcome; last: boolean }) {
  return (
    <div
      role="status"
      className={`${s.fadeIn} absolute inset-0 z-10 grid place-items-center bg-[var(--ap-stage)]`}
      style={{ ["--ap-advance" as string]: `${ADVANCE_MS}ms` }}
    >
      <div className="flex max-w-md flex-col items-center px-6 text-center">
        <span className="grid h-14 w-14 place-items-center rounded-full bg-[var(--ap-keep)] shadow-[0_0_0_8px_rgb(23_128_61_/_0.22)]">
          <svg viewBox="0 0 24 24" className="h-7 w-7" aria-hidden="true">
            <path className={s.check} d="M5 12.5 10 17.5 19 7" fill="none" stroke="#fff" strokeWidth="2.6" strokeLinecap="round" strokeLinejoin="round" />
          </svg>
        </span>
        <p className="mt-4 text-[1.35rem] font-semibold text-white">{outcome.title}</p>
        <p className="mt-1 text-[0.92rem] text-white/75">{outcome.detail}</p>
        <div className="mt-5 h-1 w-48 overflow-hidden rounded-full bg-white/15" aria-hidden="true">
          <div className={`${s.progress} h-full bg-white/85`} />
        </div>
        <p className="mt-2 text-[0.8rem] text-white/80">{last ? "That was the last one." : "Opening the next request…"}</p>
      </div>
    </div>
  );
}

export function DecisionSkeleton() {
  return (
    <div className={`${s.card} flex flex-col gap-4 p-5`} aria-hidden="true">
      <div className={`${s.skel} h-5 w-1/3`} />
      <div className={`${s.skel} h-6 w-2/3`} />
      <div className="grid flex-1 grid-cols-2 gap-4 rounded-[8px] bg-[var(--ap-stage)] p-4">
        {[0, 1, 2, 3].map((i) => (
          <div key={i} className="aspect-[3/2] rounded-[8px] bg-white/[0.06]" />
        ))}
      </div>
      <div className="flex justify-end gap-2">
        <div className={`${s.skel} h-10 w-40`} />
        <div className={`${s.skel} h-10 w-52`} />
      </div>
    </div>
  );
}
