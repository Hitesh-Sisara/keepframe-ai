"use client";

import { useEffect, useRef } from "react";
import { PICK_WORD, type Pick } from "@/lib/picks";
import { plainReason } from "@/lib/cull";
import type { Member } from "@/lib/types";
import { Frame } from "../Frame";
import { VerdictGlyph } from "../VerdictMark";
import { Viewer, type ZoomState } from "./Viewer";

export type Layout = 1 | 2 | 4;

/** Full-screen cull view: black surround, the frame(s) as large as they go, a filmstrip at the bottom. Every
 *  control is a key (see the ? sheet); the buttons are there for the mouse and for discoverability. */
export function CullMode({
  groupLabel,
  frames,
  panes,
  focusId,
  onFocus,
  names,
  sizes,
  onSize,
  zoom,
  onZoom,
  overlay,
  loupe,
  layout,
  picks,
  stamp,
  autoAdvance,
  canUndo,
  canRedo,
  done,
  hasNextGroup,
  live,
  readOnly,
  on,
}: {
  /** Judge view: look only, so no pick buttons or keys. */
  readOnly?: boolean;
  groupLabel: string;
  frames: Member[];
  panes: Member[];
  focusId: string;
  onFocus: (id: string) => void;
  names: Record<string, string>;
  sizes: Record<string, [number, number]>;
  onSize: (id: string, w: number, h: number) => void;
  zoom: ZoomState;
  onZoom: (z: ZoomState) => void;
  overlay: boolean;
  loupe: boolean;
  layout: Layout;
  picks: Record<string, Pick>;
  stamp?: { id: string; pick: Pick; n: number };
  autoAdvance: boolean;
  canUndo: boolean;
  canRedo: boolean;
  done: boolean;
  hasNextGroup: boolean;
  live: string;
  on: {
    close: () => void;
    pick: (p: Pick) => void;
    undo: () => void;
    redo: () => void;
    layout: (l: Layout) => void;
    overlay: () => void;
    loupe: () => void;
    zoom: () => void;
    autoAdvance: () => void;
    nextGroup: () => void;
    keys: () => void;
  };
}) {
  const root = useRef<HTMLDivElement>(null);
  const strip = useRef<HTMLOListElement>(null);
  useEffect(() => {
    const back = document.activeElement as HTMLElement | null;
    root.current?.focus();
    const prev = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.body.style.overflow = prev;
      back?.focus?.();
    };
  }, []);
  useEffect(() => {
    const el = strip.current?.querySelector<HTMLElement>(`[data-id="${focusId}"]`);
    el?.scrollIntoView({ block: "nearest", inline: "center", behavior: matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth" });
  }, [focusId]);
  const i = frames.findIndex((m) => m.photo_id === focusId);
  const focus = frames[i];
  const picked = frames.filter((m) => picks[m.photo_id]).length;
  const agree = frames.filter((m) => picks[m.photo_id] && m.verdict && picks[m.photo_id] === m.verdict).length;
  const mine = picks[focusId];

  return (
    <div ref={root} role="dialog" aria-modal="true" aria-label={`Cull view, ${groupLabel}`} tabIndex={-1} className="kf-fade fixed inset-0 z-[70] flex flex-col bg-black text-white outline-none">
      <p className="sr-only" role="status" aria-live="polite">
        {live}
      </p>
      {/* Top bar */}
      <div className="flex h-12 shrink-0 items-center gap-3 border-b border-white/10 px-4">
        <span className="text-[0.875rem] font-semibold">{groupLabel}</span>
        <span className="t-data text-[0.8125rem] text-white/75">
          {names[focusId]}/{frames.length}
        </span>
        {!readOnly && (
          <span className="t-data hidden text-[0.8125rem] text-white/80 md:inline" title={picked > 0 ? `${agree} agree with the AI` : undefined}>
            Picked {picked}/{frames.length}
          </span>
        )}
        <div className="ml-auto flex items-center gap-1.5">
          <Seg value={layout} onChange={on.layout} />
          <Tog on={overlay} onClick={on.overlay} label="Why flagged" k="I" />
          <Tog on={loupe} onClick={on.loupe} label="Loupe" k="L" />
          <Tog on={zoom.z > 1} onClick={on.zoom} label="Zoom" k="Z" />
          {!readOnly && <Tog on={autoAdvance} onClick={on.autoAdvance} label="Auto-advance" k="A" />}
          <button type="button" onClick={on.keys} aria-label="Keyboard shortcuts" className="grid h-7 w-7 place-items-center rounded-[6px] text-[0.8125rem] font-semibold text-white/90 ring-1 ring-white/25 ring-inset hover:text-white focus-visible:outline-2 focus-visible:outline-white">
            ?
          </button>
          <button type="button" onClick={on.close} className="ml-1 inline-flex h-7 items-center gap-1.5 rounded-[6px] bg-white/10 px-2.5 text-[0.75rem] font-medium hover:bg-white/20 focus-visible:outline-2 focus-visible:outline-white">
            Exit <span className="text-white/65">Esc</span>
          </button>
        </div>
      </div>

      {/* Stage */}
      <div className="relative min-h-0 flex-1 px-4 py-3">
        <Viewer panes={panes} focusId={focusId} onFocus={onFocus} zoom={zoom} onZoom={onZoom} overlay={overlay} loupe={loupe} names={names} sizes={sizes} onSize={onSize} picks={picks} stamp={stamp} />
        {done && !readOnly && (
          <div className="kf-pop absolute inset-x-0 bottom-6 mx-auto flex w-max items-center gap-3 rounded-full bg-[#17191e]/95 py-2 pr-2 pl-4 text-[0.875rem] shadow-2xl ring-1 ring-white/15">
            <VerdictGlyph verdict="keep" className="h-4 w-4 text-[#4cc38a]" />
            All picked
            {hasNextGroup ? (
              <button type="button" onClick={on.nextGroup} className="inline-flex h-8 items-center gap-1.5 rounded-full bg-[#2457d6] px-3.5 font-semibold hover:bg-[#1d47b3] focus-visible:outline-2 focus-visible:outline-white">
                Next group <kbd className="!border-white/30 !bg-white/15 !text-white !shadow-none">↵</kbd>
              </button>
            ) : (
              <span className="pr-2 text-white/75">Last group</span>
            )}
          </div>
        )}
      </div>

      {/* Verdict bar */}
      {!readOnly && (
      <div className="flex shrink-0 flex-wrap items-center justify-center gap-2 border-t border-white/10 px-4 py-2">
        {(["keep", "reject", "review"] as const).map((p) => (
          <PickBtn key={p} p={p} on={mine === p} onClick={() => on.pick(p)} />
        ))}
        <span className="mx-2 h-5 w-px bg-white/15" aria-hidden="true" />
        <button type="button" onClick={on.undo} disabled={!canUndo} className="inline-flex h-8 items-center gap-1.5 rounded-[7px] px-2.5 text-[0.8125rem] text-white/90 ring-1 ring-white/20 ring-inset hover:text-white focus-visible:outline-2 focus-visible:outline-white disabled:opacity-40">
          Undo <kbd className="!border-white/25 !bg-white/10 !text-white !shadow-none">⌘Z</kbd>
        </button>
        <button type="button" onClick={on.redo} disabled={!canRedo} className="inline-flex h-8 items-center gap-1.5 rounded-[7px] px-2.5 text-[0.8125rem] text-white/90 ring-1 ring-white/20 ring-inset hover:text-white focus-visible:outline-2 focus-visible:outline-white disabled:opacity-40">
          Redo
        </button>
        <span className="ml-3 hidden text-[0.75rem] text-white/80 lg:inline">
          {focus && <span className="font-semibold text-white/90">{aiLine(focus)}</span>}
        </span>
      </div>
      )}

      {/* Filmstrip */}
      <ol ref={strip} className="flex h-[136px] shrink-0 items-start gap-2 overflow-x-auto border-t border-white/10 bg-[#0c0d10] px-4 pt-2.5 [scrollbar-color:#4a4f59_transparent]" aria-label="Filmstrip">
        {frames.map((m) => {
          const cur = m.photo_id === focusId;
          const inView = panes.some((p) => p.photo_id === m.photo_id);
          const pk = picks[m.photo_id];
          return (
            <li key={m.photo_id} data-id={m.photo_id} className="w-[148px] shrink-0">
              <button
                type="button"
                onClick={() => onFocus(m.photo_id)}
                aria-current={cur ? "true" : undefined}
                aria-label={`${names[m.photo_id]}${m.is_best ? ", AI pick" : ""}${pk ? `, you: ${PICK_WORD[pk]}` : ""}`}
                className={`block w-full rounded-[7px] p-[2px] focus-visible:outline-2 focus-visible:outline-white ${cur ? "bg-[#6ea8ff]/20 ring-2 ring-[#6ea8ff]" : inView ? "ring-1 ring-white/40" : "hover:bg-white/10"}`}
              >
                {/* Once you pick, your pick is the frame's badge (and its outline); the AI's verdict moves to the caption. */}
                <Frame src={m.proxy_url} alt="" verdict={pk ?? m.verdict} best={!pk && m.is_best} markSize="xs" aspect="3 / 2" ring={false} className="!rounded-[5px] !bg-[#22252b]" style={pk ? { boxShadow: `0 0 0 2px ${PICK_BG[pk]}` } : undefined} />
                <span className="t-data mt-1 flex items-center justify-between gap-1 px-0.5 text-[0.6875rem] leading-4 whitespace-nowrap">
                  <span className={cur ? "font-semibold" : "text-white/80"}>{names[m.photo_id]}</span>
                  {pk ? (
                    <span key={pk} className="kf-pop font-semibold" style={{ color: PICK_TEXT[pk] }} title={m.verdict && pk !== m.verdict ? `The AI said ${m.is_best ? "AI pick" : m.verdict}` : undefined}>
                      You: {PICK_WORD[pk]}
                      {m.verdict && pk !== m.verdict && <span className="ml-0.5 text-white/60">≠ AI</span>}
                    </span>
                  ) : m.is_best ? (
                    <span className="text-[#f6c343]">AI pick</span>
                  ) : null}
                </span>
              </button>
            </li>
          );
        })}
      </ol>
    </div>
  );
}

const PICK_BG: Record<Pick, string> = { keep: "#1f8a57", review: "#a86200", reject: "#c4313a" };
const PICK_TEXT: Record<Pick, string> = { keep: "#4cc38a", review: "#f0a43a", reject: "#ff8080" };
const PICK_KEY: Record<Pick, string> = { keep: "K", reject: "X", review: "U" };

/** "AI: Reject · main subject's eyes closed" for the verdict bar. */
function aiLine(m: Member): string {
  const r = plainReason(m.reason_text).replace(/\.$/, "");
  const v = m.is_best ? "AI pick" : m.verdict ? `AI: ${m.verdict === "review" ? "Review" : m.verdict === "keep" ? "Keep" : "Reject"}` : "";
  return [v, r].filter(Boolean).join(" · ");
}

function PickBtn({ p, on, onClick }: { p: Pick; on: boolean; onClick: () => void }) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-pressed={on}
      className={`inline-flex h-8 items-center gap-1.5 rounded-[7px] px-3 text-[0.8125rem] font-semibold transition-[background-color,box-shadow,transform] active:scale-[0.97] focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-white motion-reduce:active:scale-100 ${
        on ? "text-white shadow-[0_0_0_1.5px_rgba(255,255,255,0.85)]" : "text-white/90 ring-1 ring-white/25 ring-inset hover:bg-white/10"
      }`}
      style={on ? { background: PICK_BG[p] } : undefined}
    >
      <VerdictGlyph verdict={p} className="h-3.5 w-3.5" />
      {PICK_WORD[p]}
      <kbd className="!border-white/25 !bg-white/10 !text-white !shadow-none">{PICK_KEY[p]}</kbd>
    </button>
  );
}

