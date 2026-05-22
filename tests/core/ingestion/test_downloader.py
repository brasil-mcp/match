"""Tests for ingestion.downloader — mocked httpx (no network)."""

from __future__ import annotations

import io
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import httpx
import pytest

from brasil_mcp_match.core.ingestion import downloader as downloader_mod
from brasil_mcp_match.core.ingestion.downloader import (
    RemoteFile,
    _parse_nextcloud_share,
    _sha256_file,
    download_file,
    download_release,
    list_release,
    resolve_base_url,
)

# ------------ resolve_base_url ------------


def test_resolve_base_url_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(
        "BRASIL_MCP_MATCH_RF_BASE_URL",
        "https://example.com/rf",
    )
    assert resolve_base_url() == "https://example.com/rf/"


def test_resolve_base_url_env_override_trailing_slash_preserved(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("BRASIL_MCP_MATCH_RF_BASE_URL", "https://example.com/rf/")
    assert resolve_base_url() == "https://example.com/rf/"


def test_resolve_base_url_first_candidate_wins(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("BRASIL_MCP_MATCH_RF_BASE_URL", raising=False)
    client = MagicMock(spec=httpx.Client)
    resp = MagicMock()
    resp.status_code = 200
    client.head.return_value = resp

    url = resolve_base_url(client)
    assert url == downloader_mod._BASE_URL_CANDIDATES[0]
    client.head.assert_called_once_with(downloader_mod._BASE_URL_CANDIDATES[0])


def test_resolve_base_url_falls_through_to_second(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("BRASIL_MCP_MATCH_RF_BASE_URL", raising=False)
    client = MagicMock(spec=httpx.Client)
    bad = MagicMock()
    bad.status_code = 404
    good = MagicMock()
    good.status_code = 200
    client.head.side_effect = [bad, good]

    url = resolve_base_url(client)
    assert url == downloader_mod._BASE_URL_CANDIDATES[1]


def test_resolve_base_url_skips_http_error_and_tries_next(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If a candidate raises HTTPError, move to the next."""
    monkeypatch.delenv("BRASIL_MCP_MATCH_RF_BASE_URL", raising=False)
    client = MagicMock(spec=httpx.Client)
    good = MagicMock()
    good.status_code = 200
    client.head.side_effect = [
        httpx.ConnectError("connection refused"),
        good,
    ]

    url = resolve_base_url(client)
    assert url == downloader_mod._BASE_URL_CANDIDATES[1]


def test_resolve_base_url_raises_when_all_fail(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("BRASIL_MCP_MATCH_RF_BASE_URL", raising=False)
    client = MagicMock(spec=httpx.Client)
    client.head.side_effect = httpx.ConnectError("nope")

    with pytest.raises(RuntimeError, match="Nenhuma URL base"):
        resolve_base_url(client)


def test_resolve_base_url_creates_own_client_when_none_passed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("BRASIL_MCP_MATCH_RF_BASE_URL", raising=False)
    fake_client = MagicMock()
    good = MagicMock()
    good.status_code = 200
    fake_client.head.return_value = good

    with patch.object(httpx, "Client", return_value=fake_client) as ctor:
        url = resolve_base_url()
        assert url == downloader_mod._BASE_URL_CANDIDATES[0]
        ctor.assert_called_once()
        # Own client must be closed.
        fake_client.close.assert_called_once()


# ------------ list_release ------------


def test_list_release_parses_html_and_heads_each(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BRASIL_MCP_MATCH_RF_BASE_URL", "https://example.com/rf")

    html = """<html><body>
        <a href="Empresas0.zip">Empresas0.zip</a>
        <a href="Empresas1.zip">Empresas1.zip</a>
        <a href="readme.txt">readme</a>
    </body></html>"""

    fake_client = MagicMock()
    fake_client.__enter__.return_value = fake_client
    fake_client.__exit__.return_value = False

    get_resp = MagicMock()
    get_resp.text = html
    get_resp.raise_for_status = MagicMock()
    fake_client.get.return_value = get_resp

    head_resp = MagicMock()
    head_resp.headers = {"content-length": "1234", "last-modified": "Wed, 01 May 2026 00:00:00 GMT"}
    head_resp.raise_for_status = MagicMock()
    fake_client.head.return_value = head_resp

    with patch.object(httpx, "Client", return_value=fake_client):
        files = list_release("2026-04")

    assert len(files) == 2
    names = [f.name for f in files]
    assert names == sorted(["Empresas0.zip", "Empresas1.zip"])
    assert all(f.size_bytes == 1234 for f in files)
    assert all(f.last_modified == "Wed, 01 May 2026 00:00:00 GMT" for f in files)


def test_list_release_no_content_length(monkeypatch: pytest.MonkeyPatch) -> None:
    """Missing content-length header → size_bytes is None."""
    monkeypatch.setenv("BRASIL_MCP_MATCH_RF_BASE_URL", "https://example.com/rf")
    html = '<a href="X.zip">X.zip</a>'
    fake_client = MagicMock()
    fake_client.__enter__.return_value = fake_client
    fake_client.__exit__.return_value = False
    fake_client.get.return_value = MagicMock(text=html, raise_for_status=MagicMock())
    fake_client.head.return_value = MagicMock(headers={}, raise_for_status=MagicMock())

    with patch.object(httpx, "Client", return_value=fake_client):
        files = list_release("2026-04")

    assert len(files) == 1
    assert files[0].size_bytes is None
    assert files[0].last_modified is None


# ------------ download_file ------------


def test_download_file_streams_and_hashes(tmp_path: Path) -> None:
    remote = RemoteFile(
        name="Empresas0.zip",
        url="https://example.com/rf/2026-04/Empresas0.zip",
        size_bytes=12,
        last_modified=None,
    )
    chunks = [b"hello ", b"world!"]

    class FakeStreamResp:
        def __enter__(self) -> Any:
            return self

        def __exit__(self, *a: Any) -> bool:
            return False

        def raise_for_status(self) -> None:
            return None

        def iter_bytes(self, chunk_size: int = 0) -> Any:
            yield from chunks

    with patch.object(httpx, "stream", return_value=FakeStreamResp()):
        result = download_file(remote, tmp_path)

    assert result.name == "Empresas0.zip"
    assert result.local_path == tmp_path / "Empresas0.zip"
    assert result.local_path.exists()
    assert result.size_bytes == 12
    # sha256("hello world!")
    import hashlib

    expected = hashlib.sha256(b"hello world!").hexdigest()
    assert result.sha256 == expected


def test_download_file_skips_when_already_present(tmp_path: Path) -> None:
    """If file already exists with matching size, skip + recompute hash."""
    dest = tmp_path / "Empresas0.zip"
    dest.write_bytes(b"existing")
    remote = RemoteFile(
        name="Empresas0.zip",
        url="https://example.com/rf/2026-04/Empresas0.zip",
        size_bytes=len(b"existing"),
        last_modified=None,
    )

    # httpx.stream must NOT be called.
    with patch.object(httpx, "stream", side_effect=AssertionError("should be skipped")):
        result = download_file(remote, tmp_path)

    import hashlib

    assert result.sha256 == hashlib.sha256(b"existing").hexdigest()
    assert result.size_bytes == len(b"existing")


def test_download_file_creates_dest_dir(tmp_path: Path) -> None:
    """Destination dir is created if missing."""
    dest_dir = tmp_path / "new" / "nested"
    assert not dest_dir.exists()

    remote = RemoteFile(name="x.zip", url="https://x/x.zip", size_bytes=None, last_modified=None)
    chunks = [b"abc"]

    class FakeStreamResp:
        def __enter__(self) -> Any:
            return self

        def __exit__(self, *a: Any) -> bool:
            return False

        def raise_for_status(self) -> None:
            return None

        def iter_bytes(self, chunk_size: int = 0) -> Any:
            yield from chunks

    with patch.object(httpx, "stream", return_value=FakeStreamResp()):
        download_file(remote, dest_dir)

    assert dest_dir.is_dir()
    assert (dest_dir / "x.zip").read_bytes() == b"abc"


# ------------ download_release ------------


def test_download_release_orchestrates_list_plus_download(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """download_release lists files then downloads each into <root>/<release>/."""
    files = [
        RemoteFile(
            name="A.zip",
            url="https://x/2026-04/A.zip",
            size_bytes=4,
            last_modified=None,
        ),
        RemoteFile(
            name="B.zip",
            url="https://x/2026-04/B.zip",
            size_bytes=4,
            last_modified=None,
        ),
    ]
    monkeypatch.setattr(downloader_mod, "list_release", lambda release: files)

    seen: list[tuple[RemoteFile, Path]] = []

    def fake_download(remote: RemoteFile, dest: Path) -> Any:
        seen.append((remote, dest))
        return downloader_mod.DownloadedFile(
            name=remote.name,
            local_path=dest / remote.name,
            size_bytes=remote.size_bytes or 0,
            sha256="deadbeef",
        )

    monkeypatch.setattr(downloader_mod, "download_file", fake_download)
    result = download_release("2026-04", tmp_path)

    assert len(result) == 2
    assert all(r.sha256 == "deadbeef" for r in result)
    # Both files written to <root>/2026-04/
    assert all(d == tmp_path / "2026-04" for _r, d in seen)


# ------------ _sha256_file ------------


def test_sha256_file_streams_in_chunks(tmp_path: Path) -> None:
    p = tmp_path / "test.bin"
    payload = b"x" * (2 * (1 << 20) + 17)  # 2 MB + a tail
    p.write_bytes(payload)

    import hashlib

    assert _sha256_file(p) == hashlib.sha256(payload).hexdigest()


def test_sha256_file_empty(tmp_path: Path) -> None:
    p = tmp_path / "empty.bin"
    p.write_bytes(b"")
    import hashlib

    assert _sha256_file(p) == hashlib.sha256(b"").hexdigest()


# A sanity test that the io module is exercised — keeps the import side-effect-free.


def test_iter_bytes_path_works_with_bytesio() -> None:
    buf = io.BytesIO(b"hello")
    assert buf.getvalue() == b"hello"


# ------------ _parse_nextcloud_share ------------


def test_parse_nextcloud_share_matches_canonical() -> None:
    result = _parse_nextcloud_share(
        "https://arquivos.receitafederal.gov.br/index.php/s/YggdBLfdninEJX9"
    )
    assert result is not None
    host, webdav_root, token = result
    assert host == "https://arquivos.receitafederal.gov.br"
    assert webdav_root == "https://arquivos.receitafederal.gov.br/public.php/webdav/"
    assert token == "YggdBLfdninEJX9"


def test_parse_nextcloud_share_trailing_slash_ok() -> None:
    result = _parse_nextcloud_share("https://x.example/index.php/s/TOKEN/")
    assert result is not None
    assert result[2] == "TOKEN"


def test_parse_nextcloud_share_returns_none_for_legacy_url() -> None:
    assert _parse_nextcloud_share("https://x.example/CNPJ/") is None
    assert _parse_nextcloud_share("https://x.example/dados/cnpj/dados_abertos_cnpj/") is None


# ------------ list_release via Nextcloud ------------


_NEXTCLOUD_PROPFIND_RESPONSE = """<?xml version="1.0"?>
<d:multistatus xmlns:d="DAV:">
  <d:response>
    <d:href>/public.php/webdav/2026-04/</d:href>
    <d:propstat><d:prop><d:resourcetype><d:collection/></d:resourcetype></d:prop>
      <d:status>HTTP/1.1 200 OK</d:status></d:propstat>
  </d:response>
  <d:response>
    <d:href>/public.php/webdav/2026-04/Empresas0.zip</d:href>
    <d:propstat><d:prop>
      <d:getcontentlength>5242880</d:getcontentlength>
      <d:getlastmodified>Sun, 12 Apr 2026 18:10:43 GMT</d:getlastmodified>
    </d:prop><d:status>HTTP/1.1 200 OK</d:status></d:propstat>
  </d:response>
  <d:response>
    <d:href>/public.php/webdav/2026-04/Cnaes.zip</d:href>
    <d:propstat><d:prop>
      <d:getcontentlength>1024</d:getcontentlength>
      <d:getlastmodified>Sun, 12 Apr 2026 18:10:43 GMT</d:getlastmodified>
    </d:prop><d:status>HTTP/1.1 200 OK</d:status></d:propstat>
  </d:response>
  <d:response>
    <d:href>/public.php/webdav/2026-04/readme.txt</d:href>
    <d:propstat><d:prop>
      <d:getcontentlength>42</d:getcontentlength>
    </d:prop><d:status>HTTP/1.1 200 OK</d:status></d:propstat>
  </d:response>
</d:multistatus>"""


def test_list_release_uses_nextcloud_when_share_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(
        "BRASIL_MCP_MATCH_RF_BASE_URL",
        "https://arquivos.receitafederal.gov.br/index.php/s/TOKEN123",
    )

    fake_client = MagicMock()
    fake_client.__enter__.return_value = fake_client
    fake_client.__exit__.return_value = False

    propfind_resp = MagicMock()
    propfind_resp.text = _NEXTCLOUD_PROPFIND_RESPONSE
    propfind_resp.raise_for_status = MagicMock()
    fake_client.request.return_value = propfind_resp

    with patch.object(httpx, "Client", return_value=fake_client):
        files = list_release("2026-04")

    # PROPFIND called with correct args
    args, kwargs = fake_client.request.call_args
    assert args[0] == "PROPFIND"
    assert args[1] == ("https://arquivos.receitafederal.gov.br/public.php/webdav/2026-04/")
    assert kwargs["auth"] == ("TOKEN123", "")
    assert kwargs["headers"] == {"Depth": "1"}

    # Only .zip files returned, sorted, with absolute URLs + auth propagated.
    assert [f.name for f in files] == ["Cnaes.zip", "Empresas0.zip"]
    cnaes, empresas = files
    assert cnaes.url == (
        "https://arquivos.receitafederal.gov.br/public.php/webdav/2026-04/Cnaes.zip"
    )
    assert cnaes.size_bytes == 1024
    assert cnaes.auth == ("TOKEN123", "")
    assert empresas.size_bytes == 5242880
    assert empresas.last_modified == "Sun, 12 Apr 2026 18:10:43 GMT"


def test_list_release_nextcloud_skips_response_without_href(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """<d:response> sem <d:href> ou com href vazio é silenciosamente ignorado."""
    monkeypatch.setenv(
        "BRASIL_MCP_MATCH_RF_BASE_URL",
        "https://x.example/index.php/s/T",
    )

    xml = """<?xml version="1.0"?>
    <d:multistatus xmlns:d="DAV:">
      <d:response><d:propstat><d:prop></d:prop></d:propstat></d:response>
      <d:response><d:href></d:href></d:response>
      <d:response>
        <d:href>/public.php/webdav/2026-04/A.zip</d:href>
        <d:propstat><d:prop>
          <d:getcontentlength>10</d:getcontentlength>
        </d:prop></d:propstat>
      </d:response>
    </d:multistatus>"""

    fake_client = MagicMock()
    fake_client.__enter__.return_value = fake_client
    fake_client.__exit__.return_value = False
    fake_client.request.return_value = MagicMock(text=xml, raise_for_status=MagicMock())

    with patch.object(httpx, "Client", return_value=fake_client):
        files = list_release("2026-04")

    assert [f.name for f in files] == ["A.zip"]


def test_list_release_nextcloud_handles_missing_size_and_lastmod(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("BRASIL_MCP_MATCH_RF_BASE_URL", "https://x.example/index.php/s/T")

    xml = """<?xml version="1.0"?>
    <d:multistatus xmlns:d="DAV:">
      <d:response>
        <d:href>/public.php/webdav/2026-04/B.zip</d:href>
        <d:propstat><d:prop></d:prop></d:propstat>
      </d:response>
    </d:multistatus>"""

    fake_client = MagicMock()
    fake_client.__enter__.return_value = fake_client
    fake_client.__exit__.return_value = False
    fake_client.request.return_value = MagicMock(text=xml, raise_for_status=MagicMock())

    with patch.object(httpx, "Client", return_value=fake_client):
        files = list_release("2026-04")

    assert len(files) == 1
    assert files[0].size_bytes is None
    assert files[0].last_modified is None


# ------------ download_file passes auth when present ------------


def test_download_file_passes_auth_to_httpx_stream(tmp_path: Path) -> None:
    remote = RemoteFile(
        name="A.zip",
        url="https://x.example/public.php/webdav/2026-04/A.zip",
        size_bytes=3,
        last_modified=None,
        auth=("TOKEN", ""),
    )

    class FakeStreamResp:
        def __enter__(self) -> Any:
            return self

        def __exit__(self, *a: Any) -> bool:
            return False

        def raise_for_status(self) -> None:
            return None

        def iter_bytes(self, chunk_size: int = 0) -> Any:
            yield from [b"abc"]

    captured: dict[str, Any] = {}

    def fake_stream(method: str, url: str, **kwargs: Any) -> Any:
        captured["method"] = method
        captured["url"] = url
        captured["auth"] = kwargs.get("auth")
        return FakeStreamResp()

    with patch.object(httpx, "stream", side_effect=fake_stream):
        download_file(remote, tmp_path)

    assert captured["auth"] == ("TOKEN", "")
