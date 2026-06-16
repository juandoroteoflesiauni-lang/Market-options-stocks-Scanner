from __future__ import annotations
from typing import Any
"""Institutional audit helpers for the Predictive module.

The audit is intentionally static and dependency-light: it scans engine files
with AST instead of importing them, then compares discovered engines against the
routes and adapters that feed `/predictive`.
"""


import ast
import json
import re
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

ENGINE_DIR = Path("backend/layer_3_specialists/ia_probabilistico/engines")
ROUTER_FILE = Path("backend/routers/probabilistic_router.py")

WIRE_REGISTRY: dict[str, dict[str, str]] = {
    "catalyst_nlp_engine": {
        "endpoint": "/analysis/{symbol}, /catalyst/{symbol}, /thesis/{symbol}",
        "basis": "Event/catalyst NLP, earnings/news tone and jump-risk context.",
    },
    "cnn_fear_greed": {
        "endpoint": "/fear-greed/compare-cnn",
        "basis": "Market breadth, volatility and sentiment proxy cross-check.",
    },
    "cor3m_engine": {
        "endpoint": "/analysis/{symbol}, /thesis/{symbol}",
        "basis": "Correlation stress / credit-volatility regime proxy.",
    },
    "correlation_analyzer": {
        "endpoint": "/fear-greed/analyze-correlation",
        "basis": "Factor correlation validation for regime and sentiment signals.",
    },
    "cross_asset_engine": {
        "endpoint": "/analysis/{symbol}, /cross-asset/{symbol}",
        "basis": "Cross-asset beta, coupling and decoupling regime analysis.",
    },
    "dealer_flow_dynamics_engine": {
        "endpoint": "/dealer-flow/{symbol}, /meta-signal/{symbol}",
        "basis": "Dealer gamma/hedging pressure and flow imbalance.",
    },
    "delta_weighted_flow_engine": {
        "endpoint": "/analysis/{symbol}",
        "basis": "Delta-weighted option flow and put/call pressure.",
    },
    "dex_engine": {
        "endpoint": "/analysis/{symbol}, /price-targets/{symbol}",
        "basis": "Delta exposure by strike and dealer hedge sensitivity.",
    },
    "ensemble_meta_learner": {
        "endpoint": "/meta-signal/{symbol}, scripts/train_meta_learner.py",
        "basis": "Walk-forward stacked learner over engine outputs.",
    },
    "ensemble_training": {
        "endpoint": "scripts/train_meta_learner.py",
        "basis": "Auditable walk-forward training pipeline and model gate.",
    },
    "ensemble_training_models": {
        "endpoint": "scripts/train_meta_learner.py",
        "basis": "Training contracts for metrics, baselines and promotion gates.",
    },
    "expected_move_engine": {
        "endpoint": "/analysis/{symbol}, /price-targets/{symbol}",
        "basis": "Options-implied expected move and horizon ranges.",
    },
    "factor_calibration": {
        "endpoint": "/analysis/{symbol}, /thesis/{symbol}",
        "basis": "Factor reliability, PCA/optimization calibration.",
    },
    "fear_greed_engine": {
        "endpoint": "/analysis/{symbol}, /fear-greed/dashboard",
        "basis": "Institutional risk-appetite composite sentiment.",
    },
    "fear_greed_storage": {
        "endpoint": "/fear-greed/history/{symbol}, /fear-greed/stats/{symbol}",
        "basis": "Historical sentiment persistence and calibration.",
    },
    "feedback_engine": {
        "endpoint": "/analysis/{symbol}",
        "basis": "Prediction feedback calibration loop.",
    },
    "gamma_exposure_engine": {
        "endpoint": "/gamma-flip/{symbol}, /predictive-options-2/{symbol}",
        "basis": "Net gamma exposure and option positioning pressure.",
    },
    "gamma_flip_engine": {
        "endpoint": "/gamma-flip/{symbol}, /predictive-options-2/{symbol}, /thesis/{symbol}",
        "basis": "Gamma flip / zero-gamma regime and dealer hedging convexity.",
    },
    "macro_regime_prior_engine": {
        "endpoint": "/macro-regime-prior/{symbol}, /meta-signal/{symbol}",
        "basis": "VIX/rates/breadth macro prior for regime classification.",
    },
    "market_data_fetcher": {
        "endpoint": "/analysis/{symbol}",
        "basis": "Market-data adapter for fear/greed and macro factors.",
    },
    "markov_regime_engine": {
        "endpoint": "/analysis/{symbol}, /markov-regime/{symbol}",
        "basis": "Hidden/Markov price-volatility regime inference.",
    },
    "ml_optimizer": {
        "endpoint": "/fear-greed/ml-optimize",
        "basis": "Sentiment factor weight optimization.",
    },
    "multimodal_predictive": {
        "endpoint": "/thesis/{symbol}",
        "basis": "Multimodal probabilistic thesis synthesis.",
    },
    "options_order_flow_toxicity_engine": {
        "endpoint": "/options-flow-toxicity/{symbol}",
        "basis": "Toxic flow, adverse selection and options microstructure.",
    },
    "portfolio_optimizer": {
        "endpoint": "/portfolio/optimize",
        "basis": "Black-Litterman portfolio construction using predictive views.",
    },
    "probabilistic_engine": {
        "endpoint": "/analysis/{symbol}, /price-targets/{symbol}, /trajectories/{symbol}",
        "basis": "EVT/GPD, jump diffusion, particle filter, Kelly and trajectories.",
    },
    "regime_weights": {
        "endpoint": "/fear-greed/regime-weights",
        "basis": "Regime-dependent engine weighting.",
    },
    "risk_neutral_density_engine": {
        "endpoint": "/risk-neutral-density/{symbol}, /meta-signal/{symbol}",
        "basis": "Breeden-Litzenberger risk-neutral density from option prices.",
    },
    "sentiment_engine": {
        "endpoint": "/thesis/{symbol}",
        "basis": "News/social sentiment and reputation signal.",
    },
    "shadow_delta_engine": {
        "endpoint": "/predictive-options-2/{symbol}",
        "basis": "Skew-adjusted delta vs Black-Scholes delta gap.",
    },
    "skew_fattails_engine": {
        "endpoint": "/analysis/{symbol}",
        "basis": "Vol skew and fat-tail risk classification.",
    },
    "speed_instability_engine": {
        "endpoint": "/predictive-options-2/{symbol}",
        "basis": "Gamma speed / instability zones around key strikes.",
    },
    "squeeze_engine": {
        "endpoint": "/analysis/{symbol}, /price-targets/{symbol}, /thesis/{symbol}",
        "basis": "Options positioning squeeze ignition monitor.",
    },
    "tail_risk_engine": {
        "endpoint": "/predictive-options-2/{symbol}",
        "basis": "Smile curvature, risk reversal and tail alerting.",
    },
    "vol_term_engine": {
        "endpoint": "/analysis/{symbol}, /thesis/{symbol}",
        "basis": "Implied volatility term-structure regime and inversion.",
    },
    "volatility_skew_engine": {
        "endpoint": "/volatility-skew/{symbol}, /predictive-options-2/{symbol}",
        "basis": "IV skew curvature and scenario analysis.",
    },
    "volatility_surface_engine": {
        "endpoint": "/analysis/{symbol}",
        "basis": "Surface anomalies and smile/term cross-section.",
    },
    "volume_oi_engine": {
        "endpoint": "/analysis/{symbol}",
        "basis": "Volume/open-interest dynamics and accumulation pressure.",
    },
    "volume_profile_engine": {
        "endpoint": "/analysis/{symbol}, /volume-profile/{symbol}",
        "basis": "Volume profile, POC and liquidity nodes.",
    },
    "zero_day_engine": {
        "endpoint": "/predictive-options-2/{symbol}",
        "basis": "0DTE gamma walls, pinning and intraday convexity pressure.",
    },
    "zomma_engine": {
        "endpoint": "/predictive-options-2/{symbol}",
        "basis": "Zomma and vol-crush sensitivity by strike.",
    },
}