export function Tog({ on, onClick, label, k, disabled }: { on: boolean; onClick: () => void; label: string; k: string; disabled?: boolean }) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-pressed={on}
      disabled={disabled}
      title={`${label} (${k})`}
      className={`inline-flex h-7 items-center gap-1.5 rounded-[6px] px-2 text-[0.75rem] font-medium ring-1 ring-inset focus-visible:outline-2 focus-visible:outline-white disabled:opacity-40 ${
        on ? "bg-[#2457d6] text-white ring-[#6ea8ff]" : "text-white/90 ring-white/25 hover:text-white hover:ring-white/40"
      }`}
    >
      {label}
      <span className="t-data rounded-[3px] bg-white/15 px-1 text-[0.6875rem]">{k}</span>
    </button>
  );
}

export function Seg({ value, onChange }: { value: Layout; onChange: (l: Layout) => void }) {
  const opts: [Layout, string][] = [
    [1, "1-up"],
    [2, "2-up"],
    [4, "4-up"],
  ];
  return (
    <div role="radiogroup" aria-label="Compare layout (C)" className="inline-flex rounded-[7px] bg-white/5 p-[2px] ring-1 ring-white/20 ring-inset">
      {opts.map(([v, l]) => (
        <button
          key={v}
          type="button"
          role="radio"
          aria-checked={value === v}
          onClick={() => onChange(v)}
          title={`${l} (${v}, or C to cycle)`}
          className={`inline-flex h-6 items-center gap-1 rounded-[5px] px-1.5 text-[0.75rem] font-medium focus-visible:outline-2 focus-visible:outline-white ${value === v ? "bg-white/90 text-[#14171c]" : "text-white/85 hover:text-white"}`}
        >
          <LayoutGlyph n={v} />
          <span className="sr-only sm:not-sr-only">{l}</span>
        </button>
      ))}
    </div>
  );
}

function LayoutGlyph({ n }: { n: Layout }) {
  return (
    <svg viewBox="0 0 16 12" className="h-3 w-4" aria-hidden="true" fill="currentColor">
      {n === 1 && <rect x="1" y="1" width="14" height="10" rx="1.5" />}
      {n === 2 && (
        <>
          <rect x="1" y="1" width="6.5" height="10" rx="1.2" />
          <rect x="8.5" y="1" width="6.5" height="10" rx="1.2" />
        </>
      )}
      {n === 4 && (
        <>
          <rect x="1" y="1" width="6.5" height="4.5" rx="1" />
          <rect x="8.5" y="1" width="6.5" height="4.5" rx="1" />
          <rect x="1" y="6.5" width="6.5" height="4.5" rx="1" />
          <rect x="8.5" y="6.5" width="6.5" height="4.5" rx="1" />
        </>
      )}
    </svg>
  );
}
