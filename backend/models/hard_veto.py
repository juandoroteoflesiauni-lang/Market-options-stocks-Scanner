from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, ConfigDict, Field


class VetoType(str, Enum):
    VETO_NO_DATA = "VETO_NO_DATA"
    VETO_ILLIQUID = "VETO_ILLIQUID"
    VETO_EXTREME_EXHAUSTION = "VETO_EXTREME_EXHAUSTION"
    VETO_COMPLETE_CONTRADICTION = "VETO_COMPLETE_CONTRADICTION"


class HardVetoResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    vetoed: bool
    veto_type: VetoType | None = Field(default=None)
    reason: str = ""

    @classmethod
    def passed(cls) -> HardVetoResult:
        return cls(vetoed=False)

    @classmethod
    def veto(cls, veto_type: VetoType, reason: str) -> HardVetoResult:
        return cls(vetoed=True, veto_type=veto_type, reason=reason)
