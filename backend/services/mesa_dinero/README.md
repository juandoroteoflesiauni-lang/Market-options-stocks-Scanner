# Mesa de Dinero Virtual

Sistema de trading institucional avanzado con orquestación multi-agente y análisis predictivo en tiempo real.

## Arquitectura

El sistema está dividido en tres capas principales:

### A. CORE BACKEND: Orquestador de Agentes

Implementa un patrón de orquestador que coordina múltiples agentes LLM especializados, ingiriendo datos de manera no bloqueante de todos los motores (Técnico, Fundamental, Opciones) y generando tesis de inversión complejas con streaming en tiempo real.

**Características:**
- Orquestación de agentes LLM especializados
- Ingesta paralela de datos de múltiples fuentes
- Caché en memoria y Redis para optimización de rendimiento
- Streaming de respuestas en tiempo real (SSE/WebSockets)
- Optimización de tokens para minimizar costos de inferencia

### B. REPORT FACTORY: Fábrica de Informes

Patrón Factory para crear diferentes tipos de informes especializados:
- Análisis Técnico
- Opciones (GEX/Gamma)
- Fundamental
- Predictivo
- Sentimiento de Mercado
- Riesgo Soberano (Argentina)

**Características:**
- Estructura de clases abstracta para diferentes tipos de informes
- Validación de integridad de datos
- Metadatos de informes para trazabilidad
- Sistema de generación de informes especializados

### C. FRONTEND STREAMING: UI/UX Profesional

Dashboard tipo terminal de Bloomberg con:
- Panel de Control de Agentes
- Panel de Fuentes de Datos
- Lienzo de Tesis con streaming en tiempo real
- Indicadores de progreso y estado del sistema

**Características:**
- Server-Sent Events (SSE) para streaming en tiempo real
- WebSockets para comunicación bidireccional
- UI modular con paneles intercambiables
- Indicadores de rendimiento y estado del sistema

## Instalación

```bash
# Instalar dependencias
pip install -r requirements.txt

# Iniciar el servidor
uvicorn app:app --host 0.0.0.0 --port 8000 --reload
```

## Uso

1. Iniciar el servidor API
2. Acceder a `http://localhost:8000/mesa-dinero`
3. Seleccionar un símbolo para analizar
4. Iniciar análisis con el botón de play
5. Observar el streaming en tiempo real de la tesis institucional

## Endpoints API

- `POST /api/v1/mesa-dinero/thesis-stream/{symbol}` - Iniciar stream de tesis
- `GET /api/v1/mesas-dinero/stream/{stream_id}` - Streaming de eventos SSE
- `POST /api/v1/mesa-dinero/generate-thesis/{symbol}` - Generar tesis institucional
- `POST /api/v1/mesa-dinero/report/{report_type}/{symbol}` - Generar informe especializado

## Requisitos

- Python 3.9+
- FastAPI
- Redis (opcional, para caché)
- LLM API keys (GitHub Models, Gemini, Azure OpenAI)
- Dependencias listadas en requirements.txt
