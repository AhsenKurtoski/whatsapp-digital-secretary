# Dashboard-only image. OpenClaw and the WhatsApp session stay on the host.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    SECRETARY_BASE=/data \
    SECRETARY_CONFIG=/config/config.json \
    OPENCLAW_CONFIG=/openclaw/openclaw.json \
    OPENCLAW_DB=/openclaw/agents/main/agent/openclaw-agent.sqlite \
    DASHBOARD_HOST=0.0.0.0 \
    DASHBOARD_PORT=8787

RUN groupadd --gid 1000 app \
    && useradd --uid 1000 --gid app --create-home app \
    && mkdir -p /app /data /config /openclaw \
    && chown -R app:app /app /data

WORKDIR /app
COPY dashboard.py dashboard.html config.py approval_store.py approval_mcp.py \
     approval_setup.py email_template.py sync_fallbacks.py \
     monitor.py watchdog.py digest.py ./
COPY google-bridge/bridge_client.py ./bridge_client.py
COPY docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
RUN chmod 755 /usr/local/bin/docker-entrypoint.sh \
    && chown -R app:app /app

USER app
EXPOSE 8787

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD python3 -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8787/', timeout=4)"

ENTRYPOINT ["/usr/local/bin/docker-entrypoint.sh"]
