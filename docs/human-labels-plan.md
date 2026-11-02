# Human labels for gate study v2

The gate's colour flags have one outside check so far, and it disagrees: the blind AI rater judged all ten
colour-flagged renders it saw a match, once it allowed for the photo's light. Print and stability have the same
gap. Nobody has yet asked people whether they would hold back the previews the gate holds back. This plan
collects blind human labels on the same 38 renders, at no YouCam or AI cost.

It is an addendum, `assets/gate_labels_addendum.json`, frozen by `assets/gate_labels_addendum.sha256` before
any human label was collected. It adds raters and changes nothing else in the registered study: the same
images, questions and shuffled order the AI rater saw, the same thresholds.

## Who

At least three adults who did not build or tune the gate and have not seen the evaluation or evidence pages.
The owner may label too; those labels are published as not independent and never count towards the majority.

## How, for each rater

1. On the owner's computer, from the project root:

   ```bash
   .venv/bin/python -I scripts/label_gate_study_local.py 8020 --rater p1
   ```

   Use a short id (`p1`, `p2`, ...), never a name. Answers save to `.local/gate-study-v2-labels-p1.json` as
   the rater clicks, so the page can be closed and reopened.
2. Open http://127.0.0.1:8020/ and read the rater this, and nothing more:
   *"Each item shows a photo, the floral blouse drawn on that photo, a product photo and a preview. Judge from
   the images alone. For print: is the top quieter than the blouse? For colour: does the top match its product
   photo? For outside: did anything other than the top change? Unclear is a fine answer."*
3. 38 items, about 100 answers, 20 to 30 minutes. Raters label alone and do not discuss answers.

## Publish

```bash
.venv/bin/python manage.py publish_human_labels p1 p2 p3 owner
.venv/bin/python manage.py evaluate_gate --write
```

The first command refuses an incomplete set, checks every answer against the registered choices, and writes
`labels-person-1.json`, ... (and `labels-owner.json`) to `exchange/static/exchange/evidence/gate-study-v2/`.
The second recomputes `gate-v1.json`; `/evaluation` then shows each rater's agreement and the majority of the
independent raters. Run the test suite, commit the label files and report together, and redeploy.

## What will be reported

Per question, each rater's agreement with the gate, the agreement with the independent raters' majority (ties
and majorities for unclear counted and left out), and every disagreement by item. All complete sets are
published, whatever they show, including if people disagree with the colour flags. Thresholds do not change in
response.
