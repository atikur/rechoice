# Rechoice held out evaluation protocol

Version `heldout-v1` freezes six planned Clothes V4 renders on two reserved synthetic source photos.
The same catalog products were used during development; the held-out dimension is the source photo.
**Completed on 29 October 2026 after owner approval for up to 12 units:** all six first attempts succeeded,
using exactly 12 units. [Results and limitations](heldout-results.md) are separate from the 36 development
outcomes. The immutable manifest remains the pre-run design; its original “not run” status is not rewritten.

## Frozen cases

| Cases | Source photo | Products in execution order | Purpose |
|---|---|---|---|
| 01 to 03 | model-d, grey studio | ORIGINAL, CALM-04, CALM-07 | Purchased patterned blouse and two quieter long-sleeved alternatives |
| 04 to 06 | model-e, red wood background | ORIGINAL, CALM-04, CALM-07 | The same comparison on a different framing and background |

Both photos were already reserved as alternates, so their inputs had been inspected. Before freezing,
neither appeared in the previous pair definitions, the 11 available private pilot reports or the 55
local paid-job records checked. Raw and normalized photo hashes were checked against the 16 distinct
paid-job source hashes. This establishes separation from the available project records, not global
novelty of the images or a representative sample of shoppers.

The selected inputs were viewed for usable framing before any outputs. All reserved clothing alternates
that passed the prior-use audit were included; no generated result influenced their selection. Photos
and products retain the source and license records from `assets/pixabay_sources.json`.

## Verify the freeze

`assets/heldout_v1.json` contains the design, six cases, source hashes, normalized hashes, code and
catalog hashes, database product facts, thresholds, reporting rules and a snapshot of the prior-use audit. Its SHA-256 is sealed
in `assets/heldout_v1.sha256`. `assets/heldout_v1_sources.json` feeds the existing feasibility runner.
These files were registered in commit `d062176f6c1118e9b452ec115651ea40b5e13152` before collecting outputs.

```bash
DATABASE_URL=sqlite:///local.sqlite3 PREVIEW_PROVIDER=mock ASSISTANT_PROVIDER=rules \
  .venv/bin/python scripts/check_heldout.py --audit-db

DATABASE_URL=sqlite:///local.sqlite3 PREVIEW_PROVIDER=mock ASSISTANT_PROVIDER=rules \
  .venv/bin/python manage.py run_feasibility --sources assets/heldout_v1_sources.json \
  --set clothes --label heldout-v1
```

At registration, both commands passed. They are preparation checks; **do not repeat the prior-use
database audit after execution**: the six jobs now correctly appear in paid history. Use
`manage.py evaluate_heldout --check` to reproduce the completed report. The original preparation
commands make no provider request and generate no previews. The
database audit also rejects changed product images, versions, prices, stock and reference-colour facts. Restore
missing model inputs with the existing asset builder; a changed hash is a failure, not an invitation
to reseal this version. The checker also verifies the normalized JPEG bytes under the frozen runtime.

## Execution budget

The approved and reconciled charge was **12 units**: six Clothes V4 tasks at the last verified price of two units each, with
no automatic retries. The owner approved up to 12 units on 29 October 2026. The run reconciled the actual unit history,
rechecked feature cost and balance, reserved the 12 within the existing combined allowance, and configured
the existing live-mode approval and spending guards. The balance moved from 970 to 958; local lifetime spend moved from 70 to 82. Do not enable live mode on the hosted demo for
this study.

The existing runner executed this frozen source file
with `--execute --confirm-units 12 --stop-after-failures 1`. Stop after the first failed or uncertain task,
keep its record and all later cases as unattempted, and obtain a separate decision before continuing.
The original first attempt remains the confirmatory observation. A later rerun is exploratory.

Reports belong in ignored `private_media/feasibility/reports/heldout-v1/`. Preserve the first report,
original response records and output hashes before inspecting results. Read the provider unit history
and reconcile actual charges afterwards. Never publish credentials or raw private logs.

## Measurements and denominators

- **Generation:** completed outputs out of all six planned tasks, with failed, uncertain and unattempted
  counts listed separately. Report six planned outputs on two source photos, not six independent people.
- **Plain garment colour:** four planned alternatives, threshold CIEDE2000 ΔE ≤ 10. Report measured,
  close, different and missing out of those four. The two patterned references are not colour-scored.
- **Requested difference:** four alternative versus purchased-top comparisons on the same source photo.
  A missing purchased-top result makes that comparison unassessed; it does not disappear from the table.
- **Product detail and unrelated changes:** the existing four-field rubric, completed only by named
  reviewers with their human/AI status and true blinding recorded. The blank template is
  `assets/heldout_v1_review_template.json`. No reviews have been filled in.
- **Outside-region change:** retain the fixed boxes and threshold 40 from the frozen protocol. It is an
  exploratory pixel statistic, not an identity, pose or background-preservation verdict.

Keep development and held-out tables separate. Do not tune reference colours, regions, preprocessing,
thresholds or provider prompts after seeing outputs. A material change makes this cohort development
data and requires a newly selected cohort and a new protocol version. Keep every case and any reviewer
disagreement visible. A misleading result prevents a positive product-fidelity claim even if colour is
close. No pooled accuracy, physical-fit, demographic-performance or return-reduction claim follows
from this small synthetic study.

## Publication after a run

The separate [result report](heldout-results.md) contains the frozen manifest hash, actual provider version, every first
attempt's state, output hashes, billing, measurements and named review provenance. Do not place results
inside the sealed design. Add results to the public evaluation only through an explicit publication
change that preserves its cohort separation. The submission copy now reports six completed new-photo outcomes with their own denominators. No independent human rubric ratings have been added.
