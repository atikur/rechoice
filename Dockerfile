FROM python:3.12.14-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PORT=8000

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
    && groupadd --gid 10001 rechoice \
    && useradd --uid 10001 --gid rechoice --no-create-home rechoice

COPY manage.py .
COPY config ./config
COPY secondlook ./secondlook
COPY exchange ./exchange
COPY assets ./assets
COPY deploy/entrypoint.sh deploy/healthcheck.py ./deploy/
COPY scripts/smoke_deployment.py scripts/check_heldout.py ./scripts/

# Build public, hashed assets without connecting to a database or loading deployment secrets.
RUN DJANGO_DEBUG=0 \
    DJANGO_SECRET_KEY=build-only-placeholder-never-used-by-a-running-deployment-00000000 \
    DJANGO_ALLOWED_HOSTS=build.invalid DATABASE_URL=sqlite:///:memory: PRIVATE_STORAGE=local \
    python manage.py collectstatic --noinput \
    && install -d -o 10001 -g 10001 -m 700 /app/private_media \
    && chmod 755 /app/deploy/entrypoint.sh

USER 10001:10001
EXPOSE 8000
ENTRYPOINT ["/app/deploy/entrypoint.sh"]
CMD ["gunicorn", "config.wsgi", "--bind", "0.0.0.0:8000", "--workers", "1", "--worker-class", "gthread", "--threads", "2", "--timeout", "30", "--access-logfile", "-", "--error-logfile", "-"]
