// Story mode: the agent's trace for one burst, collapsed into a few plain-language steps ("Faces found →
// eyes closed on Frame 2 → compared burst → asked you"). Pure: lines + pivots in, steps out.

import { fmtGap } from "./closecall.ts";
import type { DecisionSource, Pivot, TraceLine } from "./types";

export type StoryKind = "start" | "sharp" | "exposure" | "faces" | "eyes" | "quality" | "rank" | "ask" | "answer" | "write" | "other";

export interface StoryStep {
  kind: StoryKind;
  /** One short clause, ≤ 10 words ("eyes closed on Frame 2"): the headline chain and the step list. */
  short: string;
  /** One plain sentence, for the step's tooltip. */
  text: string;
  /** The trace lines this step stands for (call, result, reasoning), for "show in timeline". */
  seqs: number[];
  /** This step happened because a result changed the plan (a pivot's consequence). */
  pivot?: { index: number; kind: string };
  tone: "normal" | "warn" | "ask";
}

type R = Record<string, unknown>;
type PerPhoto = R & { photo_id?: string };

const per = (r?: R): PerPhoto[] => (Array.isArray(r?.per_photo) ? (r.per_photo as PerPhoto[]) : []);
const list = (ids: string[]) => (ids.length <= 2 ? ids.join(" and ") : `${ids.slice(0, -1).join(", ")} and ${ids[ids.length - 1]}`);
const plural = (n: number, one: string, many = `${one}s`) => `${n} ${n === 1 ? one : many}`;

