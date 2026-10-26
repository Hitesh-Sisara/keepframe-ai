"use client";

import { useEffect, useState } from "react";
import { isDemo, startDemo } from "@/lib/session";
import { LEAD_TEST_LABEL, SCRIPTED_LABEL } from "@/lib/decision";
import { STATUS_LABEL } from "@/lib/format";
import type { ApiError } from "@/lib/api";
import { EmptyState, Skeleton } from "./ui";

export { EmptyState } from "./ui";

/** Inline error panel with an optional retry. 403 explains the read-only judge token. */
export function ErrorNote({ error, retry }: { error?: ApiError; retry?: () => void }) {
  if (!error) return null;
  const readOnly = error.status === 403;
  const offline = error.status === 0;
  const title = readOnly ? "This needs team access" : offline ? "Can't reach the server" : error.status === 404 ? "Not found" : "Something went wrong";
  const text = readOnly
    ? "Judge token is read-only."
    : offline
      ? "Check your connection and retry."
      : error.message || "Request failed.";
  return (
    <div
      role="alert"
      className={`kf-card mb-4 flex items-start gap-3 px-4 py-3.5 ${readOnly ? "!border-[#f0d9b5] !bg-[var(--kf-review-soft)]" : "!border-[#f3cccc] !bg-[var(--kf-reject-soft)]"}`}
    >
      <svg
        viewBox="0 0 20 20"
        className={`mt-0.5 h-[18px] w-[18px] shrink-0 ${readOnly ? "text-[var(--kf-review)]" : "text-[var(--kf-reject)]"}`}
        aria-hidden="true"
      >
        {readOnly ? (
          <>
            <rect x="4.5" y="9" width="11" height="8" rx="1.8" fill="none" stroke="currentColor" strokeWidth="1.6" />
            <path d="M7 9V7a3 3 0 0 1 6 0v2" fill="none" stroke="currentColor" strokeWidth="1.6" />
          </>
        ) : (
          <>
            <circle cx="10" cy="10" r="7.25" fill="none" stroke="currentColor" strokeWidth="1.6" />
            <path d="M10 6.2v4.6M10 13.6v.01" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" />
          </>
        )}
      </svg>
      <div className="min-w-0 flex-1">
        <p className="text-[0.875rem] font-semibold text-[var(--kf-text)]">
          {title}
          {!readOnly && error.status > 0 && error.status !== 404 && <span className="t-data ml-2 font-normal text-[var(--kf-muted)]">HTTP {error.status}</span>}
        </p>
        <p className="t-small mt-0.5 text-[var(--kf-muted)]">{text}</p>
      </div>
      {retry && !readOnly && (
        <button type="button" className="btn btn-secondary btn-sm shrink-0" onClick={retry}>
          Try again
        </button>
      )}
    </div>
  );
}

/**
 * Loading placeholder. Renders a skeleton (never a bare "Loading…" line); `label` is announced to
 * screen readers. `variant` picks the shape: rows (default, lists/tables), cards (grid), page (full view).
 */
export function Loading({ label = "Loading", variant = "rows", count }: { label?: string; variant?: "rows" | "cards" | "page" | "inline"; count?: number }) {
  const sr = (
    <span className="sr-only" role="status">
      {label}…
    </span>
  );
  if (variant === "inline") {
    return (
      <span className="inline-flex items-center gap-2 text-[var(--kf-muted)]">
        <Skeleton className="h-3 w-24" />
        {sr}
      </span>
    );
  }
  if (variant === "cards") {
    return (
      <div aria-busy="true" className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
        {sr}
        {Array.from({ length: count ?? 3 }, (_, i) => (
          <div key={i} className="kf-card p-4">
            <Skeleton className="aspect-[3/2] w-full !rounded-[7px]" />
            <Skeleton className="mt-3 h-3.5 w-2/3" />
            <Skeleton className="mt-2 h-3 w-1/3" />
          </div>
        ))}
      </div>
    );
  }
  if (variant === "page") {
    return (
      <div aria-busy="true">
        {sr}
        <Skeleton className="h-7 w-56" />
        <Skeleton className="mt-3 h-3.5 w-[min(520px,80%)]" />
        <div className="mt-8 grid grid-cols-2 gap-3 sm:grid-cols-4">
          {Array.from({ length: 4 }, (_, i) => (
            <div key={i} className="kf-card px-4 py-3.5">
              <Skeleton className="h-3 w-16" />
              <Skeleton className="mt-2.5 h-6 w-12" />
            </div>
          ))}
        </div>
        <div className="kf-card mt-6 divide-y divide-[var(--kf-border)]">
          {Array.from({ length: 5 }, (_, i) => (
            <div key={i} className="flex items-center gap-4 px-4 py-3.5">
              <Skeleton className="h-9 w-14 !rounded-md" />
              <Skeleton className="h-3.5 w-1/3" />
              <Skeleton className="ml-auto h-3.5 w-20" />
            </div>
          ))}
        </div>
      </div>
    );
  }
  return (
    <div aria-busy="true" className="kf-card mb-4 divide-y divide-[var(--kf-border)]">
      {sr}
      {Array.from({ length: count ?? 3 }, (_, i) => (
        <div key={i} className="flex items-center gap-4 px-4 py-3.5">
          <Skeleton className="h-3.5 w-[28%]" />
          <Skeleton className="h-3.5 w-[18%]" />
          <Skeleton className="ml-auto h-3.5 w-16" />
        </div>
      ))}
    </div>
  );
}

