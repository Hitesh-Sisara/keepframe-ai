# Label guide

You are labelling event photos the way you would cull them before delivery. The labels are the
ground truth for `eval/compute_metrics.py` (`docs/EVALUATION.md` §1). Label from the photo, not from what the
agent said; do not open the agent's verdicts while labelling.

## Files

- Work from `eval/labels/<eventId>.todo.csv` (made by `eval/sample_for_labelling.py`). Save your
  result as `eval/labels/<eventId>.csv`. These files are gitignored: they contain photo ids from a
  customer event. Never commit them, screenshot them or paste them into docs.
- Contact sheets are in `eval/contact_sheets/<eventId>/<sheet>.jpg`. The `sheet` and `tile` columns
  in the CSV point to them. Open the full 1200 px image when a call depends on detail (eyes, focus).
- The `/label` page in the review UI writes the same columns, if you use it instead.

## Columns you fill

| column | value |
|---|---|
| `group_id_human` | Same id for frames of the same moment (burst or near-duplicate: same people, same pose/expression, same framing within a few seconds). Prefilled with the sheet id as a suggestion. Split a sheet into `g0012a` / `g0012b` if it holds two moments; give the same id to frames on different sheets that are really one moment. Leave empty for a true singleton. |
| `verdict_human` | `keep` or `reject`. Reject = you would not deliver it to the client. Nothing is "review" here; make the call. |
| `reasons_human` | For rejects, one or more codes separated by `;`, from the list below. Leave empty for keeps (except the note below). |
| `best_of_group_human` | `1` on exactly one frame per group (the one you would deliver if you could only send one), `0` elsewhere. Singletons: `0`. |
| `labeller` | Prefilled with your class: `photographer` (or `photographer:<initials>`) if you are the photographer who would cull this event; `team:<initials>` if you are on the team. Keep it the same on every row. Only `photographer` rows are ground truth. |
| `labelled_at` | ISO time, e.g. `2026-10-15T10:30:00Z`. |
| `notes` | Free text: why it was hard, "artistic", "intentional eyes closed", etc. |

## Reason codes

| key | code | use when |
|---|---|---|
| 1 | `BLUR_MOTION` | Subject smeared by camera shake or subject motion (directional streaks). |
| 2 | `BLUR_FOCUS` | Missed focus: the subject is soft, something else is sharp or nothing is. |
| 3 | `EYES_CLOSED` | The main subject's eyes are closed or mid-blink. |
| 4 | `EXPOSURE_UNDER` | Too dark to rescue in a normal edit. |
| 5 | `EXPOSURE_OVER` | Blown highlights on the subject. |
| 6 | `LOW_CONTRAST` | Flat or hazy to the point you would not deliver it. |
| 7 | `BURST_NOT_BEST` | A fine frame, but a better near-identical frame exists in the same group. |
| 8 | `DUPLICATE_OF:<photo_id>` | An exact or almost exact copy of another frame. `DUPLICATE_OF` without the id is accepted. |
| 9 | `NO_SUBJECT` | Accidental frame: floor, ceiling, lens cap, someone's back. |

Use the most specific code first; add a second if both apply (`BLUR_MOTION;EYES_CLOSED`).

## Rules

1. One keep per group minimum. Even if every frame in a burst is weak, keep the best one and mark it
   `best_of_group_human=1`.
2. Reject only what you would not deliver. "Slightly worse than its neighbour" in a burst is
   `BURST_NOT_BEST`, not a blur code.
3. Artistic motion blur (dance-floor drag shutter, panning, light trails) that you would deliver
   is `keep` with `notes=artistic`. Do not put a blur code on a keep.
4. Intentional closed eyes (prayer, first kiss, sleeping newborn, laughing squint) are `keep`
   with a note.
5. Group photos: reject for `EYES_CLOSED` only if you really would not deliver it. One guest
   blinking in a group of eight is often still a keep; say so in the note.
6. Dark receptions and candle-lit frames are often keeps. Use `EXPOSURE_UNDER` only when it is
   not rescuable.
7. Judge at delivery size (the 1200 px view), not at 400 % zoom.
8. When unsure, decide anyway and write `unsure` in the notes. The notes feed the failure-case
   catalogue (`docs/EVALUATION.md` §2).

## Photographer or team?

The report's accuracy figures use photographer labels only. A team member's labels are welcome but are
reported separately as "team labels (not photographer)", with their own agreement against the worker and
the agent.

- Photographer: work from `eval/labels/<eventId>.todo.csv`, save as `eval/labels/<eventId>.csv`.
- Team member: `python eval/sample_for_labelling.py --labeller team:<initials> --no-sheets
  --root <mirror>` writes `eval/labels/<eventId>.team.todo.csv` (the same photos and sheets). Save it as
  `eval/labels/<eventId>.team.csv`.
- CSV exported from the `/label` page with free-text initials: fix the class with
  `python eval/sample_for_labelling.py --stamp eval/labels/<eventId>.csv --labeller photographer:<initials>`
  (or `team:<initials>`).
- Never put `photographer` on labels you made as a team member, and never label from the agent's verdicts.

## Second labeller

Rows with `second_labeller=1` (about 20 % of photos, whole sheets) are labelled by both people
independently. Each person writes their own rows with their own `labeller` value. Both sets go in the
same `<eventId>.csv` (one row per photo per labeller). Do not discuss these sheets until both are
done. `compute_metrics.py` reports Cohen's kappa on the overlap. The labeller with more rows is the
primary ground truth.

## Pace

Aim for about 4-6 seconds per frame on clear cases. Time yourself on the first 300 frames for the
time study (`eval/time_study_template.md`).
