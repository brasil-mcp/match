# LIA — Legitimate Interest Assessment (template)

> **Status:** _Template. Must be reviewed by counsel before being relied upon
> for any specific operator/controller relationship._

This document is a structured assessment of the use of legitimate interest
(Art. 7º IX, LGPD) as the legal basis for processing personal data via the
`brasil-mcp-match` service. It follows the three-part balancing test commonly
applied by the ANPD: purpose, necessity, balancing.

The intended use is **privacy-preserving verification** of business identity
data published by the Receita Federal — i.e., the operator never returns the
underlying personal data; only a structured boolean / enumerated answer to a
specific question.

---

## 1. Identification

| Item | Content |
|---|---|
| **Controller** | _[Fill in: the legal person performing KYC, anti-fraud, or onboarding using the service]_ |
| **Operator** | Brasil MCP — `brasil-mcp-match` service. |
| **Sub-operators** | _[Fill in: hosting provider, e.g., Hetzner]_, PostgreSQL (managed/self-hosted). |
| **Date of assessment** | _YYYY-MM-DD_ |
| **Reviewer (legal)** | _Counsel name + OAB/PR_ |
| **Reviewer (technical)** | _DPO or technical lead_ |

---

## 2. Purpose of the processing

**What.** Verify whether a piece of business identity data supplied by an
integrator (e.g., a name, a UF, a CEP) matches the data registered at the
Receita Federal for a given CNPJ.

**Why.** To support legitimate business activities of the controller —
typically:

- **KYC / KYB** — onboarding a new commercial counterparty.
- **Anti-fraud** — detecting impersonation in B2B flows.
- **Cadastral hygiene** — keeping the controller's CRM/ERP aligned with reality.

The processing is _not_ for marketing, profiling, scoring, or any decision
that produces legal effects on a natural person.

---

## 3. Necessity test

**Could the controller achieve the same purpose without this processing?**

- Without verification, the controller cannot confirm that a counterparty is
  who it claims to be — admitting fraud risk that itself may breach Art. 6º
  VII (segurança).
- The alternative (asking the counterparty for proof documents and inspecting
  them manually) is operationally infeasible at scale.
- An alternative API model — one that _returns_ the full RF row — would
  expose the controller to risk of redistributing data they had no basis to
  share. The match-don't-reveal pattern is strictly narrower.

**Conclusion.** The processing is _necessary_ for the purpose, and the
specific data flow (boolean/enum responses, not raw RF data) is the **least
intrusive** option that achieves the legitimate interest.

---

## 4. Balancing test

| Risk vector | Likelihood | Severity | Mitigation |
|---|---|---|---|
| Re-identification from the response | Very low | Low | Responses are booleans/enums; no PII in body. Outputs structurally enforced + asserted by tests. |
| Operator retains PII unnecessarily | Low | Medium | RF base is on the operator's infra, isolated; per-call only the SQL parameters touch memory; audit log stores only hash of input. |
| Quota abuse → mass-querying | Medium | Medium | Per-key rate limit (120/min); per-plan monthly quota; observability of unusual patterns. |
| Sub-operator compromise (hosting) | Low | High | Encrypted at rest (Postgres + LUKS); TLS in transit; minimal Postgres role permissions. |
| API key compromise | Low | High | Keys are sha256-hashed at rest; never logged in plaintext; revocation supported. |
| Audit log leakage | Low | Medium | Audit log contains hashed key + hashed input + response summary only. Cannot reconstruct the input from the audit row alone. |

**Reasonable expectations of the titular.** A CNPJ owner who has filed and
maintained their company registration with the RF can reasonably expect that
their public registration data is consulted for cadastral verification. They
would _not_ reasonably expect that data to be redistributed in bulk — and we
don't do that.

**Children/teens.** A CNPJ cannot lawfully be registered to a minor (no MEI for
under-18s), so the dataset structurally does not contain child data.

**Sensitive data.** No racial/health/sexual-orientation/biometric data is
processed.

---

## 5. Transparency mechanisms

- **Public site** explains the processing, the lawful basis, and the rights
  exercises available to the titular.
- **`POST /v1/opt-out/{cnpj}`** endpoint — Art. 18 LGPD opt-out, no API key
  required, 15-business-day effective window, blocks all four match/check
  tools afterwards.
- **`GET /v1/audit/{query_id}`** endpoint — caller can retrieve the audit
  entry for their own call (NOT for others'). Cannot leak other titulares.
- **Privacy notice** linked from every error response that involves a CNPJ.

---

## 6. Safeguards

1. **Match-don't-reveal architecture.** Output is never the raw RF row.
2. **No PII in audit log.** Inputs are hashed; outputs are summaries; api
   keys are hashed.
3. **Opt-out enforced server-side.** A CNPJ flagged as opt-out returns 410
   `OPT_OUT_RECORD` from every match/check tool. Verified by automated tests.
4. **Rate limit + quota.** Default 120 calls/min/key + per-plan monthly quota.
5. **TLS 1.2+ enforced** at the reverse proxy.
6. **Retention.** Audit entries retained 6 months default (configurable).
7. **No third-party telemetry by default** (PostHog enabled only with
   `[telemetry]` optional extra).

---

## 7. Preliminary conclusion

Subject to counsel review and the controller's specific context, this
assessment finds that **legitimate interest is a defensible legal basis** for
the processing performed by `brasil-mcp-match`. The processing is necessary
for the purpose, the data flow is least-intrusive, and the safeguards
described above adequately mitigate the foreseeable risks.

This assessment will be **revisited annually**, or upon any material change
in:

- the set of tools offered,
- the response shape of any tool,
- the architecture (e.g., new sub-operator),
- the regulatory landscape (ANPD guidance, court rulings).

---

_Signatures + date below to be added in the actual deployment._
