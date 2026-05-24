# brasil-mcp-match

[![PyPI](https://img.shields.io/pypi/v/brasil-mcp-match.svg)](https://pypi.org/project/brasil-mcp-match/)
[![Python](https://img.shields.io/pypi/pyversions/brasil-mcp-match.svg)](https://pypi.org/project/brasil-mcp-match/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![CI](https://github.com/brasil-mcp/match/actions/workflows/ci.yml/badge.svg)](https://github.com/brasil-mcp/match/actions/workflows/ci.yml)

**MCP stdio client for the Brasil MCP Match privacy-preserving CNPJ verification API.**

> **Match, don't reveal.** Verify Brazilian CNPJs (KYC, anti-fraud, onboarding,
> LGPD) from your Claude Desktop / Claude Code without exposing PII and without
> running Postgres yourself.

## Repo layout — this is a split repo

Two pieces, two licenses, one repo:

| Path | License | Purpose |
|---|---|---|
| [`src/brasil_mcp_match/`](src/brasil_mcp_match) | **MIT** | Published to PyPI as `brasil-mcp-match`. MCP stdio client + `httpx` wrapper for the REST API. **What end users install.** |
| [`server/`](server) | **AGPL-3.0-or-later** | Operator-side code. FastAPI REST API + Receita Federal ingest + Postgres. **NOT published to PyPI** — self-hosters clone this repo and run from `server/`. See [server/README.md](server/README.md). |

This split happened in v0.3.0. Up to and including v0.2.1, the PyPI package
shipped the server. From v0.3.0 onward, the PyPI package ships only the
thin HTTP client. See [CHANGELOG.md](CHANGELOG.md) for the migration story.

---

## Quickstart (client)

### 1. Install (or just `uvx` it)

```bash
uv tool install brasil-mcp-match
# or run on demand:
uvx brasil-mcp-match
```

### 2. Configure your MCP client

Claude Desktop (`claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "brasil-mcp-match": {
      "command": "uvx",
      "args": ["brasil-mcp-match"],
      "env": {
        "BRASIL_MCP_MATCH_URL": "https://server.solidapps.tech/brasil-mcp/match",
        "BRASIL_MCP_MATCH_KEY": "your-api-key-here"
      }
    }
  }
}
```

Claude Code (`.claude/settings.local.json` or via `claude mcp add`):

```bash
claude mcp add brasil-mcp-match \
  --env BRASIL_MCP_MATCH_URL=https://server.solidapps.tech/brasil-mcp/match \
  --env BRASIL_MCP_MATCH_KEY=your-api-key-here \
  -- uvx brasil-mcp-match
```

## Environment variables

| Var | Required | Default | Description |
|---|---|---|---|
| `BRASIL_MCP_MATCH_URL` | yes | — | Base URL of the Match REST API (no trailing path) |
| `BRASIL_MCP_MATCH_KEY` | yes | — | API key issued by your Match server operator |
| `BRASIL_MCP_MATCH_TIMEOUT` | no | `10` | HTTP timeout in seconds |

## Tools exposed

All 4 tools mirror the upstream Match API verbatim:

- `match_razao_social_tool(cnpj, nome, tolerance=0.85)` — fuzzy match against
  RF-registered razão social (exact / fuzzy_prefix / fuzzy_word / fuzzy_phonetic).
- `check_situacao_cadastral_tool(cnpj)` — returns `ativa` / `suspensa` /
  `inapta` / `baixada` / `nula` + since-date.
- `check_porte_empresa_tool(cnpj)` — returns `MEI` / `ME` / `EPP` / `DEMAIS`
  plus Simples and MEI flags.
- `match_uf_tool(cnpj, uf)` — verifies that the UF you have matches the one
  registered for the CNPJ (boolean, never reveals the registered UF).

> The base excludes MEI and CNPJs not in `ativa` status — those return
> `CNPJ_NOT_FOUND`.

## Error envelope

Network/HTTP failures become a JSON envelope so tool callers always see the
same shape:

```json
{ "error": { "code": "UPSTREAM_TIMEOUT", "message_pt": "...", "message_en": "..." } }
```

Codes added by this client:

- `UPSTREAM_TIMEOUT` — Match server didn't respond within `BRASIL_MCP_MATCH_TIMEOUT`.
- `UPSTREAM_NETWORK` — connection refused, DNS failure, TLS handshake error.
- `UPSTREAM_BAD_RESPONSE` — Match server returned non-JSON or unexpected shape.

All other codes (`CNPJ_NOT_FOUND`, `INVALID_CNPJ`, `UNAUTHORIZED`, etc.) come
straight from the upstream Match API.

## Privacy

This client adds **zero** new PII exposure beyond what the Match API already
processes. By design:

- The only data that leaves your machine is the CNPJ + (optionally) the name
  or UF you pass to the tool. No raw razão social, no endereço, no CPF de
  sócio ever crosses the wire from the server back to you — that's the whole
  point of Match.
- Your `BRASIL_MCP_MATCH_KEY` is sent only as the `X-Brasil-MCP-Key` HTTP
  header against `BRASIL_MCP_MATCH_URL`. Never logged.
- All HTTPS, no plaintext fallback.

## Self-hosting the server

If you want to run your own Match deployment instead of consuming a hosted one,
see [server/README.md](server/README.md). The server is AGPL-3.0-or-later;
running a public-facing instance triggers the source-availability obligation.

## License

This published package (`src/brasil_mcp_match/`) is **MIT**. See [LICENSE](LICENSE).

The server side ([`server/`](server)) is **AGPL-3.0-or-later** — see
[server/LICENSE](server/LICENSE).

The MIT license on the client is intentional: it lets you embed the stdio
client in proprietary stacks while the AGPL server protects the operator-side
work from being relicensed downstream.

## Related

- [brasil-mcp/essentials](https://github.com/brasil-mcp/essentials) — Fase 1, MIT.
- [brasil-mcp/leads](https://github.com/brasil-mcp/leads) — Fase 3, B2B lead gen.
