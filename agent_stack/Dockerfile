FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY . /app/agent_stack

EXPOSE 11888

ENV PYTHONPATH=/app
ENV OLLAMA_AMD_ENDPOINT=http://host.docker.internal:11435
ENV OLLAMA_NVIDIA_ENDPOINT=http://host.docker.internal:11434
ENV DRAGONLAIR_LOCK_ROOT=/tmp/dragonlair_agent_stack

CMD ["python3", "-m", "uvicorn", "agent_stack.api_server:app", "--host", "0.0.0.0", "--port", "11888"]