export function buildStory(lines: TraceLine[], pivots: Pivot[], names: Record<string, string> = {}): StoryStep[] {
  const name = (id?: unknown) => (typeof id === "string" ? (names[id] ?? "a frame") : "a frame");
  const byCall = new Map(pivots.map((p, i) => [p.call_seq, { index: i + 1, kind: p.kind }]));
  const reasoningAfter = (seq: number, until: number) => lines.filter((l) => l.type === "model_reasoning" && l.seq > seq && l.seq < until).map((l) => l.seq);
  const calls = lines.filter((l) => l.type === "tool_call");
  const out: StoryStep[] = [];

  calls.forEach((call, ci) => {
    const nextCall = calls[ci + 1]?.seq ?? Number.POSITIVE_INFINITY;
    const res = lines.find((l) => l.type === "tool_result" && l.tool === call.tool && l.seq > call.seq && l.seq < nextCall);
    const r = res?.result as R | undefined;
    const args = (call.args ?? {}) as R;
    const seqs = [call.seq, ...(res ? [res.seq] : []), ...reasoningAfter(call.seq, nextCall)];
    const pivot = byCall.get(call.seq);
    const push = (kind: StoryKind, short: string, text: string, tone: StoryStep["tone"] = "normal") => out.push({ kind, short, text, seqs, pivot, tone });
    const rows = per(r);

    switch (call.tool) {
      case "next_work_item": {
        const n = Array.isArray(r?.photos) ? (r.photos as unknown[]).length : undefined;
        push("start", "picked up burst", n ? `Picked up this burst of ${n} frames.` : "Picked up this burst.");
        break;
      }
      case "score_sharpness": {
        const original = args.variant === "original";
        const faces = args.roi === "faces";
        const soft = rows.filter((p) => p.label === "blurry" || p.label === "borderline");
        const motion = soft.some((p) => p.blur_kind === "motion");
        if (original) {
          const who = list(rows.map((p) => name(p.photo_id)));
          if (soft.length)
            push("sharp", `re-checked ${who} full size: soft`, `Re-checked ${who} on the full-size original. Still soft${motion ? " (motion blur)" : ""}.`, "warn");
          else push("sharp", `re-checked ${who} full size: sharp`, `Re-checked ${who} on the full-size original. Sharp after all.`);
        } else if (soft.length) {
          const who = list(soft.map((p) => name(p.photo_id)));
          push("sharp", `${who} soft`, `Measured sharpness${faces ? " on the faces" : ""}. ${who} ${soft.length === 1 ? "looks" : "look"} soft${motion ? " (motion blur)" : ""}.`, "warn");
        } else push("sharp", faces ? "face sharpness OK" : "sharpness OK", `Measured sharpness${faces ? " on the faces" : " on the whole frame"}${rows.length ? `: all ${rows.length} frames pass` : ""}.`);
        break;
      }
      case "score_exposure": {
        const bad = rows.filter((p) => p.label && p.label !== "ok");
        if (bad.length) push("exposure", `${list(bad.map((p) => name(p.photo_id)))} badly exposed`, `Checked exposure. ${list(bad.map((p) => `${name(p.photo_id)} is ${String(p.label).replace("_", " ")}`))}.`, "warn");
        else push("exposure", "exposure OK", "Checked exposure: fine on every frame.");
        break;
      }
      case "detect_faces": {
        const n = Math.max(0, ...rows.map((p) => (typeof p.n_faces === "number" ? p.n_faces : Array.isArray(p.faces) ? (p.faces as unknown[]).length : 0)));
        push("faces", n ? "faces found" : "no faces", n ? `Looked for faces: ${plural(n, "person", "people")} in the burst.` : "Looked for faces and found none, so sharpness and exposure decide.");
        break;
      }
      case "score_eyes_open": {
        const closed = rows.filter((p) => p.any_main_subject_closed === true).map((p) => name(p.photo_id));
        if (closed.length) push("eyes", `eyes closed on ${list(closed)}`, `Checked everyone's eyes. The main subject blinked on ${list(closed)}.`, "warn");
        else push("eyes", "eyes open", "Checked everyone's eyes: the main subject's are open on every frame.");
        break;
      }
      case "score_face_quality":
        push("quality", "rated faces", "Rated how clear each face is.");
        break;
      case "pick_best_in_cluster": {
        const rank = Array.isArray(r?.ranking) ? (r.ranking as PerPhoto[]) : [];
        const best = name(r?.best_photo_id ?? rank[0]?.photo_id);
        const gap = typeof r?.margin === "number" ? r.margin * 100 : undefined;
        const second = rank[1] ? name(rank[1].photo_id) : undefined;
        const close = gap !== undefined && gap < 5;
        push(
          "rank",
          close && second ? `compared burst: ${best} by ${fmtGap(gap / 100)}` : `compared burst: ${best} wins`,
          close && second
            ? `Compared the whole burst. ${best} ranked first, but only ${fmtGap(gap / 100)} ahead of ${second}.`
            : `Compared the whole burst. ${best} ranked first${gap !== undefined ? `, ${fmtGap(gap / 100)} clear` : ""}.`,
          close ? "warn" : "normal",
        );
        break;
      }
      case "make_contact_sheet":
        // A helper for the approval request; the "asked you" step says it.
        if (out.length) out[out.length - 1].seqs.push(...seqs);
        break;
      case "request_human_approval": {
        const answer = lines.find((l) => (l.type === "interrupt_resumed" || (l.type === "tool_result" && l.tool === "request_human_approval")) && l.seq > call.seq);
        const src = (answer && (answer.answered_by && answer.answered_by !== "unknown" ? answer.answered_by : (answer as unknown as R).human_source)) as DecisionSource | undefined;
        const scripted = src === "scripted" || src === "lead_test";
        push("ask", scripted ? "asked for review" : "asked you", "Too close to call alone, so it asked a person to confirm.", "ask");
        if (answer) {
          out.push({
            kind: "answer",
            short: scripted ? "auto-answered (test run)" : "you answered",
            text: scripted ? "Test run: the proposal was accepted automatically." : "A person answered.",
            seqs: lines.filter((l) => l.seq >= answer.seq && l.seq < nextCall).map((l) => l.seq),
            tone: "normal",
          });
        }
        break;
      }
      case "write_decision": {
        const vc = (r?.verdict_counts ?? {}) as Record<string, number>;
        const parts = (["keep", "review", "reject"] as const).filter((v) => vc[v]).map((v) => `${vc[v]} ${v === "review" ? "to review" : v}`);
        push("write", "wrote verdicts", parts.length ? `Wrote the verdicts: ${parts.join(", ")}.` : "Wrote the verdicts.");
        break;
      }
      default:
        push("other", call.tool ? call.tool.replace(/_/g, " ") : "worked", `Ran ${call.tool?.replace(/_/g, " ") ?? "a step"}.`);
    }
  });
  // Consecutive steps that say the same thing (two passes of the same check) read as one.
  return out.filter((s, i) => i === 0 || s.short !== out[i - 1].short || s.pivot);
}

/** The headline: the steps that carry information, joined with arrows. Skips routine bookkeeping. */
export function storyHeadline(steps: StoryStep[]): string[] {
  const keep = steps.filter((s) => s.kind !== "start" && s.kind !== "quality" && s.kind !== "write" && !(s.kind === "exposure" && s.tone === "normal"));
  return keep.map((s, i) => (i === 0 ? s.short[0].toUpperCase() + s.short.slice(1) : s.short));
}
