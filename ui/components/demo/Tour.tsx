"use client";

import { usePathname, useRouter } from "next/navigation";
import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import { routes } from "@/lib/routes";

// First-run guided tour for demo mode: three steps (research: 3-step tours complete far more often than long
// ones). Built in-house: a spotlight cut-out, one card, keyboard (→/Enter next, ← back, Esc skip), focus kept in
// the card and restored afterwards. Shown once per browser (localStorage), replayable from the demo pill.

const SEEN_KEY = "keepframe.demo.tour";
const START_EVENT = "kf:tour";

export interface DemoManifest {
  event_id: string;
  tour_group: string;
  tour_run: string;
  pending_groups: string[];
}

interface Step {
  title: string;
  body: string;
  href: (m: DemoManifest) => string;
  /** Element to spotlight; polled for until the page has rendered it. */
  target: (m: DemoManifest) => HTMLElement | null;
  /** Optional extra action on the card (navigates and ends the tour). */
  action?: { label: string; href: (m: DemoManifest) => string };
}

const visible = (els: Iterable<Element>) => [...els].find((e) => (e as HTMLElement).offsetParent !== null) as HTMLElement | undefined;

const STEPS: Step[] = [
  {
    title: "One card per burst",
    body: "The large photo is the keeper. Reasons say why it won.",
    href: (m) => routes.event(m.event_id),
    target: (m) => visible(document.querySelectorAll(`main a[href*="${m.tour_group}"]`))?.closest("li") ?? null,
  },
  {
    title: "Close calls come to you",
    body: "Six wait in Approvals with the agent's pick. Answers stay in this tab.",
    href: (m) => routes.event(m.event_id),
    target: () => visible(document.querySelectorAll('nav[aria-label="Main"] a[href$="/approvals/"]')) ?? null,
    action: { label: "Answer one", href: () => routes.approvals() },
  },
  {
    title: "How it decided",
    body: "Red brackets mark where a measurement changed the plan. Here it asked you.",
    href: (m) => `${routes.group(m.event_id, m.tour_group, m.tour_run)}#trace`,
    // The "too close to call → asked a person" pivot (its summary names the human); else the first pivot.
    target: () => {
      const all = [...document.querySelectorAll<HTMLElement>('#trace li[aria-label^="Changed plan"]')];
      return all.find((li) => /human|person/i.test(li.getAttribute("aria-label") ?? "")) ?? all[0] ?? null;
    },
  },
];

export function startTour(): void {
  window.dispatchEvent(new Event(START_EVENT));
}

