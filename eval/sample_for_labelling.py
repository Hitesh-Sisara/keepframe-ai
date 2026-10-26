#!/usr/bin/env python3
"""Pick the photos to hand-label (docs/EVALUATION.md §1) and build contact sheets for the labeller.

Sampling is by WHOLE agent groups (bursts) plus random singletons, so human group labels are meaningful,
deterministic for a given --seed. Per-event targets come from --plan (default plan, 2026-10-05):

  E07 Engagement  700 photos, burst share 1.0  (burst-heavy: 868/1000 in 171 bursts, up to 75)
  E03 Ceremony    300 photos, burst share 0.3  (mostly singletons)
  E09 Birthday    300 photos, burst share 0.2  (singletons; best-of-burst barely measurable)

Aliases (E07, ...) resolve to event ids through eval/events.py (gitignored data/selected_events.json or
eval/labels/events.json, or $CULL_EVENTS_FILE); no real id is written in this file.

  - whole groups are drawn uniformly without replacement until the event's burst budget
    (target x burst_share) is used; a group that does not fit the remaining budget is skipped, never cut.
    Groups larger than --max-group (default 80) are skipped (none today: E07's largest is 75).
  - the rest of the target is random singletons; if an event has too few, the shortfall is reported.
  - --overlap (default 0.2) of each event's photos (whole sheets) are flagged second_labeller=1 for kappa.
  - the overall total must stay within [--min-total, --max-total] (1,000-2,000 per docs/EVALUATION.md §1).

Inputs (local mirror or --from-s3 with $WORK_BUCKET): results/<eventId>/groups.json and
manifests/<eventId>.jsonl. Images for sheets: proxy/<eventId>/<photoId>.jpg, else raw/<eventId>/...
Outputs (gitignored, contain photo ids):
  eval/labels/<eventId>.todo.csv      docs/EVALUATION.md §1 columns with verdict/reasons/best empty; group_id_human is
                                      prefilled with the sheet id as a suggestion (the labeller splits or
                                      merges it); group_id_agent, sheet, page, tile, second_labeller,
                                      clicked_at are reference columns that compute_metrics ignores
  eval/contact_sheets/<eventId>/<sheet>[_pN].jpg  one sheet per sampled group (24 tiles per page, numbering
                                      continues across pages), singletons in pages of 12
  eval/labels/sample_plan.json        what was sampled per event (counts only, no ids)

Labeller class (--labeller, default photographer): prefills the `labeller` column, so every row says who is
allowed to call it ground truth. `photographer[:<initials>]` = the ground truth for the report;
`team[:<initials>]` = a team member's own labels, written to eval/labels/<eventId>.team.todo.csv and reported
by compute_metrics.py ONLY as "team labels (not photographer)". Never label as `photographer` unless you
are the photographer.

  # re-stamp a finished CSV (e.g. exported from the /label page with free-text initials) to a class:
  python eval/sample_for_labelling.py --stamp eval/labels/<eventId>.csv --labeller team:hs
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import random
from pathlib import Path

import cv2
import numpy as np

HERE = Path(__file__).resolve().parent
TODO_COLS = ["photo_id", "group_id_human", "verdict_human", "reasons_human", "best_of_group_human", "labeller",
             "labelled_at", "notes", "group_id_agent", "sheet", "page", "tile", "second_labeller", "clicked_at"]
DEFAULT_PLAN = ["E07=700:1.0", "E03=300:0.3", "E09=300:0.2"]
TILES_PER_PAGE = 24
LABELLER_CLASSES = ("photographer", "team")


def check_labeller(value: str) -> str:
    """photographer | photographer:<initials> | team | team:<initials> (initials: letters/digits, <= 16)."""
    import re

    v = value.strip()
    if not re.fullmatch(r"(photographer|team)(:[A-Za-z0-9._-]{1,16})?", v):
        raise SystemExit(f"--labeller must be photographer[:<initials>] or team[:<initials>], got {value!r}")
    return v


def stamp_csv(path: Path, labeller: str) -> int:
    """Rewrite the labeller column of every row (labelled or todo) to `labeller`. Returns rows rewritten."""
    with path.open(newline="") as f:
        rd = csv.DictReader(f)
        cols = list(rd.fieldnames or [])
        rows = list(rd)
    if "labeller" not in cols:
        cols.append("labeller")
    for r in rows:
        r["labeller"] = labeller
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        w.writerows(rows)
    tmp.replace(path)
    return len(rows)


class Source:
    """Reads keys from a local mirror (default) or s3://$WORK_BUCKET."""

    def __init__(self, root: Path, use_s3: bool) -> None:
        self.root, self.s3, self.bucket = root, None, None
        if use_s3:
            import boto3

            self.s3 = boto3.client("s3")
            self.bucket = os.environ["WORK_BUCKET"]

    def read(self, key: str) -> bytes | None:
        if self.s3 is not None:
            try:
                return self.s3.get_object(Bucket=self.bucket, Key=key)["Body"].read()
            except self.s3.exceptions.NoSuchKey:
                return None
        p = self.root / key
        return p.read_bytes() if p.exists() else None


