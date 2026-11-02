# Development guide for the judging source snapshot

Start with the root README for offline setup, verification and the source map. The full application,
registered studies, recorded outputs, migrations, domain tests and browser tests are included.

## Providers and safety controls

The defaults are `PREVIEW_PROVIDER=mock`, `ASSISTANT_PROVIDER=rules` and `EXCHANGE_AI_PROVIDER=off`.
The recorded demo uses included outputs and requires no keys. `JUDGE_ACCESS_CODE` controls local staff
and judge features, but does not by itself turn on paid providers. See `.env.example` and
`config/settings.py` for the full configuration; `.env` is not read automatically.

Live YouCam operation requires a provider key, explicit live approval, positive cumulative/daily/session
unit limits and the relevant judge/public settings. Optional model operation has separate approval and
USD/call limits. Provider keys and the hosted judge code are deliberately absent. Do not run scripts
with `--execute` or enable providers without your own authorisation and cost limit.

`exchange/preview_jobs.py` reserves unit allowance, tracks provider tasks and preserves uncertain
billing. `secondlook/youcam_provider.py` contains the working adapters. Personal images are session
scoped and expired locally; see `docs/youcam-terms.md` for provider retention notes.

## Main flows

The style flow confirms shopper preferences, calculates eligibility, compares the purchased item and
alternatives, checks generated top previews and records an informational quote/request for staff review.
Other return reasons have separate fit, support and standard-return paths. `docs/tradeoff-explorer.md`
describes proposals that preserve every unselected confirmed limit.

`exchange/fidelity_gate.py` and `exchange/colour_check.py` are pinned by the study manifests. Preserve
those files and image bytes when reproducing the evidence. Presentation overlays live separately.
Requests recheck the active preview and exact warning acknowledgement. The fixed comparison band is
not segmentation, and a passing check is not proof of fit or appearance.

`docs/copilot-plan.md` and `docs/agent-pilot-results.md` document bounded read-only planning and
requested-change assessments. The rules fallback works without model calls. `docs/human-labels-plan.md`
is a protocol, not collected human evidence.

Production requires PostgreSQL, Redis, separate web/worker/beat processes, secure settings and shared
private media. See `docs/deployment-plan.md`. The owner's shared-host records and secrets are excluded.