export function DemoTour({ manifest }: { manifest?: DemoManifest }) {
  const router = useRouter();
  const path = usePathname();
  const [step, setStep] = useState<number | null>(null);
  const [rect, setRect] = useState<DOMRect | null>(null);
  const card = useRef<HTMLDivElement>(null);
  const returnFocus = useRef<HTMLElement | null>(null);
  const target = useRef<HTMLElement | null>(null);

  const finish = useCallback(() => {
    try {
      window.localStorage.setItem(SEEN_KEY, "done");
    } catch {
      /* private mode: the tour may show again next visit */
    }
    setStep(null);
    setRect(null);
    target.current = null;
    returnFocus.current?.focus?.();
  }, []);

  const begin = useCallback(() => {
    returnFocus.current = document.activeElement as HTMLElement | null;
    window.scrollTo(0, 0);
    setStep(0);
  }, []);

  // First visit: start shortly after the first page has painted. Replays come from the demo pill.
  useEffect(() => {
    if (!manifest) return;
    let seen = false;
    try {
      seen = window.localStorage.getItem(SEEN_KEY) === "done";
    } catch {
      /* ignore */
    }
    // Auto-start only right after "Explore the demo" (or on the static demo build's first page): a deep link,
    // e.g. a judge opening /approvals/ or a burst, must stay where it is.
    let pending = false;
    try {
      pending = window.sessionStorage.getItem("keepframe.tour.pending") === "1";
      window.sessionStorage.removeItem("keepframe.tour.pending");
    } catch {
      /* ignore */
    }
    const staticFirst = process.env.NEXT_PUBLIC_DEMO === "1" && /\/(keepframe-ai\/?)?$/.test(window.location.pathname);
    const landing = pending || staticFirst;
    const t = seen || !landing ? undefined : window.setTimeout(begin, 700);
    window.addEventListener(START_EVENT, begin);
    return () => {
      if (t) window.clearTimeout(t);
      window.removeEventListener(START_EVENT, begin);
    };
  }, [manifest, begin]);

  // Go to the step's page, then wait for its target to render.
  useEffect(() => {
    if (step === null || !manifest) return;
    const s = STEPS[step];
    const href = s.href(manifest);
    const want = href.split("#")[0];
    const here = `${path}${window.location.search}`;
    if (!sameRoute(here, want)) router.push(href);
    setRect(null);
    target.current = null;
    let tries = 0;
    const id = window.setInterval(() => {
      const el = s.target(manifest);
      if (el || ++tries > 60) {
        window.clearInterval(id);
        target.current = el;
        if (el) {
          // Instant scroll, then measure on the next frames: a smooth scroll left the rect stale (step 3).
          el.scrollIntoView({ block: el.offsetHeight > window.innerHeight * 0.7 ? "start" : "center" });
          requestAnimationFrame(() => requestAnimationFrame(() => setRect(el.getBoundingClientRect())));
          // Images above the target can still load and shift it: re-measure once they settle.
          window.setTimeout(() => target.current === el && setRect(el.getBoundingClientRect()), 600);
        } else setRect(null);
        window.setTimeout(() => card.current?.focus(), 0);
      }
    }, 150);
    return () => window.clearInterval(id);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [step, manifest]);

  // Keep the spotlight on its target while the page scrolls or resizes.
  useEffect(() => {
    if (step === null) return;
    const sync = () => target.current && setRect(target.current.getBoundingClientRect());
    window.addEventListener("scroll", sync, true);
    window.addEventListener("resize", sync);
    return () => {
      window.removeEventListener("scroll", sync, true);
      window.removeEventListener("resize", sync);
    };
  }, [step]);

  const next = useCallback(() => setStep((s) => (s === null ? s : s + 1 < STEPS.length ? s + 1 : (finish(), null))), [finish]);
  const back = useCallback(() => setStep((s) => (s && s > 0 ? s - 1 : s)), []);

  useEffect(() => {
    if (step === null) return;
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") {
        e.preventDefault();
        finish();
      } else if (e.key === "ArrowRight") {
        e.preventDefault();
        next();
      } else if (e.key === "ArrowLeft") {
        e.preventDefault();
        back();
      } else if (e.key === "Tab" && card.current) {
        // Focus stays inside the card while the tour is open.
        const f = [...card.current.querySelectorAll<HTMLElement>("button, a[href]")];
        if (!f.length) return;
        const first = f[0];
        const last = f[f.length - 1];
        if (e.shiftKey && (document.activeElement === first || document.activeElement === card.current)) {
          e.preventDefault();
          last.focus();
        } else if (!e.shiftKey && document.activeElement === last) {
          e.preventDefault();
          first.focus();
        }
      }
    }
    window.addEventListener("keydown", onKey, true);
    return () => window.removeEventListener("keydown", onKey, true);
  }, [step, next, back, finish]);

  if (step === null || !manifest) return null;
  const s = STEPS[step];
  return (
    <div className="pointer-events-none fixed inset-0 z-[80]">
      <Spotlight rect={rect} />
      <TourCard
        ref={card}
        rect={rect}
        step={step}
        total={STEPS.length}
        s={s}
        onNext={next}
        onBack={back}
        onSkip={finish}
        onAction={
          s.action
            ? () => {
                const href = s.action!.href(manifest);
                finish();
                router.push(href);
              }
            : undefined
        }
      />
    </div>
  );
}

function sameRoute(a: string, b: string): boolean {
  const norm = (u: string) => {
    const [p, q = ""] = u.split("?");
    return `${p.replace(/\/+$/, "") || "/"}?${[...new URLSearchParams(q).entries()].sort().map(([k, v]) => `${k}=${v}`).join("&")}`;
  };
  return norm(a) === norm(b);
}

function Spotlight({ rect }: { rect: DOMRect | null }) {
  if (!rect) return <div className="kf-fade absolute inset-0 bg-[rgb(15_18_25/0.45)]" aria-hidden="true" />;
  const pad = 8;
  // Keep the ring at least 4 px inside the viewport.
  const top = Math.max(4, rect.top - pad);
  // Extra room on the left: the trace's red bracket sits in the gutter just outside its block.
  const left = Math.max(4, rect.left - pad - 10);
  const bottom = Math.min(window.innerHeight - 4, rect.bottom + pad);
  const right = Math.min(window.innerWidth - 4, rect.right + pad);
  return (
    <div
      aria-hidden="true"
      className="absolute rounded-[14px] transition-[top,left,width,height] duration-200 ease-out motion-reduce:transition-none"
      style={{
        top,
        left,
        width: right - left,
        height: bottom - top,
        boxShadow: "0 0 0 2px var(--kf-accent), 0 0 0 9999px rgb(15 18 25 / 0.45)",
      }}
    />
  );
}

