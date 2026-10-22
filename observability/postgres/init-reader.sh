#!/bin/sh
set -eu
psql --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" --set=reader_password="$GRAFANA_READER_PASSWORD" <<'SQL'
CREATE ROLE grafanareader LOGIN PASSWORD :'reader_password';
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
GRANT CONNECT ON DATABASE ticketagents TO grafanareader;
GRANT USAGE ON SCHEMA public TO grafanareader;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO grafanareader;
ALTER DEFAULT PRIVILEGES FOR ROLE ticketagents IN SCHEMA public GRANT SELECT ON TABLES TO grafanareader;
SQL
