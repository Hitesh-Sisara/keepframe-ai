# KeepFrame brand kit

## The mark

Three frames from one burst. The kept frame is lifted to the front. The two frames behind it sit lower and peek out at the sides. That is the product in one shape: group the burst, keep the best frame.

Construction, on a 32-unit grid (1 unit = 0.5 px at 16 px, so every edge is pixel-crisp):

| Element | x | y | w × h | radius |
|---|---|---|---|---|
| Tile | 0 | 0 | 32 × 32 | 7 |
| Kept frame | 10 | 6 | 12 × 18 (2:3) | 2 |
| Burst frames | 4 and 24 | 10 | 4 × 16 | 1.5 |

- The gap between the kept frame and the burst frames is 2 units. Never let it drop below 1 px when the mark is rendered.
- The kept frame sits 4 units higher than the burst frames and ends 2 units above their bottoms. That offset is the lift. Keep it.

## Files

| File | Use |
|---|---|
| `logo-mark.svg` | Primary mark: accent tile with white glyph |
| `logo-mark-bare.svg` | Glyph only, in accent, no tile |
| `logo-wordmark.svg` | Outlined wordmark only |
| `logo-lockup-horizontal.svg` | Mark + wordmark for light backgrounds |
| `logo-lockup-horizontal-dark.svg` | Same lockup, white wordmark, for dark backgrounds |
| `logo-mono-black.svg`, `logo-mono-white.svg` | One-colour lockups (the glyph is knocked out of the tile) |
| `favicon.svg`, `favicon-32.png` | Browser tab |
| `apple-touch-icon-180.png`, `icon-512.png` | Full-bleed square app icons. The OS applies its own corner mask. |
| `og-image-1200x630.png` | Social share card |
| `Logo.tsx` | Drop-in React component (`LogoMark`, `Wordmark`) |
| `concepts/` | Concept rounds and review sheets |

## Wordmark

The wordmark is Geist SemiBold, outlined to paths so it renders without the font installed.

- Tracking is −10/1000.
- Hand kerning, in font units: K–e −4, e–e +4, p–F −24, F–r −42, r–a −6, a–m −2.
- Do not retype it as live text in logo use.

In the lockup:

- The wordmark cap height is 14/32 of the mark height.
- It is centred on the tile.
- The gap between mark and wordmark is 12/32 of the mark height.

## Clear space and minimum size

- Clear space on every side is half the mark height, which is 16 units on the 32 grid. Keep other elements out of it.
- The minimum mark size is 16 px.
- The minimum lockup height is 24 px. Below that, use the mark alone.

## Colour

Every value comes from `ui/app/globals.css` (`--kf-*`). Contrast ratios are WCAG 2.x and were computed, not estimated.

| Token | Hex | Role | Contrast |
|---|---|---|---|
| accent | `#2457d6` | Brand, primary actions, mark tile | 6.16 on white, 5.75 on bg |
| accent-hover | `#1d47b3` | Hover/pressed | 8.06 on white |
| accent-text | `#1f4bbb` | Accent text on accent-soft | 6.61 on `#eaf0fd` |
| accent-soft | `#eaf0fd` | Selected rows, chips | |
| text | `#14171c` | Ink, wordmark | 17.96 on white |
| muted | `#5b6370` | Secondary text | 6.06 on white, 5.66 on bg |
| faint | `#6e7683` | Tertiary text, small | 4.58 on white (AA, white only) |
| bg | `#f6f7f9` | Page | |
| surface | `#ffffff` | Cards | |
| border | `#e3e6eb` / `#cfd4dc` strong | Dividers. Never use for text (1.49:1). | |

Semantic colours for culling decisions. Each is AA (≥ 4.5:1) as text on white and on its soft tint, and with white text on top.

| Decision | Solid | Soft | Text on white | Text on soft | White on solid |
|---|---|---|---|---|---|
| Keep | `#157347` | `#e6f4ec` | 5.87 | 5.18 | 5.87 |
| Review (close call) | `#a15c00` | `#fdf2e1` | 5.19 | 4.69 | 5.19 |
| Reject | `#c42b2b` | `#fcebeb` | 5.63 | 4.89 | 5.63 |

I kept the accent `#2457d6`. It clears AA in both directions and sits well apart from the keep green and reject red, so a selected state never reads as a decision. No alternative was clearly better.

Don't rely on colour alone for keep, review or reject. Always pair it with a label or icon.

## Typography

- **Geist** for everything: UI, headings and marketing. Use SemiBold (600) for headings, Medium (500) for display copy, and Regular (400) for body.
- **Geist Mono** only for real data such as filenames, EXIF and scores.
- Display sizes take −0.02em tracking. Body text uses default tracking.
- Use sentence case throughout.

## Do

- Use the tile mark on light or dark backgrounds, and the bare glyph where a tile would be redundant (for example, inside an already-accent button).
- Use the mono lockups for single-colour print, embossing and co-branded placements.
- Keep the kept frame visibly in front, separated by the gap.

## Don't

- Don't recolour the burst frames differently from the kept frame. The story has to work in one colour.
- Don't rotate, skew or add shadows, gradients or outlines to the mark.
- Don't stretch the wordmark, retype it, or change its kerning.
- Don't place the accent tile on accent-coloured backgrounds. Use `logo-mono-white.svg` there.
- Don't add a camera, eye or check icon next to the mark.
