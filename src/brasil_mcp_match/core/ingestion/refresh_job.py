"""Refresh job — entry point pro cron mensal.

Sequência:
1. Descobre release alvo (default: mês anterior, formato YYYY-MM).
2. Lista arquivos disponíveis.
3. Baixa todos (idempotente).
4. Registra início no manifest.
5. (futuro) Parse + load.
6. Marca success ou failure.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from datetime import date
from pathlib import Path

from brasil_mcp_match.core.ingestion.downloader import download_release
from brasil_mcp_match.core.ingestion.manifest import (
    complete_run,
    fail_run,
    start_run,
)

_LOG = logging.getLogger(__name__)


def previous_month_release(today: date | None = None) -> str:
    today = today or date.today()
    year, month = today.year, today.month - 1
    if month == 0:
        year, month = year - 1, 12
    return f"{year:04d}-{month:02d}"


def cache_root() -> Path:
    return Path(os.environ.get("BRASIL_MCP_MATCH_RF_CACHE", "./data/rf-cache"))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Refresh RF base into Postgres.")
    parser.add_argument(
        "--release",
        default=None,
        help="RF release in YYYY-MM format. Defaults to previous month.",
    )
    parser.add_argument(
        "--download-only",
        action="store_true",
        help="Only download zips; skip parse+load. Useful for first runs / staging.",
    )
    args = parser.parse_args(argv)

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

        # TODO: parse + load — implemented in next sprint.
        raise NotImplementedError("parse + load not yet implemented. Use --download-only for now.")

    except Exception as exc:  # pragma: no cover - top-level handler
        _LOG.exception("ingestion failed")
        fail_run(run_id, str(exc))
        return 1


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
