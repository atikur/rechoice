"""Rechoice settings. Local development needs no environment; production reads it all from there.

All external integrations are opt-in. Production means DJANGO_DEBUG=0, which turns on HTTPS-only
cookies, an HTTPS redirect, HSTS and hashed static files, and requires DJANGO_SECRET_KEY and
DJANGO_ALLOWED_HOSTS.
"""
import os
from pathlib import Path
from urllib.parse import parse_qsl, unquote, urlparse


def _env_list(name):
    return [item.strip() for item in os.environ.get(name, "").split(",") if item.strip()]

BASE_DIR = Path(__file__).resolve().parent.parent
SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY", "local-demo-only-change-before-deployment")
DEBUG = os.environ.get("DJANGO_DEBUG", "1") == "1"
if not DEBUG and (len(SECRET_KEY) < 50 or SECRET_KEY == "local-demo-only-change-before-deployment"):
    raise RuntimeError("Set DJANGO_SECRET_KEY to a random value of at least 50 characters "
                       "before running outside local development.")
ALLOWED_HOSTS = _env_list("DJANGO_ALLOWED_HOSTS") or (["localhost", "127.0.0.1", "testserver"] if DEBUG else [])
if not DEBUG and not ALLOWED_HOSTS:
    raise RuntimeError("Set DJANGO_ALLOWED_HOSTS to the site's host names before running outside local development.")
# Full origins such as https://rechoice.example.com, needed when a proxy terminates HTTPS.
CSRF_TRUSTED_ORIGINS = _env_list("DJANGO_CSRF_TRUSTED_ORIGINS")
# Private deployment comes before public launch. This asks crawlers not to index it; it is not access control.
SITE_NOINDEX = os.environ.get("SITE_NOINDEX", "1") == "1"
INSTALLED_APPS = [
    "django.contrib.admin", "django.contrib.auth", "django.contrib.contenttypes",
    "django.contrib.sessions", "django.contrib.messages", "django.contrib.staticfiles",
    "rest_framework", "exchange",
]
MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    # runserver serves static files in development; WhiteNoise serves the collected copies in production.
    *([] if DEBUG else ["whitenoise.middleware.WhiteNoiseMiddleware"]),
    "config.middleware.SearchIndexingMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]
ROOT_URLCONF = "config.urls"
TEMPLATES = [{
    "BACKEND": "django.template.backends.django.DjangoTemplates",
    "DIRS": [BASE_DIR / "templates"],
    "APP_DIRS": True,
    "OPTIONS": {"context_processors": [
        "django.template.context_processors.debug",
        "django.template.context_processors.request",
        "django.contrib.auth.context_processors.auth",
        "django.contrib.messages.context_processors.messages",
    ]},
}]
WSGI_APPLICATION = "config.wsgi.application"
db_url = os.environ.get("DATABASE_URL", "postgresql://localhost/secondlook")
if db_url.startswith("sqlite:///"):
    # Take the write lock when a transaction starts so concurrent worker threads wait instead of failing.
    DATABASES = {"default": {"ENGINE": "django.db.backends.sqlite3",
                             "NAME": db_url.removeprefix("sqlite:///"),
                             "OPTIONS": {"transaction_mode": "IMMEDIATE", "timeout": 20}}}
