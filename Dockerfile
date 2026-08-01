# Multi-stage: wheels get built with a compiler present, the runtime image gets none.
# No Node stage — app/static/app.css is built by tools/build_css.sh and committed,
# so the image needs Python only.
FROM python:3.12-slim AS build

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

RUN apt-get update \
 && apt-get install -y --no-install-recommends build-essential \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /wheels
COPY requirements.txt .
RUN pip wheel --wheel-dir /wheels -r requirements.txt


FROM python:3.12-slim AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    DATABASE_PATH=/data/mogi.db \
    BACKUP_DIR=/data/backups \
    PORT=8000 \
    TZ=UTC

# UID/GID 568 is `apps` on TrueNAS SCALE. Matching it is what makes the bind-mounted
# dataset writable without ACL surgery.
RUN groupadd -g 568 apps && useradd -u 568 -g 568 -m -s /usr/sbin/nologin apps

COPY --from=build /wheels /wheels
COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-index --find-links=/wheels -r /tmp/requirements.txt \
 && rm -rf /wheels /tmp/requirements.txt

WORKDIR /app
COPY --chown=568:568 alembic.ini ./
COPY --chown=568:568 alembic ./alembic
COPY --chown=568:568 app ./app

RUN mkdir -p /data /data/backups && chown -R 568:568 /data
VOLUME ["/data"]

USER 568:568
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import os,urllib.request,sys; \
sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:'+os.environ.get('PORT','8000')+'/healthz', timeout=4).status==200 else 1)"

# Migrations run in-process at startup (app/main.py), so there is nothing to exec
# by hand — which matters, because there is no SSH to this host.
CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
