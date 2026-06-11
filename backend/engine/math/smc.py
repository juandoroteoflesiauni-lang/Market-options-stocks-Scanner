class SMCMath:
    """Stateless math operations for Smart Money Concepts (SMC)."""

    @staticmethod
    def infer_bias(current_price: float, sma_20: float) -> str:
        """
        Infers directional bias.
        In a real implementation this would use vectors, order blocks, FVG, etc.
        For now, this provides a basic placeholder logic based on price vs SMA.
        """
        if current_price > sma_20 * 1.01:
            return "BULLISH"
        elif current_price < sma_20 * 0.99:
            return "BEARISH"
        return "NEUTRAL"
