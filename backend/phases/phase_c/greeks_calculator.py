"""Calculadora de Greeks como wrapper del motor BSM existente.

Proporciona una interfaz simplificada para calcular Greeks de opciones
usando el motor BlackScholesPricer de src/quant_engine/math/options/bsm.py.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from backend.models.result import Result


class GreeksResult(BaseModel):
    """Resultado del cálculo de Greeks para un contrato individual."""

    model_config = ConfigDict(frozen=True)

    delta: float = Field(ge=-1.0, le=1.0)
    gamma: float = Field(ge=0.0)
    theta: float
    vega: float = Field(ge=0.0)
    rho: float
    vanna: float = Field(default=0.0)
    charm: float = Field(default=0.0)
    theoretical_price: float = Field(ge=0.0)
    implied_volatility: float = Field(ge=0.0)


class GreeksCalculator:
    """Calculadora de Greeks usando BSM de src/quant_engine.

    Esta clase actúa como bridge entre el modelo OptionContract del dominio
    y el motor matemático BSM existente en el código base.
    """

    def calculate(
        self,
        spot: float,
        strike: float,
        tte_years: float,
        risk_free_rate: float,
        iv: float,
        option_type: Literal["CALL", "PUT"],
    ) -> Result[GreeksResult]:
        """Calcula todos los Greeks para un contrato individual.

        Args:
            spot: Precio actual del underlying.
            strike: Precio de ejercicio.
            tte_years: Tiempo hasta expiración en años.
            risk_free_rate: Tasa libre de riesgo.
            iv: Volatilidad implícita.
            option_type: Tipo de opción (CALL o PUT).

        Returns:
            Result con GreeksResult o razón de fallo.
        """
        try:
            from src.quant_engine.math.options.bsm import BlackScholesPricer, OptionType

            opt = OptionType.CALL if option_type == "CALL" else OptionType.PUT

            price = BlackScholesPricer.price(spot, strike, tte_years, risk_free_rate, iv, opt)
            delta = BlackScholesPricer.delta(spot, strike, tte_years, risk_free_rate, iv, opt)
            gamma = BlackScholesPricer.gamma(spot, strike, tte_years, risk_free_rate, iv)
            vega = BlackScholesPricer.vega(spot, strike, tte_years, risk_free_rate, iv)
            theta = BlackScholesPricer.theta(spot, strike, tte_years, risk_free_rate, iv, opt)
            rho = BlackScholesPricer.rho(spot, strike, tte_years, risk_free_rate, iv, opt)

            greeks_dict = BlackScholesPricer.greeks(
                spot, strike, tte_years, risk_free_rate, iv, opt, second_order=True
            )
            vanna = greeks_dict.get("vanna", 0.0)
            charm = greeks_dict.get("charm", 0.0)

            return Result.success(
                GreeksResult(
                    delta=delta,
                    gamma=gamma,
                    theta=theta,
                    vega=vega,
                    rho=rho,
                    vanna=vanna,
                    charm=charm,
                    theoretical_price=price,
                    implied_volatility=iv,
                )
            )
        except Exception as e:
            return Result.failure(reason=f"Greeks calculation failed: {e}")

    def calculate_iv(
        self,
        market_price: float,
        spot: float,
        strike: float,
        tte_years: float,
        risk_free_rate: float,
        option_type: Literal["CALL", "PUT"],
    ) -> Result[float]:
        """Calcula la volatilidad implícita dado un precio de mercado.

        Args:
            market_price: Precio observado en el mercado.
            spot: Precio actual del underlying.
            strike: Precio de ejercicio.
            tte_years: Tiempo hasta expiración en años.
            risk_free_rate: Tasa libre de riesgo.
            option_type: Tipo de opción (CALL o PUT).

        Returns:
            Result con IV o razón de fallo.
        """
        try:
            from src.quant_engine.math.options.bsm import BlackScholesPricer, OptionType

            opt = OptionType.CALL if option_type == "CALL" else OptionType.PUT
            iv = BlackScholesPricer.implied_vol(
                market_price, spot, strike, tte_years, risk_free_rate, opt
            )

            if iv != iv:
                return Result.failure(reason="IV solver returned NaN (no valid solution)")

            return Result.success(iv)
        except Exception as e:
            return Result.failure(reason=f"IV calculation failed: {e}")

    def calculate_batch(
        self,
        spot: float,
        contracts: list[dict[str, float]],
        risk_free_rate: float = 0.05,
    ) -> Result[list[GreeksResult]]:
        """Calcula Greeks para un lote de contratos.

        Args:
            spot: Precio actual del underlying.
            contracts: Lista de dicts con keys: strike, tte_years, iv, option_type.
            risk_free_rate: Tasa libre de riesgo.

        Returns:
            Result con lista de GreeksResult o razón de fallo.
        """
        results: list[GreeksResult] = []
        for contract in contracts:
            calc_result = self.calculate(
                spot=spot,
                strike=contract["strike"],
                tte_years=contract["tte_years"],
                risk_free_rate=risk_free_rate,
                iv=contract["iv"],
                option_type=contract.get("option_type", "CALL"),
            )
            if calc_result.is_failure:
                return Result.failure(
                    reason=f"Batch failed at strike {contract['strike']}: {calc_result.reason}"
                )
            results.append(calc_result.unwrap())

        return Result.success(results)
