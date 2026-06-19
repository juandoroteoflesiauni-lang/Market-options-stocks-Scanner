# Performance Baseline — Fase 3 (evidencia cuantitativa)

> **Fecha:** 2026-06-17 · **Rama:** `sec/hardening-gitleaks-gemini`
> **Fuentes:** pytest (Phase A/B/C), DuckDB (`quantum_analyzer`, `audit_complex`, `alpaca_bot_audit`),
> EOD `eod_audit_20260617.json`. **El daemon dual estaba ACTIVO** durante la auditoría
> (PID 34012 mantenía `bingx_bot_audit.duckdb` 12.6 GB bloqueado) — lecturas en modo `read_only`.

## 0. TL;DR

**El baseline no es confiable porque las capas de medición se contradicen entre sí.** Antes de
optimizar nada (PF, IS, Sharpe), hay que **reparar la integridad de medición**. Los objetivos del
brief (PF>1.2, IS<15bps, Sharpe intraday>0) son hoy **NO MEDIBLES** de forma fiable.

## 1. Suite de tests (Fase A/B/C)

```
pytest tests/unit/test_tca_phase_a.py test_execution_policy_phase_b.py test_profit_calibration_phase_c.py
→ 16 passed in 4.03s
```
La *lógica* de TCA, execution policy y profit calibration es correcta y está testeada. El problema
no es el cálculo, sino que **no recibe datos reales** (ver §3).

## 2. Inventario de datos (DuckDB)

| DB | Tablas (filas) | Estado |
|---|---|---|
| `quantum_analyzer.duckdb` (1.3 MB) | trade_journal **48** · bot_cycle_logs **0** · probabilistic_analyses **0** · option_oi_snapshots **0** | **Journal TCA casi vacío y stale** |
| `bingx_bot_audit.duckdb` (12.6 GB) | — (bloqueado por daemon vivo) | Creciendo sin control (ver §5) |
| `alpaca_bot_audit.duckdb` (5.3 GB) | alpaca_audit_cycles **833** | Payload JSON por ciclo |
| `audit_complex.duckdb` (30 MB) | audit_trade_results **141** · agentic_decisions **1205** · api_calls 4677 · errors **6609** · logs 123181 · process_snapshots 1741 | Fuente real de cierres hoy |

## 3. Integridad de medición — hallazgo dominante (confirma + amplía H6)

**Existen ≥4 sistemas de medición que NO reconcilian:**

1. **Journal TCA** (`quantum_analyzer.trade_journal`): 48 filas, **todas `dry_run=true`**, IS=0,
   slippage=0, delay=0, **1 solo símbolo BINGX**, rango **2026-06-10 → 06-12** (≈1 semana stale).
   `probabilistic_analyses` y `bot_cycle_logs` están **en 0 filas**. → El journal no recibe fills live.
2. **`audit_complex.audit_trade_results`** (141 cierres HOY, 14:26–22:19 UTC) — la única fuente con
   PnL realizado intradía. Pero registra **exits parciales** (half-TP, structural, gex-wall), no el
   ciclo completo de la posición.
3. **EOD `route_pnl`** — calculado desde `bingx_exchange_reports` (realizado) + **delta de equity**
   asignado por notional (los fills de audit "lack closed P/L").
4. **`audit_agentic_trade_decisions`** (1205) — decisiones, pero `correlation_id` **NO une** con
   `audit_trade_results` (join devolvió 0 filas) → **imposible medir edge score→outcome por trade**.

**Contradicción de signo (crítica):** para BingX hoy:
- `audit_trade_results`: **+325.34** (23W / 77L)
- EOD `route_pnl`: **−2,374.82 USDT** (W68 / L12)

Los dos sistemas discrepan en **signo y en conteo de wins/losses**. No se puede afirmar si BingX
ganó o perdió hoy. **Esto invalida cualquier PF/Sharpe hasta unificar la contabilidad.**

## 4. Lo que SÍ se puede medir (audit_trade_results, hoy)

| Módulo | Cierres | Win/Loss | Σ PnL USD | avg pnl% |
|---|---|---|---|---|
| bingx | 100 | 23 / 77 | +325.34 | +0.424% |
| alpaca | 41 | 41 / 0 | +132.53 | +13.37% |

**R:R agregado:** `avg_win = +7.536` vs `avg_loss = −0.318` (64W / 77L).
→ Patrón de **muchas pérdidas pequeñas + pocas ganancias grandes** (anti-intuitivo vs H3). PF de
*exits parciales* ≈ (64·7.54)/(77·0.318) ≈ **19** — pero el equity Alpaca **cayó −8,483 USD/3d** y el
EOD da todas las rutas negativas. **Conclusión:** `audit_trade_results` captura solo cierres por
reglas (half-TP/structural), **no las pérdidas grandes** (mark-to-market de posiciones abiertas +
flatten EOD), que es donde se va el equity. El balance Alpaca lo confirma: long_market_value 31,307,
short −12,314, initial_margin 61,010 → el grueso del −8,483 es **posición abierta marcada**, no cierres.

