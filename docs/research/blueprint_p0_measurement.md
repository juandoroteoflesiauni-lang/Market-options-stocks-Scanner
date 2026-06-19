# Blueprint P0 [code] — Reparación de Medición (TCA hot-path + decision_score + correlation_id)

> **Estado:** PROPUESTA DE DISEÑO. **Requiere aprobación humana antes de implementar** (es [code]).
> **Prioridad:** P0 — desbloquea PF rolling, IS, edge score→outcome y la calibración del meta-learner.
> **Refs:** H6 (TCA desconectado), F3§3 (4 sistemas de PnL contradictorios), F3§7 (edge no computable).
> **Restricciones:** PD-3 (Fases B/C solo vía hub), PD-6 (test por cambio), no tocar EOD flatten ni
> `META_LEARNER_PROMOTE_SYNTHETIC`. Máx 2-3 archivos por turno.

## 1. Objetivo (una oración)

Que **cada fill real** (no solo dry-run) quede registrado en `quantum_analyzer.duckdb::trade_journal`
con `decision_score` real y un `correlation_id` que **una decisión ↔ outcome**, para que TCA/PF/edge
sean medibles y reconciliables con `audit_complex`.

## 2. Diagnóstico confirmado (Fase 3)

- `trade_journal`: 48 filas, **todas `dry_run=true`**, IS=0, **stale 06-10→06-12**, 1 símbolo.
- `bot_cycle_logs` y `probabilistic_analyses`: **0 filas**.
- `journal_tca.persist_equity_tca_execution` fija `decision_score=0.0` y `realized_pnl=0.0` (hardcoded).
- `TradeJournalEntry` **no tiene** `correlation_id`; el único enlace es `cycle_id`.
- `audit_complex.audit_trade_results` (outcomes) **sí** tiene `correlation_id`, pero el join con
  `audit_agentic_trade_decisions` devolvió 0 filas → IDs no propagados de forma consistente.

## 3. Causa raíz (hipótesis a confirmar en implementación)

1. **Wiring:** `persist_*_tca_execution` está importado en `bingx_bot_service` (8 refs) y
   `alpaca_bot_execution_mixin` (2 refs), pero **no se invoca en la rama de fill live** (o se invoca
   solo cuando `dry_run`, o falla silenciosamente). Confirmar leyendo las ramas de ejecución.
2. **decision_score:** se pierde entre `decide()` y el persist (se pasa 0.0).
3. **correlation_id:** no se genera en `decide()` ni se propaga decisión→risk desk→fill→journal.

## 4. Diseño propuesto

### 4.1 Esquema (migración idempotente — patrón ya existente `_migrate_trade_journal_tca`)
Añadir columna `correlation_id VARCHAR DEFAULT ''` a `trade_journal` vía el mismo mecanismo
idempotente de `_TCA_COLUMNS` (no romper DBs existentes). Añadir `correlation_id: str = ""` a
`TradeJournalEntry` + `to_dict()`.

### 4.2 Flujo de datos (propagación del id y el score)
```
decide() genera correlation_id (uuid4)  ─┐
  └─ BingXDecision/AlpacaDecision.score_total/probability  ─┐
       └─ risk desk OrderIntent (añadir correlation_id)      │
            └─ fill confirmado                                │
                 └─ persist_*_tca_execution(                  │
                      decision_score=<score real>,  ←─────────┘
                      correlation_id=<mismo id>,
                      dry_run=<flag real de ejecución>)
                 └─ audit_decision_snapshot(correlation_id=<mismo id>)  ← une con audit_complex
```

### 4.3 Cambios por archivo (orden de implementación, ≤3 por turno)

**Turno A — capa de persistencia (sin tocar hot-path todavía):**
- `backend/services/trade_journal_service.py`: + columna/campo `correlation_id` (schema, migración,
  `TradeJournalEntry`, INSERT, `to_dict`). + `list_trades` ya hace `SELECT *` (sin cambio).
- `backend/services/tca/journal_tca.py`: `persist_equity_tca_execution` y la variante BingX aceptan
  `decision_score: float` y `correlation_id: str` como parámetros (default conservador) en vez de 0.0.
- `tests/unit/test_tca_journal_correlation.py` (**nuevo**): persistir entry con score/correlation_id
  → `list_trades` los devuelve; migración idempotente sobre DB temporal; IS calculado != 0 cuando
  `decision_price != fill_price`.

