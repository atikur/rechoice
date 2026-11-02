#!/usr/bin/env python3
"""Build a history-free judging source ZIP from an explicit allowlist.

Run from the original project checkout. This reads the private checkout but never publishes it.
"""
from pathlib import Path
import hashlib
import json
import os
import re
import stat
import subprocess
import sys
import tempfile
import zipfile

ROOT = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else Path(__file__).resolve().parents[3]
if not (ROOT / '.git').exists():
    raise SystemExit('Pass the original private checkout path as the first argument; this builder needs its tracked-file list and privacy checks.')
OUT = ROOT / '.local/submission/source-release'
BASELINE = 'docs/submission-release-2026-11-02b.json'
ROOT_FILES = ['.dockerignore', '.env.example', '.gitignore', '.python-version', 'Dockerfile',
              'Procfile', 'LICENSE', 'manage.py', 'requirements.txt', 'requirements-dev.txt']
SOURCE_ROOTS = ['config', 'secondlook', 'exchange', 'assets', 'tests']
SCRIPT_FILES = ['build_gate_study_people.py', 'build_pixabay_assets.py', 'check_feature_costs.py',
                'check_heldout.py', 'check_hosted_demo.py', 'check_youcam_budget.py',
                'label_gate_study_local.py', 'list_dependency_licenses.py', 'measure_pilot_accuracy.py',
                'measure_reference_colours.py', 'publish_pilot_evidence.py', 'smoke_deployment.py',
                'smoke_live_judge.py']
DOC_FILES = ['accessories-pilot-plan.md', 'advisor-plan.md', 'agent-pilot-results.md',
             'ai-assistant-plan.md', 'beauty-pilot-plan.md', 'copilot-plan.md',
             'dependency-licenses.md', 'earring-size-plan.md', 'earrings-pilot-plan.md',
             'foundation-retest-plan.md', 'hair-pilot-plan.md', 'heldout-evaluation.md',
             'heldout-results.md', 'human-labels-plan.md', 'looks-necklace-plan.md',
             'looks-pilot-plan.md', 'necklace-pilot-plan.md', 'retailer-api.md',
             'tradeoff-explorer.md', 'youcam-terms.md']
README = '''# Rechoice — judging source snapshot

Turn a return into an informed exchange. Rechoice compares eligible replacements on the same person,
shows concerns in generated previews, and requires confirmation before changing shopper limits.

[Watch the 2:50 video](https://www.youtube.com/watch?v=OaZSOgA0ebY) ·
[Hosted demo](https://rechoice.atikur.xyz/judge) ·
[API evidence](https://rechoice.atikur.xyz/evidence) ·
[Evaluation](https://rechoice.atikur.xyz/evaluation)

This is a complete application source snapshot for review. Original Git history, private credentials,
local databases, uploaded photos, private judge attachments and shared-host operations records are
excluded. Its creation does not imply that any source repository is public or that organisers have
received access. Runtime files match the dated source receipt in
[docs/submission-release-2026-11-02b.json](docs/submission-release-2026-11-02b.json).

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
PUBLIC_LIVE_PREVIEWS=1 PYTHONWARNINGS=error::DeprecationWarning \
  .venv/bin/python manage.py test tests exchange --noinput
```

The test-process-only public-preview switch lets fake-provider fixtures exercise their expected paths.
Providers remain mock/rules/off and no credentials are supplied; this does not enable paid calls.

The dated baseline receipt records 433 tests on each of SQLite and PostgreSQL, including 23 Chrome
tests and zero skips, in the original development checkout. In this history-free source export,
one optional historical calibration test skips because its original private pilot files are excluded.
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
'''
DEVELOPMENT = '''# Development guide for the judging source snapshot

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
'''
DEPLOYMENT = '''# Generic deployment guidance

The easiest judge setup is the root README's SQLite recorded-demo path. This guide describes the
production components without exposing the owner's shared host configuration.

Use Python 3.12.14 and the pinned requirements. `Dockerfile` builds a non-root image and collects
static files. `Procfile` describes release, web, worker and scheduler commands; `deploy/entrypoint.sh`
and `deploy/healthcheck.py` are the runtime entrypoint and health helper.

Production needs PostgreSQL, Redis, a web process, a Celery worker and a Celery beat scheduler. Copy
`.env.example` only as a configuration reference: supply strong private credentials through your
platform's secret manager, set hosts/CSRF origins and HTTPS/proxy settings for your own domain, and
configure a private shared media volume or supported S3 storage. Do not publish `.env` or mounted media.

Apply migrations once for each release and seed only a fresh fictional demo database. Run
`manage.py check --deploy --fail-level WARNING`, the evidence checks and the offline smoke script
before exposing the service. Default paid providers should remain disabled until keys, approval and
explicit allowance limits are configured. Never reuse the example passwords or local judge code for
an internet-facing deployment.

This source snapshot omits the original shared-host Compose/provisioning configuration, private
credentials, deployment receipts and neighbour-container details. It does not provide hosted access
or change any existing service.
'''
VERIFY = '''#!/usr/bin/env python3
"""Check every recorded snapshot payload hash; no network or application execution."""
import hashlib
import json
from pathlib import Path
root = Path(__file__).resolve().parents[1]
manifest = json.loads((root / 'SOURCE-MANIFEST.json').read_text())
for row in manifest['files']:
    path = root / row['file']
    assert path.is_file() and not path.is_symlink(), row['file']
    content = path.read_bytes()
    assert len(content) == row['bytes'], row['file']
    assert hashlib.sha256(content).hexdigest() == row['sha256'], row['file']
print(f"Verified {len(manifest['files'])} source snapshot payloads.")
'''

