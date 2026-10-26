"use client";

import { useEffect, useState } from "react";

type Theme = "light" | "dark";
const KEY = "keepframe.theme";

/** Runs before first paint (inlined in <head>) so a pinned theme never flashes the other one. */
export const THEME_BOOT = `try{var t=localStorage.getItem("${KEY}");if(t==="light"||t==="dark")document.documentElement.dataset.theme=t}catch(e){}`;

function systemTheme(): Theme {
  return window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}

/** Light/dark switch. Follows the system until the user picks one; the pick is remembered per browser. */
export function ThemeToggle({ compact = false }: { compact?: boolean }) {
  const [theme, setTheme] = useState<Theme>("light");
  useEffect(() => {
    const pinned = document.documentElement.dataset.theme as Theme | undefined;
    setTheme(pinned ?? systemTheme());
    const mq = window.matchMedia("(prefers-color-scheme: dark)");
    const on = () => !document.documentElement.dataset.theme && setTheme(systemTheme());
    mq.addEventListener("change", on);
    return () => mq.removeEventListener("change", on);
  }, []);

  function flip() {
    const next: Theme = theme === "dark" ? "light" : "dark";
    setTheme(next);
    // Picking the system's own theme clears the pin, so the app keeps following the system afterwards.
    if (next === systemTheme()) {
      delete document.documentElement.dataset.theme;
      localStorage.removeItem(KEY);
    } else {
      document.documentElement.dataset.theme = next;
      localStorage.setItem(KEY, next);
    }
  }

  const label = theme === "dark" ? "Switch to light theme" : "Switch to dark theme";
  return (
    <button type="button" onClick={flip} aria-label={label} title={label} className={`btn btn-ghost !h-9 ${compact ? "btn-icon" : "w-full !justify-start gap-2 !px-2.5"}`}>
      <svg aria-hidden="true" viewBox="0 0 16 16" className="h-4 w-4 shrink-0">
        {theme === "dark" ? (
          <>
            <circle cx="8" cy="8" r="3" fill="none" stroke="currentColor" strokeWidth="1.5" />
            <path d="M8 1.5v1.5M8 13v1.5M1.5 8H3M13 8h1.5M3.4 3.4l1 1M11.6 11.6l1 1M3.4 12.6l1-1M11.6 4.4l1-1" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
          </>
        ) : (
          <path d="M13 9.5A5.5 5.5 0 0 1 6.5 3a5.5 5.5 0 1 0 6.5 6.5z" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinejoin="round" />
        )}
      </svg>
      {!compact && <span className="max-xl:md:hidden">{theme === "dark" ? "Light" : "Dark"}</span>}
    </button>
  );
}
