"""match_razao_social — fuzzy match privacy-preserving.

Confirma se o nome fornecido bate com a razão social registrada na RF pro
CNPJ informado. Nunca devolve a razão social registrada — só confirma/nega
+ um hint do tipo de match (exact, fuzzy_prefix, fuzzy_word, fuzzy_phonetic).

Contrato:
    Input:  cnpj (14 chars normalizados), nome (str), tolerance (float 0-1)
    Output: { match: bool, confidence: 0-1, hint: str | None }

Algoritmo:
1. Normaliza ambos os strings (uppercase, strip accents, collapse spaces).
2. Compara string-equal → "exact" (confidence=1.0).
3. rapidfuzz.partial_ratio (substring fuzzy) → "fuzzy_prefix" se >= tolerance*100.
4. rapidfuzz.token_set_ratio (words em qualquer ordem) → "fuzzy_word".
5. Fallback rapidfuzz.WRatio (peso ponderado) → "fuzzy_phonetic".

`tolerance` default 0.85. Caller pode subir pra reduzir falsos positivos.
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass
from enum import StrEnum

from rapidfuzz import fuzz


class MatchHint(StrEnum):
    EXACT = "exact"
    FUZZY_PREFIX = "fuzzy_prefix"
    FUZZY_WORD = "fuzzy_word"
    FUZZY_PHONETIC = "fuzzy_phonetic"
    NO_MATCH = "no_match"


@dataclass(frozen=True, slots=True)
class RazaoSocialMatch:
    match: bool
    confidence: float  # 0.0 to 1.0
    hint: MatchHint

    def to_dict(self) -> dict[str, object]:
        return {
            "match": self.match,
            "confidence": round(self.confidence, 3),
            "hint": str(self.hint),
        }


def _normalize(s: str) -> str:
    """Strip accents, uppercase, collapse whitespace."""
    if not s:
        return ""
    nfkd = unicodedata.normalize("NFKD", s)
    no_accents = "".join(c for c in nfkd if not unicodedata.combining(c))
    return " ".join(no_accents.upper().split())


def match_razao_social(
    nome_informado: str,
    razao_social_rf: str,
    tolerance: float = 0.85,
) -> RazaoSocialMatch:
    """Compare an informed name against the RF-registered razão social.

    Returns a structured match result. The RF razão social is consumed but
    NEVER returned in the output — only the boolean/confidence/hint."""
    n_info = _normalize(nome_informado)
    n_rf = _normalize(razao_social_rf)
    threshold = tolerance * 100

    if not n_info or not n_rf:
        return RazaoSocialMatch(False, 0.0, MatchHint.NO_MATCH)

    if n_info == n_rf:
        return RazaoSocialMatch(True, 1.0, MatchHint.EXACT)

    partial = fuzz.partial_ratio(n_info, n_rf)
    if partial >= threshold and (n_info in n_rf or n_rf in n_info):
        return RazaoSocialMatch(True, partial / 100, MatchHint.FUZZY_PREFIX)

    token_set = fuzz.token_set_ratio(n_info, n_rf)
    if token_set >= threshold:
        return RazaoSocialMatch(True, token_set / 100, MatchHint.FUZZY_WORD)

    weighted = fuzz.WRatio(n_info, n_rf)
    if weighted >= threshold:
        return RazaoSocialMatch(True, weighted / 100, MatchHint.FUZZY_PHONETIC)

    # Best score for transparency in the no-match case
    best = max(partial, token_set, weighted)
    return RazaoSocialMatch(False, best / 100, MatchHint.NO_MATCH)
