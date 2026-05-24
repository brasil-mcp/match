# brasil-mcp-match-server

[![License: AGPL v3](https://img.shields.io/badge/License-AGPL_v3-blue.svg)](https://www.gnu.org/licenses/agpl-3.0)

**Server-side code for the Brasil MCP Match privacy-preserving CNPJ verification API.**

FastAPI REST API + Model Context Protocol (stdio + SSE) adapter + Receita
Federal ingest job + Postgres repository. AGPL-3.0-or-later.

> **Not published to PyPI.** Self-hosters clone this repo and run code from
> the [`server/`](.) subdirectory directly. End users who just want to call a
> deployed Match server install the client (`pip install brasil-mcp-match` from
> the [parent directory](..)).

---

## Why "match, don't reveal"

Instead of "give me the razão social for CNPJ X" (the model every CNPJ API
uses), Brasil MCP Match flips the question: "does CNPJ X have a razão social
that matches the string Y?" The answer is a boolean + a confidence hint.
**The base data never crosses the wire.**

- **LGPD posture.** Operators don't accumulate copies of the RF base;
  controllers don't redistribute PII they had no basis to share.
- **Match accuracy >> exact lookup.** Fuzzy matching is built in for razão
  social (accent-insensitive, token-set, weighted phonetic).
- **Auditability.** Every call returns a `query_id` and is logged with a
  hashed API key, hashed input, and response summary only — never the raw
  RF payload.

---

## Requirements

- Python 3.11+ (3.12 / 3.13 supported)
- Postgres 16+
- ~10 GB disk for the Receita Federal base
- ~1 day for the initial ingest (depends on bandwidth + IO)

## Quickstart

### 1. Clone and install

```bash
git clone https://github.com/brasil-mcp/match.git
cd match/server
uv sync --all-extras --dev
```

### 2. Bring up Postgres

The repo ships a `docker-compose.yml` for local Postgres:

```bash
docker compose up -d postgres
```

### 3. Run the ingest (initial bootstrap)

```bash
export BRASIL_MCP_MATCH_DATABASE_URL=postgresql://brasilmcp:dev_password_only@localhost:5432/brasil_mcp_match
export BRASIL_MCP_MATCH_RF_CACHE=./data/rf-cache
uv run brasil-mcp-match-server-ingest
```

This downloads the latest Receita Federal monthly dump, parses CSVs, and
loads `empresas`, `estabelecimentos`, `simples` into Postgres. Initial run
takes ~1 day. Monthly refreshes are incremental and faster.

### 4. Serve the REST API

```bash
uv run brasil-mcp-match-server-rest
# → uvicorn on http://0.0.0.0:8000
```

### 5. (Optional) Single-user stdio adapter

For local-only single-user usage (no auth, ENTERPRISE plan synthetic):

```bash
uv run brasil-mcp-match-server-stdio
```

For production, mount the REST API and let users connect via the
`brasil-mcp-match` PyPI client.

---

## Environment variables

| Var | Required | Default | Description |
|---|---|---|---|
| `BRASIL_MCP_MATCH_DATABASE_URL` | yes (prod) | local dev URL | Postgres DSN. |
| `BRASIL_MCP_MATCH_RF_CACHE` | no | `./data/rf-cache` | Local dir where the RF dump is cached during ingest. |
| `BRASIL_MCP_MATCH_RF_BASE_URL` | no | RF default | Override the source URL for the RF monthly dump (e.g. Nextcloud mirror for staging). |
| `BRASIL_MCP_MATCH_INCLUDE_INATIVAS` | no | `0` | Set `1` to keep CNPJs that aren't `ativa` in the loaded base. Default behavior excludes them. |
| `BRASIL_MCP_MATCH_INCLUDE_MEI` | no | `0` | Set `1` to keep MEI in the loaded base. Default excludes MEI. |
| `MATCH_INTERNAL_SERVICE_TOKEN` | when using `/v1/internal/empresa` | — | Shared secret guarding the internal endpoint that returns the full `EmpresaRecord` for trusted sister services (`brasil-mcp-leads`, etc.). |

API keys for end users live in Postgres (`api_keys` table) — see
`src/brasil_mcp_match_server/core/auth/api_key.py` for the schema and
provisioning helper.

---

## Endpoints

Public (require `X-Brasil-MCP-Key` header):

- `POST /v1/match/razao-social` — fuzzy match against razão social.
- `POST /v1/check/situacao` — situação cadastral.
- `POST /v1/check/porte` — porte + Simples + MEI flags.
- `POST /v1/match/uf` — UF match (boolean).
- `POST /v1/lgpd/opt-out` — LGPD opt-out request flow.

Internal (require `X-Internal-Service-Token`):

- `POST /v1/internal/empresa` — full `EmpresaRecord` for trusted sister
  services. Bypasses the privacy-preserving wrapper because the caller is
  another Brasil MCP service operating under the same controller. Logged
  with the same audit trail as public calls.

See `docs/tools.md` for the full per-tool contract.

---

## LGPD & compliance

- `docs/lgpd/DPA-template.md` — Data Processing Agreement template for
  operator ↔ controller relationships.
- `docs/lgpd/LIA.md` — Legitimate Interest Assessment template.
- Audit logs hash both the API key and the input payload before persisting.
  Raw inputs never hit disk.

---

## Tests

```bash
uv run pytest -q                                # full suite, 610 tests
uv run pytest tests/security -q                 # security suite only
uv run pytest tests/core/repository -q          # repo tests (testcontainers Postgres)
uv run ruff check src tests
uv run pyright src
```

Integration tests under `tests/core/repository/` and the security suite spin
up real Postgres via `testcontainers`. Requires Docker.

---

## License

AGPL-3.0-or-later. See [LICENSE](LICENSE).

If you deploy this server on a network-facing service, the AGPL obligation
requires you to make the source available to your users. The MIT-licensed
[client](../src/brasil_mcp_match) is the supported way to consume a hosted
deployment without inheriting the AGPL obligation.
