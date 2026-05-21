# brasil-mcp-match

[![PyPI version](https://img.shields.io/pypi/v/brasil-mcp-match.svg)](https://pypi.org/project/brasil-mcp-match/)
[![Python](https://img.shields.io/pypi/pyversions/brasil-mcp-match.svg)](https://pypi.org/project/brasil-mcp-match/)
[![License: AGPL v3](https://img.shields.io/badge/License-AGPL_v3-blue.svg)](https://www.gnu.org/licenses/agpl-3.0)
[![CI](https://github.com/brasil-mcp/match/actions/workflows/ci.yml/badge.svg)](https://github.com/brasil-mcp/match/actions/workflows/ci.yml)
[![Downloads](https://img.shields.io/pypi/dm/brasil-mcp-match.svg)](https://pypi.org/project/brasil-mcp-match/)

> **Match, don't reveal.** Privacy-preserving CNPJ verification against the Brazilian Receita Federal base.

**brasil-mcp-match** is a Model Context Protocol server (and matching REST API) that lets AI agents and KYC pipelines _verify_ what they already know about a Brazilian company — without ever returning the underlying personal/business data.

Instead of "give me the razão social for CNPJ X" (which is the model every existing CNPJ API uses), the question becomes "does CNPJ X have a razão social that matches the string Y?". The answer is a boolean + a confidence hint. **The base data never crosses the wire.**

That single inversion has big downstream consequences:

- **LGPD posture.** Operators don't accumulate copies of the RF base; controllers don't redistribute PII they had no basis to share.
- **Match accuracy >> exact lookup.** Fuzzy matching is built in for razão social (accent-insensitive, token-set, weighted phonetic). The caller doesn't need to know exactly how the RF spells things.
- **Auditability.** Every call returns a `query_id` and is logged with a hashed API key, hashed input, and response summary only — never the raw RF payload.

---

## Install

```bash
pip install brasil-mcp-match
# or
uv add brasil-mcp-match
```

**License note.** This package is **AGPL-3.0-or-later**. If you're calling our hosted API, you're fine. If you want to self-host commercially without releasing your derived source, contact us for a commercial license.

---

## Quick start

### 1. Start Postgres + run the ingest

```bash
git clone https://github.com/brasil-mcp/match.git
cd match
docker compose up -d postgres
uv run brasil-mcp-match-ingest --release 2026-04
```

The first ingest takes ~30 minutes (downloads ~5 GB of RF dumps, parses, COPYs into Postgres). Subsequent monthly refreshes are incremental.

### 2. Start the API

```bash
uv run brasil-mcp-match serve 8000
```

### 3. Make a call

```bash
curl -X POST http://localhost:8000/v1/match/razao-social \
  -H "X-Brasil-MCP-Key: brasilmcp_yourkeyhere" \
  -H "Content-Type: application/json" \
  -d '{"cnpj": "33000167000101", "nome": "Petrobras"}'
```

```json
{
  "query_id": "06d9ef6e-3759-43a4-864f-786e1ad59a6d",
  "base_updated_at": "2026-04-01",
  "match": true,
  "confidence": 1.0,
  "hint": "fuzzy_prefix"
}
```

The response **never** contains the actual razão social registered at the Receita Federal — just confirmation that what you supplied matches.

---

## v0.1.0 tools

| Tool | Description | Output |
|---|---|---|
| `match_razao_social` | Verify if a name matches the RF-registered razão social. Fuzzy: accent-insensitive, token-set, phonetic. | `{ match, confidence, hint }` |
| `check_situacao_cadastral` | Return the cadastral status (ativa/suspensa/inapta/baixada/nula). | `{ situacao, since }` |
| `check_porte_empresa` | Return company size enum (MEI/ME/EPP/DEMAIS) and Simples Nacional flag. | `{ porte, is_simples_nacional, is_mei }` |
| `match_uf` | Verify the company's registered UF matches what you supplied. | `{ match }` |

Full input/output schemas + cURL examples: [`docs/tools.md`](docs/tools.md).

---

## MCP client setup (Claude Desktop)

```json
{
  "mcpServers": {
    "brasil-mcp-match": {
      "url": "https://match.brasil-mcp.com/sse",
      "transport": "sse",
      "headers": {
        "X-Brasil-MCP-Key": "brasilmcp_yourkeyhere"
      }
    }
  }
}
```

Different MCP clients negotiate auth differently. For stdio (single-user dev), use the REST API with the same key. For hosted SSE, the header above is sufficient. See the MCP spec for transport details.

---

## Privacy & LGPD

This is a server that processes data classified by Brazilian law as personal (when a CNPJ is associated with an MEI or natural-person sócio). We take three explicit positions:

1. **Operational minimization.** Every output is a boolean, enum, or short string. The RF payload (razão social, capital social, addresses, sócio names, CPF fragments) never leaves the server. We've structurally enforced this in the codebase + asserted it across a security test suite (323 tests).
2. **Opt-out (Art. 18 LGPD).** Titulares can request removal via `POST /v1/opt-out/{cnpj}` with proof. After 15 business days the CNPJ is blocked from all match/check tools. See [`docs/lgpd/`](docs/lgpd/) for our LIA template and DPA template.
3. **Auditability without spying.** Every call yields a `query_id`. Callers can retrieve the audit entry for their own calls — never for someone else's (RBAC enforced + tested).

If you're an attorney evaluating us for a deployment, start with the LIA template (`docs/lgpd/LIA.md`). It maps our processing to a legitimate-interest legal basis with explicit safeguards.

---

## Architecture

```
src/brasil_mcp_match/
  core/
    ingestion/    # downloader, parser, loader, refresh job, manifest
    matching/    # razao_social, situacao, porte, localizacao
    repository/   # CnpjRepo protocol + PostgresCnpjRepo
    auth/        # api_key + quota
    audit/       # append-only log
    lgpd/        # opt-out (Art. 18)
    errors.py    # ErrorCode + ErrorObj
  adapters/
    mcp/         # FastMCP server + 4 tools
    rest/        # FastAPI app + dependencies + routes_match + routes_lgpd
```

Same pattern as [brasil-mcp-essentials](https://github.com/brasil-mcp/essentials): pure-Python core, thin adapters. Postgres + GIN tri-gram indexes for fuzzy. FastAPI + FastMCP SSE.

---

## Roadmap

- **v0.1.0 (now)** — 4 match/check tools, REST + MCP SSE adapters, API keys, audit, opt-out, rate limit. 100% test coverage (line + branch).
- **v0.2.0** — More tools: `match_cnae`, `check_idade`, `match_socio_cpf` (compares CPF prefix only), `match_municipio`, `match_cep`.
- **v0.3.0** — OAuth 2.0 (vs API keys), tenant-scoped quotas, OpenTelemetry tracing.
- **v0.4.0** — Tri-gram fuzzy as a built-in match strategy (vs Python-side rapidfuzz). Async refresh job.

---

## Family

- **Fase 1** — [`brasil-mcp-essentials`](https://github.com/brasil-mcp/essentials). 14 offline utilities (validators, boletos, PIX QR, calendário). MIT.
- **Fase 2 — this repo.** Verification against the RF base. AGPLv3.
- **Fase 3** — `brasil-mcp-compliance` (future). Due diligence + KYC, commercial.

---

## License

[AGPL-3.0-or-later](LICENSE). For commercial self-host without source release obligations, contact us.

---

## Contributing

Issues + PRs welcome. Before opening a PR, run:

```bash
uv run ruff check && uv run ruff format --check && uv run pyright src
uv run pytest --cov-fail-under=100 -q
```

CI runs the same gates across Python 3.11, 3.12, and 3.13.
