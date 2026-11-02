# Rechoice evidence guide

The submission story describes the shopper journey. This guide links the original evidence and keeps
its different denominators separate. Hosted links are open; the judge code adds live previews and staff pages.

## Named original YouCam pilots

Completion below means the provider returned a result, not that the result passed a visual gate.
These counts describe the named original pilots, not every development request. Later probes,
combined looks, published-image measurements and the registered new-photo study have separate scopes.

| API | Completed tasks | Important observation | Source |
|---|---|---|---|
| Clothes V4 | 10/10 | Original tops development pilot | `exchange/static/exchange/evidence/pilot.json` |
| Makeup VTO | 10/10 | Six lip and four foundation tasks | `docs/beauty-pilot-plan.md`; `exchange/static/exchange/evidence/beauty.json` |
| Nail VTO | 3/3 | Polish on the demo hand | Same beauty-pilot sources |
| Earrings VTO | 4/4 | Crystal drop rendered out of proportion | `exchange/static/exchange/evidence/jewellery.json` |
| Necklace VTO | 4/4 | One background-removal artifact rejected; three outputs published | `exchange/static/exchange/evidence/necklace.json` |
| AI Hair Color | 4/4 | One light shade barely showed on dark hair; three outputs published | `exchange/static/exchange/evidence/hair.json` |
| Shoes V3 | 4/4 | Four published shoe outputs | `exchange/static/exchange/evidence/accessories.json` |
| Bag V2 | 1 completed; 3 unattempted | Probe changed outfit, pose and background; previews disabled | Same accessories source and `docs/accessories-pilot-plan.md` |

The beauty pilot completed after correcting initially rejected lip/nail payloads. Both pre-task
rejections were uncharged. The necklaces and bag examples demonstrate why API completion alone cannot
be reported as usable-preview accuracy. Later intensity, earring-scale/padding and look probes remain
documented in the corresponding pilot plans.

## The fidelity gate on published renders

