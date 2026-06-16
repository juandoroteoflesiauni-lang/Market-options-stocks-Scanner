# Reenfoque del módulo `05 funding` para el Builder Plan de My Funded Futures

## Objetivo
Este documento redefine el alcance del módulo `05 funding` para que deje de ser un motor genérico de prop firms y pase a estar **especializado exclusivamente** en generar rendimientos compatibles con el **Builder Plan** de My Funded Futures. La meta ya no es “pasar cualquier evaluación”, sino optimizar el pipeline completo para sobrevivir, aprobar, retirar y sostener consistencia dentro de las reglas específicas del Builder [cite:58][cite:73].

El Builder Plan tiene una estructura clara: evaluación de 1 día mínimo, target de $3,000, EOD trailing drawdown de $2,000 o $1,500 según la opción elegida, daily loss limit de $1,000 como soft pause, máximo de 4 minis o 40 micros, news trading permitido, una sola cuenta Builder activa por usuario y un esquema de payouts sim con consistencia del 50%, buffer y tope por ciclo [cite:58][cite:73][cite:64]. Ese conjunto de reglas obliga a que `05 funding` se diseñe como un módulo de **supervivencia + extracción ordenada de payouts**, no como un simple gate de entrada a mercado [cite:58][cite:83].

## Tesis de diseño
La tesis central es que el módulo `05 funding` debe responder una sola pregunta operativa: **“¿esta operación mejora la probabilidad de aprobar, sostener el account ladder y convertir ganancias en payouts bajo Builder?”** [cite:58][cite:73]. Todo componente que no contribuya a esa pregunta debería salir del módulo o quedar subordinado a ella [cite:1][cite:58].

Esto implica tres cambios de filosofía. Primero, el éxito deja de medirse solo por PnL bruto y pasa a medirse por avance de fase, preservación del drawdown y elegibilidad de payout [cite:58]. Segundo, el sizing debe penalizar jornadas de alta varianza aunque prometan profit rápido, porque el Builder premia llegar vivo y mantener consistencia [cite:58][cite:83]. Tercero, el módulo debe tratar el target de evaluación y la mecánica de retiro como parte del mismo problema matemático, no como etapas separadas [cite:58][cite:73].

## Alcance del módulo
### Qué debe hacer
El módulo `05 funding` debe encargarse de:

- modelar las reglas del Builder como constraints ejecutivos [cite:58][cite:73];
- decidir si una señal puede operarse bajo el estado actual de la cuenta [cite:58];
- ajustar tamaño y agresividad para no comprometer el EOD trailing drawdown ni el DLL soft pause [cite:58][cite:73];
- optimizar la ruta de evaluación a sim-funded y de sim-funded a live [cite:58];
- controlar elegibilidad de payout, buffer, consistencia y cooldowns [cite:58];
- emitir reason codes y estados determinísticos específicos del Builder [cite:58][cite:73].

### Qué no debe hacer
El módulo no debe seguir intentando abstraer todas las prop firms con la misma prioridad. El roadmap original estaba pensado para múltiples firmas y presets, pero si el objetivo inmediato es operar el Builder como primera prueba de fondeo, la complejidad multi-firma deja de ser una ventaja y pasa a ser ruido [cite:1][cite:58]. El soporte multi-firma puede mantenerse como extensión futura, pero no debe guiar las decisiones actuales de arquitectura del módulo [cite:1].

## Perfil operativo Builder
### Parámetros base
Se propone fijar un perfil interno `funding_profile = MFFU_BUILDER_50K` con los siguientes parámetros centrales:

| Parámetro | Valor | Uso en el motor |
|---|---|---|
| Starting balance | $50,000 | Estado inicial de evaluación [cite:58][cite:73] |
| Profit target evaluación | $3,000 | Objetivo de aprobación [cite:58][cite:73] |
| Minimum trading days | 1 | Gate de aprobación [cite:73] |
| Max loss | $2,000 o $1,500 | Constraint principal de supervivencia [cite:58][cite:73] |
| Daily loss limit | $1,000 soft pause | Límite intradía operativo [cite:58] |
| Max contracts | 4 minis / 40 micros | Cap de exposición [cite:58] |
| News trading | Permitido | No bloquear por calendario macro per se [cite:64][cite:86] |
| Sim payout consistency | 50% | Regla principal de retiros [cite:58][cite:83] |
| Payout cap | $2,000 por ciclo | Control de extracción [cite:58][cite:64] |
| Min profit for payout | $500 | Trigger mínimo de retiro [cite:58] |
| Inactivity rule | 7 días | Monitor de actividad [cite:58] |
| Max sim payouts before live | 5 | Lógica de transición [cite:58] |
| Live cooldown after breach | 21 días | Gestión de estado live [cite:58] |

### Estados de cuenta
El módulo debe manejar estados explícitos:

- `EVAL_ACTIVE`
- `EVAL_PASSED_PENDING`
- `SIM_ACTIVE`
- `SIM_PAYOUT_ELIGIBLE`
- `SIM_BUFFER_BUILDING`
- `LIVE_ACTIVE`
- `LIVE_COOLDOWN`
- `BREACHED`
- `INACTIVE_RISK`