interface CardProps {
  rect: DOMRect | null;
  step: number;
  total: number;
  s: Step;
  onNext: () => void;
  onBack: () => void;
  onSkip: () => void;
  onAction?: () => void;
}

const TourCard = ({ ref, rect, step, total, s, onNext, onBack, onSkip, onAction }: CardProps & { ref: React.Ref<HTMLDivElement> }) => {
  const own = useRef<HTMLDivElement | null>(null);
  const [pos, setPos] = useState<React.CSSProperties>({ left: "50%", top: "50%", transform: "translate(-50%, -50%)" });
  useLayoutEffect(() => {
    const el = own.current;
    if (!el) return;
    const W = window.innerWidth;
    const H = window.innerHeight;
    const w = el.offsetWidth;
    const h = el.offsetHeight;
    if (!rect) return setPos({ left: Math.max(16, (W - w) / 2), top: Math.max(16, (H - h) / 2) });
    const gap = 20;
    // Prefer beside the target (the sidebar item, a tall card); else below; else above.
    let left: number;
    let top: number;
    if (rect.right + gap + w <= W - 16 && rect.width < W / 2) {
      left = rect.right + gap;
      top = Math.min(Math.max(16, rect.top), H - h - 16);
    } else if (rect.bottom + gap + h <= H - 16) {
      left = Math.min(Math.max(16, rect.left), W - w - 16);
      top = rect.bottom + gap;
    } else {
      left = Math.min(Math.max(16, rect.left), W - w - 16);
      top = Math.max(16, rect.top - gap - h);
    }
    setPos({ left, top });
  }, [rect, step]);

  const last = step === total - 1;
  return (
    <div
      ref={(n) => {
        own.current = n;
        if (typeof ref === "function") ref(n);
        else if (ref) (ref as React.RefObject<HTMLDivElement | null>).current = n;
      }}
      role="dialog"
      aria-modal="false"
      aria-labelledby="kf-tour-title"
      aria-describedby="kf-tour-body"
      tabIndex={-1}
      className="kf-pop pointer-events-auto absolute w-[min(360px,calc(100vw-2rem))] rounded-[var(--kf-radius-lg)] bg-[var(--kf-surface)] p-5 shadow-[0_24px_60px_-12px_rgb(16_24_40/0.45)] ring-1 ring-[var(--kf-border)] outline-none focus-visible:ring-2 focus-visible:ring-[var(--kf-accent)]"
      style={pos}
    >
      <p className="t-eyebrow !text-[var(--kf-accent-text)]">
        Tour · {step + 1} of {total}
      </p>
      <h2 id="kf-tour-title" className="mt-1.5 text-[1.0625rem] leading-snug font-semibold tracking-[-0.01em] text-[var(--kf-text)]">
        {s.title}
      </h2>
      <p id="kf-tour-body" className="mt-1.5 text-[0.875rem] leading-relaxed text-[var(--kf-muted)]">
        {s.body}
      </p>
      {onAction && (
        <button type="button" onClick={onAction} className="mt-3 inline-flex items-center gap-1 rounded-[4px] text-[0.8125rem] font-semibold text-[var(--kf-accent-text)] hover:underline">
          {s.action?.label} <span aria-hidden="true">→</span>
        </button>
      )}
      <div className="mt-4 flex items-center gap-2">
        <span className="flex gap-1" aria-hidden="true">
          {Array.from({ length: total }, (_, i) => (
            <span key={i} className={`h-1.5 rounded-full transition-all motion-reduce:transition-none ${i === step ? "w-4 bg-[var(--kf-accent)]" : "w-1.5 bg-[var(--kf-border-strong)]"}`} />
          ))}
        </span>
        <span className="ml-auto flex items-center gap-1.5">
          <button type="button" onClick={onSkip} className="btn btn-ghost btn-sm">
            Skip
          </button>
          {step > 0 && (
            <button type="button" onClick={onBack} className="btn btn-secondary btn-sm" aria-label="Previous step">
              Back
            </button>
          )}
          <button type="button" onClick={onNext} className="btn btn-primary btn-sm">
            {last ? "Done" : "Next"}
          </button>
        </span>
      </div>
      <p className="t-small mt-3 hidden text-[var(--kf-faint)] sm:block">
        <kbd>←</kbd> <kbd>→</kbd> to move, <kbd>Esc</kbd> to close
      </p>
    </div>
  );
};