def digest(data):
    return hashlib.sha256(data).hexdigest()

def main():
    OUT.mkdir(parents=True, exist_ok=True)
    tracked = {x for x in subprocess.check_output(['git', 'ls-files', '-z'], cwd=ROOT).decode().split('\0') if x}
    selected = set(ROOT_FILES + [BASELINE, 'deploy/entrypoint.sh', 'deploy/healthcheck.py',
                                 'docs/submission/evidence-guide.md', 'docs/submission/claim-references.json'])
    selected.update('scripts/' + name for name in SCRIPT_FILES)
    selected.update('docs/' + name for name in DOC_FILES)
    selected.update(name for name in tracked if name.split('/')[0] in SOURCE_ROOTS)
    payloads = {}
    for name in sorted(selected):
        path = ROOT / name
        assert name in tracked and path.is_file() and not path.is_symlink(), name
        payloads[name] = path.read_bytes()
    # The public guide contains links to historical private operations receipts. Keep its evidence
    # text, but remove those historical verification rows/notes from this focused source export.
    guide = payloads['docs/submission/evidence-guide.md'].decode()
    guide = '\n'.join(line for line in guide.splitlines() if not any(token in line for token in
        ('| 31 October application verification |', '| Earlier application verification |')))
    guide = guide.replace(' [Live and public receipt](../hosted-live-2026-10-30.json).', '')
    guide = guide.replace('corresponding pilot plans and historical handoff entries.', 'corresponding pilot plans.')
    guide = guide.replace('https://github.com/atikur/secondlook/blob/main/', '../../')
    payloads['docs/submission/evidence-guide.md'] = (guide + '\n').encode()
    payloads['README.md'] = README.encode()
    payloads['docs/development.md'] = DEVELOPMENT.encode()
    payloads['docs/deployment-plan.md'] = DEPLOYMENT.encode()
    payloads['tools/verify_snapshot.py'] = VERIFY.encode()
    payloads['tools/build_source_snapshot.py'] = Path(__file__).read_bytes()
    # Keep only claims whose cited local evidence is actually shipped. Hosted operations,
    # publication status and externally sourced market statistics do not describe this export.
    original_claims = json.loads(payloads['docs/submission/claim-references.json'])
    retained_claims = []
    removed_claims = 0
    for row in original_claims['claims']:
        sources = row.get('sources', [row.get('source', '')])
        local_paths = [value.split(' (', 1)[0] for value in sources if not value.startswith(('http://', 'https://'))]
        if not local_paths or any(value not in payloads for value in local_paths):
            removed_claims += 1
            continue
        if 'expanded by USD 5' in row['claim']:
            row = dict(row)
            row['claim'] = ('The optional read-only copilot uses checked tool facts, with configurable cumulative, '
                            'daily and session limits, a judge-only option and a rules fallback.')
        retained_claims.append(row)
    original_claims['claims'] = retained_claims
    original_claims['scope'] = ('Source snapshot claims only. Every local source named below is included. '
                                'Hosted operations and publication-status claims are excluded.')
    original_claims['claims_not_supported'] = [value for value in original_claims['claims_not_supported']
                                              if value != 'Devpost submission already complete']
    payloads['docs/submission/claim-references.json'] = (json.dumps(original_claims, indent=2) + '\n').encode()
    baseline = json.loads((ROOT / BASELINE).read_text())
    runtime_checked = []
    for name, wanted in baseline['source_hashes'].items():
        assert name in payloads and digest(payloads[name]) == wanted, ('runtime mismatch', name)
        runtime_checked.append(name)
    personal_emails = {v for v in subprocess.check_output(['git', 'log', '--all', '--format=%ae%n%ce'], cwd=ROOT).decode().splitlines()
                       if '@' in v and not v.endswith('@users.noreply.github.com')}
    private_values = set()
    if (ROOT / '.env').exists():
        for line in (ROOT / '.env').read_text().splitlines():
            if '=' in line and not line.startswith('#'):
                key, value = line.split('=', 1)
                value = value.strip().strip('"\'')
                if len(value) >= 12 and any(t in key.upper() for t in ['KEY', 'SECRET', 'TOKEN', 'PASSWORD', 'CODE']):
                    private_values.add(value)
    for name in ['access.txt', 'judge-testing-instructions.md', 'judge-testing-instructions-before-20261030.md']:
        path = ROOT / '.local/deployment' / name
        if path.exists():
            content = path.read_text()
            private_values.update(re.findall(r'\|\s*(?:Judge code|Site password)\s*\|\s*\*\*([^*\[\]]{8,})\*\*', content))
            if name == 'access.txt':
                private_values.update(v for v in re.findall(r'[:=]\s*(\S{12,})', content)
                                      if not re.match(r'https?://|[\w.-]+\.(xyz|com|org)\b', v))
    for name, data in payloads.items():
        assert not any(email.lower().encode() in data.lower() for email in personal_emails), ('personal email', name)
        assert not any(value.encode() in data for value in private_values), ('private value', name)
        assert not re.search(rb'(?:sk-ant-[A-Za-z0-9_-]{20,}|gh[pousr]_[A-Za-z0-9]{20,}|-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----)', data), ('secret signature', name)
        assert not any(part in {'.git', '.local', '.venv', 'private_media', 'uploads', '__pycache__'} for part in Path(name).parts), name
        assert Path(name).name != '.env' and not name.endswith(('.sqlite3', '.mp4', '.zip', '.pyc')), name
    manifest = {'format': 1, 'purpose': 'Complete application source without original Git history or private operations data',
                'created_utc': '2026-11-02', 'runtime_baseline': BASELINE,
                'runtime_hashes_matched': len(runtime_checked), 'builder_original_allowlist': sorted(selected),
                'files': [{'file': n, 'bytes': len(b), 'sha256': digest(b)} for n,b in sorted(payloads.items())]}
    payloads['SOURCE-MANIFEST.json'] = (json.dumps(manifest, indent=2) + '\n').encode()
    archive = OUT / 'rechoice-source-20261102.zip'
    temp = archive.with_suffix('.tmp.zip')
    with zipfile.ZipFile(temp, 'w', zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for name, content in sorted(payloads.items()):
            item = zipfile.ZipInfo('rechoice-source/' + name, date_time=(2026,11,2,0,0,0))
            item.compress_type = zipfile.ZIP_DEFLATED
            item.external_attr = (0o100755 if name.endswith('.sh') else 0o100644) << 16
            z.writestr(item, content)
    with zipfile.ZipFile(temp) as z:
        assert z.testzip() is None
        for name, content in payloads.items(): assert z.read('rechoice-source/' + name) == content
    temp.replace(archive)
    archive.chmod(0o600)
    receipt = {'archive': str(archive.relative_to(ROOT)), 'bytes': archive.stat().st_size,
               'sha256': digest(archive.read_bytes()), 'entries': len(payloads), 'crc_and_payload_hashes_ok': True,
               'runtime_files_unchanged': len(runtime_checked), 'personal_history_email_values_checked': len(personal_emails),
               'private_values_checked': len(private_values), 'sensitive_matches': 0,
               'tracked_sources_included': len(selected), 'original_history_included': False,
               'publication_performed': False, 'paid_calls': 0, 'smoke_checks': 'pending',
               'claim_entries_retained': len(retained_claims), 'claim_entries_removed': removed_claims,
               'test_dependency_review': 'No test dependencies on omitted deployment/workflow/docs; one optional calibration test depends on deliberately excluded private pilot images.',
               'expected_optional_test_skips': ['exchange.test_colour_check.ColourCheckTests.test_calibration_on_the_recorded_pilot']}
    (OUT / 'validation.json').write_text(json.dumps(receipt, indent=2) + '\n')
    print(json.dumps(receipt))

if __name__ == '__main__': main()
