FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /opt/price-oracle

RUN addgroup --system price-oracle \
    && adduser --system --ingroup price-oracle price-oracle \
    && mkdir -p /data \
    && chown price-oracle:price-oracle /data

COPY backend/requirements-engine.txt ./requirements.txt
RUN python -m pip install --no-cache-dir --requirement requirements.txt

COPY --chown=price-oracle:price-oracle backend/engine ./engine

USER price-oracle

VOLUME ["/data"]

CMD ["python", "-m", "engine"]
