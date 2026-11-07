# Kopifund in one container, for a home server: the web app built by Node, then
# served with the API by server/serve.py. The data folder (record, statements,
# config, .env) is a volume at /data and never part of the image.
#
#   docker build -t kopifund .
#   docker run -d --name kopifund -p 8770:8770 -v kopifund-data:/data \
#     -e KOPIFUND_HOSTS=homeserver.local kopifund
#
# docker stop sends SIGTERM, and serve.py waits up to 120 s for a running load or
# stats rebuild; STOPSIGNAL is explicit and `docker stop -t 130` gives it the time.
#
# KOPIFUND_HOSTS lists the names the page is opened under (serve.py's docstring).
# Secrets (NOTION_TOKEN, <PROVIDER>_API_KEY) come in as -e variables or as a
# .env file in the volume; there is no Keychain on Linux.

# ---- the web app
FROM node:22-bookworm-slim AS web
WORKDIR /src/apps/web
COPY apps/web/package.json apps/web/package-lock.json ./
RUN npm ci
COPY apps/web/ ./
RUN npm run build

# ---- the server
FROM python:3.14-slim-bookworm
# pdftotext reads every statement (server/finance/pdftext.py)
RUN apt-get update \
 && apt-get install -y --no-install-recommends poppler-utils \
 && rm -rf /var/lib/apt/lists/*

RUN useradd --create-home --uid 1000 kopifund \
 && mkdir /data && chown kopifund:kopifund /data

WORKDIR /app
COPY server/ server/
COPY data/rules/ data/rules/
# the icon registry Settings' App icon card reads (server/finance/app_icon.py)
COPY app-icons/sets.json app-icons/sets.json
COPY --from=web /src/apps/web/dist apps/web/dist

ENV KOPIFUND_DATA=/data \
    KOPIFUND_NO_KEYCHAIN=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

USER kopifund
VOLUME /data
EXPOSE 8770

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s \
  CMD python3 -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8770/api/health', timeout=4)"

STOPSIGNAL SIGTERM
CMD ["python3", "server/serve.py", "--host", "0.0.0.0", "--port", "8770"]
