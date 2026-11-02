import json
import os
import secrets
import subprocess
import sys
from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase, override_settings

ROOT = Path(settings.BASE_DIR)
PRODUCTION = {
    "DJANGO_DEBUG": "0", "DJANGO_ALLOWED_HOSTS": "rechoice.example.com",
    "DJANGO_CSRF_TRUSTED_ORIGINS": "https://rechoice.example.com", "TRUSTED_PROXY_COUNT": "1",
    "CACHE_URL": "redis://127.0.0.1:6399/1", "DATABASE_URL": "sqlite:///unused.sqlite3",
}
CLEARED = ("DJANGO_DEBUG", "DJANGO_ALLOWED_HOSTS", "DJANGO_CSRF_TRUSTED_ORIGINS", "TRUSTED_PROXY_COUNT",
           "DJANGO_SECURE_SSL_REDIRECT", "DJANGO_HSTS_SECONDS", "DJANGO_HSTS_INCLUDE_SUBDOMAINS", "CACHE_URL",
           "PREVIEW_PROVIDER", "DJANGO_SECRET_KEY", "CELERY_TASK_ALWAYS_EAGER", "PRIVATE_STORAGE",
           "PRIVATE_STORAGE_BUCKET", "PRIVATE_STORAGE_ENDPOINT", "AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY",
           "SITE_NOINDEX")
READ = """
import json, config.settings as s
print(json.dumps({name: getattr(s, name, None) for name in (
    "DEBUG", "ALLOWED_HOSTS", "CSRF_TRUSTED_ORIGINS", "SECURE_SSL_REDIRECT", "SECURE_REDIRECT_EXEMPT",
    "SESSION_COOKIE_SECURE", "CSRF_COOKIE_SECURE", "SECURE_HSTS_SECONDS", "SECURE_HSTS_INCLUDE_SUBDOMAINS",
    "SECURE_PROXY_SSL_HEADER", "SECURE_REFERRER_POLICY", "MIDDLEWARE", "STORAGES", "DATABASES",
    "AUTH_PASSWORD_VALIDATORS", "LOGGING", "SITE_NOINDEX")}, default=str))
"""


def run(args, **env):
    base = {k: v for k, v in os.environ.items() if k not in CLEARED}
    return subprocess.run([sys.executable, *args], cwd=ROOT, env={**base, **env},
                          capture_output=True, text=True, timeout=120)


def production(**overrides):
    return {**PRODUCTION, "DJANGO_SECRET_KEY": secrets.token_urlsafe(50), **overrides}


def read_settings(**env):
    result = run(["-c", READ], **env)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


