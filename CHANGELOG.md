# Changelog

All notable changes to `brasil-mcp-match` (the PyPI package shipped from this
repo's root) will be documented here. Format:
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/). Versioning:
[SemVer](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.5.0] - 2026-05-25

### Added

- 5 sócio-verification MCP tools (privacy-preserving, "match don't reveal"):
  - `match_nome_socio_tool(cnpj, nome, tolerance?)` — fuzzy match against all sócios, boolean+hint.
  - `match_cpf_socio_tool(cnpj, cpf)` — verify CPF belongs to a sócio (mask-aware).
  - `match_cnpj_socio_tool(cnpj, cnpj_socio)` — detect holding/cross-ownership structures.
  - `check_qualificacao_socio_tool(cnpj, qualificacao)` — `{exists, count}` for an RF qualificação code.
  - `count_socios_tool(cnpj)` — aggregate counts by PF/PJ/estrangeiro.
- `MatchHttpClient.socio_match_nome()` / `socio_match_cpf()` / `socio_match_cnpj_socio()` /
  `socio_check_qualificacao()` / `socio_count()` low-level methods.
- All sócio tools require `BRASIL_MCP_MATCH_KEY` — same `MISSING_API_KEY` envelope path.

## [0.4.0] - 2026-05-25

### Added

- `request_api_key(email, plan, cpf_cnpj?)` MCP tool — self-service signup.
  Free plan delivers the key inline; paid plans return an Asaas checkout URL.
- `check_signup_status(polling_token)` MCP tool — polls for paid-flow key
  delivery (plaintext-once); server also emails the key via Brevo on paid.
- `MatchHttpClient.signup_start()` and `signup_status()` low-level methods.
- `ClientConfig.has_api_key` property.
- Responses delivering `api_key` plaintext now include a `warning` field
  (`code: STORE_KEY_NOW`) and a `next_steps` line leading with
  `CRITICAL: Store this api_key NOW`. Both fields are designed so the LLM
  consumer and any downstream API treat the key as one-time material — it
  will not be returned again. Server-provided `warning` (when present) is
  preserved as-is; the client only fills it in when absent.

### Changed

- `BRASIL_MCP_MATCH_KEY` is now OPTIONAL. Without it, the 4 verifier tools
  return a `MISSING_API_KEY` envelope pointing users at `request_api_key`.
  Configure the key after signup to enable verifiers.

## [0.3.0] - 2026-05-24

### BREAKING CHANGE

`brasil-mcp-match` on PyPI is now the **MCP stdio HTTP client** (MIT). The
previous Postgres-direct stdio (v0.2.1) and `lookup_cnpj()` top-level helper
have been removed from the published package.

- New behavior: `brasil-mcp-match` reads `BRASIL_MCP_MATCH_URL` +
  `BRASIL_MCP_MATCH_KEY` and forwards MCP tool calls to a remote
  `brasil-mcp-match-server` deployment via HTTPS. Postgres is never touched
  by the client.
- Migration for self-hosters: the server code moved to
  [`server/`](server/) in this repo. Clone the repo and run from there. See
  [server/README.md](server/README.md).
- Migration for downstream packages that imported
  `brasil_mcp_match.lookup_cnpj()`: the helper is now server-internal. Use
  the new `POST /v1/internal/empresa` REST endpoint gated by
  `X-Internal-Service-Token` (env `MATCH_INTERNAL_SERVICE_TOKEN`).

### Added

- `POST /v1/internal/empresa` server endpoint returning the full
  `EmpresaRecord` for trusted sister services (e.g., `brasil-mcp-leads`).
  Gated by `X-Internal-Service-Token` (env `MATCH_INTERNAL_SERVICE_TOKEN`).
- Repo split: `src/` (client, MIT) + `server/` (server, AGPL, not published).
- License relicense (published artifact only): AGPL-3.0-or-later → MIT.

### Removed (from the published `brasil-mcp-match` package)

- `brasil-mcp-match-server` console script (server has its own pyproject;
  renamed to `brasil-mcp-match-server-stdio` there).
- `brasil-mcp-match-ingest` console script (renamed to
  `brasil-mcp-match-server-ingest` on the server side).
- Top-level `brasil_mcp_match.lookup_cnpj()` (moved to server-internal,
  exposed via `/v1/internal/empresa`).
- All FastAPI / Postgres / Receita Federal ingestion code paths from the
  published wheel.

---

## [0.2.1] - 2026-05-24 (deprecated approach — superseded by 0.3.0)

Prior to v0.3.0, `brasil-mcp-match` on PyPI shipped the full server
(FastAPI + ingest + Postgres-direct stdio) under AGPL-3.0-or-later. The
v0.2.x line is preserved on PyPI for archaeological reasons but should not
be used for new installs.

### Added

- **MCP stdio transport** wired in `brasil-mcp-match-server` console script.
  Single-user dev mode reads `BRASIL_MCP_MATCH_DATABASE_URL`, opens a
  long-lived connection, and serves the 4 v0.1.0 tools over stdio.

## [0.2.0] - 2026-05-22

### Added

- `brasil_mcp_match.lookup_cnpj()` top-level public API for sister services.
  (Removed in v0.3.0 — see migration note above.)

## [0.1.1] - 2026-05-22

### Changed

- Ingest excludes MEI and non-`ativa` CNPJs by design. Those return
  `CNPJ_NOT_FOUND` on the tools.

## [0.1.0] - 2026-05-21

First public release of `brasil-mcp-match` — Fase 2 of Brasil MCP.

### Added

- **Core matching tools** (privacy-preserving):
  - `match_razao_social` — fuzzy match against RF-registered razão social
    (exact / fuzzy_prefix / fuzzy_word / fuzzy_phonetic, configurable
    tolerance).
  - `check_situacao_cadastral` — returns `ativa` / `suspensa` / `inapta` /
    `baixada` / `nula` / `desconhecida` + the since-date.
  - `check_porte_empresa` — returns `MEI` / `ME` / `EPP` / `DEMAIS` +
    Simples Nacional / MEI booleans.
  - `match_uf` — boolean match against RF-registered UF.
- **Adapters:** REST API via FastAPI; MCP server via FastMCP.
- **Ingestion pipeline:** RF downloader, streaming CSV parser, Postgres
  loader via `COPY FROM STDIN`, manifest tracking, monthly refresh job.
- **Auth & quota:** API key generation + sha256 hashing, per-plan monthly
  quota (Free / Starter / Pro / Enterprise).
- **LGPD:** Append-only audit log (hashed key + hashed input + summary
  only); `POST /v1/opt-out/{cnpj}` (Art. 18) with 15-business-day delay;
  `GET /v1/audit/{query_id}` with per-key RBAC; privacy assertions that
  raw RF payload never leaks.
- **Rate limiting:** Per-key sliding window via slowapi.
- **Tests:** 584 tests, 100% line + branch coverage; 240+ adversarial-input
  tests; SQL-injection-resistance suite; RBAC / rate-limit / opt-out
  isolation suites.
- **CI/CD:** GitHub Actions matrix (Python 3.11 / 3.12 / 3.13); PyPI
  release workflow (OIDC trusted publishing).

### Notes

- v0.1.0 was distributed under AGPL-3.0-or-later. From v0.3.0 onward the
  PyPI package is MIT (client only); the server stays AGPL but ships from
  [`server/`](server/) in this repo, not from PyPI.
