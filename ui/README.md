# KeepFrame review UI (`ui/`)

Next.js 15, TypeScript, Tailwind 4, static export to `ui/out` (S3 + CloudFront). Ids travel in query
strings (`/events/?id=…`, `/groups/?event=…&id=…&run=…`), so every page is a prebuilt HTML file.

```bash
cd ui && npm ci
NEXT_PUBLIC_API_BASE=https://<api-gateway-url> npm run build   # → ui/out
npm test        # labels CSV / reason vocabulary unit tests (node --test)
npm run typecheck
```

Local, against the synthetic fixture API: start `services/api/dev.sh` (prints throwaway dev tokens), then
`NEXT_PUBLIC_API_BASE=http://127.0.0.1:8000 npm run dev`. The API must list the UI origin in
`CULL_CORS_ORIGINS`.

Pages: `/` runs and events (team can start a run), `/events/` groups grid and run banner, `/groups/` group
detail and trace chain, `/approvals/` pending approvals (team, keyboard driven), `/bench/` per-stage latency, one series per benchmarked host ("COOL not available" until a `cool` host exists),
`/label/` labelling tool (team; exports `eval/labels/<eventId>.csv` with `docs/EVALUATION.md` §1 columns). The
reason-code vocabulary is mirrored in `lib/reasons.ts` from `config/reason_codes.yaml`.

The token is kept in `sessionStorage` only. No customer images are bundled: every image is a presigned
URL fetched at runtime.