El motivo es simple: la misma señal no debería tratarse igual en evaluación, en buffer-building sim o cerca de una ventana de payout [cite:58][cite:73].

## Nuevo objetivo matemático
### Función objetivo real
El módulo debería reemplazar la lógica genérica de “maximize expected return” por una función objetivo más alineada con Builder:

```text
Maximizar:
P(pass_eval) × P(reach_payout_buffer) × P(request_payout_without_breach) × payout_efficiency

Sujeto a:
max_loss, dll_soft_pause, max_contracts, consistency_rule, inactivity_rule, payout_cap
```

El punto es que una operación con alto retorno esperado pero que eleva mucho la probabilidad de tocar el trailing drawdown puede ser racional en una cuenta propia y pésima en Builder [cite:58][cite:73]. Por eso `05 funding` debe optimizar “rendimiento utilizable por la firma”, no “profit aislado” [cite:58][cite:83].

## Reglas de especialización
### 1. El drawdown manda sobre la señal
El EOD trailing drawdown debe convertirse en la variable dominante del módulo. Si una operación acerca demasiado la cuenta al límite de pérdida máxima, la calidad de señal deja de importar [cite:58][cite:73].

Esto exige que el motor calcule de forma permanente:
- distancia al trailing drawdown,
- distancia al DLL soft pause,
- riesgo remanente del día,
- riesgo remanente del ciclo de payout,
- margen contractual disponible bajo el max contracts [cite:58].

### 2. El módulo debe penalizar días heroicos
Aunque el Builder permite pasar en un día y no exige consistency rule en evaluación, luego sí exige consistencia del 50% para payouts sim [cite:58][cite:73]. Por eso no conviene que `05 funding` incentive jornadas explosivas que dejen una distribución de beneficios poco compatible con retiros futuros [cite:58][cite:83].

La consecuencia práctica es que el módulo debe introducir una penalización de tamaño cuando el PnL diario proyectado se concentra demasiado en una sola sesión o cuando la ganancia diaria excede un porcentaje excesivo del profit acumulado del ciclo [cite:58][cite:83].

### 3. News trading permitido no significa agresividad libre
Builder permite news trading, por lo que no corresponde un hard block automático por evento macro [cite:64][cite:86]. Sin embargo, el hecho de que esté permitido no elimina el riesgo de slippage ni el efecto del trailing drawdown [cite:58].

La especialización correcta es:
- no bloquear por noticia en sí misma;
- sí reducir tamaño en news si la volatilidad implícita, spreads o conflicto de régimen empeoran [cite:86][cite:89];
- sí endurecer el sizing cuando el estado de cuenta esté cerca del DLL o del trailing DD [cite:58].

## Arquitectura sugerida
### Submódulos internos de `05 funding`
Se recomienda que el módulo quede compuesto por cinco servicios internos:

1. `BuilderRuleEngine`: codifica reglas contractuales del plan [cite:58][cite:73].
2. `BuilderSurvivalEngine`: calcula riesgo de breach por operación, por día y por fase [cite:58].
3. `BuilderPayoutEngine`: monitorea buffer, consistencia, días calificantes y elegibilidad de payout [cite:58].
4. `BuilderSizingOverlay`: ajusta tamaño con base en estado de cuenta, riesgo remanente y fase [cite:58][cite:73].
5. `BuilderStateMachine`: administra transiciones EVAL → SIM → LIVE → COOLDOWN [cite:58].

Esta separación permite que la lógica quede testeable, auditable y mucho más clara para el IDE [cite:1][cite:58].

## Sizing especializado
### Fórmula propuesta
La fórmula genérica del roadmap debe reemplazarse, dentro de este módulo, por una variante especializada:

```python
allowed_risk_pct = min(
    base_risk_builder
    * F_signal
    * F_market_regime
    * F_builder_drawdown
    * F_builder_daily_buffer
    * F_builder_payout_consistency
    * F_builder_phase,
    builder_remaining_daily_risk_pct,
    builder_remaining_trailing_risk_pct,
    builder_contract_cap_risk_pct
)
```

### Factores específicos
| Factor | Función | Intención |
|---|---|---|
| `F_builder_drawdown` | Reduce tamaño al acercarse al trailing DD | Evitar breach estructural [cite:58][cite:73] |
| `F_builder_daily_buffer` | Reduce tamaño cerca del DLL soft pause | Proteger el día operativo [cite:58] |
| `F_builder_payout_consistency` | Penaliza concentración de PnL | Facilitar retiros consistentes [cite:58][cite:83] |
| `F_builder_phase` | Diferencia evaluación, sim y live | Ajustar agresividad según objetivo de fase [cite:58] |

### Política por fase
- En `EVAL_ACTIVE`, el sizing debe buscar progreso estable hacia $3,000 sin exponerse a pérdida irreversible del attempt [cite:73].
- En `SIM_ACTIVE`, el objetivo primario debe ser construir buffer y elegibilidad de payout, no crecer equity agresivamente [cite:58].
- En `LIVE_ACTIVE`, el módulo debe priorizar supervivencia y continuidad sobre velocidad de crecimiento, porque una breach activa cooldown de 21 días [cite:58].

