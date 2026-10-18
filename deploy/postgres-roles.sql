-- Run as database owner in the target database, before starting the services.
-- Set passwords interactively with psql \password, never commit credentials.
CREATE ROLE remotedesk_relay LOGIN;
CREATE ROLE remotedesk_accounts LOGIN;
CREATE SCHEMA relay AUTHORIZATION remotedesk_relay;
CREATE SCHEMA accounts AUTHORIZATION remotedesk_accounts;
ALTER ROLE remotedesk_relay SET search_path = relay;
ALTER ROLE remotedesk_accounts SET search_path = accounts;
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
-- Use separate roles; do not grant superuser, replication, or cross-schema access.
