"""match_uf, match_municipio, match_cep — localização privacy-preserving.

Confirma se a localização informada bate com a registrada na RF, sem
devolver a localização registrada. Útil pra "esta empresa está em SP?"
sem expor a cidade exata.

UF é case-insensitive (SP == sp). CEP é normalizado (só dígitos, 8 chars).
Municipio é fuzzy (a RF usa código IBGE / código RF próprio; o caller
pode passar o nome textual e nós comparamos com o nome do município
da tabela ref_municipio_rf, mas isso requer DB — aqui só comparamos
strings normalizadas como fallback).
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass


def _normalize_string(s: str) -> str:
    if not s:
        return ""
    nfkd = unicodedata.normalize("NFKD", s)
    no_accents = "".join(c for c in nfkd if not unicodedata.combining(c))
    return " ".join(no_accents.upper().split())


@dataclass(frozen=True, slots=True)
class UFMatch:
    match: bool

    def to_dict(self) -> dict[str, object]:
        return {"match": self.match}


@dataclass(frozen=True, slots=True)
class MunicipioMatch:
    match: bool

    def to_dict(self) -> dict[str, object]:
        return {"match": self.match}


@dataclass(frozen=True, slots=True)
class CepMatch:
    match: bool

    def to_dict(self) -> dict[str, object]:
        return {"match": self.match}


def match_uf(uf_informada: str, uf_rf: str | None) -> UFMatch:
    """Compare informed UF against RF-registered UF. Case-insensitive."""
    if not uf_informada or not uf_rf:
        return UFMatch(False)
    return UFMatch(uf_informada.strip().upper() == uf_rf.strip().upper())


def match_municipio(municipio_informado: str, municipio_rf: str | None) -> MunicipioMatch:
    """Compare informed municipio name against RF-registered municipio name.

    Accent-insensitive, case-insensitive, whitespace-tolerant.
    """
    if not municipio_informado or not municipio_rf:
        return MunicipioMatch(False)
    return MunicipioMatch(_normalize_string(municipio_informado) == _normalize_string(municipio_rf))


_CEP_RE = re.compile(r"\D")


def match_cep(cep_informado: str, cep_rf: str | None) -> CepMatch:
    """Compare informed CEP against RF-registered CEP. Strips masks."""
    if not cep_informado or not cep_rf:
        return CepMatch(False)
    a = _CEP_RE.sub("", cep_informado)
    b = _CEP_RE.sub("", cep_rf)
    if len(a) != 8 or len(b) != 8:
        return CepMatch(False)
    return CepMatch(a == b)
