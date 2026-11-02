# Exchange-agent pilot results — 29 October 2026

The owner approved USD 3 cumulatively for two versioned pilot runs and remaining judge-only use.
No new YouCam call or render was made. Both registrations were committed before their respective runs.
The same six copilot cases and six known public synthetic image cases were retained in both runs.

| Run | Copilot tool plans | Cases with both image assessments | Outcome |
|---|---|---|---|
| 1 | 6/6 completed | 2/6 completed; third stopped, later cases unattempted | A long, disclaimer-heavy second-model observation was rejected. USD 0.029854 charged, no uncertain billing. |
| 2 | 6/6 completed | 6/6 completed, 12 individual assessments | Shorter, criterion-specific instructions; strict validation preserved. USD 0.061654 incremental cost. |

The cumulative local total is **USD 0.091508**, with **zero reserved or uncertain charges**. There were
52 generation responses across both runs (below the approved 72-request pilot maximum). The production
allowance may be no larger than **USD 2.908492** from the original USD 3 total, with 24 calls per UTC day,
12 per judge session and no automatic retries. Legacy assistants remain on rules and YouCam on mock.

The second run's reviewers agreed on **11/12 requested-attribute findings**. On `heldout-v1-05`, Sonnet
raised a sleeve-coverage concern while Haiku reported support. Rechoice marks that disputed attribute
**unclear** in the recorded assessment. Both models are from the same family, and the cases were already
known: this is descriptive agreement, not independent fidelity accuracy or generalization evidence.
Human labels and shopper/merchant feedback remain absent.

`/evaluation/agents` retains completed, rejected and unattempted outcomes. It supplies the exact model
input bytes and separates these results from 192 deterministic contract scenarios and the earlier
frozen YouCam study. The public report is `exchange/static/exchange/evidence/agents-evaluation.json`;
recorded assessments are `ai-reviews-v1.json`. Original image-study measurements were not edited.

Run 1 registration: `assets/exchange_ai_pilot_v1_run1.json`; run 2 registration:
`assets/exchange_ai_pilot_v1_run2.json`. The private reports and response audits stay under
`private_media/agent-evaluation/`. Their provider identifiers and credentials are not public artifacts.

The final hosted verification ledger records seven model responses at USD 0.001151, including
the first walkthrough that exposed a delayed-confirmation UI race. After its tested fix, the full
judge walkthrough passed with a visible AI tool-plan source. Ordinary sessions made zero paid calls.
Combined pilot-plus-host spend is USD 0.092659, leaving USD 2.907341 from the approved USD 3;
there are zero reserved or uncertain charges. The production cap remains cumulative across redeploys.
