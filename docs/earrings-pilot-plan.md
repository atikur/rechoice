# Earrings pilot: unit plan for approval

Drafted 28 October 2026 and approved by the owner the same day for up to 4 units.

## Result, 28 October 2026

- **All 4 pairs succeeded, for exactly 4 units,** reconciled against the unit history at 1 unit each.
- **The probe ran first** and was checked before the other three.
- **Each earring keeps its design and metal.** It hangs from both earlobes and replaces Face A's own studs.
- **Size, measured afterwards:** the studs render in proportion to the 58 mm pair, but the 38 mm crystal drop renders about as long as it. The size sent to YouCam does not set drop length.
- **Details:** in the HANDOFF entry for 28 October 2026.

## What it spends

**4 units in total.** Earrings VTO costs 1 unit for one earring ("single-item wear"), as the read-only feature-cost check on 27 October 2026 confirmed.

- **Lifetime spend:** goes from 33 units (the clothes and beauty pilots) to 37, against the proposed lifetime ceiling of 300.
- **Balance:** about 1,007 before and 1,003 after, well above the 700 floor.
- **No contingency:** a retry after a failure needs a new approval.

## The 4 pairs

All four run on Face A, the demo face whose ears are visible. Each earring is a product photo cropped to a single earring, so the request can give its size in centimetres from the catalog's drop length.

| # | Earring | Metal, style, length | Why |
|---|---|---|---|
| 1 | Baroque Pearl Drops | gold drop, 58 mm | **Probe.** Confirms the Earrings VTO payload and that Face A's ear is found. It is the pair bought in the demo |
| 2 | Blue Crystal Drops | silver drop, 38 mm | The demo's first suggestion for "too long, and I wanted silver" |
| 3 | Blue Topaz Studs | silver stud, 12 mm | The demo's second suggestion. With pairs 1 and 2, a complete recorded comparison |
| 4 | Pearl Rope Studs | gold stud, 10 mm | The suggestion when only the size changes |

## How it runs

1. **Probe, 1 unit:**
   ```bash
   manage.py run_feasibility --set jewellery --execute --pairs 1 --confirm-units 1
   ```
   If the probe fails or looks wrong, the run stops there and the result is reported before any further unit.
2. **The other 3, for 3 units,** only if the probe looks right:
   ```bash
   manage.py run_feasibility --set jewellery --execute --pairs 2,3,4 --confirm-units 3 --stop-after-failures 2
   ```
3. **Reconcile, 0 units:** a read-only copy of YouCam's unit history records what each task actually cost.

## Guards

These are the same as for the beauty pilot.
- **One-off settings.** Only the run's own process gets these settings; `config/settings.py` is not edited:
  - `PREVIEW_UNIT_CEILING=37` (33 already spent plus 4)
  - `PREVIEW_DAILY_UNIT_CAP=4`
  - `YOUCAM_BALANCE_FLOOR=700`
  - the API key, read from `.env` into that process and never printed
- **No automatic retry.**
- **Inputs are checked first.** The runner refuses to start if Face A's file no longer matches its committed hash, or if an earring is missing its photo or length.

## The request

`POST /s2s/v2.0/task/2d-vto/earring`, following YouCam's published `ai_earrings.json` definition:

```json
{"src_file_id": "<face>", "ref_file_ids": ["<earring>"], "source_info": {"name": "<face>"},
 "object_infos": [{"name": "<earring>", "parameter": {"earring_need_remove_background": true, "earring_scale": 5.8}}]}
```

## Known risks

- **Face A's studs.** Face A already wears small studs, which may show beneath a drop.
- **Front-facing photo.** YouCam says earrings render best on side-facing photos, and Face A is front-facing.
- **Single-ear default.** The default renders on one ear only.
- **Cropped hook.** The pearl drop's hook is cut off in its source photo.

The probe will show whether any of these matter.
