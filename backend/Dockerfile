FROM python:3.12-slim

# WeasyPrint (PDF rendering) needs Pango; zbar/tesseract are only needed on the scan station.
RUN apt-get update \
    && apt-get install -y --no-install-recommends libpango-1.0-0 libpangoft2-1.0-0 libharfbuzz-subset0 fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
# Dependencies first (cached layer); the app itself runs from /app, not site-packages.
COPY pyproject.toml constraints.txt ./
RUN pip install --no-cache-dir -c constraints.txt .
COPY . .

# Fixed UID so restored backup files can be handed back to this user (deploy/backup/restore.sh).
RUN useradd --uid 10001 --create-home appuser && mkdir -p /data/storage && chown appuser /data/storage
USER appuser

ENV ADGVS_STORAGE_DIR=/data/storage
EXPOSE 8000
# Migrations run in the separate `migrate` service as the database owner; the API itself
# connects with a least-privilege login. Only Nginx can reach port 8000, so its
# X-Forwarded-For header is trusted for client IPs in the audit log.
CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port 8000 --proxy-headers --forwarded-allow-ips='*' --workers ${API_WORKERS:-2}"]
