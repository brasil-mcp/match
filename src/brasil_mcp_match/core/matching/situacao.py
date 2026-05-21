"""check_situacao_cadastral — retorna a situação cadastral da empresa.

Diferente das tools de match, esta é uma "check" tool que retorna um valor
enumerado (não a string da RF). O caller pergunta "qual o status?" e recebe
um enum estável.

A RF codifica `situacao_cadastral` como int:
    1 = nula
    2 = ativa
    3 = suspensa
    4 = inapta
    8 = baixada

Mapeamos pra strings estáveis. `since` é a data_situacao_cadastral, devolvida
como ISO date (LGPD: data de mudança de status é informação pública por força
de lei, ok devolver).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import StrEnum


class SituacaoCadastral(StrEnum):
    NULA = "nula"
    ATIVA = "ativa"
    SUSPENSA = "suspensa"
    INAPTA = "inapta"
    BAIXADA = "baixada"
    DESCONHECIDA = "desconhecida"


_CODIGO_TO_ENUM: dict[int, SituacaoCadastral] = {
    1: SituacaoCadastral.NULA,
    2: SituacaoCadastral.ATIVA,
    3: SituacaoCadastral.SUSPENSA,
    4: SituacaoCadastral.INAPTA,
    8: SituacaoCadastral.BAIXADA,
}


@dataclass(frozen=True, slots=True)
class SituacaoResult:
    situacao: SituacaoCadastral
    since: date | None

    def to_dict(self) -> dict[str, object]:
        return {
            "situacao": str(self.situacao),
            "since": self.since.isoformat() if self.since else None,
        }


def check_situacao_cadastral(
    codigo_rf: int | str | None,
    since: date | None = None,
) -> SituacaoResult:
    """Translate the RF situacao_cadastral code into a stable enum."""
    if codigo_rf is None or codigo_rf == "":
        return SituacaoResult(SituacaoCadastral.DESCONHECIDA, since)
    try:
        code = int(codigo_rf)
    except (TypeError, ValueError):
        return SituacaoResult(SituacaoCadastral.DESCONHECIDA, since)
    situacao = _CODIGO_TO_ENUM.get(code, SituacaoCadastral.DESCONHECIDA)
    return SituacaoResult(situacao, since)
