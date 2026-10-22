# Same major version as the database, so pg_dump/pg_restore match the server.
FROM postgres:16
RUN apt-get update \
    && apt-get install -y --no-install-recommends gnupg \
    && rm -rf /var/lib/apt/lists/*
# Build context is deploy/ (docker-compose.yml), so the shared scheduler is in reach.
COPY backup/backup.sh backup/verify-backup.sh backup/restore.sh daily.sh /usr/local/bin/
RUN chmod 755 /usr/local/bin/*.sh
