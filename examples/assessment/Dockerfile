FROM python:3.12-slim
WORKDIR /app
COPY mock_agent.py /app/mock_agent.py
USER 65534:65534
EXPOSE 9081
ENTRYPOINT ["python", "-I", "-B", "/app/mock_agent.py", "--host", "0.0.0.0", "--port", "9081"]
