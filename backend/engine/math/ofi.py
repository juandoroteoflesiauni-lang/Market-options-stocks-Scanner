class OFIMath:
    """Stateless math operations for Order Flow Imbalance (OFI)."""

    @staticmethod
    def compute_delta_bid(
        prev_bid_price: float,
        prev_bid_size: float,
        curr_bid_price: float,
        curr_bid_size: float,
        epsilon: float = 1e-9,
    ) -> float:
        """Bid contribution per Cont et al. equation 2."""
        diff = curr_bid_price - prev_bid_price
        if diff > epsilon:
            return curr_bid_size
        if diff < -epsilon:
            return -prev_bid_size
        return curr_bid_size - prev_bid_size

    @staticmethod
    def compute_delta_ask(
        prev_ask_price: float,
        prev_ask_size: float,
        curr_ask_price: float,
        curr_ask_size: float,
        epsilon: float = 1e-9,
    ) -> float:
        """Ask contribution per Cont et al. equation 2."""
        diff = curr_ask_price - prev_ask_price
        if diff < -epsilon:
            return curr_ask_size
        if diff > epsilon:
            return -prev_ask_size
        return curr_ask_size - prev_ask_size

    @staticmethod
    def compute_raw_ofi(delta_bid: float, delta_ask: float) -> float:
        return delta_bid - delta_ask
