"""Planos e quotas mensais.

Configuração estática (não no DB pra simplificar). Mudanças exigem deploy —
ok pra v0.1.0; mover pro DB quando tiver pricing dinâmico.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class Plan(StrEnum):
    FREE = "free"
    STARTER = "starter"
    PRO = "pro"
    ENTERPRISE = "enterprise"


@dataclass(frozen=True, slots=True)
class PlanConfig:
    name: Plan
    monthly_quota: int  # -1 = unlimited
    rate_limit_per_minute: int

    @property
    def unlimited(self) -> bool:
        return self.monthly_quota == -1


PLANS: dict[Plan, PlanConfig] = {
    Plan.FREE: PlanConfig(Plan.FREE, monthly_quota=50, rate_limit_per_minute=10),
    Plan.STARTER: PlanConfig(Plan.STARTER, monthly_quota=500, rate_limit_per_minute=30),
    Plan.PRO: PlanConfig(Plan.PRO, monthly_quota=5_000, rate_limit_per_minute=120),
    Plan.ENTERPRISE: PlanConfig(Plan.ENTERPRISE, monthly_quota=-1, rate_limit_per_minute=600),
}


def get_plan_config(plan: Plan | str) -> PlanConfig:
    """Returns the config for a plan. Accepts the enum or its string value."""
    plan_enum = Plan(plan) if isinstance(plan, str) else plan
    return PLANS[plan_enum]
