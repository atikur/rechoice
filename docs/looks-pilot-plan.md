# Looks pilot: unit plan for approval

Drafted 28 October 2026 and approved by the owner the same day for up to 12 units. The probe stopped at its earrings step.

The owner then made looks the top and lipstick only, leaving the earrings out. The probe's first two steps became look 1, and looks 2 and 3 were approved for up to 6 units: ceiling 46, daily cap 13, floor 700.

## Result, 28 October 2026

**All 3 looks succeeded, for 9 units in total, reconciled against the unit history.** That is 3 per look: Clothes V4 2 and Makeup VTO 1.
- **The looks:**
  1. Ribbed Turtleneck, the probe's first two steps
  2. Sage Knit Top
  3. Midnight Floral Blouse
- **Timing:** each look took about 37 s, 19 s for the top and 18 s for the lipstick.
- **Spend:** lifetime spend is now 46 units, today's (UTC) 13, and the balance 994.
- **What they show:**
  - Each top keeps its design.
  - The lipstick is on the lips alone.
  - Model A stays recognisably the same person across all three.
- **One flaw:** Clothes V4 left a small pink smudge on the jeans in look 1.
- **Shade check:** it could not read the lips, which are too small on a chest-up photo.
- **Published,** at no unit cost, as recorded looks: `looks.json` and both steps of each look on `/evidence`. They replay on Model A in the shopper flow.

## Probe, 28 October 2026

- **Steps 1 and 2 worked.**
  - Clothes V4 put the Ribbed Turtleneck on Model A in 19 s.
  - Makeup VTO then added the 230 Terracotta lipstick to that result in 17 s.

  So two features chain on one photo.
- **Step 3 failed.** Earrings VTO refused the photo: "[PHOTO_CHECK_INVALID] Ear size should fit in range and position should be in the center." On a chest-up photo the ear is too small and off-centre.
  - The poll recorded only "4". Reading the task afterwards, read-only, gave the message.
  - The adapter now keeps such a bracketed code, and the code counts as a photo problem.
- **Spend: 3 units, reconciled against the unit history:** Clothes V4 2 and Makeup VTO 1. YouCam charged the earrings task 1 unit and refunded it when the task failed.
  - The reconcile had counted only charges, so it showed 4. It now nets refunds.
  - Lifetime spend is 40 units and the balance is 1,000.
- **Shade check.** It could not read the lips: on a chest-up photo they cover too few pixels at the check's working size. The lipstick change is visible.
- **Not run:** looks 2 and 3, as approved: they were to run only if the probe looked right. There was no retry.

## What a look is, as first planned

A look was to show the replacement next to what the shopper is keeping, on the same person. The top comes from Clothes V4, then the lipstick from Makeup VTO, then the earrings from Earrings VTO. Each step runs on the result of the step before. After the probe, looks became the top and lipstick only.

The items come from the demo order: the lipstick is 230 Terracotta and the earrings are the Baroque Pearl Drops.

Nail polish and foundation are left out, and the page says why:
- **Nail polish** needs a photo of a hand.
- **Foundation** barely shows on a photo of this size.

## What it spends

**12 units in total, 4 per look:**

| Feature | Units per look |
|---|---|
| Clothes V4 | 2 |
| Makeup VTO | 1 |
| Earrings VTO | 1 |

These prices come from the read-only feature-cost checks.

- **Lifetime spend:** goes from 37 units to 49. That is under the proposed lifetime ceiling of 300 and the feasibility target of below 50.
- **Balance:** about 1,003 before and 991 after, well above the 700 floor.
- **Today (UTC):** the earrings pilot already used 4 units today, so this run's daily cap is 16.
- **No contingency:** a retry after a failure needs a new approval.

## The 3 looks

All three run on Model A, the published, AI-generated demo person. Model A suits a look:
- the photo is chest-up
- she wears no sunglasses
- her face is clear
- one ear is visible

The other two models wear sunglasses, and the face close-ups don't show the upper body that Clothes V4 needs.

| # | Top | Why |
|---|---|---|
| 1 | Ribbed Turtleneck (CALM-07) | **Probe.** It confirms that the three features chain on one photo. It is also the judge path's first suggestion |
| 2 | Sage Knit Top (CALM-04) | The judge path's second suggestion |
| 3 | Midnight Floral Blouse (ORIGINAL) | The top bought, for "What you have now". It is made the same way, so the comparison is fair |

Each look then adds the 230 Terracotta lipstick and the Baroque Pearl Drops.

## How it runs

1. **Probe, 4 units:**
   ```bash
   manage.py run_feasibility --set looks --execute --pairs 1 --confirm-units 4
   ```
   If any step fails or the look looks wrong, the run stops there. I report the result before spending any further unit.
2. **Looks 2 and 3, 8 units,** only if the probe looks right:
   ```bash
   manage.py run_feasibility --set looks --execute --pairs 2,3 --confirm-units 8 --stop-after-failures 1
   ```
3. **Reconcile, 0 units:** a read-only copy of YouCam's unit history records what each task actually cost.

## Guards

These are the same guards as the earlier pilots, plus one for chained steps.

- **One-off settings.** Only the run's own process gets these settings; `config/settings.py` is not edited:
  - `PREVIEW_UNIT_CEILING=49` (37 already spent plus 12)
  - `PREVIEW_DAILY_UNIT_CAP=16` (4 already used today plus 12)
  - `YOUCAM_BALANCE_FLOOR=700`
  - the API key, read from `.env` into that process and never printed
- **Every step is checked.** Each step passes the usual checks before it starts: the pause switch, the ceiling, the daily cap and the balance floor.
- **A failed step ends its look.** The steps after it never start, so a broken look spends no more units.
- **No automatic retry.**
- **Inputs are checked first.** The runner refuses to start if Model A's file no longer matches its committed hash, or a product is missing its photo or shade.

## The requests

They are the same payloads that worked in the three pilots:
1. **Clothes V4:** `cloth-v4` with the top's catalog photo, `upper_body`.
2. **Makeup VTO:** `makeup-vto`, `lip_color` #b5532f matte, with the original lip shape and the full style.
3. **Earrings VTO:** `2d-vto/earring` with `earring_scale` 5.8.

Steps 2 and 3 upload the previous step's result as the person photo.

## Known risks

The probe will show whether any of these matter.
- **One ear.** Model A's hair covers one ear, so the earring may show on one ear only, or Earrings VTO may not find an ear.
- **A smaller face.** Makeup and earrings were tested on face close-ups. Here the face is about a quarter of the photo's width.
- **Drift between steps.** Each step redraws part of the image, so the person may change slightly from step to step. The report keeps every step's result, so this can be checked.
- **Earring size.** As measured on 28 October, the size sent does not control drop length. The look's label says size is approximate.

## After the pilot

Publishing the looks as recorded results costs no units and is a separate step. That step adds `looks.json` and the images to `/evidence` and replay, and puts the pilot date in the label.
