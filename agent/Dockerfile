# Cloud Run image for the Clinic Broker (adk web UI + API).
# Node is included because the MCP Toolbox server runs via npx.
FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends nodejs npm \
    && rm -rf /var/lib/apt/lists/*
# Pre-download the MCP Toolbox so the first request doesn't wait on npm.
RUN npx -y @toolbox-sdk/server --version || true

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY clinic_agent/ /app/agents/clinic_agent/

ENV PORT=8080
CMD ["sh", "-c", "adk web --host 0.0.0.0 --port ${PORT} /app/agents"]
