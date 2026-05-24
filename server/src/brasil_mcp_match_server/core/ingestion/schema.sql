-- Brasil MCP Match — schema Postgres v0.1.0
-- Carrega base pública Receita Federal (CNPJ) + audit log + opt-out.

CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- =============================================================
-- Tabelas de referência (lookup, populadas a partir de tabelas RF)
-- =============================================================

CREATE TABLE IF NOT EXISTS ref_cnae (
    codigo CHAR(7) PRIMARY KEY,
    descricao TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS ref_natureza_juridica (
    codigo SMALLINT PRIMARY KEY,
    descricao TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS ref_motivo_situacao (
    codigo SMALLINT PRIMARY KEY,
    descricao TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS ref_municipio_rf (
    -- IMPORTANTE: o código de município da RF não é o IBGE. Manter separado.
    codigo_rf SMALLINT PRIMARY KEY,
    nome TEXT NOT NULL,
    uf CHAR(2)
);

CREATE TABLE IF NOT EXISTS ref_pais (
    codigo SMALLINT PRIMARY KEY,
    nome TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS ref_qualificacao_socio (
    codigo SMALLINT PRIMARY KEY,
    descricao TEXT NOT NULL
);

-- =============================================================
-- Tabelas principais (CNPJ universe)
-- =============================================================

CREATE TABLE IF NOT EXISTS empresa (
    cnpj_basico CHAR(8) PRIMARY KEY,
    razao_social TEXT NOT NULL,
    natureza_juridica SMALLINT REFERENCES ref_natureza_juridica(codigo),
    qualificacao_responsavel SMALLINT,
    capital_social NUMERIC(15, 2),
    porte_empresa SMALLINT,
    ente_federativo_responsavel TEXT
);

CREATE TABLE IF NOT EXISTS estabelecimento (
    cnpj_basico CHAR(8) NOT NULL REFERENCES empresa(cnpj_basico) ON DELETE CASCADE,
    cnpj_ordem CHAR(4) NOT NULL,
    cnpj_dv CHAR(2) NOT NULL,
    cnpj_completo CHAR(14) GENERATED ALWAYS AS (cnpj_basico || cnpj_ordem || cnpj_dv) STORED,
    identificador_matriz_filial SMALLINT,
    nome_fantasia TEXT,
    situacao_cadastral SMALLINT,
    data_situacao_cadastral DATE,
    motivo_situacao_cadastral SMALLINT REFERENCES ref_motivo_situacao(codigo),
    nome_cidade_exterior TEXT,
    pais SMALLINT REFERENCES ref_pais(codigo),
    data_inicio_atividade DATE,
    cnae_principal CHAR(7) REFERENCES ref_cnae(codigo),
    cnae_secundario TEXT,
    tipo_logradouro TEXT,
    logradouro TEXT,
    numero TEXT,
    complemento TEXT,
    bairro TEXT,
    cep CHAR(8),
    uf CHAR(2),
    municipio SMALLINT REFERENCES ref_municipio_rf(codigo_rf),
    ddd_1 TEXT,
    telefone_1 TEXT,
    ddd_2 TEXT,
    telefone_2 TEXT,
    ddd_fax TEXT,
    fax TEXT,
    correio_eletronico TEXT,
    situacao_especial TEXT,
    data_situacao_especial DATE,
    PRIMARY KEY (cnpj_basico, cnpj_ordem)
);

CREATE INDEX IF NOT EXISTS idx_estab_cnpj_completo ON estabelecimento(cnpj_completo);
CREATE INDEX IF NOT EXISTS idx_estab_situacao ON estabelecimento(situacao_cadastral);
CREATE INDEX IF NOT EXISTS idx_estab_uf_municipio ON estabelecimento(uf, municipio);
CREATE INDEX IF NOT EXISTS idx_estab_cep ON estabelecimento(cep);
CREATE INDEX IF NOT EXISTS idx_estab_cnae ON estabelecimento(cnae_principal);

-- GIN tri-gram pra fuzzy de razão social / nome fantasia
CREATE INDEX IF NOT EXISTS idx_empresa_razao_trgm
    ON empresa USING gin (razao_social gin_trgm_ops);
CREATE INDEX IF NOT EXISTS idx_estab_fantasia_trgm
    ON estabelecimento USING gin (nome_fantasia gin_trgm_ops)
    WHERE nome_fantasia IS NOT NULL;

CREATE TABLE IF NOT EXISTS socio (
    cnpj_basico CHAR(8) NOT NULL REFERENCES empresa(cnpj_basico) ON DELETE CASCADE,
    identificador_socio SMALLINT,
    nome_socio TEXT NOT NULL,
    cnpj_cpf_socio TEXT,  -- CPF mascarado por RF (***123456**); mantemos como veio
    qualificacao_socio SMALLINT REFERENCES ref_qualificacao_socio(codigo),
    data_entrada_sociedade DATE,
    pais SMALLINT REFERENCES ref_pais(codigo),
    representante_legal TEXT,
    nome_representante TEXT,
    qualificacao_representante SMALLINT REFERENCES ref_qualificacao_socio(codigo),
    faixa_etaria SMALLINT
);

CREATE INDEX IF NOT EXISTS idx_socio_cnpj ON socio(cnpj_basico);
CREATE INDEX IF NOT EXISTS idx_socio_nome_trgm
    ON socio USING gin (nome_socio gin_trgm_ops);

CREATE TABLE IF NOT EXISTS simples_nacional (
    cnpj_basico CHAR(8) PRIMARY KEY REFERENCES empresa(cnpj_basico) ON DELETE CASCADE,
    opcao_simples CHAR(1),
    data_opcao_simples DATE,
    data_exclusao_simples DATE,
    opcao_mei CHAR(1),
    data_opcao_mei DATE,
    data_exclusao_mei DATE
);

-- =============================================================
-- Manifest de ingestão (qual mês carregado, quando, hashes)
-- =============================================================

CREATE TABLE IF NOT EXISTS ingestion_manifest (
    id SERIAL PRIMARY KEY,
    rf_release CHAR(6) NOT NULL,  -- YYYYMM
    started_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    completed_at TIMESTAMPTZ,
    status TEXT NOT NULL CHECK (status IN ('running', 'success', 'failed')),
    file_count INT,
    row_count BIGINT,
    error_message TEXT,
    UNIQUE (rf_release)
);

-- =============================================================
-- Audit log (LGPD)
-- =============================================================

CREATE TABLE IF NOT EXISTS audit_log (
    query_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    api_key_hash CHAR(64) NOT NULL,
    tool TEXT NOT NULL,
    input_hash CHAR(64) NOT NULL,
    cnpj_basico CHAR(8) NOT NULL,
    response_summary JSONB NOT NULL,
    base_updated_at DATE NOT NULL,
    ip_address INET,
    latency_ms INT
);
CREATE INDEX IF NOT EXISTS idx_audit_created ON audit_log(created_at);
CREATE INDEX IF NOT EXISTS idx_audit_cnpj ON audit_log(cnpj_basico);
CREATE INDEX IF NOT EXISTS idx_audit_api_key ON audit_log(api_key_hash);

-- =============================================================
-- Opt-out (LGPD Art. 18)
-- =============================================================

CREATE TABLE IF NOT EXISTS opt_out_request (
    cnpj CHAR(14) PRIMARY KEY,
    requested_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    effective_at TIMESTAMPTZ NOT NULL,
    proof JSONB
);
CREATE INDEX IF NOT EXISTS idx_opt_out_effective ON opt_out_request(effective_at);

-- =============================================================
-- API keys / planos
-- =============================================================

CREATE TABLE IF NOT EXISTS api_key (
    id SERIAL PRIMARY KEY,
    key_hash CHAR(64) NOT NULL UNIQUE,
    plan TEXT NOT NULL CHECK (plan IN ('free', 'starter', 'pro', 'enterprise')),
    customer_email TEXT,
    customer_company TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    revoked_at TIMESTAMPTZ,
    quota_per_month INT NOT NULL
);

CREATE TABLE IF NOT EXISTS api_key_usage (
    api_key_id INT NOT NULL REFERENCES api_key(id),
    period CHAR(7) NOT NULL,  -- YYYY-MM
    queries INT NOT NULL DEFAULT 0,
    PRIMARY KEY (api_key_id, period)
);
