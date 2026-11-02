# Beauty pilot: unit plan for approval

Drafted 27 October 2026. The owner approved it the same day for up to 13 units.

## Probe run, 27 October 2026

- **Pair 7, foundation:** succeeded, 1 unit.
- **Pairs 1 and 11, lipstick and nails:** refused with `InvalidParameters` before any task was created. They were not charged, as the unit history confirms.
- **Cause.** The payloads lacked fields that YouCam's OpenAPI definitions require:
  - lip colour: `shape` and `style`
  - nail polish: `effect_type`, and on each finger `reflection`, `contrast`, `roughness` and `transparency`
- **Fixed in the adapter**, and every payload below is checked against those definitions.
- **Spent so far:** 1 unit.
- **Still to run:** pairs 1 and 11 again (2 units), then the other 10 (10 units). That keeps the total at 13. A retry waits for the owner's go-ahead.

## Completed run, 27 October 2026

- **Retry and run.** With the owner's go-ahead, pairs 1 and 11 were retried with the fixed payloads, and then the other 10 ran.
- **All 13 succeeded,** for exactly 13 units, reconciled against the unit history.
- **Lip colour and nail polish** look right on every result.
- **Foundation** moves the right way but subtly. The deep shade barely showed on Face B.
- **Shade check:** close on every result it could read (ΔE 1.9 to 7.5). It could not read the four subtlest changes.
- **Details:** in the HANDOFF entry for 27 October 2026.

## What it spends

**13 units in total**, at 1 unit per result. The read-only feature-cost check on 27 October 2026 confirmed these prices.

| Feature | Results | Units |
|---|---|---|
| Makeup VTO, lip colour | 6 | 6 |
| Makeup VTO, foundation | 4 | 4 |
| Nail VTO | 3 | 3 |
| **Total** | 13 | **13** |

- **Lifetime spend:** goes from 20 units (the clothes pilot) to 33, against the proposed lifetime ceiling of 300.
- **Balance:** about 1,020 before the run and 1,007 after, well above the 700 floor.
- **No contingency:** the approval covers 13 units only. A retry after a failure needs a new approval.

## The 13 pairs

Every person is an owner-approved, AI-generated demo image published in `exchange/static/exchange/models/`. The runner checks each one against its committed hash before running.

| # | Person | Shade | Feature | Why |
|---|---|---|---|---|
| 1 | Face A | Velvet Matte 230 Terracotta, #b5532f, matte | Makeup VTO | **Lip probe.** Confirms the lip payload. It is the purchased shade in the demo |
| 2 | Face A | Velvet Matte 220 Rosewood, #a04a55 | Makeup VTO | The demo's first cooler shade |
| 3 | Face A | Velvet Matte 240 Mauve, #9c5a6e | Makeup VTO | The demo's second cooler shade. With pairs 1 and 2, a complete recorded comparison |
| 4 | Face B | 230 Terracotta | Makeup VTO | The same shade on a second face, in warm backlight |
| 5 | Face C | 230 Terracotta | Makeup VTO | The same shade on a third face, in dark side light |
| 6 | Face C | Satin Cream 10 Petal, #c4707f, cream | Makeup VTO | A light pink in the cream finish. Confirms the cream texture value |
| 7 | Face A | Skin Veil 220W Light Warm, #d9ae7f | Makeup VTO | **Foundation probe.** Confirms the foundation payload. It is the purchased shade in the demo |
| 8 | Face A | Skin Veil 210C Light Cool, #dcb2a3 | Makeup VTO | The demo's first cooler shade |
| 9 | Face A | Skin Veil 310C Tan Cool, #b8877a | Makeup VTO | The demo's second cooler shade. With pairs 7 and 8, a complete recorded comparison |
| 10 | Face B | Skin Veil 410N Deep Neutral, #7e5a43 | Makeup VTO | The deepest shade in the line, on a second face, to test the far end of the range |
| 11 | Hand A | Gloss Lacquer 01 Coral Crush, #ef6f5a, gloss | Nail VTO | **Nail probe.** Confirms the nail payload and whether the demo hand is accepted. It is the purchased shade |
| 12 | Hand A | 02 Cherry, #b3203a | Nail VTO | The demo's first cooler shade |
| 13 | Hand A | 04 Lilac, #b9a3d6 | Nail VTO | The demo's second cooler shade. With pairs 11 and 12, a complete recorded comparison |

