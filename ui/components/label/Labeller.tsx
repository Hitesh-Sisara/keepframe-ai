"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ErrorNote } from "@/components/States";
import { PageHeader } from "@/components/ui";
import { byPhotoId, isUnsure, toggleUnsure } from "@/components/label/notes";
import { api, ApiError } from "@/lib/api";
import { setBest, toCsv, toggleReason, type Label } from "@/lib/labels";
import { FAMILY_STYLE, REASONS, reasonByKey } from "@/lib/reasons";
import type { Group, GroupsPage, Member } from "@/lib/types";
import { ShortcutButton, ShortcutSheet, type ShortcutGroup } from "@/components/groups/Shortcuts";
import { EXPIRED_TEXT, PhotoUnavailable, probeImage, useImageFailure } from "@/components/States";

const KEYED = REASONS.filter((r) => r.key);
const UNDO_LIMIT = 200;
/** Viewer height: fill the window under the top bar and filmstrip, within sane bounds. */
const VIEW_H = "clamp(400px, calc(100dvh - 400px), 1100px)";

function storeKey(eventId: string) {
  return `keepframe.labels.${eventId}`;
}

function useAllGroups(eventId: string) {
  const [groups, setGroups] = useState<Group[]>();
  const [error, setError] = useState<ApiError>();
  const [tick, setTick] = useState(0);
  useEffect(() => {
    let live = true;
    setError(undefined);
    (async () => {
      const out: Group[] = [];
      let cursor: string | null | undefined;
      try {
        for (let i = 0; i < 200; i++) {
          const page = await api.get<GroupsPage>(`/events/${encodeURIComponent(eventId)}/groups`, { limit: 100, cursor: cursor ?? undefined });
          out.push(...page.items);
          cursor = page.next_cursor;
          if (!cursor) break;
        }
        // Capture order inside each burst; the agent's ranking must not steer the labeller (LABEL_GUIDE: label from the photo).
        if (live) setGroups(out.map((g) => ({ ...g, members: [...g.members].sort(byPhotoId) })));
      } catch (e) {
        if (live) setError(e instanceof ApiError ? e : new ApiError(0, String(e)));
      }
    })();
    return () => {
      live = false;
    };
  }, [eventId, tick]);
  return { groups, error, retry: () => setTick((t) => t + 1) };
}

type SaveState = { kind: "idle" } | { kind: "saving" } | { kind: "saved"; at: Date } | { kind: "failed" };
interface Snapshot {
  labels: Label[];
  gi: number;
  pi: number;
  action: string;
}

