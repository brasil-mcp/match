"""Downloader dos dumps mensais da Receita Federal.

A RF publica em https://arquivos.receitafederal.gov.br/dados/cnpj/dados_abertos_cnpj/<YYYY-MM>/.
Conteúdo: ~30 zips totalizando ~5 GB comprimido (~30 GB descomprimido).

Estratégia:
- Lista o diretório HTTP da release (parsing leve do HTML).
- Para cada arquivo .zip, baixa via streaming pra ``data/rf-cache/<YYYY-MM>/``.
- Calcula sha256 progressivo.
- Pula arquivos já baixados com hash conhecido (idempotência).
- Compara `Last-Modified` no HEAD pra detectar mudança caso a RF refaça o release.

Não descompacta — descompactação é parte do parser, que faz streaming.
"""

from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urljoin

import httpx

_LOG = logging.getLogger(__name__)

# Receita Federal moveu hosting da base CNPJ múltiplas vezes (2023 → 2024 →
# 2025 → 2026). Tentamos uma chain de URLs históricas conhecidas. O primeiro
# que retornar 200 (ou listing válido) ganha. Override via env var
# `BRASIL_MCP_MATCH_RF_BASE_URL`.
_BASE_URL_CANDIDATES = (
    # 2025-2026 (mais comum em projetos ativos como br-acc, sinarc, libercapital)
    "https://arquivos.receitafederal.gov.br/dados/cnpj/dados_abertos_cnpj/",
    "https://arquivos.receitafederal.gov.br/CNPJ/",
    # Pré-2024 (legacy, ainda às vezes responde)
    "https://dadosabertos.rfb.gov.br/CNPJ/",
    "http://200.152.38.155/CNPJ/",
)


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


@dataclass(frozen=True, slots=True)
class DownloadedFile:
    name: str
    local_path: Path
    size_bytes: int
    sha256: str


def list_release(release: str) -> list[RemoteFile]:
    """List zip files in a given RF release (e.g., "2026-04")."""
    with httpx.Client(timeout=30.0, follow_redirects=True) as client:
        base = resolve_base_url(client)
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
    with httpx.stream("GET", remote.url, timeout=300.0, follow_redirects=True) as resp:
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
