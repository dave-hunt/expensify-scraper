FROM python:3.13-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Chromium is required to remint API tokens from the saved browser session.
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt \
    && playwright install --with-deps chromium

COPY pyproject.toml ./
COPY src ./src
RUN pip install --no-cache-dir --no-deps -e .

ENV EXPENSIFY_OUTPUT_DIR=/data/out
ENV EXPENSIFY_DATA_DIR=/data
ENV EXPENSIFY_AUTH_DIR=/data/.auth

VOLUME ["/data"]

ENTRYPOINT ["expensify-scraper"]