export function Labeller({ eventId, eventName, picker }: { eventId: string; eventName: string; picker?: React.ReactNode }) {
  const { groups, error, retry } = useAllGroups(eventId);
  const [labels, setLabels] = useState<Label[]>([]);
  const [gi, setGi] = useState(0);
  const [pi, setPi] = useState(0);
  const [labeller, setLabeller] = useState("");
  const [save, setSave] = useState<SaveState>({ kind: "idle" });
  const [exportOpen, setExportOpen] = useState(false);
  const [flash, setFlash] = useState<string>();
  const undo = useRef<Snapshot[]>([]);
  const [lastAction, setLastAction] = useState<string>();
  const [zoom, setZoom] = useState(false);
  const [keysOpen, setKeysOpen] = useState(false);
  const closeKeys = useCallback(() => setKeysOpen(false), []);
  const [undoCount, setUndoCount] = useState(0);
  const seeded = useRef(false);

  // Seed one row per frame (agent groups as the starting group_id_human), merged with saved work.
  useEffect(() => {
    if (!groups) return;
    let saved: Label[] = [];
    try {
      saved = JSON.parse(window.sessionStorage.getItem(storeKey(eventId)) ?? "[]");
    } catch {
      saved = [];
    }
    const byId = new Map(saved.map((l) => [l.photo_id, l]));
    const seededLabels = groups.flatMap((g) =>
      g.members.map(
        (m) => byId.get(m.photo_id) ?? { photo_id: m.photo_id, group_id_human: g.group_id, reasons_human: [], best_of_group_human: false, notes: "" },
      ),
    );
    setLabels(seededLabels);
    setLabeller(window.sessionStorage.getItem("keepframe.labeller") ?? "");
    // Resume at the first unlabelled frame.
    const done = new Set(seededLabels.filter((l) => l.verdict_human).map((l) => l.photo_id));
    outer: for (let g = 0; g < groups.length; g++)
      for (let p = 0; p < groups[g].members.length; p++)
        if (!done.has(groups[g].members[p].photo_id)) {
          setGi(g);
          setPi(p);
          break outer;
        }
    seeded.current = true;
  }, [groups, eventId]);

  // Autosave to this tab's sessionStorage, debounced, with a visible state.
  const firstSave = useRef(true);
  useEffect(() => {
    if (!seeded.current || !labels.length) return;
    if (firstSave.current) {
      firstSave.current = false; // the seed itself is not a change
      return;
    }
    setSave({ kind: "saving" });
    const t = window.setTimeout(() => {
      try {
        window.sessionStorage.setItem(storeKey(eventId), JSON.stringify(labels));
        setSave({ kind: "saved", at: new Date() });
      } catch {
        setSave({ kind: "failed" });
      }
    }, 350);
    return () => window.clearTimeout(t);
  }, [labels, eventId]);

  const group = groups?.[gi];
  const member = group?.members[pi];
  const label = labels.find((l) => l.photo_id === member?.photo_id);
  const byPhoto = useMemo(() => new Map(labels.map((l) => [l.photo_id, l])), [labels]);
  const done = useMemo(() => labels.filter((l) => l.verdict_human).length, [labels]);
  const frameIndex = useMemo(() => {
    if (!groups) return 0;
    let n = 0;
    for (let g = 0; g < gi; g++) n += groups[g].members.length;
    return n + pi;
  }, [groups, gi, pi]);

  // Pure transition from the current labels; the undo stack is pushed outside the state updater so React's
  // strict-mode double invoke can't record a change twice.
  const commit = useCallback(
    (next: (ls: Label[]) => Label[], action: string, note?: string) => {
      const out = next(labels);
      if (out === labels) return;
      undo.current.push({ labels, gi, pi, action });
      if (undo.current.length > UNDO_LIMIT) undo.current.shift();
      setUndoCount(undo.current.length);
      setLastAction(action);
      setLabels(out);
      if (note) setFlash(note);
    },
    [gi, labels, pi],
  );

  const update = useCallback(
    (fn: (l: Label) => Label, action: string, note?: string) => {
      if (!member) return;
      const now = new Date().toISOString();
      commit((ls) => ls.map((l) => (l.photo_id === member.photo_id ? { ...fn(l), labelled_at: now } : l)), action, note);
    },
    [commit, member],
  );

  const doUndo = useCallback(() => {
    const snap = undo.current.pop();
    setUndoCount(undo.current.length);
    setLastAction(undo.current[undo.current.length - 1]?.action);
    if (!snap) return;
    setLabels(snap.labels);
    setGi(snap.gi);
    setPi(snap.pi);
    setFlash(`Undid ${snap.action}`);
  }, []);

  const step = useCallback(
    (d: 1 | -1) => {
      if (!groups || !group) return;
      const np = pi + d;
      if (np >= 0 && np < group.members.length) return setPi(np);
      const ng = gi + d;
      if (ng < 0 || ng >= groups.length) return;
      setGi(ng);
      setPi(d === 1 ? 0 : groups[ng].members.length - 1);
    },
    [gi, group, groups, pi],
  );

  const nextUnlabelled = useCallback(() => {
    if (!groups) return;
    const flat = groups.flatMap((g, a) => g.members.map((m, b) => ({ a, b, id: m.photo_id })));
    const start = frameIndex + 1;
    for (let i = 0; i < flat.length; i++) {
      const f = flat[(start + i) % flat.length];
      if (!byPhoto.get(f.id)?.verdict_human) {
        setGi(f.a);
        setPi(f.b);
        return;
      }
    }
    setFlash("Every frame is labelled");
  }, [byPhoto, frameIndex, groups]);

  const setVerdict = useCallback(
    (v: "keep" | "reject") => {
      // Keeps carry no reject reasons (LABEL_GUIDE rule 3), so keep clears them and moves on; reject stays for reasons.
      update((l) => ({ ...l, verdict_human: v, reasons_human: v === "keep" ? [] : l.reasons_human }), v, v === "keep" ? "Kept" : "Rejected. Add reasons with 1–9");
      if (v === "keep") step(1);
    },
    [step, update],
  );

  const reason = useCallback(
    (code: string) =>
      update(
        (l) => ({ ...toggleReason(l, code), verdict_human: "reject" }),
        reasonByCodeLabel(code),
        `${label?.reasons_human.includes(code) ? "Removed" : "Rejected:"} ${reasonByCodeLabel(code)}`,
      ),
    [label?.reasons_human, update],
  );

  const best = useCallback(() => {
    if (!member) return;
    const now = new Date().toISOString();
    commit((ls) => setBest(ls, member.photo_id).map((l) => (l.group_id_human === label?.group_id_human ? { ...l, labelled_at: l.labelled_at ?? now } : l)), "best of group");
  }, [commit, label?.group_id_human, member]);

  const unsure = useCallback(
    () => update((l) => ({ ...l, notes: toggleUnsure(l.notes) }), "unsure", isUnsure(label?.notes ?? "") ? "Unsure cleared" : "Marked unsure"),
    [label?.notes, update],
  );

  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      const t = e.target as HTMLElement;
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "z" && !["INPUT", "TEXTAREA"].includes(t.tagName)) {
        e.preventDefault();
        return doUndo();
      }
      if (["INPUT", "TEXTAREA", "SELECT"].includes(t.tagName) || e.metaKey || e.ctrlKey || e.altKey) return;
      if (keysOpen) return;
      if (e.key === "?") {
        e.preventDefault();
        return setKeysOpen(true);
      }
      const k = e.key.toLowerCase();
      const act = (fn: () => void) => {
        e.preventDefault();
        fn();
      };
      if (k === "k") act(() => setVerdict("keep"));
      else if (k === "r") act(() => setVerdict("reject"));
      else if (k === "u") act(unsure);
      else if (k === "b") act(best);
      else if (k === "z") act(() => setZoom((z) => !z));
      else if (e.key === "Backspace") act(doUndo);
      else if (e.key === "Escape" && zoom) act(() => setZoom(false));
      else if (k === "n") act(nextUnlabelled);
      else if (/^[1-9]$/.test(e.key)) {
        const def = reasonByKey(Number(e.key));
        if (def) act(() => reason(def.code));
      } else if (e.key === "ArrowRight" || (k === " " && t.tagName !== "BUTTON")) act(() => step(1));
      else if (e.key === "ArrowLeft") act(() => step(-1));
      else if (e.key === "ArrowDown" && groups) act(() => (setGi((g) => Math.min(g + 1, groups.length - 1)), setPi(0)));
      else if (e.key === "ArrowUp") act(() => (setGi((g) => Math.max(g - 1, 0)), setPi(0)));
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [best, doUndo, groups, nextUnlabelled, reason, setVerdict, step, unsure, zoom, keysOpen]);

  // Clear the action toast after a moment.
  useEffect(() => {
    if (!flash) return;
    const t = window.setTimeout(() => setFlash(undefined), 1600);
    return () => window.clearTimeout(t);
  }, [flash, labels]);

  useEffect(() => setZoom(false), [member?.photo_id]);

  // Preload the next frame so moving on feels instant.
  useEffect(() => {
    if (!groups || !group) return;
    const next = group.members[pi + 1] ?? groups[gi + 1]?.members[0];
    if (next?.proxy_url) {
      const img = new Image();
      img.referrerPolicy = "no-referrer";
      img.src = next.proxy_url;
    }
  }, [gi, group, groups, pi]);

  const csvHref = useMemo(() => {
    if (!labels.length || !exportOpen) return "";
    return URL.createObjectURL(new Blob([toCsv(labels, labeller.trim() || "unknown")], { type: "text/csv;charset=utf-8" }));
  }, [labels, labeller, exportOpen]);
  useEffect(
    () => () => {
      if (csvHref) URL.revokeObjectURL(csvHref);
    },
    [csvHref],
  );

  if (error) return <ErrorNote error={error} retry={retry} />;
  if (!groups) return <LabelSkeleton />;
  if (!groups.length || !group || !member || !label)
    return (
      <div className="kf-card mx-auto mt-10 max-w-[520px] px-8 py-10 text-center">
        <p className="text-[1.125rem] font-semibold">Nothing to label yet</p>
      </div>
    );

  const pct = labels.length ? (done / labels.length) * 100 : 0;
  const verdict = label.verdict_human;
  const flagged = isUnsure(label.notes);
  const groupName = group.group_label ?? `Group ${gi + 1}`;

  const navControls = (
    <div className="flex flex-wrap items-center gap-1.5">
      <NavButton onClick={() => step(-1)} label="Previous frame" kbd="←">
        <path d="M10 3 5 8l5 5" />
      </NavButton>
      <NavButton onClick={() => step(1)} label="Next frame" kbd="→">
        <path d="m6 3 5 5-5 5" />
      </NavButton>
      <button
        type="button"
        onClick={nextUnlabelled}
        title="Next unlabelled (N)"
        aria-keyshortcuts="N"
        className="kf-focus inline-flex h-8 items-center gap-2 rounded-[8px] border border-(--p-border) bg-(--p-surface) px-2.5 text-[0.8125rem] font-medium outline-none hover:bg-(--p-surface-2)"
      >
        <span className="whitespace-nowrap">Next unlabelled</span>
      </button>
      <button
        type="button"
        onClick={doUndo}
        disabled={!undoCount}
        aria-label={undoCount ? `Undo ${lastAction ?? "last change"}` : "Nothing to undo"}
        title={undoCount ? `Undo ${lastAction ?? "last change"} (⌘Z)` : "Nothing to undo"}
        className="kf-focus inline-flex h-8 items-center gap-1.5 rounded-[8px] border border-(--p-border) bg-(--p-surface) px-2.5 text-[0.8125rem] font-medium outline-none hover:bg-(--p-surface-2) disabled:cursor-not-allowed disabled:opacity-45"
      >
        <svg aria-hidden="true" viewBox="0 0 16 16" className="h-4 w-4">
          <path d="M4 6h6a3 3 0 0 1 0 6H7M4 6l3-3M4 6l3 3" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" />
        </svg>
        Undo
      </button>
    </div>
  );

  return (
    <div className="flex flex-col gap-4 max-lg:pb-48">
      {/* ---------- Header: the shared page-header pattern ---------- */}
      <PageHeader
        className="!mb-0"
        title={eventName}
        subtitle={
          <div className="flex flex-wrap items-center gap-x-5 gap-y-2">
            <div className="flex min-w-[220px] items-center gap-3" aria-label="Progress">
              <p className="kf-num shrink-0 text-[0.875rem]">
                <span className="font-semibold text-(--p-text)">{done}</span>/{labels.length}
                <span className="sr-only"> frames labelled</span>
              </p>
              <div
                role="progressbar"
                aria-label="Frames labelled"
                aria-valuemin={0}
                aria-valuemax={labels.length}
                aria-valuenow={done}
                className="h-1.5 w-[160px] overflow-hidden rounded-full bg-(--p-border)"
              >
                <div className="h-full rounded-full bg-(--p-accent) transition-[width] duration-300" style={{ width: `${pct}%` }} />
              </div>
            </div>
            <SaveBadge save={save} />
          </div>
        }
        actions={
          <>
            {picker}
            <span className="kf-touch-hide">
              <ShortcutButton onClick={() => setKeysOpen(true)} />
            </span>

        <div className="relative">
          <button
            type="button"
            aria-expanded={exportOpen}
            aria-controls="export-panel"
            onClick={() => setExportOpen((o) => !o)}
            className={`btn ${done > 0 ? "btn-primary" : "btn-secondary"}`}
          >
            <svg aria-hidden="true" viewBox="0 0 16 16" className="h-4 w-4">
              <path d="M8 2v8m0 0L5 7m3 3 3-3M3 12.5h10" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" />
            </svg>
            Export CSV
          </button>
          {exportOpen && (
            <ExportPanel
              done={done}
              labeller={labeller}
              setLabeller={(v) => {
                setLabeller(v);
                window.sessionStorage.setItem("keepframe.labeller", v);
              }}
              href={csvHref}
              eventId={eventId}
              onClose={() => setExportOpen(false)}
            />
          )}
        </div>
          </>
        }
      />

      {/* ---------- Workspace ---------- */}
      <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_340px]">
        <div className="flex min-w-0 flex-col gap-3">
          <BigPhoto member={member} frameNo={pi + 1} verdict={verdict} best={label.best_of_group_human} unsure={flagged} flash={flash} zoom={zoom} onZoom={setZoom} />
          <Filmstrip
            group={group}
            groupName={groupName}
            gi={gi}
            groupsTotal={groups.length}
            pi={pi}
            byPhoto={byPhoto}
            onPick={setPi}
            nav={navControls}
          />
        </div>

        <aside aria-label="Label controls" className="flex flex-col gap-3 lg:sticky lg:top-4 lg:self-start">
          <section aria-label="Verdict" className="kf-card p-3 max-lg:fixed max-lg:inset-x-3 max-lg:bottom-3 max-lg:z-30 max-lg:shadow-xl">
            {label.verdict_human && (
              <div className="px-1 pb-2.5">
                <StatusPill label={label} />
              </div>
            )}
            <div className="grid grid-cols-2 gap-2">
              <VerdictButton kind="keep" active={verdict === "keep"} onClick={() => setVerdict("keep")} />
              <VerdictButton kind="reject" active={verdict === "reject"} onClick={() => setVerdict("reject")} />
            </div>
            <div className="mt-2 grid grid-cols-2 gap-2">
              <ToggleButton active={flagged} onClick={unsure} kbd="U" label="Unsure" tone="review" hint="Unsure (U): flags it, still make the call" />
              <ToggleButton active={label.best_of_group_human} onClick={best} kbd="B" label="Best of group" tone="accent" hint="Best of group (B): one per group" />
            </div>
          </section>

          <section aria-labelledby="reasons-h" className="kf-card p-3">
            <h2 id="reasons-h" className="sr-only">
              Reasons (picking one rejects the frame)
            </h2>
            <ul className="grid grid-cols-2 gap-1.5">
              {KEYED.map((r) => {
                const on = label.reasons_human.includes(r.code);
                const s = FAMILY_STYLE[r.family];
                return (
                  <li key={r.code}>
                    <button
                      type="button"
                      aria-pressed={on}
                      title={`${r.description} (${r.key})`}
                      aria-keyshortcuts={String(r.key)}
                      onClick={() => reason(r.code)}
                      className="kf-focus kf-touch-target flex h-9 w-full items-center gap-2 rounded-[8px] border px-2.5 text-left text-[0.8125rem] font-medium outline-none transition-colors hover:border-(--p-border-strong)"
                      style={
                        on
                          ? { background: s.tint, color: s.ink, borderColor: s.rule, boxShadow: `inset 0 0 0 1px ${s.rule}` }
                          : { background: "var(--p-surface)", color: "var(--p-text)", borderColor: "var(--p-border)" }
                      }
                    >
                      <span aria-hidden="true" className="h-2 w-2 shrink-0 rounded-full" style={{ background: on ? s.rule : "var(--p-border-strong)" }} />
                      <span className="truncate">{r.label}</span>
                    </button>
                  </li>
                );
              })}
            </ul>
          </section>

          <section className="kf-card p-3">
            <label className="block px-1">
              <span className="text-[0.875rem] font-medium">Notes</span>
              <textarea
                rows={2}
                value={label.notes}
                onChange={(e) => update((l) => ({ ...l, notes: e.target.value }), "note")}
                onKeyDown={(e) => e.key === "Escape" && (e.target as HTMLTextAreaElement).blur()}
                className="kf-focus mt-1.5 w-full resize-none rounded-[8px] border border-(--p-border) bg-(--p-surface) px-2.5 py-2 text-[0.875rem] outline-none placeholder:text-(--p-faint)"
              />
            </label>
          </section>
        </aside>
      </div>
      <ShortcutSheet open={keysOpen} onClose={closeKeys} groups={LABEL_KEYS} />
    </div>
  );
}

