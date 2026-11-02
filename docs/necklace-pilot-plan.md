# Necklace pilot: unit plan for approval

Drafted 28 October 2026, after the owner approved the necklace work, and approved for up to 4 units the same day. The pilot ran that day.

## Result, 28 October 2026

**3 of 4 necklaces rendered properly, for 4 units, reconciled against the unit history.**
- **The probe ran first:** the Amethyst Y Chain hangs at the neck over the tee, pendant down the chest, design and metal kept, Model A unchanged, in 19 s.
- **The bloodstone bib and the lapis collar** sit at the collarbone and both read "in proportion" to the 55 cm chain bought: 73 and 85 px against its 106.
- **The Rose Gold Leaf Pendant failed.** The task returned success, but the engine pasted a beige block over the chest: its background removal failed on that photo, which has a soft shadow and the chain gathered to one side. **The size check caught it:** the change covered 24% of the photo, so it refused to read a size. The result stays private.
- **Timing:** 18 to 20 s each.
- **Spend:** lifetime 53 units, today's (UTC) 20, balance 987.
- **Published** as recorded results on `/evidence` and in replay: the three good ones, with the failure stated.
- **Open:** the gold pendant. A 1-unit retry with its photo cropped tight on a plain white background would show whether the photo was the cause. Until then, "too long and I wanted gold" has no recorded result and says so.


## What was checked, read-only

YouCam's `ai_necklace.json` and reference page, read 28 October 2026:
- **Endpoint:** `POST /s2s/v2.0/task/2d-vto/necklace`, polled at `/{task_id}`, with the same file upload as the other features.
- **Request:** `src_file_id`, `ref_file_ids`, `source_info`, `object_infos`, the same shape as earrings. `necklace_need_remove_background` defaults to true, so product photos go as they are.
- **Cost:** 1 unit for single-item wear.
- **The photo:** front-facing, neck visible, the neck at least 15% of the width. Model A fits.
- **The product photo:** the necklace as worn, front on. The five photos chosen show that.

## What is built, with no spend

- **A necklace category** in the jewellery flow: the shopper says shorter or longer, or gold or silver, and the catalog's lengths and metals filter the alternatives, as for earrings. Nothing is read from the person.
- **Five necklaces** from Pixabay, recorded with source, author and hash in `assets/pixabay_sources.json`, and one in the demo order:

| SKU | Necklace | Metal | Form | Length |
|---|---|---|---|---|
| NECK-AMETHYST-S55 | Amethyst Y Chain, the one bought | silver | drop | 55 cm |
| NECK-LAPIS-S45 | Lapis Collar | silver | collar | 45 cm |
| NECK-BLOOD-S40 | Bloodstone Bib | silver | collar | 40 cm |
| NECK-GEM-S50 | Teal Gem Drop | silver | drop | 50 cm |
| NECK-LEAF-G40 | Rose Gold Leaf Pendant | gold | drop | 40 cm |

- **Necklace VTO** as a provider, `youcam-necklace`, at 1 unit.
- **The size check** on every necklace result, against the one bought.
- **Looks** now have three steps: the top, the lipstick, then the necklace from the order.
- **Recorded looks** replay as far as their recording goes, so the two-step looks already published still play.

## What it spends

**4 units, 1 per necklace,** all on Model A under the label `necklace-pilot`:

| # | Necklace | Why |
|---|---|---|
| 1 | Amethyst Y Chain | **Probe.** Confirms the payload and that Model A's neck is found. The one bought |
| 2 | Rose Gold Leaf Pendant | The demo's suggestion for "too long and I wanted gold" |
| 3 | Bloodstone Bib | The suggestion for shorter in silver; with 1 and 2, a complete recorded comparison |
| 4 | Lapis Collar | A second collar, at a middling length |

- **Lifetime spend:** 49 to 53 units.
- **Today (UTC):** 16 used, so the daily cap is 20.
- **Balance:** about 991 to 987, above the 700 floor.
- **No contingency:** a retry needs a new approval.

## How it runs

1. **Probe, 1 unit:** `run_feasibility --set necklace --execute --pairs 1 --confirm-units 1`. It stops there unless the necklace hangs at the neck at a believable length and the person is otherwise unchanged.
2. **The other three, 3 units:** `--pairs 2,3,4 --confirm-units 3 --stop-after-failures 1`.
3. **Reconcile, 0 units,** from the read-only unit history.
4. **Publish, 0 units:** the comparison on `/evidence` and in replay.

## A second stage, not yet built

The published looks have two steps. Adding the necklace to them would be 1 unit per look, 3 in all, once the runner can continue a shorter recorded look. That is a separate plan.

## Guards

One-off settings for the run's process only: `PREVIEW_UNIT_CEILING=53`, `PREVIEW_DAILY_UNIT_CAP=20`, `YOUCAM_BALANCE_FLOOR=700`, the key read from `.env` and never printed. No automatic retry. Inputs are hash-checked first.

## Known risks

- **The neck.** Model A's top has a crew neck; the engine may place the necklace over the fabric or refuse the photo (`PHOTO_CHECK_INVALID`).
- **Size.** As with earrings, the engine sizes the necklace by its own rule. The size check will say how the four compare.
- **The gathered chain.** The teal gem photo has its clasp gathered at the top; it is not in this pilot.
