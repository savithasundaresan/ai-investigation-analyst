"""Read-only access to the built database."""

import duckdb

from src.config import DB_PATH


def connect() -> duckdb.DuckDBPyConnection:
    if not DB_PATH.exists():
        raise FileNotFoundError(f"{DB_PATH} not found; run `uv run python -m src.load` first")
    return duckdb.connect(str(DB_PATH), read_only=True)
