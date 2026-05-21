# brasil-mcp-match

**Pre-alpha.** Fase 2 do Brasil MCP — verificação privacy-preserving contra base Receita Federal.

**Filosofia:** _match, don't reveal._ A API confirma ou nega um dado que o integrador já tem, em vez de devolver o dado completo. Destrava casos de uso de KYC, anti-fraude e onboarding B2B sem o risco LGPD de uma API que "devolve dados de empresas".

## Status atual

Em design + scaffold. Spec detalhada em [`docs/superpowers/specs/2026-05-21-brasil-mcp-match-design.md`](docs/superpowers/specs/2026-05-21-brasil-mcp-match-design.md).

## Diferencial vs concorrência

| Concorrente | Modelo | Risco LGPD do cliente |
|---|---|---|
| Cnpja, BigDataCorp, Serasa | Devolve PII | Médio-alto |
| **brasil-mcp-match** | Confirma/nega match (bool/enum) | **Baixo** |

## Tools planejadas

13 match/check tools cobrindo razão social, nome fantasia, CNAE, sócios, endereço, situação, porte, idade, capital. Todas exigem CNPJ + atributo a verificar; output é booleano, enum ou range — nunca o dado bruto.

Detalhes em `docs/superpowers/specs/`.

## Licença

**AGPLv3.** Uso comercial via API hosted (nossa) é livre — o que está sob AGPL é o source. Self-host comercial sem reciprocidade requer licença comercial separada.

## Arquitetura

Mesmo padrão de `brasil-mcp-essentials` (Fase 1): core puro + adapters MCP/REST. Postgres + GIN tri-gram pra fuzzy. FastAPI pra REST, FastMCP SSE pra MCP.

## Roadmap

Ver spec. v0.1.0 cobre 4 match/check tools como MVP de KYC.

## Família Brasil MCP

- **Fase 1** — [`brasil-mcp-essentials`](https://github.com/brasil-mcp/essentials) — 14 utilities offline (validators, boleto, PIX, calendário). MIT.
- **Fase 2 — este repo** — verificação RF privacy-first. AGPLv3.
- **Fase 3** — `brasil-mcp-compliance` (futuro) — due diligence + KYC pago.