**Turno B — wiring hot-path BingX:**
- `backend/services/bingx_bot_service.py`: en la rama de fill **live**, invocar persist con
  `decision_score=decision.score_total`, `correlation_id`, `dry_run=<real>`, `decision_price`/`fill_price`
  reales. Generar/propagar `correlation_id`.
- `backend/services/bingx_decision_engine.py` (mínimo): exponer/propagar `correlation_id` si se decide
  generarlo en `decide()` (alternativa: generarlo en el service).
- `tests/unit/test_bingx_journal_hotpath.py` (**nuevo**): mock del cliente venue → tras fill simulado,
  el journal recibe 1 fila con `dry_run=false`, `decision_score>0`, `correlation_id` no vacío.

**Turno C — wiring hot-path Alpaca (idéntico patrón):**
- `backend/services/bot/alpaca_bot_execution_mixin.py` + test análogo.

### 4.4 Reconciliación
Tras los 3 turnos, `tca_eod_report.build_tca_eod_report` empezará a leer trades con IS real, y el join
`trade_journal.correlation_id ↔ audit_trade_results.correlation_id` permitirá computar **edge
score→outcome**. Añadir un check EOD que compare `sum(realized_pnl)` del journal vs `route_pnl` y
**alerte si divergen >X%** (resuelve la contradicción +325 vs -2375).

## 5. Plan de tests (PD-6 — obligatorio por cambio financiero)

| Test | Qué valida | AAA |
|---|---|---|
| `test_tca_journal_correlation::test_persist_roundtrip_with_score_and_id` | score/correlation_id persisten y se recuperan | sí |
| `..::test_migration_idempotent_adds_correlation_id` | migración no rompe DB existente | sí |
| `..::test_is_bps_nonzero_when_slippage` | IS≠0 con decision_price≠fill_price | sí |
| `test_bingx_journal_hotpath::test_live_fill_persists_real_row` | fill live → 1 fila dry_run=false score>0 | mock venue |
| `test_alpaca_journal_hotpath::test_live_fill_persists_real_row` | idem Alpaca | mock |
| `test_eod_reconciliation::test_alerts_on_pnl_divergence` | alerta si journal vs route_pnl divergen | sí |

Comando: `pytest tests/ -v -k "journal or tca or reconcil"` → todo verde antes de mergear.

## 6. Riesgos y mitigaciones

| Riesgo | Mitigación |
|---|---|
| Doble escritura (idempotencia) | `UNIQUE(execution_timestamp, symbol, cycle_id)` ya existe; añadir correlation_id no rompe la clave. |
| Persist en hot-path añade latencia/IO | Mantener fire-and-forget + JSONL fallback (patrón actual); no bloquear el fill. |
| DB bloqueada por daemon vivo (12.6 GB) | Escribir solo a `quantum_analyzer.duckdb` (no a los audit DBs); el daemon no la tiene en exclusiva. |
| Romper tests TCA actuales (16) | Parámetros con default conservador → backward-compatible. |
| Tocar lógica de ejecución | Cambios mínimos en la rama de fill; no alterar decisión ni sizing. |

## 7. Criterios de aceptación

- [ ] `trade_journal` recibe filas **con `dry_run=false`** en una corrida live de 1 ciclo.
- [ ] `decision_score > 0` y `correlation_id` no vacío en esas filas.
- [ ] `build_tca_eod_report` reporta `trades_with_tca > 0` e `IS` real.
- [ ] Join `trade_journal ↔ audit_trade_results` por `correlation_id` devuelve filas.
- [ ] `pytest tests/ -v -k "journal or tca or execution or calibration"` verde.
- [ ] EOD no muestra contradicción de signo journal vs route_pnl (o la alerta dispara).

## 8. Fuera de alcance (P1+, Blueprint propio)

Env-flag del blend ML (H1), `PredictiveRiskGate` size-down (H2), cluster/regime weights (P2),
diagnóstico OPTIONS_R1 (H7). No se tocan aquí.

---
**Aprobación requerida** para iniciar el Turno A. Indicar si se aprueba el diseño o se ajusta el
alcance (p. ej. implementar solo Turno A primero y validar antes de tocar el hot-path).