## Payout como problema de control
El `BuilderPayoutEngine` debe incorporar reglas concretas:

- no solicitar payout si no se cumple profit neto mínimo de $500 [cite:58];
- no considerar payout-ready una cuenta que no cumple 2 días calificantes del ciclo [cite:58];
- no superar $2,000 por ciclo [cite:58][cite:64];
- monitorear consistencia del 50% como métrica viva, no solo al momento del retiro [cite:58][cite:83];
- mantener buffer por encima del umbral correspondiente ($2,100 default o $1,600 add-on) antes de liberar estado `SIM_PAYOUT_ELIGIBLE` [cite:58].

Este punto es crítico porque para Builder el dinero “ganado” y el dinero “retirable” no son la misma cosa [cite:58].

## Métricas que deben gobernar el módulo
Las métricas más importantes dejan de ser genéricas y pasan a ser Builder-native:

| Métrica | Descripción | Prioridad |
|---|---|---|
| `distance_to_trailing_dd` | Capital restante antes del breach | Crítica |
| `distance_to_dll_soft_pause` | Riesgo operativo intradía restante | Crítica |
| `eval_progress_pct` | Avance hacia $3,000 | Alta |
| `buffer_progress_pct` | Avance hacia payout buffer | Alta |
| `consistency_ratio_live` | Concentración de ganancias | Alta |
| `qualified_days_count` | Días válidos para payout | Alta |
| `payout_eligibility_state` | Estado operativo de retiro | Alta |
| `days_since_last_trade` | Riesgo de inactividad | Media |
| `phase_transition_readiness` | Probabilidad de paso a la fase siguiente | Alta |

Estas métricas deben desplazar otras más abstractas si entran en conflicto con las restricciones del plan [cite:58][cite:73].

## Reason codes nuevos
Se propone agregar reason codes específicos del Builder:

- `buildertrailingddcritical`
- `builderdailysoftpausethreat`
- `builderpayoutconsistencyrisk`
- `builderbuffernotreached`
- `builderqualifyingdaysmissing`
- `builderpayoutcapreached`
- `builderinactivityrisk`
- `builderlivecooldownactive`
- `buildercontractcapexceeded`
- `builderphasemismatch`

Estos códigos deben tener prioridad sobre códigos multi-firma más genéricos dentro del módulo [cite:58][cite:73].

## Cambios sugeridos al documento principal
### Reemplazo de alcance
Donde el documento actual habla de “FTMO Survival”, “Portfolio Risk multi-firma” o “Simulation multi-firma”, conviene introducir una variante explícita y dominante para el caso presente: `MFFU Builder Funding Core` [cite:1][cite:58].

### Renombrado funcional
Se recomienda renombrar internamente el módulo `05 funding` a:

```text
05 funding — MFFU Builder Performance & Payout Engine
```

Ese nombre obliga a que el equipo piense el módulo en términos de rendimiento útil para Builder, no como un gate abstracto de prop firms [cite:58][cite:73].

### Ajuste del dashboard
El panel principal debería mostrar, como primera línea:
- progreso a target de evaluación,
- distancia al trailing DD,
- riesgo intradía restante,
- progreso a payout buffer,
- consistencia actual,
- elegibilidad de retiro,
- fase actual de cuenta [cite:58].

Si el dashboard prioriza Sharpe, Conviction o señales antes que estos datos, el módulo estará visualmente mal enfocado para Builder [cite:1][cite:58].

## Implementación mínima viable
La MVP del módulo especializado debería incluir solo lo esencial:

1. Preset `MFFU_BUILDER_50K` [cite:58][cite:73].
2. State machine de fases [cite:58].
3. Risk engine con trailing DD, DLL soft pause y cap de contratos [cite:58].
4. Sizing overlay de fase y buffer [cite:58].
5. Payout engine con buffer, 50% consistency y tope por ciclo [cite:58][cite:83].
6. Dashboard con métricas Builder-native [cite:58].

Todo lo demás, incluida la abstracción multi-firma, debería quedar fuera de la primera versión [cite:1][cite:58].

## Conclusión
La especialización correcta del módulo `05 funding` no consiste en “agregar compatibilidad” con Builder, sino en **redefinir su función** alrededor del Builder Plan como primer objetivo real de fondeo [cite:58][cite:73]. Bajo esta lógica, el módulo deja de ser un wrapper de reglas de prop firm y pasa a ser un motor de supervivencia, consistencia y extracción de payouts específicamente adaptado a My Funded Futures [cite:58][cite:83].

El resultado buscado no es solo aprobar la evaluación, sino construir un sistema que produzca rendimientos operables dentro de las restricciones reales del plan: aprobar, sostener buffer, pedir payouts válidos y llegar al live sin breach innecesario [cite:58][cite:73]. Esa debe ser la definición operativa de éxito para `05 funding` en esta etapa del proyecto [cite:58].
