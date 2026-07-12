FROM python:3.11-slim AS core
WORKDIR /app
COPY pyproject.toml .
COPY src/ src/
RUN pip install --no-cache-dir .
COPY migrations/ migrations/
COPY alembic.ini .

FROM core AS migrate
CMD ["alembic", "upgrade", "head"]

FROM core AS app
RUN pip install --no-cache-dir ".[server]"
COPY web/ web/
CMD ["uvicorn", "ndwinfo.api.main:app", "--host", "0.0.0.0", "--port", "3500"]

FROM core AS poller
# Pyogrio's manylinux wheel bundles the required GDAL runtime. Avoid Debian's
# `libgdal-dev`, which pulls hundreds of build/development packages (~800 MB).
RUN pip install --no-cache-dir ".[ingest]"
CMD ["python", "-m", "ndwinfo.poller"]