/* ---------- Pieces ---------- */

function SaveBadge({ save }: { save: SaveState }) {
  const text =
    save.kind === "saving"
      ? "Saving…"
      : save.kind === "saved"
        ? "Saved"
        : save.kind === "failed"
          ? "Couldn’t save. Export now."
          : "Saved";
  const tone = save.kind === "failed" ? "text-(--p-reject)" : "text-(--p-muted)";
  return (
    <p
      role="status"
      aria-live="polite"
      title={`Labels autosave in this browser tab${save.kind === "saved" ? ` (last saved ${save.at.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })})` : ""}. Export the CSV before you close the tab.`}
      className={`inline-flex items-center gap-2 text-[0.875rem] ${tone}`}
    >
      {save.kind === "saving" ? (
        <span aria-hidden="true" className="h-3.5 w-3.5 animate-spin rounded-full border-2 border-(--p-border-strong) border-t-(--p-accent) motion-reduce:animate-none" />
      ) : save.kind === "failed" ? (
        <span aria-hidden="true" className="h-2 w-2 rounded-full bg-(--p-reject)" />
      ) : (
        <svg aria-hidden="true" viewBox="0 0 16 16" className="h-4 w-4 text-(--p-keep)">
          <path d="m3.5 8.5 3 3 6-7" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" />
        </svg>
      )}
      <span className="kf-num">{text}</span>
    </p>
  );
}

