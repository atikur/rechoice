# Foundation retest: unit plan for approval

Drafted 28 October 2026 and approved by the owner the same day for up to 3 units. The probe read no closer, so the other two shades were not run.

## Result, 28 October 2026

- **The probe was 220W on Face A, at colour 100, coverage 80 and glow 10.** It succeeded in 18 s for 1 unit, reconciled against the unit history.
- **It read no closer to the swatch.**
  - Re-measured from the stored files, it is ΔE 8.5 from the swatch, against 7.4 in the pilot.
  - The live shade check gave 14.7.
- **It changed almost nothing.**
  - Face A's cheek is L* 55.4 in the photo, 62.7 after the pilot and 63.3 after the retest.
  - The swatch is L* 73.9, much lighter than her skin.
  - Foundation in Makeup VTO blends with the skin beneath it. It evens and lightens the skin, but it does not paint the swatch colour, whatever the strength. Visually the retest is slightly more even than the pilot, with no mask-like look.
- **Not run:** pairs 8 and 9, as the approval required. There was no retry.
- **Decision.** The adapter keeps the pilot's settings, 70, 50 and 30, which the published results reflect. The code records why. The published foundation comparison stays, and `/evidence` keeps foundation as the weak spot.
- **Spend:** lifetime spend is 47 units, today's (UTC) 14, and the balance 993.

## Why

Foundation is our weakest published result. These are the readings from the beauty pilot on Face A, 27 October 2026:

| Shade | Requested | Measured | ΔE |
|---|---|---|---|
| 220W Light Warm, the shade bought | #d9ae7f | #c49979 | 7.4 |
| 210C Light Cool | #dcb2a3 | #caa18a | 6.0 |
| 310C Tan Cool | #b8877a | too subtle to measure | – |

- **The readable shades are off.** Both came out darker and duller than the shade.
- **Two changed too little to measure:** 310C, and 410N on Face B.
- **The other beauty results are fine.** Lipstick and nail polish read within ΔE 5.

## The change tried

Makeup VTO's foundation takes three settings, each from 0 to 100, as YouCam's `makeup_vto.json` defines them:

| Setting | Pilot | Retest |
|---|---|---|
| `colorIntensity` | 70 | 100 |
| `coverageIntensity` | 50 | 80 |
| `glowIntensity` | 30 | 10 |

The retest uses full colour, high coverage, and a low glow that suits the line's natural finish.
- **New jobs only.** The retest ran as `makeup-vto-1.2`, so it made new jobs and never reused the pilot's. After the result, the adapter went back to the pilot's settings and `makeup-vto-1.1`.
- **Lipstick is unchanged.** Its payload stays as it was.

## What it spends

**3 units in total,** 1 per foundation preview.
- **Lifetime spend:** goes from 46 units to 49, under the proposed lifetime ceiling of 300.
- **Balance:** about 994 before and 991 after, well above the 700 floor.
- **Today (UTC):** 13 units have been used today, so this run's daily cap is 16.
- **No contingency:** a retry after a failure needs a new approval.

## The 3 pairs

These are the published foundation comparison on Face A, under their own label, `beauty-retest`:

| # | Shade | Why |
|---|---|---|
| 7 | 220W Light Warm | **Probe.** The shade bought, and the worst reading (ΔE 7.4) |
| 8 | 210C Light Cool | The first cooler suggestion |
| 9 | 310C Tan Cool | The second cooler suggestion, too subtle to measure before |

## How it runs

1. **Probe, 1 unit:**
   ```bash
   manage.py run_feasibility --set beauty --label beauty-retest --execute --pairs 7 --confirm-units 1
   ```
   The run continues only if the probe looks right:
   - the colour stays on the skin
   - the face is otherwise unchanged
   - the shade check reads closer than ΔE 7.4
2. **The other 2, for 2 units,** only if the probe looks right:
   ```bash
   manage.py run_feasibility --set beauty --label beauty-retest --execute --pairs 8,9 --confirm-units 2 --stop-after-failures 1
   ```
3. **Reconcile, 0 units:** a read-only copy of YouCam's unit history records what each task actually cost.

## Guards

These are the same as for the earlier pilots.
- **One-off settings.** Only the run's own process gets these settings; `config/settings.py` is not edited:
  - `PREVIEW_UNIT_CEILING=49` (46 already spent plus 3)
  - `PREVIEW_DAILY_UNIT_CAP=16` (13 already used today plus 3)
  - `YOUCAM_BALANCE_FLOOR=700`
  - the API key, read from `.env` into that process and never printed
- **No automatic retry.**
- **Inputs are checked first.** The runner refuses to start if Face A's file no longer matches its committed hash.

## Afterwards

- **If the retest reads closer,** it replaces the published foundation comparison:
  - `/evidence` and replay show the new results.
  - The accuracy section is measured again.
  - HANDOFF keeps the pilot's first readings.
- **If it does not,** the published results stay, and `/evidence` keeps saying foundation is the weak spot.

## Known risks

The probe will show whether these matter.
- **A mask-like look.** Higher coverage may look flat and hide the skin's texture.
- **Overshoot.** Full colour may push the cooler shades too far.
- **Whole-image brightening.** Makeup VTO lightens the whole image by about 5 L*. Showing the shade bought on the same face cancels this out in the comparison.
