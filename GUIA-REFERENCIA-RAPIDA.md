# 📚 GUÍA DE REFERENCIA RÁPIDA
## Deep Trading Terminal — Para el Usuario (Cero Código)

> 💡 Imprimí esta guía y tenela cerca mientras programás con IA.
> Todo lo que necesitás saber sin tocar una sola línea de código.

---

## 🚀 RITUAL DE INICIO DE SESIÓN

Hacer **siempre en este orden** al comenzar:

```
Paso 1: Abrir el IDE (Cursor o VS Code)
Paso 2: Decirle a la IA: "Lee CLAUDE.md y PROJECT_CONFIG.md. ¿En qué estamos?"
Paso 3: La IA te dirá cuánto hay hecho y qué sigue. Confirmás.
Paso 4: Decís tu objetivo del día en UNA sola oración.
Paso 5: La IA hace el Blueprint (plan). Vos lo aprobás ANTES de que empiece.
```

---

## 💬 ARSENAL DE PROMPTS — Copiar, pegar y completar

### Para iniciar cualquier sesión
```
"Lee CLAUDE.md y PROJECT_CONFIG.md.
¿Cuántos módulos están completos y qué sigue?"
```

### Para empezar una nueva funcionalidad
```
"Quiero implementar [nombre de la función].
Primero mostrá el Blueprint completo para que lo revise.
NO escribas código todavía."
```

### Para continuar donde quedamos
```
"Lee CLAUDE.md y PROJECT_CONFIG.md.
¿Dónde quedamos? Continuemos con [tarea pendiente].
Empezá buscando los comentarios CHECKPOINT en el código."
```

### Para un módulo complejo que no entendés bien
```
"Necesito implementar [módulo].
Primero explicame en términos simples, sin jerga técnica,
cómo va a funcionar y por qué lo necesitamos.
Después hacemos el Blueprint para aprobarlo."
```

### Para corregir un error
```
"Tengo este error: [pegar error COMPLETO con todo el texto]
¿Qué lo causa exactamente?
¿Cuál es la solución más simple y segura?"
```

### Para entender código existente
```
"Explicame en términos simples, sin jerga técnica,
qué hace el archivo [nombre del archivo],
por qué existe y cómo encaja en el sistema."
```

### Para agregar una función nueva
```
"Quiero que cuando [acción del usuario],
la aplicación [resultado esperado].
¿Qué necesitamos crear? Primero el Blueprint, sin código."
```

### Para revisar el progreso general
```
"¿Cuántos módulos están completos?
¿Qué falta para tener el sistema funcionando en testnet?"
```

### Para hacer un checkpoint al final de sesión
```
"Llegamos a un buen punto.
Actualizá PROJECT_CONFIG.md con el estado actual,
agregá los comentarios CHECKPOINT donde corresponde,
y proponé el mensaje del commit."
```

### Para pedir una explicación más simple
```
"No entendí eso. Explicamelo como si nunca hubiera
programado en mi vida. Usá una analogía del mundo real."
```

### Para validar que algo funciona antes de seguir
```
"Antes de continuar, corramos los tests para confirmar
que lo que hicimos anda bien. ¿Qué comando uso?"
```

### Para conectar con el exchange real (testnet)
```
"Quiero probar la conexión al exchange en testnet.
¿Qué configuraciones necesito en el .env?
Guiame paso a paso sin que toque código."
```

---

## ⚠️ SEÑALES DE ALARMA — Detenerse inmediatamente

```
🔴 La IA quiere modificar más de 3 archivos a la vez
→ "Para. Hagamos de a un archivo. Empezamos por el más importante."

🔴 La IA usa una librería nueva que no estaba en el proyecto
→ "¿Por qué necesitamos esta librería nueva?
   ¿No hay forma de hacerlo con lo que ya tenemos?"

🔴 La IA dice "esto puede romper el sistema" o menciona algún riesgo
→ ANTES de aceptar: git add . && git commit -m "backup: antes de cambio riesgoso"

🔴 Los tests están fallando
→ "STOP. Arreglemos los tests primero antes de avanzar."

🔴 La IA propone cambiar la arquitectura del sistema
→ "Para completamente. Necesito un Blueprint separado para revisar ese cambio."

🔴 La IA empieza a escribir código sin haber mostrado el Blueprint
→ "Para. Primero mostrá el plan completo para que lo apruebe. Después el código."

🔴 La IA dice "voy a simplificar esto" sin que se lo pediste
→ "¿Qué exactamente vas a simplificar? ¿Qué se pierde? Explicame primero."

🔴 La IA habla de cambiar la base de datos o el modelo de datos
→ "Para totalmente. Eso requiere una sesión dedicada con Blueprint aprobado."

🔴 Ves números raros en los cálculos de dinero (muchos decimales)
→ "Estos números no se ven bien. ¿Estamos usando Decimal o float para esto?"

🔴 La sesión lleva más de 10 intercambios sin un checkpoint
→ "Checkpoint: resumí qué construimos y qué sigue antes de continuar."
```

