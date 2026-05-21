# Data Processing Agreement — template

> **Status:** _Template only. Counsel must adapt and review before execution._

This template provides the contractual scaffolding for a Data Processing
Agreement (DPA) between **Brasil MCP** (as **Operator**) and the **Controller**
(client) for the use of the `brasil-mcp-match` service, in compliance with the
Lei Geral de Proteção de Dados (Lei 13.709/2018, LGPD).

---

## 1. Parties

**Operator** — Brasil MCP, _[full legal entity, address, CNPJ if applicable]_

**Controller** — _[Client legal name, address, CNPJ]_

Both Parties enter into this DPA on _[Effective Date]_ to govern the
processing of personal data by the Operator on behalf of the Controller, in
the context of the Controller's use of the `brasil-mcp-match` API.

---

## 2. Definitions

Terms used here have the meanings ascribed in Article 5 of the LGPD. In
particular: **personal data**, **sensitive personal data**, **titular**,
**controller**, **operator**, **processing**, **consent**, **incident**,
**ANPD**.

---

## 3. Scope of the processing

The Operator processes personal data on instruction from the Controller, for
the **sole purpose** of verifying CNPJ-related identity data published by the
Brazilian Receita Federal, via the API endpoints described in
[`docs/tools.md`](../tools.md).

The Operator **does not** return raw RF data to the Controller. The output
of each tool is a structured boolean/enum response. The Operator **does not**
use Controller-supplied data for any purpose other than answering the
Controller's queries.

---

## 4. Categories of personal data

- **CNPJ** (legal-person identifier; constitutes personal data when associated
  with an MEI or natural-person sócio).
- **Hash of a candidate name or attribute** (the actual input is hashed before
  persistence in the audit log).
- **API key** (sha256 hash only; never plaintext at rest).
- **IP address** of the request (optional, audit only).

The Operator does **not** process: full name of sócio, CPF, residential
address, contact email/phone of titular (except as supplied in opt-out
requests, which are hashed).

---

## 5. Categories of titulares

- MEIs (Microempreendedores Individuais), whose CNPJ is associated with a
  natural person.
- Natural-person sócios listed at the RF.

---

## 6. Duration

This DPA is effective from the Effective Date and remains in force for as long
as the Controller has an active integration with the Operator's API, plus any
retention period required by law.

---

## 7. Rights of the titular

The Operator commits to operationally support, within the technical scope of
the service:

- **Confirmation of processing.** The Operator's hosted audit log indicates
  whether a CNPJ has been queried (Operator-side).
- **Access.** The Controller can retrieve audit entries for its own calls via
  `GET /v1/audit/{query_id}`.
- **Rectification, anonymization, blocking, deletion.** Operationalized via
  the `POST /v1/opt-out/{cnpj}` endpoint.
- **Portability.** Inapplicable — the Operator does not store user-supplied
  personal data beyond hashed forms.
- **Revocation of consent.** Inapplicable — processing is grounded in
  legitimate interest, not consent. Opt-out endpoint serves the equivalent
  function.

The Controller commits to informing titulares about the processing in its own
privacy notice and to forwarding any Art. 18 requests received directly to
the Operator within five (5) business days.

---

## 8. Sub-operators

The Operator may engage **sub-operators** for hosting, monitoring, and
delivery. The current list is published at _[URL to public sub-operator
list]_ and updated upon any material change. The Operator notifies the
Controller of new sub-operators with at least **15 calendar days** advance
notice and provides a reasonable opportunity to object.

The Operator remains responsible for the acts of its sub-operators.

---

## 9. Confidentiality

The Operator ensures that all personnel with access to personal data are
bound by appropriate confidentiality obligations and have completed LGPD
awareness training within the past 12 months.

---

## 10. Security measures

The Operator implements technical and administrative measures appropriate to
the risk, including:

- TLS 1.2+ for all network traffic.
- Postgres role separation; minimum privileges per workload.
- API keys hashed (sha256) at rest; rotation supported.
- Audit log append-only; retention default 6 months.
- Per-key rate limit + monthly quota.
- Quarterly review of access logs.
- Backup of Postgres data, encrypted at rest.

---

## 11. Incident notification

In case of a confirmed personal-data security incident, the Operator notifies
the Controller in writing within **48 hours** of the discovery, with all
material facts known at the time, and cooperates with the Controller for
notification of the ANPD and the affected titulares as required by Art. 48
LGPD.

---

## 12. Audits

The Controller may, with at least **30 calendar days' notice**, audit the
Operator's compliance with this DPA, either:

- by reviewing the Operator's standardized compliance documents (SOC-2 type
  letter, ISO 27001 SoA, or equivalent), or
- by a remote interview-style audit, not exceeding 4 hours of Operator
  personnel time per audit, no more than once per calendar year.

Costs of any audit beyond the above-defined scope are borne by the requesting
Party.

---

## 13. Return / deletion at termination

Upon termination of the underlying service agreement, the Operator deletes
all Controller-supplied identifiers (hashed inputs, hashed keys, audit rows
linked to the Controller) within **30 calendar days**, except where retention
is required by law (e.g., tax records).

The Operator retains the public RF base — that is not Controller data.

---

## 14. Liability

Liability for breaches of this DPA tracks the underlying service agreement,
subject to applicable mandatory provisions of Brazilian law.

---

## 15. Governing law and forum

This DPA is governed by Brazilian law. The Parties elect the **comarca
of São Paulo, SP** as the exclusive forum, unless a different forum is
mandatory by law.

---

_Signatures + dates to be added per the Parties' execution requirements._
