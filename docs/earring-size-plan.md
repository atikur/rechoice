# Earring size: calibration plan

Drafted 28 October 2026, after the owner approved the earring calibration, the necklace and the hair colour work on the same day, and approved this run for up to 4 units. The probe ran the same day and did not land right, so the other three pairs did not run.

## Probe result, 28 October 2026

- **The crystal drop, told 24 mm with the anchor at its hook, rendered 140 px: pixel for pixel the pilot's render,** which was told 38 mm with no anchor. Both ears, 140 px.
- **So `earring_scale` has no effect on this engine, and nor does the anchor.** The rule below, that the engine scales the part below the hook, is wrong. The pearl and the stud agreeing at 24 px per cm was a coincidence.
- **What does set the size** is still open. The two drops, whose photos are 960 px tall, render at 140 px; the stud, whose photo is 475 px tall, at 28 px. That is not one scale factor either, so the engine may size by the earring's type, drop or stud, or by something in the photo we have not isolated.
- **Spend:** 1 unit, reconciled. Lifetime spend is 48 units, today's (UTC) 15, and the balance 992.
- **What stands.** The size check built for this plan is unaffected and is now the honest answer: every live earring result is measured against the pair bought, and the preview says when it renders longer than its length implies.
- **The padding test, 1 unit, approved and run the same day.** The crystal photo padded to twice its height, so the earring filled half the canvas, rendered **134 px against 140 px unpadded**: a 4% change for a 100% change in the canvas. So the engine crops to the earring first and sizes it by its own rule, not by the photo's geometry, `earring_scale` or the anchor.
- **Conclusion.** The rendered size of an earring cannot be controlled from our side with this engine. A drop renders at about 140 px on Face A and a stud at about 28 px whatever we send. The size check is therefore the right answer: measure every result and tell the shopper when it is out of proportion, which the crystal always is here.
- **Spend:** 2 units for the two probes, reconciled. Lifetime spend is 49 units and the balance 991.

- **The resize, 28 October 2026, with no spend.** Since the engine will not size an earring, Rechoice now does it after the engine: an earring whose size check reads out of proportion is cut from the result by its changed pixels, its box on the photo is cleared back to the original, and it is pasted back scaled to its catalog length, its top centre kept at the lobe. The copy is measured again and kept only if it now reads in proportion. On the pilot's crystal drop, YouCam's 140 px became **92 px against 93 expected**; the published accuracy table shows 0.65 against 0.66. The shopper sees the resized earring, is told "YouCam drew it 50% longer; Rechoice resized it to its catalog length", and can switch to YouCam's own render. Necklaces are not resized: a chain runs round the neck, so scaling it from one point would break it, and the pilot's necklaces already read in proportion.
- **Why the earrings are published at full size.** An 800 px copy loses the pearl's fine chain, so replay measured the pair bought 12 px short and the crystal 63% too long. The three published earring results are now the full 1280 px results.

## What the pilot showed

Measured on the 28 October 2026 earrings pilot on Face A, relative to the 58 mm pair bought:

| Earring | Told | Rendered | Pixels per cm |
|---|---|---|---|
| Baroque Pearl Drops, 58 mm | 5.8 cm | 142 px | 24.5 |
| Blue Crystal Drops, 38 mm | 3.8 cm | 140 px | 36.8 |
| Blue Topaz Studs, 12 mm | 1.2 cm | 28 px | 23.3 |

Two of the three render at about 24 px per cm, which looked as if `earring_scale` worked. The crystal drop is the odd one out, and its photo is the odd one out too: a lever-back hook takes the top 38% of it. The pearl's hook is cut off in its photo and the stud has none.

**The rule that seemed to fit all three, and which the probe disproved:** the engine scales the part below the hook to `earring_scale` and adds the hook back. Told 3.8 cm for a drop whose visible part below the hook is 62% of the photo, the crystal came out 3.8 ÷ 0.62 = 6.1 cm, about the pearl's length. That is what we measured.

## The change, built with no spend

- **Each earring is told the length below its hook** (`ProductVariant.scale_mm`): 58, 24, 12 and 10 mm. The crystal's 24 mm is 38 mm × 0.62. The hook share is recorded with the product photo in `assets/pixabay_sources.json`.
- **The anchor is sent:** `earring_anchor_point`, the top centre of the earring in its photo, where the hook meets the ear. The adapter version is `earring-vto-1.1`, so new jobs never reuse old ones.
- **A size check on every live result**, like the colour check. The rendered height is read from the pixels that changed at each ear and compared with the pair bought, rendered on the same photo. Within 15% of the catalog proportion is "in proportion"; otherwise the preview says "renders 50% longer than its length implies". It works on any photo, because the pair bought is the yardstick; nothing is measured about the person.
- **Staff** see the status and the rendered height in the provenance table.

## What it spends

**Up to 4 units,** 1 per earring, all on Face A under the label `jewellery-sized`:

| # | Earring | Why |
|---|---|---|
| 2 | Blue Crystal Drops | **Probe.** Told 24 mm; it should now render about 93 px, 38/58 of the pearl |
| 1 | Baroque Pearl Drops | The yardstick, re-rendered the new way so the published set is one method |
| 3 | Blue Topaz Studs | Should stay in proportion |
| 4 | Pearl Rope Studs | Held out of the first pilot's comparison; a second stud |

- **Lifetime spend:** 47 to 51 units.
- **Today (UTC):** 14 used, so the daily cap is 18.
- **Balance:** about 993 to 989, above the 700 floor.
- **No contingency:** a retry needs a new approval.

## How it runs

1. **Probe, 1 unit:** `run_feasibility --set jewellery --label jewellery-sized --execute --pairs 2 --confirm-units 1`. The run continues only if the crystal lands within 15% of 93 px, measured the same way as the pilot.
2. **The other three, 3 units:** `--pairs 1,3,4 --confirm-units 3 --stop-after-failures 1`.
3. **Reconcile, 0 units,** from the read-only unit history.
4. **Publish, 0 units:** republish the earrings comparison and re-measure the accuracy section from the new label, so `/evidence` shows the before and after.

## Guards

One-off settings for the run's process only: `PREVIEW_UNIT_CEILING=51`, `PREVIEW_DAILY_UNIT_CAP=18`, `YOUCAM_BALANCE_FLOOR=700`, the key read from `.env` and never printed. No automatic retry. Inputs are hash-checked first.

## Known risks

- **The rule may be wrong.** The probe tests it with one unit before anything else runs. If the crystal still renders long, the hook share or the rule is wrong, and the size check still tells the shopper so.
- **The anchor may move the earring.** The engine's default placement worked on the pilot; the anchor is the same point, stated explicitly.
- **No absolute size.** Face A has no ruler, so the check is relative to the pair bought. That is what the shopper needs: whether the new pair is shorter, as the catalog says.
