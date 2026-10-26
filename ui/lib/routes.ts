// Every in-app route in one place, so cross-page links can't drift apart.
//   /                                  events + recent runs (home)
//   /events/?id=<event>[&run=<run>]    event detail: run panel, verdicts, group preview
//   /groups/?event=<event>[&v=review]  event-wide burst grid (the cull view)
//   /groups/?event=<event>&id=<group>  one burst: preview, filmstrip, inspector, agent trace
//   /approvals/[?group=<group>]        approvals desk, optionally opened on one group's request

const q = encodeURIComponent;

export const routes = {
  home: "/",
  event: (eventId: string, runId?: string) => `/events/?id=${q(eventId)}${runId ? `&run=${q(runId)}` : ""}`,
  groups: (eventId: string, opts: { verdict?: "keep" | "review" | "reject" } = {}) =>
    `/groups/?event=${q(eventId)}${opts.verdict ? `&v=${opts.verdict}` : ""}`,
  group: (eventId: string, groupId: string, runId?: string) =>
    `/groups/?event=${q(eventId)}&id=${q(groupId)}${runId ? `&run=${q(runId)}` : ""}`,
  approvals: (groupId?: string) => (groupId ? `/approvals/?group=${q(groupId)}` : "/approvals/"),
  bench: "/bench/",
  label: "/label/",
};