---

## 🔑 COMANDOS ESENCIALES

```bash
# ════════════════════════════════════════════════════════════
# BACKEND (Python) — correr desde la carpeta backend/
# ════════════════════════════════════════════════════════════
cd backend
source venv/bin/activate          # SIEMPRE activar antes de cualquier cosa
pip install -r requirements.txt   # Solo si hay dependencias nuevas
uvicorn app.main:app --reload     # Iniciar el servidor (ve cambios en tiempo real)
pytest tests/ -v                  # Correr TODOS los tests
pytest tests/test_trading.py -v   # Tests de un archivo específico
pytest tests/ -v -k "pnl"        # Tests que contengan "pnl" en el nombre

# ════════════════════════════════════════════════════════════
# FRONTEND (TypeScript) — correr desde la carpeta frontend/
# ════════════════════════════════════════════════════════════
cd frontend
npm install                       # Solo si hay dependencias nuevas
npm run dev                       # Iniciar servidor de desarrollo
npm run test                      # Correr tests
npm run build                     # Verificar que compila (antes de producción)
npm run lint                      # Verificar calidad del código

# ════════════════════════════════════════════════════════════
# BASE DE DATOS — Docker
# ════════════════════════════════════════════════════════════
docker-compose up -d postgres redis   # Iniciar PostgreSQL y Redis
docker-compose down                   # Detener todo
docker-compose ps                     # Ver estado de los contenedores
alembic upgrade head                  # Aplicar migraciones pendientes
alembic downgrade -1                  # Deshacer la última migración (emergencia)

# ════════════════════════════════════════════════════════════
# GIT — Control de versiones
# ════════════════════════════════════════════════════════════
git status                            # Ver qué cambió
git add .                             # Agregar todos los cambios
git commit -m "feat: descripción"     # Guardar con mensaje claro
git commit -m "fix: descripción"      # Para corrección de bugs
git push                              # Subir al repositorio remoto
git log --oneline -10                 # Ver los últimos 10 commits
git diff                              # Ver exactamente qué cambió

# GIT — Emergencias
git stash                             # Guardar cambios y volver al estado limpio
git stash pop                         # Recuperar los cambios guardados
git stash drop                        # Descartar los cambios guardados
git checkout -- .                     # ⚠️ DESHACER TODOS los cambios no commiteados

# ════════════════════════════════════════════════════════════
# CALIDAD — Correr antes de CADA commit
# ════════════════════════════════════════════════════════════
pre-commit run --all-files            # Verificar calidad de todo el código
```

---

## 📁 ARCHIVOS CLAVE — Para qué sirve cada uno

| Archivo | Para qué sirve | Quién lo edita |
|---------|---------------|---------------|
| `CLAUDE.md` | 🤖 Reglas y contexto para la IA | Solo la IA (o vos con la IA) |
| `PROJECT_CONFIG.md` | 📊 Estado actual y módulos | La IA al fin de cada sesión |
| `WORKFLOW_STATE.md` | 📍 Tarea exacta donde quedamos | La IA al fin de cada sesión |
| `.env` o `.env.local` | 🔑 API keys y contraseñas | Solo vos (NUNCA subir a Git) |
| `.env.local.example` | 📋 Template de variables | Solo como referencia |
| `CHANGELOG.md` | 📝 Todo lo que se fue haciendo | La IA al fin de cada sesión |
| `requirements.txt` | 📦 Dependencias Python | La IA cuando agrega librerías |
| `frontend/package.json` | 📦 Dependencias JavaScript | La IA cuando agrega librerías |
| `docker-compose.yml` | 🐳 PostgreSQL y Redis | Raramente se toca |
| `pyproject.toml` | ⚙️ Herramientas Python | Raramente se toca |

---

## 🌐 URLs DEL SISTEMA (cuando el servidor está corriendo)

| Qué | URL | Para qué |
|-----|-----|---------|
| Frontend / Dashboard | `http://localhost:3000` | La interfaz visual |
| Backend API | `http://localhost:8000` | El servidor de datos |
| **API Docs Interactiva** | `http://localhost:8000/docs` | ⭐ Probar endpoints manualmente |
| API Docs Alternativa | `http://localhost:8000/redoc` | Documentación legible |
| pgAdmin (DB visual) | `http://localhost:5050` | Ver datos en la base |

---

## 📞 GLOSARIO DE TRADING

