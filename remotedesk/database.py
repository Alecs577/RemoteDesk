"""Small DB adapter: SQLite for local tests, PostgreSQL for deployed services."""
from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path


class Database:
    def __init__(self, url: str):
        self.url = url
        self.postgres = url.startswith(("postgresql://", "postgres://"))
        if not self.postgres:
            path = Path(url)
            path.parent.mkdir(parents=True, exist_ok=True)
            fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
            os.close(fd)

    @contextmanager
    def connect(self):
        if self.postgres:
            import psycopg
            from psycopg.rows import dict_row
            connection = psycopg.connect(self.url, row_factory=dict_row, connect_timeout=5,
                                         options="-c statement_timeout=5000 -c lock_timeout=5000")
        else:
            connection = sqlite3.connect(self.url, timeout=5)
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys=ON")
        try:
            if not self.postgres:
                connection.execute("BEGIN IMMEDIATE")
            yield Session(connection, self.postgres)
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()


class Session:
    def __init__(self, connection, postgres: bool):
        self.connection, self.postgres = connection, postgres

    def execute(self, sql: str, args=()):
        return self.connection.execute(sql.replace("?", "%s") if self.postgres else sql, args)
