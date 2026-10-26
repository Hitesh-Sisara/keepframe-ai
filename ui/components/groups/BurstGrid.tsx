"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { captureOrder, memberMatches, reasonBase, sortGroups, type GroupSort, type VerdictFilter } from "@/lib/cull";
import { framesText } from "@/lib/format";
import { parseReason } from "@/lib/reasons";
import type { Group, Verdict } from "@/lib/types";
import { Frame } from "../Frame";
import { VERDICT_COLOR, VERDICT_WORD, VerdictGlyph, VerdictTally } from "../VerdictMark";
import { routes } from "@/lib/routes";
import { cardReason, CONFIDENCE_HINT, CONFIDENCE_WORD, confidenceOf, type Confidence } from "@/lib/cardreason";
import { Tooltip } from "../ui";
import { ShortcutButton, ShortcutSheet, type ShortcutGroup } from "./Shortcuts";

const ZOOM_KEY = "keepframe.cull.zoom";
const ZOOM_MIN = 160;
const ZOOM_MAX = 520;

export interface GridState {
  verdict: VerdictFilter;
  reason: string;
  sort: GroupSort;
}

/** Event-wide cull grid: every burst as a card with its frames, filterable by verdict and reason. */
export function BurstGrid({
  eventId,
  groups,
  runId,
  state,
  onState,
}: {
  eventId: string;
  groups: Group[];
  runId?: string;
  state: GridState;
  onState: (s: Partial<GridState>) => void;
}) {
  const router = useRouter();
  const [zoom, setZoom] = useState(200);
  useEffect(() => {
    const z = Number(window.localStorage.getItem(ZOOM_KEY));
    if (z >= ZOOM_MIN && z <= ZOOM_MAX) setZoom(z);
  }, []);
  useEffect(() => window.localStorage.setItem(ZOOM_KEY, String(zoom)), [zoom]);

  const counts = useMemo(() => {
    const c: Record<Verdict, number> = { keep: 0, review: 0, reject: 0 };
    for (const g of groups) for (const m of g.members) if (m.verdict) c[m.verdict]++;
    return c;
  }, [groups]);
  const total = useMemo(() => groups.reduce((n, g) => n + g.members.length, 0), [groups]);

  const reasons = useMemo(() => {
    const c = new Map<string, number>();
    for (const g of groups)
      for (const m of g.members)
        if (state.verdict === "all" || m.verdict === state.verdict)
          for (const r of new Set((m.reason_codes ?? []).map(reasonBase))) c.set(r, (c.get(r) ?? 0) + 1);
    return [...c.entries()].sort((a, b) => b[1] - a[1]);
  }, [groups, state.verdict]);

  const shown = useMemo(() => {
    const sorted = sortGroups(groups, state.sort);
    return sorted
      .map((g) => ({ g, frames: captureOrder(g.members).filter((m) => memberMatches(m, state.verdict, state.reason)) }))
      .filter((x) => x.frames.length > 0);
  }, [groups, state]);
  const shownFrames = shown.reduce((n, x) => n + x.frames.length, 0);

  // Keyboard: arrows move between groups, Enter opens, 1-4 switch the verdict filter, +/- zoom.
  const [focus, setFocus] = useState(0);
  const [keys, setKeys] = useState(false);
  const closeKeys = useCallback(() => setKeys(false), []);
  const cards = useRef<(HTMLAnchorElement | null)[]>([]);
  const gridRef = useRef<HTMLOListElement>(null);
  useEffect(() => setFocus((f) => Math.min(f, Math.max(0, shown.length - 1))), [shown.length]);
  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      const t = e.target as HTMLElement | null;
      if (e.metaKey || e.ctrlKey || e.altKey || (t && /^(INPUT|TEXTAREA|SELECT)$/.test(t.tagName)) || t?.isContentEditable) return;
      const vmap: Record<string, VerdictFilter> = { "1": "all", "2": "keep", "3": "review", "4": "reject" };
      if (vmap[e.key]) {
        onState({ verdict: vmap[e.key], reason: "" });
        return;
      }
      if (e.key === "?") {
        e.preventDefault();
        setKeys(true);
        return;
      }
      if (e.key === "+" || e.key === "=") setZoom((z) => Math.min(ZOOM_MAX, z + 40));
      else if (e.key === "-") setZoom((z) => Math.max(ZOOM_MIN, z - 40));
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onState]);

  const win = useGridWindow(gridRef, shown.length, zoom + 80, GAP);
  const pendingFocus = useRef<number | null>(null);
  useEffect(() => {
    const i = pendingFocus.current;
    if (i === null) return;
    const el = cards.current[i];
    if (el) {
      pendingFocus.current = null;
      el.focus({ preventScroll: true });
      el.scrollIntoView({ block: "nearest", behavior: "auto" });
    } else win.reveal(i);
  });
  function onGridKey(e: React.KeyboardEvent) {
    const n = shown.length;
    if (!n) return;
    const c = win.cols;
    const d =
      e.key === "ArrowRight" ? 1 : e.key === "ArrowLeft" ? -1 : e.key === "ArrowDown" ? c : e.key === "ArrowUp" ? -c : e.key === "Home" ? -n : e.key === "End" ? n : 0;
    if (!d) return;
    e.preventDefault();
    const next = Math.max(0, Math.min(n - 1, focus + d));
    setFocus(next);
    pendingFocus.current = next;
    cards.current[next]?.focus();
  }

  const href = (g: Group) =>
    routes.group(eventId, g.group_id, g.run_id ?? runId);

  return (
    <>
      {/* Toolbar */}
      {/* Sticky toolbar. The background bleeds to the column gutters so cards scroll under it cleanly, but the
          rule underneath stays inside the content column like every other page. */}
      <div className="sticky top-14 z-20 mb-4 bg-[var(--kf-bg)]/95 pt-3 shadow-[0_1px_0_var(--kf-border)] backdrop-blur md:top-0">
        <div className="pb-3">
        <div className="flex flex-wrap items-center gap-x-4 gap-y-2.5">
          <div className="-mx-1 flex max-w-full items-center gap-2 overflow-x-auto px-1 py-0.5 [scrollbar-width:none]">
          <VerdictFilterBar value={state.verdict} counts={counts} total={total} onChange={(v) => onState({ verdict: v, reason: "" })} />
          </div>
          <label className="flex items-center gap-2 text-[0.8125rem] text-[var(--kf-muted)]">
            <span className="sr-only">Reason</span>
            <select
              value={state.reason}
              onChange={(e) => onState({ reason: e.target.value })}
              className="kf-select w-[190px]"
            >
              <option value="">Any reason</option>
              {reasons.map(([code, n]) => (
                <option key={code} value={code}>
                  {parseReason(code).def.label} ({n})
                </option>
              ))}
            </select>
          </label>
          <label className="flex items-center gap-2 text-[0.8125rem] text-[var(--kf-muted)]">
            <span className="sr-only">Sort</span>
            <select
              value={state.sort}
              onChange={(e) => onState({ sort: e.target.value as GroupSort })}
              className="kf-select w-[190px]"
            >
              <option value="capture">Capture order</option>
              <option value="close">Closest calls first</option>
              <option value="review">Most to review</option>
              <option value="size">Largest bursts</option>
            </select>
          </label>
          <div className="ml-auto hidden items-center gap-2.5 sm:flex">
            <ZoomIcon small />
            <input
              type="range"
              min={ZOOM_MIN}
              max={ZOOM_MAX}
              step={20}
              value={zoom}
              onChange={(e) => setZoom(Number(e.target.value))}
              aria-label="Thumbnail size"
              className="kf-range w-28 accent-[var(--kf-accent)]"
            />
            <ZoomIcon />
          </div>
        </div>
        </div>
      </div>

      <p className={`t-small mb-3 min-h-6 flex-wrap items-center gap-x-3 gap-y-1 text-[var(--kf-muted)] flex`} aria-live="polite">
        {(state.verdict !== "all" || state.reason) && (
          <span>
            <span className="t-data font-semibold text-[var(--kf-text)]">{shownFrames}</span> frames ·{" "}
            <span className="t-data font-semibold text-[var(--kf-text)]">{shown.length}</span>/{groups.length} groups
          </span>
        )}
        {(state.verdict !== "all" || state.reason) && (
          <button type="button" className="font-medium text-[var(--kf-accent-text)] hover:underline" onClick={() => onState({ verdict: "all", reason: "" })}>
            Clear
          </button>
        )}
        <span className="ml-auto hidden sm:inline-flex">
          <ShortcutButton onClick={() => setKeys(true)} withLabel />
        </span>
      </p>

      <style>{`@keyframes kf-card-in{from{opacity:.001;transform:translateY(4px)}to{opacity:1;transform:none}}.kf-card-in{animation:kf-card-in .28s cubic-bezier(.2,.8,.2,1) both}@media (prefers-reduced-motion:reduce){.kf-card-in{animation:none}}`}</style>
      <ShortcutSheet open={keys} onClose={closeKeys} groups={GRID_KEYS} note="In a group: F full-screen cull" />
      {shown.length === 0 ? (
        <div className="rounded-[var(--kf-radius)] border border-dashed border-[var(--kf-border-strong)] bg-[var(--kf-surface)] px-6 py-14 text-center">
          <p className="t-heading">No frames match</p>
          <button type="button" className="btn btn-secondary btn-sm mt-4" onClick={() => onState({ verdict: "all", reason: "" })}>
            Clear filters
          </button>
        </div>
      ) : (
        <ol
          ref={gridRef}
          onKeyDown={onGridKey}
          className="grid"
          style={{ gridTemplateColumns: `repeat(${win.cols}, minmax(0, 1fr))`, gap: GAP, paddingTop: win.padTop, paddingBottom: win.padBottom }}
          aria-label="Groups"
          aria-rowcount={win.virtual ? Math.ceil(shown.length / win.cols) : undefined}
        >
          {shown.slice(win.from, win.to).map(({ g, frames }, k) => {
            const i = win.from + k;
            return (
              <li key={g.group_id} ref={k === 0 ? win.measure : undefined}>
                <Link
                  ref={(el) => {
                    cards.current[i] = el;
                  }}
                  href={href(g)}
                  tabIndex={i === focus ? 0 : -1}
                  onFocus={() => setFocus(i)}
                  onKeyDown={(e) => {
                    if (e.key === " ") {
                      e.preventDefault();
                      router.push(href(g));
                    }
                  }}
                  className="group/card kf-card kf-card-in block h-full p-2 transition-[box-shadow,border-color,transform] hover:-translate-y-px hover:border-[var(--kf-border-strong)] hover:shadow-[var(--kf-shadow-lg)] focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[var(--kf-accent)] motion-reduce:hover:translate-y-0"
                  aria-label={`${g.group_label ?? "Group"}, ${framesText(g)}, ${CONFIDENCE_WORD[confidenceOf(g)]}. ${cardReason(g)}${g.pending_approval ? ", waiting for a person" : ""}`}
                >
                  <BurstCard g={g} frames={frames} zoom={zoom} />
                </Link>
              </li>
            );
          })}
        </ol>
      )}
    </>
  );
}

