from abc import ABC, abstractmethod
import pandas as pd
import numpy as np

class BaseStrategy(ABC):
    """
    Abstract base class for all backtesting strategies.
    Every strategy must generate an array of signals (1=Long, -1=Short, 0=Flat).
    """

    def __init__(self, name: str, params: dict = None):
        self.name = name
        self.params = params or {}

    @abstractmethod
    def generate_signals(self, data: pd.DataFrame, **kwargs) -> pd.Series:
        """
        Takes OHLCV dataframe (and potentially sentiment).
        Returns a pandas Series of signals (-1, 0, or 1) aligned with the dataframe index.

        The signal indicates the desired position AT THE CLOSE of the bar.
        (The execution will happen at the *next* open or close, defined in the engine)
        """
        pass
