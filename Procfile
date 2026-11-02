release: python manage.py migrate --noinput
web: gunicorn config.wsgi --bind 0.0.0.0:${PORT:-8000} --workers ${WEB_CONCURRENCY:-2} --timeout 30 --access-logfile - --error-logfile -
worker: celery -A config worker --loglevel INFO --pool threads --concurrency ${WORKER_CONCURRENCY:-4}
beat: celery -A config beat --loglevel INFO --schedule /tmp/celerybeat-schedule