function BurstCard({ g, frames, zoom }: { g: Group; frames: Group["members"]; zoom: number }) {
  // Lead frame = the best pick when it is in the filtered set, else the first match. Below it, a strip of fixed
  // slots (same shape on every card) for the rest, with "+N" in its own slot so no badge is ever covered.
  const best = frames.find((m) => m.is_best);
  const lead = best ?? frames[0];
  // Under a filter, the strip shows the matches first, then the group's other frames faded for context.
  const matches = frames.filter((m) => m !== lead);
  const context = captureOrder(g.members).filter((m) => m !== lead && !frames.includes(m));
  const others = [...matches, ...context];
  const slots = zoom >= 400 ? 5 : 4;
  const fits = others.length <= slots;
  const strip = fits ? others : others.slice(0, slots - 1);
  const more = others.length - strip.length;
  const conf = confidenceOf(g);
  const reason = cardReason(g);
  return (
    <>
      <Frame src={lead.proxy_url} alt={`${g.group_label ?? "Group"}, lead frame`} verdict={lead.verdict} best={lead.is_best} markSize="md" className="!rounded-[7px]" aspect="3 / 2" dimReject />
      {/* Fixed-height strip (same on every card); fewer frames simply get wider tiles. */}
      {strip.length + (more > 0 ? 1 : 0) > 0 ? (
        <div className="mt-1 flex gap-1" style={{ aspectRatio: `${slots * 3 + (slots - 1) * 0.08} / 2` }}>
          {strip.map((m) => (
            <Frame
              key={m.photo_id}
              src={m.proxy_url}
              alt=""
              verdict={m.verdict}
              best={m.is_best}
              markSize="xs"
              aspect=""
              className={`h-full min-w-0 shrink-0 !rounded-[5px] ${context.includes(m) ? "opacity-45 grayscale" : ""}`}
              dimReject
              ring={false}
              style={{ width: `calc((100% - ${(slots - 1) * 4}px) / ${slots})` }}
            />
          ))}
          {more > 0 && (
            <span
              style={{ width: `calc((100% - ${(slots - 1) * 4}px) / ${slots})` }}
              className="t-data grid h-full min-w-0 shrink-0 place-items-center rounded-[5px] bg-[var(--kf-surface-2)] text-[0.8125rem] font-semibold text-[var(--kf-text)] ring-1 ring-inset ring-[var(--kf-border-strong)]"
            >
              +{more}
              <span className="sr-only"> more frames</span>
            </span>
          )}
        </div>
      ) : (
        <div className="mt-1" style={{ aspectRatio: `${slots * 3 + (slots - 1) * 0.08} / 2` }} aria-hidden="true" />
      )}
      <div className="mt-2 flex items-center gap-2 px-0.5">
        <span className="truncate text-[0.875rem] font-semibold text-[var(--kf-text)]">{g.group_label ?? "Group"}</span>
        <span className="t-data shrink-0 text-[0.75rem] text-[var(--kf-muted)]" aria-hidden="true">{g.size}</span>
        <span className="ml-auto shrink-0">{g.pending_approval ? <WaitingChip /> : <ConfidenceChip c={conf} />}</span>
      </div>
      <p className="mt-0.5 truncate px-0.5 text-[0.8125rem] leading-5 text-[var(--kf-muted)]">{reason}</p>
    </>
  );
}

