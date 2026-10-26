"use client";

import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { Suspense, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Decision, DecisionSkeleton } from "@/components/approvals/Decision";
import { burstLabel, Inbox, InboxSkeleton } from "@/components/approvals/Inbox";
import { ANSWERED_WORD, type ApprovalVM } from "@/components/approvals/logic";
import s from "@/components/approvals/approvals.module.css";
import { useDesk, type AnsweredVM } from "@/components/approvals/useDesk";
import { ErrorNote } from "@/components/States";
import { useSession } from "@/lib/session";

type Tab = "waiting" | "answered";

export default function ApprovalsPage() {
  const session = useSession();
  if (!session) return null;
  return (
    <Suspense fallback={null}>
      <Desk role={session.role} />
    </Suspense>
  );
}

function Desk({ role }: { role: "team" | "judge" }) {
  const readOnly = role !== "team";
  const desk = useDesk(role);
  const params = useSearchParams();
  const wantGroup = params.get("group");
  const [tab, setTab] = useState<Tab>("waiting");
  const [activeId, setActiveId] = useState<string>();
  // ?group=<id> (from a group page's "Decide this group") opens that group's request once the lists load.
  const deepLinked = useRef(false);
  useEffect(() => {
    if (!wantGroup || deepLinked.current || !desk.waiting || !desk.answered) return;
    deepLinked.current = true;
    const w = desk.waiting.find((a) => a.groupId === wantGroup);
    const h = desk.answered.find((a) => a.groupId === wantGroup);
    if (w) setActiveId(w.id);
    else if (h) {
      setTab("answered");
      setActiveId(h.id);
    }
  }, [wantGroup, desk.waiting, desk.answered]);
  // Never open on an empty queue: with nothing waiting on first load, start on the answered history.
  const tabChosen = useRef(false);
  useEffect(() => {
    if (tabChosen.current || wantGroup || !desk.waiting || !desk.answered) return;
    tabChosen.current = true;
    if (desk.waiting.length === 0 && desk.answered.length > 0) setTab("answered");
  }, [desk.waiting, desk.answered, wantGroup]);
  // Answered in this tab session: hidden from the list once the success state has advanced.
  const [answered, setAnswered] = useState<Set<string>>(new Set());
  const [gone, setGone] = useState<Set<string>>(new Set());
  const [now, setNow] = useState(() => Date.now());

  useEffect(() => {
    const id = window.setInterval(() => setNow(Date.now()), 30000);
    return () => window.clearInterval(id);
  }, []);

  const waiting = useMemo(() => desk.waiting?.filter((a) => !gone.has(a.id)), [desk.waiting, gone]);
  // Answers given in this desk first; automatic (test-run) answers last, under one collapsed heading.
  const history = useMemo(
    () => desk.answered && [...desk.answered.filter((h) => h.source !== "scripted"), ...desk.answered.filter((h) => h.source === "scripted")],
    [desk.answered],
  );
  const [autoOpen, setAutoOpen] = useState(false);
  const items: (ApprovalVM | AnsweredVM)[] | undefined = tab === "waiting" ? waiting : history;
  const active = items?.find((a) => a.id === activeId) ?? items?.[0];
  const index = active && items ? items.indexOf(active) : -1;

  // Keep the selection valid as the list changes (poll, answers).
  useEffect(() => {
    if (items && items.length && !items.some((a) => a.id === activeId)) setActiveId(items[0].id);
  }, [items, activeId]);

  const move = useCallback(
    (d: 1 | -1) => {
      if (!items?.length) return;
      const i = Math.max(0, Math.min(items.length - 1, index + d));
      setActiveId(items[i].id);
    },
    [items, index],
  );

  const onAnswered = useCallback((id: string) => setAnswered((s0) => new Set(s0).add(id)), []);
  const onAdvance = useCallback(
    (id: string) => {
      if (!waiting) return;
      const i = waiting.findIndex((a) => a.id === id);
      const next = waiting.slice(i + 1).find((a) => !answered.has(a.id) && a.id !== id) ?? waiting.find((a) => !answered.has(a.id) && a.id !== id);
      setGone((g) => new Set(g).add(id));
      setActiveId(next?.id);
      desk.reload();
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [waiting, answered],
  );

  // Nothing waiting: one centred empty state instead of an empty list beside an empty desk.
  const caughtUp = tab === "waiting" && items !== undefined && items.length === 0;
  // Progress through this session's queue: answered here / (answered here + still waiting).
  const queueTotal = (desk.waiting?.length ?? 0) + gone.size;
  const answeredHere = answered.size;
  const sessionDone = caughtUp && answeredHere > 0;
  const eventNames = [...new Set((items ?? []).map((i) => i.eventName).filter((x): x is string => Boolean(x)))];
  const personHistory = history?.filter((h) => h.source !== "scripted") ?? [];
  const autoHistory = history?.filter((h) => h.source === "scripted") ?? [];

  return (
    // The shell gives the desk route a full-height flex column (xl+), so the grid below just takes the rest:
    // no header-height arithmetic, and it stays right when the judge banner or a wrapped subtitle changes height.
    <div className={`${s.root} flex min-h-0 flex-1 flex-col`}>
      <div data-kf-toast-anchor aria-hidden="true" className="h-0" />
      {/* No title block: the nav already says Approvals, and the desk needs the height for the photos. */}
      <h1 className="sr-only">Approvals</h1>

      <ErrorNote error={desk.error} retry={desk.reload} />

      {/* Below xl the inbox is a compact selector above the desk. */}
      {!caughtUp && (
        <div className="mb-3 flex flex-wrap items-center gap-2 xl:hidden">
          <div role="tablist" aria-label="Requests" className="flex rounded-[9px] bg-[var(--kf-surface-2)] p-[3px] ring-1 ring-inset ring-[var(--kf-border)]">
            <TabButton active={tab === "waiting"} onClick={() => setTab("waiting")} count={waiting?.filter((a) => !answered.has(a.id)).length} urgent>
              Waiting
            </TabButton>
            <TabButton active={tab === "answered"} onClick={() => setTab("answered")} count={history?.length}>
              Answered
            </TabButton>
          </div>
          {items && items.length > 0 && (
            <>
              <label htmlFor="ap-pick" className="sr-only">
                Request
              </label>
              <select
                id="ap-pick"
                value={active?.id ?? ""}
                onChange={(e) => setActiveId(e.target.value)}
                className="h-10 min-w-0 flex-1 basis-[14rem] rounded-[8px] border border-[var(--ap-border)] bg-[var(--ap-surface)] px-3 text-[0.88rem] focus-visible:outline-2 focus-visible:outline-[var(--ap-accent)]"
              >
                {items.map((a) => (
                  <option key={a.id} value={a.id}>
                    {`${a.eventName ? `${a.eventName} · ` : ""}${burstLabel(a)}${answered.has(a.id) ? " (answered)" : ""}${"source" in a ? ` · ${ANSWERED_WORD[(a as AnsweredVM).source]}` : ""}`}
                  </option>
                ))}
              </select>
            </>
          )}
        </div>
      )}

      <div
        className={`grid min-h-0 grid-cols-1 gap-2 xl:min-h-[520px] xl:flex-1 ${caughtUp ? "" : s.deskCols}`}
      >
        {!caughtUp && (
        <aside className={`${s.card} hidden min-h-0 min-w-0 flex-col overflow-hidden xl:flex`} aria-label="Request inbox">
          <div role="tablist" aria-label="Requests" className="border-b border-[var(--ap-border)] p-2">
            <div className="flex rounded-[9px] bg-[var(--kf-surface-2)] p-[3px] ring-1 ring-inset ring-[var(--kf-border)]">
            <TabButton active={tab === "waiting"} onClick={() => setTab("waiting")} count={waiting?.filter((a) => !answered.has(a.id)).length} urgent>
              Waiting
            </TabButton>
            <TabButton active={tab === "answered"} onClick={() => setTab("answered")} count={history?.length}>
              Answered
            </TabButton>
            </div>
          </div>
          {tab === "waiting" && !readOnly && queueTotal > 0 && <QueueMeter done={answeredHere} total={queueTotal} />}
          {eventNames.length === 1 && (
            <p className="truncate border-b border-[var(--ap-border)] px-4 py-2 text-[0.76rem] font-medium text-[var(--ap-muted)]">{eventNames[0]}</p>
          )}
          <div className={`${s.queueScroll} min-h-0 flex-1 overflow-y-auto p-1.5`}>
            {!items ? (
              <InboxSkeleton />
            ) : items.length === 0 ? (
              <p className="px-3 py-6 text-center text-[0.85rem] text-[var(--ap-muted)]">{tab === "waiting" ? "Queue empty" : "None yet"}</p>
            ) : tab === "answered" ? (
              <>
                {personHistory.length > 0 && (
                  <Inbox items={personHistory} activeId={active?.id} onSelect={setActiveId} now={now} badge={(vm) => <SourceTag source={(vm as AnsweredVM).source} />} />
                )}
                {autoHistory.length > 0 && (
                  <div className={personHistory.length ? "mt-2 border-t border-[var(--ap-border)] pt-2" : ""}>
                    <button
                      type="button"
                      aria-expanded={autoOpen || autoHistory.some((h) => h.id === active?.id)}
                      onClick={() => setAutoOpen((o) => !o)}
                      className="flex w-full items-center gap-2 rounded-[8px] px-2.5 py-2 text-left text-[0.8rem] font-medium text-[var(--ap-muted)] hover:bg-[var(--ap-surface-2)] hover:text-[var(--ap-text)] focus-visible:outline-2 focus-visible:outline-[var(--ap-accent)]"
                    >
                      <svg aria-hidden="true" viewBox="0 0 12 12" className={`h-3 w-3 transition-transform motion-reduce:transition-none ${autoOpen || autoHistory.some((h) => h.id === active?.id) ? "rotate-90" : ""}`}>
                        <path d="M4.5 2.5L8 6l-3.5 3.5" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" />
                      </svg>
                      <span className="flex-1" title="Answered by a script while the demo was recorded">Auto-answered</span>
                      <span className="t-data rounded-full bg-[var(--ap-surface-2)] px-1.5 text-[0.72rem] ring-1 ring-[var(--ap-border)] ring-inset">{autoHistory.length}</span>
                    </button>
                    {(autoOpen || autoHistory.some((h) => h.id === active?.id)) && (
                      <div className="mt-1">
                        <Inbox items={autoHistory} activeId={active?.id} onSelect={setActiveId} now={now} />
                      </div>
                    )}
                  </div>
                )}
              </>
            ) : (
              <Inbox items={items} activeId={active?.id} onSelect={setActiveId} now={now} doneIds={answered} />
            )}
          </div>
        </aside>
        )}

        <div className="flex min-h-0 min-w-0 flex-col">
          {!items ? (
            <DecisionSkeleton />
          ) : active ? (
            <Decision
              key={active.id}
              vm={active}
              readOnly={readOnly}
              position={{ index, total: items.length }}
              onAnswered={onAnswered}
              onAdvance={onAdvance}
              onMove={move}
              answeredLine={tab === "answered" ? ANSWERED_WORD[(active as AnsweredVM).source] : undefined}
              answeredNote={tab === "answered" ? (active as AnsweredVM).note : undefined}
            />
          ) : sessionDone ? (
            <Celebrate n={answeredHere} onHistory={() => setTab("answered")} />
          ) : (
            <EmptyDesk tab={tab} hasHistory={Boolean(history?.length)} onHistory={() => setTab("answered")} />
          )}
        </div>
      </div>
    </div>
  );
}

function TabButton({ active, onClick, count, urgent = false, children }: { active: boolean; onClick: () => void; count?: number; urgent?: boolean; children: React.ReactNode }) {
  return (
    <button
      type="button"
      role="tab"
      aria-selected={active}
      onClick={onClick}
      // Same look as every segmented control in the app: the selected tab is the raised white pill.
      className={`flex h-8 flex-1 items-center justify-center gap-2 rounded-[6px] px-3 text-[0.8125rem] font-medium transition-[background-color,color,box-shadow] focus-visible:outline-2 focus-visible:outline-[var(--ap-accent)] ${
        active ? "bg-[var(--kf-surface)] text-[var(--ap-text)] shadow-[0_1px_2px_rgb(16_24_40/0.1),0_0_0_1px_var(--kf-border)]" : "text-[var(--ap-muted)] hover:text-[var(--ap-text)]"
      }`}
    >
      {children}
      {count !== undefined && (
        <span className={`t-data rounded-full px-1.5 text-[0.72rem] ${urgent && count > 0 ? "bg-[var(--kf-review-soft,#fdf2e1)] font-semibold text-[var(--ap-review)]" : "text-[var(--ap-muted)]"}`}>{count}</span>
      )}
    </button>
  );
}

function SourceTag({ source }: { source: AnsweredVM["source"] }) {
  const scripted = source === "scripted";
  return (
    <span
      className={`inline-flex items-center rounded-[4px] px-1.5 py-px text-[0.68rem] font-semibold ${
        scripted ? "bg-[#fdf1de] text-[var(--ap-review)]" : "bg-[var(--ap-surface-2)] text-[var(--ap-muted)] ring-1 ring-[var(--ap-border)] ring-inset"
      }`}
    >
      {ANSWERED_WORD[source]}
    </span>
  );
}

function EmptyDesk({ tab, hasHistory, onHistory }: { tab: Tab; hasHistory: boolean; onHistory: () => void }) {
  return (
    <div className={`${s.card} mx-auto grid w-full max-w-[720px] place-items-center p-10 text-center xl:my-auto`}>
      <div className="max-w-sm">
        <span aria-hidden="true" className="mx-auto grid h-14 w-14 place-items-center rounded-full bg-[#e7f4ec] text-[var(--ap-keep)]">
          <svg viewBox="0 0 24 24" className="h-7 w-7">
            <path d="M5 12.5 10 17.5 19 7" fill="none" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round" strokeLinejoin="round" />
          </svg>
        </span>
        <h2 className="mt-4 text-[1.2rem] font-semibold">{tab === "waiting" ? "You're all caught up" : "Nothing answered yet"}</h2>
        <p className="mt-1.5 text-[0.92rem] text-[var(--ap-muted)]">
          {tab === "waiting"
            ? "New close calls appear here."
            : "Answers appear here."}
        </p>
        <div className="mt-5 flex justify-center gap-2">
          {tab === "waiting" && hasHistory && (
            <button type="button" className="btn btn-primary" onClick={onHistory}>
              Answered
            </button>
          )}
          <Link href="/" className="btn btn-secondary">
            Overview
          </Link>
        </div>
      </div>
    </div>
  );
}

/** "3 of 19 answered" with a slim bar: progress through this session's queue. */
function QueueMeter({ done, total }: { done: number; total: number }) {
  const pct = total ? Math.round((done / total) * 100) : 0;
  return (
    <div className="border-b border-[var(--ap-border)] px-4 py-2.5">
      <div className="flex items-baseline justify-between text-[0.78rem]">
        <span className="sr-only" aria-live="polite">
          <span className="t-data">{done}</span> of <span className="t-data">{total}</span> answered
        </span>
      </div>
      <div
        role="progressbar"
        aria-label="Requests answered"
        aria-valuemin={0}
        aria-valuemax={total}
        aria-valuenow={done}
        className="mt-1.5 h-1.5 overflow-hidden rounded-full bg-[var(--ap-surface-2)] ring-1 ring-[var(--ap-border)] ring-inset"
      >
        <div className="h-full rounded-full bg-[var(--ap-keep)] transition-[width] duration-500 motion-reduce:transition-none" style={{ width: `${pct}%` }} />
      </div>
    </div>
  );
}

/** Queue cleared in this session: a short, warm finish, with where to go next. */
function Celebrate({ n, onHistory }: { n: number; onHistory: () => void }) {
  return (
    <div className={`${s.card} ${s.fadeIn} relative mx-auto w-full max-w-[720px] overflow-hidden p-10 text-center xl:my-auto`} role="status">
      <div aria-hidden="true" className={s.confetti}>
        {Array.from({ length: 14 }, (_, i) => (
          <span key={i} style={{ ["--i" as string]: i }} />
        ))}
      </div>
      <span aria-hidden="true" className="relative mx-auto grid h-16 w-16 place-items-center rounded-full bg-[var(--ap-keep)] shadow-[0_0_0_10px_var(--kf-keep-soft,#e6f4ec)]">
        <svg viewBox="0 0 24 24" className="h-8 w-8">
          <path className={s.check} d="M5 12.5 10 17.5 19 7" fill="none" stroke="#fff" strokeWidth="2.6" strokeLinecap="round" strokeLinejoin="round" />
        </svg>
      </span>
      <h2 className="relative mt-6 text-[1.4rem] font-semibold tracking-[-0.015em]">Queue cleared</h2>
      <p className="relative mx-auto mt-2 max-w-sm text-[0.95rem] text-[var(--ap-muted)]">
        <span className="t-data font-semibold text-[var(--ap-text)]">{n}</span> answered
      </p>
      <div className="relative mt-6 flex justify-center gap-2">
        <button type="button" className="btn btn-primary btn-lg" onClick={onHistory}>
          See answers
        </button>
        <Link href="/" className="btn btn-secondary btn-lg">
          Overview
        </Link>
      </div>
    </div>
  );
}
