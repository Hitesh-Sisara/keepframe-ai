"use client";

import { useRouter } from "next/navigation";
import { trapTab } from "@/lib/a11y";
import { useEffect, useMemo, useRef, useState } from "react";
import { EmptyGlyph, Kbd } from "./ui";

export interface Command {
  id: string;
  group: string;
  label: string;
  hint?: string;
  href?: string;
  run?: () => void;
  icon?: React.ReactNode;
}

/** ⌘K palette: filter, arrow keys to move, Enter to run, Esc to close. */
export function CommandPalette({ commands, onClose }: { commands: Command[]; onClose: () => void }) {
  const router = useRouter();
  const ref = useRef<HTMLDialogElement>(null);
  const listRef = useRef<HTMLUListElement>(null);
  const [q, setQ] = useState("");
  const [active, setActive] = useState(0);

  useEffect(() => {
    const d = ref.current;
    d?.showModal();
    return () => d?.close();
  }, []);

  const results = useMemo(() => {
    const needle = q.trim().toLowerCase();
    if (!needle) return commands;
    // Exact label first, then label prefix, then a word in the label, then anywhere, then fuzzy. Within a tier,
    // sections stay together and labels sort naturally ("Group 2" before "Group 10").
    const order = new Map<string, number>();
    for (const c of commands) if (!order.has(c.group)) order.set(c.group, order.size);
    return commands
      .map((c) => {
        const label = c.label.toLowerCase();
        const hay = `${c.label} ${c.hint ?? ""} ${c.group}`.toLowerCase();
        const score =
          label === needle ? 0 : wordPrefix(label, needle) ? 1 : label.startsWith(needle) ? 1.5 : label.includes(` ${needle}`) ? 2 : hay.includes(needle) ? 3 : fuzzy(hay, needle) ? 4 : -1;
        return { c, score };
      })
      .filter((r, _, all) => r.score >= 0 && (r.score < 4 || !all.some((x) => x.score >= 0 && x.score < 4)))
      .sort(
        (a, b) =>
          a.score - b.score ||
          (order.get(a.c.group) ?? 0) - (order.get(b.c.group) ?? 0) ||
          a.c.label.localeCompare(b.c.label, "en", { numeric: true }),
      )
      .map((r) => r.c);
  }, [q, commands]);

  useEffect(() => setActive(0), [q]);
  useEffect(() => {
    listRef.current?.querySelector(`[data-i="${active}"]`)?.scrollIntoView({ block: "nearest" });
  }, [active]);

  function run(c: Command | undefined) {
    if (!c) return;
    onClose();
    if (c.run) c.run();
    else if (c.href) router.push(c.href);
  }

  function onKey(e: React.KeyboardEvent) {
    if (e.key === "ArrowDown") {
      e.preventDefault();
      setActive((a) => Math.min(a + 1, results.length - 1));
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setActive((a) => Math.max(a - 1, 0));
    } else if (e.key === "Enter") {
      e.preventDefault();
      run(results[active]);
    }
  }

  let lastGroup = "";
  return (
    <dialog
      ref={ref}
      // StrictMode re-runs the effect: the cleanup's close() fires its event after showModal() reopened it.
      onClose={() => !ref.current?.open && onClose()}
      onClick={(e) => e.target === ref.current && onClose()}
      aria-label="Command palette"
      // Keys typed in the modal stay in the modal: page shortcuts listen on window (Esc on a group page would
      // otherwise close this AND navigate back to the grid; Z / arrows would act on the hidden page). ⌘K still
      // reaches the Shell so it toggles the palette.
      onKeyDown={(e) => {
        if (!((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k")) e.stopPropagation();
        // showModal makes the page inert, but Tab can still leave for the browser chrome: cycle inside.
        trapTab(e, ref.current);
      }}
      className="kf-pop mx-auto mt-[12vh] w-[min(560px,calc(100vw-2rem))] overflow-hidden rounded-[var(--kf-radius-lg)] border border-[var(--kf-border)] bg-[var(--kf-surface)] p-0 text-[var(--kf-text)] shadow-[var(--kf-shadow-lg)]"
    >
      <div className="flex items-center gap-2.5 border-b border-[var(--kf-border)] px-4">
        <svg viewBox="0 0 20 20" className="h-[18px] w-[18px] shrink-0 text-[var(--kf-muted)]" aria-hidden="true">
          <circle cx="9" cy="9" r="5.5" fill="none" stroke="currentColor" strokeWidth="1.6" />
          <path d="m13.2 13.2 3.3 3.3" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" />
        </svg>
        <input
          autoFocus
          value={q}
          onChange={(e) => setQ(e.target.value)}
          onKeyDown={onKey}
          placeholder="Jump to a page, event, group or run…"
          aria-label="Search commands"
          role="combobox"
          aria-expanded="true"
          aria-controls="kf-palette-list"
          aria-activedescendant={results[active] ? `kf-cmd-${results[active].id}` : undefined}
          className="h-[52px] min-w-0 flex-1 bg-transparent text-[0.9375rem] outline-none placeholder:text-[var(--kf-faint)]"
        />
        <button type="button" onClick={onClose} className="btn btn-ghost btn-sm -mr-2 sm:hidden">
          Cancel
        </button>
      </div>
      <ul
        ref={listRef}
        id="kf-palette-list"
        role="listbox"
        aria-label="Results"
        className="max-h-[min(60vh,calc(100dvh-14rem))] overflow-y-auto overscroll-contain p-2"
      >
        {results.length === 0 && (
          <li className="flex flex-col items-center gap-2 pt-8 pb-4 text-center text-[var(--kf-muted)]">
            <EmptyGlyph icon="search" />
            <span className="t-small">Nothing matches “{q}”. Try a page, an event, or “Group 12”.</span>
            <span className="mt-2 flex flex-wrap justify-center gap-1.5">
              {commands
                .filter((c) => c.group === "Go to")
                .map((c) => (
                  <button key={c.id} type="button" onClick={() => run(c)} className="btn btn-secondary btn-sm">
                    {c.label}
                  </button>
                ))}
            </span>
          </li>
        )}
        {results.map((c, i) => {
          const header = c.group !== lastGroup ? c.group : null;
          lastGroup = c.group;
          const on = i === active;
          return (
            <li key={c.id} role="presentation">
              {header && <p className="t-eyebrow px-2.5 pt-2.5 pb-1.5">{header}</p>}
              <div
                id={`kf-cmd-${c.id}`}
                role="option"
                aria-selected={on}
                data-i={i}
                onMouseMove={() => setActive(i)}
                onClick={() => run(c)}
                className={`flex h-10 cursor-pointer items-center gap-3 rounded-[var(--kf-radius-sm)] px-2.5 text-[0.875rem] ${
                  on ? "bg-[var(--kf-accent-soft)] text-[var(--kf-accent-text)]" : "text-[var(--kf-text)]"
                }`}
              >
                <span className={on ? "text-[var(--kf-accent)]" : "text-[var(--kf-muted)]"}>{c.icon}</span>
                <span className="flex-1 truncate font-medium">
                  <Highlight text={c.label} q={q} />
                </span>
                {c.hint && <span className="t-small truncate text-[var(--kf-muted)]">{c.hint}</span>}
                {on && (
                  <svg viewBox="0 0 16 16" className="kf-kbd-hint h-3.5 w-3.5" aria-hidden="true">
                    <path
                      d="M13 4v4.5a1.5 1.5 0 0 1-1.5 1.5H3.5M6 7l-2.7 3L6 13"
                      fill="none"
                      stroke="currentColor"
                      strokeWidth="1.5"
                      strokeLinecap="round"
                      strokeLinejoin="round"
                    />
                  </svg>
                )}
              </div>
            </li>
          );
        })}
      </ul>
      <div className="kf-kbd-hint t-small hidden items-center gap-4 border-t sm:flex border-[var(--kf-border)] bg-[var(--kf-surface-2)] px-4 py-2 text-[var(--kf-muted)]">
        {results.length > 0 && (
          <>
            <span className="flex items-center gap-1.5">
              <Kbd keys={["↑", "↓"]} /> move
            </span>
            <span className="flex items-center gap-1.5">
              <Kbd>↵</Kbd> open
            </span>
          </>
        )}
        <span className="t-data ml-auto" aria-live="polite">
          {q.trim() ? `${results.length} of ${commands.length}` : `${commands.length} items`}
        </span>
        <span className="flex items-center gap-1.5">
          <Kbd>Esc</Kbd> close
        </span>
      </div>
    </dialog>
  );
}

/** "group 1" matches "Group 1" and "Group 1 · x" but not "Group 12": the needle ends on a word boundary. */
function wordPrefix(label: string, needle: string): boolean {
  if (!label.startsWith(needle)) return false;
  const next = label.charAt(needle.length);
  return next === "" || !/[a-z0-9]/i.test(next);
}

function fuzzy(hay: string, needle: string): boolean {
  let j = 0;
  for (let i = 0; i < hay.length && j < needle.length; i++) if (hay[i] === needle[j]) j++;
  return j === needle.length;
}

function Highlight({ text, q }: { text: string; q: string }) {
  const n = q.trim();
  const at = n ? text.toLowerCase().indexOf(n.toLowerCase()) : -1;
  if (at < 0) return <>{text}</>;
  return (
    <>
      {text.slice(0, at)}
      <mark className="bg-transparent font-bold text-inherit">{text.slice(at, at + n.length)}</mark>
      {text.slice(at + n.length)}
    </>
  );
}
