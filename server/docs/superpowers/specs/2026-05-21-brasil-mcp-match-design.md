# Brasil MCP Match — Fase 2 — Spec de Design

**Data:** 2026-05-21
**Status:** Draft pra aprovação
**Escopo:** Produto Fase 2 do Brasil MCP. Pacote separado de `brasil-mcp-essentials`.

---

## 1. Visão geral

`brasil-mcp-match` é um MCP server (SSE/HTTP, autenticado) + REST API que permite **verificar** dados contra a base pública da Receita Federal sem expor PII. Casos de uso: KYC, anti-fraude, onboarding B2B.

**Filosofia central:** "match, don't reveal". A API responde `{ match: true/false, confidence?: 0-1 }` em vez de devolver o dado completo. O integrador prova que sabe (ou suspeita) o dado correto; o serviço apenas confirma ou nega.

Isso destrava use cases pesados em compliance (KYC) sem o risco LGPD de uma API que "devolve dados de empresas". É a diferença entre:
- Concorrente: `GET /cnpj/12345678000195` → retorna 47 campos da empresa.
- **Nós:** `POST /match/razao_social { cnpj, nome }` → retorna `{ match: true, confidence: 0.92 }`.

---

## 2. Posicionamento de mercado

| Concorrente | Modelo | Preço típico | Risco LGPD do cliente |
|---|---|---|---|
| Cnpja.com | Devolve dados | R$150-500/mo | Médio — cliente recebe PII e precisa armazenar/proteger |
| BigDataCorp | Devolve dados em bulk | R$1k+/mo | Alto — bulk de PII |
| Serasa Experian | Devolve dados | R$ mil+/mo | Médio-alto |
| **Brasil Match (nós)** | **Confirma/nega match** | **R$ a definir** | **Baixo — cliente não recebe PII** |

Diferencial: nosso cliente NÃO precisa de DPO próprio pra usar o serviço, porque ele não está processando PII — está usando uma função booleana.

---

## 3. Tools (MCP) / Endpoints (REST)

Todas exigem `cnpj` + um campo a verificar. Outputs são booleanos ou enums — nunca o dado bruto.

### 3.1. Match Tools

| Tool | Input | Output |
|---|---|---|
| `match_razao_social` | `{ cnpj, nome, tolerance?: float }` | `{ match: bool, confidence: 0-1, hint: "exact" \| "fuzzy_prefix" \| "fuzzy_word" \| "fuzzy_phonetic" }` |
| `match_nome_fantasia` | `{ cnpj, nome }` | `{ match: bool, confidence: 0-1 }` |
| `match_cnae_principal` | `{ cnpj, cnae_code }` | `{ match: bool }` |
| `match_cnae_secundario` | `{ cnpj, cnae_code }` | `{ match: bool }` |
| `match_socio` | `{ cnpj, nome_socio }` | `{ match: bool, hint: "exact" \| "fuzzy" }` (CPF nunca exposto) |
| `match_endereco` | `{ cnpj, endereco: {...} }` | `{ match: bool, breakdown: {uf: bool, municipio: bool, cep: bool, logradouro: bool} }` |
| `match_uf` | `{ cnpj, uf }` | `{ match: bool }` |
| `match_municipio` | `{ cnpj, municipio, uf? }` | `{ match: bool }` |
| `match_cep` | `{ cnpj, cep }` | `{ match: bool }` |

### 3.2. Check Tools (status/atributo enumerado)

| Tool | Input | Output |
|---|---|---|
| `check_situacao_cadastral` | `{ cnpj }` | `{ situacao: "ativa" \| "suspensa" \| "inapta" \| "baixada", since: date }` |
| `check_porte_empresa` | `{ cnpj }` | `{ porte: "MEI" \| "ME" \| "EPP" \| "DEMAIS", is_simples_nacional: bool }` |
| `check_idade_empresa` | `{ cnpj, anos_min: int }` | `{ older_than: bool, opened_year: int }` (ano apenas, não a data) |
| `check_capital_range` | `{ cnpj, min: int, max: int }` | `{ within_range: bool }` (nunca o valor exato) |

### 3.3. Endpoints administrativos (REST only — não MCP)

| Endpoint | Função |
|---|---|
| `GET /v1/audit/<query_id>` | Trilha auditável de uma chamada (timestamp, cliente, tool, hash do input) |
| `POST /v1/opt-out/<cnpj>` | Titular pede remoção (Art. 18 LGPD) — efetiva em até 15 dias úteis |
| `GET /v1/admin/refresh-status` | Última sincronização da base RF |
| `GET /v1/health` | Health check |

