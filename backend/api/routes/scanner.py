"""Scanner candidates endpoint — Phase A results."""

import random

from fastapi import APIRouter

from backend.api.contracts import CandidateResponse, EngineSignalResponse, GreeksResponse

router = APIRouter(prefix="/api/scanner", tags=["scanner"])


def generate_mock_sparkline() -> list[float]:
    """Generates a random walk sparkline with 30 points."""
    points = [100.0]
    for _ in range(29):
        change = random.uniform(-2.0, 2.0)
        points.append(round(points[-1] + change, 2))
    return points


@router.get("/candidates", response_model=list[CandidateResponse])
async def get_candidates() -> list[CandidateResponse]:
    """Returns current Phase A scanner candidates.

    TODO: Wire to real Phase A scanner output when the scanner is running.
    Currently returns a rich mocked dataset for frontend Dashboard integration.
    """
    tickers = ["AAPL", "MSFT", "GOOGL", "AMZN", "META", "NVDA", "TSLA", "MSTR", "COIN"]

    candidates = []
    for ticker in tickers:
        base_price = random.uniform(50, 600)
        price_change = random.uniform(-10, 15)
        is_bull = price_change > 0

        candidates.append(
            CandidateResponse(
                symbol=ticker,
                price=f"{base_price:.2f}",
                priceChange=f"{'+' if is_bull else ''}{price_change:.2f}",
                priceChangePct=f"{'+' if is_bull else ''}{(price_change/base_price)*100:.2f}%",
                volume=f"{random.randint(1_000_000, 50_000_000)}",
                avgVolume=f"{random.randint(10_000_000, 60_000_000)}",
                iv=f"{random.uniform(15, 80):.1f}%",
                ivRank=f"{random.uniform(0, 100):.0f}",
                phase=random.choice(["A", "B", "C", "D"]),
                momentum=f"{random.uniform(-100, 100):.1f}",
                sparkline=generate_mock_sparkline(),
                greeks=GreeksResponse(
                    delta=f"{random.uniform(0.1, 0.9):.2f}",
                    gamma=f"{random.uniform(0.01, 0.05):.3f}",
                    theta=f"{random.uniform(-0.5, -0.01):.2f}",
                    vega=f"{random.uniform(0.05, 0.3):.2f}",
                ),
                signals=[
                    EngineSignalResponse(
                        engineName="RSI Composite",
                        value=f"{random.randint(20, 80)}",
                        direction="BULL" if is_bull else "BEAR",
                        weight=20,
                    ),
                    EngineSignalResponse(
                        engineName="MACD Div",
                        value=f"{random.uniform(-1, 1):.2f}",
                        direction="BULL" if random.random() > 0.5 else "BEAR",
                        weight=15,
                    ),
                ],
            )
        )

    return candidates
