"use client";

import Link from "next/link";
import { usePathname, useSearchParams } from "next/navigation";
import { Suspense, useCallback, useEffect, useRef, useState } from "react";
import { ignoreShortcut, routeTitle, trapTab } from "@/lib/a11y";
import { useApi } from "@/lib/api";
import { routes } from "@/lib/routes";
import { clearSession, useSession, type Session } from "@/lib/session";
import { categoryLabel } from "@/lib/category";
import { tallyText } from "@/lib/format";
import { shortcutPage, shortcutSections } from "@/lib/shortcuts";
import type { Approval, EventSummary, GroupsPage, Page, Run } from "@/lib/types";
import dynamic from "next/dynamic";
import type { Command } from "./CommandPalette";
import { DemoChecklist } from "./demo/Checklist";
import { DemoPill } from "./demo/DemoPill";
import { DemoTour, startTour, type DemoManifest } from "./demo/Tour";
import { ThemeToggle } from "./theme/ThemeToggle";
import { SignIn } from "./SignIn";
import { Badge, LogoMark, ShortcutHint, Toaster, Wordmark } from "./ui";
// Dialogs load on first open: they are not needed to paint any page, so they stay out of the shared bundle.
const AboutPanel = dynamic(() => import("./About").then((m) => m.AboutPanel), { ssr: false });
const CommandPalette = dynamic(() => import("./CommandPalette").then((m) => m.CommandPalette), { ssr: false });
const ShortcutSheet = dynamic(() => import("./ui/ShortcutSheet").then((m) => m.ShortcutSheet), { ssr: false });

export { Wordmark } from "./ui";

type NavKey = "events" | "groups" | "approvals" | "bench" | "label";

/** How <main> is laid out: centred reading column, full-width cull view, or a full-height desk. */
type Layout = "read" | "wide" | "desk";

interface NavItem {
  key: NavKey;
  label: string;
  href: string;
  team?: boolean;
  icon: React.ReactNode;
}

const LAST_EVENT_KEY = "keepframe.lastEvent";
const LAST_ROLE_KEY = "keepframe.lastEventRole";

export function Shell({ children }: { children: React.ReactNode }) {
  const session = useSession();
  if (session === undefined) return <ShellSkeleton />;
  if (session === null)
    return (
      <>
        <SignIn />
        <Toaster />
      </>
    );
  return (
    <Suspense fallback={<ShellSkeleton />}>
      <AppShell session={session}>{children}</AppShell>
    </Suspense>
  );
}

