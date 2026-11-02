# Hair colour pilot: unit plan for approval

Drafted 28 October 2026, after the owner approved the hair colour work, and approved for up to 4 units the same day. The pilot ran that day.

## Result, 28 October 2026

**4 of 4 tasks succeeded, for 4 units, reconciled against the unit history; 3 are published.**
- **The probe ran first:** 7.3 Golden Blonde on Model A colours the hair alone, face and tee unchanged, in 17.5 s. It comes out a brighter, more saturated gold than its swatch.
- **7.1 Ash Blonde and 6.1 Dark Ash Blonde** both come out as a cool grey-blonde, clearly cooler than the gold, but close to each other: the step between them is smaller on screen than in the catalog.
- **9.1 Very Light Ash Blonde on Model C's dark curly hair barely changed it.** The engine tints hair rather than lifting it, so a shade far lighter than the hair in the photo does not show. It stays private as a finding.
- **The shade check is not calibrated for hair.** It reads the dominant changed colour, and shaded strands dominate, so it called the ash blonde dark (ΔE 31). The raw readings are published on `/evidence`, labelled as uncalibrated, and none reaches the shopper, as for every beauty shade check so far.
- **Timing:** 17 to 18 s each.
- **Spend:** lifetime 57 units, today's (UTC) 24, balance 983.
- **Published** as recorded results on `/evidence` and in replay: the shade bought and the two cooler shades on Model A.


## What was checked, read-only

YouCam's `ai_hair_color.json` and reference page, read 28 October 2026:
- **Endpoint:** `POST /s2s/v2.0/task/hair-color`, polled at `/{task_id}`, with the same file upload as the other features.
- **Request:** `src_file_id` plus either a named preset or a `pattern` of `full` with one `palettes` entry: a hex colour, `color_intensity` and `shine_intensity`. We send the catalog shade's hex at intensity 80 and shine 30. Ombre mode exists; a catalog shade is one colour.
- **Cost:** 1 unit in full mode.
- **The photo:** must show the hair, long side under 1920 px, face at least 100 px wide. Our half-body models fit; the face close-ups show little hair, so hair colour goes on Models A, B and C.

## What is built, with no spend

- **A hair dye line** in the beauty flow: Tone Permanent Hair Colour, seven shades in the trade's level.tone numbering, from 4.0 Medium Brown to 9.1 Very Light Ash Blonde. The shopper says lighter or darker, warmer or cooler, softer or bolder, and the shades are ranked by colour distance, as for lipstick. Nothing is read from the person. The line has no reds: by hue a red counts as cooler than a golden blonde, which fits lipstick but not what "too brassy" means for hair.
- **7.3 Golden Blonde** is in the demo order as `DEMO-ORDER-001-HAIR`, opened, so it exchanges within its line, as the policy allows for opened products.
- **AI Hair Color** as a provider, `youcam-hair`, at 1 unit, with the shade check on every result.
- **The page** asks for a photo with the hair down and offers the half-body demo people.
- **Looks** leave hair colour out for now, and say so.

## What it spends

**4 units, 1 per shade,** under the label `hair-pilot`:

| # | Person | Shade | Why |
|---|---|---|---|
| 1 | Model A | 7.3 Golden Blonde | **Probe.** The shade bought, on blonde hair: confirms the payload and that the hair is found |
| 2 | Model A | 7.1 Ash Blonde | The demo's first cooler shade for "too brassy" |
| 3 | Model A | 6.1 Dark Ash Blonde | The second cooler shade; with 1 and 2, a complete recorded comparison |
| 4 | Model C | 9.1 Very Light Ash Blonde | A very light shade on dark, curly hair: the far end of the range on a second person |

- **Lifetime spend:** 53 to 57 units.
- **Today (UTC):** 20 used, so the daily cap is 24.
- **Balance:** about 987 to 983, above the 700 floor.
- **No contingency:** a retry needs a new approval.

## How it runs

1. **Probe, 1 unit:** `run_feasibility --set hair --execute --pairs 1 --confirm-units 1`. It stops there unless the colour sits on the hair alone and the face and clothes are unchanged.
2. **The other three, 3 units:** `--pairs 2,3,4 --confirm-units 3 --stop-after-failures 1`.
3. **Reconcile, 0 units,** from the read-only unit history.
4. **Publish, 0 units:** the comparison on `/evidence` and in replay, and the accuracy section re-measured with the hair shades.

## Guards

One-off settings for the run's process only: `PREVIEW_UNIT_CEILING=57`, `PREVIEW_DAILY_UNIT_CAP=24`, `YOUCAM_BALANCE_FLOOR=700`, the key read from `.env` and never printed. No automatic retry. Inputs are hash-checked first.

## Known risks

- **Dark hair.** A very light blonde on Model C's dark hair is a lift, not a dye; pair 4 is there to see how the engine handles the far end, and the page will say the result is approximate either way.
- **Intensity.** 80 may read as a wig on blonde hair or barely show on dark hair. The probe and pair 4 bracket it.
- **The shade check** reads the dominant changed colour; hair has highlights and shadow, so its ΔE will run higher than lipstick's. The reading is published either way.
