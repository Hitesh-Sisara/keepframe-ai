"use client";

import { faceCrop, faceScale, plainReason, scoreBars, type FaceInfo, type ScoreBar, type Scores } from "@/lib/cull";
import { decisionLine, isBadged, isLeadTest, isPerson, isScripted, SCRIPTED_LABEL } from "@/lib/decision";
import { CLOSE_BELOW, fmtGap as gapText, TIE_BELOW } from "@/lib/closecall";
import type { Member } from "@/lib/types";
import { Tooltip } from "../ui";
import { SignedImg } from "../Frame";
import { ReasonChips } from "../ReasonChip";
import { parseReason } from "@/lib/reasons";
import { LeadTestBadge } from "../States";
import { CrownGlyph, VERDICT_COLOR, VERDICT_WORD, VerdictGlyph } from "../VerdictMark";

const TONE_COLOR: Record<ScoreBar["tone"], string> = {
  good: "var(--kf-keep)",
  warn: "var(--kf-review)",
  bad: "var(--kf-reject)",
  none: "var(--kf-border-strong)",
};

/** Right-hand panel for the selected frame: verdict in words, why, scores as bars, faces as crops. */
export function Inspector({
  m,
  scores,
  proxySize,
  bestId,
  margin,
  provisional,
  bestAvailableFace,
}: {
  /** The group's approval is still open: the keep isn't final. */
  provisional?: boolean;
  /** Face row (1-based) that has a closed eye in every frame, the best pick included. */
  bestAvailableFace?: number;
  m: Member;
  scores: Scores;
  proxySize?: [number, number];
  bestId?: string;
  margin?: number;
}) {
  // Person-decided rows carry no machine reason; fall back to the reason codes' plain description.
  const said = plainReason(m.reason_text) || codeSentence(m.reason_codes);
  // "Chosen as the best frame of its group." only restates the Best pick pill.
  const reason = m.is_best && /^(chosen as )?(the )?best frame of (its|the) group\.?$/i.test(said.trim()) ? "" : said;
  const conf = typeof m.confidence === "number" ? Math.round(m.confidence * 100) : undefined;
  const bars = scoreBars(scores);
  // "Best of group" restates the Best pick pill; the reason sentence already says it.
  const chips = (m.reason_codes ?? []).filter(
    (c) => !(m.is_best && c === "KEEP_BEST_OF_GROUP") && !(c === "BURST_NOT_BEST" && /weaker frame of its burst/i.test(reason)),
  );
  return (
    <div className="flex flex-col gap-5">
      <section aria-label="Verdict">
        <div className="flex flex-wrap items-center gap-2">
          {m.verdict ? (
            <span
              className="inline-flex h-7 items-center gap-1.5 rounded-full pr-3 pl-2 text-[0.8125rem] font-semibold text-white"
              style={{ background: VERDICT_COLOR[m.verdict] }}
            >
              <VerdictGlyph verdict={m.verdict} className="h-3.5 w-3.5" />
              {provisional && m.verdict === "keep" ? "Provisional keep" : VERDICT_WORD[m.verdict]}
            </span>
          ) : (
            <span className="inline-flex h-7 items-center rounded-full bg-[var(--kf-surface-2)] px-3 text-[0.8125rem] font-medium text-[var(--kf-muted)]">Undecided</span>
          )}
          {m.is_best && (
            <span className="inline-flex h-7 items-center gap-1.5 rounded-full bg-[#fdf3d7] pr-3 pl-2 text-[0.8125rem] font-semibold text-[#5c4300]">
              <CrownGlyph className="h-3.5 w-3.5 text-[#c99400]" />
              Best pick
            </span>
          )}
        </div>

        {/* One line; the standing and the numbers behind it sit in the info tooltip. */}
        <p className="mt-3 flex items-start gap-1 text-[0.875rem] leading-snug font-medium text-[var(--kf-text)]">
          <span className="min-w-0 line-clamp-2" title={reason || undefined}>
            {reason || (m.is_best ? "" : m.verdict === "review" ? "Needs a second look" : m.verdict ? "No reason recorded" : "No verdict yet")}
          </span>
          <Tooltip
            className="shrink-0"
            label={
              <span className="block text-left">
                {[summaryLine(m, margin, conf), !m.is_best && bestId && m.verdict === "reject" ? `Best pick: ${bestId} (B).` : "", plainConfidence(conf, m),
                  typeof scores.best_score === "number" ? `Score ${(scores.best_score * 100).toFixed(1)}/100.` : ""].filter(Boolean).join(" ")}
              </span>
            }
          >
            <button type="button" aria-label="How sure: the numbers behind this" className="inline-grid h-5 w-5 place-items-center rounded-full text-[var(--kf-muted)] hover:bg-[var(--kf-surface-2)] hover:text-[var(--kf-text)]">
              <svg viewBox="0 0 16 16" className="h-3.5 w-3.5" aria-hidden="true">
                <circle cx="8" cy="8" r="6.2" fill="none" stroke="currentColor" strokeWidth="1.4" />
                <path d="M8 7.2v3.8M8 5v.01" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" />
              </svg>
            </button>
          </Tooltip>
        </p>
        {bestAvailableFace !== undefined && (
          <p className="mt-1.5 text-[0.8125rem] font-medium text-[var(--kf-review)]">Best available: every frame has a closed eye on face {bestAvailableFace}</p>
        )}
        {isScripted(m.decision_source) && (
          <span className="mt-2 inline-flex h-6 items-center rounded-full bg-[var(--kf-review-soft)] px-2.5 text-[0.75rem] font-medium text-[var(--kf-review)]" title={SCRIPTED_LABEL}>
            Test run<span className="sr-only">: {SCRIPTED_LABEL}</span>
          </span>
        )}
        {isLeadTest(m.decision_source) && <LeadTestBadge className="mt-2" />}
        {m.human_note && isPerson(m.decision_source) && (
          <blockquote className="mt-2 border-l-2 border-[var(--kf-border-strong)] pl-3 text-[0.8125rem] text-[var(--kf-text)]">“{m.human_note}”</blockquote>
        )}
        {chips.length > 0 && (
          <div className="mt-3">
            <ReasonChips codes={chips} />
          </div>
        )}
      </section>

      <section aria-labelledby="scores-h">
        <h3 id="scores-h" className="t-eyebrow mb-2.5">
          Quality
        </h3>
        <ul className="flex flex-col gap-2">
          {bars.map((b) => (
            <Bar key={b.key} bar={b} />
          ))}
        </ul>
      </section>

      <FaceChips m={m} scores={scores} proxySize={proxySize} />
    </div>
  );
}

