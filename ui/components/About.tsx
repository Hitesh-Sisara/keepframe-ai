"use client";

import { trapTab } from "@/lib/a11y";
import { useEffect, useRef } from "react";
import { useApi } from "@/lib/api";
import { cleanText } from "@/lib/format";
import type { Page, Run } from "@/lib/types";
import { LogoMark, Skeleton } from "./ui";
import pkg from "../package.json";

/** About: one line on what it is, the latest run's planner and OpenCV version (GET /runs/{id}), the app version. */
export function AboutPanel({ onClose }: { onClose: () => void }) {
  const ref = useRef<HTMLDialogElement>(null);
  const runs = useApi<Page<Run>>("/runs", { limit: 1 });
  const latestId = runs.data?.items[0]?.run_id;
  const run = useApi<Run>(latestId ? `/runs/${encodeURIComponent(latestId)}` : null);

  useEffect(() => {
    const d = ref.current;
    d?.showModal();
    return () => d?.close();
  }, []);

  return (
    <dialog
      ref={ref}
      // StrictMode re-runs the effect: the cleanup's close() fires its event after showModal() reopened it.
      onClose={() => !ref.current?.open && onClose()}
      onClick={(e) => e.target === ref.current && onClose()}
      aria-labelledby="about-title"
      // Keys typed in the modal stay in the modal: page shortcuts listen on window (Esc on a group page would
      // otherwise close this AND navigate back to the grid; Z / arrows would act on the hidden page). ⌘K still
      // reaches the Shell so it toggles the palette.
      onKeyDown={(e) => {
        if (!((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k")) e.stopPropagation();
        // showModal makes the page inert, but Tab can still leave for the browser chrome: cycle inside.
        trapTab(e, ref.current);
      }}
      className="kf-pop m-auto w-[min(480px,calc(100vw-2rem))] overflow-hidden rounded-[var(--kf-radius-lg)] border border-[var(--kf-border)] bg-[var(--kf-surface)] p-0 text-[var(--kf-text)] shadow-[var(--kf-shadow-lg)]"
    >
      <div className="flex items-center justify-between gap-4 border-b border-[var(--kf-border)] px-6 py-4">
        <h2 id="about-title" className="flex items-center gap-2.5 t-title">
          <LogoMark className="h-6 w-6" />
          About KeepFrame
          <span className="t-data text-[0.8125rem] font-normal text-[var(--kf-muted)]">v{pkg.version}</span>
        </h2>
        <button type="button" className="btn btn-ghost btn-icon btn-sm !w-8" onClick={onClose} aria-label="Close" autoFocus>
          <svg viewBox="0 0 16 16" className="h-4 w-4" aria-hidden="true">
            <path d="M4 4l8 8M12 4l-8 8" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" />
          </svg>
        </button>
      </div>
      <div className="max-h-[calc(100dvh-8rem)] overflow-y-auto px-6 pt-4 pb-5">
        <p className="text-[0.9375rem] font-medium text-[var(--kf-text)]">Culls event photos; asks a person on close calls.</p>
        <OpenCvBanner run={run.data} loading={runs.loading || run.loading} />
      </div>
    </dialog>
  );
}

export function OpenCvBanner({ run, loading }: { run?: Run; loading?: boolean }) {
  if (!run) {
    return loading ? (
      <div className="mt-4" aria-busy="true">
        <Skeleton className="h-4 w-40" />
        <Skeleton className="mt-2 h-3 w-64" />
      </div>
    ) : (
      <p className="t-small mt-4 text-[var(--kf-muted)]">No runs yet</p>
    );
  }
  return (
    <dl className="mt-4 grid grid-cols-[6rem_minmax(0,1fr)] gap-y-1.5 text-[0.8125rem]">
      <dt className="text-[var(--kf-muted)]">Planner</dt>
      <dd className="font-medium">{plannerSentence(run)}</dd>
      <dt className="text-[var(--kf-muted)]">OpenCV</dt>
      <dd className="t-data font-medium" title={run.opencv_hal ? `Acceleration: ${run.opencv_hal}` : undefined}>
        {run.opencv_version ?? "5"}
      </dd>
    </dl>
  );
}

const SCRIPTED = new Set(["fake", "scripted", "none", "test"]);

/** Who planned this run, in a few words. */
export function plannerSentence(run: Run): string {
  const p = (run.llm_provider ?? "").toLowerCase();
  if (SCRIPTED.has(p) || /fake|scripted|test/i.test(run.planner_model ?? "")) {
    return "Offline test planner (deployed: Claude)";
  }
  if (run.planner_model) return cleanText(run.planner_model);
  return run.llm_provider ? PROVIDER_WORD[p] ?? run.llm_provider : "Not reported";
}

const PROVIDER_WORD: Record<string, string> = {
  anthropic: "Anthropic-compatible API",
  bedrock: "Amazon Bedrock",
  fake: "Offline test planner",
  // values from the API are shown as sent; only the provider word is mapped here
  none: "None (deterministic worker)",
};