function BigPhoto({
  member,
  frameNo,
  verdict,
  best,
  unsure,
  flash,
  zoom,
  onZoom,
}: {
  member: Member;
  frameNo: number;
  verdict?: "keep" | "reject";
  best: boolean;
  unsure: boolean;
  flash?: string;
  zoom: boolean;
  onZoom: (z: boolean) => void;
}) {
  const [state, setState] = useState<"loading" | "ok" | "error">("loading");
  const why = useImageFailure(member.proxy_url, state === "error");
  const [ratio, setRatio] = useState(3 / 2);
  const [origin, setOrigin] = useState("50% 50%");
  // An image can finish loading before React attaches onLoad (cached, or before hydration): check on mount too.
  const imgRef = useCallback((im: HTMLImageElement | null) => {
    if (im?.complete && im.naturalWidth) {
      setRatio(im.naturalWidth / im.naturalHeight);
      setState("ok");
    }
  }, []);
  useEffect(() => setState("loading"), [member.proxy_url]);
  const ring = verdict === "keep" ? "ring-(--p-keep)" : verdict === "reject" ? "ring-(--p-reject)" : "ring-transparent";
  return (
    <div className="kf-viewer grid place-items-center rounded-(--p-radius) bg-[#16181d]" style={{ ["--view-h" as string]: VIEW_H, ["--ratio" as string]: ratio }}>
      <figure
        className={`relative grid h-full max-h-full w-auto max-w-full place-items-center overflow-hidden rounded-[6px] ring-[3px] transition-[box-shadow] ${ring} ${
          state === "ok" ? (zoom ? "cursor-zoom-out" : "cursor-zoom-in") : ""
        }`}
        style={{ aspectRatio: ratio }}
        onClick={() => state === "ok" && onZoom(!zoom)}
        onPointerMove={(e) => {
          if (!zoom) return;
          const r = e.currentTarget.getBoundingClientRect();
          setOrigin(`${((e.clientX - r.left) / r.width) * 100}% ${((e.clientY - r.top) / r.height) * 100}%`);
        }}
      >
        {state === "loading" && <div aria-hidden="true" className="kf-skel absolute inset-0 opacity-10" />}
        {state === "error" ? (
          <div className="relative h-[240px] w-[360px] max-w-full">
            <PhotoUnavailable why={why} onDark />
          </div>
        ) : (
          // eslint-disable-next-line @next/next/no-img-element
          <img
            key={member.proxy_url}
            ref={imgRef}
            src={member.proxy_url}
            alt={`Frame ${frameNo}`}
            decoding="async"
            referrerPolicy="no-referrer"
            onLoad={(e) => {
              const im = e.currentTarget;
              if (im.naturalWidth && im.naturalHeight) setRatio(im.naturalWidth / im.naturalHeight);
              setState("ok");
            }}
            onError={() => setState("error")}
            className={`h-full w-full object-contain transition-[opacity,transform] duration-150 motion-reduce:transition-none ${state === "ok" ? "opacity-100" : "opacity-0"}`}
            style={{ transform: zoom ? "scale(2.5)" : undefined, transformOrigin: origin }}
          />
        )}
        <figcaption className="pointer-events-none absolute top-3 left-3 flex flex-wrap gap-2">
          {verdict && (
            <span
              className={`inline-flex items-center gap-1.5 rounded-full px-3 py-1 text-[0.875rem] font-semibold text-white shadow-md ${
                verdict === "keep" ? "bg-(--p-keep)" : "bg-(--p-reject)"
              }`}
            >
              <VerdictIcon kind={verdict} />
              {verdict === "keep" ? "Keep" : "Reject"}
            </span>
          )}
          {best && <span className="rounded-full bg-(--p-accent) px-3 py-1 text-[0.875rem] font-semibold text-white shadow-md">Best of group</span>}
          {unsure && <span className="rounded-full bg-(--p-review) px-3 py-1 text-[0.875rem] font-semibold text-white shadow-md">Unsure</span>}
        </figcaption>
        {state === "ok" && (
          <span className="pointer-events-none absolute top-3 right-3 inline-flex items-center gap-1.5 rounded-full bg-black/60 px-2.5 py-1 text-[0.75rem] font-medium text-white backdrop-blur">
            <span className="kf-num">{zoom ? "2.5×" : "Fit"}</span>
          </span>
        )}
        <p
          role="status"
          aria-live="polite"
          className={`pointer-events-none absolute bottom-4 left-1/2 -translate-x-1/2 rounded-full bg-black/75 px-4 py-1.5 text-[0.875rem] font-medium whitespace-nowrap text-white backdrop-blur transition-opacity duration-200 ${
            flash ? "opacity-100" : "opacity-0"
          }`}
        >
          {flash ?? ""}
        </p>
      </figure>
    </div>
  );
}