def load_event(src: Source, ev: str) -> tuple[list[list[str]], list[str], dict[str, dict], dict[str, str]]:
    man_raw = src.read(f"manifests/{ev}.jsonl")
    manifest = {}
    if man_raw:
        for line in man_raw.decode().splitlines():
            if line.strip():
                r = json.loads(line)
                manifest[r["photo_id"]] = r
    g_raw = src.read(f"results/{ev}/groups.json")
    if not g_raw:
        raise SystemExit(f"results/{ev}/groups.json not found")
    doc = json.loads(g_raw)
    raw_groups = [(g.get("group_id"), list(g.get("photo_ids") or g.get("members") or [])) for g in doc.get("groups") or []]
    raw_groups = [(gid, g) for gid, g in raw_groups if len(g) >= 2]
    groups = [g for _, g in raw_groups]
    agent_gid = {p: str(gid) for gid, g in raw_groups for p in g}
    in_group = {p for g in groups for p in g}
    singles = doc.get("singletons")
    if isinstance(singles, list):
        singles = [s if isinstance(s, str) else s.get("photo_id") for s in singles]
    else:  # count only -> derive from the manifest
        singles = [p for p in manifest if p not in in_group]
    singles = [s for s in singles if s and s not in in_group]
    return groups, sorted(singles), manifest, agent_gid


def sample_event(groups: list[list[str]], singles: list[str], target: int, burst_share: float, max_group: int,
                 overlap: float, rng: random.Random) -> list[tuple[str, list[str], bool]]:
    """-> [(sheet_id, photo_ids, second_labeller)] covering ~target photos."""
    picked: list[tuple[str, list[str]]] = []
    budget_b = int(round(target * burst_share))
    order = list(range(len(groups)))
    rng.shuffle(order)
    used = 0
    for gi in order:
        g = groups[gi]
        if len(g) > max_group or used + len(g) > budget_b:
            continue  # never cut a burst; try a smaller one
        picked.append((f"g{gi:04d}", sorted(g)))
        used += len(g)
        if used >= budget_b:
            break
    n_single = max(0, target - used)
    chosen = sorted(rng.sample(singles, min(n_single, len(singles))))
    for k in range(0, len(chosen), 12):
        picked.append((f"s{k // 12:04d}", chosen[k:k + 12]))
    total = sum(len(p) for _, p in picked)
    # overlap: whole sheets until >= overlap * total
    idx = list(range(len(picked)))
    rng.shuffle(idx)
    second, got = set(), 0
    for i in idx:
        if got >= overlap * total:
            break
        second.add(i)
        got += len(picked[i][1])
    return [(sid, pids, i in second) for i, (sid, pids) in enumerate(picked)]