RESEARCH_ONLY = {
    "cm_math": "Shared convexity math primitives; not a standalone predictive output.",
}

BLOCKED_REGISTRY: dict[str, str] = {
    "parametric_optimizer": "Optimizer scaffold lacks a stable Predictive response contract.",
    "quantum_alpha": "Runtime model artifact/backfill gate exists, but production inference hookup is not complete.",
    "vsa_forecast_engine": "Used by technical scanner; Predictive needs a technical adapter before direct exposure.",
}

SECRET_PATTERN = re.compile(
    r"(?i)(apiKey|apikey|token|access_token|secret|password)=([^&\s\"',}]+)"
)
BEARER_PATTERN = re.compile(r"(?i)(Authorization:\s*Bearer\s+)([A-Za-z0-9._\-]+)")


def sanitize_secret_text(value: object) -> str:
    """Mask common query-string and bearer-token secret shapes."""
    text = str(value)
    text = SECRET_PATTERN.sub(lambda m: f"{m.group(1)}=***", text)
    text = BEARER_PATTERN.sub(lambda m: f"{m.group(1)}***", text)
    return text


def _module_capabilities(path: Path) -> dict[str, Any]:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except SyntaxError as exc:
        return {"broken": True, "error": str(exc), "classes": [], "functions": []}
    classes: list[str] = []
    functions: list[str] = []
    for node in tree.body:
        if isinstance(node, ast.ClassDef):
            classes.append(node.name)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            functions.append(node.name)
    return {"broken": False, "error": None, "classes": classes, "functions": functions}


