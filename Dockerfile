FROM python:3.14-slim AS builder

WORKDIR /build

COPY requirements.txt .
RUN python -m venv /opt/venv && \
    /opt/venv/bin/pip install --no-cache-dir -r requirements.txt

FROM python:3.14-slim

RUN groupadd -r appuser && useradd -r -g appuser -d /app appuser

WORKDIR /app

COPY --from=builder /opt/venv /opt/venv
COPY src/ src/
COPY config/ config/

RUN mkdir -p data logs && chown -R appuser:appuser /app

ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONPATH="src" \
    PYTHONUNBUFFERED="1"

USER appuser

CMD ["python", "-m", "tokenomics"]
