# Cloud Run image: Clinic Broker UI + ADK agent API in one service.
# Built by deploy/cloud_run_ui.sh, which assembles the build folder.
FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends nodejs npm \
    && rm -rf /var/lib/apt/lists/*
RUN npx -y @toolbox-sdk/server --version || true

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY clinic_agent/ /app/agents/clinic_agent/
COPY server.py /app/server.py
COPY static/ /app/static/

ENV AGENTS_DIR=/app/agents PORT=8080
CMD ["python", "server.py"]
