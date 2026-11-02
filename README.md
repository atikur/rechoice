# Rechoice — judging source snapshot

Turn a return into an informed exchange. Rechoice compares eligible replacements on the same person,
shows concerns in generated previews, and requires confirmation before changing shopper limits.

[Watch the 2:50 video](https://www.youtube.com/watch?v=OaZSOgA0ebY) ·
[Hosted demo](https://rechoice.atikur.xyz/judge) ·
[API evidence](https://rechoice.atikur.xyz/evidence) ·
[Evaluation](https://rechoice.atikur.xyz/evaluation)

This is a complete application source snapshot for review. Original Git history, private credentials,
local databases, uploaded photos, private judge attachments and shared-host operations records are
excluded. This dedicated public repository contains the complete judging source; the original
private development repository and its history are separate. Runtime files match the
[2 November partial-preview release receipt](docs/submission-release-2026-11-02d.json).
If status checking is interrupted, **Check preview status** or **Check look status** resumes
checking the existing request. It does not create another preview or look; an authorised look can continue its remaining stages.

## Run the recorded demo without API keys

Use Python 3.12.14 (or a compatible newer interpreter). In the extracted project directory:

```bash
python3.12 -m venv .venv
.venv/bin/python -m pip install -r requirements-dev.txt
export DATABASE_URL=sqlite:///local.sqlite3
export PREVIEW_PROVIDER=mock ASSISTANT_PROVIDER=rules EXCHANGE_AI_PROVIDER=off
export PUBLIC_LIVE_PREVIEWS=0 PREVIEW_UNIT_CEILING=0
.venv/bin/python manage.py migrate
.venv/bin/python manage.py seed_demo
.venv/bin/python manage.py runserver 127.0.0.1:8000
```

Open http://127.0.0.1:8000/. All catalog photos, generated demo people and recorded YouCam results are
included. No Redis, PostgreSQL or provider key is needed for this local path. Django does not load
`.env` automatically. Start from a clean shell; do not import existing live-provider credentials.
The Python dependency installation requires an internet connection. The source ZIP does not bundle
third-party Python wheels, Chrome or a virtual environment.

Try the blouse exchange, choose **I don't like how it looks**, confirm quieter print and long sleeves,
and use **Preview on a person** with recorded **Model B**. Compare the passing turtleneck and the
colour-flagged sage top. A flagged preview requires acknowledgement before the request is saved.
For the no-match route, start another order with an even exchange ($0 extra) and inspect the $2 proposal.
Reviewing a proposal does not change the confirmed limits.

For local staff tools, stop the server and restart it with a local example code:

```bash
JUDGE_ACCESS_CODE=local-demo-only .venv/bin/python manage.py runserver 127.0.0.1:8000
```

Enter that code through **Judge access**. It is not the hosted judge code. The hosted code is supplied
separately through the private Devpost instructions. Creating a demo request changes no real order,
stock or payment. Default providers remain offline even when local judge access is enabled.

## Verify

```bash
.venv/bin/python tools/verify_snapshot.py
.venv/bin/python manage.py check
.venv/bin/python manage.py evaluate_previews --check
.venv/bin/python manage.py evaluate_heldout --check
.venv/bin/python manage.py evaluate_gate --check
.venv/bin/python manage.py evaluate_agents
```

For the full suite, install Google Chrome, or install Playwright Chromium and set
`E2E_BROWSER_CHANNEL=chromium`. Tests use fake providers and recorded data, not paid API calls:

```bash
PUBLIC_LIVE_PREVIEWS=1 PYTHONWARNINGS=error::DeprecationWarning   .venv/bin/python manage.py test tests exchange --noinput
```

The test-process-only public-preview switch lets fake-provider fixtures exercise their expected paths.
Providers remain mock/rules/off and no credentials are supplied; this does not enable paid calls.

The current release receipt records 441 tests on each of SQLite and PostgreSQL, including 29 Chrome
tests and zero skips, in the original development checkout. The earlier 433-test receipt remains as
historical evidence. In this history-free source export, one optional historical calibration test skips
because its original private pilot files are excluded.
The public-image evaluation checks above still reproduce fully. Browser tests additionally require
the stated browser dependencies. The source-export validation performs migration, seeding, route and evidence
checks using existing installed dependencies. It does not claim another complete PostgreSQL/browser
suite or a fresh dependency installation.

## Source map and configuration

- `exchange/`: Django app, views, migrations, templates, static assets, registered evidence and integration tests.
- `secondlook/`: domain rules, provider contracts, YouCam adapters and deterministic preference parsing.
- `config/`: Django/Celery settings and routes.
- `assets/`: source manifests, frozen study protocols, hashes and catalog reference colours.
- `tests/`: domain and provider tests.
- `scripts/`: evidence reproduction, asset preparation and offline/provider smoke tools. Paid tools are
  included for source review, not authorised execution; their execution and spending gates still apply.
- `.env.example`: configurable provider, storage and spending settings; all credentials are placeholders.
- `Dockerfile`, `Procfile`, `deploy/`: generic build/process entry points. Shared-host provisioning is excluded.

See [development guidance](docs/development.md), [deployment guidance](docs/deployment-plan.md),
[retailer API](docs/retailer-api.md) and [evidence guide](docs/submission/evidence-guide.md).

## Limits and licences

Image checks are heuristics, not proof of fit, exact colour or complete fidelity. The records include
failed probes and model disagreements. No shopper/merchant outcome study or independent human fidelity
rating is claimed. The demo uses fictional commerce data and generated people with genuine YouCam outputs.

Source is under [MIT](LICENSE); Pixabay assets, YouCam outputs and dependencies retain their separate
terms. Keep the asset manifests, [dependency licences](docs/dependency-licenses.md) and
[YouCam terms notes](docs/youcam-terms.md) when sharing this snapshot. Do not redistribute the image
files separately as a stock-photo collection.

If a later combined-look stage fails, Rechoice can show the completed prefix as a **Partial preview**. The failed step remains visible; the preview does not claim the missing item was applied. This display makes no new generation request.
