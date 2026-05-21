"""Tests for ingestion.refresh_job — orchestrator with all heavy deps mocked."""

from __future__ import annotations

import zipfile
from contextlib import contextmanager
from datetime import date
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from brasil_mcp_match.core.ingestion import refresh_job as refresh_mod
from brasil_mcp_match.core.ingestion.downloader import DownloadedFile
from brasil_mcp_match.core.ingestion.parser import FileType
from brasil_mcp_match.core.ingestion.refresh_job import (
    cache_root,
    main,
    previous_month_release,
)

# ------------ previous_month_release ------------


@pytest.mark.parametrize(
    "today,expected",
    [
        (date(2026, 5, 21), "2026-04"),
        (date(2026, 1, 5), "2025-12"),  # rollover
        (date(2026, 12, 31), "2026-11"),
        (date(2020, 3, 1), "2020-02"),
    ],
)
def test_previous_month_release(today: date, expected: str) -> None:
    assert previous_month_release(today) == expected


def test_previous_month_release_default_uses_today() -> None:
    """No argument: uses date.today(). Just verify shape."""
    r = previous_month_release()
    assert len(r) == 7
    assert r[4] == "-"


# ------------ cache_root ------------


def test_cache_root_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("BRASIL_MCP_MATCH_RF_CACHE", raising=False)
    assert cache_root() == Path("./data/rf-cache")


def test_cache_root_env_override(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("BRASIL_MCP_MATCH_RF_CACHE", str(tmp_path / "cache"))
    assert cache_root() == tmp_path / "cache"


# ------------ main() — happy path with download + load mocked ------------


@pytest.fixture
def patched_main(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> dict[str, Any]:
    """Wire up monkeypatches for main(); return a dict of tracking hooks."""
    state: dict[str, Any] = {
        "started": [],
        "completed": [],
        "failed": [],
        "downloaded": [],
        "load_zips": [],
    }

    def fake_start(rf_release: str) -> int:
        state["started"].append(rf_release)
        return 42

    def fake_complete(run_id: int, file_count: int, row_count: int) -> None:
        state["completed"].append((run_id, file_count, row_count))

    def fake_fail(run_id: int, error: str) -> None:
        state["failed"].append((run_id, error))

    def fake_download(release: str, root: Path) -> list[DownloadedFile]:
        # Create stub zip on disk so the load step can open it
        zip_path = tmp_path / "stub.zip"
        with zipfile.ZipFile(zip_path, "w") as zf:
            zf.writestr("K3241.K03200Y0.D40510.EMPRECSV", b'"1";"X";"1";"1";"0";"01";""\n')
        df = DownloadedFile(name="stub.zip", local_path=zip_path, size_bytes=128, sha256="x" * 64)
        state["downloaded"].append((release, root, df))
        return [df]

    monkeypatch.setattr(refresh_mod, "start_run", fake_start)
    monkeypatch.setattr(refresh_mod, "complete_run", fake_complete)
    monkeypatch.setattr(refresh_mod, "fail_run", fake_fail)
    monkeypatch.setattr(refresh_mod, "download_release", fake_download)

    # _load_zip patches loader + connection inside the module — patch the
    # imports used inside `_load_zip` directly.
    fake_conn = MagicMock(name="conn")

    @contextmanager
    def fake_connect() -> Any:
        yield fake_conn

    monkeypatch.setattr(refresh_mod, "connect", fake_connect)

    def fake_load_by_filetype(file_type: FileType, rows: Any, conn: Any) -> int:
        state["load_zips"].append(file_type)
        # Force-drain the iterator so the parser is exercised.
        list(rows)
        return 1

    monkeypatch.setattr(refresh_mod.loader, "load_by_filetype", fake_load_by_filetype)

    # Point cache root at tmp dir.
    monkeypatch.setenv("BRASIL_MCP_MATCH_RF_CACHE", str(tmp_path))
    return state


def test_main_happy_path_default_release(
    patched_main: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """No --release flag: uses previous_month_release()."""
    monkeypatch.setattr(refresh_mod, "previous_month_release", lambda: "2026-04")
    rc = main([])
    assert rc == 0
    assert patched_main["started"] == ["202604"]
    assert len(patched_main["completed"]) == 1
    run_id, file_count, row_count = patched_main["completed"][0]
    assert run_id == 42
    assert file_count == 1
    assert row_count >= 1  # at least 1 row from the stub
    assert patched_main["failed"] == []


def test_main_explicit_release(patched_main: dict[str, Any]) -> None:
    rc = main(["--release", "2025-12"])
    assert rc == 0
    assert patched_main["started"] == ["202512"]
    assert patched_main["downloaded"][0][0] == "2025-12"


def test_main_download_only_skips_load(patched_main: dict[str, Any]) -> None:
    rc = main(["--release", "2026-04", "--download-only"])
    assert rc == 0
    # Load step not invoked.
    assert patched_main["load_zips"] == []
    # Manifest still marked success with row_count=0.
    assert patched_main["completed"][0][2] == 0


def test_main_skip_refs_skips_ref_filetypes(
    patched_main: dict[str, Any], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """--skip-refs: REF_* filetypes inside zips are skipped."""

    # Replace the download to return a zip containing only a REF file.
    def fake_download(release: str, root: Path) -> list[DownloadedFile]:
        zip_path = tmp_path / "refs.zip"
        with zipfile.ZipFile(zip_path, "w") as zf:
            zf.writestr("F.K03200$Z.D40510.CNAECSV", b'"1";"X"\n')
        return [DownloadedFile(name="refs.zip", local_path=zip_path, size_bytes=8, sha256="y" * 64)]

    monkeypatch.setattr(refresh_mod, "download_release", fake_download)
    rc = main(["--release", "2026-04", "--skip-refs"])
    assert rc == 0
    assert patched_main["load_zips"] == []  # CNAECSV is REF — skipped


def test_main_unknown_file_skipped(
    patched_main: dict[str, Any], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Inner files of UNKNOWN type are skipped silently."""

    def fake_download(release: str, root: Path) -> list[DownloadedFile]:
        zip_path = tmp_path / "junk.zip"
        with zipfile.ZipFile(zip_path, "w") as zf:
            zf.writestr("readme.txt", b"not a csv")
        return [DownloadedFile(name="junk.zip", local_path=zip_path, size_bytes=8, sha256="z" * 64)]

    monkeypatch.setattr(refresh_mod, "download_release", fake_download)
    rc = main(["--release", "2026-04"])
    assert rc == 0
    assert patched_main["load_zips"] == []


def test_main_failure_marks_fail_run(
    patched_main: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Exception during download → fail_run is called + rc=1."""

    def boom(release: str, root: Path) -> list[DownloadedFile]:
        raise RuntimeError("disk on fire")

    monkeypatch.setattr(refresh_mod, "download_release", boom)
    rc = main(["--release", "2026-04"])
    assert rc == 1
    assert len(patched_main["failed"]) == 1
    run_id, msg = patched_main["failed"][0]
    assert run_id == 42
    assert "disk on fire" in msg