function StatusPill({ label }: { label: Label }) {
  const v = label.verdict_human;
  const reasons = label.reasons_human.map(reasonByCodeLabel);
  const text = !v ? "Not labelled" : v === "keep" ? "Kept" : `Rejected${reasons.length ? ` · ${reasons.join(", ")}` : ""}`;
  const tone = !v ? "bg-(--p-surface-2) text-(--p-muted)" : v === "keep" ? "bg-(--p-keep-soft) text-(--p-keep)" : "bg-(--p-reject-soft) text-(--p-reject)";
  return (
    <span className={`inline-flex max-w-[230px] items-center gap-1.5 truncate rounded-full px-2.5 py-0.5 text-[0.8125rem] font-semibold ${tone}`} title={text}>
      <span aria-hidden="true" className={`h-1.5 w-1.5 shrink-0 rounded-full ${!v ? "bg-(--p-faint)" : v === "keep" ? "bg-(--p-keep)" : "bg-(--p-reject)"}`} />
      <span className="truncate">{text}</span>
    </span>
  );
}

function reasonByCodeLabel(code: string): string {
  return REASONS.find((r) => r.code === code.split(":", 1)[0])?.label ?? code;
}

function VerdictIcon({ kind }: { kind: "keep" | "reject" }) {
  return (
    <svg aria-hidden="true" viewBox="0 0 16 16" className="h-4 w-4">
      {kind === "keep" ? (
        <path d="m3.5 8.5 3 3 6-7" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
      ) : (
        <path d="m4.5 4.5 7 7m0-7-7 7" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
      )}
    </svg>
  );
}