| Término | Significado en palabras simples |
|---------|--------------------------------|
| **Symbol / Ticker** | Par de trading (ej: BTC/USDT, ETH/USD, AAPL) |
| **Long** | Comprás apostando a que el precio sube |
| **Short** | Vendés apostando a que el precio baja |
| **Stop Loss** | Precio límite automático para no perder más de lo planeado |
| **Take Profit** | Precio objetivo donde asegurás la ganancia |
| **P&L** | Profit & Loss = Ganancia o pérdida de una operación |
| **Spread** | Diferencia entre precio de compra y precio de venta |
| **Order Book** | Lista de todas las órdenes pendientes del mercado |
| **Tick** | Una sola actualización de precio en tiempo real |
| **Candle / Vela** | Barra OHLC: Apertura, Máximo, Mínimo, Cierre |
| **Backtest** | Probar estrategia con datos históricos (sin dinero real) |
| **Leverage** | Apalancamiento: operar con más capital del que tenés |
| **Liquidity** | Cuánto volumen hay (alto = fácil entrar y salir sin mover el precio) |
| **VPIN** | Indicador de flujo de órdenes informadas (señal de movimiento grande) |
| **OFI** | Order Flow Imbalance = Desequilibrio compra/venta (señal direccional) |
| **Options Chain** | Tabla de todos los contratos de opciones disponibles |
| **Strike** | Precio objetivo de un contrato de opciones |
| **Expiry** | Fecha de vencimiento del contrato |
| **Greeks** | Delta, Gamma, Theta, Vega — métricas de riesgo de opciones |
| **Testnet** | Red de prueba del exchange (dinero ficticio, sin riesgo real) |
| **API Key** | Clave de acceso al exchange (tratar exactamente como contraseña) |
| **Decimal** | Tipo de número exacto en código (evita errores de float con dinero) |
| **WebSocket** | Conexión en tiempo real (como un canal de noticias continuo) |
| **Rate Limit** | Límite de llamadas permitidas por minuto a la API del exchange |
| **Pydantic** | Librería Python que valida y estructura los datos automáticamente |
| **Docker** | Programa que crea entornos aislados para la base de datos |
| **Migración** | Cambio controlado en la estructura de la base de datos |

---

## 🆘 PROCEDIMIENTOS DE EMERGENCIA

### Tests fallando — NO avanzar
```
1. Copiar el error completo de los tests
2. Decirle a la IA:
   "Los tests de [archivo] están fallando. Error: [pegar error]
   Arreglemos SOLO eso antes de continuar con lo que estábamos."
```

### El servidor no arranca
```
1. Copiar todos los mensajes de error
2. Decirle a la IA:
   "El servidor no arranca. Error: [pegar error completo]
   ¿Qué falta configurar o qué está mal?"
```

### Algo se rompió y no sabés qué
```
git stash          # Los cambios se guardan, el código vuelve al último commit
                   # Si el error desaparece → los cambios del stash causaban el problema
git stash pop      # Recuperar los cambios para revisarlos con la IA
```

### La IA hizo demasiados cambios a la vez y perdiste el control
```
1. NO hagas más cambios
2. git status          → Para ver qué archivos cambiaron
3. Decirle a la IA:
   "Perdí el hilo de todos estos cambios.
   Explicame archivo por archivo qué cambiaste y por qué."
4. Si querés empezar de cero desde el último commit:
   git checkout -- .   (deshace TODOS los cambios no commiteados)
```

### Las migraciones de base de datos fallaron
```
1. No borres nada todavía
2. Decirle a la IA:
   "La migración falló con este error: [error]
   ¿Cómo revertimos sin perder datos?"
3. Si vas a intentar algo → hacer backup primero
```

### Antes de cualquier cambio grande — crear punto de retorno
```
git add . && git commit -m "backup: antes de [descripción del cambio]"
# Esto crea un punto seguro al que siempre podés volver
```

---

## 📊 CHEAT SHEET DE SESIÓN

```
╔══════════════════════════════════════════════════════════════╗
║  INICIO:      "Lee CLAUDE.md y PROJECT_CONFIG.md. ¿Dónde   ║
║               estamos?"                                      ║
╠══════════════════════════════════════════════════════════════╣
║  PLANEAR:     "Antes de código, mostrá el Blueprint.        ║
║               No escribas nada todavía."                     ║
╠══════════════════════════════════════════════════════════════╣
║  APROBAR:     "Ok, el plan está bien. Empezamos por         ║
║               [primer archivo]."                             ║
╠══════════════════════════════════════════════════════════════╣
║  REVISAR:     "Corremos los tests antes de continuar."      ║
╠══════════════════════════════════════════════════════════════╣
║  CHECKPOINT:  "Actualizá PROJECT_CONFIG.md con el estado." ║
╠══════════════════════════════════════════════════════════════╣
║  CERRAR:      "Hacemos commit y listás los CHECKPOINT."     ║
╚══════════════════════════════════════════════════════════════╝
```
