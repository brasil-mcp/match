# Changelog — brasil-mcp-match-server

All notable changes to the server side (this `server/` subdirectory) will be
documented in this file. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project
adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

> The server is **not** published to PyPI. Self-hosters track this CHANGELOG
> against the git tags on the `brasil-mcp/match` repo.

## [Unreleased]

## [0.3.0] - 2026-05-24

### Changed (BREAKING for self-hosters tracking the old layout)

- Package renamed `brasil_mcp_match` → `brasil_mcp_match_server`. All
  internal imports updated. End users consuming the REST API see **no**
  change.
- Console scripts renamed (the unprefixed `brasil-mcp-match` name now
  belongs to the PyPI client):
  - `brasil-mcp-match` → `brasil-mcp-match-server-rest`
  - `brasil-mcp-match-server` → `brasil-mcp-match-server-stdio`
  - `brasil-mcp-match-ingest` → `brasil-mcp-match-server-ingest`
- Source layout moved from repo root to `server/` subdirectory. The repo
  root now holds the MIT client. See `../CHANGELOG.md` for the rationale.

### Added

- `POST /v1/internal/empresa` endpoint returning the full `EmpresaRecord`
  for trusted sister services (`brasil-mcp-leads`, etc.). Gated by
  `X-Internal-Service-Token` header against the
  `MATCH_INTERNAL_SERVICE_TOKEN` env var. Replaces the in-process
  `lookup_cnpj()` helper that used to live in the PyPI package.

## [0.2.1] - 2026-05-24

### Added

- **MCP stdio transport** wired in `brasil-mcp-match-server` console script.
  Single-user dev mode reads `BRASIL_MCP_MATCH_DATABASE_URL`, opens a
  long-lived connection, and serves the 4 v0.1.0 tools over stdio.
- `build_stdio_context(repo, base_updated_at)` helper exposes the synthetic
  single-user `RequestContext` (plan=ENTERPRISE, key_hash=`stdio-local-dev`)
  so tests and downstream wrappers can reuse it.

## [0.2.0] - 2026-05-22

### Added

- `brasil_mcp_match.lookup_cnpj()` top-level public API for sister services.
  (Removed in v0.3.0 from the published PyPI client; the equivalent server
  endpoint is `POST /v1/internal/empresa`.)

## [0.1.1] - 2026-05-22

### Changed

- Ingest excludes MEI and non-`ativa` CNPJs by design. Those return
  `CNPJ_NOT_FOUND` on the tools.

## [0.1.0] - 2026-05-21

First public release of `brasil-mcp-match` — Fase 2 of Brasil MCP.

### Added

- **Core matching tools** (privacy-preserving):
  - `match_razao_social` — fuzzy match against RF-registered razão social
    (exact / fuzzy_prefix / fuzzy_word / fuzzy_phonetic, configurable tolerance).
  - `check_situacao_cadastral` — returns `ativa` / `suspensa` / `inapta` /
    `baixada` / `nula` / `desconhecida` + the since-date.
  - `check_porte_empresa` — returns `MEI` / `ME` / `EPP` / `DEMAIS` + Simples
    Nacional / MEI booleans.
  - `match_uf` — boolean match against RF-registered UF.
- **Adapters:**
  - REST API via FastAPI (`brasil-mcp-match serve`).
  - MCP server via FastMCP (4 tools registered).
- **Ingestion pipeline:**
  - Downloader resilient to RF base-URL changes (probes a chain, env override
    via `BRASIL_MCP_MATCH_RF_BASE_URL`).
  - Streaming parser for RF CSVs (latin-1, semicolon-separated, schemas for
    Empresa / Estabelecimento / Socio / Simples / 6 ref tables).
  - Postgres loader via `COPY FROM STDIN` + staging temp tables.
  - Manifest tracking per ingestion run.
  - Monthly refresh job (`brasil-mcp-match-ingest`).
- **Auth & quota:**
  - API key generation + sha256 hashing.
  - Per-plan monthly quota (Free / Starter / Pro / Enterprise).
- **LGPD:**
  - Append-only audit log (hashed key + hashed input + summary only).
  - `POST /v1/opt-out/{cnpj}` (Art. 18) with 15-business-day delay.
  - `GET /v1/audit/{query_id}` with per-key RBAC (404 on cross-key access).
  - Privacy assertions: razão social, capital social, full address, full CPF
    never present in any output.
- **Rate limiting:**
  - Per-key sliding window via slowapi (default 120/min).
- **Errors:**
  - Structured `ErrorObj` with PT/EN messages.
- **Tests:**
  - 584 tests; 100% line + branch coverage.
  - 240+ adversarial-input tests across all public surfaces.
  - SQL-injection-resistance suite (psycopg parameterization).
  - RBAC, rate-limit, opt-out isolation suites.
- **Docs:**
  - `README.md` — install, quick start, tool table, MCP config, privacy stance.
  - `docs/tools.md` — full API reference (input/output/cURL/errors).
  - `docs/lgpd/LIA.md` — Legitimate Interest Assessment template.
  - `docs/lgpd/DPA-template.md` — Data Processing Agreement template.
- **CI/CD:**
  - GitHub Actions matrix (Python 3.11 / 3.12 / 3.13) with 100% coverage gate.
  - PyPI release workflow (OIDC trusted publishing) on `v*` tags.

### Notes

- Distributed under **AGPL-3.0-or-later**. Commercial self-host without source
  release obligations requires a separate license.
- The ingestion job downloads ~5 GB of RF dumps for a full release. First-time
  ingest takes ~30 min on a modest VPS.
- Postgres 16+ is required (uses `MERGE`, `JSONB`, `GIN` tri-gram).
