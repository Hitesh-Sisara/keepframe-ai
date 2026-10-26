"use client";

import { useEffect, useRef, useState } from "react";
import { api, ApiError } from "@/lib/api";
import { saveSession, startDemo } from "@/lib/session";
import type { Role } from "@/lib/types";
import { Button, LogoMark, Wordmark } from "./ui";

/** Token sign-in. The token is kept in sessionStorage only (see lib/session.ts). */
export function SignIn() {
  const [token, setToken] = useState("");
  const [show, setShow] = useState(false);
  const [error, setError] = useState<string>();
  const [busy, setBusy] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);
  // The app reserves a scrollbar gutter; the full-bleed sign-in screen never scrolls at desktop sizes, so the gutter
  // would show as a light strip beside the dark panel.
  useEffect(() => {
    const el = document.documentElement;
    const prev = el.style.scrollbarGutter;
    el.style.scrollbarGutter = "auto";
    return () => {
      el.style.scrollbarGutter = prev;
    };
  }, []);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    const t = token.trim();
    if (!t) {
      setError("Paste your token.");
      inputRef.current?.focus();
      return;
    }
    setBusy(true);
    setError(undefined);
    try {
      const h = await api.get<{ role: Role }>("/health", undefined, t);
      saveSession({ token: t, role: h.role });
    } catch (err) {
      setError(
        err instanceof ApiError && err.status === 401
          ? "Token not accepted."
          : err instanceof ApiError && err.status === 0
            ? "Can't reach the server. Try again."
            : "Couldn't check the token. Try again.",
      );
      inputRef.current?.focus();
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="grid min-h-dvh lg:grid-cols-[minmax(0,5fr)_minmax(0,7fr)]">
      <main className="flex flex-col px-6 py-8 sm:px-12 lg:px-16">
        <span className="flex self-start">
          <Wordmark size="lg" />
        </span>
        <div className="flex flex-1 items-start py-10 sm:items-center sm:py-12">
          <div className="w-full max-w-[420px]">
            <h1 className="text-[2rem] leading-[1.08] font-semibold tracking-[-0.035em] text-balance sm:text-[2.375rem]">
              AI culling for event photos.
            </h1>

            <div className="mt-10">
              <Button variant="primary" size="lg" className="w-full" onClick={startDemo} autoFocus>
                Explore the demo
              </Button>
            </div>

            <div className="mt-8 flex items-center gap-3 text-[0.75rem] font-medium text-[var(--kf-faint)]" aria-hidden="true">
              <span className="h-px flex-1 bg-[var(--kf-border)]" />
              or
              <span className="h-px flex-1 bg-[var(--kf-border)]" />
            </div>

            <form onSubmit={submit} noValidate className="mt-5">
              <label htmlFor="token" className="block text-[0.8125rem] font-semibold">
                Access token
              </label>
              <div className="relative mt-1.5">
                <input
                  ref={inputRef}
                  id="token"
                  name="token"
                  type={show ? "text" : "password"}
                  autoComplete="off"
                  spellCheck={false}
                  placeholder="Paste token"
                  aria-describedby={error ? "token-error token-help" : "token-help"}
                  aria-invalid={Boolean(error)}
                  value={token}
                  onChange={(e) => {
                    setToken(e.target.value);
                    if (error) setError(undefined);
                  }}
                  className="kf-input !h-10 !pr-16 [&:not(:placeholder-shown)]:font-mono"
                />
                <button
                  type="button"
                  onClick={() => setShow((s) => !s)}
                  aria-pressed={show}
                  aria-label={show ? "Hide token" : "Show token"}
                  className="absolute top-1/2 right-1.5 h-8 -translate-y-1/2 rounded-md px-2 text-[0.75rem] font-medium text-[var(--kf-muted)] hover:bg-[var(--kf-surface-2)] hover:text-[var(--kf-text)]"
                >
                  {show ? "Hide" : "Show"}
                </button>
              </div>
              <div className="min-h-[2.25rem] pt-1.5 pb-2">
                {error && (
                  <p id="token-error" role="alert" className="t-small flex items-center gap-1.5 font-medium text-[var(--kf-reject)]">
                    <svg viewBox="0 0 16 16" className="h-3.5 w-3.5 shrink-0" aria-hidden="true">
                      <circle cx="8" cy="8" r="6.5" fill="none" stroke="currentColor" strokeWidth="1.5" />
                      <path d="M8 4.8v3.6M8 10.9v.01" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" />
                    </svg>
                    {error}
                  </p>
                )}
              </div>
              <Button type="submit" variant="secondary" size="lg" loading={busy} className="w-full">
                {busy ? "Checking" : "Sign in"}
              </Button>
              <p id="token-help" className="t-small mt-3 text-[var(--kf-muted)]">
                Live event photos expire after 7 days. Explore the demo to see images.
              </p>
            </form>
          </div>
        </div>
      </main>
      <Showcase />
    </div>
  );
}

