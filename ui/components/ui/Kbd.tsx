"use client";

import { useEffect, useState } from "react";

/** Keyboard key. Pass several keys to render a chord: <Kbd keys={["⌘", "K"]} />. */
export function Kbd({ keys, children, className = "" }: { keys?: string[]; children?: React.ReactNode; className?: string }) {
  if (keys) {
    return (
      <span className={`inline-flex items-center gap-0.5 ${className}`}>
        {keys.map((k) => (
          <kbd key={k}>{k}</kbd>
        ))}
      </span>
    );
  }
  return <kbd className={className}>{children}</kbd>;
}

/** The palette chord: "⌘ K" on Apple platforms, "Ctrl K" elsewhere (decided after mount to avoid a hydration mismatch). Hidden on touch-only devices. */
export function ShortcutHint({ className = "" }: { className?: string }) {
  const [mac, setMac] = useState(true);
  useEffect(() => setMac(/Mac|iPhone|iPad/.test(navigator.platform || navigator.userAgent)), []);
  return <Kbd keys={mac ? ["⌘", "K"] : ["Ctrl", "K"]} className={`kf-kbd-hint ${className}`} />;
}