**Exit reasons (hoy):** `structural_exit` 52 (−20.2) · `parametric_half_tp` 41 (+132.53) ·
`gex_wall_proximity_close` 36 (−3.41) · varios `parametric_step_N_fatigue` (positivos). Los 41 wins
de Alpaca son **todos `parametric_half_tp`** (opciones) → el motor de salidas parciales funciona; el
problema está en la **selección de entrada** y en el **manejo de la posición grande**.

**Decisiones agénticas:** PASS **820** / EXECUTE **385** (execute rate **32%**); **97% con
`quant_default_used=true`** (committee AI off, como esperado). El payload tiene `options_analysis`,
`committee`, `macro_risk` pero **no un score numérico directo joinable** con outcomes.

## 5. Errores y degradación de datos (audit_errors, 6,609)

| Error | Conteo | Implicación |
|---|---|---|
| `SPX-USDT: fetch_failed` + `no_equity_data_source` | 437 + 437 | El proxy SPX→SPY del bridge predictivo **falla** → señal predictiva inexistente para índices |
| `NCSK*USD-USDT: l2_unavailable:empty_book` (AAPL/TSLA/META/INTC/GOOGL/MCD…) | 1,900+ | Los stock-perp sintéticos **no tienen libro L2** → módulo L2 degradado a 0 |

→ En la práctica, BingX corre **sin L2 y sin predictivo real** para buena parte del universo: el
score se sostiene en `technical` (consenso 16-motores) + heurística equity. Esto **confirma H10
empíricamente** y explica por qué `predictive_score`/`l2_score` aportan poco al aggregate.

> **Operacional:** `bingx_bot_audit.duckdb` = **12.6 GB** y `audit_logs` = 123k filas. El flag
> `AUDIT_COMPACT_PAYLOAD=true` + `AUDIT_RETAIN_MAX_CYCLES=1500` no está conteniendo el crecimiento.
> Riesgo de disco/IO en producción. Marcar para Fase 4/5.

## 6. Gaps vs objetivos del brief

| Métrica objetivo | Estado actual | ¿Medible hoy? |
|---|---|---|
| PF rolling ≥ 1.15 (30 trades) | `rolling_pf=null` (sample 0 en journal); audit da signos contradictorios | **NO** — depende de unificar contabilidad |
| Implementation Shortfall < 15 bps | `trades_with_tca=0`, journal IS=0 dry-run stale | **NO** — journal desconectado de fills |
| Win rate por ruta (+5% vs baseline) | BingX 23/100 (audit) vs 68/80 (EOD) — sin baseline confiable | **NO** |
| Trades/día verification ≥ 8 | Sí: 141 cierres + 385 EXECUTE hoy | **SÍ** — volumen sobra |
| Drawdown intraday sin breach EOD flatten | Alpaca −8,483 USD/3d; daily_loss cap BingX 5000 no disparó | Parcial — equity baja pero cap no vincula |
| BingX options↔technical <10% conflictos | No instrumentado (no hay journal de conflictos) | **NO** |

## 7. Correlación score→outcome (¿aporta edge el predictivo?)

**No computable hoy.** Razones: (a) `decision_score` en el journal está hardcodeado a `0.0`
(ver `journal_tca.persist_equity_tca_execution`), (b) `correlation_id` no une decisiones con
resultados, (c) el blend ML (H1) altera el score sin dejar traza separable. → La pregunta central
del brief ("¿el predictive aporta edge real?") **no tiene respuesta empírica** con la
instrumentación actual. Es el principal entregable de medición a reparar.

## 8. Conclusiones y prerequisitos para Fase 4

**Prerequisitos de medición (bloquean la optimización de PF/IS/ejecución):**
1. **Unificar contabilidad** en una sola fuente de verdad (preferible: journal TCA recibiendo todos
   los fills reales con `decision_price`, `fill_price`, `decision_score`, `correlation_id` válidos).
2. **Cablear el journaling al hot-path real** (los fills no llegan a `quantum_analyzer.duckdb` desde 06-12).
3. **Persistir `decision_score` real** (hoy 0.0) y un `correlation_id` que una decisión↔outcome.
4. **Aislar el blend ML (H1)** detrás de env-flag para poder medir baseline sin/ con ML.

**Lo que el baseline sí permite afirmar:**
- El sistema **opera con volumen suficiente** (≥8 trades/día) — la fase verification cumple su objetivo de recolección.
- Las **entradas son demasiado laxas** (H4): Alpaca equity sangra vía posición marcada, no vía cierres.
- BingX corre **degradado** (sin L2/predictivo real para gran parte del universo) — el edge teórico del stack predictivo **no se está realizando**.
- Los **motores de salida parcial funcionan** (parametric_half_tp positivo); el cuello de botella es selección de entrada + gestión de la posición grande + EOD flatten.

**Comandos de reproducción:**
```powershell
cd c:\dev\deep-funnel-station
.venv\Scripts\python.exe -m pytest tests/unit/test_tca_phase_a.py tests/unit/test_execution_policy_phase_b.py tests/unit/test_profit_calibration_phase_c.py -q
# DuckDB read-only (no correr mientras el daemon tenga la DB bloqueada):
.venv\Scripts\python.exe -c "import duckdb;con=duckdb.connect('data/audit_complex.duckdb',read_only=True);print(con.execute('select module,count(*),round(sum(pnl_usd),2),sum(case when pnl_usd>0 then 1 else 0 end) from audit_trade_results group by module').fetchall())"
```
