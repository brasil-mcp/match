"""Downloader dos dumps mensais da Receita Federal.

A RF publica em duas modalidades:
- Legacy (≤ 2025): HTTP directory listing em
  ``https://arquivos.receitafederal.gov.br/dados/cnpj/dados_abertos_cnpj/<YYYY-MM>/``.
- 2026+: Nextcloud public share (WebDAV) em
  ``https://arquivos.receitafederal.gov.br/index.php/s/<token>``.

Conteúdo: ~30 zips totalizando ~5 GB comprimido (~30 GB descomprimido).

Estratégia:
- ``resolve_base_url`` descobre qual URL canônica responde (env override → chain).
- Se for Nextcloud share, usa PROPFIND no public WebDAV (``/public.php/webdav/``)
  com Basic Auth — share token como username, senha vazia. Caso contrário, faz
  parsing leve do HTML listing.
- Para cada arquivo .zip, baixa via streaming pra ``data/rf-cache/<YYYY-MM>/``,
  reaproveitando a mesma auth (quando aplicável).
- Calcula sha256 progressivo.
- Pula arquivos já baixados com tamanho conhecido (idempotência).

Não descompacta — descompactação é parte do parser, que faz streaming.
"""

from __future__ import annotations

import hashlib
import logging
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urljoin

import httpx

_LOG = logging.getLogger(__name__)

# Receita Federal moveu hosting da base CNPJ múltiplas vezes (2023 → 2024 →
# 2025 → 2026). Em 2026 a hospedagem oficial é via Nextcloud share link.
# Override via env var `BRASIL_MCP_MATCH_RF_BASE_URL` quando o token mudar.
_BASE_URL_CANDIDATES = (
    # 2026 — Nextcloud share oficial RF (verificado em 2026-05-22)
    "https://arquivos.receitafederal.gov.br/index.php/s/YggdBLfdninEJX9",
    # 2025 (legacy direct listing — pode voltar a responder)
    "https://arquivos.receitafederal.gov.br/dados/cnpj/dados_abertos_cnpj/",
    "https://arquivos.receitafederal.gov.br/CNPJ/",
    # Pré-2024 (último recurso)
    "https://dadosabertos.rfb.gov.br/CNPJ/",
    "http://200.152.38.155/CNPJ/",
)

# Padrão de share público Nextcloud: <scheme>://<host>/index.php/s/<token>[/]
_NEXTCLOUD_SHARE_RE = re.compile(r"^(https?://[^/]+)/index\.php/s/([^/]+)/?$")
_DAV_NS = "{DAV:}"


def resolve_base_url(client: httpx.Client | None = None) -> str:
    """Discover the current canonical RF base URL by probing the candidate chain.

    Honors `BRASIL_MCP_MATCH_RF_BASE_URL` env var override. Returns the first
    URL that responds with a 2xx HEAD. Raises `RuntimeError` if none respond.
    """
    import os

    override = os.environ.get("BRASIL_MCP_MATCH_RF_BASE_URL")
    if override:
        return override.rstrip("/") + "/"

    own_client = client is None
    if client is None:
        client = httpx.Client(timeout=15.0, follow_redirects=True)
    try:
        for url in _BASE_URL_CANDIDATES:
            try:
                resp = client.head(url)
                if 200 <= resp.status_code < 300:
                    return url
            except httpx.HTTPError:
                continue
        raise RuntimeError(
            "Nenhuma URL base da Receita Federal respondeu. Defina "
            "BRASIL_MCP_MATCH_RF_BASE_URL manualmente com a URL canônica atual."
        )
    finally:
        if own_client:
            client.close()


# Lazy resolution — só faz HTTP quando uma operação real é chamada.
BASE_URL = _BASE_URL_CANDIDATES[0]  # default; overridden by resolve_base_url() at runtime
_HREF_RE = re.compile(r'href="([^"]+\.zip)"', re.IGNORECASE)


@dataclass(frozen=True, slots=True)
class RemoteFile:
    name: str
    url: str
    size_bytes: int | None
    last_modified: str | None  # HTTP date header verbatim
    # Credentials a serem aplicadas no GET (Basic Auth tupla (user, pass)).
    # Usado pelo transport Nextcloud public-share — o token vira o username.
    auth: tuple[str, str] | None = None


@dataclass(frozen=True, slots=True)
class DownloadedFile:
    name: str
    local_path: Path
    size_bytes: int
    sha256: str


def _parse_nextcloud_share(base_url: str) -> tuple[str, str, str] | None:
    """Detect a Nextcloud public share URL.

    Returns ``(host_origin, webdav_root, token)`` or ``None`` if not a share.

    - ``host_origin`` = scheme + authority (sem path), p/ remontar URLs absolutas.
    - ``webdav_root`` = ``<host_origin>/public.php/webdav/`` (com trailing slash).
    - ``token`` = share token, usado como username em Basic Auth (senha vazia).
    """
    m = _NEXTCLOUD_SHARE_RE.match(base_url.rstrip("/"))
    if not m:
        return None
    host_origin = m.group(1)
    token = m.group(2)
    return host_origin, f"{host_origin}/public.php/webdav/", token