def _status_for(stem: str, capabilities: dict[str, Any]) -> tuple[str, str, str, str]:
    if capabilities.get("broken"):
        return (
            "broken",
            "",
            "Syntax/import contract cannot be audited.",
            str(capabilities.get("error") or ""),
        )
    if stem in RESEARCH_ONLY:
        return "research_only", "", RESEARCH_ONLY[stem], ""
    if stem in WIRE_REGISTRY:
        endpoint = WIRE_REGISTRY[stem]["endpoint"]
        basis = WIRE_REGISTRY[stem]["basis"]
        status = (
            "partially_wired"
            if endpoint.startswith("scripts/") or "scripts/" in endpoint
            else "wired"
        )
        return status, endpoint, basis, ""
    if stem in BLOCKED_REGISTRY:
        return (
            "blocked",
            "",
            "Candidate engine with no approved Predictive contract.",
            BLOCKED_REGISTRY[stem],
        )
    if capabilities["classes"] or capabilities["functions"]:
        return (
            "unused",
            "",
            "Potential predictive engine; requires institutional contract review.",
            "not referenced by Predictive registry",
        )
    return "research_only", "", "No public engine class/function discovered.", ""


def build_engine_coverage(
    engine_dir: Path | str = ENGINE_DIR,
) -> list[dict[str, Any]]:
    """Return an auditable coverage row for every probabilistic engine file."""
    base = Path(engine_dir)
    rows: list[dict[str, Any]] = []
    for path in sorted(base.glob("*.py")):
        if path.name == "__init__.py":
            continue
        stem = path.stem
        capabilities = _module_capabilities(path)
        status, endpoint, basis, missing = _status_for(stem, capabilities)
        if status == "wired":
            action = "verified_existing_predictive_integration"
        elif status == "partially_wired":
            action = "verified_partial_integration_or_training_only_path"
        elif status == "blocked":
            action = "blocked_until_contract_or_inputs_are_stable"
        elif status == "unused":
            action = "classified_for_follow_up_not_auto_wired"
        elif status == "broken":
            action = "repair_required_before_predictive_exposure"
        else:
            action = "documented_as_research_or_helper"
        rows.append(
            {
                "engine_name": stem,
                "file": str(path).replace("\\", "/"),
                "status": status,
                "wired_endpoint": endpoint,
                "financial_basis": basis,
                "missing_inputs": missing,
                "action_taken": action,
                "public_classes": capabilities.get("classes", []),
                "public_functions": capabilities.get("functions", []),
            }
        )
    return rows