`exchange/fidelity_gate.py` measures every finished top preview: garment colour against the catalog
(CIEDE2000 ≤ 10), print busyness as mean edge detail inside the garment compared with the bought top
rendered on the same photo (quieter at ≤ 0.5×), and the share of the image outside the garment band
that changed (stable at ≤ 3%; a render with a different shape fails). `python manage.py evaluate_gate
--check` reproduces [the public report](https://rechoice.atikur.xyz/static/exchange/evidence/gate-v1.json)
from committed files.

| Measure | Observed result | Scope and source |
|---|---|---|
| Print against the catalog label | 6/6 comparable development renders agree (plain tops 0.11–0.42×, patterned 0.50–0.70×) | The 0.5 bound was chosen on these renders, so this is calibration, not a test |
| Colour concern | 1 development render (SHORT-03, visibly lighter) flagged; 0 held-out | Same colour check as before, now gating |
| Stability | 16/16 renders stable; the rejected bag probe flagged (different shape, 55% outside change) | Fixed garment band and pixel threshold from the held-out study |
| Live study gate-study-v1 | 8/8 renders succeeded (16 units); print 6/6 quieter (0.14–0.43×); stability 8/8 stable; colour 3/6 close, 3 flagged at ΔE 11.1, 13.6, 19.4; verdicts 3 shows, 3 concern | Registered in `assets/gate_study_v1.json` before any render; two demo people with no prior blouse render |
| Live study gate-study-v2 | 38/38 renders succeeded (76 units) on four new people plus repeats. Plain tops: print 16/20 quieter (the four misses are all the utility shirt), colour 16/20 close, 12/20 shows. Patterned sweaters: 4/8 not passed (2 on print, 2 on stability). Stability 25/32 (all 24 on half-body photos; 1/8 on the full-length photo). Repeats: each v1 colour flag recurred in both repeats within 0.2 ΔE | Registered in `assets/gate_study_v2.json` before any render. The sweaters were weak negatives: the blind AI rater judged all eight quieter than the floral. 36 renders were first refused by the app's own daily cap before reaching YouCam and then sent once; see `run.log` |
| Blind AI labels for v2 | Sonnet 5.5, images only: agrees with the gate on print 28/34, colour 16/26, outside the garment 30/38. It accepted all ten colour flags, allowing for the photo's light | `evidence/gate-study-v2/labels-ai.json`, with every reason. The owner, named as a second rater, chose not to label. A first labelling run crashed before saving and one call timed out; both are recorded in the file's note |

Grouped by what each render was meant to show (`exchange/gate_outcomes.py`, the headline on `/evaluation#gate`):
of 26 plain-top alternatives rendered after the gate thresholds were fixed, 15 passed; the 11 held back were
flagged 7 times for colour, 4 for print (all the utility shirt) and 4 for change outside the garment (all on the
full-length photo), some for more than one reason. 4 of 8 patterned tops registered to fail were held back, and
6 of 6 repeats were flagged again. The four passing alternatives from the earlier photo holdout are retrospective for this later gate, which was developed using those images. Their original held-out colour result remains separate. Human labels on the same 38 v2 renders are registered as an addendum
(`assets/gate_labels_addendum.json`, `docs/human-labels-plan.md`) and not yet collected.

On each card the verdict is drawn on the preview, the catalog colour sits beside the colour measured on it, and
**Show what changed** draws the stability check's comparison: blue inside the garment band, orange outside it.

A verdict of *shows* means the three measurements agree with the request. It does not establish physical
fit, fabric, exact colour, or that sleeves and neckline match; those remain catalog facts.

## Published images, checks and application verification

| Evidence | Observed result | Scope and source |
|---|---|---|
| Development images | 36 published final outcomes, including the rejected bag probe | [Report](https://rechoice.atikur.xyz/evaluation); committed `exchange/static/exchange/evidence/evaluation.json` |
| Plain-top colour, development | 6/7 measured alternatives within ΔE 10 | A narrow image-colour check; 12 beauty shade outcomes remain uncalibrated |
| Relative earring size | 1/2 alternatives in proportion before correction; one flagged alternative passed after correction | Relative to the purchased pair in the same photo; original render remains available |
| Registered new-photo study | 6/6 first renders on two synthetic source photos unused in prior project records; 4/4 plain-top colours within ΔE 10 | [Report](https://rechoice.atikur.xyz/evaluation/heldout); known products, two patterned references excluded from colour scoring; 12 units billed |
| Sleeve concern | An unblinded AI observation flags pushed-up sleeves on `heldout-v1-05` | Colour passing does not establish requested-detail fidelity |
| Offline agent contracts | 192/192 deterministic catalog scenarios passed | Software contracts, not model or human accuracy |
| Model pilot, run 1 | 6/6 copilot plans, 2/6 cases with both image assessments; next case rejected and later cases unattempted | Failure retained in the [agent report](https://rechoice.atikur.xyz/evaluation/agents) |
| Model pilot, run 2 | 6/6 copilot plans and 12 individual image assessments on the same six known cases | Versioned prompt repair; exact image inputs and both registrations published |
| Same-family model agreement | 11/12 requested-attribute findings agree; disputed sleeve finding remains unclear | Descriptive agreement; zero independent human labels |
| Current source verification | 433 tests on each of PostgreSQL and SQLite, including 23 Chrome tests, no skips; deprecations as errors. Current-photo warnings, draft acknowledgement, judge-popup order preservation and accessibility covered | [2 November receipt](../submission-release-2026-11-02b.json) |

## Reproduction and provenance

- [Development evaluation](../../README.md): `python manage.py evaluate_previews --check`.
- [Original photo-study protocol](../../docs/heldout-evaluation.md) and [results](../../docs/heldout-results.md):
  `python manage.py evaluate_heldout --check`. Registration commit:
  `d062176f6c1118e9b452ec115651ea40b5e13152`.
- [Copilot implementation and protocol](../../docs/copilot-plan.md) and [model-pilot results](../../docs/agent-pilot-results.md):
  `python manage.py evaluate_agents` runs the offline contracts without a model request.
  Paid pilot registrations are `assets/exchange_ai_pilot_v1_run1.json` and `assets/exchange_ai_pilot_v1_run2.json`.
- [Claim references](claim-references.json) maps quantitative claims to source files.

Both approved model-pilot runs have already been used. Reproduction commands above do not authorize
new paid generation. Private response audits and original research records are excluded from the public kit.

## How to interpret this evidence

Visitors see genuine recorded YouCam outputs. Hosted judge sessions can also run new YouCam previews,
including on their own photo, within daily and session unit caps, and use the live read-only LLM
planner within its existing allowance. The local video uses a rules guide and recorded AI assessments.
Source labels distinguish these modes.

All store data and demo people are fictional or synthetic. Earlier clothes ratings and newer image
assessments are AI-authored. No independent human fidelity ratings or shopper/merchant study has been
completed. These results do not establish physical fit, overall fidelity, reduced returns, retained
revenue or generalization to unseen products and shoppers.
