# Judge access

Judges get a read-only view of the live KeepFrame review desk. The token itself is never in this
repository. It is shared in the Devpost private "testing instructions" field.

## Open the review desk

1. Open the demo URL from the README: today **https://fnua4aumrl.execute-api.ap-south-1.amazonaws.com/ui/** (the
   review UI is served by the HTTP API under `/ui` until CloudFront is enabled for the account; docs/DEPLOY.md). The
   page itself loads without a token.
2. Paste the judge token into the "Access token" box and choose "Open review desk".
3. The header shows "Judge view, read-only".

The token stays in that browser tab only (`sessionStorage`) and is cleared when the tab closes. Use
"Sign out" to clear it sooner.

## What you can see

| Page | What it shows |
|---|---|
| Runs | Every culling run: status, OpenCV build (stock OpenCV 5 wheel), verdict counts, tool calls, cost |
| Event | All burst groups as contact-sheet strips with keep / review / reject marks and reason chips; the run banner shows the OpenCV version, `cv2.__file__`, and the LLM provider and models |
| Group | Every frame with scores and faces, the best pick and its margin, and the agent's full trace. The red brackets mark the steps where an OpenCV 5 result changed the agent's next tool call |
| Benchmark | OpenCV 5 on Graviton per stage (p50 and p95), with a table view. COOL is not used |
| About | Architecture overview and the OpenCV build of the latest run |

Images come from the private S3 bucket through presigned links that expire after 10 minutes (never more
than 15). If an image stops loading, reload the page.

## What is read-only

The judge token can only make GET requests. The API rejects any other method with HTTP 403 before the
request reaches a route, so a judge cannot start runs, answer approvals or change any decision. The
Approvals and Label pages are hidden for the judge token, and their API routes also return 403.

Event names and source user or studio ids are never shown; events appear as "Event 1", "Event 2" with their category and date. Since the aliasing release (security review #1, deployed 2026-10-09 08:05 UTC and verified live), judges see only aliases such as `ev_03`, `ph_…` and `g_ev_03_7`: every judge response, trace argument and error is scrubbed, raw source ids are refused for the judge token, and images are streamed through signed `/img/…` links, so no storage key or public-CDN path reaches the browser.

Labeller handles: the `labeller` field on approvals is echoed to judges as `last_labeller`. Use a handle such as `photog-a`, never a person's name or e-mail (security review #12).

## Calling the API directly (optional)

```bash
curl -H "Authorization: Bearer $KEEPFRAME_JUDGE_TOKEN" "$KEEPFRAME_API/health"
curl -H "Authorization: Bearer $KEEPFRAME_JUDGE_TOKEN" "$KEEPFRAME_API/runs"
```

`$KEEPFRAME_API` is the API Gateway URL from the README. Read routes: `/health`, `/events`,
`/events/{event_id}/groups`, `/events/{event_id}/photos/{photo_id}`, `/runs`, `/runs/{run_id}`,
`/runs/{run_id}/trace`, `/bench`.

## Token rotation

- The judge token lives in AWS Secrets Manager as `keepframe/judge-token`. The API caches it for
  5 minutes.
- To rotate, store the new value as `{"token": "<new>", "previous": "<old>"}`. Both values work during
  the overlap. Drop `previous` once judges have the new token.
- The current token is valid through the end of judging (10 November 2026). The rotation date is
  recorded in the Devpost private notes, never here.
- If a token leaks, replace the secret with a new value and no `previous`. The old token stops working
  within 5 minutes.
