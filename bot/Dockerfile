FROM python:3.11-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN apt update && \
    apt install -y --no-install-recommends \
    build-essential libffi-dev libssl-dev && \
    rm -rf /var/lib/apt/lists/*

RUN useradd --create-home appuser

WORKDIR /src

COPY ./ /src

RUN mkdir -p /src/logs

RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir wheel newtulipy \
    && pip install --no-cache-dir -r requirements.txt

RUN chown -R appuser:appuser /src

USER appuser

CMD ["python", "main.py"]
# CMD ["tail", "-f", "/dev/null"]

