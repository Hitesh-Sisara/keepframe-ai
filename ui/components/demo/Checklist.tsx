"use client";

import { usePathname, useSearchParams } from "next/navigation";
import { useEffect, useState } from "react";
import { demoAnsweredCount, onDemoChange } from "@/lib/demo";
import type { DemoManifest } from "./Tour";

// First-run checklist (demo only): four things that tell the human-in-the-loop story. Each item ticks itself
// from what the visitor actually does; the card collapses to an "n/4" pill. Progress is kept per browser.

const KEY = "keepframe.demo.checklist";
type Item = "open" | "answer" | "trace" | "palette";
const ITEMS: { key: Item; label: string }[] = [
  { key: "open", label: "Open a close call" },
  { key: "answer", label: "Answer a close call" },
  { key: "trace", label: "See why it asked" },
  { key: "palette", label: "Jump anywhere with ⌘K" },
];

function readDone(): Set<Item> {
  try {
    return new Set(JSON.parse(window.localStorage.getItem(KEY) ?? "[]") as Item[]);
  } catch {
    return new Set();
  }
}

/** Invisible tracker, mounted once in the shell: ticks items from what the visitor does. The list itself is
 *  shown inside the demo pill's popover (ChecklistPanel), so it never takes space by default. */
export function DemoChecklist({ manifest }: { manifest?: DemoManifest }) {
  const path = usePathname();
  const params = useSearchParams();
  const tick = (k: Item) => {
    const d = readDone();
    if (d.has(k)) return;
    d.add(k);
    try {
      window.localStorage.setItem(KEY, JSON.stringify([...d]));
    } catch {
      /* ignore */
    }
    window.dispatchEvent(new Event(CHANGE));
  };

  // 1: an approval request, or a waiting group, is on screen.
  useEffect(() => {
    if (!manifest) return;
    const gid = params.get("id") ?? params.get("group");
    if (path.startsWith("/approvals") || (path.startsWith("/groups") && gid && manifest.pending_groups.includes(gid))) tick("open");
    // 3: the agent's trace has been scrolled into view on a group page.
    if (!path.startsWith("/groups") || !params.get("id")) return;
    let io: IntersectionObserver | undefined;
    const t = window.setInterval(() => {
      const el = document.getElementById("trace");
      if (!el) return;
      window.clearInterval(t);
      io = new IntersectionObserver((es) => es.some((e) => e.isIntersecting) && tick("trace"), { threshold: 0.15 });
      io.observe(el);
    }, 250);
    return () => {
      window.clearInterval(t);
      io?.disconnect();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [path, params, manifest]);

  // 2: the visitor answered a close call (in-memory demo write).
  useEffect(() => {
    if (demoAnsweredCount() > 0) tick("answer");
    return onDemoChange(() => demoAnsweredCount() > 0 && tick("answer"));
  }, []);

  // 4: the command palette.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => (e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k" && tick("palette");
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  return null;
}

const CHANGE = "kf:checklist";

/** The checklist, inside the demo pill's popover. */
export function ChecklistPanel() {
  const [done, setDone] = useState<Set<Item>>(() => new Set());
  useEffect(() => {
    const sync = () => setDone(readDone());
    sync();
    window.addEventListener(CHANGE, sync);
    return () => window.removeEventListener(CHANGE, sync);
  }, []);
  const n = ITEMS.filter((i) => done.has(i.key)).length;
  return (
    <section aria-label={`Getting started, ${n} of ${ITEMS.length} done`}>
      <p className="mb-2 flex items-center gap-2 font-semibold text-[var(--kf-text)]">
        <Ring n={n} total={ITEMS.length} />
        <span className="flex-1">Getting started</span>
        <span className="t-data text-[0.75rem] font-normal text-[var(--kf-muted)]">
          {n}/{ITEMS.length}
        </span>
      </p>
      <List done={done} />
    </section>
  );
}

function List({ done, className = "" }: { done: Set<Item>; className?: string }) {
  return (
      <ul className={`space-y-1.5 ${className}`}>
        {ITEMS.map((i) => {
          const on = done.has(i.key);
          return (
            <li key={i.key} className="flex items-center gap-2 text-[0.78rem]">
              <span
                aria-hidden="true"
                className={`grid h-[18px] w-[18px] shrink-0 place-items-center rounded-full ${on ? "bg-[var(--kf-keep)] text-white" : "ring-[1.5px] ring-[var(--kf-border-strong)] ring-inset"}`}
              >
                {on && (
                  <svg viewBox="0 0 12 12" className="h-3 w-3">
                    <path d="M3 6.2l2 2 4-4.4" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" />
                  </svg>
                )}
              </span>
              <span className={on ? "text-[var(--kf-muted)] line-through decoration-[var(--kf-border-strong)]" : "text-[var(--kf-text)]"}>{i.label}</span>
              <span className="sr-only">{on ? ", done" : ", not done yet"}</span>
            </li>
          );
        })}
      </ul>
  );
}

function Ring({ n, total }: { n: number; total: number }) {
  const r = 7;
  const c = 2 * Math.PI * r;
  return (
    <svg viewBox="0 0 18 18" className="h-[18px] w-[18px] -rotate-90" aria-hidden="true">
      <circle cx="9" cy="9" r={r} fill="none" stroke="var(--kf-border)" strokeWidth="2.2" />
      {n > 0 && <circle cx="9" cy="9" r={r} fill="none" stroke="var(--kf-keep)" strokeWidth="2.2" strokeLinecap="round" strokeDasharray={`${(n / total) * c} ${c}`} />}
    </svg>
  );
}
