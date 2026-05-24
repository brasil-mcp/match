"""Postgres connection pool e helpers.

A connection string vem de `BRASIL_MCP_MATCH_DATABASE_URL`. Default aponta pro
docker-compose local. Em prod, a URL é injetada via env var.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import psycopg
from psycopg.rows import dict_row

_DEFAULT_URL = "postgresql://brasilmcp:dev_password_only@localhost:5432/brasil_mcp_match_server"


def get_database_url() -> str:
    return os.environ.get("BRASIL_MCP_MATCH_DATABASE_URL", _DEFAULT_URL)


@contextmanager
def connect() -> Iterator[psycopg.Connection[Any]]:
    """Open a psycopg connection with dict_row factory. Caller manages commit/rollback."""
    with psycopg.connect(get_database_url(), row_factory=dict_row) as conn:  # pyright: ignore[reportArgumentType]
        yield conn