The shades in pairs 2, 3, 8, 9, 12 and 13 are the ones the shopper page suggests today for "cooler" on each item. That way, the pilot records the demo's real comparisons.

## How it runs

The commands below are given exactly as they will run. Each command refuses to start unless `--confirm-units` equals its estimate and the run fits under both caps.

1. **Probes, 3 units:**
   ```bash
   manage.py run_feasibility --set beauty --execute --pairs 1,7,11 --confirm-units 3 --stop-after-failures 2
   ```
   Each probe runs one feature's payload for the first time. If any probe fails or looks wrong, the run stops there and the result is reported before any further unit.
2. **The rest, 10 units,** only if all three probes succeed and their results look right:
   ```bash
   manage.py run_feasibility --set beauty --execute --pairs 2,3,4,5,6,8,9,10,12,13 --confirm-units 10 --stop-after-failures 2
   ```
3. **Reconcile, 0 units:** a saved, read-only copy of the unit history replaces the assumed charges with the real ones:
   ```bash
   manage.py run_feasibility --set beauty --reconcile FILE
   ```

## Guards

- **One-off settings.** Only the run's own process gets these settings; `config/settings.py` is not edited, so a normal start still refuses YouCam:
  - `PREVIEW_PROVIDER=youcam`
  - `PREVIEW_LIVE_APPROVAL`, set to the owner's approval
  - `PREVIEW_UNIT_CEILING=33` (20 already spent plus 13)
  - `PREVIEW_DAILY_UNIT_CAP=13`
  - `YOUCAM_BALANCE_FLOOR=700`
  - the API key, read from `.env` into that process and never printed
- **No automatic retry.** A failed or uncertain task is counted as charged until reconciled, and it is never resent automatically.
- **Inputs are checked first.** The runner stops before any write if:
  - a person's file no longer matches its committed hash
  - a pair puts a shade on the wrong kind of person
  - a shade is missing from the catalog

## What the request looks like

These are the exact request bodies for the three probes, as `secondlook/youcam_provider.py` builds them:

- **Lip, pair 1:** `makeup-vto` with `{"category": "lip_color", "shape": {"name": "original"}, "style": {"type": "full"}, "palettes": [{"color": "#b5532f", "colorIntensity": 70, "texture": "matte"}]}`
- **Foundation, pair 7:** `makeup-vto` with `{"category": "foundation", "palettes": [{"color": "#d9ae7f", "colorIntensity": 70, "coverageIntensity": 50, "glowIntensity": 30}]}`
- **Nails, pair 11:** `nail-vto` with `"effect_type": "nail_polish"` and five effects, one per finger. Each is `{"sub_type": "color", "finger": "thumb", "color": "#ef6f5a", "texture": "cream", "transparency": 0, "reflection": 70, "contrast": 50, "roughness": 10}`, with the finger changing from thumb to pinky.

These follow YouCam's published OpenAPI definitions, which list fields the reference pages leave out. The adapter accepts both documented result shapes.

## What it decides

- **Payloads.** Whether each feature accepts the payload, and whether the AI-generated faces and hand are accepted.
- **Review.** Each result gets a score from the owner, recorded in the private report:
  - colour on the right area only: 0 to 2
  - reads as the requested shade: 0 to 2
  - the person otherwise unchanged
  - usefulness

  A feature is switched on only if every one of its results passes this gate:
  - the colour is on the right area alone
  - it reads as the requested shade
  - the face or hand is otherwise unchanged
  - delay and cost fit the approved budget
- **Shade check.** The shade check's raw readings for each result go into the report. They are used to calibrate the check before shoppers see it.
- **Recorded results.** Three complete comparisons, published later with no new spend:
  - lipstick on Face A
  - foundation on Face A
  - nail polish on Hand A

  One change is needed first: replay looks results up by product line, which would merge shades of the same line, so it must look up shades by SKU.

## Out of scope

- **Blush.** There is no blush line in the demo catalog.
- **Reserve images.** Faces D, E and F and Hand B are used only if a main image fails, and only with a new approval.
