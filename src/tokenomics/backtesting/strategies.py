"""backtesting.py Strategy classes for V2/V3/V4 signal replay."""

from backtesting import Strategy


class ScoreSignalStrategy(Strategy):
    """Universal signal-driven strategy for score-based portfolio backtests.

    Expects a "Signal" column in the OHLCV DataFrame (1.0 = long, 0.0 = flat).
    Buys with full position size on the first bar where signal=1, closes on signal=0.

    This is intentionally simple: the goal is to benchmark price performance of
    the score-selected universe, not to model fancy execution.
    """

    def init(self) -> None:
        # Register the signal as an indicator so backtesting.py tracks it
        self.sig = self.I(lambda x: x, self.data.Signal, name="Signal", overlay=False)

    def next(self) -> None:
        in_portfolio = self.sig[-1] > 0

        if in_portfolio and not self.position:
            self.buy()
        elif not in_portfolio and self.position:
            self.position.close()