function VerdictButton({ kind, active, onClick }: { kind: "keep" | "reject"; active: boolean; onClick: () => void }) {
  const keep = kind === "keep";
  return (
    <button
      type="button"
      aria-pressed={active}
      onClick={onClick}
      aria-keyshortcuts={keep ? "K" : "R"}
      title={keep ? "Keep (K)" : "Reject (R)"}
      className={`kf-focus group flex h-16 items-center justify-center rounded-[9px] px-4 text-[1.0625rem] font-semibold outline-none transition-colors ${
        active
          ? keep
            ? "bg-(--p-keep) text-white"
            : "bg-(--p-reject) text-white"
          : // Unchosen: neutral surface like the other controls, so nothing looks pre-selected. Colour only on hover/choice.
            keep
            ? "border border-(--p-border) bg-(--p-surface) text-(--p-text) hover:border-(--p-keep) hover:bg-(--p-keep-soft)/50"
            : "border border-(--p-border) bg-(--p-surface) text-(--p-text) hover:border-(--p-reject) hover:bg-(--p-reject-soft)/50"
      }`}
    >
      <span className="inline-flex items-center gap-2">
        <span className={active ? "" : keep ? "text-(--p-keep)" : "text-(--p-reject)"}>
          <VerdictIcon kind={kind} />
        </span>
        {keep ? "Keep" : "Reject"}
      </span>
    </button>
  );
}

