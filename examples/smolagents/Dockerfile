FROM python:3.12-slim
WORKDIR /app
RUN pip install --no-cache-dir 'smolagents[openai]==1.26.0' 'fastapi>=0.115,<1' 'uvicorn>=0.34,<1' 'httpx>=0.28,<1'
COPY agent_server.py /app/agent_server.py
ENV PYTHONDONTWRITEBYTECODE=1 HF_HOME=/tmp/huggingface
USER 65534:65534
EXPOSE 9091
ENTRYPOINT ["python", "-B", "/app/agent_server.py", "--host", "0.0.0.0", "--port", "9091", "--backend", "offline"]