/** The one sentence a reviewer reads first: where this frame stands, in words. */
function summaryLine(m: Member, margin: number | undefined, conf: number | undefined): string {
  if (m.is_best) {
    if (typeof margin !== "number") return "Best in group.";
    if (margin < TIE_BELOW) return "Best, tied with runner-up.";
    return margin < CLOSE_BELOW ? `Best by ${gapText(margin)}: close call.` : `Best by ${gapText(margin)}.`;
  }
  if (typeof m.rank === "number") return `Ranked #${m.rank}.`;
  return "";
}

function codeSentence(codes?: string[]): string {
  const c = (codes ?? [])[0];
  if (!c) return "";
  const { def, arg } = parseReason(c);
  if (def.description === "Unrecognised reason code") return "";
  return `${def.description}${arg ? ` (${arg})` : ""}.`;
}

function plainConfidence(conf: number | undefined, m: Member): string {
  if (isBadged(m.decision_source)) return conf !== undefined ? `The agent proposed this at ${conf}% confidence.` : "";
  if (m.decision_source === "agent" || !m.decision_source) {
    if (conf === undefined) return "Decided by the agent.";
    const word = conf >= 90 ? "very sure" : conf >= 75 ? "fairly sure" : conf >= 55 ? "unsure" : "not sure";
    return `The agent was ${word} (${conf}% confidence).`;
  }
  const line = decisionLine(m.decision_source, m.confidence);
  return `${line[0].toUpperCase()}${line.slice(1)}.`;
}

function Bar({ bar }: { bar: ScoreBar }) {
  const pct = bar.value === undefined ? 0 : Math.max(3, Math.min(100, bar.value * 100));
  return (
    <li title={bar.hint} className="grid grid-cols-[76px_minmax(0,1fr)] items-center gap-x-3 text-[0.8125rem]">
      <span className="font-medium text-[var(--kf-text)]">{bar.label.replace("Eyes open", "Eyes")}</span>
      <span className="min-w-0">
        <span
          className="block h-[6px] overflow-hidden rounded-full bg-[var(--kf-surface-2)] ring-1 ring-inset ring-[var(--kf-border)]"
          role="meter"
          aria-label={bar.label}
          aria-valuemin={0}
          aria-valuemax={100}
          aria-valuenow={bar.value === undefined ? undefined : Math.round(bar.value * 100)}
          aria-valuetext={bar.text}
        >
          <span className="block h-full rounded-full" style={{ width: `${pct}%`, background: TONE_COLOR[bar.tone] }} />
        </span>
        <span className={`mt-0.5 block truncate text-[0.75rem] ${bar.tone === "good" ? "sr-only" : ""} ${bar.tone === "bad" ? "font-medium text-[var(--kf-reject)]" : bar.tone === "warn" ? "font-medium text-[var(--kf-review)]" : "text-[var(--kf-muted)]"}`}>
          {bar.text.replace(/^avg /, "")}
        </span>
      </span>
    </li>
  );
}

const EYE_WORD: Record<string, string> = { open: "Eyes open", partial: "Eyes half open", closed: "Eyes closed", unknown: "Too small to read" };

