"""check_porte_empresa — retorna porte enumerado + flag de Simples Nacional.

RF codifica `porte_empresa`:
    01 → MEI (Microempreendedor Individual)
    03 → ME (Microempresa)
    05 → EPP (Empresa de Pequeno Porte)
    00, "", anything else → DEMAIS

Plus, vem da tabela `simples_nacional`:
    opcao_simples = 'S' → is_simples_nacional True
    opcao_mei = 'S' → is_mei True (sobrepoe porte=MEI)

LGPD: porte é dado público por força de lei (publicado na RF).
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class PorteEmpresa(StrEnum):
    MEI = "MEI"
    ME = "ME"
    EPP = "EPP"
    DEMAIS = "DEMAIS"
    DESCONHECIDO = "DESCONHECIDO"


_CODIGO_TO_PORTE: dict[str, PorteEmpresa] = {
    "01": PorteEmpresa.MEI,
    "03": PorteEmpresa.ME,
    "05": PorteEmpresa.EPP,
}


@dataclass(frozen=True, slots=True)
class PorteResult:
    porte: PorteEmpresa
    is_simples_nacional: bool
    is_mei: bool

    def to_dict(self) -> dict[str, object]:
        return {
            "porte": str(self.porte),
            "is_simples_nacional": self.is_simples_nacional,
            "is_mei": self.is_mei,
        }


def check_porte_empresa(
    codigo_porte_rf: int | str | None,
    opcao_simples: str | None = None,
    opcao_mei: str | None = None,
) -> PorteResult:
    """Translate RF porte code + Simples flags into a stable enum."""
    if codigo_porte_rf is None or codigo_porte_rf == "":
        porte = PorteEmpresa.DESCONHECIDO
    else:
        # Normalize to 2-char zero-padded string ("1" → "01", 5 → "05")
        try:
            normalized = f"{int(codigo_porte_rf):02d}"
        except (TypeError, ValueError):
            return PorteResult(PorteEmpresa.DESCONHECIDO, False, False)
        porte = _CODIGO_TO_PORTE.get(normalized, PorteEmpresa.DEMAIS)

    is_simples = (opcao_simples or "").upper() == "S"
    is_mei = (opcao_mei or "").upper() == "S"

    # MEI flag overrides porte enum if RF marks the company as MEI
    if is_mei:
        porte = PorteEmpresa.MEI

    return PorteResult(porte=porte, is_simples_nacional=is_simples, is_mei=is_mei)