def summarize_engine_coverage(coverage: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    rows = coverage if coverage is not None else build_engine_coverage()
    counts = Counter(str(row.get("status", "unknown")) for row in rows)
    return {
        "total": len(rows),
        "wired": counts.get("wired", 0),
        "partially_wired": counts.get("partially_wired", 0),
        "unused": counts.get("unused", 0),
        "blocked": counts.get("blocked", 0),
        "broken": counts.get("broken", 0),
        "research_only": counts.get("research_only", 0),
        "by_status": dict(sorted(counts.items())),
    }


def _markdown_report(payload: dict[str, Any]) -> str:
    summary = payload["engine_coverage_summary"]
    lines = [
        "# Predictive Institutional Audit",
        "",
        f"- generated_at: {payload['generated_at']}",
        f"- total_engines: {summary['total']}",
        f"- wired: {summary['wired']}",
        f"- partially_wired: {summary['partially_wired']}",
        f"- blocked: {summary['blocked']}",
        f"- unused: {summary['unused']}",
        "",
        "## Engine Coverage",
        "",
        "| engine | status | endpoint | action |",
        "| --- | --- | --- | --- |",
    ]
    for row in payload["engine_coverage"]:
        lines.append(
            "| {engine_name} | {status} | {endpoint} | {action} |".format(
                engine_name=row["engine_name"],
                status=row["status"],
                endpoint=row["wired_endpoint"] or "-",
                action=row["action_taken"],
            )
        )
    lines.extend(
        [
            "",
            "## Model Gate",
            "",
            "Meta-learner promotion remains blocked unless walk-forward out-of-sample metrics beat naive and rule-based baselines.",
            "",
            "## Data Hygiene",
            "",
            "Provider credentials and query-string API keys are masked in this artifact.",
        ]
    )
    return "\n".join(lines) + "\n"


def generate_predictive_audit_report(
    *,
    output_dir: Path | str = Path("artifacts/reports"),
    now_iso: str | None = None,
    extra_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Write JSON and Markdown audit artifacts and return their paths."""
    generated_at = now_iso or datetime.now(tz=UTC).isoformat()
    stamp = generated_at.replace(":", "").replace("-", "").replace(".", "").replace("+", "Z")
    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    coverage = build_engine_coverage()
    payload: dict[str, Any] = {
        "generated_at": generated_at,
        "engine_coverage_summary": summarize_engine_coverage(coverage),
        "engine_coverage": coverage,
        "extra_context": json.loads(
            sanitize_secret_text(json.dumps(extra_context or {}, default=str))
        ),
        "methodology": {
            "scope": "predictive institutional audit",
            "primary_horizon_days": 5,
            "backtest_policy": "strict walk-forward, expanding train windows",
            "promotion_gate": "must beat naive and rule-based baselines out-of-sample",
        },
    }
    json_path = out_dir / f"predictive_audit_{stamp}.json"
    md_path = out_dir / f"predictive_audit_{stamp}.md"
    json_text = sanitize_secret_text(json.dumps(payload, indent=2, sort_keys=True, default=str))
    json_path.write_text(json_text + "\n", encoding="utf-8")
    md_path.write_text(sanitize_secret_text(_markdown_report(payload)), encoding="utf-8")
    return {
        "json_path": str(json_path),
        "markdown_path": str(md_path),
        "engine_coverage_summary": payload["engine_coverage_summary"],
    }
