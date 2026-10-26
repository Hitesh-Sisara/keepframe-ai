"use client";

import { useEffect, useRef } from "react";
import s from "./approvals.module.css";
import { gist, keeperOf, relAge, type ApprovalVM } from "./logic";

/** The request's name as shown everywhere else in the app ("Group 13"): one noun across pages. */
export function burstLabel(vm: ApprovalVM): string {
  return vm.groupLabel.replace(/^Burst\b/, "Group");
}

/** Inbox of requests: thumbnail of the agent's pick, burst, event, why it asked, age. */
export function Inbox({
  items,
  activeId,
  onSelect,
  now,
  doneIds,
  badge,
}: {
  items: ApprovalVM[];
  activeId?: string;
  onSelect: (id: string) => void;
  now: number;
  doneIds?: Set<string>;
  badge?: (vm: ApprovalVM) => React.ReactNode;
}) {
  const manyEvents = new Set(items.map((i) => i.eventId)).size > 1;
  // Two "Group 1"s from different events: prefix the event so they read apart.
  const labelEvents = new Map<string, Set<string>>();
  for (const i of items) labelEvents.set(burstLabel(i), (labelEvents.get(burstLabel(i)) ?? new Set()).add(i.eventId));
  const shownLabel = (vm: ApprovalVM) => ((labelEvents.get(burstLabel(vm))?.size ?? 0) > 1 && vm.eventName ? `${vm.eventName} · ${burstLabel(vm)}` : burstLabel(vm));
  const refs = useRef(new Map<string, HTMLButtonElement>());
  useEffect(() => {
    if (activeId) refs.current.get(activeId)?.scrollIntoView({ block: "nearest" });
  }, [activeId]);

  return (
    <ul className="flex flex-col gap-1.5" aria-label="Requests">
      {items.map((vm) => {
        const active = vm.id === activeId;
        const done = doneIds?.has(vm.id);
        const keeper = vm.frames.find((f) => f.photo_id === keeperOf(vm.frames, vm.proposed)) ?? vm.frames[0];
        return (
          <li key={vm.id}>
            <button
              ref={(el) => {
                if (el) refs.current.set(vm.id, el);
                else refs.current.delete(vm.id);
              }}
              type="button"
              onClick={() => onSelect(vm.id)}
              aria-current={active ? "true" : undefined}
              className={`group flex w-full items-center gap-3 rounded-[8px] px-2.5 py-2 text-left transition-colors focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-[var(--ap-accent)] ${
                active
                  ? "bg-[var(--ap-accent-soft)] shadow-[inset_3px_0_0_var(--ap-accent)]"
                  : "hover:bg-[var(--ap-surface-2)]"
              } ${done ? "opacity-50" : ""}`}
            >
              <span className="relative block h-[44px] w-[66px] shrink-0 overflow-hidden rounded-[6px] bg-[var(--ap-surface-2)]">
                {keeper?.proxy_url && (
                  // eslint-disable-next-line @next/next/no-img-element
                  <img src={keeper.proxy_url} alt="" referrerPolicy="no-referrer" loading="lazy" className="h-full w-full object-cover" />
                )}
                <span className="absolute right-1 bottom-1 inline-flex items-center gap-0.5 rounded-[4px] bg-black/70 px-1 text-[0.64rem] font-semibold text-white tabular-nums" title={`${vm.frames.length} frames in this group`}>
                  <svg aria-hidden="true" viewBox="0 0 12 12" className="h-2.5 w-2.5">
                    <rect x="1" y="3.5" width="7.5" height="6" rx="1" fill="none" stroke="currentColor" strokeWidth="1.2" />
                    <path d="M3.5 2h7v5.5" fill="none" stroke="currentColor" strokeWidth="1.2" />
                  </svg>
                  {vm.frames.length}
                  <span className="sr-only"> frames</span>
                </span>
              </span>
              <span className="min-w-0 flex-1">
                <span className="flex items-baseline justify-between gap-2">
                  <span className="truncate text-[0.92rem] font-semibold">{shownLabel(vm)}</span>
                  <span className="shrink-0 text-[0.72rem] text-[var(--ap-muted)] tabular-nums">
                    {done ? "answered" : relAge(vm.createdAt, now)}
                  </span>
                </span>
                {manyEvents && vm.eventName && <span className="block truncate text-[0.74rem] text-[var(--ap-muted)]">{vm.eventName}</span>}
                <span className="mt-0.5 line-clamp-2 block text-[0.78rem] leading-snug text-[var(--ap-muted)]">
                  {gist(vm)}
                </span>
                {badge && <span className="mt-1 block">{badge(vm)}</span>}
              </span>
            </button>
          </li>
        );
      })}
    </ul>
  );
}

export function InboxSkeleton() {
  return (
    <div className="flex flex-col gap-1.5" aria-hidden="true">
      {[0, 1, 2, 3, 4].map((i) => (
        <div key={i} className="flex gap-3 p-2.5">
          <div className={`${s.skel} h-[44px] w-[66px] shrink-0`} />
          <div className="flex-1 space-y-2 pt-1">
            <div className={`${s.skel} h-3 w-2/3`} />
            <div className={`${s.skel} h-2.5 w-1/2`} />
            <div className={`${s.skel} h-2.5 w-11/12`} />
          </div>
        </div>
      ))}
    </div>
  );
}