const BASE = (process.env.NEXT_PUBLIC_BASE_PATH ?? "").replace(/\/+$/, "");

/** Right-hand product panel: one real four-frame burst (licensed stills, public/signin/) with keep / review /
 *  reject marks, the way the desk shows a group. */
function Showcase() {
  const frames: { src: string; v: "keep" | "review" | "reject" }[] = [
    { src: "burst-1.webp", v: "review" },
    { src: "burst-2.webp", v: "review" },
    { src: "burst-3.webp", v: "reject" },
    { src: "burst-4.webp", v: "keep" },
  ];
  return (
    <aside aria-label="Product preview" className="relative hidden overflow-hidden bg-[#0f1a33] lg:block">
      <div className="absolute inset-0 bg-[radial-gradient(120%_80%_at_80%_0%,#2457d6_0%,transparent_55%),radial-gradient(90%_70%_at_0%_100%,#1b3a8a_0%,transparent_60%)] opacity-90" />
      <div className="relative flex h-full flex-col justify-center px-14 py-16">
        <div aria-hidden="true" className="mx-auto w-full max-w-[560px] rounded-[16px] 2xl:max-w-[680px] border border-white/10 bg-white/[0.06] p-4 shadow-[0_30px_80px_-20px_rgb(0_0_0/0.55)] backdrop-blur-sm">
          <div className="mb-3 flex items-center justify-between px-1">
            <span className="flex items-center gap-2 text-[0.8125rem] font-medium text-white/90">
              <LogoMark className="h-5 w-5" />
              4 frames
            </span>
            <span className="flex gap-1.5 text-[0.6875rem] font-medium">

            </span>
          </div>
          <div className="grid grid-cols-2 gap-2">
            {frames.map((f, i) => (
              <figure key={f.src} className="relative m-0 aspect-[3/2] overflow-hidden rounded-[8px] bg-white/10">
                {/* eslint-disable-next-line @next/next/no-img-element */}
                <img src={`${BASE}/signin/${f.src}`} alt="" width={560} height={373} decoding="async" className={`h-full w-full object-cover ${f.v === "reject" ? "opacity-45 grayscale-[40%]" : ""}`} />
                {f.v === "keep" && <div className="absolute inset-0 rounded-[8px] ring-[2.5px] ring-[#22c07a] ring-inset" />}
                {f.v === "review" && <div className="absolute inset-0 rounded-[8px] ring-[2.5px] ring-[#f0a531] ring-inset" />}
                {f.v === "reject" && <div className="absolute inset-0 rounded-[8px] ring-[2.5px] ring-[#e5484d]/70 ring-inset" />}
                <span className="absolute bottom-1.5 left-1.5 rounded-[5px] bg-[#0f1a33]/75 px-1.5 py-0.5 font-mono text-[0.625rem] text-white/85">{i + 1}</span>
                <span
                  className={`absolute top-1.5 right-1.5 grid h-5 w-5 place-items-center rounded-full ${f.v === "keep" ? "bg-[#22c07a]" : f.v === "review" ? "bg-[#f0a531]" : "bg-[#e5484d]"}`}
                >
                  <svg viewBox="0 0 12 12" className="h-3 w-3">
                    {f.v === "keep" && <path d="M3 6.2l2 2 4-4.4" fill="none" stroke="#fff" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" />}
                    {f.v === "review" && <path d="M4.6 3.4v5.2M7.4 3.4v5.2" fill="none" stroke="#fff" strokeWidth="1.7" strokeLinecap="round" />}
                    {f.v === "reject" && <path d="M3.5 3.5l5 5M8.5 3.5l-5 5" fill="none" stroke="#fff" strokeWidth="1.6" strokeLinecap="round" />}
                  </svg>
                </span>
              </figure>
            ))}
          </div>
        </div>
        <p className="mx-auto mt-6 w-full max-w-[560px] text-[0.75rem] leading-snug text-white/75 2xl:max-w-[680px]">
          Web Summit 2016,{" "}
          <a href="https://commons.wikimedia.org/wiki/File:Web_Summit_2016_-_Centre_Stage_-_Nov_10_-_Day_3_DSC_4064_(30259869143).jpg" target="_blank" rel="noopener noreferrer" className="underline decoration-white/40 underline-offset-2 hover:text-white focus-visible:rounded-sm focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-white">
            Wikimedia Commons
          </a>
          ,{" "}
          <a href="https://creativecommons.org/licenses/by/2.0/" target="_blank" rel="noopener noreferrer" className="underline decoration-white/40 underline-offset-2 hover:text-white focus-visible:rounded-sm focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-white">
            CC BY 2.0
          </a>

        </p>
      </div>
    </aside>
  );
}
