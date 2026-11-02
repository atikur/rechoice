# Shoes and bags pilot: unit plan for approval

Drafted 28 October 2026 as tier 2. The owner approved the nine Pixabay downloads and the build on the fake API, then this run for up to 16 units, the same day. It ran that day.

## Result, 28 October 2026

**Shoes: 4 of 4 succeeded, for 8 units, published. Bags: the probe failed its gate, for 2 units; the other bags did not run, and bag try-on is off.** 10 units in all, reconciled against the unit history.

- **The shoes probe ran first:** the red ankle boots appear on Model F's feet below the trouser hem as red pointed heels, with Model F, her trousers and the background unchanged, in 21 s.
- **The other three pairs** came out just as faithfully: the cream peep-toes, the metallic platform brogues with their striped sole, and the nude pumps. 19 to 21 s each.
- **The trousers hide the ankles,** so the boots' shafts and the heel heights are mostly out of sight. The previews show style and colour; the caption says to check the heel height in the details.
- **The bags probe failed.** Bag V2 drew the red shoulder bag well, but it made a new picture around it: a gallery background, a green dress in place of the white tee and jeans, a new pose, and a fixed 1104 by 1472 output. Its reference page then explained why: it applies one of four preset styles, chosen at random unless one is named. That cannot show a shopper's own bag on them, and each bag would arrive in a different outfit, so the comparison would not be fair.
- **What changed in the app:**
  - Bags keep the exchange (smaller or larger, neutral or bright), but with no try-on; the page says why.
  - Shoe previews are cropped from the bottom, so the feet show.
  - The judges' page lists Shoes V3 in use and Bag V2 as tried and switched off.
- **Spend:** lifetime 70 units, today's (UTC) 37, balance 970.
- **Published** on `/evidence` and in replay: all four pairs of shoes on Model F, and the bag probe beside Model A as sent, under "Why bag try-on is off".

## What was checked, read-only

YouCam's `ai_shoes.json` and `ai_bag.json`, and their overview pages, read 28 October 2026:

| Feature | Endpoint | Units | Person photo | Product photo |
|---|---|---|---|---|
| Shoes V3 | `POST /s2s/v2.0/task/shoes-transfer` | 2 | Full body, one person, feet fully visible, 512 px or more | One product, at least 512 px, over 25% of the image height |
| Bag V2 | `POST /s2s/v2.0/task/bag` | 2 | Head to chest or half body, face over 15% of the height | The same |

**The bag call requires `gender`, female or male, of the person in the photo.** Rechoice never infers anything from a person's appearance, so it sends only the gender recorded for each AI-generated demo person, taken from the description already on file for that image. A shopper's own photo has none, so a bag preview on it is refused before anything is sent or spent, and the photo step does not offer one for bags.

## What is built, with no spend

- **Assets:** nine Pixabay files, recorded in `assets/pixabay_sources.json` with page, author and SHA-256, and built by `scripts/build_pixabay_assets.py`:
  - **Model F,** an AI-generated full-length woman with her feet showing, for shoes: a new "full" kind of demo person.
  - **Four shoes and four bags,** logo-free, into `exchange/static/exchange/accessories/`. Two printed marks on the nude pumps' insole are painted out as a recorded retouch step, in case they were a maker's stamp.
- **An accessories group** that shares the earrings' rules, `secondlook/jewellery.py`:
  - **Shoes:** a lower or higher heel, from the catalog's heel height.
  - **Bags:** smaller or larger, from the catalog's width.
  - **Both:** a neutral or bright colour family in place of a metal.
  - Each stated change is a hard filter; nothing is read from the shopper's feet or body.
- **Words:** the rules read "too high", "flats", "too loud", "too bulky", "brighter" and the like; Claude gets the same controls, with neutral and bright defined.
- **Help me compare** describes shoes by heel height and bags by width.
- **Adapters:** `YouCamShoesProvider` (`shoes-v3-1.0`) and `YouCamBagProvider` (`bag-v2-1.0`), 2 units each, wired through the same limits, balance floor and reconcile as every other feature.
- **The demo order** gains red heeled ankle boots and a red shoulder bag, nine items in all.

## The pilot pairs

**Shoes, on Model F, under the label `shoes-pilot`:**

| # | Shoes | Why |
|---|---|---|
| 1 | Red Heeled Ankle Boots, 100 mm | **Probe.** The pair bought: confirms the payload and that Model F's feet are found |
| 2 | Cream Block-Heel Peep-Toes, 50 mm, neutral | The demo's answer to "the heels are too high and the red is too loud" |
| 3 | Metallic Platform Brogues, 30 mm, bright | The flattest pair, a lower heel in a bright colour |
| 4 | Nude Suede Pumps, 110 mm, neutral | A neutral pair with a heel as high as the boots |

**Bags, on Model A, under the label `bags-pilot`:**

| # | Bag | Why |
|---|---|---|
| 1 | Red Slouch Shoulder Bag, 42 cm | **Probe.** The bag bought: confirms the payload, with the recorded gender |
| 2 | Tan Leather Messenger, 30 cm, neutral | The demo's answer to "too big and too bright" |
| 3 | Yellow Ring Crossbody, 20 cm, bright | The smallest bag |
| 4 | Coral Mini Tote, 26 cm, bright | A smaller bright tote, completing the comparison |

## What it spends

**Up to 16 units: 8 for shoes and 8 for bags, 2 per pair.**

- **Lifetime spend:** 60 to 76 units.
- **Today (UTC):** 27 units were used on 28 October, so a daily cap of **43** covers the run whether it falls on 28 or 29 October.
- **Balance:** about 980 to 964, far above the 700 floor.
- **No contingency:** a retry needs a new approval.

## How it runs

1. **Shoes probe, 2 units:** `run_feasibility --set shoes --execute --pairs 1 --confirm-units 2`. It stops there unless the boots appear on Model F's feet at a believable size, the trousers and the rest of the person are unchanged, and the result comes back within about a minute.
2. **The other shoes, 6 units:** `--pairs 2,3,4 --confirm-units 6 --stop-after-failures 1`.
3. **Bags probe, 2 units:** `run_feasibility --set bags --execute --pairs 1 --confirm-units 2`, with the same kind of check: the bag carried or worn believably, Model A unchanged.
4. **The other bags, 6 units:** `--pairs 2,3,4 --confirm-units 6 --stop-after-failures 1`.
5. **Reconcile, 0 units,** from the read-only unit history.
6. **Publish, 0 units:** the comparisons on `/evidence` and in replay, and the two features added to the judges' page table.

## Guards

One-off settings for the run's process only: `PREVIEW_UNIT_CEILING=76`, `PREVIEW_DAILY_UNIT_CAP=43`, `YOUCAM_BALANCE_FLOOR=700`, the key read from `.env` and never printed. No automatic retry. Inputs are hash-checked first. Each probe gates its own feature, so a failed shoes probe does not stop the bags.

## Known risks

- **The trousers.** Model F's wide trousers fall to the ankle. Shoes V3 may redraw the hem, or refuse the photo if it judges the feet hidden.
- **Hands in pockets.** Model A stands with her hands in her pockets. Bag V2 may hang the bag from the shoulder, or place it oddly.
- **Bottoms and outerwear wait.** Almost every Pixabay trousers or skirt photo shows a real person wearing it, which this project does not use, and the few product-only ones are near-identical jeans on hangers. Bottoms need AI-generated garment images or a store's own photos.