**Webhook (premium, fase posterior):** notifica quando situação de CNPJ X mudar.

### 3.4. Convenções de output

Todo response inclui:
- `query_id`: UUID4 da chamada, indexa audit log
- `base_updated_at`: ISO date da última sincronização da base RF

---

## 4. Arquitetura

Mesmo padrão de `brasil-mcp-essentials`: **core puro + adapters**.

```
brasil-mcp-match/
├── pyproject.toml
├── docker-compose.yml                  # Postgres + opcional Meilisearch pra dev
├── README.md
├── LICENSE                              # ainda MIT no core? Decidir.
├── src/brasil_mcp_match/
│   ├── __init__.py
│   ├── core/
│   │   ├── matching/                    # funções puras de match
│   │   │   ├── razao_social.py          # fuzzy: rapidfuzz partial_ratio
│   │   │   ├── nome_fantasia.py
│   │   │   ├── cnae.py
│   │   │   ├── socio.py
│   │   │   ├── endereco.py
│   │   │   ├── localizacao.py           # uf, municipio, cep
│   │   │   ├── situacao.py
│   │   │   ├── porte.py
│   │   │   ├── idade.py
│   │   │   └── capital.py
│   │   ├── ingestion/                   # pipeline RF (download, parse, load)
│   │   │   ├── downloader.py            # baixa zip da RF
│   │   │   ├── parser.py                # parse dos CSVs
│   │   │   ├── loader.py                # carga em Postgres
│   │   │   ├── schema.sql               # schema do banco
│   │   │   └── refresh_job.py           # orquestração mensal
│   │   ├── audit/
│   │   │   ├── log.py                   # append-only audit log
│   │   │   ├── query_id.py
│   │   │   └── retention.py             # 6+ meses
│   │   ├── auth/
│   │   │   ├── api_key.py               # validação + rate limit
│   │   │   └── plan.py                  # plan tier (free/starter/pro/enterprise)
│   │   ├── lgpd/
│   │   │   ├── opt_out.py               # registro de opt-out por CNPJ
│   │   │   ├── dpa.py                   # DPA generator (template fill)
│   │   │   └── consent.py               # consent mandate (AP2 token validation)
│   │   ├── repository/                  # acesso ao Postgres
│   │   │   ├── cnpj_repo.py
│   │   │   ├── socio_repo.py
│   │   │   └── connection.py
│   │   └── errors.py
│   └── adapters/
│       ├── mcp/                          # MCP server (SSE) — adapter fino
│       │   ├── server.py
│       │   └── tools.py
│       ├── rest/                         # REST API (FastAPI)
│       │   ├── app.py
│       │   ├── routes_match.py
│       │   ├── routes_check.py
│       │   ├── routes_audit.py
│       │   └── routes_admin.py
│       └── http_common/                  # auth middleware, request_id, rate limit
│           └── middleware.py
├── tests/...
└── docs/...
```

---

## 5. Stack

| Componente | Escolha | Justificativa |
|---|---|---|
| Python | ≥ 3.11 | mesmo de essentials |
| Build | `uv` + `hatchling` | mesmo de essentials |
| DB | **PostgreSQL 16** | row-level + JSON + full-text + GIN |
| Search fuzzy | `rapidfuzz` (Python lib) | rápido, sem infra extra; suficiente pra <500ms p95 |
| Search avançado | **Meilisearch** (opcional, fase posterior) | só se rapidfuzz não escalar; defer |
| MCP transport | SSE (FastMCP) | stdio errado pra multi-tenant; SSE permite auth via header |
| REST API | FastAPI | OpenAPI grátis, async maduro |
| ORM | `psycopg[binary]` direto (sem ORM) | velocidade + controle de query plan; ORMs pesam aqui |
| Migrations | Alembic? Ou apenas `schema.sql` versionado? | **Apenas `schema.sql` + scripts numerados** — mais simples na v0 |
| Cache | Redis (opcional, fase posterior) | só se rate de query ficar alto |
| Queue | RQ ou Celery (opcional) | só pra refresh mensal — provavelmente cron suffices |
| Auth | API key (header `X-Brasil-MCP-Key`) | simples; OAuth fica pra v1 |
| Rate limit | `slowapi` + Redis (ou in-memory na v0) | per-plan limits |
| Audit log storage | Postgres tabela append-only | mesma infra, query fácil |
| Audit log retention | 6 meses (LGPD compliant) | minimum exigível |
| Telemetria | PostHog (opt-in, herdado de essentials) | mesma stack |
| Licença | **AGPLv3** | impede uso embedded sem reciprocidade; force-multiplier comercial |

