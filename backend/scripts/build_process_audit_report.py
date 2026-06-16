"""Consolida calibración + ejecuciones + EOD en informe de procesos."""

from __future__ import annotations

import json
import sqlite3
import sys
from datetime import UTC, datetime
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


def _load_json(path: Path) -> dict:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> int:
    report: dict = {"generated_at": datetime.now(UTC).isoformat()}

    cal_path = _ROOT / "backend/config/alpaca_r1_options_calibrated.json"
    report["calibration_r1_c5"] = _load_json(cal_path)

    cal_db = _ROOT / "backend/data/options_strategy_audit.sqlite3"
    if cal_db.exists():
        conn = sqlite3.connect(cal_db)
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT report_json FROM options_strategy_calibrations ORDER BY created_at DESC LIMIT 1"
        ).fetchone()
        if row:
            report["options_strategy_calibration"] = json.loads(row[0])
        execs = conn.execute(
            """
            SELECT e.execution_id, e.audit_id, e.underlying, e.ok, e.dry_run,
                   e.venue_order_id, e.created_at,
                   a.decision, a.symbol
            FROM options_strategy_executions e
            LEFT JOIN options_strategy_audit a ON a.audit_id = e.audit_id
            WHERE date(e.created_at) = date('now')
            ORDER BY e.created_at DESC
            LIMIT 50
            """
        ).fetchall()
        report["options_executions_today"] = [dict(r) for r in execs]
        audits = conn.execute(
            """
            SELECT symbol, decision, COUNT(*) AS n
            FROM options_strategy_audit
            WHERE date(created_at) = date('now')
            GROUP BY symbol, decision
            ORDER BY n DESC
            """
        ).fetchall()
        report["options_audit_summary_today"] = [dict(r) for r in audits]
        conn.close()

    eod = _load_json(_ROOT / "data/eod_snapshots/eod_audit_20260615.json")
    if eod:
        bal = eod.get("alpaca_balance") or {}
        bingx = (eod.get("bingx_perp_balance") or {}).get("balance") or {}
        report["eod_snapshot"] = {
            "alpaca_equity": bal.get("equity"),
            "alpaca_portfolio_value": bal.get("portfolio_value"),
            "options_buying_power": bal.get("options_buying_power"),
            "bingx_equity": bingx.get("equity"),
            "bingx_unrealized": bingx.get("unrealizedProfit"),
            "bingx_positions": len(eod.get("bingx_perp_positions") or []),
        }

    audit = _load_json(_ROOT / "reports/alpaca_audit_20260615_session.json")
    rows = audit.get("audit_rows") or []
    if rows:
        r1 = [r for r in rows if r.get("route") == "priority"]
        r2 = [r for r in rows if r.get("route") == "scan"]
        report["post_calibration_cycle"] = {
            "blocked": (audit.get("cycle") or {}).get("blocked_reasons"),
            "r1_allow": sum(1 for r in r1 if r.get("decision") in ("ALLOW", "SIZE_DOWN")),
            "r1_block": sum(1 for r in r1 if r.get("decision") == "BLOCK"),
            "r2_allow": sum(1 for r in r2 if r.get("decision") in ("ALLOW", "SIZE_DOWN")),
            "r2_block": sum(1 for r in r2 if r.get("decision") == "BLOCK"),
            "symbols_size_down": [
                str(r["symbol"]) for r in rows if r.get("decision") == "SIZE_DOWN"
            ],
        }

    out_json = _ROOT / "reports/process_audit_20260615.json"
    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")

    lines = [
        "# Auditoría de procesos — 2026-06-15",
        "",
        f"Generado: {report['generated_at']}",
        "",
        "## 1. Calibración C5 (R1 opciones)",
    ]
    c5 = report.get("calibration_r1_c5") or {}
    fw = c5.get("family_weights") or {}
    metrics = c5.get("metrics") or {}
    lines.append(f"- Muestras: **{metrics.get('n_samples', '—')}** | Motores isotónicos: **8**")
    lines.append(
        f"- Pesos familia: momentum={fw.get('momentum', 0):.3f} "
        f"volume={fw.get('volume', 0):.3f} structure={fw.get('structure', 0):.3f}"
    )
    lines.append(
        f"- Blend classic/options: {c5.get('classic_weight')}/{c5.get('options_weight')}"
    )
    lines.append(
        "- Artefactos: `alpaca_r1_options_calibrated.json` + `alpaca_r1_engine_calibrators.joblib`"
    )
    lines.append("")
    lines.append("## 2. Calibración Options Strategy (Fase 7)")
    osc = report.get("options_strategy_calibration") or {}
    if osc:
        rate = float(osc.get("execute_rate") or 0) * 100
        lines.append(
            f"- Observaciones: **{osc.get('observation_count', '—')}** | "
            f"Execute rate: **{rate:.1f}%**"
        )
        lines.append(f"- Pesos actuales: {osc.get('current_weights')}")
        lines.append(f"- Pesos sugeridos: {osc.get('suggested_weights')}")
        lines.append(
            f"- Confianza min: {osc.get('current_min_global_confidence')} "
            f"→ sugerido {osc.get('suggested_min_global_confidence')}"
        )
    lines.append("")
    lines.append("## 3. Ejecuciones opciones hoy (paper)")
    execs = report.get("options_executions_today") or []
    ok_execs = [e for e in execs if e.get("ok")]
    lines.append(
        f"- Órdenes enviadas: **{len(execs)}** | OK: **{len(ok_execs)}** | "
        f"Fallidas: **{len(execs) - len(ok_execs)}**"
    )
    for e in ok_execs[:15]:
        vid = str(e.get("venue_order_id") or "")[:8]
        lines.append(
            f"  - {e.get('underlying')}: {e.get('decision')} venue={vid}…"
        )
    lines.append("")
    lines.append("## 4. Resumen auditoría opciones (decisiones)")
    for row in (report.get("options_audit_summary_today") or [])[:12]:
        lines.append(
            f"- {row['symbol']}: {row['decision']} (n={row['n']})"
        )
    lines.append("")
    eod_snap = report.get("eod_snapshot") or {}
    if eod_snap:
        lines.append("## 5. EOD balances")
        lines.append(
            f"- Alpaca equity: **${eod_snap.get('alpaca_equity')}** | "
            f"options BP: **${eod_snap.get('options_buying_power')}**"
        )
        lines.append(
            f"- BingX equity: **${eod_snap.get('bingx_equity')}** | "
            f"uPnL: **${eod_snap.get('bingx_unrealized')}** | "
            f"posiciones: **{eod_snap.get('bingx_positions')}**"
        )
        lines.append("")
    pc = report.get("post_calibration_cycle") or {}
    if pc:
        lines.append("## 6. Ciclo post-calibración (diagnóstico, mercado cerrado)")
        lines.append(f"- Bloqueado: {pc.get('blocked')}")
        lines.append(
            f"- R1 SIZE_DOWN/ALLOW: {pc.get('r1_allow')} | BLOCK: {pc.get('r1_block')}"
        )
        lines.append(f"- R2 ALLOW: {pc.get('r2_allow')} | BLOCK: {pc.get('r2_block')}")
        lines.append(f"- SIZE_DOWN: {', '.join(pc.get('symbols_size_down') or [])}")
        lines.append("")
    lines.append("## 7. Archivos generados")
    lines.append("- `reports/alpaca_audit_20260615_session.json` — tabla completa R1+R2")
    lines.append("- `reports/process_audit_20260615.json` — consolidado")
    lines.append("- `data/eod_snapshots/eod_audit_20260615.json` — snapshot EOD daemon")

    out_md = _ROOT / "reports/process_audit_20260615.md"
    out_md.write_text("\n".join(lines), encoding="utf-8")
    print(out_md.read_text(encoding="utf-8"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