function ToggleButton({
  active,
  onClick,
  kbd,
  label,
  tone,
  hint,
}: {
  active: boolean;
  onClick: () => void;
  kbd: string;
  label: string;
  tone: "review" | "accent";
  hint: string;
}) {
  const on = tone === "review" ? "bg-(--p-review-soft) text-(--p-review) border-(--p-review)" : "bg-(--p-accent-soft) text-(--p-accent-text) border-(--p-accent)";
  return (
    <button
      type="button"
      aria-pressed={active}
      onClick={onClick}
      title={hint}
      aria-keyshortcuts={kbd}
      className={`kf-focus flex h-10 items-center justify-center rounded-[8px] border px-3 text-[0.875rem] font-medium outline-none transition-colors ${
        active ? on : "border-(--p-border) bg-(--p-surface) text-(--p-text) hover:bg-(--p-surface-2)"
      }`}
    >
      {label}
    </button>
  );
}

function NavButton({ onClick, label, kbd, children }: { onClick: () => void; label: string; kbd: string; children: React.ReactNode }) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-label={label}
      title={`${label} (${kbd})`}
      className="kf-focus grid h-9 w-9 place-items-center rounded-[8px] border border-(--p-border) bg-(--p-surface) outline-none hover:bg-(--p-surface-2)"
    >
      <svg aria-hidden="true" viewBox="0 0 16 16" className="h-4 w-4" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
        {children}
      </svg>
    </button>
  );
}

function Filmstrip({
  group,
  groupName,
  gi,
  groupsTotal,
  pi,
  byPhoto,
  onPick,
  nav,
}: {
  group: Group;
  groupName: string;
  gi: number;
  groupsTotal: number;
  pi: number;
  byPhoto: Map<string, Label>;
  onPick: (i: number) => void;
  nav: React.ReactNode;
}) {
  const labelled = group.members.filter((m) => byPhoto.get(m.photo_id)?.verdict_human).length;
  return (
    <section aria-label="Frames in this group" className="kf-card px-3 pt-2.5 pb-3">
      <div className="flex flex-wrap items-center justify-between gap-x-3 gap-y-2 px-1 pb-2">
        <p className="text-[0.875rem]">
          <span className="font-semibold" title={groupName}>
            Group {gi + 1}
          </span>
          <span className="kf-num text-(--p-muted)">
            /{groupsTotal} · {labelled}/{group.members.length}
          </span>
        </p>
        {nav}
      </div>
      {/* Edge fade on the scroll axis so a cut-off frame reads as "more this way", not a clipping bug. */}
      <ol className="flex snap-x gap-2 overflow-x-auto px-0.5 pt-0.5 pb-1.5 [mask-image:linear-gradient(to_right,transparent,#000_12px,#000_calc(100%-24px),transparent)]">
        {group.members.map((m, i) => {
          const l = byPhoto.get(m.photo_id);
          const current = i === pi;
          return (
            <li key={m.photo_id} className="shrink-0 snap-start">
              <button
                type="button"
                onClick={() => onPick(i)}
                aria-current={current ? "true" : undefined}
                aria-label={`Frame ${i + 1}${l?.verdict_human ? `, ${l.verdict_human}` : ", not labelled"}${l?.best_of_group_human ? ", best of group" : ""}`}
                className={`kf-focus relative block h-[76px] w-[114px] overflow-hidden rounded-[7px] bg-(--p-surface-2) outline-none ring-offset-2 transition ${
                  current ? "ring-2 ring-(--p-accent)" : "opacity-70 hover:opacity-100"
                }`}
              >
                {/* eslint-disable-next-line @next/next/no-img-element */}
                <img
                  src={m.proxy_url}
                  alt=""
                  loading="lazy"
                  decoding="async"
                  referrerPolicy="no-referrer"
                  className="h-full w-full object-cover"
                  onError={(e) => {
                    const el = e.currentTarget;
                    void probeImage(m.proxy_url).then((w) => {
                      if (w !== "expired") return;
                      el.style.visibility = "hidden";
                      el.parentElement?.setAttribute("title", EXPIRED_TEXT);
                    });
                  }}
                />
                <span className="absolute bottom-1 left-1 rounded-[4px] bg-[rgb(0_0_0/0.78)] px-1.5 font-mono text-[0.75rem] font-semibold text-white">{i + 1}</span>
                {l?.verdict_human && (
                  <span
                    aria-hidden="true"
                    className={`absolute top-1 right-1 grid h-5 w-5 place-items-center rounded-full text-white shadow ${l.verdict_human === "keep" ? "bg-(--p-keep)" : "bg-(--p-reject)"}`}
                  >
                    <svg viewBox="0 0 16 16" className="h-3 w-3">
                      {l.verdict_human === "keep" ? (
                        <path d="m3.5 8.5 3 3 6-7" fill="none" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round" strokeLinejoin="round" />
                      ) : (
                        <path d="m4.5 4.5 7 7m0-7-7 7" fill="none" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round" />
                      )}
                    </svg>
                  </span>
                )}
                {l?.best_of_group_human && (
                  <span aria-hidden="true" className="absolute right-1 bottom-1 rounded-[4px] bg-(--p-accent) px-1 text-[0.6875rem] font-semibold text-white">
                    Best
                  </span>
                )}
              </button>
            </li>
          );
        })}
      </ol>
    </section>
  );
}