class DeployConfigTests(SimpleTestCase):
    def test_private_deployment_defaults_to_noindex_on_pages_and_errors(self):
        self.assertTrue(read_settings(**production())["SITE_NOINDEX"])
        with override_settings(SITE_NOINDEX=True):
            for route, status in (("/", 200), ("/missing-page", 404)):
                response = self.client.get(route)
                self.assertEqual(response.status_code, status)
                self.assertEqual(response.headers["X-Robots-Tag"], "noindex, nofollow")

    def test_public_launch_can_remove_the_noindex_header(self):
        self.assertFalse(read_settings(**production(SITE_NOINDEX="0"))["SITE_NOINDEX"])
        with override_settings(SITE_NOINDEX=False):
            response = self.client.get("/")
            self.assertEqual(response.status_code, 200)
            self.assertNotIn("X-Robots-Tag", response.headers)

    def test_production_refuses_to_start_without_hosts(self):
        result = run(["-c", "import config.settings"], **production(DJANGO_ALLOWED_HOSTS=""))
        self.assertIn("Set DJANGO_ALLOWED_HOSTS", result.stderr)

    def test_production_refuses_an_empty_or_short_secret_key(self):
        for key in ("", "too-short"):
            with self.subTest(key=key):
                result = run(["-c", "import config.settings"], **{**production(), "DJANGO_SECRET_KEY": key})
                self.assertIn("Set DJANGO_SECRET_KEY", result.stderr)

    def test_production_turns_on_https_hashed_static_and_strict_cookies(self):
        s = read_settings(**production())
        self.assertEqual((s["DEBUG"], s["ALLOWED_HOSTS"], s["CSRF_TRUSTED_ORIGINS"]),
                         (False, ["rechoice.example.com"], ["https://rechoice.example.com"]))
        self.assertTrue(s["SECURE_SSL_REDIRECT"] and s["SESSION_COOKIE_SECURE"] and s["CSRF_COOKIE_SECURE"])
        self.assertEqual((s["SECURE_HSTS_SECONDS"], s["SECURE_HSTS_INCLUDE_SUBDOMAINS"]), (3600, False))
        self.assertEqual(s["SECURE_PROXY_SSL_HEADER"], ["HTTP_X_FORWARDED_PROTO", "https"])
        self.assertEqual(s["SECURE_REDIRECT_EXEMPT"], ["^api/health$"])
        self.assertEqual(s["MIDDLEWARE"][:2], ["django.middleware.security.SecurityMiddleware",
                                              "whitenoise.middleware.WhiteNoiseMiddleware"])
        self.assertEqual(s["STORAGES"]["staticfiles"]["BACKEND"], "whitenoise.storage.CompressedManifestStaticFilesStorage")
        self.assertEqual(len(s["AUTH_PASSWORD_VALIDATORS"]), 4)
        self.assertEqual(s["LOGGING"]["root"]["handlers"], ["console"])

    def test_managed_postgres_urls_keep_sslmode(self):
        s = read_settings(**production(DATABASE_URL="postgresql://app:pw@db.example.com:5432/secondlook?sslmode=require"))
        db = s["DATABASES"]["default"]
        self.assertEqual((db["HOST"], db["NAME"], db["OPTIONS"], db["CONN_MAX_AGE"]),
                         ("db.example.com", "secondlook", {"sslmode": "require"}, 60))

    def test_django_deployment_check_passes(self):
        result = run(["manage.py", "check", "--deploy", "--fail-level", "WARNING"], **production())
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertIn("System check identified no issues (2 silenced)", result.stdout)

    def test_a_separate_worker_needs_shared_storage_chosen_knowingly(self):
        worker = production(CELERY_TASK_ALWAYS_EAGER="0")
        result = run(["-c", "import config.settings"], **worker)
        self.assertIn("Set PRIVATE_STORAGE: s3 when the web and worker services run apart", result.stderr)
        self.assertEqual(run(["-c", "import config.settings"], **worker, PRIVATE_STORAGE="local").returncode, 0)

    def test_the_bucket_names_every_missing_setting_and_needs_https(self):
        result = run(["-c", "import config.settings"], **production(PRIVATE_STORAGE="s3"))
        self.assertIn("PRIVATE_STORAGE=s3 needs these set: PRIVATE_STORAGE_BUCKET, PRIVATE_STORAGE_ENDPOINT, "
                      "AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY.", result.stderr)
        bucket = dict(PRIVATE_STORAGE="s3", PRIVATE_STORAGE_BUCKET="b", AWS_ACCESS_KEY_ID="test-only",
                      AWS_SECRET_ACCESS_KEY="test-only")
        plain = run(["-c", "import config.settings"], **production(**bucket, PRIVATE_STORAGE_ENDPOINT="http://x"))
        self.assertIn("PRIVATE_STORAGE_ENDPOINT must be an https:// URL.", plain.stderr)
        self.assertIn("Unknown PRIVATE_STORAGE 'disk'", run(["-c", "import config.settings"],
                                                             **production(PRIVATE_STORAGE="disk")).stderr)
        ready = production(**bucket, PRIVATE_STORAGE_ENDPOINT="https://t3.storageapi.dev", CELERY_TASK_ALWAYS_EAGER="0")
        check = run(["manage.py", "check", "--deploy", "--fail-level", "WARNING"], **ready)
        self.assertEqual(check.returncode, 0, check.stderr)

    def test_procfile_defines_release_web_worker_and_beat(self):
        processes = dict(line.split(": ", 1) for line in (ROOT / "Procfile").read_text().splitlines() if line.strip())
        self.assertEqual(set(processes), {"release", "web", "worker", "beat"})
        self.assertIn("migrate --noinput", processes["release"])
        self.assertTrue(processes["web"].startswith("gunicorn config.wsgi --bind 0.0.0.0:${PORT"))
        self.assertTrue(processes["worker"].startswith("celery -A config worker"))
        self.assertIn("--pool threads", processes["worker"])
        self.assertTrue(processes["beat"].startswith("celery -A config beat"))

    def test_local_development_defaults_are_unchanged(self):
        s = read_settings(DATABASE_URL="sqlite:///unused.sqlite3")
        self.assertEqual((s["DEBUG"], s["ALLOWED_HOSTS"]), (True, ["localhost", "127.0.0.1", "testserver"]))
        self.assertIsNone(s["SECURE_SSL_REDIRECT"])
        self.assertEqual(s["STORAGES"]["staticfiles"]["BACKEND"], "django.contrib.staticfiles.storage.StaticFilesStorage")
        self.assertEqual(s["DATABASES"]["default"]["OPTIONS"], {"transaction_mode": "IMMEDIATE", "timeout": 20})
        self.assertNotIn("whitenoise.middleware.WhiteNoiseMiddleware", s["MIDDLEWARE"])
        self.assertIsNone(s["LOGGING"])