else:
    parsed = urlparse(db_url)
    if parsed.scheme not in ("postgresql", "postgres"):
        raise ValueError("DATABASE_URL must use postgresql or sqlite")
    # Managed databases usually need ?sslmode=require on the URL.
    db_options = {k: v for k, v in parse_qsl(parsed.query) if k in ("sslmode", "sslrootcert")}
    DATABASES = {"default": {"ENGINE": "django.db.backends.postgresql",
                             "NAME": parsed.path.lstrip("/"), "USER": unquote(parsed.username or ""),
                             "PASSWORD": unquote(parsed.password or ""),
                             "HOST": parsed.hostname or "", "PORT": parsed.port or "",
                             "OPTIONS": db_options,
                             "CONN_MAX_AGE": int(os.environ.get("DB_CONN_MAX_AGE", "60")),
                             "CONN_HEALTH_CHECKS": True}}
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation." + name}
    for name in ("UserAttributeSimilarityValidator", "MinimumLengthValidator",
                 "CommonPasswordValidator", "NumericPasswordValidator")
]
LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_TZ = True
STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
# Production serves hashed, compressed copies from STATIC_ROOT, so browsers never keep stale scripts.
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage" if DEBUG
                    else "whitenoise.storage.CompressedManifestStaticFilesStorage"},
}
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
CSRF_COOKIE_SAMESITE = "Lax"
SECURE_CONTENT_TYPE_NOSNIFF = True
X_FRAME_OPTIONS = "DENY"
CELERY_BROKER_URL = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
# Local default runs preview jobs inline so the demo needs no Redis or worker.
CELERY_TASK_ALWAYS_EAGER = os.environ.get("CELERY_TASK_ALWAYS_EAGER", "1") == "1"
CELERY_TASK_EAGER_PROPAGATES = True
CELERY_TASK_IGNORE_RESULT = True
CELERY_BROKER_CONNECTION_RETRY_ON_STARTUP = True
# YouCam's docs suggest polling a task about every 10 seconds.
PREVIEW_POLL_SECONDS = int(os.environ.get("PREVIEW_POLL_SECONDS", "10"))
PREVIEW_MAX_POLLS = int(os.environ.get("PREVIEW_MAX_POLLS", "40"))
# Judges enter this code at /judge for live previews and the staff pages. Blank turns the judge path off.
JUDGE_ACCESS_CODE = os.environ.get("JUDGE_ACCESS_CODE", "")
# Judges' own daily units, kept apart from the public daily cap so public use cannot crowd them out.
JUDGE_DAILY_UNIT_CAP = int(os.environ.get("JUDGE_DAILY_UNIT_CAP", "0"))
# Whether visitors without the judge code may start live previews. Recorded results stay open to all.
PUBLIC_LIVE_PREVIEWS = os.environ.get("PUBLIC_LIVE_PREVIEWS", "1") == "1"
# A preview with no progress for this long lost its worker task and is driven again.
PREVIEW_STALL_SECONDS = int(os.environ.get("PREVIEW_STALL_SECONDS", "60"))
if PREVIEW_STALL_SECONDS < 2 * PREVIEW_POLL_SECONDS:
    raise RuntimeError("PREVIEW_STALL_SECONDS must be at least twice PREVIEW_POLL_SECONDS.")
