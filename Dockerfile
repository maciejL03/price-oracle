FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

RUN addgroup --system --gid 10001 priceoracle \
    && adduser --system --uid 10001 --ingroup priceoracle --home /app priceoracle

COPY backend/requirements-runtime.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY --chown=priceoracle:priceoracle backend/engine ./engine
RUN mkdir --parents /var/lib/price-oracle \
    && chown priceoracle:priceoracle /var/lib/price-oracle

USER priceoracle

CMD ["python", "-m", "engine.runtime"]
