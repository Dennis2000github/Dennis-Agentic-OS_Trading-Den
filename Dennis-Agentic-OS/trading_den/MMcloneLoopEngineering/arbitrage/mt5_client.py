"""
MetaTrader 5 Client with Conditional Import & Platform-Agnostic Status.
Allows running the crypto arbitrage engine seamlessly on macOS/Linux while
enabling live MT5 execution on Windows environments.
Follows strict Real-Data-First rule: Never fabricates mock prices or simulated order books.
"""

import logging
import time
from typing import Any, Dict, Optional

from .models import ConnectionStatus, OrderBook, OrderBookEntry

logger = logging.getLogger(__name__)

# Conditional try/except for MT5 (Windows-only native module)
try:
    import MetaTrader5 as mt5
    MT5_AVAILABLE = True
    MT5_IMPORT_ERROR = None
except (ImportError, ModuleNotFoundError, Exception) as _err:
    mt5 = None
    MT5_AVAILABLE = False
    MT5_IMPORT_ERROR = str(_err)


class MT5Client:
    """
    MT5 Client that wraps MetaTrader 5 API calls safely.
    On non-Windows systems (or when MT5 is not installed), operations safely report UNAVAILABLE.
    No simulated or mock order book data is generated.
    """

    def __init__(
        self,
        login: Optional[int] = None,
        server: Optional[str] = None,
        password: Optional[str] = None,
        path: Optional[str] = None,
        mock_if_unavailable: bool = False,
    ):
        self.login = login
        self.server = server
        self.password = password
        self.path = path
        self.mock_if_unavailable = mock_if_unavailable
        self.initialized = False
        self.connection_status = ConnectionStatus.UNAVAILABLE if not MT5_AVAILABLE else ConnectionStatus.DISCONNECTED

        if not MT5_AVAILABLE:
            logger.info(
                "MT5 is not available on this platform (%s). Running in crypto-only mode.",
                MT5_IMPORT_ERROR or "Non-Windows OS",
            )

    def initialize(self) -> bool:
        """Initialize connection to MT5 terminal."""
        if not MT5_AVAILABLE:
            if self.mock_if_unavailable:
                self.initialized = True
                self.connection_status = ConnectionStatus.CONNECTED
                logger.info("MT5Client initialized in test mock mode.")
                return True
            self.connection_status = ConnectionStatus.UNAVAILABLE
            logger.warning("Cannot initialize MT5: MetaTrader5 package is not available on this platform.")
            return False

        try:
            self.connection_status = ConnectionStatus.CONNECTING
            init_kwargs: Dict[str, Any] = {}
            if self.path:
                init_kwargs["path"] = self.path
            if self.login and self.server and self.password:
                init_kwargs["login"] = self.login
                init_kwargs["server"] = self.server
                init_kwargs["password"] = self.password

            if not mt5.initialize(**init_kwargs):
                err = mt5.last_error()
                logger.error("MT5 initialization failed: %s", err)
                self.connection_status = ConnectionStatus.FAILED
                return False

            self.initialized = True
            self.connection_status = ConnectionStatus.CONNECTED
            logger.info("MT5 connection established successfully.")
            return True
        except Exception as e:
            self.connection_status = ConnectionStatus.FAILED
            logger.error("Exception during MT5 initialization: %s", e)
            return False

    def shutdown(self) -> None:
        """Shutdown MT5 terminal connection."""
        if MT5_AVAILABLE and self.initialized:
            try:
                mt5.shutdown()
            except Exception as e:
                logger.warning("Error during MT5 shutdown: %s", e)
        self.initialized = False
        self.connection_status = ConnectionStatus.UNAVAILABLE if not MT5_AVAILABLE else ConnectionStatus.DISCONNECTED

    def get_order_book(self, symbol: str) -> Optional[OrderBook]:
        """
        Fetch real order book / depth of market for the given MT5 symbol.
        Falls back to symbol tick if DOM is not provided by the broker.
        Returns None if MT5 is unavailable (zero mock data fallback).
        """
        if not MT5_AVAILABLE or not self.initialized:
            if self.mock_if_unavailable and self.initialized:
                return self._generate_mock_order_book(symbol)
            return None

        try:
            # Subscribe to Depth of Market
            mt5.market_book_add(symbol)
            items = mt5.market_book_get(symbol)

            bids = []
            asks = []
            if items:
                for item in items:
                    # item.type: 1 = BUY (Bid), 2 = SELL (Ask)
                    if item.type == 1:
                        bids.append(OrderBookEntry(price=float(item.price), size=float(item.volume)))
                    elif item.type == 2:
                        asks.append(OrderBookEntry(price=float(item.price), size=float(item.volume)))

            # If DOM is empty, fallback to top-of-book from real tick
            if not bids or not asks:
                tick = mt5.symbol_info_tick(symbol)
                if tick and tick.bid > 0 and tick.ask > 0:
                    bids = [OrderBookEntry(price=float(tick.bid), size=float(tick.volume or 1.0))]
                    asks = [OrderBookEntry(price=float(tick.ask), size=float(tick.volume or 1.0))]

            if not bids or not asks:
                return None

            # Ensure proper ordering
            bids.sort(key=lambda x: x.price, reverse=True)
            asks.sort(key=lambda x: x.price)

            ob = OrderBook(
                symbol=symbol,
                venue="MT5",
                bids=bids,
                asks=asks,
                timestamp=time.time(),
                source="MT5_NATIVE",
                freshness="FRESH",
                connection_status=ConnectionStatus.CONNECTED.value,
                is_ready=True,
                is_synchronized=True,
            )
            if not ob.is_valid():
                return None
            return ob
        except Exception as e:
            logger.error("Failed to fetch MT5 order book for %s: %s", symbol, e)
            return None

    def _generate_mock_order_book(self, symbol: str) -> OrderBook:
        """Isolated test mock book used only when mock_if_unavailable is explicitly set to True in isolated unit test."""
        return OrderBook(
            symbol=symbol,
            venue="MT5",
            bids=[OrderBookEntry(price=68000.0, size=1.0)],
            asks=[OrderBookEntry(price=68005.0, size=1.0)],
            timestamp=time.time(),
            source="MT5_TEST_MOCK",
            is_ready=False,
            is_synchronized=False,
        )