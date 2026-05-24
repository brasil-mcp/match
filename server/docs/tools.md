# Tools — v0.1.0

Four match/check tools shipped in v0.1.0. All require an API key
(`X-Brasil-MCP-Key` header for REST, MCP tool-call config for SSE). All return
an envelope with `query_id` (UUID4) and `base_updated_at` (ISO 8601 date of the
last successful RF ingest).

Common error responses:

| HTTP | code | when |
|---|---|---|
| 401 | `INVALID_API_KEY` | Missing, malformed, unknown, or revoked API key. |
| 404 | `CNPJ_NOT_FOUND` | The CNPJ does not exist in the current RF base. |
| 410 | `OPT_OUT_RECORD` | The CNPJ owner has invoked Art. 18 LGPD opt-out. |
| 422 | `INVALID_CNPJ` | The supplied CNPJ has fewer or more than 14 digits after stripping. |
| 429 | `PLAN_LIMIT` | Per-minute rate limit (default 120/min/key). |

---

## 1. `match_razao_social`

Confirms whether the supplied name matches the RF-registered razão social. The
RF razão social is **never** returned. Output is a structured match decision.

### Endpoint

`POST /v1/match/razao-social`

### MCP tool name

`match_razao_social_tool`

### Input schema

| field | type | required | default | description |
|---|---|---|---|---|
| `cnpj` | string | yes | — | 14-digit CNPJ (mask tolerated; non-digits stripped) |
| `nome` | string | yes | — | Candidate name to verify |
| `tolerance` | float | no | `0.85` | Fuzzy threshold in `[0.5, 1.0]` |

### Output

```json
{
  "query_id": "<uuid4>",
  "base_updated_at": "YYYY-MM-DD",
  "match": true,
  "confidence": 0.953,
  "hint": "fuzzy_prefix"
}
```

`hint` ∈ `exact`, `fuzzy_prefix`, `fuzzy_word`, `fuzzy_phonetic`, `no_match`.

### cURL

```bash
curl -X POST http://localhost:8000/v1/match/razao-social \
  -H "X-Brasil-MCP-Key: brasilmcp_..." \
  -H "Content-Type: application/json" \
  -d '{"cnpj": "33000167000101", "nome": "Petrobras"}'
```

---

## 2. `check_situacao_cadastral`

Returns the company's cadastral status at the RF. This is **public data** by
force of Brazilian law (a company's situação must be disclosed) — so a literal
enum is returned here, unlike the match tools.

### Endpoint

`POST /v1/check/situacao`

### MCP tool name

`check_situacao_cadastral_tool`

### Input schema

| field | type | required | description |
|---|---|---|---|
| `cnpj` | string | yes | 14-digit CNPJ (mask tolerated) |

### Output

```json
{
  "query_id": "<uuid4>",
  "base_updated_at": "YYYY-MM-DD",
  "situacao": "ativa",
  "since": "2010-02-01"
}
```

`situacao` ∈ `ativa`, `suspensa`, `inapta`, `baixada`, `nula`, `desconhecida`.

### cURL

```bash
curl -X POST http://localhost:8000/v1/check/situacao \
  -H "X-Brasil-MCP-Key: brasilmcp_..." \
  -H "Content-Type: application/json" \
  -d '{"cnpj": "33000167000101"}'
```

---

## 3. `check_porte_empresa`

Returns the company size enum + Simples Nacional / MEI flags.

### Endpoint

`POST /v1/check/porte`

### MCP tool name

`check_porte_empresa_tool`

### Input schema

| field | type | required | description |
|---|---|---|---|
| `cnpj` | string | yes | 14-digit CNPJ (mask tolerated) |

### Output

```json
{
  "query_id": "<uuid4>",
  "base_updated_at": "YYYY-MM-DD",
  "porte": "EPP",
  "is_simples_nacional": false,
  "is_mei": false
}
```

`porte` ∈ `MEI`, `ME`, `EPP`, `DEMAIS`, `DESCONHECIDO`.

### cURL

```bash
curl -X POST http://localhost:8000/v1/check/porte \
  -H "X-Brasil-MCP-Key: brasilmcp_..." \
  -H "Content-Type: application/json" \
  -d '{"cnpj": "33000167000101"}'
```

---

## 4. `match_uf`

Confirms whether the supplied UF matches the RF-registered UF. Case-insensitive.

### Endpoint

`POST /v1/match/uf`

### MCP tool name

`match_uf_tool`

### Input schema

| field | type | required | description |
|---|---|---|---|
| `cnpj` | string | yes | 14-digit CNPJ (mask tolerated) |
| `uf` | string | yes | 2-letter UF code (e.g., `SP`, `RJ`) |

### Output

```json
{
  "query_id": "<uuid4>",
  "base_updated_at": "YYYY-MM-DD",
  "match": true
}
```

### cURL

```bash
curl -X POST http://localhost:8000/v1/match/uf \
  -H "X-Brasil-MCP-Key: brasilmcp_..." \
  -H "Content-Type: application/json" \
  -d '{"cnpj": "33000167000101", "uf": "RJ"}'
```

---

## LGPD endpoints

### `GET /v1/audit/{query_id}`

Retrieves the audit entry for a previously-returned `query_id`. RBAC: only the
API key that made the original call can retrieve its audit row. Returns **404**
(not 403, not 500) if the row belongs to a different key — this prevents
enumeration.

### `POST /v1/opt-out/{cnpj}`

Public endpoint (no API key required). The CNPJ titular registers a removal
request with proof (email or document ID hash). After 15 business days, all
four match/check tools return `410 OPT_OUT_RECORD` for that CNPJ.

Request body:

```json
{
  "proof_type": "email",
  "proof_value": "<email or doc id>",
  "contact_email": "<optional contact>"
}
```

Response (202 Accepted):

```json
{
  "cnpj": "33000167000101",
  "requested_at": "2026-05-21T10:00:00+00:00",
  "effective_at": "2026-06-11T10:00:00+00:00",
  "message_pt": "Pedido de remoção registrado. Vigência em até 15 dias úteis.",
  "message_en": "Opt-out request recorded. Effective within 15 business days."
}
```

The `proof_value` is **hashed** (sha256) before persistence — we never store
plaintext PII.
