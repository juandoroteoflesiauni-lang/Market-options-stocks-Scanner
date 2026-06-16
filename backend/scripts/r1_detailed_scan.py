import sys
from pathlib import Path
import json
from datetime import datetime, timezone

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from backend.config.alpaca_priority_route import ALPACA_ROUTE1_WATCHLIST
from backend.config.options_strategy_loader import get_options_strategy_config
from backend.models.options_strategy import OptionsStrategyInput
from backend.services.options_strategy.pipeline import OptionsStrategyPipeline

def main() -> None:
    config = get_options_strategy_config()
    as_of = datetime.now(timezone.utc)
    
    report = []
    
    for symbol in ALPACA_ROUTE1_WATCHLIST:
        try:
            inp = OptionsStrategyInput(symbol=symbol, as_of=as_of)
            log = OptionsStrategyPipeline.run_dry(inp, config=config, persist=False)
            features = log.features
            decision = log.playbook_decision
            
            ticker_data = {
                "symbol": symbol,
                "decision": str(decision.decision),
                "playbook": str(decision.playbook_family) if decision.playbook_family else "-",
                "recommended_structure": str(decision.recommended_structure),
                "direction": str(decision.direction),
                "confidence": round(decision.confidence, 4),
                "veto_triggered": decision.veto_triggered,
                "reason_codes": decision.reason_codes,
            }
            
            if features:
                ticker_data["technical"] = {
                    "bias": round(features.technical_direction_bias, 4),
                    "trend_quality": round(features.trend_quality_score, 4),
                    "breakout_state": str(features.breakout_state),
                    "reversal_risk": round(features.reversal_risk_score, 4),
                    "structure_alignment": round(features.structure_alignment_score, 4)
                }
                ticker_data["predictive"] = {
                    "bias": round(features.predictive_direction_bias, 4),
                    "regime_class": str(features.regime_class),
                    "expected_move_pct": round(features.expected_move_pct, 4),
                    "tail_risk_left": round(features.left_tail_risk_score, 4),
                    "tail_risk_right": round(features.right_tail_risk_score, 4),
                    "dispersion": round(features.forecast_dispersion_score, 4)
                }
                ticker_data["options"] = {
                    "bias": round(features.options_direction_bias, 4),
                    "dealer_regime": str(features.dealer_regime),
                    "gamma_pressure": round(features.gamma_pressure_score, 4),
                    "iv_state": str(features.iv_state),
                    "flow_conviction": round(features.flow_conviction_score, 4),
                    "chain_liquidity": round(features.chain_liquidity_score, 4),
                    "structure_preference": str(features.structure_preference)
                }
                ticker_data["fusion"] = {
                    "global_bias": round(features.global_bias, 4),
                    "global_confidence": round(features.global_confidence, 4)
                }
            
            report.append(ticker_data)
        except Exception as e:
            report.append({"symbol": symbol, "error": str(e)})

    print(json.dumps(report, indent=2))

if __name__ == "__main__":
    main()