/** Page header: title, optional lede, optional eyebrow/breadcrumb, actions on the right. */
export function PageHead({ title, children, lede, eyebrow }: { title: string; children?: React.ReactNode; lede?: React.ReactNode; eyebrow?: React.ReactNode }) {
  return (
    <div className="mb-7 flex flex-wrap items-end justify-between gap-x-8 gap-y-4">
      <div className="max-w-[72ch] min-w-0">
        {eyebrow && <div className="t-small mb-1.5 font-medium text-[var(--kf-muted)]">{eyebrow}</div>}
        <h1 className="t-display">{title}</h1>
        {lede && <div className="mt-1.5 text-[0.875rem] leading-relaxed text-[var(--kf-muted)]">{lede}</div>}
      </div>
      {children && <div className="flex flex-wrap items-center gap-2">{children}</div>}
    </div>
  );
}

/** Run status as a soft pill with a dot (pulsing while running). */
export function Status({ status }: { status: string }) {
  const waiting = status === "awaiting_approval";
  const failed = status === "failed";
  const done = status === "succeeded";
  const live = status === "running" || status === "starting";
  const cls = waiting
    ? "bg-[var(--kf-review-soft)] text-[var(--kf-review)]"
    : failed
      ? "bg-[var(--kf-reject-soft)] text-[var(--kf-reject)]"
      : done
        ? "bg-[var(--kf-keep-soft)] text-[var(--kf-keep)]"
        : live
          ? "bg-[var(--kf-accent-soft)] text-[var(--kf-accent-text)]"
          : "bg-[var(--kf-surface-2)] text-[var(--kf-muted)]";
  return (
    <span className={`inline-flex h-6 items-center gap-1.5 rounded-full px-2.5 text-[0.75rem] font-medium whitespace-nowrap ${cls}`}>
      <span aria-hidden="true" className="relative inline-flex h-1.5 w-1.5">
        {live && <span className="absolute inset-0 animate-ping rounded-full bg-current opacity-60 motion-reduce:hidden" />}
        <span className="relative h-1.5 w-1.5 rounded-full bg-current" />
      </span>
      {STATUS_LABEL[status] ?? status}
    </span>
  );
}

/** Amber marker for verdicts an automatic approver supplied: never presented as a person's decision. */
export function ScriptedBadge({ className = "" }: { className?: string }) {
  return (
    <p
      className={`inline-flex min-h-6 items-start gap-1.5 rounded-[8px] bg-[var(--kf-review-soft)] px-2.5 py-1 text-[0.75rem] leading-snug font-medium text-[var(--kf-review)] ${className}`}
    >
      <svg aria-hidden="true" viewBox="0 0 12 12" className="mt-0.5 h-3 w-3 shrink-0">
        <rect x="2" y="2" width="8" height="8" rx="1.5" fill="none" stroke="currentColor" strokeWidth="1.3" />
        <path d="M4 5h4M4 7.5h2.5" stroke="currentColor" strokeWidth="1.2" strokeLinecap="round" />
      </svg>
      {/* The same story the trace tells: the agent asked, a script answered (recording/test run). */}
      <span>
        Agent asked for a decision · answered automatically (test run)
        <span className="sr-only">. {SCRIPTED_LABEL}.</span>
      </span>
    </p>
  );
}

const RUN_KIND_WORD: Record<string, string> = {
  deployed: "Deployed",
  agentcore: "Deployed",
  local_calibration: "Local calibration",
  local: "Local",
};