function AppShell({ session, children }: { session: Session; children: React.ReactNode }) {
  const path = usePathname();
  const params = useSearchParams();
  const team = session.role === "team";
  const [about, setAbout] = useState(false);
  const [palette, setPalette] = useState(false);
  const [drawer, setDrawer] = useState(false);
  const [keys, setKeys] = useState(false);
  const drawerRef = useRef<HTMLDivElement>(null);
  // The element that opened a drawer/sheet gets focus back when it closes (native <dialog> does this for
  // About and ⌘K; the drawer is a div and the sheet can open from a keypress).
  const returnTo = useRef<HTMLElement | null>(null);

  const events = useApi<Page<EventSummary>>("/events", { limit: 100 });
  const demo = Boolean(session.demo);
  const manifest = useDemoManifest(demo);
  const approvals = useApi<Page<Approval>>(team ? "/approvals" : null, { limit: 50 }, 15000);
  const pending = approvals.data ? approvals.data.items.length : undefined;
  const pendingText = pending === undefined ? undefined : approvals.data?.next_cursor ? `${pending}+` : String(pending);

  // Each route gets its own title (WCAG 2.4.2), so tabs, history and screen readers say where you are; the
  // pending count rides in front. A page that sets a more specific title after mount keeps it.
  useEffect(() => {
    const base = routeTitle(path);
    document.title = pendingText && pendingText !== "0" ? `(${pendingText}) ${base}` : base;
  }, [pendingText, path]);

  // Remember the event being looked at so "Groups" returns to it.
  const urlEvent = path.startsWith("/events") ? params.get("id") : path.startsWith("/groups") ? params.get("event") : null;
  const [lastEvent, setLastEvent] = useState<string | null>(null);
  useEffect(() => {
    // Team and judge see different event ids (judges get aliases), so a remembered id is only kept for the role
    // that saw it.
    if (urlEvent) {
      window.sessionStorage.setItem(LAST_EVENT_KEY, urlEvent);
      window.sessionStorage.setItem(LAST_ROLE_KEY, session.role);
      setLastEvent(urlEvent);
    } else if (window.sessionStorage.getItem(LAST_ROLE_KEY) === session.role) setLastEvent(window.sessionStorage.getItem(LAST_EVENT_KEY));
    else setLastEvent(null);
  }, [urlEvent, session.role]);
  const knownEvent = lastEvent && events.data && !events.data.items.some((e) => e.event_id === lastEvent) ? null : lastEvent;
  const groupsEvent = knownEvent ?? events.data?.items[0]?.event_id;
  // ⌘K also indexes the groups of the event in focus and the recent runs; fetched only while the palette is open.
  const paletteGroups = useApi<GroupsPage>(palette && groupsEvent ? `/events/${encodeURIComponent(groupsEvent)}/groups` : null, { limit: 100 });
  const paletteRuns = useApi<Page<Run>>(palette ? "/runs" : null, { limit: 20 });

  const nav: NavItem[] = [
    { key: "events", label: "Overview", href: routes.home, icon: <IconRuns /> },
    { key: "groups", label: "Groups", href: groupsEvent ? routes.groups(groupsEvent) : "/groups/", icon: <IconGroups /> },
    // Judges can browse answered approvals read-only, so the item is shown to them too; only Label is team-only.
    { key: "approvals", label: "Approvals", href: routes.approvals(), icon: <IconApprovals /> },
    { key: "bench", label: "Benchmark", href: routes.bench, icon: <IconBench /> },
    { key: "label", label: "Label", href: routes.label, team: true, icon: <IconLabel /> },
  ];
  const visible = nav.filter((n) => !n.team || team);
  const activeKey: NavKey | undefined =
    path === "/" || path.startsWith("/events")
      ? "events"
      : path.startsWith("/groups")
        ? "groups"
        : path.startsWith("/approvals")
          ? "approvals"
          : path.startsWith("/bench")
            ? "bench"
            : path.startsWith("/label")
              ? "label"
              : undefined;
  const layout: Layout = path.startsWith("/approvals") ? "desk" : path.startsWith("/groups") || path.startsWith("/label") ? "wide" : "read";
  const width = layout === "read" ? "max-w-[1520px]" : "max-w-[1880px]";

  useEffect(() => setDrawer(false), [path]);
  // Moving focus into the new page on client navigation: the skip target is focusable, so a keyboard user
  // starts at the content instead of at the link they clicked (which may no longer exist).
  const firstPath = useRef(path);
  useEffect(() => {
    if (path === firstPath.current) return;
    firstPath.current = path;
    const active = document.activeElement;
    if (!active || active === document.body || !document.body.contains(active)) document.getElementById("main")?.focus({ preventScroll: true });
  }, [path]);
  // Reserve the scrollbar gutter only while the page actually scrolls: a pinned page (the approvals desk) with a
  // reserved but empty gutter shows a strip beside the full-width judge banner.
  useEffect(() => {
    const el = document.documentElement;
    const sync = () => el.classList.toggle("kf-no-scroll", el.scrollHeight <= el.clientHeight + 1);
    sync();
    const ro = new ResizeObserver(sync);
    ro.observe(document.body);
    window.addEventListener("resize", sync);
    return () => {
      ro.disconnect();
      window.removeEventListener("resize", sync);
      el.classList.remove("kf-no-scroll");
    };
  }, []);
  // Mobile drawer is modal: Tab cycles inside it, Esc closes it, focus goes back to the menu button.
  useEffect(() => {
    if (!drawer) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.stopPropagation();
        setDrawer(false);
      } else trapTab(e, drawerRef.current);
    };
    window.addEventListener("keydown", onKey, true);
    return () => {
      window.removeEventListener("keydown", onKey, true);
      const back = returnTo.current;
      if (back && document.body.contains(back)) back.focus({ preventScroll: true });
    };
  }, [drawer]);

  // "?" opens the shortcut sheet from anywhere (not while typing or while another dialog is open).
  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (e.key !== "?" || ignoreShortcut(e)) return;
      e.preventDefault();
      openKeys();
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);
  function openKeys() {
    returnTo.current = (document.activeElement as HTMLElement) ?? null;
    setKeys(true);
  }
  function closeKeys() {
    setKeys(false);
    const back = returnTo.current;
    // Native dialogs restore focus themselves; this covers a sheet opened by the "?" key from the page body.
    requestAnimationFrame(() => back && document.body.contains(back) && back.focus({ preventScroll: true }));
  }

  // ⌘K / Ctrl+K opens the palette from anywhere.
  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        setPalette((p) => !p);
      }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  const signOut = useCallback(() => {
    window.sessionStorage.removeItem(LAST_EVENT_KEY);
    window.sessionStorage.removeItem(LAST_ROLE_KEY);
    clearSession();
  }, []);

  const commands: Command[] = [
    ...visible.map((n) => ({ id: `nav-${n.key}`, group: "Go to", label: n.label, href: n.href, icon: n.icon })),
    ...(events.data?.items ?? []).map((e) => ({
      id: `ev-${e.event_id}`,
      group: "Events",
      label: e.name ?? e.label,
      hint: [categoryLabel(e.category), e.event_date ? new Date(`${e.event_date}T00:00:00`).toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric" }) : ""].filter(Boolean).join(" · "),
      href: routes.event(e.event_id),
      icon: <IconCalendar />,
    })),
    ...(events.data?.items ?? []).map((e) => ({
      id: `gr-${e.event_id}`,
      group: "Events",
      label: `${e.name ?? e.label}: all groups`,
      hint: "Burst grid",
      href: routes.groups(e.event_id),
      icon: <IconGroups />,
    })),
    ...(groupsEvent
      ? [
          { id: "act-review", group: "Actions", label: "Review groups with frames in review", hint: "Burst grid, review filter", href: routes.groups(groupsEvent, { verdict: "review" }), icon: <IconGroups /> },
          ...(team ? [{ id: "act-approvals", group: "Actions", label: "Open the approvals desk", hint: pendingText && pendingText !== "0" ? `${pendingText} waiting` : "Nothing waiting", href: routes.approvals(), icon: <IconApprovals /> }] : []),
          ...(team ? [{ id: "act-label", group: "Actions", label: "Label frames and export CSV", href: routes.label, icon: <IconLabel /> }] : []),
        ]
      : []),
    ...[...(paletteGroups.data?.items ?? [])].sort((a, b) => (a.group_label ?? "").localeCompare(b.group_label ?? "", "en", { numeric: true })).map((g) => ({
      id: `g-${g.group_id}`,
      group: paletteGroups.data?.event ? `Groups in ${paletteGroups.data.event.name ?? paletteGroups.data.event.label}` : "Groups",
      label: g.group_label ?? "Group",
      hint: `${g.members.length} frames${tallyText(g.verdict_counts)}`,
      href: routes.group(groupsEvent as string, g.group_id, g.run_id),
      icon: <IconGroups />,
    })),
    ...(paletteRuns.data?.items ?? []).map((r, i, all) => ({
      id: `run-${r.run_id}`,
      group: "Runs",
      label: `${(events.data?.items ?? []).find((e) => e.event_id === r.event_id)?.name ?? "Event"} · Run ${all.length - i}`,
      hint: [r.status?.replace(/_/g, " "), r.started_at ? new Date(r.started_at).toLocaleString("en-GB", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" }) : ""].filter(Boolean).join(" · "),
      href: routes.event(r.event_id, r.run_id),
      icon: <IconRuns />,
    })),
    { id: "about", group: "Help", label: "About KeepFrame", run: () => setAbout(true), icon: <IconInfo /> },
    { id: "keys", group: "Help", label: "Keyboard shortcuts", hint: "?", run: openKeys, icon: <IconKeys /> },
    ...(demo ? [{ id: "tour", group: "Help", label: "Replay the demo tour", run: startTour, icon: <IconInfo /> }] : []),
    { id: "signout", group: "Account", label: demo ? "Leave the demo" : "Sign out", run: signOut, icon: <IconSignOut /> },
  ];

  const sidebar = (
    <SidebarBody
      items={visible}
      activeKey={activeKey}
      pending={pendingText}
      session={session}
      onSearch={() => setPalette(true)}
      onAbout={() => setAbout(true)}
      onKeys={openKeys}
      onSignOut={signOut}
      manifest={manifest}
    />
  );

  return (
    // The desk layout pins the page to the viewport (xl and up) so the approvals desk can fill whatever height is
    // left under the judge banner with plain flex, instead of subtracting a guessed header height.
    <div className={`flex min-h-dvh flex-col md:pl-[var(--kf-nav-w)] ${layout === "desk" ? "xl:h-dvh" : ""}`}>
      <a
        href="#main"
        // not-sr-only resets padding to 0, so the padding is re-applied with focus: (the visible link was a
        // cramped 100x21 box).
        className="sr-only z-[70] rounded-md bg-[var(--kf-surface)] font-medium text-[var(--kf-accent-text)] shadow-[var(--kf-shadow-lg)] ring-1 ring-[var(--kf-border-strong)] focus:not-sr-only focus:fixed focus:top-3 focus:left-3 focus:px-4 focus:py-2.5"
      >
        Skip to content
      </a>

      {/* Desktop sidebar */}
      <aside className="fixed inset-y-0 left-0 z-30 hidden w-[var(--kf-nav-w)] flex-col border-r border-[var(--kf-border)] bg-[var(--kf-surface)] md:flex">
        <SidebarBody
          items={visible}
          activeKey={activeKey}
          pending={pendingText}
          session={session}
          onSearch={() => setPalette(true)}
          onAbout={() => setAbout(true)}
          onKeys={openKeys}
          onSignOut={signOut}
          manifest={manifest}
          collapsible
        />
      </aside>

      {/* Mobile top bar + drawer */}
      <header className="sticky top-0 z-30 flex h-14 items-center gap-3 border-b border-[var(--kf-border)] bg-[var(--kf-surface)] px-4 md:hidden">
        <button
          type="button"
          className="btn btn-ghost btn-icon relative -ml-2"
          aria-label={pendingText && pendingText !== "0" ? `Open navigation, ${pendingText} approvals waiting` : "Open navigation"}
          aria-expanded={drawer}
          onClick={(e) => {
            // Captured here: by the time an effect runs, the drawer's autoFocus close button has focus.
            returnTo.current = e.currentTarget;
            setDrawer(true);
          }}
        >
          <IconMenu />
          {pendingText && pendingText !== "0" && (
            <span aria-hidden="true" className="absolute top-1.5 right-1.5 h-2 w-2 rounded-full bg-[var(--kf-review)] ring-2 ring-[var(--kf-surface)]" />
          )}
        </button>
        <Link href="/" aria-label="KeepFrame home" className="inline-flex rounded-md">
          <Wordmark />
        </Link>
        <span className="ml-auto flex items-center gap-1">
          {demo ? <DemoPill compact onTour={startTour} /> : <RolePill role={session.role} compact />}
          <button type="button" className="kf-kbd-hint btn btn-ghost btn-icon" aria-label="Keyboard shortcuts" aria-keyshortcuts="?" onClick={openKeys}>
            <IconKeys />
          </button>
          <button type="button" className="btn btn-ghost btn-icon -mr-2" aria-label="Search pages and events" onClick={() => setPalette(true)}>
            <IconSearch />
          </button>
        </span>
      </header>
      {drawer && (
        <div ref={drawerRef} className="fixed inset-0 z-40 md:hidden" role="dialog" aria-modal="true" aria-label="Navigation">
          {/* Pointer-only backdrop: the close button inside the panel is the keyboard path, so this one stays
              out of the tab order and the accessibility tree (two "Close navigation" buttons confused SRs). */}
          <div aria-hidden="true" className="kf-fade absolute inset-0 bg-[rgb(15_18_25/0.42)]" onClick={() => setDrawer(false)} />
          <aside className="kf-pop absolute inset-y-0 left-0 flex w-[min(280px,85vw)] flex-col bg-[var(--kf-surface)] shadow-[var(--kf-shadow-lg)]">
            <button
              type="button"
              autoFocus
              aria-label="Close navigation"
              onClick={() => setDrawer(false)}
              className="btn btn-ghost btn-icon absolute top-[15px] right-3"
            >
              <svg viewBox="0 0 16 16" className="h-4 w-4" aria-hidden="true">
                <path d="M4 4l8 8M12 4l-8 8" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" />
              </svg>
            </button>
            {sidebar}
          </aside>
        </div>
      )}

      {!team && (
        // A labelled region (axe "region": content outside landmarks). Below md the role pill says the same.
        <aside aria-label="Read-only judge view" className="hidden shrink-0 border-b border-[#e6c99a] bg-[var(--kf-review-soft)] md:block">
          <p className={`t-small mx-auto flex w-full items-center gap-2 px-5 py-1.5 font-medium text-[var(--kf-review)] sm:px-8 lg:px-10 2xl:px-12 ${width}`}>
            <IconEye />
            Read-only judge view
          </p>
        </aside>
      )}
      {demo && (
        // In flow (not an overlay), so it never sits on a page's own header buttons or sticky side cards.
        <div className="hidden shrink-0 md:block">
          <div className={`mx-auto flex w-full justify-end ${width} ${
            // The desk's top margin is thin, so the pill takes its own short row instead of overlapping the desk.
            layout === "desk" ? "-mb-2 px-4 pt-2 lg:px-2" : "-mb-4 px-5 pt-3 sm:px-8 lg:-mb-6 lg:px-10 2xl:px-12"
          }`}>
            <DemoPill onTour={startTour} />
          </div>
        </div>
      )}
      <main
        id="main"
        tabIndex={-1}
        className={`mx-auto w-full outline-none ${width} ${
          // The desk is a working surface, not a reading page: thin margins so the photos get the room.
          layout === "desk" ? "flex min-h-0 flex-1 flex-col px-4 pt-4 pb-4 lg:px-2 lg:pb-2" : "px-5 pt-7 pb-24 sm:px-8 lg:px-10 lg:pt-9 2xl:px-12"
        }`}
      >
        {children}
      </main>

      {demo && <DemoTour manifest={manifest} />}
      {demo && <DemoChecklist manifest={manifest} />}
      {about && <AboutPanel onClose={() => setAbout(false)} />}
      {palette && <CommandPalette commands={commands} onClose={() => setPalette(false)} />}
      {keys && <ShortcutSheet sections={shortcutSections(shortcutPage(path, Boolean(params.get("id"))), team)} onClose={closeKeys} />}
      {/* On the approvals desk the bottom half is decision bar + clickable filmstrip: toasts go top-right instead. */}
      <Toaster placement={layout === "desk" ? "desk" : "bottom"} />
    </div>
  );
}

function SidebarBody({
  items,
  activeKey,
  pending,
  session,
  onSearch,
  onAbout,
  onKeys,
  onSignOut,
  manifest,
  collapsible = false,
}: {
  manifest?: DemoManifest;
  collapsible?: boolean;
  items: NavItem[];
  activeKey?: NavKey;
  pending?: string;
  session: Session;
  onSearch: () => void;
  onAbout: () => void;
  onKeys: () => void;
  onSignOut: () => void;
}) {
  // collapsible: the desktop sidebar is an icon rail between md and xl (labels become tooltips), full above xl.
  const full = collapsible ? "max-xl:sr-only" : "";
  const railPad = collapsible ? "max-xl:justify-center max-xl:px-0" : "";
  const judge = session.role === "judge";
  return (
    <>
      <div className={`flex h-16 shrink-0 items-center px-5 ${collapsible ? "max-xl:justify-center max-xl:px-0" : ""}`}>
        <Link href="/" aria-label="KeepFrame, events" className="inline-flex rounded-md">
          {collapsible ? (
            <>
              <span className="flex xl:hidden">
                <LogoMark />
              </span>
              <span className="flex max-xl:hidden">
                <Wordmark />
              </span>
            </>
          ) : (
            <Wordmark />
          )}
        </Link>
      </div>
      <div className={collapsible ? "px-3 max-xl:px-2" : "px-3"}>
        <RailTip label="Jump to… (⌘K)" on={collapsible}>
          <button
            type="button"
            onClick={onSearch}
            // The accessible name starts with the visible words (WCAG 2.5.3 label in name: voice users say "Jump to").
            aria-label="Jump to… a page or event"
            aria-keyshortcuts="Meta+K Control+K"
            className={`flex h-9 w-full items-center gap-2 rounded-[var(--kf-radius-sm)] bg-[var(--kf-surface)] px-2.5 text-left text-[0.8125rem] text-[var(--kf-muted)] ring-1 ring-inset ring-[var(--kf-border)] transition-colors hover:text-[var(--kf-text)] hover:ring-[var(--kf-border-strong)] ${railPad}`}
          >
            <IconSearch />
            <span className={`flex-1 ${full}`}>Jump to…</span>
            <ShortcutHint className={`hidden md:inline-flex ${collapsible ? "max-xl:!hidden" : ""}`} />
          </button>
        </RailTip>
      </div>
      <nav aria-label="Main" className={`mt-4 flex-1 overflow-y-auto ${collapsible ? "px-3 max-xl:px-2" : "px-3"}`}>
        <ul className="flex flex-col gap-0.5">
          {items.map((n) => {
            const on = n.key === activeKey;
            const badge = n.key === "approvals" && pending !== undefined && pending !== "0";
            return (
              <li key={n.key}>
                <RailTip label={badge ? `${n.label} (${pending} waiting)` : n.label} on={collapsible}>
                  <Link
                    href={n.href}
                    aria-current={on ? "page" : undefined}
                    className={`group relative flex h-9 w-full items-center gap-2.5 rounded-[var(--kf-radius-sm)] px-2.5 text-[0.875rem] font-medium transition-colors ${railPad} ${
                      on
                        ? "bg-[var(--kf-accent-soft)] text-[var(--kf-accent-text)]"
                        : "text-[var(--kf-muted)] hover:bg-[var(--kf-surface-2)] hover:text-[var(--kf-text)]"
                    }`}
                  >
                    <span className={on ? "text-[var(--kf-accent)]" : "text-[var(--kf-faint)] group-hover:text-[var(--kf-text)]"}>{n.icon}</span>
                    <span className={`flex-1 ${full}`}>{n.label}</span>
                    {badge && (
                      <>
                        <Badge tone="review" count aria-label={`${pending} waiting`} className={collapsible ? "max-xl:hidden" : ""}>
                          {pending}
                        </Badge>
                        {collapsible && (
                          <span aria-hidden="true" className="absolute top-1.5 right-2 h-2 w-2 rounded-full bg-[var(--kf-review)] ring-2 ring-[var(--kf-surface)] xl:hidden" />
                        )}
                      </>
                    )}
                  </Link>
                </RailTip>
              </li>
            );
          })}
        </ul>
      </nav>
      <div className={`shrink-0 border-t border-[var(--kf-border)] p-3 ${collapsible ? "max-xl:px-2" : ""}`}>
        <div className={`flex items-center gap-1 ${collapsible ? "max-xl:hidden" : ""}`}>
          <div className="min-w-0 flex-1">
            <RolePill role={session.role} demo={session.demo} />
          </div>
          <ThemeToggle compact />
          <KeysButton onKeys={onKeys} />
        </div>
        {collapsible && (
          <RailTip label={session.demo ? "Demo" : judge ? "Judge, read-only" : "Team"} on className="xl:hidden">
            <span
              tabIndex={0}
              aria-label={session.demo ? "Demo session" : judge ? "Signed in as judge, read-only" : "Signed in as team"}
              className={`mx-auto grid h-9 w-9 place-items-center rounded-full ${judge ? "bg-[var(--kf-review-soft)] text-[var(--kf-review)]" : "bg-[var(--kf-surface-2)] text-[var(--kf-accent)]"}`}
            >
              {judge ? <IconEye /> : <IconTeam />}
            </span>
          </RailTip>
        )}
        {/* Help lives here on every page (WCAG 3.2.6): About, the "?" shortcut sheet, sign out. About + "?" + "Leave
            demo" need ~227px but the sidebar row has ~208px, so the "?" button sits in the theme row above and this
            row holds just the two labelled buttons, each at its own width with a gap between them. On the collapsed
            rail everything stacks as icons, so "?" comes back into this column there. */}
        <div className={`mt-1.5 grid grid-cols-[auto_auto] justify-between gap-2 ${collapsible ? "max-xl:grid-cols-1 max-xl:gap-0.5" : ""}`}>
          {collapsible && (
            <RailTip label="Light or dark theme" on className="xl:hidden">
              <ThemeToggle compact />
            </RailTip>
          )}
          <RailTip label="About KeepFrame" on={collapsible}>
            <button type="button" onClick={onAbout} aria-label="About KeepFrame" className={`btn btn-ghost !h-9 !justify-start gap-1.5 !px-2 ${collapsible ? "max-xl:w-full max-xl:!justify-center max-xl:!px-0" : ""}`}>
              <IconInfo />
              <span className={full}>About</span>
            </button>
          </RailTip>
          {collapsible && (
            <RailTip label="Keyboard shortcuts (?)" on className="xl:hidden">
              <KeysButton onKeys={onKeys} rail />
            </RailTip>
          )}
          <RailTip label={session.demo ? "Leave the demo" : "Sign out"} on={collapsible}>
            <button type="button" onClick={onSignOut} aria-label={session.demo ? "Leave the demo" : "Sign out"} className={`btn btn-ghost !h-9 !justify-start gap-1.5 !px-2 ${collapsible ? "max-xl:w-full max-xl:!justify-center max-xl:!px-0" : ""}`}>
              <IconSignOut />
              <span className={full}>{session.demo ? "Leave demo" : "Sign out"}</span>
            </button>
          </RailTip>
        </div>
      </div>
    </>
  );
}

function KeysButton({ onKeys, rail }: { onKeys: () => void; rail?: boolean }) {
  return (
    <button
      type="button"
      onClick={onKeys}
      aria-label="Keyboard shortcuts"
      aria-keyshortcuts="?"
      title="Keyboard shortcuts (?)"
      className={`btn btn-ghost btn-icon !h-9 ${rail ? "!w-full" : "!w-8 shrink-0"}`}
    >
      <IconKeys />
    </button>
  );
}

/** Rail tooltip: on the collapsed rail the label moves into a tooltip to the right. Above xl it never shows. */
function RailTip({ label, on, children, className = "" }: { label: string; on: boolean; children: React.ReactNode; className?: string }) {
  if (!on) return <>{children}</>;
  return (
    <span className={`group/tip relative flex ${className}`}>
      {children}
      <span
        role="presentation"
        className="pointer-events-none absolute top-1/2 left-full z-50 ml-2.5 hidden w-max -translate-y-1/2 rounded-md bg-[#14171c] px-2 py-1 text-[0.75rem] font-medium text-white shadow-lg group-focus-within/tip:max-xl:block group-hover/tip:max-xl:block"
      >
        {label}
      </span>
    </span>
  );
}
export function RolePill({ role, compact, demo }: { role: Session["role"]; compact?: boolean; demo?: boolean }) {
  const judge = role === "judge";
  // The demo pill is the one demo marker; the sidebar shows no second one.
  if (demo && !compact) return null;
  if (compact) {
    return (
      <Badge tone={judge ? "review" : "neutral"} className="!h-6 !gap-1 !pl-1.5">
        {judge ? <IconEye /> : <IconTeam />}
        {judge ? "Judge, read-only" : "Team"}
      </Badge>
    );
  }
  return (
    <div className={`flex items-center gap-2.5 rounded-[var(--kf-radius-sm)] px-2 py-1.5 ${judge ? "bg-[var(--kf-review-soft)]" : "bg-[var(--kf-surface-2)]"}`}>
      <span
        className={`grid h-7 w-7 shrink-0 place-items-center rounded-full ${
          judge ? "bg-[var(--kf-surface)] text-[var(--kf-review)]" : "bg-[var(--kf-surface)] text-[var(--kf-accent)]"
        }`}
        aria-hidden="true"
      >
        {judge ? <IconEye /> : <IconTeam />}
      </span>
      <span className="min-w-0 flex-1 leading-tight">
        <span className="block text-[0.8125rem] font-semibold text-[var(--kf-text)]" title={judge ? undefined : "Can approve and label"}>{judge ? "Judge" : "Team"}</span>
        {judge && <span className="block text-[0.72rem] text-[var(--kf-muted)]">Read-only</span>}
      </span>
    </div>
  );
}

/** Tour targets and pending ids for the demo dataset (public/demo/manifest.json). */
function useDemoManifest(on: boolean): DemoManifest | undefined {
  const [m, setM] = useState<DemoManifest>();
  useEffect(() => {
    if (!on) return;
    const base = (process.env.NEXT_PUBLIC_BASE_PATH ?? "").replace(/\/+$/, "");
    fetch(`${base}/demo/manifest.json`)
      .then((r) => (r.ok ? (r.json() as Promise<DemoManifest>) : undefined))
      .then(setM)
      .catch(() => setM(undefined));
  }, [on]);
  return m;
}

function ShellSkeleton() {
  return (
    <div className="min-h-dvh md:pl-[var(--kf-nav-w)]" aria-busy="true">
      <div className="fixed inset-y-0 left-0 hidden w-[var(--kf-nav-w)] border-r border-[var(--kf-border)] bg-[var(--kf-surface)] md:block" />
    </div>
  );
}

/* ---------- icons (16px, 1.6 stroke) ---------- */
const ip = { fill: "none", stroke: "currentColor", strokeWidth: 1.7, strokeLinecap: "round" as const, strokeLinejoin: "round" as const };
function I({ children }: { children: React.ReactNode }) {
  return (
    <svg viewBox="0 0 20 20" className="h-[18px] w-[18px] shrink-0" aria-hidden="true">
      {children}
    </svg>
  );
}
export function IconRuns() {
  return (
    <I>
      <path {...ip} d="M3.5 5h13M3.5 10h13M3.5 15h8" />
    </I>
  );
}
export function IconGroups() {
  return (
    <I>
      <rect {...ip} x="3" y="3" width="6" height="6" rx="1.5" />
      <rect {...ip} x="11" y="3" width="6" height="6" rx="1.5" />
      <rect {...ip} x="3" y="11" width="6" height="6" rx="1.5" />
      <rect {...ip} x="11" y="11" width="6" height="6" rx="1.5" />
    </I>
  );
}
export function IconApprovals() {
  return (
    <I>
      <path {...ip} d="M3 11.5 5 4.8A1.5 1.5 0 0 1 6.4 3.8h7.2A1.5 1.5 0 0 1 15 4.8l2 6.7V15a1.5 1.5 0 0 1-1.5 1.5h-11A1.5 1.5 0 0 1 3 15z" />
      <path {...ip} d="M3 11.5h4l1 2h4l1-2h4" />
    </I>
  );
}
export function IconBench() {
  return (
    <I>
      <rect {...ip} x="3.5" y="10" width="3" height="6.5" rx="0.8" />
      <rect {...ip} x="8.5" y="4" width="3" height="12.5" rx="0.8" />
      <rect {...ip} x="13.5" y="7.5" width="3" height="9" rx="0.8" />
    </I>
  );
}
export function IconLabel() {
  return (
    <I>
      <path {...ip} d="M3.5 10.6V4.5a1 1 0 0 1 1-1h6.1l6 6a1.4 1.4 0 0 1 0 2l-5 5a1.4 1.4 0 0 1-2 0z" />
      <circle cx="7.2" cy="7.2" r="1.2" fill="currentColor" />
    </I>
  );
}
function IconCalendar() {
  return (
    <I>
      <rect {...ip} x="3.5" y="4.5" width="13" height="12" rx="2" />
      <path {...ip} d="M3.5 8.5h13M7 3v3M13 3v3" />
    </I>
  );
}
function IconInfo() {
  return (
    <I>
      <circle {...ip} cx="10" cy="10" r="7" />
      <path {...ip} d="M10 9v4.5M10 6.6v.01" />
    </I>
  );
}
function IconSignOut() {
  return (
    <I>
      <path {...ip} d="M8 16.5H5a1.5 1.5 0 0 1-1.5-1.5V5A1.5 1.5 0 0 1 5 3.5h3M12.5 13.5 16 10l-3.5-3.5M16 10H8" />
    </I>
  );
}
function IconKeys() {
  return (
    <I>
      <rect {...ip} x="2.5" y="5" width="15" height="10" rx="2" />
      <path {...ip} d="M5.5 8h.01M8.5 8h.01M11.5 8h.01M14.5 8h.01M6.5 12h7" />
    </I>
  );
}
function IconSearch() {
  return (
    <I>
      <circle {...ip} cx="9" cy="9" r="5.5" />
      <path {...ip} d="m13.2 13.2 3.3 3.3" />
    </I>
  );
}
function IconMenu() {
  return (
    <I>
      <path {...ip} d="M3.5 6h13M3.5 10h13M3.5 14h13" />
    </I>
  );
}
function IconEye() {
  return (
    <svg viewBox="0 0 20 20" className="h-4 w-4" aria-hidden="true">
      <path {...ip} d="M2 10s3-5.5 8-5.5S18 10 18 10s-3 5.5-8 5.5S2 10 2 10z" />
      <circle {...ip} cx="10" cy="10" r="2.4" />
    </svg>
  );
}
function IconTeam() {
  return (
    <svg viewBox="0 0 20 20" className="h-4 w-4" aria-hidden="true">
      <circle {...ip} cx="7.5" cy="7" r="2.8" />
      <path {...ip} d="M2.5 16c.6-2.6 2.6-4 5-4s4.4 1.4 5 4M13 4.6a2.6 2.6 0 0 1 0 5M14.5 12.3c1.5.5 2.6 1.7 3 3.7" />
    </svg>
  );
}
