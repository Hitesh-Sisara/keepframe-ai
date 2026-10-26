"use client";

import Link from "next/link";
import { useState } from "react";
import { ReasonChips } from "@/components/ReasonChip";
import { PhotoUnavailable, useImageFailure } from "@/components/States";
import { framesText, tallyText } from "@/lib/format";
import type { Group, Member } from "@/lib/types";
import { card, focusRing } from "./tokens";
import { VerdictBar } from "./VerdictBar";
import { CrownBadge, VerdictMark } from "../VerdictMark";
import { closeCallHint, closeCallText, fmtGap, isClose, isTie } from "@/lib/closecall";

export function GroupCard({ group, href }: { group: Group; href: string }) {
  const label = group.group_label ?? "Group";
  const best = group.members.find((m) => m.is_best) ?? group.members[0];
  const others = group.members.filter((m) => m !== best).slice(0, 3);
  const hidden = group.members.length - 1 - others.length;
  // A tag only when it changes what the reviewer does: a tie, or a close call that left frames in review.
  const inReview = (group.verdict_counts.review ?? 0) > 0;
  const tie = isTie(group);
  const close = inReview && isClose(group);

  return (
    <li className={`${card} group relative flex flex-col overflow-hidden transition-shadow duration-150 hover:shadow-[0_8px_24px_-10px_rgb(16_24_32/0.25),0_0_0_1px_rgb(16_24_32/0.08)] motion-reduce:transition-none ${group.pending_approval ? "ring-2 ring-[var(--kf-review,#b06800)]/60" : ""}`}>
      <Mosaic best={best} others={others} hidden={hidden} />
      <div className="flex flex-1 flex-col gap-3 p-4">
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0">
            <h3 className="text-[0.95rem] font-semibold text-[var(--kf-text,#1e2327)]">
              <Link
                href={href}
                aria-label={`${label}, ${framesText(group)}${tallyText(group.verdict_counts)}${group.pending_approval ? ", needs you" : ""}`}
                className={`rounded-[4px] after:absolute after:inset-0 after:content-[''] hover:underline ${focusRing}`}
              >
                {label}
              </Link>
            </h3>
            <p className="tabular-nums mt-0.5 text-[0.78rem] text-[var(--kf-muted,#5a646d)]">
              {group.size} {group.size === 1 ? "frame" : "frames"}
              {typeof group.span_s === "number" && group.span_s >= 0.05 ? ` in ${group.span_s.toFixed(1)} s` : ""}
            </p>
          </div>
          {group.pending_approval ? (
            <span className="shrink-0 rounded-full bg-[#fdf1de] px-2 py-0.5 text-[0.75rem] font-semibold text-[#8a5200] ring-1 ring-[#f3d9ae] ring-inset">Needs you</span>
          ) : tie || close ? (
            <span className="tabular-nums shrink-0 rounded-full bg-[var(--kf-surface-2,#f5f7f9)] px-2 py-0.5 text-[0.75rem] font-medium text-[var(--kf-muted,#5a646d)] ring-1 ring-[var(--kf-border,#dfe4e8)] ring-inset" title={closeCallHint(group)}>
              {tie ? "Tie" : typeof group.margin === "number" ? `${fmtGap(group.margin)} gap` : closeCallText(group)}
            </span>
          ) : null}
        </div>
        <VerdictBar counts={group.verdict_counts} legend="numbers" />
        <Reasons counts={group.reason_counts} />
      </div>
    </li>
  );
}

/** Fixed 16:10 media box laid out by frame count, so every card in a row has the same height. */
function Mosaic({ best, others, hidden }: { best: Member; others: Member[]; hidden: number }) {
  if (!others.length) {
    return (
      <div className="aspect-[16/10] overflow-hidden">
        <Thumb m={best} big />
      </div>
    );
  }
  if (others.length === 1) {
    return (
      <div className="grid aspect-[16/10] grid-cols-2 grid-rows-[minmax(0,1fr)] gap-0.5 overflow-hidden bg-[var(--kf-border,#dfe4e8)]">
        <Thumb m={best} big />
        <Thumb m={others[0]} />
      </div>
    );
  }
  return (
    <div
      className="grid aspect-[16/10] grid-cols-[2fr_1fr] gap-0.5 overflow-hidden bg-[var(--kf-border,#dfe4e8)]"
      style={{ gridTemplateRows: `repeat(${others.length}, minmax(0, 1fr))` }}
    >
      <div style={{ gridRow: `span ${others.length}` }} className="min-h-0">
        <Thumb m={best} big />
      </div>
      {others.map((m, i) => (
        <Thumb key={m.photo_id} m={m} overlay={i === others.length - 1 && hidden > 0 ? `+${hidden}` : undefined} />
      ))}
    </div>
  );
}