const LABEL_KEYS: ShortcutGroup[] = [
  { title: "Label", keys: [[["K"], "Keep"], [["R"], "Reject"], [["U"], "Unsure"], [["1–9"], "Reason"], [["B"], "Best of group"], [["⌘", "Z"], "Undo"]] },
  { title: "Move", keys: [[["←", "→"], "Frame"], [["↑", "↓"], "Group"], [["N"], "Next unlabelled"], [["Z"], "Zoom"], [["?"], "This sheet"]] },
];

function ExportPanel({
  done,
  labeller,
  setLabeller,
  href,
  eventId,
  onClose,
}: {
  done: number;
  labeller: string;
  setLabeller: (v: string) => void;
  href: string;
  eventId: string;
  onClose: () => void;
}) {
  const ready = done > 0 && labeller.trim().length > 0;
  const input = useRef<HTMLInputElement>(null);
  const box = useRef<HTMLDivElement>(null);
  useEffect(() => input.current?.focus(), []);
  useEffect(() => {
    const onDown = (e: PointerEvent) => {
      const t = e.target as Node;
      if (box.current && !box.current.contains(t) && !(t as HTMLElement).closest?.("[aria-controls='export-panel']")) onClose();
    };
    document.addEventListener("pointerdown", onDown);
    return () => document.removeEventListener("pointerdown", onDown);
  }, [onClose]);
  return (
    <div
      ref={box}
      id="export-panel"
      role="dialog"
      aria-label="Export labels"
      onKeyDown={(e) => e.key === "Escape" && onClose()}
      className="kf-card absolute top-[calc(100%+8px)] right-0 z-30 w-[320px] p-4 shadow-xl"
    >
      <label className="block">
        <span className="text-[0.875rem] font-medium">Labeller</span>
        <input
          ref={input}
          value={labeller}
          maxLength={24}
          placeholder="photographer:AB"
          onChange={(e) => setLabeller(e.target.value)}
          className="kf-focus mt-1.5 w-full rounded-[8px] border border-(--p-border) bg-(--p-surface) px-2.5 py-2 font-mono text-[0.875rem] outline-none"
        />
      </label>
      <a
        href={ready && href ? href : undefined}
        download={`${eventId}.csv`}
        aria-disabled={!ready}
        onClick={(e) => !ready && e.preventDefault()}
        className={`kf-focus mt-3 flex h-10 w-full items-center justify-center rounded-[8px] text-[0.9375rem] font-semibold outline-none ${
          ready ? "bg-(--p-accent) text-white hover:opacity-90" : "cursor-not-allowed bg-(--p-surface-2) text-(--p-muted)"
        }`}
      >
        Download {done} {done === 1 ? "row" : "rows"}
      </a>
      <p className="mt-2 text-[0.75rem] text-(--p-muted)">Saved in this tab only</p>
    </div>
  );
}

export function LabelSkeleton() {
  return (
    <div aria-busy="true" className="flex flex-col gap-4">
      <span className="sr-only" aria-live="polite">
        Loading frames
      </span>
      <div className="flex items-center gap-6">
        <div className="mr-auto">
          <div className="kf-skel h-3 w-20" />
          <div className="kf-skel mt-2 h-6 w-56" />
        </div>
        <div className="kf-skel h-8 w-72" />
        <div className="kf-skel h-9 w-28" />
      </div>
      <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_340px]">
        <div className="flex flex-col gap-3">
          <div className="kf-skel" style={{ height: VIEW_H }} />
          <div className="kf-skel h-[110px]" />
        </div>
        <div className="flex flex-col gap-3">
          <div className="kf-skel h-[170px]" />
          <div className="kf-skel h-[200px]" />
          <div className="kf-skel h-[130px]" />
        </div>
      </div>
    </div>
  );
}
