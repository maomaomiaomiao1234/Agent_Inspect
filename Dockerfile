ARG NODE_IMAGE=node:22-alpine
ARG PYTHON_IMAGE=python:3.12-slim
FROM ${NODE_IMAGE} AS web
WORKDIR /web
COPY web/package.json web/package-lock.json ./
RUN npm ci
COPY web/ ./
RUN npm run build

FROM ${PYTHON_IMAGE}
WORKDIR /app
COPY deploy/requirements.txt /app/requirements.txt
RUN apt-get update && apt-get install -y --no-install-recommends git && rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir --require-hashes -r /app/requirements.txt
COPY src/agent_trace_review/ /app/src/agent_trace_review/
COPY --from=web /web/dist/ /app/src/agent_trace_review/static/
RUN mkdir -p /data /config && chown -R 10001:10001 /data /config
ENV PYTHONPATH=/app/src PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
USER 10001:10001
EXPOSE 8765
HEALTHCHECK --interval=10s --timeout=3s --start-period=10s CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8765/api/health', timeout=2)"
ENTRYPOINT ["python", "-m", "agent_trace_review.cli", "serve", "--host", "0.0.0.0", "--data-dir", "/data", "--targets", "/config/targets.json"]