**Decisão licença:** essentials = MIT (open source generoso, atrair audience). Match = **AGPLv3** (uso comercial direto via API hosted nossa, ou pago se quiser self-host). Modelo Sentry / Plausible / Mattermost.

---

## 6. Ingestão da base Receita Federal

Esta é a parte mais pesada e que distingue Match de "wrapper de API". O motor de ingestão é também precursor da Fase 3.

### 6.1. Fonte

Receita Federal publica dumps mensais em https://dados.gov.br/dados/conjuntos-dados/cadastro-nacional-da-pessoa-juridica-cnpj. URLs estáveis (`/CNPJ/dados_abertos_cnpj/<YYYYMM>/`).

Conteúdo:
- `Empresas{0..9}.zip` — dados cadastrais (~55M registros)
- `Estabelecimentos{0..9}.zip` — estabelecimentos (~60M)
- `Socios{0..9}.zip` — sócios
- `Simples.zip` — Simples Nacional / MEI
- Tabelas auxiliares: Cnaes, Naturezas, Municípios, Países, Qualificações, Motivos.

Total comprimido: ~5 GB. Descomprimido: ~30 GB.

### 6.2. Pipeline

```
+----------------+    +------------+    +-----------+    +-------------+
| Downloader     | -> | Parser     | -> | Loader    | -> | Postgres    |
| (zip → local)  |    | (CSV → Row)|    | (COPY)    |    | (tabelas)   |
+----------------+    +------------+    +-----------+    +-------------+
        |                                                       ↑
        v                                                       |
+----------------+                                       +-------------+
| Manifest log   |                                       | indexes     |
| (timestamps,   |                                       | + GIN       |
|  hashes)       |                                       +-------------+
+----------------+
```

- **Downloader:** baixa só o que mudou (via headers `If-Modified-Since`); calcula sha256; armazena em `s3://brasil-mcp-rf-cache/<YYYYMM>/`.
- **Parser:** streaming CSV (não carrega tudo em memória), decode latin-1 → utf-8, lida com CSVs malformados (linhas com aspas escapadas erradas, etc.).
- **Loader:** usa `COPY FROM` do Postgres pra carga rápida (10x mais rápido que INSERT). Faz em transação por arquivo; se falhar, rollback.
- **Refresh:** cron mensal (dia 5 de cada mês — RF costuma publicar nos primeiros dias).

### 6.3. Schema Postgres

Tabelas principais:

