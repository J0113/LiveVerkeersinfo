FROM python:3.11-slim

RUN apt-get update && apt-get install -y \
    libgdal-dev \
    gdal-bin \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY pyproject.toml .
COPY src/ src/
RUN pip install --no-cache-dir .
COPY web/ web/
COPY migrations/ migrations/
COPY alembic.ini .

CMD ["uvicorn", "ndwinfo.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