const CONF_STYLE: Record<Confidence, { fg: string; bg: string }> = {
  confident: { fg: "var(--kf-keep)", bg: "var(--kf-keep-soft)" },
  close: { fg: "var(--kf-review)", bg: "var(--kf-review-soft)" },
  unsure: { fg: "#5a3db3", bg: "#f1ecfd" },
};

/** Categorical confidence: icon + word (never colour alone), with the scale in a tooltip. */
export function ConfidenceChip({ c }: { c: Confidence }) {
  const st = CONF_STYLE[c];
  return (
    <Tooltip label={CONFIDENCE_HINT} side="top">
      <span
        className="inline-flex h-[22px] cursor-help items-center gap-1 rounded-full pr-2 pl-1.5 text-[0.71875rem] font-semibold ring-1 ring-inset ring-current/25"
        style={{ color: st.fg, background: st.bg }}
      >
        <ConfGlyph c={c} />
        {CONFIDENCE_WORD[c]}
      </span>
    </Tooltip>
  );
}

function ConfGlyph({ c }: { c: Confidence }) {
  // Three bars filled 3/2/1: a signal-strength read that works without colour.
  const n = c === "confident" ? 3 : c === "close" ? 2 : 1;
  return (
    <svg viewBox="0 0 12 12" className="h-3 w-3" aria-hidden="true">
      {[0, 1, 2].map((i) => (
        <rect key={i} x={1 + i * 3.7} y={8 - i * 2.6} width="2.6" height={3 + i * 2.6} rx="0.8" fill="currentColor" opacity={i < n ? 1 : 0.28} />
      ))}
    </svg>
  );
}

