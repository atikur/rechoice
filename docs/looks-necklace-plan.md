# Necklace in the looks: unit plan for approval

Drafted 28 October 2026, after the owner asked to close the two gaps left by the competitor re-check: measured size, and looks recorded with two steps. Approved the same day for up to 3 units, and run that day.

## Result, 28 October 2026

**3 of 3 necklace steps succeeded, for 3 units, reconciled against the unit history; all three looks are published with three steps.**
- **The probe ran first:** on look 2's sage crew neck the Amethyst Y Chain hangs at the neck about as low as on the plain photo, with the top, the lipstick and the face unchanged, in 20 s.
- **Look 3, the floral blouse bought:** the chain sits in the open collar; it is hard to pick out against the busy print.
- **Look 1, the turtleneck, was not refused.** The chain sits below the rolled collar and hangs a little lower than on the crew neck, as it would be worn.
- **The top and lipstick were not made again:** each necklace step ran on its look's recorded lipstick result. Look 1 continues from the first probe.
- **Timing:** 19.6 to 20.1 s per necklace step; about 57 s per three-step look.
- **Spend:** lifetime 60 units, today's (UTC) 27, balance 980. The three looks have cost 12 units in all.
- **Published** on `/evidence` (four across: the photo sent and each step) and in replay, replacing the two-step looks. A request for the top and lipstick alone still replays the first two steps.

## What is built, with no spend

- **A look can continue an earlier one.** `LookPreview.continues` names the earlier look, and `continued_steps` says how many of its steps the new look takes as its own first steps. The new step works on the last of those results, so the top and lipstick are neither run nor paid for again. Deleting the photo still deletes every step, because each was made from a result made from it.
- **The runner finds what is already made.** For each look, `run_feasibility --set looks` takes the longest run of succeeded first steps from any look in the same run, and estimates and confirms units for the rest only. On the local records it reports: "Already made: look 1 steps 1 to 2, look 2 steps 1 to 2, look 3 steps 1 to 2."
- **Look 1 needs this most.** Its top and lipstick were the probe's first two steps, and the probe's third step, the earrings, was refused. That look cannot be extended in place, so the new look continues from its second step.
- **Replay covers the first items of a recorded look.** Once three-step looks are published, a request for the top and lipstick alone still plays the recording's first two steps.
- **Tests on the fake API:** the three looks, as recorded, gain the necklace for exactly 3 tasks and 3 units; asking again spends nothing; each necklace step is made from the lipstick step's result; deleting the photo deletes the necklace steps.

## What it spends

**3 units, 1 Necklace VTO task per look,** all on Model A under the existing label `looks-pilot`. The numbers are the published looks':

| # | Look | Made already | Adds | Order |
|---|---|---|---|---|
| 2 | Sage Knit Top, crew neck, Terracotta lipstick | look 2's steps 1 and 2 | The Amethyst Y Chain on the lipstick result | **Probe**, first: a crew neck, like the plain photo the necklace already worked on |
| 3 | Midnight Floral Blouse, the top bought, open collar | look 3's steps 1 and 2 | The same necklace: what the shopper has now | Second |
| 1 | Ribbed Turtleneck, Terracotta lipstick | the probe's steps 1 and 2 | The same necklace, over the turtleneck | Last: the neck is covered, so the engine may refuse it |

- **Lifetime spend:** 57 to 60 units.
- **Today (UTC):** 24 units used on 28 October, so the daily cap is **27**. If it runs after midnight UTC (06:00 in Dhaka), the day starts at 0 and 27 still covers it.
- **Balance:** 983 to about 980, far above the 700 floor.
- **No contingency:** a retry needs a new approval.

## How it runs

1. **Probe, 1 unit:** `run_feasibility --set looks --execute --pairs 2 --confirm-units 1`. It stops there unless the necklace hangs at the neck over the redrawn top, the top and lipstick are unchanged, and it hangs about as low as it did on the plain photo. The size check cannot judge this one: in a look the necklace bought is the only necklace on its photo.
2. **Looks 3 and 1, 2 units:** `--pairs 3,1 --confirm-units 2`. A refusal of the turtleneck look ends only that look; YouCam refunded the refused earrings step on 28 October, and the reconcile will show whether it does here.
3. **Reconcile, 0 units,** from the read-only unit history.
4. **Publish, 0 units:** the three-step looks on `/evidence` and in replay, replacing the two-step ones. A look whose necklace step fails keeps its two published steps, and the page says why.

## Guards

One-off settings for the run's process only: `PREVIEW_UNIT_CEILING=60`, `PREVIEW_DAILY_UNIT_CAP=27`, `YOUCAM_BALANCE_FLOOR=700`, the key read from `.env` and never printed. No automatic retry. Inputs are hash-checked first.

## Known risks

- **The turtleneck.** YouCam asks for a visible neck. Over a turtleneck the engine may refuse the photo (`PHOTO_CHECK_INVALID`), or hang the chain over the collar, which is how it is worn. Either way the result says something true, and a refused look keeps its two published steps.
- **A redrawn top.** Clothes V4 redraws the upper body, so the necklace engine sees a new neckline on every look.
- **A third generation.** Each step works on a result of a result. The probe shows whether the face and lipstick survive a third pass.
- **The beige block.** The necklace engine pasted a block over the chest once, for the gold pendant. This plan uses the Amethyst Y Chain, which rendered properly on the plain photo.