/** Where a run happened. Deployed is a solid accent chip; local runs are neutral so they read as secondary. */
export function RunKindChip({ kind, note }: { kind?: string; note?: string }) {
  if (!kind) return null;
  const solid = kind === "deployed" || kind === "agentcore";
  return (
    <span
      title={note || undefined}
      className={`inline-flex h-5 items-center rounded-full px-2 text-[0.6875rem] font-medium whitespace-nowrap ${
        solid
          ? "bg-[var(--kf-accent-soft)] text-[var(--kf-accent-text)]"
          : "bg-[var(--kf-surface-2)] text-[var(--kf-muted)] ring-1 ring-[var(--kf-border)] ring-inset"
      }`}
    >
      {RUN_KIND_WORD[kind] ?? kind}
      {note && <span className="sr-only">: {note}</span>}
    </span>
  );
}

/** Neutral grey marker for answers a team member gave while testing the resume path. */
export function LeadTestBadge({ className = "" }: { className?: string }) {
  return (
    <p
      className={`inline-flex h-6 items-center gap-1.5 rounded-full bg-[var(--kf-surface-2)] px-2.5 text-[0.75rem] font-medium text-[var(--kf-muted)] ring-1 ring-[var(--kf-border)] ring-inset ${className}`}
    >
      <svg aria-hidden="true" viewBox="0 0 12 12" className="h-3 w-3">
        <circle cx="6" cy="6" r="4.5" fill="none" stroke="currentColor" strokeWidth="1.3" />
        <path d="M6 3.5v3l2 1.2" stroke="currentColor" strokeWidth="1.2" fill="none" strokeLinecap="round" />
      </svg>
      {LEAD_TEST_LABEL}
    </p>
  );
}

/** Convenience: the empty state used when a list has nothing yet. */
export function EmptyNote({ title, children, action }: { title: string; children?: React.ReactNode; action?: React.ReactNode }) {
  return <EmptyState title={title} description={children} action={action} compact />;
}

/** Why an image failed: "expired" = the photo is gone (404; the work bucket keeps proxies 7 days), "link" = the
 *  signed link lapsed (403), "other" = anything else. One probe per URL, cached; never runs in demo mode. */
export type ImageFailure = "expired" | "link" | "other";
const failCache = new Map<string, Promise<ImageFailure>>();
export function probeImage(url: string): Promise<ImageFailure> {
  let p = failCache.get(url);
  if (!p) {
    p = fetch(url, { cache: "no-store", referrerPolicy: "no-referrer" })
      .then((r): ImageFailure => (r.status === 404 ? "expired" : r.status === 403 ? "link" : "other"))
      .catch((): ImageFailure => "other");
    failCache.set(url, p);
  }
  return p;
}

/** `failed` → the probed reason (undefined while probing, or when the demo serves the image). */
export function useImageFailure(url: string | undefined, failed: boolean): ImageFailure | undefined {
  const [why, setWhy] = useState<ImageFailure>();
  useEffect(() => {
    if (!failed || !url) return setWhy(undefined);
    if (isDemo()) return setWhy("other");
    let live = true;
    void probeImage(url).then((w) => live && setWhy(w));
    return () => {
      live = false;
    };
  }, [url, failed]);
  return why;
}

export const EXPIRED_TEXT = "Photo expired (7-day retention)";

/** Placeholder for a photo that didn't load. Expired live photos (judges) get a way into the demo. */
/** `compact` drops the demo link (small thumbnails, or inside another control where a nested button is invalid). */
export function PhotoUnavailable({ why, onDark = false, compact = false }: { why?: ImageFailure; onDark?: boolean; compact?: boolean }) {
  const text = why === "expired" ? EXPIRED_TEXT : why === "link" ? "Image link expired. Reload the page." : "Preview unavailable";
  const muted = onDark ? "text-white/75" : "text-[var(--kf-muted)]";
  return (
    <span className={`absolute inset-0 flex flex-col items-center justify-center gap-1 p-2 text-center ${compact ? "text-[0.6875rem]" : "text-[0.75rem]"} ${muted}`}>
      <span>{text}</span>
      {why === "expired" && !compact && (
        <button
          type="button"
          onClick={(e) => {
            e.preventDefault();
            e.stopPropagation();
            startDemo();
          }}
          className={`relative z-20 rounded-[4px] font-semibold underline-offset-2 hover:underline focus-visible:outline-2 ${onDark ? "text-white" : "text-[var(--kf-accent-text)]"}`}
        >
          See the demo →
        </button>
      )}
    </span>
  );
}