# A preview still queued after this long ends without reaching the provider, and the shopper may retry.
PREVIEW_QUEUE_LIMIT_SECONDS = int(os.environ.get("PREVIEW_QUEUE_LIMIT_SECONDS", "300"))
# Spending limits for paid previews. Every one defaults to zero or off, so nothing can be spent
# until the owner sets them. The lifetime ceiling counts every paid job ever recorded, the pilot included.
PREVIEW_UNIT_CEILING = int(os.environ.get("PREVIEW_UNIT_CEILING", "0"))
PREVIEW_DAILY_UNIT_CAP = int(os.environ.get("PREVIEW_DAILY_UNIT_CAP", "0"))
PREVIEW_SESSION_UNIT_CAP = int(os.environ.get("PREVIEW_SESSION_UNIT_CAP", "6"))
PREVIEW_PROVIDER = os.environ.get("PREVIEW_PROVIDER", "mock")
# A reference to the owner's written approval for live previews, recorded on every paid job.
PREVIEW_LIVE_APPROVAL = os.environ.get("PREVIEW_LIVE_APPROVAL", "")
# Django never reads .env by itself; live settings must come from the process environment.
YOUCAM_API_KEY = os.environ.get("YOUCAM_API_KEY", "")
YOUCAM_BASE_URL = os.environ.get("YOUCAM_BASE_URL", "https://yce-api-01.makeupar.com")
YOUCAM_UNITS_PER_RESULT = int(os.environ.get("YOUCAM_UNITS_PER_RESULT", "2"))
# Makeup and nail previews, confirmed at 1 unit each by the read-only feature-cost check on 27 October 2026.
YOUCAM_MAKEUP_UNITS = int(os.environ.get("YOUCAM_MAKEUP_UNITS", "1"))
YOUCAM_NAIL_UNITS = int(os.environ.get("YOUCAM_NAIL_UNITS", "1"))
# Earrings VTO is 1 unit for one earring (2 for stacked wear), per the 27 October 2026 feature-cost check.
YOUCAM_EARRING_UNITS = int(os.environ.get("YOUCAM_EARRING_UNITS", "1"))
# Necklace VTO is 1 unit for single-item wear, per its reference page read on 28 October 2026.
YOUCAM_NECKLACE_UNITS = int(os.environ.get("YOUCAM_NECKLACE_UNITS", "1"))
# AI Hair Color is 1 unit in full mode, per its reference page read on 28 October 2026.
YOUCAM_HAIR_UNITS = int(os.environ.get("YOUCAM_HAIR_UNITS", "1"))
# Shoes V3 and Bag V2 are 2 units per result, per their overview pages read on 28 October 2026.
YOUCAM_SHOES_UNITS = int(os.environ.get("YOUCAM_SHOES_UNITS", "2"))
YOUCAM_BAG_UNITS = int(os.environ.get("YOUCAM_BAG_UNITS", "2"))
# Paid starts stop when the provider balance would fall below this many units.
_balance_floor = os.environ.get("YOUCAM_BALANCE_FLOOR", "")
YOUCAM_BALANCE_FLOOR = float(_balance_floor) if _balance_floor else None
YOUCAM_BALANCE_MAX_AGE_SECONDS = int(os.environ.get("YOUCAM_BALANCE_MAX_AGE_SECONDS", "600"))
CELERY_BEAT_SCHEDULE = {
    "refresh-provider-balance": {"task": "exchange.refresh_provider_balance", "schedule": 300.0},
    "reconcile-provider-units": {"task": "exchange.reconcile_provider_units", "schedule": 3600.0},
    "cleanup-expired": {"task": "exchange.cleanup_expired", "schedule": 900.0},
    "worker-heartbeat": {"task": "exchange.worker_heartbeat", "schedule": 60.0},
    "recover-stuck-previews": {"task": "exchange.recover_stuck_previews", "schedule": 60.0},
}
# A shared cache keeps rate limits and the worker heartbeat consistent across processes. Without
# CACHE_URL each process keeps its own memory cache, which is fine for one local process only.
CACHE_URL = os.environ.get("CACHE_URL", "")
CACHES = {"default": ({"BACKEND": "django.core.cache.backends.redis.RedisCache", "LOCATION": CACHE_URL}
                      if CACHE_URL else {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"})}
# Requests per address per window for starting sessions and asking for previews.
SESSION_LIMIT_PER_ADDRESS = int(os.environ.get("SESSION_LIMIT_PER_ADDRESS", "20"))
PREVIEW_REQUEST_LIMIT_PER_ADDRESS = int(os.environ.get("PREVIEW_REQUEST_LIMIT_PER_ADDRESS", "30"))
RATE_LIMIT_WINDOW_SECONDS = int(os.environ.get("RATE_LIMIT_WINDOW_SECONDS", "3600"))
# Behind N trusted reverse proxies, the client address is the Nth entry from the right of
# X-Forwarded-For. Zero means requests arrive directly and REMOTE_ADDR is used.
TRUSTED_PROXY_COUNT = int(os.environ.get("TRUSTED_PROXY_COUNT", "0"))
PRIVATE_MEDIA_ROOT = BASE_DIR / "private_media"
# Where private images live: "local" under PRIVATE_MEDIA_ROOT, or "s3", a private S3-compatible bucket that
# separate web and worker services share (docs/deployment-plan.md). The access keys use boto3's own names,
# which Railway's bucket preset sets.
PRIVATE_STORAGE = os.environ.get("PRIVATE_STORAGE", "local")
PRIVATE_STORAGE_BUCKET = os.environ.get("PRIVATE_STORAGE_BUCKET", "")
PRIVATE_STORAGE_ENDPOINT = os.environ.get("PRIVATE_STORAGE_ENDPOINT", "")
PRIVATE_STORAGE_REGION = os.environ.get("PRIVATE_STORAGE_REGION", "auto")
# "virtual" puts the bucket in the host name; some older buckets need "path".
PRIVATE_STORAGE_ADDRESSING = os.environ.get("PRIVATE_STORAGE_ADDRESSING", "virtual")
PRIVATE_STORAGE_ACCESS_KEY_ID = os.environ.get("AWS_ACCESS_KEY_ID", "")
PRIVATE_STORAGE_SECRET_ACCESS_KEY = os.environ.get("AWS_SECRET_ACCESS_KEY", "")
if PRIVATE_STORAGE not in ("local", "s3"):
    raise RuntimeError(f"Unknown PRIVATE_STORAGE {PRIVATE_STORAGE!r}; use local or s3.")
if PRIVATE_STORAGE_ADDRESSING not in ("virtual", "path"):
    raise RuntimeError(f"Unknown PRIVATE_STORAGE_ADDRESSING {PRIVATE_STORAGE_ADDRESSING!r}; use virtual or path.")
if PRIVATE_STORAGE == "s3":
    _missing = [name for name, value in (
        ("PRIVATE_STORAGE_BUCKET", PRIVATE_STORAGE_BUCKET), ("PRIVATE_STORAGE_ENDPOINT", PRIVATE_STORAGE_ENDPOINT),
        ("AWS_ACCESS_KEY_ID", PRIVATE_STORAGE_ACCESS_KEY_ID),
        ("AWS_SECRET_ACCESS_KEY", PRIVATE_STORAGE_SECRET_ACCESS_KEY)) if not value]
    if _missing:
        raise RuntimeError("PRIVATE_STORAGE=s3 needs these set: " + ", ".join(_missing) + ".")
    if not PRIVATE_STORAGE_ENDPOINT.startswith("https://"):
        raise RuntimeError("PRIVATE_STORAGE_ENDPOINT must be an https:// URL.")
elif not DEBUG and not CELERY_TASK_ALWAYS_EAGER and "PRIVATE_STORAGE" not in os.environ:
    # A worker on another machine cannot read the web service's disk, so local storage must be chosen knowingly.
    raise RuntimeError("Set PRIVATE_STORAGE: s3 when the web and worker services run apart, or local only if "
                       "they share one disk.")
ALLOW_PHOTO_UPLOAD = os.environ.get("ALLOW_PHOTO_UPLOAD", "0") == "1"
# Photo upload attempts allowed per window, per session and per client address. The default cache is
# per process; production should use a shared cache such as Redis so limits hold across workers.
UPLOAD_LIMIT_PER_SESSION = int(os.environ.get("UPLOAD_LIMIT_PER_SESSION", "3"))
UPLOAD_LIMIT_PER_ADDRESS = int(os.environ.get("UPLOAD_LIMIT_PER_ADDRESS", "10"))
UPLOAD_WINDOW_SECONDS = int(os.environ.get("UPLOAD_WINDOW_SECONDS", "3600"))
if PREVIEW_PROVIDER not in ("mock", "youcam"):
    raise RuntimeError(f"Unknown PREVIEW_PROVIDER {PREVIEW_PROVIDER!r}; use mock or youcam.")
if PREVIEW_PROVIDER != "mock":
    _missing = [name for name, ok in (
        ("PREVIEW_LIVE_APPROVAL", bool(PREVIEW_LIVE_APPROVAL)),
        ("YOUCAM_API_KEY", bool(YOUCAM_API_KEY)),
        ("PREVIEW_UNIT_CEILING", PREVIEW_UNIT_CEILING > 0),
        ("PREVIEW_DAILY_UNIT_CAP", PREVIEW_DAILY_UNIT_CAP > 0),
        ("YOUCAM_BALANCE_FLOOR", YOUCAM_BALANCE_FLOOR is not None),
        ("CELERY_TASK_ALWAYS_EAGER=0", DEBUG or not CELERY_TASK_ALWAYS_EAGER),
        ("CACHE_URL", DEBUG or bool(CACHE_URL)),
    ) if not ok]
    if _missing:
        raise RuntimeError("Live provider is disabled until these are set: " + ", ".join(_missing) + ".")

# A store's key for the retailer API (exchange/retailer.py), sent as a bearer token. Blank turns the API off.
RETAILER_API_KEY = os.environ.get("RETAILER_API_KEY", "")
if RETAILER_API_KEY and len(RETAILER_API_KEY) < 32:
    raise RuntimeError("RETAILER_API_KEY must be at least 32 characters.")

# Who reads "Fill in from my words": the fixed rules, or Claude through the Anthropic API
# (docs/ai-assistant-plan.md). Claude stays off until every limit below and the owner's approval are set.
ASSISTANT_PROVIDER = os.environ.get("ASSISTANT_PROVIDER", "rules")
ASSISTANT_MODEL = os.environ.get("ASSISTANT_MODEL", "claude-haiku-5-5")
ASSISTANT_APPROVAL = os.environ.get("ASSISTANT_APPROVAL", "")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
# Reads across the whole site per UTC day, and per shopper session. Past either, the rules answer.
ASSISTANT_DAILY_READS = int(os.environ.get("ASSISTANT_DAILY_READS", "0"))
ASSISTANT_SESSION_READS = int(os.environ.get("ASSISTANT_SESSION_READS", "5"))
ASSISTANT_MAX_TOKENS = int(os.environ.get("ASSISTANT_MAX_TOKENS", "2000"))
ASSISTANT_TIMEOUT_SECONDS = float(os.environ.get("ASSISTANT_TIMEOUT_SECONDS", "10"))
if ASSISTANT_PROVIDER not in ("rules", "claude"):
    raise RuntimeError(f"Unknown ASSISTANT_PROVIDER {ASSISTANT_PROVIDER!r}; use rules or claude.")
if ASSISTANT_PROVIDER == "claude":
    _missing = [name for name, ok in (
        ("ASSISTANT_APPROVAL", bool(ASSISTANT_APPROVAL)),
        ("ANTHROPIC_API_KEY", bool(ANTHROPIC_API_KEY)),
        ("ASSISTANT_DAILY_READS", ASSISTANT_DAILY_READS > 0),
        ("ASSISTANT_SESSION_READS", ASSISTANT_SESSION_READS > 0),
    ) if not ok]
    if _missing:
        raise RuntimeError("The Claude assistant is disabled until these are set: " + ", ".join(_missing) + ".")

# Separate, explicitly approved copilot/visual-review budget.
EXCHANGE_AI_PROVIDER = os.environ.get("EXCHANGE_AI_PROVIDER", "off")
EXCHANGE_AI_JUDGE_ONLY = os.environ.get("EXCHANGE_AI_JUDGE_ONLY", "1") == "1"
EXCHANGE_AI_APPROVAL = os.environ.get("EXCHANGE_AI_APPROVAL", "")
EXCHANGE_AI_MAX_USD = os.environ.get("EXCHANGE_AI_MAX_USD", "0")
EXCHANGE_AI_DAILY_CALLS = int(os.environ.get("EXCHANGE_AI_DAILY_CALLS", "0"))
EXCHANGE_AI_SESSION_CALLS = int(os.environ.get("EXCHANGE_AI_SESSION_CALLS", "12"))
EXCHANGE_AI_MODEL = os.environ.get("EXCHANGE_AI_MODEL", "claude-haiku-5-5")
VISUAL_REVIEW_MODEL = os.environ.get("VISUAL_REVIEW_MODEL", "claude-sonnet-5-5")
EXCHANGE_AI_TIMEOUT_SECONDS = float(os.environ.get("EXCHANGE_AI_TIMEOUT_SECONDS", "10"))
if EXCHANGE_AI_PROVIDER not in ("off", "claude"):
    raise RuntimeError("EXCHANGE_AI_PROVIDER must be off or claude.")
if EXCHANGE_AI_PROVIDER == "claude":
    from decimal import Decimal, InvalidOperation
    try:
        _exchange_ai_cap = Decimal(EXCHANGE_AI_MAX_USD)
        _exchange_ai_valid = _exchange_ai_cap.is_finite() and _exchange_ai_cap > 0
    except InvalidOperation:
        _exchange_ai_valid = False
    if not (EXCHANGE_AI_APPROVAL and ANTHROPIC_API_KEY and _exchange_ai_valid
            and EXCHANGE_AI_DAILY_CALLS > 0 and EXCHANGE_AI_SESSION_CALLS > 0):
        raise RuntimeError("Exchange AI requires its own approval, positive USD/call caps and API key.")

# HTTPS and headers. Local development stays on plain HTTP.
SECURE_REFERRER_POLICY = "same-origin"
if TRUSTED_PROXY_COUNT:
    # The proxy terminates HTTPS and says so in X-Forwarded-Proto.
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
if not DEBUG:
    SECURE_SSL_REDIRECT = os.environ.get("DJANGO_SECURE_SSL_REDIRECT", "1") == "1"
    # Load balancers often check health over plain HTTP inside the platform.
    SECURE_REDIRECT_EXEMPT = [r"^api/health$"]
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    # Start HSTS short; raise DJANGO_HSTS_SECONDS once HTTPS is confirmed for the real domain.
    SECURE_HSTS_SECONDS = int(os.environ.get("DJANGO_HSTS_SECONDS", "3600"))
    SECURE_HSTS_INCLUDE_SUBDOMAINS = os.environ.get("DJANGO_HSTS_INCLUDE_SUBDOMAINS", "0") == "1"
# Deliberate: subdomain coverage is opt-in because the final domain is not chosen, and HSTS preload
# needs a manual, hard-to-reverse submission.
SILENCED_SYSTEM_CHECKS = ["security.W005", "security.W021"]

# Production logs to stdout for the host to collect. Development keeps Django's default logging.
if not DEBUG:
    LOGGING = {
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": {"plain": {"format": "%(asctime)s %(levelname)s %(name)s %(message)s"}},
        "handlers": {"console": {"class": "logging.StreamHandler", "formatter": "plain"},
                     "discard": {"class": "logging.NullHandler"}},
        "root": {"handlers": ["console"], "level": os.environ.get("LOG_LEVEL", "INFO")},
        # Client errors (4xx) are expected traffic; keep request logging to server errors.
        "loggers": {
            "django.request": {"level": os.environ.get("DJANGO_REQUEST_LOG_LEVEL", "ERROR")},
            # Requests for unknown host names are routine scanner noise; they are refused with 400 anyway.
            "django.security.DisallowedHost": {"handlers": ["discard"], "propagate": False},
        },
    }
