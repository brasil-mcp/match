"""Refresh job — entry point pro cron mensal.

Sequência:
1. Descobre release alvo (default: mês anterior, formato YYYY-MM).
2. Lista arquivos disponíveis.
3. Baixa todos (idempotente).
4. Registra início no manifest.
5. Parse + load (COPY via staging + ON CONFLICT, ver loader.py).
6. Marca success ou failure.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from datetime import date
from pathlib import Path

from brasil_mcp_match.core.ingestion import loader, parser
from brasil_mcp_match.core.ingestion.downloader import download_release
from brasil_mcp_match.core.ingestion.manifest import (
    complete_run,
    fail_run,
    start_run,
)
from brasil_mcp_match.core.repository.connection import connect

_LOG = logging.getLogger(__name__)


def previous_month_release(today: date | None = None) -> str:
    today = today or date.today()
    year, month = today.year, today.month - 1
    if month == 0:
        year, month = year - 1, 12
    return f"{year:04d}-{month:02d}"


def cache_root() -> Path:
    return Path(os.environ.get("BRASIL_MCP_MATCH_RF_CACHE", "./data/rf-cache"))


def _load_zip(zip_path: Path, *, skip_refs: bool) -> int:
    """Open one downloaded zip, route each inner file through the loader.

    Returns the number of rows inserted across the entire zip.
    """
    import zipfile

    rows_total = 0
    with zipfile.ZipFile(zip_path) as zf:
        names = zf.namelist()

    for inner in names:
        info = parser.detect_file_type(inner)
        if info.type == parser.FileType.UNKNOWN:
            _LOG.info("skipping %s in %s (unknown type)", inner, zip_path.name)
            continue
        if skip_refs and info.type in loader.REF_FILETYPES:
            _LOG.info("skipping %s in %s (--skip-refs)", inner, zip_path.name)
            continue

        _LOG.info("loading %s (type=%s)", inner, info.type)
        rows_iter = parser.parse_zip(zip_path)
        with connect() as conn:
            inserted = loader.load_by_filetype(info.type, rows_iter, conn)
            conn.commit()
        rows_total += inserted
        _LOG.info("  inserted %d rows", inserted)
    return rows_total


def main(argv: list[str] | None = None) -> int:
    arg_parser = argparse.ArgumentParser(description="Refresh RF base into Postgres.")
    arg_parser.add_argument(
        "--release",
        default=None,
        help="RF release in YYYY-MM format. Defaults to previous month.",
    )
    arg_parser.add_argument(
        "--download-only",
        action="store_true",
        help="Only download zips; skip parse+load. Useful for first runs / staging.",
    )
    arg_parser.add_argument(
        "--skip-refs",
        action="store_true",
        help="Skip reference tables (ref_*). Useful for incremental loads.",
    )
    args = arg_parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    release = args.release or previous_month_release()
    rf_release_db = release.replace("-", "")  # YYYYMM for the manifest table

    _LOG.info("starting ingestion for release %s", release)
    run_id = start_run(rf_release_db)

    try:
        files = download_release(release, cache_root())
        total_size = sum(f.size_bytes for f in files)
        _LOG.info("downloaded %d files, %d bytes total", len(files), total_size)

        if args.download_only:
            complete_run(run_id, file_count=len(files), row_count=0)
            _LOG.info("download-only run completed (manifest marked success)")
            return 0

        total_rows = 0
        for f in files:
            total_rows += _load_zip(f.local_path, skip_refs=args.skip_refs)

        complete_run(run_id, file_count=len(files), row_count=total_rows)
        _LOG.info("ingestion completed: %d files, %d rows", len(files), total_rows)
        return 0

    except Exception as exc:  # pragma: no cover - top-level handler
        _LOG.exception("ingestion failed")
        fail_run(run_id, str(exc))
        return 1


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