function WaitingChip() {
  return (
    <span className="inline-flex h-[22px] items-center gap-1 rounded-full bg-[var(--kf-review-soft)] pr-2 pl-1.5 text-[0.71875rem] font-semibold text-[var(--kf-review)] ring-1 ring-inset ring-current/25">
      <VerdictGlyph verdict="review" className="h-3 w-3" />
      Waiting
    </span>
  );
}

export function VerdictFilterBar({
  value,
  counts,
  total,
  onChange,
}: {
  value: VerdictFilter;
  counts: Record<Verdict, number>;
  total: number;
  onChange: (v: VerdictFilter) => void;
}) {
  const opts: { v: VerdictFilter; label: string; n: number }[] = [
    { v: "all", label: "All", n: total },
    { v: "keep", label: "Keep", n: counts.keep },
    { v: "review", label: "Review", n: counts.review },
    { v: "reject", label: "Reject", n: counts.reject },
  ];
  function onKey(e: React.KeyboardEvent) {
    const d = e.key === "ArrowRight" ? 1 : e.key === "ArrowLeft" ? -1 : 0;
    if (!d) return;
    e.preventDefault();
    e.stopPropagation();
    const i = opts.findIndex((o) => o.v === value);
    const n = opts[(i + d + opts.length) % opts.length];
    onChange(n.v);
    (e.currentTarget.querySelector(`[data-v="${n.v}"]`) as HTMLElement | null)?.focus();
  }
  return (
    <div role="radiogroup" aria-label="Show verdict" onKeyDown={onKey} className="inline-flex rounded-[9px] bg-[var(--kf-surface-2)] p-[3px] ring-1 ring-inset ring-[var(--kf-border)]">
      {opts.map((o) => {
        const on = o.v === value;
        return (
          <button
            key={o.v}
            type="button"
            role="radio"
            data-v={o.v}
            aria-checked={on}
            tabIndex={on ? 0 : -1}
            onClick={() => onChange(o.v)}
            className={`inline-flex h-7 items-center gap-1.5 rounded-[6px] px-2.5 text-[0.8125rem] font-medium transition-[background-color,color,box-shadow] ${
              on ? "bg-[var(--kf-surface)] text-[var(--kf-text)] shadow-[0_1px_2px_rgb(16_24_40/0.1),0_0_0_1px_var(--kf-border)]" : "text-[var(--kf-muted)] hover:text-[var(--kf-text)]"
            }`}
          >
            {o.v !== "all" && (
              <span aria-hidden="true" className="grid h-3.5 w-3.5 place-items-center rounded-full text-white" style={{ background: VERDICT_COLOR[o.v] }}>
                <VerdictGlyph verdict={o.v} className="h-2.5 w-2.5" />
              </span>
            )}
            <span className={o.v === "all" ? "" : "sr-only sm:not-sr-only"}>{o.label}</span>
            <span className="t-data text-[0.75rem] text-[var(--kf-muted)]">{o.n}</span>
          </button>
        );
      })}
    </div>
  );
}

