import type { ApiError } from "@/lib/api";
import { btnSecondary, card } from "./tokens";

/** Designed error/empty panels for the events pages (the shared ErrorNote stays for other pages). */
export function ErrorPanel({ error, retry }: { error?: ApiError; retry?: () => void }) {
  if (!error) return null;
  const text =
    error.status === 403
      ? "Needs the team token."
      : error.status === 0
        ? "Can't reach the server."
        : error.message;
  return (
    <div role="alert" className={`${card} flex items-center gap-4 px-5 py-4`}>
      <span aria-hidden="true" className="grid h-8 w-8 shrink-0 place-items-center rounded-full bg-[#fbe6e8] text-[#a8232f]">
        <svg viewBox="0 0 16 16" className="h-4 w-4">
          <path d="M8 4.5v4M8 11v.5" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" />
        </svg>
      </span>
      <p className="flex-1 text-[0.875rem] text-[var(--kf-text,#1e2327)]">{text}</p>
      {retry && error.status !== 403 && (
        <button type="button" className={btnSecondary} onClick={retry}>
          Try again
        </button>
      )}
    </div>
  );
}

export function EmptyPanel({ title, body, icon }: { title: string; body?: React.ReactNode; icon?: React.ReactNode }) {
  return (
    <div className={`${card} flex flex-col items-center px-6 py-12 text-center`}>
      <span aria-hidden="true" className="grid h-10 w-10 place-items-center rounded-full bg-[var(--kf-accent-soft,#e8eefc)] text-[var(--kf-accent,#2457d6)]">
        {icon ?? (
          <svg viewBox="0 0 20 20" className="h-5 w-5">
            <rect x="3" y="4.5" width="14" height="11" rx="2" fill="none" stroke="currentColor" strokeWidth="1.5" />
            <path d="M3.5 13l4-3 3 2 3-2.5 3 2.5" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinejoin="round" />
          </svg>
        )}
      </span>
      <p className="mt-3 text-[0.95rem] font-semibold text-[var(--kf-text,#1e2327)]">{title}</p>
      {body && <p className="mt-1 max-w-[46ch] text-[0.85rem] text-[var(--kf-muted,#5a646d)]">{body}</p>}
    </div>
  );
}