/** One eye shut and the other open reads as a wink, not "half open": name the closed eye. */
function eyeWord(state: string, eyes: number[]): string {
  if (eyes.length === 2) {
    const [l, r] = eyes;
    if (l < 0.2 && r >= 0.5) return "One eye shut";
    if (r < 0.2 && l >= 0.5) return "One eye shut";
  }
  return EYE_WORD[state] ?? state;
}

function FaceChips({ m, scores, proxySize }: { m: Member; scores: Scores; proxySize?: [number, number] }) {
  const faces = scores.faces;
  if (!faces.length) {
    return (
      <section aria-labelledby="faces-h">
        <h3 id="faces-h" className="t-eyebrow mb-2">
          Faces
        </h3>
        <p className="t-small text-[var(--kf-muted)]">None</p>
      </section>
    );
  }
  const k = proxySize ? faceScale(scores, proxySize[0], proxySize[1]) : 1;
  return (
    <section aria-labelledby="faces-h">
      <h3
        id="faces-h"
        className="t-eyebrow mb-2.5"
        title={
          [
            (scores.sharp_label === "borderline" || scores.sharp_label === "blurry") && faces.some((f) => f.state === "closed") ? "Blurred frame: eye reads less certain." : "",
            faces.some((f) => f.state === "unknown") ? "Faces too small for eyes fall back to sharpness." : "",
          ]
            .filter(Boolean)
            .join(" ") || undefined
        }
      >
        Faces <span className="t-data ml-1 font-medium tracking-normal text-[var(--kf-faint)] normal-case">{faces.length}</span>
      </h3>
      <ul className="grid grid-cols-[repeat(auto-fill,92px)] gap-2.5">
        {faces.map((f, i) => (
          <FaceChip key={f.face_id} n={i + 1} face={f} src={m.proxy_url} scale={k} proxySize={proxySize} />
        ))}
      </ul>
    </section>
  );
}

function FaceChip({ n, face, src, scale, proxySize }: { n: number; face: FaceInfo; src: string; scale: number; proxySize?: [number, number] }) {
  const S = 92;
  const state = face.state ?? "unknown";
  const tone = state === "closed" ? "var(--kf-reject)" : state === "partial" ? "var(--kf-review)" : state === "open" ? "var(--kf-keep)" : "var(--kf-faint)";
  let crop: React.CSSProperties | undefined;
  if (face.bbox_px && proxySize) {
    const { cx, cy, side } = faceCrop(face.bbox_px, scale);
    const z = S / side;
    crop = { width: proxySize[0] * z, height: proxySize[1] * z, left: -(cx - side / 2) * z, top: -(cy - side / 2) * z, maxWidth: "none" };
  }
  const eyes = [face.left_open_p, face.right_open_p].filter((v): v is number => typeof v === "number");
  return (
    <li className="w-[92px] min-w-0" title={[eyes.length === 2 ? `L ${pct(eyes[0])} · R ${pct(eyes[1])}` : face.min_px ? `${Math.round(face.min_px)} px face` : "", typeof face.fiqa === "number" ? `Quality ${pct(face.fiqa)}` : ""].filter(Boolean).join(" · ") || undefined}>
      <div
        className="relative h-[92px] w-[92px] overflow-hidden rounded-[8px] bg-[var(--kf-surface-2)]"
        style={{ boxShadow: `inset 0 0 0 2px ${face.main_subject ? "var(--kf-accent)" : "transparent"}` }}
      >
        {crop ? (
          <div className="absolute" style={crop}>
            <SignedImg src={src} alt="" />
          </div>
        ) : (
          <div className="absolute inset-0 grid place-items-center text-[var(--kf-muted)]" title="No crop: photo not loaded">
            <svg viewBox="0 0 24 24" className="h-7 w-7" fill="none" stroke="currentColor" strokeWidth="1.5" aria-hidden="true">
              <circle cx="12" cy="9" r="4" />
              <path d="M4 21c1-4 4-6 8-6s7 2 8 6" />
            </svg>
          </div>
        )}
        <span className={`t-data absolute top-1 left-1 grid h-4 min-w-4 place-items-center rounded-full px-1 text-[0.625rem] font-semibold text-white ${face.main_subject ? "bg-[var(--kf-accent)]" : "bg-black/60"}`}>
          {n}
        </span>
      </div>
      <p className="mt-1.5 flex items-center gap-1.5 text-[0.75rem] font-medium text-[var(--kf-text)]">
        <span aria-hidden="true" className="h-2 w-2 shrink-0 rounded-full" style={{ background: tone }} />
        <span className="min-w-0 leading-tight">{eyeWord(state, eyes)}</span>
        {face.main_subject && <span className="sr-only">, main subject</span>}
      </p>
      <p className="sr-only">
        {eyes.length === 2 ? `L ${pct(eyes[0])} · R ${pct(eyes[1])}` : face.min_px ? `${Math.round(face.min_px)} px face` : ""}
        {typeof face.fiqa === "number" && `, quality ${pct(face.fiqa)}`}
      </p>
    </li>
  );
}

const pct = (v: number) => `${Math.round(v * 100)}%`;