function ZoomIcon({ small }: { small?: boolean }) {
  return (
    <svg viewBox="0 0 16 16" className={small ? "h-3 w-3 text-[var(--kf-faint)]" : "h-4 w-4 text-[var(--kf-muted)]"} aria-hidden="true">
      <rect x="2" y="2" width="5" height="5" rx="1" fill="currentColor" />
      <rect x="9" y="2" width="5" height="5" rx="1" fill="currentColor" />
      <rect x="2" y="9" width="5" height="5" rx="1" fill="currentColor" />
      <rect x="9" y="9" width="5" height="5" rx="1" fill="currentColor" />
    </svg>
  );
}

export { VerdictTally };

const GAP = 12;

const GRID_KEYS: ShortcutGroup[] = [
  { title: "Move", keys: [[["←", "→", "↑", "↓"], "Move between groups"], [["Home", "End"], "First / last group"], [["↵"], "Open the group"]] },
  { title: "View", keys: [[["1"], "All frames"], [["2"], "Keep only"], [["3"], "Review only"], [["4"], "Reject only"], [["+", "−"], "Bigger / smaller cards"], [["?"], "This sheet"]] },
];

/** Windowed rendering for big events: only the card rows near the viewport are in the DOM (300+ groups stay
 *  smooth). Small events render everything, so nothing changes for them. */