def _list_release_nextcloud(
    client: httpx.Client,
    host_origin: str,
    webdav_root: str,
    token: str,
    release: str,
) -> list[RemoteFile]:
    """List a release directory via Nextcloud public WebDAV (PROPFIND Depth: 1)."""
    release_url = urljoin(webdav_root, f"{release}/")
    auth = (token, "")
    resp = client.request(
        "PROPFIND",
        release_url,
        auth=auth,
        headers={"Depth": "1"},
    )
    resp.raise_for_status()
    root = ET.fromstring(resp.text)
    out: list[RemoteFile] = []
    for response in root.findall(f"{_DAV_NS}response"):
        href_el = response.find(f"{_DAV_NS}href")
        if href_el is None or not href_el.text:
            continue
        href = href_el.text
        if not href.lower().endswith(".zip"):
            continue
        name = href.rstrip("/").rsplit("/", 1)[-1]
        size_el = response.find(f".//{_DAV_NS}getcontentlength")
        lastmod_el = response.find(f".//{_DAV_NS}getlastmodified")
        size_bytes = int(size_el.text) if size_el is not None and size_el.text else None
        last_modified = lastmod_el.text if lastmod_el is not None else None
        out.append(
            RemoteFile(
                name=name,
                url=host_origin + href,
                size_bytes=size_bytes,
                last_modified=last_modified,
                auth=auth,
            )
        )
    return sorted(out, key=lambda f: f.name)


def list_release(release: str) -> list[RemoteFile]:
    """List zip files in a given RF release (e.g., "2026-04").

    Auto-detecta o transport: Nextcloud public share (WebDAV PROPFIND) ou
    HTTP directory listing legacy.
    """
    with httpx.Client(timeout=30.0, follow_redirects=True) as client:
        base = resolve_base_url(client)
        nc = _parse_nextcloud_share(base)
        if nc is not None:
            host_origin, webdav_root, token = nc
            return _list_release_nextcloud(client, host_origin, webdav_root, token, release)
        release_url = urljoin(base, f"{release}/")
        resp = client.get(release_url)
        resp.raise_for_status()
        zip_names = sorted(set(_HREF_RE.findall(resp.text)))
        out: list[RemoteFile] = []
        for name in zip_names:
            file_url = urljoin(release_url, name)
            head = client.head(file_url)
            head.raise_for_status()
            size = head.headers.get("content-length")
            out.append(
                RemoteFile(
                    name=name,
                    url=file_url,
                    size_bytes=int(size) if size else None,
                    last_modified=head.headers.get("last-modified"),
                )
            )
        return out


def download_file(remote: RemoteFile, dest_dir: Path) -> DownloadedFile:
    """Download a single zip via streaming. Computes sha256 as it writes.

    If a file with the same name + size already exists at dest, skips and just
    recomputes the sha256 to confirm integrity.
    """
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest_path = dest_dir / remote.name

    if dest_path.exists() and remote.size_bytes and dest_path.stat().st_size == remote.size_bytes:
        _LOG.info("skipping %s (already present, size matches)", remote.name)
        return DownloadedFile(
            name=remote.name,
            local_path=dest_path,
            size_bytes=dest_path.stat().st_size,
            sha256=_sha256_file(dest_path),
        )

    hasher = hashlib.sha256()
    bytes_written = 0
    tmp_path = dest_path.with_suffix(dest_path.suffix + ".tmp")
    with httpx.stream(
        "GET",
        remote.url,
        timeout=300.0,
        follow_redirects=True,
        auth=remote.auth,
    ) as resp:
        resp.raise_for_status()
        with tmp_path.open("wb") as fh:
            for chunk in resp.iter_bytes(chunk_size=1 << 20):
                fh.write(chunk)
                hasher.update(chunk)
                bytes_written += len(chunk)
    tmp_path.rename(dest_path)
    return DownloadedFile(
        name=remote.name,
        local_path=dest_path,
        size_bytes=bytes_written,
        sha256=hasher.hexdigest(),
    )


def download_release(release: str, dest_root: Path) -> list[DownloadedFile]:
    """Download all zip files of a release into ``<dest_root>/<release>/``."""
    files = list_release(release)
    dest = dest_root / release
    out: list[DownloadedFile] = []
    for i, f in enumerate(files, 1):
        _LOG.info("[%d/%d] downloading %s (%s bytes)", i, len(files), f.name, f.size_bytes)
        out.append(download_file(f, dest))
    return out


def _sha256_file(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as fh:
        while True:
            chunk = fh.read(1 << 20)
            if not chunk:
                break
            hasher.update(chunk)
    return hasher.hexdigest()
