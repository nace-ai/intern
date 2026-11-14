# Intern (Nace harness) does not run on native Windows (needs fcntl), so we run it in Linux.
# Build:  docker build -t quorum-intern intern      Run: see intern/run.ps1
FROM python:3.12-slim
RUN apt-get update -qq && apt-get install -y -qq --no-install-recommends git ca-certificates curl && rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir "intern-agent @ git+https://github.com/nace-ai/intern"
RUN pip install --no-cache-dir nace-sdk pymupdf pyarrow pydantic httpx pytest pytest-asyncio fastapi "uvicorn[standard]" python-dotenv
# Node for building the web UI (web/). Kept as the last layer so the layers above stay cached.
RUN apt-get update -qq && apt-get install -y -qq --no-install-recommends nodejs npm && rm -rf /var/lib/apt/lists/*
ENV INTERN_HOME=/intern-home PYTHONIOENCODING=utf-8
WORKDIR /work
ENTRYPOINT ["intern"]