function useGridWindow(ref: React.RefObject<HTMLOListElement | null>, n: number, minW: number, gap: number) {
  const VIRTUAL_FROM = 60;
  const OVERSCAN = 2;
  const [width, setWidth] = useState(0);
  const [rowH, setRowH] = useState(0);
  const [range, setRange] = useState<[number, number]>([0, 8]);
  const virtual = n > VIRTUAL_FROM;
  const cols = width ? Math.max(1, Math.floor((width + gap) / (Math.min(width, minW) + gap))) : 1;
  const rows = Math.ceil(n / cols);

  useLayoutEffect(() => {
    const el = ref.current?.parentElement;
    if (!el) return;
    const ro = new ResizeObserver(() => setWidth(el.clientWidth));
    ro.observe(el);
    setWidth(el.clientWidth);
    return () => ro.disconnect();
  }, [ref]);

  const measureRO = useRef<ResizeObserver | null>(null);
  const measure = useCallback((li: HTMLLIElement | null) => {
    measureRO.current?.disconnect();
    if (!li) return;
    measureRO.current = new ResizeObserver(() => setRowH(li.offsetHeight + gap));
    measureRO.current.observe(li);
  }, [gap]);

  const update = useCallback(() => {
    const el = ref.current;
    if (!el || !virtual) return;
    const h = rowH || 360;
    const top = el.getBoundingClientRect().top;
    const a = Math.max(0, Math.floor(-top / h) - OVERSCAN);
    const b = Math.min(rows, Math.ceil((window.innerHeight - top) / h) + OVERSCAN);
    setRange((r) => (r[0] === a && r[1] === b ? r : [a, b]));
  }, [ref, virtual, rowH, rows]);

  useEffect(() => {
    if (!virtual) return;
    update();
    let raf = 0;
    const on = () => {
      cancelAnimationFrame(raf);
      raf = requestAnimationFrame(update);
    };
    window.addEventListener("scroll", on, { capture: true, passive: true });
    window.addEventListener("resize", on);
    return () => {
      cancelAnimationFrame(raf);
      window.removeEventListener("scroll", on, { capture: true });
      window.removeEventListener("resize", on);
    };
  }, [virtual, update]);

  /** Scroll so card i is rendered (keyboard moves past the window). */
  const reveal = useCallback(
    (i: number) => {
      const el = ref.current;
      if (!el) return;
      const h = rowH || 360;
      const y = el.getBoundingClientRect().top + window.scrollY + Math.floor(i / cols) * h - window.innerHeight / 3;
      window.scrollTo({ top: Math.max(0, y) });
      update();
    },
    [ref, rowH, cols, update],
  );

  if (!virtual) return { virtual, cols, from: 0, to: n, padTop: 0, padBottom: 0, measure, reveal };
  const [a, b] = range;
  const h = rowH || 360;
  return { virtual, cols, from: a * cols, to: Math.min(n, b * cols), padTop: a * h, padBottom: Math.max(0, rows - b) * h, measure, reveal };
}
