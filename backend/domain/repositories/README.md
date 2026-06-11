# Domain Repositories - Clean Architecture Implementation

## 📋 Resumen

Este módulo implementa el **Repository Pattern** para aislar el Domain Layer de las implementaciones concretas de persistencia, siguiendo los principios de **Clean Architecture**.

## 🎯 Beneficios

1. **Aislamiento del Domain Layer**: El dominio no depende de implementaciones externas (FMP, Polygon, etc.)
2. **Testeabilidad**: Tests unitarios sin conexión a APIs externas
3. **Intercambiabilidad**: Cambiar de proveedor sin modificar la lógica de negocio
4. **Múltiples implementaciones**: Soporte para redundancia (ej: FMP + fallback a Polygon)

## 📁 Estructura

```
backend/
├── domain/
│   └── repositories/
│       ├── price_repository.py       # Interfaz abstracta (Domain)
│       └── test_price_repository.py  # Tests unitarios
├── infrastructure/
│   └── repositories/
│       └── fmp_price_repository.py   # Implementación concreta (Infrastructure)
└── services/
    └── technical_terminal_payload.py # Servicio que usa la repositorio
```

## 🔧 Uso

### 1. Definir la Interfaz (Domain Layer)

```python
from domain.repositories.price_repository import PriceRepository

class MyRepository(PriceRepository):
    async def get_historical_prices(self, symbol: str, ...) -> List[FMPHistoricalPrice]:
        # Tu implementación
        pass
```

### 2. Inyectar en el Servicio

```python
from infrastructure.repositories.fmp_price_repository import FMPPriceRepository
from layer_1_data.fetchers.fmp_client import FMPClient

# En el router o punto de entrada
fmp_client = FMPClient()
price_repo = FMPPriceRepository(fmp_client)

# Inyectar en el servicio
payload = await build_technical_terminal_payload(
    symbol="AAPL",
    days=100,
    price_repo=price_repo  # ✅ Inyección explícita
)
```

### 3. Testear con Mocks

```python
from domain.repositories.price_repository import PriceRepository

class MockPriceRepository(PriceRepository):
    async def get_historical_prices(self, symbol: str, ...) -> List[FMPHistoricalPrice]:
        return self._mock_data

# Test sin conexión a FMP
mock_repo = MockPriceRepository(mock_data)
result = await build_technical_terminal_payload(
    symbol="AAPL",
    price_repo=mock_repo  # ✅ Mock para testing
)
```

## 🧪 Tests

Ejecutar tests unitarios:

```bash
cd backend
pytest domain/repositories/test_price_repository.py -v
```

## 📊 Ejemplo de Implementación

### Interfaz Abstracta

```python
from abc import ABC, abstractmethod

class PriceRepository(ABC):
    @abstractmethod
    async def get_historical_prices(
        self,
        symbol: str,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
    ) -> List[FMPHistoricalPrice]:
        pass
```

### Implementación Concreta

```python
class FMPPriceRepository(PriceRepository):
    def __init__(self, fmp_client: FMPClient):
        self._client = fmp_client

    async def get_historical_prices(self, symbol: str, ...) -> List[FMPHistoricalPrice]:
        try:
            return await self._client.get_historical_prices(symbol)
        except Exception as e:
            raise RepositoryError("Failed to fetch", "FMP", e)
```

## 🚨 Manejo de Errores

El módulo define errores específicos para cada escenario:

- `RepositoryError`: Error genérico de infraestructura
- `RateLimitError`: Rate limit del proveedor (reintentar con backoff)
- `AuthenticationError`: API key inválida (alertar al operador)

## 📚 Referencias

- **Clean Architecture**: Robert C. Martin
- **Repository Pattern**: Martin Fowler
- **Dependency Injection**: Michael Seemann

## 🔍 Próximos Pasos

1. ✅ Implementado: `PriceRepository` para datos históricos
2. 🔄 Pendiente: `QuoteRepository` para quotes en tiempo real
3. 🔄 Pendiente: `PolygonPriceRepository` como alternativa a FMP
4. 🔄 Pendiente: Cache decorator para repositorios
