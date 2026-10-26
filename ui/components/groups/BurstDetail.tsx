"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useApi } from "@/lib/api";
import { frameNames, frameOrder, memberMatches, normalizeScores, sortGroups, type VerdictFilter } from "@/lib/cull";
import { nextUnpicked, PICK_WORD, type Pick } from "@/lib/picks";
import { cardReason, confidenceOf } from "@/lib/cardreason";
import { useSession } from "@/lib/session";
import type { Group, Member, Run, TracePage, Verdict } from "@/lib/types";

const ACTIVE = new Set(["starting", "running", "awaiting_approval"]);
const NOT_REACHED = "The agent didn\u2019t reach this burst in this run. The verdict came from scoring alone.";
import { ErrorNote } from "../States";
import { Frame, ImageRefreshContext } from "../Frame";
import { TraceChain } from "../TraceChain";
import { VERDICT_COLOR, VerdictGlyph } from "../VerdictMark";
import { routes } from "@/lib/routes";

import { Breadcrumbs, Button, READ_ONLY_REASON, SegmentedControl, Skeleton, Tooltip } from "../ui";
import { ConfidenceChip } from "./BurstGrid";
import { CullMode, Seg, Tog, type Layout } from "./CullMode";
import { FaceStrip } from "./FaceStrip";
import { ShortcutButton, ShortcutSheet, type ShortcutGroup } from "./Shortcuts";
import { TraceStory } from "./TraceStory";
import { usePicks } from "./usePicks";
import { FIT, Viewer, VIEWER_CSS, type ZoomState } from "./Viewer";
import { useEventGroups, useExpiryRefresh, useGroupDetail } from "./data";
import { Inspector } from "./Inspector";
import { bestAvailableFace, matchFaces } from "@/lib/facestrip";

/** One burst: large preview + filmstrip + inspector, then the agent's trace as a timeline. */
export function BurstDetail({ eventId, groupId, runParam }: { eventId: string; groupId: string; runParam?: string }) {
  const detail = useGroupDetail(eventId, groupId);
  const all = useEventGroups(eventId);
  const group = detail.data?.items[0];
  const runId = runParam ?? group?.run_id;
  const trace = useApi<TracePage>(runId && groupId ? `/runs/${encodeURIComponent(runId)}/trace` : null, { group_id: groupId, limit: 500 }, 10000);
  const run = useApi<Run>(runId ? `/runs/${encodeURIComponent(runId)}` : null);

  const refresh = detail.refresh;
  useExpiryRefresh(group?.members.map((m) => m.proxy_url) ?? [], refresh);

  if (detail.error) {
    return (
      <>
        <Crumbs eventId={eventId} eventName={undefined} label="Group" />
        <ErrorNote error={detail.error} retry={() => void detail.refresh()} />
      </>
    );
  }
  if (!detail.data || !group) return <DetailSkeleton />;
  return (
    <ImageRefreshContext.Provider value={refresh}>
      <DetailBody
        eventId={eventId}
        eventName={detail.data.event.name ?? detail.data.event.label}
        group={group}
        siblings={all.data?.groups}
        runId={runId}
        trace={trace}
        runActive={run.data ? ACTIVE.has(run.data.status) : true}
      />
    </ImageRefreshContext.Provider>
  );
}

