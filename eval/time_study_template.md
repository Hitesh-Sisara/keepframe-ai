# Time study (`docs/EVALUATION.md` §2 "Time saved")

Copy to `eval/results/time_study.md` and fill in. Only aggregate numbers go in the committed copy:
no photo ids, no thumbnails.

## Setup

| item | value |
|---|---|
| Photographer (initials) | |
| Culling experience (years) | |
| Tool used for the manual pass | (e.g. Lightroom, Photo Mechanic, the platform's web app) |
| Machine / screen | |
| Event A (manual) category, frames | |
| Event B (assisted) category, frames | |
| Date | |

Use two different events of the same category, or two disjoint 300-frame slices of one event, so
the photographer has not seen the assisted frames before. Same machine, same screen, no breaks
inside a timed block.

## Manual baseline (300 frames, unaided)

| measure | value |
|---|---|
| Start / end time | |
| Wall minutes | |
| Frames kept / rejected | |
| Minutes per 1,000 frames (= wall min / 300 x 1000) | |

## Assisted (300 different frames, reviewing KeepFrame output)

The photographer opens the review UI for the event and (a) answers every approval request,
(b) skims keeps and rejects and overrides what they disagree with.

| measure | value |
|---|---|
| Start / end time | |
| Wall minutes | |
| Approval requests answered | |
| Overrides of agent keep -> reject | |
| Overrides of agent reject -> keep | |
| Frames that needed a human look (approvals + overrides) / 300 (%) | |
| Minutes per 1,000 frames | |

## Result

| | manual | assisted |
|---|---|---|
| minutes per 1,000 frames | | |
| % of frames needing a human look | 100 % | |

Time saved = 1 - assisted / manual = ____ %. Report it as measured on one photographer and
two events. It is not a population estimate.

## Notes

What slowed the assisted pass down (UI, waiting for contact sheets, disagreements by reason)?
