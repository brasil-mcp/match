"""Códigos de erro e ErrorObj usados pelo brasil-mcp-match."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class ErrorCode(StrEnum):
    # Input validation
    INVALID_CNPJ = "INVALID_CNPJ"
    INVALID_FORMAT = "INVALID_FORMAT"
    MISSING_REQUIRED_FIELD = "MISSING_REQUIRED_FIELD"
    EMPTY_INPUT = "EMPTY_INPUT"
    VALUE_TOO_LONG = "VALUE_TOO_LONG"

    # Match-specific
    CNPJ_NOT_FOUND = "CNPJ_NOT_FOUND"
    OPT_OUT_RECORD = "OPT_OUT_RECORD"

    # Auth / quota
    INVALID_API_KEY = "INVALID_API_KEY"
    QUOTA_EXCEEDED = "QUOTA_EXCEEDED"
    PLAN_LIMIT = "PLAN_LIMIT"

    # System
    BASE_NOT_LOADED = "BASE_NOT_LOADED"
    INTERNAL_ERROR = "INTERNAL_ERROR"


@dataclass(frozen=True, slots=True)
class ErrorObj:
    code: ErrorCode
    message_pt: str
    message_en: str
    suggestion: str | None = None

    def to_dict(self) -> dict[str, str | None]:
        return {
            "code": str(self.code),
            "message_pt": self.message_pt,
            "message_en": self.message_en,
            "suggestion": self.suggestion,
        }
