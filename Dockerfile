FROM python:3.11-slim

RUN apt-get update && apt-get install -y \
    libgdal-dev \
    gdal-bin \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY pyproject.toml .
RUN mkdir -p src/ndwinfo && touch src/ndwinfo/__init__.py \
    && pip install --no-cache-dir . \
    && rm -rf src

COPY src/ src/
RUN pip install --no-cache-dir --no-deps .
COPY web/ web/
COPY migrations/ migrations/
COPY alembic.ini .

CMD ["uvicorn", "ndwinfo.api.main:app", "--host", "0.0.0.0", "--port", "3500"]