```sql
CREATE TABLE empresa (
    cnpj_basico CHAR(8) PRIMARY KEY,            -- 8 primeiros do CNPJ
    razao_social TEXT NOT NULL,
    natureza_juridica SMALLINT,
    qualificacao_responsavel SMALLINT,
    capital_social NUMERIC(15, 2),
    porte_empresa SMALLINT,                      -- 01=ME, 03=EPP, 05=DEMAIS
    ente_federativo_responsavel TEXT
);

CREATE TABLE estabelecimento (
    cnpj_basico CHAR(8) NOT NULL,
    cnpj_ordem CHAR(4) NOT NULL,                 -- 0001 = matriz
    cnpj_dv CHAR(2) NOT NULL,
    cnpj_completo CHAR(14) GENERATED ALWAYS AS (cnpj_basico || cnpj_ordem || cnpj_dv) STORED,
    identificador_matriz_filial SMALLINT,
    nome_fantasia TEXT,
    situacao_cadastral SMALLINT,                 -- 1=nula, 2=ativa, 3=suspensa, 4=inapta, 8=baixada
    data_situacao_cadastral DATE,
    motivo_situacao_cadastral SMALLINT,
    cnae_principal CHAR(7),
    cnae_secundario TEXT[],                      -- array de cnaes 7-digit
    tipo_logradouro TEXT,
    logradouro TEXT,
    numero TEXT,
    complemento TEXT,
    bairro TEXT,
    cep CHAR(8),
    uf CHAR(2),
    municipio SMALLINT,
    PRIMARY KEY (cnpj_basico, cnpj_ordem)
);

CREATE INDEX idx_estab_cnpj_completo ON estabelecimento(cnpj_completo);
CREATE INDEX idx_estab_situacao ON estabelecimento(situacao_cadastral);
CREATE INDEX idx_estab_uf_municipio ON estabelecimento(uf, municipio);
CREATE INDEX idx_estab_cep ON estabelecimento(cep);

-- GIN tri-gram pra fuzzy de razão social
CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE INDEX idx_empresa_razao_trgm ON empresa USING gin (razao_social gin_trgm_ops);
CREATE INDEX idx_estab_fantasia_trgm ON estabelecimento USING gin (nome_fantasia gin_trgm_ops);

CREATE TABLE socio (
    cnpj_basico CHAR(8) NOT NULL,
    identificador_socio SMALLINT,                -- 1=PJ, 2=PF, 3=estrangeiro
    nome_socio TEXT NOT NULL,
    cnpj_cpf_socio TEXT,                          -- CPF vem mascarado da RF: ***123456**
    qualificacao_socio SMALLINT,
    data_entrada_sociedade DATE,
    pais SMALLINT,
    representante_legal TEXT,
    nome_representante TEXT,
    qualificacao_representante SMALLINT,
    faixa_etaria SMALLINT
);

CREATE INDEX idx_socio_cnpj ON socio(cnpj_basico);
CREATE INDEX idx_socio_nome_trgm ON socio USING gin (nome_socio gin_trgm_ops);

CREATE TABLE simples_nacional (
    cnpj_basico CHAR(8) PRIMARY KEY,
    opcao_simples CHAR(1),                       -- S/N
    data_opcao_simples DATE,
    data_exclusao_simples DATE,
    opcao_mei CHAR(1),
    data_opcao_mei DATE,
    data_exclusao_mei DATE
);
```

Tabelas auxiliares (CNAE, naturezas, motivos, municípios IBGE) — lookup pequeno, em memória ou tabela.

Audit log:

```sql
CREATE TABLE audit_log (
    query_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    api_key_hash CHAR(64) NOT NULL,              -- sha256 da key, nunca a key em si
    tool TEXT NOT NULL,
    input_hash CHAR(64) NOT NULL,                -- sha256 do payload, nunca o payload
    cnpj_basico CHAR(8) NOT NULL,                -- pra opt-out reverse-lookup
    response_summary JSONB,                       -- {match: true, confidence: 0.92} — apenas resumo
    base_updated_at DATE NOT NULL,
    ip_address INET
);
CREATE INDEX idx_audit_created ON audit_log(created_at);
CREATE INDEX idx_audit_cnpj ON audit_log(cnpj_basico);
CREATE INDEX idx_audit_api_key ON audit_log(api_key_hash);
```

Opt-out:

```sql
CREATE TABLE opt_out_request (
    cnpj CHAR(14) PRIMARY KEY,
    requested_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    effective_at TIMESTAMPTZ NOT NULL,            -- requested_at + 15 business days
    proof JSONB                                    -- evidência (email, documento, etc.)
);
```

Quando um CNPJ está em `opt_out_request` e `effective_at <= now()`, queries pra ele retornam `OPT_OUT_RECORD` error em vez do resultado.

---

## 7. LGPD baseline

### 7.1. Base legal

**Art. 7º, IX da LGPD** (legítimo interesse) + dados tornados públicos por força de lei (cadastro RF é público).

### 7.2. LIA (Legítimo Interesse Assessment)

Documento escrito que justifica a base legal por escrito. Itens obrigatórios:
1. Propósito do tratamento (KYC, anti-fraude, compliance)
2. Necessidade (não há alternativa menos invasiva — match contra base oficial)
3. Balanceamento (vantagens vs riscos pro titular — minimização via "match, not reveal")
4. Mecanismos de transparência (página pública explicando como dados são tratados)
5. Salvaguardas (mascaramento de CPF de sócio, opt-out funcional)

Vai em `docs/lgpd/LIA.md`. Revisão anual.

### 7.3. DPA template

Pra clientes B2B (integradores), gerar DPA (Data Processing Agreement). Nós = operador. Cliente = controlador.

Template em `docs/lgpd/DPA-template.md`. Gerador automatizado pode preencher campos do cliente.

### 7.4. Audit log retention

Mínimo 6 meses. Configurável até 24 meses (regulatório). Implementado via partição temporal Postgres (tabela `audit_log` particionada por mês).