def tile(img: np.ndarray | None, n: int, size: int = 360) -> np.ndarray:
    canvas = np.full((size + 28, size, 3), 250, np.uint8)
    if img is not None:
        s = size / max(img.shape[:2])
        im = cv2.resize(img, (max(1, int(img.shape[1] * s)), max(1, int(img.shape[0] * s))),
                        interpolation=cv2.INTER_AREA)
        y, x = (size - im.shape[0]) // 2, (size - im.shape[1]) // 2
        canvas[y:y + im.shape[0], x:x + im.shape[1]] = im
    else:
        cv2.putText(canvas, "missing", (size // 3, size // 2), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (120, 120, 120), 2)
    cv2.putText(canvas, str(n), (8, size + 21), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (11, 11, 11), 2, cv2.LINE_AA)
    return canvas


def contact_sheet(imgs: list[np.ndarray | None], cols: int = 4, start: int = 1) -> np.ndarray:
    tiles = [tile(im, start + i) for i, im in enumerate(imgs)] or [tile(None, start)]
    while len(tiles) % cols:
        tiles.append(np.full_like(tiles[0], 252))
    rows = [cv2.hconcat(tiles[i:i + cols]) for i in range(0, len(tiles), cols)]
    return cv2.vconcat(rows)


def sheet_pages(pids: list[str]) -> list[list[str]]:
    return [pids[k:k + TILES_PER_PAGE] for k in range(0, len(pids), TILES_PER_PAGE)] or [[]]


def parse_plan(items: list[str], aliases: dict[str, str]) -> list[tuple[str, str, int, float]]:
    """['E07=700:1.0', '<uuid>=300'] -> [(label, event_id, target, burst_share)]"""
    out = []
    for it in items:
        name, _, rest = it.partition("=")
        tgt, _, share = rest.partition(":")
        out.append((name, aliases.get(name, name), int(tgt), float(share) if share else 0.7))
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--plan", nargs="+", default=DEFAULT_PLAN,
                    help="EVENT=TARGET[:BURST_SHARE] per event; EVENT is an alias (E07) or an event id")
    ap.add_argument("--alias", nargs="*", default=[], help="extra NAME=EVENT_ID aliases (not for committed docs)")
    ap.add_argument("--events-file", type=Path, default=None, help="alias file (default: see eval/events.py)")
    ap.add_argument("--min-total", type=int, default=1000)
    ap.add_argument("--max-total", type=int, default=2000)
    ap.add_argument("--max-group", type=int, default=80)
    ap.add_argument("--overlap", type=float, default=0.2)
    ap.add_argument("--seed", type=int, default=20261005)
    ap.add_argument("--root", type=Path, default=HERE.parent / "data" / "work",
                    help="local mirror of the work bucket (manifests/, results/, raw/ or proxy/)")
    ap.add_argument("--from-s3", action="store_true")
    ap.add_argument("--labels-dir", type=Path, default=HERE / "labels")
    ap.add_argument("--sheets-dir", type=Path, default=HERE / "contact_sheets")
    ap.add_argument("--no-sheets", action="store_true")
    ap.add_argument("--labeller", default="photographer",
                    help="photographer[:<initials>] (ground truth) or team[:<initials>] (reported as team labels, "
                         "not photographer); prefilled in the labeller column")
    ap.add_argument("--stamp", type=Path, default=None,
                    help="only re-stamp the labeller column of this CSV with --labeller, then exit")
    args = ap.parse_args()
    labeller = check_labeller(args.labeller)
    if args.stamp:
        n = stamp_csv(args.stamp, labeller)
        print(f"stamped {n} rows of {args.stamp.name} as labeller={labeller}")
        return 0
    team = labeller.startswith("team")

    import sys

    sys.path.insert(0, str(HERE))
    from events import load_aliases

    aliases = {**load_aliases(args.events_file), **dict(a.split("=", 1) for a in args.alias)}
    plan = parse_plan(args.plan, aliases)
    unresolved = [n for n, ev, _, _ in plan if n == ev and n.upper().startswith("E") and len(n) <= 4]
    if unresolved:
        raise SystemExit(f"cannot resolve {unresolved}: no events file (data/selected_events.json or "
                         "eval/labels/events.json) and no --alias")
    total = sum(t for _, _, t, _ in plan)
    if not args.min_total <= total <= args.max_total:
        raise SystemExit(f"plan total {total} outside [{args.min_total}, {args.max_total}]")
    src = Source(args.root, args.from_s3)
    args.labels_dir.mkdir(parents=True, exist_ok=True)
    report = {"seed": args.seed, "plan": args.plan, "labeller_class": labeller.split(":", 1)[0], "events": []}
    grand = 0
    for name, ev, target, share in plan:
        rng = random.Random(f"{args.seed}:{ev}")
        groups, singles, manifest, agent_gid = load_event(src, ev)
        sheets = sample_event(groups, singles, target, share, args.max_group, args.overlap, rng)
        out = args.labels_dir / (f"{ev}.team.todo.csv" if team else f"{ev}.todo.csv")
        n = n_burst = n_pages = 0
        with out.open("w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=TODO_COLS)
            w.writeheader()
            for sid, pids, second in sheets:
                pids = sorted(pids, key=lambda p: (manifest.get(p, {}).get("clicked_at") or "", p))
                pages = sheet_pages(pids)
                tile_no = 0
                for page_no, page in enumerate(pages, 1):
                    for p in page:
                        tile_no += 1
                        w.writerow({"photo_id": p, "labeller": labeller,
                                    "group_id_human": sid if sid.startswith("g") else "",
                                    "group_id_agent": agent_gid.get(p, ""), "sheet": sid,
                                    "page": page_no if len(pages) > 1 else "", "tile": tile_no,
                                    "second_labeller": int(second),
                                    "clicked_at": manifest.get(p, {}).get("clicked_at", "")})
                        n += 1
                        n_burst += int(sid.startswith("g"))
                    if not args.no_sheets and not (team and (args.sheets_dir / ev).exists()):
                        imgs = []
                        for p in page:
                            raw = (src.read(f"proxy/{ev}/{p}.jpg") or
                                   src.read(f"raw/{ev}/{p}.webp") or
                                   src.read(manifest.get(p, {}).get("processed_key") or ""))
                            imgs.append(cv2.imdecode(np.frombuffer(raw, np.uint8), cv2.IMREAD_COLOR) if raw else None)
                        d = args.sheets_dir / ev
                        d.mkdir(parents=True, exist_ok=True)
                        first = tile_no - len(page) + 1
                        sheet = contact_sheet(imgs, start=first)
                        stem = sid if len(pages) == 1 else f"{sid}_p{page_no}"
                        cv2.imwrite(str(d / f"{stem}.jpg"), sheet, [cv2.IMWRITE_JPEG_QUALITY, 85])
                        n_pages += 1
        n_groups = sum(1 for s_, _, _ in sheets if s_.startswith("g"))
        sizes = sorted((len(p) for s_, p, _ in sheets if s_.startswith("g")), reverse=True)
        rec = {"event": name, "target": target, "burst_share": share, "photos": n, "in_bursts": n_burst,
               "singletons": n - n_burst, "groups": n_groups, "largest_groups": sizes[:5],
               "overlap_photos": sum(len(p) for _, p, sec in sheets if sec), "sheet_pages": n_pages,
               "groups_available": len(groups), "singletons_available": len(singles),
               "shortfall": max(0, target - n)}
        report["events"].append(rec)
        print(f"{name}: {n} photos = {n_burst} in {n_groups} whole bursts + {n - n_burst} singletons; "
              f"overlap {rec['overlap_photos']}; largest {sizes[:3]}; shortfall {rec['shortfall']} -> {out}")
        grand += n
    report["total"] = grand
    plan_name = "sample_plan.team.json" if team else "sample_plan.json"
    (args.labels_dir / plan_name).write_text(json.dumps(report, indent=2) + "\n")
    print(f"total {grand} photos across {len(plan)} events")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