/** "Best of group" is on every group, so it is noise here; show the two most frequent other reasons. */
// Burst-level reasons sit on nearly every group, so they are noise on the card (the group view lists them).
// Low confidence is what "review" already means, so it adds nothing next to the review count.
const QUIET = new Set(["KEEP_BEST_OF_GROUP", "BURST_NOT_BEST", "KEEP_SINGLETON", "REVIEW_LOW_CONFIDENCE"]);
function Reasons({ counts }: { counts?: Record<string, number> }) {
  const entries = Object.entries(counts ?? {})
    .filter(([c]) => !QUIET.has(c))
    .sort((a, b) => b[1] - a[1]);
  if (!entries.length) return null;
  const shown = Object.fromEntries(entries.slice(0, 2));
  const more = entries.length - 2;
  return (
    <div className="relative z-10 mt-auto flex flex-wrap items-center gap-1.5">
      <ReasonChips counts={shown} />
      {more > 0 && (
        <span className="text-[0.75rem] font-medium text-[var(--kf-muted,#5a646d)]" title={entries.slice(2).map(([c, n]) => `${c} ${n}`).join(", ")}>
          +{more} more
        </span>
      )}
    </div>
  );
}

function Thumb({ m, big = false, overlay }: { m: Member; big?: boolean; overlay?: string }) {
  const [state, setState] = useState<"loading" | "ok" | "error">("loading");
  const why = useImageFailure(m.proxy_url, state === "error");
  return (
    <div className="relative h-full min-h-0 w-full overflow-hidden bg-[var(--kf-surface-2,#eef1f4)]">
      {state !== "error" ? (
        // eslint-disable-next-line @next/next/no-img-element
        <img
          src={m.proxy_url}
          alt=""
          loading="lazy"
          decoding="async"
          referrerPolicy="no-referrer"
          onLoad={() => setState("ok")}
          onError={() => setState("error")}
          className={`h-full w-full object-cover transition-opacity duration-300 motion-reduce:transition-none ${state === "ok" ? "opacity-100" : "opacity-0"}`}
        />
      ) : (
        <PhotoUnavailable why={why} compact={!big} />
      )}
      {state === "loading" && <span aria-hidden="true" className="absolute inset-0 animate-pulse bg-[var(--kf-surface-2,#eef1f4)] motion-reduce:animate-none" />}
      {/* Same marks as the groups grid and the burst view: verdict badge top-left, crown top-right. */}
      {m.verdict && !overlay && <VerdictMark verdict={m.verdict} size={big ? "md" : "sm"} ring={false} />}
      {big && m.is_best && <CrownBadge />}
      {overlay && (
        <span className="tabular-nums absolute inset-0 grid place-items-center bg-black/70 text-[0.85rem] font-semibold text-white">{overlay}</span>
      )}
    </div>
  );
}

export function GroupCardSkeleton() {
  return (
    <li aria-hidden="true" className={`${card} overflow-hidden`}>
      <div className="aspect-[16/10] animate-pulse bg-[var(--kf-surface-2,#eef1f4)] motion-reduce:animate-none" />
      <div className="space-y-2.5 p-4">
        <div className="h-3.5 w-24 rounded bg-[var(--kf-surface-2,#eef1f4)]" />
        <div className="h-3 w-32 rounded bg-[var(--kf-surface-2,#eef1f4)]" />
        <div className="h-1.5 w-full rounded-full bg-[var(--kf-surface-2,#eef1f4)]" />
      </div>
    </li>
  );
}
