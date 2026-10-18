#!/bin/sh
# Configure a protected .pgpass/PGSERVICE and an offline age recovery key.
set -eu
umask 077
: "${AGE_RECIPIENT:?Set the public age backup recipient}"
: "${BACKUP_DIRECTORY:?Set the absolute backup destination directory}"
case "$BACKUP_DIRECTORY" in /*) ;; *) echo "Absolute backup directory required" >&2; exit 1;; esac
mkdir -p "$BACKUP_DIRECTORY"
temporary=$(mktemp -d)
trap 'rm -f "$temporary/database.dump" "$temporary/backup.age"; rmdir "$temporary"' EXIT HUP INT TERM
pg_dump --format=custom --file="$temporary/database.dump"
age --recipient "$AGE_RECIPIENT" --output "$temporary/backup.age" "$temporary/database.dump"
name="remotedesk-$(date -u +%Y%m%dT%H%M%SZ)-$$.dump.age"
install -m 0600 "$temporary/backup.age" "$BACKUP_DIRECTORY/$name"
printf '%s\n' "$BACKUP_DIRECTORY/$name"
