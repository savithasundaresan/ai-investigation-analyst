"""Build the DuckDB database from the pinned public mirror.

    uv run python -m src.load            # fetch, verify, load raw, build all layers
    uv run python -m src.load --sql-only # rebuild stg/int/marts from existing raw tables

Each CSV is fetched into memory, checked against its pinned SHA-256, and
written to a raw_ table with every column kept as text. Nothing is cast or
cleaned in the raw layer, so it stays an exact copy of the source. All typing
and cleaning happens in sql/, which runs in file-name order.
"""

import argparse
import hashlib
import sys
import tempfile
import urllib.request
from pathlib import Path

import duckdb

from src.config import DB_PATH, SOURCE_FILES, SOURCE_URL, SQL_DIR


def fetch_verified(file_name: str, expected_sha256: str) -> bytes:
    with urllib.request.urlopen(f"{SOURCE_URL}/{file_name}", timeout=120) as resp:
        data = resp.read()
    actual = hashlib.sha256(data).hexdigest()
    if actual != expected_sha256:
        raise RuntimeError(f"{file_name}: checksum mismatch (got {actual[:12]}…, expected {expected_sha256[:12]}…)")
    return data


def load_raw(con: duckdb.DuckDBPyConnection) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        for table, (file_name, sha) in SOURCE_FILES.items():
            path = Path(tmp) / file_name
            path.write_bytes(fetch_verified(file_name, sha))
            con.execute(
                f"CREATE OR REPLACE TABLE {table} AS "
                "SELECT * FROM read_csv(?, header = true, all_varchar = true)",
                [str(path)],
            )
            n = con.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
            print(f"  {table:<26} {n:>8,} rows  (checksum ok)")


def build_models(con: duckdb.DuckDBPyConnection) -> None:
    for sql_file in sorted(SQL_DIR.glob("*.sql")):
        con.execute(sql_file.read_text())
        print(f"  ran {sql_file.name}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--sql-only", action="store_true", help="skip fetching; rebuild from existing raw tables")
    args = parser.parse_args()

    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with duckdb.connect(str(DB_PATH)) as con:
        if not args.sql_only:
            print("Loading raw tables from the pinned mirror")
            load_raw(con)
        print("Building staging, intermediate and mart tables")
        build_models(con)
    print(f"Done: {DB_PATH}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