function DetailBody({
  eventId,
  eventName,
  group,
  siblings,
  runId,
  trace,
  runActive,
}: {
  eventId: string;
  eventName?: string;
  group: Group;
  siblings?: Group[];
  runId?: string;
  trace: ReturnType<typeof useApi<TracePage>>;
  /** The run can still add steps (unknown counts as active, so nothing is claimed early). */
  runActive: boolean;
}) {
  const router = useRouter();
  const session = useSession();
  const judge = session?.role === "judge";
  // Capture order when the ids carry it (team), else score rank (judge aliases); the strip says which.
  const ordered = useMemo(() => frameOrder(group.members), [group.members]);
  const frames = ordered.frames;
  const names = useMemo(() => frameNames(group.members), [group.members]);
  const nameOf = (id?: string) => (id ? (names[id] ?? "a frame") : "");
  const [filter, setFilter] = useState<VerdictFilter>("all");
  const visible = useMemo(() => frames.filter((m) => memberMatches(m, filter, "")), [frames, filter]);

  // Selected frame: ?frame= in the URL (read from window.location; replaceState is invisible to useSearchParams).
  const [selected, setSelected] = useState<string | undefined>(undefined);
  useEffect(() => {
    const f = new URLSearchParams(window.location.search).get("frame");
    setSelected(f && frames.some((m) => m.photo_id === f) ? f : (frames.find((m) => m.is_best) ?? frames[0])?.photo_id);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [group.group_id]);
  const select = useCallback((id: string) => {
    setSelected(id);
    const u = new URL(window.location.href);
    u.searchParams.set("frame", id);
    window.history.replaceState(window.history.state, "", u);
  }, []);
  const focus = frames.find((m) => m.photo_id === selected) ?? frames.find((m) => m.is_best) ?? frames[0];
  const scores = useMemo(() => normalizeScores(focus?.scores as Record<string, unknown> | undefined, focus?.breakdown), [focus]);
  const [proxySize, setProxySize] = useState<Record<string, [number, number]>>({});
  const size = focus ? proxySize[focus.photo_id] : undefined;
  const onSize = useCallback((id: string, w: number, h: number) => setProxySize((p) => (p[id]?.[0] === w ? p : { ...p, [id]: [w, h] })), []);

  // Viewer state, shared by the inline preview and the full-screen cull view.
  const [zoom, setZoom] = useState<ZoomState>(FIT);
  const [overlay, setOverlay] = useState(false);
  const [loupe, setLoupe] = useState(false);
  const [layout, setLayout] = useState<Layout>(1);
  const [cull, setCull] = useState(false);
  const [keys, setKeys] = useState(false);
  const [autoAdvance, setAutoAdvance] = useState(true);
  useEffect(() => setAutoAdvance(window.localStorage.getItem("keepframe.cull.advance") !== "off"), []);
  const toggleAdvance = useCallback(() => {
    setAutoAdvance((a) => {
      window.localStorage.setItem("keepframe.cull.advance", a ? "off" : "on");
      return !a;
    });
  }, []);
  const [stamp, setStamp] = useState<{ id: string; pick: Pick; n: number }>();
  const [live, setLive] = useState("");
  const [hot, setHot] = useState<string>();
  const [raw, setRaw] = useState(false);

  const picksApi = usePicks(eventId, (id) => {
    select(id);
    setLive(`Undone on ${nameOf(id)}`);
  });
  const { picks } = picksApi;

  // Compare panes: the focused frame plus its neighbours (2-up: focus + the AI pick or next frame; 4-up: a window).
  const panes = useMemo(() => {
    if (!focus) return [];
    if (layout === 1) return [focus];
    const i = frames.indexOf(focus);
    if (layout === 2) {
      const best = frames.find((m) => m.is_best);
      const other = best && best !== focus ? best : (frames[i + 1] ?? frames[i - 1]);
      return other ? [focus, other].sort((a, b) => frames.indexOf(a) - frames.indexOf(b)) : [focus];
    }
    const start = Math.max(0, Math.min(i - 1, frames.length - 4));
    return frames.slice(start, start + 4);
  }, [layout, focus, frames]);

  // Group navigation in capture order.
  const order = useMemo(() => (siblings ? sortGroups(siblings, "capture") : []), [siblings]);
  const at = order.findIndex((g) => g.group_id === group.group_id);
  const prev = at > 0 ? order[at - 1] : undefined;
  const next = at >= 0 && at < order.length - 1 ? order[at + 1] : undefined;
  const hrefFor = (g: Group) => routes.group(eventId, g.group_id, g.run_id);
  const gridHref = routes.groups(eventId);
  const goGroup = useCallback(
    (g: Group | undefined, keepCull: boolean) => {
      if (!g) return;
      if (keepCull) window.sessionStorage.setItem("keepframe.cull.resume", "1");
      router.push(hrefFor(g));
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [router, eventId],
  );
  // Arriving from "Next group" inside the cull view: reopen it.
  useEffect(() => {
    if (window.sessionStorage.getItem("keepframe.cull.resume")) {
      window.sessionStorage.removeItem("keepframe.cull.resume");
      setCull(true);
    }
  }, []);

  const strip = useRef<HTMLOListElement>(null);
  const move = useCallback(
    (d: number) => {
      const list = visible.length ? visible : frames;
      const i = Math.max(0, list.findIndex((m) => m.photo_id === focus?.photo_id));
      const n = list[Math.max(0, Math.min(list.length - 1, i + d))];
      if (n) select(n.photo_id);
    },
    [visible, frames, focus, select],
  );

  const doneBurst = frames.length > 0 && frames.every((m) => picks[m.photo_id]);
  const pick = useCallback(
    (p: Pick) => {
      if (!focus) return;
      const id = focus.photo_id;
      const after = picksApi.set(id, p);
      setStamp((s) => ({ id, pick: p, n: (s?.n ?? 0) + 1 }));
      const order = frames.map((m) => m.photo_id);
      const allDone = order.every((o) => after.picks[o]);
      // Auto-advance to the next frame still without a pick; once the burst is done, stay and offer the next group.
      const nxt = autoAdvance && !allDone ? nextUnpicked(order, id, after.picks) : undefined;
      setLive(`${nameOf(id)}: ${PICK_WORD[p]}${allDone ? ". Every frame is picked; press Enter for the next group" : nxt && nxt !== id ? `. Now on ${nameOf(nxt)}` : ""}`);
      if (nxt && nxt !== id) window.setTimeout(() => select(nxt), 260);
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [focus, frames, autoAdvance, picksApi.set, select],
  );

  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      const t = e.target as HTMLElement | null;
      if (keys) return;
      if ((e.metaKey || e.ctrlKey) && (e.key === "z" || e.key === "Z")) {
        if (!cull) return;
        e.preventDefault();
        if (e.shiftKey) picksApi.redo();
        else picksApi.undo();
        return;
      }
      if (e.metaKey || e.ctrlKey || e.altKey || (t && /^(INPUT|TEXTAREA|SELECT)$/.test(t.tagName)) || t?.isContentEditable) return;
      if (!cull && t?.closest("[role=radiogroup]") && (e.key === "ArrowLeft" || e.key === "ArrowRight")) return;
      const k = e.key;
      const lower = k.length === 1 ? k.toLowerCase() : k;
      if (k === "ArrowRight" || k === "ArrowLeft") {
        e.preventDefault();
        if (e.shiftKey) goGroup(k === "ArrowRight" ? next : prev, cull);
        else move(k === "ArrowRight" ? 1 : -1);
      } else if (k === "Home") move(-999);
      else if (k === "End") move(999);
      else if (k === "?") {
        e.preventDefault();
        setKeys(true);
      } else if (lower === "f") setCull((c) => !c);
      else if (lower === "z") setZoom((z) => (z.z > 1 ? { ...z, z: 1 } : { z: 2.5, u: z.u, v: z.v }));
      else if (lower === "i") setOverlay((o) => !o);
      else if (lower === "l") setLoupe((l) => !l);
      else if (lower === "c") setLayout((l) => (l === 1 ? 2 : l === 2 ? 4 : 1));
      else if (lower === "a" && cull) toggleAdvance();
      else if (lower === "b") {
        const b = frames.find((m) => m.is_best);
        if (b) select(b.photo_id);
      } else if (cull && judge && ["k", "p", "x", "u"].includes(lower)) return;
      else if (cull && (lower === "k" || lower === "p")) pick("keep");
      else if (cull && lower === "x") pick("reject");
      else if (cull && lower === "u") pick("review");
      else if (cull && (k === "Backspace" || k === "Delete") && focus) picksApi.clear(focus.photo_id);
      else if (cull && k === "Enter" && doneBurst && next) goGroup(next, true);
      else if (k === "Escape") {
        if (zoom.z > 1) setZoom(FIT);
        else if (cull) setCull(false);
        else if (layout !== 1) setLayout(1);
        else router.push(gridHref);
      } else if (k === "]") goGroup(next, cull);
      else if (k === "[") goGroup(prev, cull);
      else if (!cull && e.shiftKey && /^Digit[1-4]$/.test(e.code)) setFilter((["all", "keep", "review", "reject"] as const)[Number(e.code.slice(5)) - 1]);
      else if (k === "1") setLayout(1);
      else if (k === "2") setLayout(2);
      else if (k === "4") setLayout(4);
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [move, next, prev, zoom, frames, select, cull, keys, layout, pick, doneBurst, focus, toggleAdvance, judge]);

  // Keep the selected thumbnail in view.
  useEffect(() => {
    const box = strip.current;
    const el = box?.querySelector<HTMLElement>(`[data-id="${focus?.photo_id}"]`);
    if (box && el) {
      const left = el.offsetLeft - box.clientWidth / 2 + el.clientWidth / 2;
      box.scrollTo({ left, behavior: matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth" });
    }
  }, [focus?.photo_id]);

  const counts = useMemo(() => {
    const c: Record<Verdict, number> = { keep: 0, review: 0, reject: 0 };
    for (const m of frames) if (m.verdict) c[m.verdict]++;
    return c;
  }, [frames]);
  // useApi's reload has no completion signal: resolve after a short wait so the frame retries with new links.
  const traceReload = trace.reload;
  const traceRefresh = useCallback(() => {
    traceReload();
    return new Promise<void>((r) => window.setTimeout(r, 1200));
  }, [traceReload]);
  const conf = confidenceOf(group);
  // Who answered this group's approval, from the verdicts (the trace line may not record it).
  const approvalSource = group.members.find((m) => m.decision_source && m.decision_source !== "agent")?.decision_source;
  const traceLines = useMemo(() => trace.data?.items.filter((l) => l.type !== "run_header" && l.type !== "run_end") ?? [], [trace.data]);
  const showRaw = useCallback((seq: number) => {
    setRaw(true);
    window.setTimeout(() => {
      const el = document.getElementById(`seq-${seq}`);
      el?.scrollIntoView({ block: "center", behavior: matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth" });
      el?.classList.add("kf-flash");
      window.setTimeout(() => el?.classList.remove("kf-flash"), 1600);
    }, 60);
  }, []);
  const hasFaces = scores.faces.some((f) => f.bbox_px);
  const bestIdx = frames.findIndex((m) => m.photo_id === (group.best_photo_id ?? frames.find((x) => x.is_best)?.photo_id));
  const bestAvail = useMemo(
    () => bestAvailableFace(matchFaces(frames.map((m) => normalizeScores(m.scores as Record<string, unknown> | undefined, m.breakdown).faces)), bestIdx),
    [frames, bestIdx],
  );

  if (!focus) return null;
  return (
    <>
      <style>{VIEWER_CSS + `.kf-flash{animation:kf-flash 1.6s ease-out}@keyframes kf-flash{0%,30%{background:var(--kf-accent-soft)}100%{background:transparent}}@media (prefers-reduced-motion:reduce){.kf-flash{animation:none;background:var(--kf-accent-soft)}}`}</style>
      {/* Header */}
      <div className="mb-3 flex flex-wrap items-end gap-x-8 gap-y-3">
        <div className="min-w-0 flex-1 basis-[22rem]">
          <Crumbs eventId={eventId} eventName={eventName} label={group.group_label ?? "Group"} />
          <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
            <h1 className="t-display">{group.group_label ?? "Group"}</h1>
            {group.pending_approval ? null : <ConfidenceChip c={conf} />}
          </div>
          <p className="mt-1 text-[0.875rem] text-[var(--kf-muted)]">
            {cardReason(group)} · {group.size} frames
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          {group.pending_approval &&
            (judge ? (
              <Button size="sm" variant="primary" disabledReason={READ_ONLY_REASON}>
                Decide this group
              </Button>
            ) : (
              <Link href={routes.approvals(group.group_id)} className="btn btn-primary btn-sm">
                Decide this group
              </Link>
            ))}
          <button type="button" onClick={() => setCull(true)} className="btn btn-primary btn-sm gap-1.5">
            <svg viewBox="0 0 16 16" className="h-3.5 w-3.5" aria-hidden="true">
              <path d="M2.5 6V2.5H6M10 2.5h3.5V6M13.5 10v3.5H10M6 13.5H2.5V10" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" />
            </svg>
            {judge ? "View full screen" : "Cull"}
            <kbd className="!border-white/30 !bg-white/15 !text-white !shadow-none">F</kbd>
          </button>
          <ShortcutButton onClick={() => setKeys(true)} />
          <div className="flex items-center gap-1" role="group" aria-label="Group navigation">
            <NavBtn href={prev ? hrefFor(prev) : undefined} label={prev ? `Previous group, ${prev.group_label ?? ""}` : "No previous group"} dir="prev" />
            <span className="t-data min-w-[52px] text-center text-[0.75rem] text-[var(--kf-muted)]" aria-label={at >= 0 ? `Group ${at + 1} of ${order.length}` : undefined}>
              {at >= 0 ? `${at + 1} of ${order.length}` : ""}
            </span>
            <NavBtn href={next ? hrefFor(next) : undefined} label={next ? `Next group, ${next.group_label ?? ""}` : "No next group"} dir="next" />
          </div>
        </div>
      </div>

      {/* Cull view */}
      <section aria-label="Frames" className={`grid gap-5 ${layout === 1 ? "lg:grid-cols-[minmax(0,1fr)_300px] xl:grid-cols-[minmax(0,1fr)_340px] 2xl:grid-cols-[minmax(0,1fr)_380px]" : ""}`}>
        <div className="min-w-0">
          <div className="overflow-hidden rounded-[var(--kf-radius-lg)] bg-[#111317] shadow-[var(--kf-shadow-lg)]">
            {/* Viewer toolbar */}
            <div className="flex flex-wrap items-center gap-1.5 border-b border-white/10 px-3 py-2">
              <Seg value={layout} onChange={setLayout} />
              <span className="mx-1 h-4 w-px bg-white/15" aria-hidden="true" />
              <Tog on={overlay} onClick={() => setOverlay((o) => !o)} label="Why flagged" k="I" />
              <Tog on={loupe} onClick={() => setLoupe((l) => !l)} label="Loupe" k="L" />
              <Tog on={zoom.z > 1} onClick={() => setZoom((z) => (z.z > 1 ? FIT : { z: 2.5, u: 0.5, v: 0.4 }))} label="Zoom" k="Z" />
              <span className="t-data ml-auto text-[0.75rem] text-white/80" aria-live="polite">
                {layout === 1 ? (
                  <>
                    {nameOf(focus.photo_id)}
                  </>
                ) : (
                  <>{panes.map((m) => nameOf(m.photo_id)).join(" vs ")}</>
                )}
                {zoom.z > 1 && <> · {Math.round(zoom.z * 100)}%</>}
              </span>
            </div>
            <div className={`relative px-3 pt-3 pb-2 ${layout === 1 ? "sm:px-12" : ""}`}>
              <div style={{ height: layout === 1 ? `clamp(260px, calc(100dvh - ${hasFaces ? 470 : 400}px), 720px)` : `clamp(320px, calc(100dvh - 300px), 820px)` }}>
                <Viewer
                  panes={panes}
                  focusId={focus.photo_id}
                  onFocus={select}
                  zoom={zoom}
                  onZoom={setZoom}
                  overlay={overlay}
                  loupe={loupe}
                  names={names}
                  sizes={proxySize}
                  onSize={onSize}
                  picks={picks}
                  stamp={stamp}
                />
              </div>
              {layout === 1 && (
                <>
                  <button type="button" onClick={() => move(-1)} disabled={frames.indexOf(focus) <= 0} aria-label="Previous frame" className="absolute top-1/2 left-2 hidden h-9 w-9 -translate-y-1/2 place-items-center rounded-full bg-black/45 text-white ring-1 ring-white/15 backdrop-blur transition-opacity hover:bg-black/65 focus-visible:outline-2 focus-visible:outline-white disabled:pointer-events-none disabled:opacity-25 sm:grid">
                    <Chevron dir="prev" />
                  </button>
                  <button type="button" onClick={() => move(1)} disabled={frames.indexOf(focus) >= frames.length - 1} aria-label="Next frame" className="absolute top-1/2 right-2 hidden h-9 w-9 -translate-y-1/2 place-items-center rounded-full bg-black/45 text-white ring-1 ring-white/15 backdrop-blur transition-opacity hover:bg-black/65 focus-visible:outline-2 focus-visible:outline-white disabled:pointer-events-none disabled:opacity-25 sm:grid">
                    <Chevron dir="next" />
                  </button>
                </>
              )}
            </div>
            {/* Filmstrip */}
            <div className="border-t border-white/10 bg-[#17191e] px-3 pt-2.5 pb-3">
              <div className="mb-2 flex flex-wrap items-center gap-2">
                <DarkFilter value={filter} counts={counts} total={frames.length} onChange={setFilter} />
                {Object.keys(picks).some((id) => frames.some((m) => m.photo_id === id)) && (
                  <span className="t-small ml-auto text-white/80">
                    Picked {frames.filter((m) => picks[m.photo_id]).length}/{frames.length}
                  </span>
                )}
              </div>
              <ol ref={strip} className="relative flex gap-2 overflow-x-auto pb-1 [scrollbar-color:#4a4f59_transparent]" aria-label={ordered.by === "capture" ? "Filmstrip, in capture order" : "Filmstrip, best score first"}>
                {frames.map((m) => {
                  const on = m.photo_id === focus.photo_id;
                  const inView = layout > 1 && panes.includes(m);
                  const dim = !visible.includes(m);
                  const pk = picks[m.photo_id];
                  return (
                    <li key={m.photo_id} data-id={m.photo_id} className="max-w-[184px] min-w-[92px] flex-1 basis-0">
                      <button
                        type="button"
                        onClick={() => select(m.photo_id)}
                        aria-current={on ? "true" : undefined}
                        aria-label={`${nameOf(m.photo_id)}${m.verdict ? `, ${m.verdict}` : ""}${m.is_best ? ", AI pick" : ""}${pk ? `, you: ${PICK_WORD[pk]}` : ""}`}
                        className={`block w-full rounded-[8px] p-[3px] transition-[box-shadow,opacity,transform] focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-white ${
                          on ? "bg-[#6ea8ff]/15 outline-2 -outline-offset-2 outline-[#6ea8ff]" : inView ? "bg-white/5 outline-1 -outline-offset-1 outline-white/35" : "hover:bg-white/10"
                        } ${dim ? "opacity-35" : ""} ${hot === m.photo_id ? "-translate-y-0.5 outline-2 outline-[#f6c343]" : ""}`}
                      >
                        <Frame src={m.proxy_url} alt="" verdict={m.verdict} best={m.is_best} markSize="sm" ring={false} dimReject={!on} className="!rounded-[6px] !bg-[#2a2e36]" aspect="3 / 2" onDark onSize={(w, h) => onSize(m.photo_id, w, h)} />
                        <span className={`t-data mt-1 flex items-center justify-center gap-1.5 px-1 pb-0.5 text-[0.75rem] ${on ? "font-semibold text-white" : "text-white/85"}`}>
                          <span className="shrink-0">{nameOf(m.photo_id)}</span>
                          {pk && (
                            <span key={pk} className="kf-pop inline-flex items-center gap-0.5 rounded-full bg-white/15 px-1.5 text-[0.6875rem] font-semibold text-white">
                              <VerdictGlyph verdict={pk} className="h-2.5 w-2.5" />
                              {PICK_WORD[pk]}
                            </span>
                          )}
                        </span>
                      </button>
                    </li>
                  );
                })}
              </ol>
            </div>
            <FaceStrip frames={frames} names={names} proxySizes={proxySize} selected={focus.photo_id} bestId={group.best_photo_id ?? frames.find((m) => m.is_best)?.photo_id} onSelect={select} />
          </div>
        </div>

        {layout === 1 && (
        <aside aria-label="Selected frame" className="kf-card self-start p-5 lg:sticky lg:top-4 lg:max-h-[calc(100dvh-2rem)] lg:overflow-y-auto lg:p-4 xl:p-5">
          <Inspector
            m={focus}
            scores={scores}
            proxySize={size}
            bestId={group.best_photo_id ? nameOf(group.best_photo_id) : undefined}
            margin={group.margin}
            provisional={group.pending_approval}
            bestAvailableFace={focus.is_best && bestAvail >= 0 ? bestAvail + 1 : undefined}
          />
        </aside>
        )}
      </section>

      {/* Trace */}
      <section id="trace" aria-labelledby="trace-h" className="mt-12 scroll-mt-6">
        <div className="mb-4 flex flex-wrap items-end justify-between gap-3">
          <h2 id="trace-h" className="t-title">
            How it decided
          </h2>
          <div className="flex items-center gap-3">
            {trace.data && (
              <SegmentedControl
                size="sm"
                label="Trace view"
                value={raw ? "raw" : "story"}
                onChange={(v) => setRaw(v === "raw")}
                options={[
                  { value: "story", label: "Story" },
                  { value: "raw", label: "Details" },
                ]}
              />
            )}
          </div>
        </div>
        {!runId && <p className="t-small text-[var(--kf-muted)]">No run yet.</p>}
        <ErrorNote error={trace.error} retry={trace.reload} />
        {runId && !trace.data && !trace.error && <TraceSkeleton />}
        {trace.data && (
          <div className="max-w-[880px]">
            <div className="kf-card min-w-0 px-4 py-3 sm:px-5">
              {raw ? (
                <ImageRefreshContext.Provider value={traceRefresh}>
                  <TraceChain lines={traceLines} pivots={trace.data.pivots} knownSource={approvalSource} names={names} margin={group.margin} emptyText={runActive ? undefined : NOT_REACHED} />
                </ImageRefreshContext.Provider>
              ) : (
                <TraceStory lines={traceLines} pivots={trace.data.pivots} names={names} hot={hot} onHot={setHot} onShowRaw={showRaw} emptyText={runActive ? undefined : NOT_REACHED} />
              )}
            </div>
          </div>
        )}
      </section>

      {cull && (
        <CullMode
          groupLabel={group.group_label ?? "Group"}
          frames={frames}
          panes={panes}
          focusId={focus.photo_id}
          onFocus={select}
          names={names}
          sizes={proxySize}
          onSize={onSize}
          zoom={zoom}
          onZoom={setZoom}
          overlay={overlay}
          loupe={loupe}
          layout={layout}
          picks={picks}
          stamp={stamp}
          autoAdvance={autoAdvance}
          canUndo={picksApi.canUndo}
          canRedo={picksApi.canRedo}
          done={doneBurst}
          hasNextGroup={Boolean(next)}
          live={live}
          readOnly={judge}
          on={{
            close: () => setCull(false),
            pick,
            undo: picksApi.undo,
            redo: picksApi.redo,
            layout: setLayout,
            overlay: () => setOverlay((o) => !o),
            loupe: () => setLoupe((l) => !l),
            zoom: () => setZoom((z) => (z.z > 1 ? FIT : { z: 2.5, u: 0.5, v: 0.4 })),
            autoAdvance: toggleAdvance,
            nextGroup: () => goGroup(next, true),
            keys: () => setKeys(true),
          }}
        />
      )}
      <ShortcutSheet open={keys} onClose={() => setKeys(false)} groups={judge ? JUDGE_KEYS : DETAIL_KEYS} note={judge ? undefined : "Picks save in this browser."} />
    </>
  );
}

const DETAIL_KEYS: ShortcutGroup[] = [
  { title: "Cull (full screen)", keys: [[["F"], "Open / close the cull view"], [["K"], "Keep (P works too)"], [["X"], "Reject"], [["U"], "Review (unsure)"], [["⌫"], "Clear your pick"], [["⌘", "Z"], "Undo"], [["⇧", "⌘", "Z"], "Redo"], [["A"], "Auto-advance on / off"], [["↵"], "Next group, when the burst is done"]] },
  { title: "Look", keys: [[["C"], "Compare: 1-up → 2-up → 4-up"], [["1", "2", "4"], "1-up, 2-up, 4-up"], [["Z"], "Zoom (synced across panes)"], [["L"], "Loupe at 100%"], [["I"], "Why it was flagged"], [["B"], "Jump to the AI pick"]] },
  { title: "Move", keys: [[["←", "→"], "Previous / next frame"], [["[", "]"], "Previous / next group"], [["Home", "End"], "First / last frame"], [["Esc"], "Back out (sheet, zoom, cull, compare, grid)"]] },
  { title: "Filter the filmstrip", keys: [[["⇧", "1"], "All frames"], [["⇧", "2"], "Keep"], [["⇧", "3"], "Review"], [["⇧", "4"], "Reject"]] },
  { title: "General", keys: [[["?"], "This sheet"]] },
];

// Judge view is read-only: the full-screen view opens, but the verdict keys (K/X/U, undo, auto-advance) are gone.
const JUDGE_KEYS: ShortcutGroup[] = [
  { title: "Full screen", keys: [[["F"], "Open / close"]] },
  ...DETAIL_KEYS.slice(1),
];

function DarkFilter({ value, counts, total, onChange }: { value: VerdictFilter; counts: Record<Verdict, number>; total: number; onChange: (v: VerdictFilter) => void }) {
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
    const i = opts.findIndex((o) => o.v === value);
    const n = opts[(i + d + opts.length) % opts.length];
    onChange(n.v);
    (e.currentTarget.querySelector(`[data-v="${n.v}"]`) as HTMLElement | null)?.focus();
  }
  return (
    <div role="radiogroup" aria-label="Show verdict" onKeyDown={onKey} className="inline-flex rounded-[8px] bg-black/30 p-[3px] ring-1 ring-inset ring-white/10">
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
            className={`inline-flex h-6 items-center gap-1.5 rounded-[5px] px-2 text-[0.75rem] font-medium focus-visible:outline-2 focus-visible:outline-white ${
              on ? "bg-white/90 text-[#14171c]" : "text-white/85 hover:text-white"
            }`}
          >
            {o.v !== "all" && (
              <span aria-hidden="true" className="grid h-3 w-3 place-items-center rounded-full text-white" style={{ background: VERDICT_COLOR[o.v] }}>
                <VerdictGlyph verdict={o.v} className="h-2 w-2" />
              </span>
            )}
            {o.label}
            <span className={`t-data ${on ? "text-[#5b6370]" : "text-white/80"}`}>{o.n}</span>
          </button>
        );
      })}
    </div>
  );
}

function NavBtn({ href, label, dir }: { href?: string; label: string; dir: "prev" | "next" }) {
  const cls = "btn btn-secondary btn-sm btn-icon !w-8";
  if (!href)
    return (
      <span className={`${cls} opacity-40`} aria-disabled="true" aria-label={label}>
        <Chevron dir={dir} />
      </span>
    );
  return (
    <Tooltip label={dir === "prev" ? "Previous group  [" : "Next group  ]"} side="bottom">
      <Link href={href} className={cls} aria-label={label}>
        <Chevron dir={dir} />
      </Link>
    </Tooltip>
  );
}

function Chevron({ dir }: { dir: "prev" | "next" }) {
  return (
    <svg viewBox="0 0 16 16" className="h-4 w-4" aria-hidden="true">
      <path d={dir === "prev" ? "M10 3.5L5.5 8 10 12.5" : "M6 3.5L10.5 8 6 12.5"} fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

function Crumbs({ eventId, eventName, label }: { eventId: string; eventName?: string; label: string }) {
  return (
    <Breadcrumbs
      crumbs={[
        { label: "Events", href: routes.home },
        { label: eventName ?? "Event", href: routes.event(eventId) },
        { label: "Groups", href: routes.groups(eventId) },
        { label },
      ]}
    />
  );
}

// ------------------------------------------------------------------ skeletons

function DetailSkeleton() {
  return (
    <div aria-busy="true">
      <span className="sr-only" role="status">
        Loading group…
      </span>
      <Skeleton className="h-3 w-48" />
      <Skeleton className="mt-2.5 h-7 w-64" />
      <div className="mt-5 grid gap-5 xl:grid-cols-[minmax(0,1fr)_360px]">
        <div className="rounded-[var(--kf-radius-lg)] bg-[#16181d] p-5">
          <div className="mx-auto aspect-[3/2] max-h-[60vh] w-full rounded-md bg-white/5" />
          <div className="mt-4 flex gap-2">
            {Array.from({ length: 6 }, (_, i) => (
              <div key={i} className="aspect-[3/2] w-28 rounded-md bg-white/5" />
            ))}
          </div>
        </div>
        <div className="kf-card p-5">
          <Skeleton className="h-7 w-28 !rounded-full" />
          <Skeleton className="mt-4 h-4 w-4/5" />
          <Skeleton className="mt-2 h-3 w-3/5" />
          {Array.from({ length: 4 }, (_, i) => (
            <div key={i} className="mt-5">
              <Skeleton className="h-3 w-24" />
              <Skeleton className="mt-2 h-1.5 w-full" />
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

function TraceSkeleton() {
  return (
    <div className="kf-card p-4" aria-busy="true">
      <span className="sr-only" role="status">
        Loading trace…
      </span>
      {Array.from({ length: 6 }, (_, i) => (
        <div key={i} className="flex gap-3 py-2.5">
          <Skeleton className="h-7 w-7 !rounded-full" />
          <div className="flex-1">
            <Skeleton className="h-3.5 w-40" />
            <Skeleton className="mt-2 h-3 w-3/4" />
          </div>
        </div>
      ))}
    </div>
  );
}