### 7.5. Opt-out

Endpoint `POST /v1/opt-out/<cnpj>` com prova (email do titular, documento). Validação humana antes de efetivar. SLA 15 dias úteis.

Quando ativo, queries do CNPJ retornam:
```json
{
  "error": {
    "code": "OPT_OUT_RECORD",
    "message_pt": "Este CNPJ optou por remoção (Art. 18 LGPD). Contate-nos.",
    "message_en": "This CNPJ opted out (LGPD Art. 18). Contact us."
  }
}
```

### 7.6. Mascaramento de PII

- CPF de sócios: NUNCA exposto. A RF já entrega mascarado (`***123456**`), mas mesmo o mascarado nunca sai da API.
- Endereço completo: nunca devolvido — só `match: true/false` por componente.
- Telefones/emails: a RF não publica, então não temos.

### 7.7. Termos de uso

Proibições explícitas pro integrador:
- Cold call/email marketing em massa
- Cruzamento com bases de terceiros sem consentimento do titular
- Revenda do acesso bulk
- Engenharia reversa pra recompor o dado (ex: pedir match repetidamente pra inferir o nome real)

Detecção automática de abuso de "guessing attack" (muitas queries fuzzy no mesmo CNPJ) → rate limit agressivo + investigação.

---

## 8. Pricing inicial (proposta)

| Plano | Preço | Queries/mês | Features |
|---|---|---|---|
| Free | R$ 0 | 50 | Match básico, sem audit retrieve |
| Starter | R$ 49/mês | 500 | + audit retrieve, email support |
| Pro | R$ 199/mês | 5.000 | + webhooks, SLA email 24h |
| Enterprise | R$ 799+/mês | Ilimitado | + DPA, SLA email 4h, suporte por chat |

Cobrança via Stripe. Sem custo de cartão de crédito BR.

---

## 9. Critério de aceite (v0.1.0 da Fase 2)

- [ ] Pipeline de ingestão funcionando ponta-a-ponta com 1 mês da base RF carregado.
- [ ] 4 match tools implementados e testados: `match_razao_social`, `check_situacao_cadastral`, `match_uf`, `check_porte_empresa`. Suficiente pra MVP de KYC.
- [ ] MCP SSE server expondo essas 4 tools.
- [ ] REST API com mesmas 4 + endpoints admin (audit, health, refresh-status).
- [ ] Auth API key + rate limit em memória.
- [ ] Audit log em Postgres com retention 6 meses.
- [ ] Opt-out funcional (endpoint + bloqueio de queries).
- [ ] LIA + DPA template prontos pra advogado revisar.
- [ ] Tests com coverage ≥90%, security suite inclusa.
- [ ] CI verde.
- [ ] Docker Compose pra dev local (Postgres + app).
- [ ] README + tools.md + landing básica.

Estimativa: 4-5 semanas / 1 dev + 0.5 advogado.

---

## 10. Próximos passos imediatos (hoje)

Hoje fechamos esse design e fazemos o **bootstrap**:

1. `pyproject.toml` com a stack
2. Layout completo
3. Schema SQL inicial (`schema.sql`)
4. Docker Compose pra Postgres local
5. Stubs do core/matching/ (interfaces vazias)
6. Stub do REST app + MCP server

**Não fazemos hoje:** download/ingestão real da RF (é trabalho de horas, melhor em sessão dedicada), implementação dos match algorithms, LIA/DPA (advogado).

---

## 11. Decisões abertas a revisar

| Tópico | Decisão proposta | Alternativa |
|---|---|---|
| Licença do core | AGPLv3 | MIT (mais aberto, perde leverage comercial) |
| ORM | psycopg direto | SQLAlchemy 2.0 (verboso, mas migra fácil pra outras DBs) |
| Migrations | scripts numerados em SQL | Alembic (mais robusto, mais cerimônia) |
| Search fuzzy | rapidfuzz + pg_trgm | Meilisearch (overhead, mas melhor no longo prazo) |
| Rate limit | slowapi in-memory na v0 | Redis (single-node OK; Redis vira quando escalar) |
| MCP transport | SSE | streamable HTTP (mais novo, menos clientes suportam ainda) |
| Hosting | TBD — Hetzner? Fly? Railway? | Render, AWS, etc. |

Itens marcados como "proposto" podem ser alterados pelo usuário. Cada um tem trade-off documentado pra reavaliação no review.
