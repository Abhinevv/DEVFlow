# Execution sandbox for generated projects.
#   docker build -t devflow-sandbox:latest sandbox
#
# Only the template's approved dependencies are installed; containers run with --network none,
# so generated code cannot install anything else. DockerExecutor adds: read-only root and
# workspace mounts, a 64 MB tmpfs at /tmp, memory/CPU/PID limits, all capabilities dropped,
# no-new-privileges, and uid 65534.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN pip install \
      "fastapi==0.115.6" \
      "starlette==0.41.3" \
      "pydantic==2.10.4" \
      "sqlalchemy==2.0.36" \
      "httpx==0.28.1" \
      "pytest==8.3.4" \
      "uvicorn==0.34.0" \
 && rm -rf /root/.cache

WORKDIR /workspace
USER 65534:65534
