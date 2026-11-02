# Rechoice registered photo holdout — results

On 29 October 2026 the owner approved **up to 12 YouCam units** for the frozen six-case study.
All six Clothes V4 tasks succeeded on their first attempt. No retry, correction or replacement render
was made. The provider history matched all six tasks at two units each: **12 units used**, balance
**970 → 958**, local lifetime spend **70 → 82**. This approval is exhausted.

## Outcomes

| Case | Synthetic photo | Product | First attempt | Colour ΔE | Units |
|---|---|---|---|---|---|
| heldout-v1-01 | model-d | Midnight Floral Blouse | succeeded | patterned, not scored | 2 |
| heldout-v1-02 | model-d | Sage Knit Top | succeeded | 0.9 | 2 |
| heldout-v1-03 | model-d | Ribbed Turtleneck | succeeded | 3.7 | 2 |
| heldout-v1-04 | model-e | Midnight Floral Blouse | succeeded | patterned, not scored | 2 |
| heldout-v1-05 | model-e | Sage Knit Top | succeeded | 5.5 | 2 |
| heldout-v1-06 | model-e | Ribbed Turtleneck | succeeded | 2.5 | 2 |

Generation: **6/6 planned**, zero failed, uncertain or unattempted. Colour: **4/4 planned plain-top
alternatives measured and within the frozen CIEDE2000 ΔE ≤ 10 threshold**, zero different or missing.
The two patterned references are excluded from single-colour scoring. Client elapsed times were
19.3–20.6 seconds, including upload and polling. These are six correlated renders on **two photos**.

The original fixed-box outside-region statistic reads 0.0% for each image. Its broad approximate
region and threshold make it insensitive to some changes; zero is not evidence that the face, pose
or background was preserved. It has no pass/fail threshold.

## Fidelity remains unassessed

An **unblinded AI observation by Codex**, after generation, flags pushed-up sleeves in model-e's
Sage Knit Top result. Exposed forearms conflict with treating the long-sleeve catalog attribute as
verified image coverage. Its close colour reading does not resolve this concern.

There are **zero independent human rubric reviews** and **zero of four requested-difference
comparisons rated**. Product-detail, requested-change and misleading-result judgments remain unassessed.
The observation is preserved beside the output and is not converted into a human rating or hidden
by the colour summary. No overall fidelity or physical-fit claim follows.

The held-out dimension is the source photo, unused in the available previous project records.
The catalog products were already used in development. Neither two synthetic photos nor these
products establish performance on real shoppers, demographic groups or new products. The **36
development outcomes retain their separate report and denominators**. There is no pooled accuracy.

## Reproduce and inspect

- App page: `/evaluation/heldout`; the development page `/evaluation` links to it.
- Public inputs, all six stored JPEG outputs and sanitized execution observations:
  `exchange/static/exchange/evidence/heldout-v1/`.
- Frozen design: [heldout-evaluation.md](heldout-evaluation.md), registered at commit
  `d062176f6c1118e9b452ec115651ea40b5e13152` before generation.
- Manifest SHA-256: `9374a8edfd0040871446b5df48c19f0a4cb0b716f2a085ab180dab36a776dcc6`.

```bash
DATABASE_URL=sqlite:///local.sqlite3 PREVIEW_PROVIDER=mock ASSISTANT_PROVIDER=rules \
  .venv/bin/python manage.py evaluate_heldout --check
```

The command verifies frozen code/catalog/input hashes and recomputes the existing measurements
from public normalized input bytes and exact app-stored output bytes. It needs no API key, seeded
database or private media. Measurement code and thresholds were frozen before execution; this
publication code was written afterwards. Images were not resized or corrected for publication.

The immutable registration still says `frozen_not_run` and contains its pre-approval state; it is a
design snapshot, not the execution record. The separate observations and report record the actual
approval, execution, billing and measurements. Do not reseal the design. The old runner's inherited
ten-pair pilot gate does not apply to this six-case study.

Private evidence retains the original first report, app task IDs, before/after provider billing
history, outputs and the pre-run database backup. Public records include hashes of the reports and
task IDs, with no credentials or signed URLs. The runner did not retain every intermediate HTTP
response; no complete raw-response trace is claimed. The pre-run database reuse audit now correctly
rejects reuse of these executed subjects; use the post-run reproduction command above.

The shared hosted app remains on mock/rules with paid caps zero. This local study did not activate
live hosting, change DNS or publish a video or Devpost submission. The updated proposed fresh-host
allowance is 218 units within the existing 300 combined ceiling; it is not approval to spend.
