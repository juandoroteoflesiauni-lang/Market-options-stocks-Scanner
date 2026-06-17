"""Transaction Cost Analysis — implementation shortfall y reportes EOD. # [PD-2][TH]"""

from backend.services.tca.implementation_shortfall import (
    TcaExecutionMetrics,
    compute_implementation_shortfall,
)
from backend.services.tca.tca_eod_report import build_tca_eod_report

__all__ = [
    "TcaExecutionMetrics",
    "build_tca_eod_report",
    "compute_implementation_shortfall",
]
