# Generic deployment guidance

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
