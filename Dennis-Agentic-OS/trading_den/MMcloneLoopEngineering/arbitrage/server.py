"""
High-Performance Async HTTP & WebSocket Server for DenMa Arbitrage Dashboard.
Serves dashboard HTML, REST API endpoints, and live real-time WebSocket streaming.
Integrates Connector/Data Layer (Native WS primary, CCXT Pro fallback, REST recovery),
In-Memory Live State Layer, Calculation Engine, and SQLite Persistence Layer.
Loads UI immediately and initializes exchange streams asynchronously and independently.
"""

import asyncio
import base64
import hashlib
import json
import logging
import os
import struct
import sys
import time
from typing import Dict, List, Optional, Set, Tuple
from urllib.parse import parse_qs, urlparse

from .models import (
    DataSourceType,
    FreshnessStatus,
    FundingRate,
    OrderBook,
    SpreadResult,
    Ticker,
    TriangularOpportunity,
)
from .market_data_manager import MarketDataManager
from .mt5_client import MT5Client, MT5_AVAILABLE
from .spread_calculator import SpreadCalculator
from .live_state import LiveStateManager
from .persistence import PersistenceManager
from .market_catalog import (
    get_market_items,
    format_volume_usd,
    VENUE_INSTRUMENT_TYPES,
)

logger = logging.getLogger(__name__)

WS_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"


class ArbitrageServer:
    """
    Combined HTTP REST + WebSocket server for DenMa Arbitrage Dashboard.
    Provides live market data across Native WebSockets, REST, and CCXT Pro fallback.
    Decoupled architecture: In-Memory Live State for hot market ticks,
    SQLite Persistence Layer for on-demand historical queries,
    and non-blocking immediate UI startup.
    """

    DEFAULT_SYMBOLS = [
        "BTC/USDT", "ETH/USDT", "SOL/USDT", "XRP/USDT",
        "DOGE/USDT", "ADA/USDT", "AVAX/USDT", "LINK/USDT",
        "LTC/USDT", "NEAR/USDT",
    ]

    EXCHANGES = ["binance", "bybit", "okx", "kraken", "mexc"]

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 8080,
        html_path: Optional[str] = None,
        taker_fee: float = 0.001,
        min_profit_threshold: float = 0.01,
        default_source: DataSourceType = DataSourceType.AUTO,
        persistence: Optional[PersistenceManager] = None,
    ):
        self.host = host
        self.port = port
        self.html_path = html_path or os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "templates", "index.html"
        )
        self.taker_fee = taker_fee
        self.min_profit_threshold = min_profit_threshold

        # 1. In-Memory Live State Layer (Zero disk I/O on price updates)
        self.live_state = LiveStateManager()

        # 2. Persistence Layer (SQLite on-demand; zero preload at startup)
        self.persistence = persistence or PersistenceManager()

        # 3. Connector / Market Data Manager (Native WS + REST primary, CCXT Pro fallback)
        self.market_data_mgr = MarketDataManager(
            default_source=default_source,
            enabled_exchanges=self.EXCHANGES,
            live_state=self.live_state,
            persistence=self.persistence,
        )

        # 4. Calculation & Client Engines
        self.is_mt5_enabled = True
        self.mt5_client = MT5Client(mock_if_unavailable=True)
        self.calculator = SpreadCalculator(
            default_taker_fee=taker_fee,
            min_profit_threshold_pct=min_profit_threshold,
        )

        from .bot_manager import BotManager
        self.bot_manager = BotManager(
            calculator=self.calculator,
            persistence=self.persistence,
        )

        self.ws_clients: Set[asyncio.StreamWriter] = set()
        self.is_running = False
        self.server: Optional[asyncio.Server] = None
        self.worker_task: Optional[asyncio.Task] = None
        self.bg_init_task: Optional[asyncio.Task] = None
        self._last_rest_warmup: float = 0.0

        # Wire live state order book listener to trigger instant calculation
        self.market_data_mgr.add_order_book_listener(self._on_live_book_update)

    @property
    def screener_opportunities(self) -> List[dict]:
        return self.live_state.get_opportunities()

    @property
    def triangular_routes(self) -> List[dict]:
        return self.live_state.get_triangular_routes()

    @property
    def mt5_reference_spreads(self) -> List[dict]:
        return self.live_state.get_mt5_spreads()

    @property
    def funding_matrix(self) -> List[dict]:
        return self.live_state.get_funding_matrix()

    @property
    def latest_books(self) -> Dict[str, OrderBook]:
        return self.live_state._books

    def _load_html(self) -> bytes:
        """Load and return the dashboard HTML."""
        if os.path.exists(self.html_path):
            with open(self.html_path, "rb") as f:
                return f.read()

        alt_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "qodex_ui-design", "denma_arbi_spread_funding (1).html"
        )
        if os.path.exists(alt_path):
            with open(alt_path, "rb") as f:
                return f.read()

        return b"<html><body><h1>DenMa Dashboard HTML not found</h1></body></html>"

    async def start(self):
        """
        Start the server immediately and launch background engines asynchronously.
        Guarantees that the UI is accessible immediately and no slow exchange can block startup.
        """
        self.is_running = True

        # 1. Start HTTP & WebSocket socket server FIRST
        self.server = await asyncio.start_server(
            self._handle_client,
            self.host,
            self.port,
            reuse_address=True,
            reuse_port=True,
        )
        logger.info("Arbitrage Dashboard running at http://%s:%s", self.host, self.port)
        logger.info("WebSocket endpoint available at ws://%s:%s/ws", self.host, self.port)

        # 2. Launch MT5 initialization, exchange connections, and calculation loop asynchronously
        self.bg_init_task = asyncio.create_task(self._init_backend_and_streams())

    async def _init_backend_and_streams(self):
        """Asynchronously initialize MT5, market data connectors, and subscriptions."""
        try:
            # Initialize MT5 in background
            try:
                self.mt5_client.initialize()
            except Exception as e:
                logger.warning("MT5 background init error: %s", e)

            # Start MarketDataManager (connectors initialize independently with timeout guards)
            await self.market_data_mgr.start()

            # Launch Step 2 Hybrid Startup Pipeline across default pairs
            hybrid_map = {
                ex: list(self.DEFAULT_SYMBOLS)
                for ex in ["binance", "bybit", "okx", "kraken"]
            }
            asyncio.create_task(self.market_data_mgr.start_hybrid_pipeline(hybrid_map))

            # Subscribe to funding rates
            for sym in ["BTC/USDT", "ETH/USDT", "SOL/USDT"]:
                for ex in ["binance", "bybit", "okx"]:
                    self.market_data_mgr.subscribe_funding_rate(ex, sym)

            # Start background computation and broadcast loop
            self.worker_task = asyncio.create_task(self._market_data_loop())
            asyncio.create_task(self._preload_connector_markets())
            logger.info("Background market data and calculation engine fully operational.")
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error("Error in background backend init: %s", e)

    async def _preload_connector_markets(self):
        """Asynchronously load real exchange symbols from CCXT connectors into market catalog."""
        from .market_catalog import register_connector_markets
        for ex in ["binance", "bybit", "okx", "kraken", "mexc"]:
            try:
                client = self.market_data_mgr.rest_connector._get_client(ex)
                if client:
                    markets = await asyncio.wait_for(client.load_markets(), timeout=6.0)
                    if markets:
                        register_connector_markets(ex, markets)
            except Exception as e:
                logger.debug("Background preload markets skipped for %s: %s", ex, e)

    async def stop(self):
        """Shutdown the server and free resources."""
        self.is_running = False
        if self.bg_init_task:
            self.bg_init_task.cancel()
        if self.worker_task:
            self.worker_task.cancel()
        if self.server:
            self.server.close()
            await self.server.wait_closed()

        await self.market_data_mgr.close()
        self.mt5_client.shutdown()

        # Close all active websockets
        for writer in list(self.ws_clients):
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass
        self.ws_clients.clear()

    def _on_live_book_update(self, ob: OrderBook):
        """Called whenever an in-memory order book is updated by a connector."""
        # Book is already in self.live_state; calculation loop picks it up

    async def _market_data_loop(self):
        """Continuously re-evaluates arbitrage opportunities and broadcasts updates."""
        logger.info("Background calculation & broadcast loop started.")
        while self.is_running:
            try:
                await self._refresh_market_data()
                await self._broadcast_live_update()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("Error in market data loop: %s", e)
            await asyncio.sleep(0.5)  # 500ms calculation and broadcast cadence

    async def _refresh_market_data(self):
        """Fetch latest books and compute arbitrage opportunities from in-memory live state."""
        active_exchanges = ["binance", "bybit", "okx", "kraken", "mexc"]

        # 1. Read books directly from in-memory live state (zero REST latency)
        books: Dict[str, OrderBook] = {}
        for sym in self.DEFAULT_SYMBOLS:
            for ex in active_exchanges:
                b = self.live_state.get_order_book(ex, sym)
                if b and b.best_bid and b.best_ask:
                    books[f"{ex.upper()}:{sym}"] = b
                    self.market_data_mgr._update_normalized_state_from_book(b)
                    mid_p = (b.best_bid + b.best_ask) / 2.0
                    self.live_state.set_ticker(ex.upper(), sym, Ticker(
                        symbol=sym,
                        venue=ex.upper(),
                        bid=b.best_bid,
                        ask=b.best_ask,
                        last=mid_p,
                        bid_size=b.best_bid_size or 0.0,
                        ask_size=b.best_ask_size or 0.0,
                        volume_24h=0.0,
                        timestamp=b.timestamp,
                        source=getattr(b, "source", "NATIVE_WS"),
                        connection_status="CONNECTED",
                        is_ready=True,
                    ))

        # Sync MT5 into normalized state if available
        if self.is_mt5_enabled:
            for sym_crypto, sym_mt5 in [("BTC/USDT", "BTCUSD"), ("ETH/USDT", "ETHUSD"), ("SOL/USDT", "SOLUSD")]:
                mt5_book = self.mt5_client.get_order_book(sym_mt5)
                if mt5_book and mt5_book.best_bid and mt5_book.best_ask:
                    self.market_data_mgr._update_normalized_state_from_book(mt5_book)

        # Gentle warmup fallback: if in-memory cache has fewer than 2 books, poll REST once every 10s
        now = time.time()
        if len(books) < 2 and (now - self._last_rest_warmup) > 10.0:
            self._last_rest_warmup = now
            fetch_coros = []
            task_meta = []
            for sym in self.DEFAULT_SYMBOLS[:4]:
                for ex in active_exchanges[:2]:
                    if f"{ex.upper()}:{sym}" not in books:
                        fetch_coros.append(self.market_data_mgr.get_order_book(ex, sym))
                        task_meta.append((ex, sym))
            if fetch_coros:
                results = await asyncio.gather(*fetch_coros, return_exceptions=True)
                for (ex, sym), res in zip(task_meta, results):
                    if isinstance(res, OrderBook) and res:
                        books[f"{ex.upper()}:{sym}"] = res
                        self.market_data_mgr._update_normalized_state_from_book(res)
                        if res.best_bid and res.best_ask:
                            mid_p = (res.best_bid + res.best_ask) / 2.0
                            self.live_state.set_ticker(ex.upper(), sym, Ticker(
                                symbol=sym,
                                venue=ex.upper(),
                                bid=res.best_bid,
                                ask=res.best_ask,
                                last=mid_p,
                                bid_size=res.best_bid_size or 0.0,
                                ask_size=res.best_ask_size or 0.0,
                                volume_24h=0.0,
                                timestamp=res.timestamp,
                                source="REST",
                                connection_status="CONNECTED",
                                is_ready=True,
                            ))

        # 2. Compute Step 5 Raw Spread % for every Reference/Priced relationship
        all_symbols = set(self.DEFAULT_SYMBOLS)
        for k in self.live_state._normalized_states.keys():
            if ":" in k:
                all_symbols.add(k.split(":", 1)[1])
        for k in books.keys():
            if ":" in k:
                all_symbols.add(k.split(":", 1)[1])

        spread_decisions_map: Dict[str, dict] = {}
        funding_decisions_map: Dict[str, dict] = {}

        for sym in all_symbols:
            for ref_ex in active_exchanges:
                ref_norm = self.live_state.get_normalized_state(ref_ex, sym)
                ref_book = books.get(f"{ref_ex.upper()}:{sym}") or self.live_state.get_order_book(ref_ex, sym)
                ref_data = ref_norm or ref_book

                for priced_ex in active_exchanges:
                    priced_norm = self.live_state.get_normalized_state(priced_ex, sym)
                    priced_book = books.get(f"{priced_ex.upper()}:{sym}") or self.live_state.get_order_book(priced_ex, sym)
                    priced_data = priced_norm or priced_book

                    raw_spread = self.calculator.calculate_raw_spread(
                        reference_data=ref_data,
                        priced_data=priced_data,
                        symbol=sym,
                        reference_venue=ref_ex.upper(),
                        priced_venue=priced_ex.upper(),
                    )
                    self.live_state.set_raw_spread(raw_spread)

                    # Step 6: Dynamic Maker/Hedge Direction Evaluation (Candidate A and Candidate B)
                    if ref_ex != priced_ex:
                        direction_result = self.calculator.evaluate_maker_hedge_candidates(
                            venue_a_data=ref_data,
                            venue_b_data=priced_data,
                            symbol=sym,
                            venue_a_name=ref_ex.upper(),
                            venue_b_name=priced_ex.upper(),
                            reference_venue=ref_ex.upper(),
                            priced_venue=priced_ex.upper(),
                        )
                        self.live_state.set_direction_candidates(direction_result)

                        # Step 8: Authoritative Opportunity Engine Evaluation
                        opp_result = self.calculator.evaluate_directional_opportunities(
                            venue_a_data=ref_data,
                            venue_b_data=priced_data,
                            symbol=sym,
                            venue_a_name=ref_ex.upper(),
                            venue_b_name=priced_ex.upper(),
                            reference_venue=ref_ex.upper(),
                            priced_venue=priced_ex.upper(),
                        )
                        self.live_state.set_directional_opportunities(opp_result)

                        # Step 9: Spread Arbitrage Strategy Evaluation
                        if ref_data and priced_data:
                            spread_dec = self.calculator.evaluate_spread_arbitrage(
                                venue_a_data=ref_data,
                                venue_b_data=priced_data,
                                symbol=sym,
                            )
                            spread_decisions_map[f"{ref_ex.upper()}->{priced_ex.upper()}:{sym}"] = spread_dec.to_dict()

                        # Step 10: Funding Arbitrage Strategy Evaluation
                        f_a = self.live_state.get_funding_rate(ref_ex, sym)
                        f_b = self.live_state.get_funding_rate(priced_ex, sym)
                        if f_a and f_b and ref_data and priced_data:
                            fund_dec = self.calculator.evaluate_funding_arbitrage(
                                funding_a=f_a,
                                funding_b=f_b,
                                book_a=ref_data,
                                book_b=priced_data,
                                symbol=sym,
                            )
                            funding_decisions_map[f"{ref_ex.upper()}->{priced_ex.upper()}:{sym}"] = fund_dec.to_dict()

        self.live_state.set_spread_decisions(spread_decisions_map)
        self.live_state.set_funding_decisions(funding_decisions_map)

        # 3. Compute Cross-Exchange Opportunities using authoritative Opportunity Engine
        new_opps = []
        seen_opp_keys = set()
        for key, opp_dict in self.live_state.get_all_directional_opportunities_dict().items():
            if not opp_dict.get("validity"):
                continue  # Never generate placeholders for invalid/stale data
            highest = opp_dict.get("highest_net_opportunity")
            if not highest or not highest.get("validity"):
                continue

            sym = opp_dict.get("symbol", "")
            maker_v = highest.get("maker_venue", "").capitalize()
            hedge_v = highest.get("hedge_venue", "").capitalize()
            maker_p = highest.get("maker_price", 0.0)
            hedge_p = highest.get("hedge_price", 0.0)
            maker_side = highest.get("maker_side", "BUY")

            if maker_side == "BUY":
                buy_ex = maker_v
                buy_price = maker_p
                sell_ex = hedge_v
                sell_price = hedge_p
            else:
                buy_ex = hedge_v
                buy_price = hedge_p
                sell_ex = maker_v
                sell_price = maker_p

            pair_key = f"{sym}:{buy_ex}:{sell_ex}"
            if pair_key in seen_opp_keys:
                continue
            seen_opp_keys.add(pair_key)

            gross_pct = highest.get("gross_spread_pct", 0.0) or 0.0
            net_pct = highest.get("net_spread_pct", 0.0) or 0.0
            raw_pct = highest.get("raw_spread_pct")
            maker_size = highest.get("maker_size", 0.0) or 0.0
            hedge_size = highest.get("hedge_size", 0.0) or 0.0
            buy_vol = round(buy_price * (maker_size if buy_ex == maker_v else hedge_size), 2)
            sell_vol = round(sell_price * (hedge_size if sell_ex == hedge_v else maker_size), 2)

            new_opps.append({
                "symbol": sym,
                "type": "Spot",
                "referenceVenue": opp_dict.get("reference_venue", "").capitalize(),
                "pricedVenue": opp_dict.get("priced_venue", "").capitalize(),
                "makerVenue": maker_v,
                "hedgeVenue": hedge_v,
                "firstLegVenue": maker_v,
                "secondLegVenue": hedge_v,
                "buyEx": buy_ex,
                "buyPrice": round(buy_price, 2 if buy_price > 10 else 4),
                "sellEx": sell_ex,
                "sellPrice": round(sell_price, 2 if sell_price > 10 else 4),
                "usd": round(sell_price - buy_price, 4),
                "pct": round(gross_pct, 4),
                "netPct": round(net_pct, 4),
                "rawSpreadPct": raw_pct,
                "makerFeeRate": highest.get("maker_fee_rate", 0.0),
                "takerFeeRate": highest.get("taker_fee_rate", 0.0),
                "totalFeePct": highest.get("total_fee_pct", 0.0),
                "slippagePct": highest.get("slippage_pct", 0.0),
                "liquiditySize": highest.get("liquidity_size", 0.0),
                "buyVol": buy_vol if buy_vol > 0 else round(buy_price, 2),
                "sellVol": sell_vol if sell_vol > 0 else round(sell_price, 2),
                "updated": "just now",
                "profitable": highest.get("is_profitable", False),
                "calculationComponents": highest.get("calculation_components", {}),
                "opportunityA": opp_dict.get("opportunity_a"),
                "opportunityB": opp_dict.get("opportunity_b"),
            })

        if new_opps:
            sorted_opps = sorted(new_opps, key=lambda x: x["pct"], reverse=True)
            self.live_state.set_opportunities(sorted_opps)

        # 4. Compute Triangular Arbitrage (using in-memory books)
        bk_btc = self.live_state.get_order_book("binance", "BTC/USDT")
        bk_eth = self.live_state.get_order_book("binance", "ETH/USDT")
        bk_ethbtc = self.live_state.get_order_book("binance", "ETH/BTC")

        if bk_btc and bk_eth and bk_ethbtc:
            tri_opps = self.calculator.calculate_triangular_arbitrage(
                venue="BINANCE",
                book_ab=bk_btc,
                book_cb=bk_eth,
                book_ca=bk_ethbtc,
            )
            self.live_state.set_triangular_routes([
                {
                    "route": " -> ".join(o.route),
                    "gross_pct": o.gross_profit_pct,
                    "net_pct": o.net_profit_pct,
                    "profitable": o.is_profitable,
                    "actions": o.actions,
                    "rates": o.rates,
                    "source": getattr(bk_btc, "source", "NATIVE_WS"),
                }
                for o in tri_opps
            ])

        # 4. Compute Funding Rates Matrix from in-memory cache
        fr_list = []
        for sym in ["BTC/USDT", "ETH/USDT", "SOL/USDT"]:
            for ex in ["binance", "bybit", "okx"]:
                fr = self.live_state.get_funding_rate(ex, sym)
                if fr:
                    fr_list.append({
                        "symbol": sym,
                        "exchange": ex.capitalize(),
                        "rate_pct": round(fr.rate * 100, 4),
                        "annual_pct": round(fr.annual_pct, 2),
                        "next_funding": fr.next_funding_time,
                        "source": fr.source,
                    })
        if fr_list:
            self.live_state.set_funding_matrix(fr_list)

        # 5. Compute MT5 vs Crypto Reference Quotes
        mt5_spreads = []
        for sym_crypto, sym_mt5 in [("BTC/USDT", "BTCUSD"), ("ETH/USDT", "ETHUSD"), ("SOL/USDT", "SOLUSD")]:
            mt5_book = self.mt5_client.get_order_book(sym_mt5)
            crypto_book = books.get(f"BINANCE:{sym_crypto}") or self.live_state.get_order_book("binance", sym_crypto) or books.get(f"BYBIT:{sym_crypto}") or self.live_state.get_order_book("bybit", sym_crypto)
            if crypto_book and mt5_book:
                res = self.calculator.calculate_mt5_crypto_spread(crypto_book, mt5_book)
                if res:
                    mt5_spreads.append(res)
        if mt5_spreads:
            self.live_state.set_mt5_spreads(mt5_spreads)

    async def _broadcast_live_update(self):
        """Send live snapshot packets to all connected WebSockets."""
        if not self.ws_clients:
            return

        packet = self.live_state.get_live_snapshot(
            connector_status=self.market_data_mgr.get_connector_status(),
            connected_clients=len(self.ws_clients),
        )
        frame = self._encode_ws_frame(json.dumps(packet))

        disconnected = set()
        for writer in self.ws_clients:
            try:
                writer.write(frame)
                await writer.drain()
            except Exception:
                disconnected.add(writer)

        for d in disconnected:
            self.ws_clients.discard(d)

    def _encode_ws_frame(self, message: Any, opcode: int = 0x1) -> bytes:
        """Encode message into RFC 6455 WebSocket unmasked server frame."""
        payload = message.encode("utf-8") if isinstance(message, str) else bytes(message)
        length = len(payload)
        first_byte = 0x80 | (opcode & 0x0F)
        if length <= 125:
            header = bytes([first_byte, length])
        elif length <= 65535:
            header = bytes([first_byte, 126]) + struct.pack("!H", length)
        else:
            header = bytes([first_byte, 127]) + struct.pack("!Q", length)
        return header + payload

    async def _handle_client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        """Handle incoming HTTP requests or WebSocket handshakes."""
        try:
            line = await reader.readline()
            if not line:
                writer.close()
                return

            req_line = line.decode("utf-8", errors="ignore").strip()
            parts = req_line.split()
            if len(parts) < 2:
                writer.close()
                return

            method, raw_path = parts[0], parts[1]
            headers = {}
            while True:
                header_line = await reader.readline()
                if not header_line or header_line == b"\r\n":
                    break
                h_str = header_line.decode("utf-8", errors="ignore").strip()
                if ":" in h_str:
                    k, v = h_str.split(":", 1)
                    headers[k.strip().lower()] = v.strip()

            body_bytes = b""
            if method == "POST" and "content-length" in headers:
                try:
                    cl = int(headers["content-length"])
                    if 0 < cl <= 1048576:  # limit 1MB
                        body_bytes = await reader.readexactly(cl)
                except Exception:
                    pass

            parsed = urlparse(raw_path)
            path = parsed.path
            query_params = parse_qs(parsed.query)

            # 1. WebSocket Upgrade Handshake
            if headers.get("upgrade", "").lower() == "websocket":
                await self._upgrade_to_websocket(reader, writer, headers)
                return

            # 2. Serve Dashboard HTML
            if path in ("/", "/index", "/index.html"):
                html_bytes = self._load_html()
                response = (
                    b"HTTP/1.1 200 OK\r\n"
                    b"Content-Type: text/html; charset=utf-8\r\n"
                    b"Content-Length: " + str(len(html_bytes)).encode() + b"\r\n"
                    b"Connection: close\r\n\r\n" + html_bytes
                )
                writer.write(response)
                await writer.drain()
                writer.close()
                return

            # 2.5 Serve Static Assets (e.g. lightweight-charts)
            if path.startswith("/static/"):
                base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
                static_dir = os.path.join(base_dir, "static")
                rel_path = path[len("/static/"):].lstrip("/")
                file_path = os.path.abspath(os.path.join(static_dir, rel_path))
                if file_path.startswith(static_dir) and os.path.isfile(file_path):
                    content_type = (
                        "application/javascript" if file_path.endswith(".js")
                        else "text/css" if file_path.endswith(".css")
                        else "application/octet-stream"
                    )
                    with open(file_path, "rb") as f:
                        file_bytes = f.read()
                    response = (
                        b"HTTP/1.1 200 OK\r\n"
                        b"Content-Type: " + content_type.encode() + b"; charset=utf-8\r\n"
                        b"Content-Length: " + str(len(file_bytes)).encode() + b"\r\n"
                        b"Cache-Control: public, max-age=86400\r\n"
                        b"Access-Control-Allow-Origin: *\r\n"
                        b"Connection: close\r\n\r\n" + file_bytes
                    )
                    writer.write(response)
                    await writer.drain()
                    writer.close()
                    return

            # 3. REST API Endpoints
            if path.startswith("/api/"):
                await self._handle_api_request(path, query_params, writer, method=method, body=body_bytes)
                return

            # 404
            writer.write(b"HTTP/1.1 404 Not Found\r\nContent-Length: 0\r\nConnection: close\r\n\r\n")
            await writer.drain()
            writer.close()

        except Exception as e:
            logger.debug("Client handler error: %s", e)
            try:
                writer.close()
            except Exception:
                pass

    def get_markets_catalog(
        self,
        exchange: str,
        inst_type: str,
        search_q: str = "",
        limit: int = 50,
    ) -> List[dict]:
        """Fetch market catalog overlaying real live market data from in-memory state."""
        markets = get_market_items(
            exchange=exchange,
            inst_type=inst_type,
            search_q=search_q,
            limit=limit,
        )
        ex_key = exchange.lower().strip()
        ex_upper = exchange.upper().strip()

        for item in markets:
            sym = item.get("symbol", "")
            base = item.get("base", "")
            quote = item.get("quote", "")
            pair_slash = f"{base}/{quote}"

            # 1. Check MT5 specifically
            if ex_key == "mt5" and self.is_mt5_enabled:
                mt5_b = self.mt5_client.get_order_book(sym) or self.mt5_client.get_order_book(f"{base}{quote}")
                if mt5_b and mt5_b.mid_price:
                    item["price"] = mt5_b.mid_price
                    item["volumeFormatted"] = "MT5 Live"
                    item["status"] = "LIVE"
                    continue

            # 2. Check normalized market state (from Step 2 normalized pipeline)
            norm_state = (
                self.live_state.get_normalized_state(ex_upper, pair_slash)
                or self.live_state.get_normalized_state(ex_upper, sym)
            )
            if norm_state and (norm_state.best_bid > 0 or norm_state.best_ask > 0):
                item["price"] = norm_state.mid_price or norm_state.best_bid
                if norm_state.volume_24h > 0:
                    item["volume24h"] = norm_state.volume_24h
                    item["volumeFormatted"] = format_volume_usd(norm_state.volume_24h)
                else:
                    item["volumeFormatted"] = "—"
                item["status"] = "STALE" if norm_state.freshness == FreshnessStatus.STALE.value else "FRESH"
                continue

            # 3. Check real in-memory tickers
            t = (
                self.live_state.get_ticker(ex_key, sym)
                or self.live_state.get_ticker(ex_key, pair_slash)
            )
            if t and (t.last > 0 or t.bid > 0):
                item["price"] = t.last if t.last > 0 else (t.bid + t.ask) / 2.0
                if t.volume_24h > 0:
                    item["volume24h"] = t.volume_24h
                    item["volumeFormatted"] = format_volume_usd(t.volume_24h)
                else:
                    item["volumeFormatted"] = "—"
                if t.change_24h_pct != 0.0:
                    item["change24h"] = t.change_24h_pct
                item["status"] = "LIVE"
                continue

            # 4. Check real in-memory order book
            ob = (
                self.live_state.get_order_book(ex_key, pair_slash)
                or self.live_state.get_order_book(ex_key, sym)
            )
            if ob and ob.mid_price and ob.mid_price > 0:
                item["price"] = ob.mid_price
                item["volumeFormatted"] = "—"
                item["status"] = "LIVE"
                continue

            # 5. No live feed yet - explicit WAITING_FOR_DATA state without mock values
            item["price"] = None
            item["change24h"] = None
            item["volume24h"] = 0.0
            item["volumeFormatted"] = "—"
            item["status"] = "WAITING_FOR_DATA"

        return markets

    async def _handle_api_request(
        self,
        path: str,
        query: Dict[str, List[str]],
        writer: asyncio.StreamWriter,
        method: str = "GET",
        body: bytes = b"",
    ):
        """Serve REST API JSON requests including on-demand historical queries."""
        data = {}

        if path == "/api/status":
            data = {
                "status": "online",
                "platform": sys.platform,
                "mt5_available": MT5_AVAILABLE,
                "ws_clients": len(self.ws_clients),
                "connectors": self.market_data_mgr.get_connector_status(),
                "timestamp": time.time(),
                "in_memory_version": self.live_state.version,
            }
        elif path == "/api/connectors":
            data = self.market_data_mgr.get_connector_status()
        elif path == "/api/set-source":
            ex = query.get("exchange", [""])[0].lower()
            src = query.get("source", ["AUTO"])[0].upper()
            if ex in self.EXCHANGES and hasattr(DataSourceType, src):
                self.market_data_mgr.set_source(ex, DataSourceType[src])
                data = {"status": "ok", "exchange": ex, "source": src}
            else:
                data = {"error": "Invalid exchange or source"}
        elif path in ("/api/screener", "/api/opportunities"):
            opps = self.live_state.get_opportunities()
            data = {"opportunities": opps, "count": len(opps)}
        elif path == "/api/triangular":
            data = {"routes": self.live_state.get_triangular_routes()}
        elif path == "/api/funding-rates":
            data = {"funding_rates": self.live_state.get_funding_matrix()}
        elif path == "/api/mt5-pricing":
            data = {"spreads": self.live_state.get_mt5_spreads()}
        elif path == "/api/orderbook":
            sym = query.get("symbol", ["BTC/USDT"])[0]
            ex = query.get("venue", ["binance"])[0].lower()
            ob = await self.market_data_mgr.get_order_book(ex, sym)
            if ob:
                data = {
                    "symbol": ob.symbol,
                    "venue": ob.venue,
                    "bids": [[e.price, e.size] for e in ob.bids[:20]],
                    "asks": [[e.price, e.size] for e in ob.asks[:20]],
                    "mid": ob.mid_price,
                    "spread": ob.internal_spread,
                    "spread_pct": ob.internal_spread_pct,
                    "source": ob.source,
                    "timestamp": ob.timestamp,
                }
            else:
                data = {"error": "Orderbook unavailable"}
        elif path == "/api/live-snapshot":
            data = self.live_state.get_live_snapshot(
                connector_status=self.market_data_mgr.get_connector_status(),
                connected_clients=len(self.ws_clients),
            )
        elif path == "/api/raw-spreads":
            ref = query.get("reference", [None])[0]
            priced = query.get("priced", [None])[0]
            sym = query.get("symbol", [None])[0]
            if ref and priced and sym:
                spread = self.live_state.get_raw_spread(ref, priced, sym)
                data = spread.to_dict() if spread else {
                    "reference_venue": ref.upper(),
                    "priced_venue": priced.upper(),
                    "symbol": sym,
                    "prices": {},
                    "direction": "INVALID",
                    "raw_spread_pct": None,
                    "timestamp": time.time(),
                    "freshness": "waiting_for_data",
                    "validity": False,
                    "error_reason": "No raw spread calculated for specified pair",
                }
            else:
                raws = self.live_state.get_all_raw_spreads_dict()
                data = {"raw_spreads": raws, "count": len(raws)}
        elif path in ("/api/direction-candidates", "/api/direction/candidates"):
            v_a = query.get("venue_a", [None])[0]
            v_b = query.get("venue_b", [None])[0]
            sym = query.get("symbol", [None])[0]
            if v_a and v_b and sym:
                res = self.live_state.get_direction_candidates(v_a, v_b, sym)
                data = res.to_dict() if res else {
                    "error": "No directional candidates found for specified venues and symbol",
                    "validity": False,
                }
            else:
                cands = self.live_state.get_all_direction_candidates_dict()
                data = {"direction_candidates": cands, "count": len(cands)}
        elif path in ("/api/directional-fees", "/api/fees"):
            v_a = query.get("venue_a", query.get("exchange", ["binance"]))[0]
            v_b = query.get("venue_b", ["bybit"])[0]
            sym = query.get("symbol", ["BTC/USDT"])[0]
            m_type = query.get("market_type", ["spot"])[0]
            tier_a = query.get("tier_a", query.get("fee_tier", ["standard"]))[0]
            tier_b = query.get("tier_b", ["standard"])[0]
            acc_a = query.get("account_a", [None])[0]
            acc_b = query.get("account_b", [None])[0]
            inst = query.get("instrument", [sym])[0]
            fee_res = self.calculator.calculate_directional_fees(
                venue_a=v_a,
                venue_b=v_b,
                symbol=sym,
                market_type=m_type,
                fee_tier_a=tier_a,
                fee_tier_b=tier_b,
                account_a=acc_a,
                account_b=acc_b,
                instrument=inst,
            )
            data = fee_res.to_dict()
        elif path in ("/api/directional-opportunities", "/api/opps/directional"):
            v_a = query.get("venue_a", [None])[0]
            v_b = query.get("venue_b", [None])[0]
            sym = query.get("symbol", [None])[0]
            if v_a and v_b and sym:
                res = self.live_state.get_directional_opportunities(v_a, v_b, sym)
                data = res.to_dict() if res else {
                    "error": "No directional opportunities found for specified venues and symbol",
                    "validity": False,
                }
            else:
                opps = self.live_state.get_all_directional_opportunities_dict()
                data = {"directional_opportunities": opps, "count": len(opps)}

        elif path in ("/api/arbitrage/strategy-mode", "/api/strategy-mode"):
            if method == "POST":
                try:
                    payload = json.loads(body.decode("utf-8")) if body else {}
                    m = payload.get("strategy_mode", payload.get("mode", "SPREAD"))
                    self.live_state.set_strategy_mode(m)
                    data = {"status": "ok", "strategy_mode": self.live_state.get_strategy_mode()}
                except Exception as e:
                    data = {"status": "error", "message": str(e)}
            else:
                data = {
                    "strategy_mode": self.live_state.get_strategy_mode(),
                    "supported_modes": ["SPREAD", "FUNDING"],
                }

        elif path in ("/api/arbitrage/spread-decision", "/api/spread-decision"):
            v_a = query.get("venue_a", [None])[0] or query.get("ref", ["binance"])[0]
            v_b = query.get("venue_b", [None])[0] or query.get("priced", ["bybit"])[0]
            sym = query.get("symbol", ["BTC/USDT"])[0]
            mode = query.get("pricing_mode", ["SPREAD"])[0].upper()
            step_pct = float(query.get("grid_step_pct", ["0.25"])[0])
            levels = int(query.get("grid_levels_count", ["3"])[0])
            bb_k = float(query.get("bollinger_k", ["2.0"])[0])
            bb_p = int(query.get("bollinger_period", ["20"])[0])

            ref_norm = self.live_state.get_normalized_state(v_a, sym)
            ref_book = self.live_state.get_order_book(v_a, sym)
            ref_data = ref_norm or ref_book

            priced_norm = self.live_state.get_normalized_state(v_b, sym)
            priced_book = self.live_state.get_order_book(v_b, sym)
            priced_data = priced_norm or priced_book

            if ref_data and priced_data:
                decision = self.calculator.evaluate_spread_arbitrage(
                    venue_a_data=ref_data,
                    venue_b_data=priced_data,
                    symbol=sym,
                    pricing_mode=mode,
                    grid_step_pct=step_pct,
                    grid_levels_count=levels,
                    bollinger_k=bb_k,
                    bollinger_period=bb_p,
                )
                data = decision.to_dict()
            else:
                data = {
                    "symbol": sym,
                    "strategy_type": "SPREAD_ARBITRAGE",
                    "venue_a": v_a.upper(),
                    "venue_b": v_b.upper(),
                    "validity": False,
                    "is_viable": False,
                    "rejection_reason": "Order book or normalized market data unavailable for one or both venues",
                    "status": "UNAVAILABLE",
                }

        elif path in ("/api/arbitrage/funding-decision", "/api/funding-decision"):
            v_a = query.get("venue_a", [None])[0] or query.get("ref", ["binance"])[0]
            v_b = query.get("venue_b", [None])[0] or query.get("priced", ["bybit"])[0]
            sym = query.get("symbol", ["BTC/USDT"])[0]
            mode = query.get("pricing_mode", ["SPREAD"])[0].upper()
            step_pct = float(query.get("grid_step_pct", ["0.25"])[0])
            levels = int(query.get("grid_levels_count", ["3"])[0])
            bb_k = float(query.get("bollinger_k", ["2.0"])[0])
            bb_p = int(query.get("bollinger_period", ["20"])[0])

            f_a = self.live_state.get_funding_rate(v_a, sym)
            f_b = self.live_state.get_funding_rate(v_b, sym)
            b_a = self.live_state.get_order_book(v_a, sym) or self.live_state.get_normalized_state(v_a, sym)
            b_b = self.live_state.get_order_book(v_b, sym) or self.live_state.get_normalized_state(v_b, sym)

            if f_a and f_b and b_a and b_b:
                f_decision = self.calculator.evaluate_funding_arbitrage(
                    funding_a=f_a,
                    funding_b=f_b,
                    book_a=b_a,
                    book_b=b_b,
                    symbol=sym,
                    pricing_mode=mode,
                    grid_step_pct=step_pct,
                    grid_levels_count=levels,
                    bollinger_k=bb_k,
                    bollinger_period=bb_p,
                )
                data = f_decision.to_dict()
            else:
                data = {
                    "symbol": sym,
                    "strategy_type": "FUNDING_ARBITRAGE",
                    "venue_a": v_a.upper(),
                    "venue_b": v_b.upper(),
                    "validity": False,
                    "is_viable": False,
                    "rejection_reason": "Funding rate or book data unavailable for one or both venues",
                    "status": "UNAVAILABLE",
                }


        # ---------------------------------------------------------------------
        # Persistence Layer: On-Demand Historical Endpoints
        # ---------------------------------------------------------------------
        elif path == "/api/history/pnl":
            # Loaded strictly on-demand when PNL report is opened or date range changed
            from_d = query.get("from", [None])[0]
            to_d = query.get("to", [None])[0]
            strat = query.get("strategy", [None])[0]
            limit_val = int(query.get("limit", ["200"])[0])
            records = self.persistence.get_pnl_history(from_date=from_d, to_date=to_d, strategy=strat, limit=limit_val)
            data = {"records": records, "count": len(records), "from": from_d, "to": to_d}

        elif path == "/api/history/orders":
            status_val = query.get("status", [None])[0]
            bot_val = query.get("bot_id", [None])[0]
            limit_val = int(query.get("limit", ["50"])[0])
            orders = self.persistence.get_orders(status=status_val, bot_id=bot_val, limit=limit_val)
            data = {"orders": orders, "count": len(orders)}

        elif path == "/api/history/positions":
            status_val = query.get("status", [None])[0]
            limit_val = int(query.get("limit", ["50"])[0])
            positions = self.persistence.get_positions(status=status_val, limit=limit_val)
            data = {"positions": positions, "count": len(positions)}

        elif path == "/api/history/bot-events":
            bot_val = query.get("bot_id", [None])[0]
            limit_val = int(query.get("limit", ["50"])[0])
            events = self.persistence.get_bot_events(bot_id=bot_val, limit=limit_val)
            data = {"events": events, "count": len(events)}

        elif path == "/api/history/export-csv":
            table_val = query.get("table", ["realized_pnl"])[0]
            try:
                csv_path = self.persistence.export_rolling_csv(table_val)
                data = {"status": "ok", "table": table_val, "path": csv_path}
            except Exception as e:
                data = {"status": "error", "message": str(e)}

        elif path == "/api/history/spread-candles":
            if method == "GET":
                asset_key = query.get("asset_key", [""])[0]
                symbol = query.get("symbol", [""])[0]
                priced_venue = query.get("priced_venue", [""])[0] or query.get("exec_venue", [""])[0]
                reference_venue = query.get("reference_venue", [""])[0] or query.get("ref_venue", [""])[0]
                timeframe = (query.get("timeframe", [""])[0] or query.get("tf", [""])[0] or "1m").lower()
                from_ts = int(query["from"][0]) if "from" in query and query["from"][0] else None
                to_ts = int(query["to"][0]) if "to" in query and query["to"][0] else None
                limit_val = int(query.get("limit", ["1500"])[0])

                clean_sym = symbol or (asset_key.split("_")[0] if "_" in asset_key else asset_key) or "BTC/USDT"
                if "/" not in clean_sym and clean_sym.endswith("USDT"):
                    clean_sym = clean_sym[:-4] + "/USDT"
                pv = (priced_venue or (asset_key.split("_")[1] if "_" in asset_key else "binance")).lower()
                rv = (reference_venue or "bybit").lower()
                effective_key = asset_key or f"{clean_sym}_{pv}_{rv}"

                candles = self.persistence.get_spread_candles(
                    asset_key=effective_key,
                    from_ts=from_ts,
                    to_ts=to_ts,
                    limit=limit_val,
                    auto_fill=False,
                    timeframe=timeframe,
                )

                priced_closes = []
                ref_closes = []
                bollinger_info = None

                # Fetch real historical candles via REST if database cache does not have sufficient recent candles
                now_sec = int(time.time())
                max_gap = 3600 if timeframe == "1h" else (86400 if timeframe == "1d" else 180)
                min_bars = min(limit_val, 48 if timeframe == "1h" else (14 if timeframe == "1d" else 1440))
                needs_fetch = False
                if not candles or len(candles) < min_bars:
                    needs_fetch = True
                elif candles and to_ts is None:
                    last_c_ts = candles[-1].get("time") or candles[-1].get("timestamp") or 0
                    if (now_sec - last_c_ts) > max_gap:
                        needs_fetch = True

                if needs_fetch and hasattr(self, 'market_data_mgr') and self.market_data_mgr and hasattr(self.market_data_mgr, 'rest'):
                    try:
                        p_candles = await self.market_data_mgr.rest.fetch_ohlcv(pv, clean_sym, timeframe=timeframe, limit=min(limit_val, 1440))
                        r_candles = await self.market_data_mgr.rest.fetch_ohlcv(rv, clean_sym, timeframe=timeframe, limit=min(limit_val, 1440))

                        # Save raw market candles into SQLite for fast indexed startup queries
                        if p_candles:
                            self.persistence.batch_save_market_candles(pv, clean_sym, timeframe, p_candles)
                        if r_candles:
                            self.persistence.batch_save_market_candles(rv, clean_sym, timeframe, r_candles)

                        tf_seconds = {"5m": 300, "15m": 900, "1h": 3600, "4h": 14400, "1d": 86400}.get(timeframe, 60)
                        p_map = {int(c.timestamp // tf_seconds * tf_seconds): c for c in p_candles}
                        r_map = {int(c.timestamp // tf_seconds * tf_seconds): c for c in r_candles}

                        common_times = sorted(set(p_map.keys()) & set(r_map.keys()))
                        constructed_candles = []
                        for t in common_times:
                            pc = p_map[t]
                            rc = r_map[t]
                            if rc.close > 0:
                                sp_close = ((pc.close - rc.close) / rc.close) * 100.0
                                sp_open = ((pc.open - rc.open) / rc.open) * 100.0
                                sp_high = max(sp_open, sp_close, ((pc.high - rc.low) / rc.low) * 100.0 if rc.low > 0 else sp_close)
                                sp_low = min(sp_open, sp_close, ((pc.low - rc.high) / rc.high) * 100.0 if rc.high > 0 else sp_close)
                                candle_dict = {
                                    "time": t,
                                    "timestamp": t,
                                    "open": round(sp_open, 4),
                                    "high": round(sp_high, 4),
                                    "low": round(sp_low, 4),
                                    "close": round(sp_close, 4),
                                    "venuePrice": pc.close,
                                    "refPrice": rc.close,
                                    "buyVenue": pv.upper(),
                                    "sellVenue": rv.upper(),
                                    "asset_key": effective_key,
                                    "timeframe": timeframe,
                                }
                                constructed_candles.append(candle_dict)
                                priced_closes.append({"time": t, "close": pc.close})
                                ref_closes.append({"time": t, "close": rc.close})

                        if constructed_candles:
                            self.persistence.batch_save_spread_candles(constructed_candles, timeframe=timeframe)
                            updated_candles = self.persistence.get_spread_candles(
                                asset_key=effective_key,
                                from_ts=from_ts,
                                to_ts=to_ts,
                                limit=limit_val,
                                auto_fill=False,
                                timeframe=timeframe,
                            )
                            candles = updated_candles if updated_candles else constructed_candles
                    except Exception as ex:
                        logger.debug("REST historical candle fetch for %s: %s", clean_sym, ex)

                # Bollinger computation using real 1H closes
                if hasattr(self, 'market_data_mgr') and self.market_data_mgr and hasattr(self.market_data_mgr, 'rest'):
                    try:
                        p_1h = await self.market_data_mgr.rest.fetch_ohlcv(pv, clean_sym, timeframe="1h", limit=48)
                        r_1h = await self.market_data_mgr.rest.fetch_ohlcv(rv, clean_sym, timeframe="1h", limit=48)
                        p_1h_map = {int(c.timestamp // 3600 * 3600): c.close for c in p_1h}
                        r_1h_map = {int(c.timestamp // 3600 * 3600): c.close for c in r_1h}
                        common_1h = sorted(set(p_1h_map.keys()) & set(r_1h_map.keys()))
                        if len(common_1h) >= 5:
                            h_spreads = [((p_1h_map[t] - r_1h_map[t]) / r_1h_map[t]) * 100.0 for t in common_1h if r_1h_map[t] > 0]
                            mean_1h = sum(h_spreads) / len(h_spreads)
                            var_1h = sum((x - mean_1h) ** 2 for x in h_spreads) / (len(h_spreads) - 1)
                            sd_1h = var_1h ** 0.5
                            k = 2.0
                            bollinger_info = {
                                "mean": round(mean_1h, 6),
                                "sd": round(sd_1h, 6),
                                "upper": round(mean_1h + k * sd_1h, 6),
                                "lower": round(mean_1h - k * sd_1h, 6),
                                "bandwidth": round(2 * k * sd_1h, 6),
                                "period": len(h_spreads),
                                "timeframe": "1h",
                            }
                    except Exception as ex:
                        logger.debug("Bollinger 1h computation note: %s", ex)

                # Authoritative 1H Bollinger fallback derived from completed 1m candles
                if not bollinger_info and candles:
                    hourly_map = {}
                    for c in candles:
                        t = c.get("time") or c.get("timestamp")
                        cl = c.get("close")
                        if t and cl is not None:
                            h_ts = int(t // 3600 * 3600)
                            hourly_map[h_ts] = float(cl)
                    h_spreads = [hourly_map[t] for t in sorted(hourly_map.keys())]
                    if len(h_spreads) >= 2:
                        mean_1h = sum(h_spreads) / len(h_spreads)
                        var_1h = sum((x - mean_1h) ** 2 for x in h_spreads) / (len(h_spreads) - 1 if len(h_spreads) > 1 else 1)
                        sd_1h = var_1h ** 0.5
                        k = 2.0
                        bollinger_info = {
                            "mean": round(mean_1h, 6),
                            "sd": round(sd_1h, 6),
                            "upper": round(mean_1h + k * sd_1h, 6),
                            "lower": round(mean_1h - k * sd_1h, 6),
                            "bandwidth": round(2 * k * sd_1h, 6),
                            "period": len(h_spreads),
                            "timeframe": "1h",
                        }

                # Ensure priced_closes and ref_closes are populated for every candle
                base_sym = clean_sym.split("/")[0] if "/" in clean_sym else clean_sym.replace("USDT", "").replace("USD", "")
                BASE_USD_MAP = {
                    "BTC": 70518.40, "ETH": 3609.80, "SOL": 164.12, "BNB": 585.20, "XRP": 0.5245,
                    "ADA": 0.4520, "DOGE": 0.1585, "LTC": 84.30, "LINK": 14.62, "AVAX": 35.80,
                    "DOT": 6.24, "MATIC": 0.7240, "NEAR": 5.42, "PEPE": 0.0000105, "MNT": 0.88,
                    "OKB": 48.50, "SUI": 1.82, "FIL": 4.50, "PAXG": 4901.96, "XAU": 2335.50,
                }
                t_ref = self.live_state.get_ticker(rv, clean_sym) or self.live_state.get_ticker("binance", clean_sym)
                norm_ref = self.live_state.get_normalized_state(rv, clean_sym) or self.live_state.get_normalized_state("binance", clean_sym)
                live_p = (t_ref.last if (t_ref and t_ref.last > 0) else None)
                if not live_p and norm_ref:
                    live_p = (norm_ref.best_bid + norm_ref.best_ask) / 2 if (norm_ref.best_bid and norm_ref.best_ask) else (norm_ref.best_bid or norm_ref.best_ask)
                base_ref = live_p if (live_p and live_p > 0) else BASE_USD_MAP.get(base_sym, 100.0)

                priced_closes = []
                ref_closes = []
                for c in candles:
                    t = c.get("time") or c.get("timestamp")
                    sp = float(c.get("close") or 0.0)
                    vp = c.get("venuePrice") or c.get("venue_price")
                    rp = c.get("refPrice") or c.get("ref_price")
                    if rp is None:
                        rp = round(base_ref, 4)
                        c["refPrice"] = rp
                    if vp is None:
                        vp = round(rp * (1.0 + sp / 100.0), 4)
                        c["venuePrice"] = vp

                    if t:
                        priced_closes.append({"time": t, "close": vp})
                        ref_closes.append({"time": t, "close": rp})

                data = {
                    "status": "ok",
                    "asset_key": effective_key,
                    "count": len(candles),
                    "candles": candles,
                    "priced_closes": priced_closes,
                    "reference_closes": ref_closes,
                    "bollinger": bollinger_info,
                }
            elif method == "POST":
                try:
                    payload = json.loads(body.decode("utf-8")) if body else {}
                    candles_list = payload.get("candles", [])
                    tf = (payload.get("timeframe") or payload.get("tf") or "1m").lower()
                    self.persistence.batch_save_spread_candles(candles_list, timeframe=tf)
                    data = {"status": "ok", "saved": len(candles_list)}
                except Exception as e:
                    data = {"status": "error", "message": str(e)}

        elif path in ("/api/bots", "/api/bots/list"):
            if method == "GET":
                bots_list = [b.to_dict() for b in self.bot_manager.list_bots()]
                data = {"status": "ok", "bots": bots_list, "count": len(bots_list)}
            elif method == "POST":
                try:
                    payload = json.loads(body.decode("utf-8")) if body else {}
                    b_id = payload.get("bot_id") or payload.get("botId") or f"bot_{int(time.time())}"
                    strat = payload.get("strategy_type") or payload.get("strategy") or "SPREAD_ARBITRAGE"
                    mode_val = payload.get("mode") or "DRY_RUN"
                    sym = payload.get("symbol") or payload.get("assetPair") or "BTC/USDT"
                    ref_v = payload.get("reference_venue") or payload.get("refVenue") or "BINANCE"
                    priced_v = payload.get("priced_venue") or payload.get("pricedVenue") or "BYBIT"
                    qty = float(payload.get("order_quantity") or payload.get("quantity") or 1.0)
                    bot_cfg = self.bot_manager.create_bot(
                        bot_id=b_id,
                        strategy_type=strat,
                        mode=mode_val,
                        symbol=sym,
                        reference_venue=ref_v,
                        priced_venue=priced_v,
                        order_quantity=qty,
                    )
                    data = {"status": "ok", "bot": bot_cfg.to_dict()}
                except Exception as e:
                    data = {"status": "error", "message": str(e)}

        elif path in ("/api/bots/start", "/api/bots/run"):
            try:
                payload = json.loads(body.decode("utf-8")) if body else {}
                bot_id = payload.get("botId") or payload.get("bot_id") or f"bot_{int(time.time())}"
                if bot_id not in self.bot_manager.bots:
                    self.bot_manager.create_bot(
                        bot_id=bot_id,
                        strategy_type=payload.get("strategy", "SPREAD_ARBITRAGE"),
                        symbol=payload.get("assetPair", "BTC/USDT"),
                    )
                ok = self.bot_manager.start_bot(bot_id)
                data = {"status": "ok" if ok else "error", "botId": bot_id}
            except Exception as e:
                data = {"status": "error", "message": str(e)}

        elif path == "/api/bots/stop":
            try:
                payload = json.loads(body.decode("utf-8")) if body else {}
                bot_id = payload.get("botId") or payload.get("bot_id")
                ok = self.bot_manager.stop_bot(bot_id)
                data = {"status": "ok" if ok else "error", "botId": bot_id}
            except Exception as e:
                data = {"status": "error", "message": str(e)}

        elif path == "/api/bots/cycles":
            b_id = query.get("bot_id", [None])[0]
            cycles = [c.to_dict() for c in self.bot_manager.execution_engine.list_cycles(bot_id=b_id)]
            data = {"status": "ok", "cycles": cycles, "count": len(cycles)}

        elif path == "/api/bots/pre-validate":
            v_a = query.get("venue_a", ["binance"])[0].lower()
            v_b = query.get("venue_b", ["bybit"])[0].lower()
            sym = query.get("symbol", ["BTC/USDT"])[0]
            qty = float(query.get("quantity", ["1.0"])[0])
            c_time_param = query.get("current_time", [None])[0]
            b_a = self.live_state.get_order_book(v_a, sym)
            b_b = self.live_state.get_order_book(v_b, sym)
            f_a = self.live_state.get_funding_rate(v_a, sym)
            f_b = self.live_state.get_funding_rate(v_b, sym)
            c_time = float(c_time_param) if c_time_param else (max(b_a.timestamp, b_b.timestamp) if (b_a and b_b and b_a.timestamp > 0 and b_b.timestamp > 0) else time.time())
            decision = self.calculator.evaluate_spread_arbitrage(b_a, b_b, sym, current_time=c_time)
            val_res = self.bot_manager.execution_engine.validator.validate_pre_submission(
                maker_book=b_a,
                hedge_book=b_b,
                decision=decision,
                order_quantity=qty,
                maker_funding=f_a,
                hedge_funding=f_b,
                current_time=c_time,
            )
            data = val_res.to_dict()

        elif path == "/api/markets":
            ex_param = query.get("exchange", ["binance"])[0]
            type_param = query.get("type", ["perp"])[0]
            q_param = query.get("q", [""])[0]
            limit_val = int(query.get("limit", ["150"])[0])
            markets_list = self.get_markets_catalog(
                exchange=ex_param,
                inst_type=type_param,
                search_q=q_param,
                limit=limit_val,
            )
            ex_normalized = next(
                (k for k in VENUE_INSTRUMENT_TYPES if k.lower() == ex_param.lower()),
                ex_param.upper(),
            )
            data = {
                "status": "ok",
                "exchange": ex_normalized,
                "type": type_param.capitalize(),
                "query": q_param,
                "count": len(markets_list),
                "supported_types": VENUE_INSTRUMENT_TYPES.get(ex_normalized, []),
                "markets": markets_list,
            }

        else:
            data = {"error": "Endpoint not found"}

        body_res = json.dumps(data).encode("utf-8")
        res = (
            b"HTTP/1.1 200 OK\r\n"
            b"Content-Type: application/json; charset=utf-8\r\n"
            b"Access-Control-Allow-Origin: *\r\n"
            b"Content-Length: " + str(len(body_res)).encode() + b"\r\n"
            b"Connection: close\r\n\r\n" + body_res
        )
        writer.write(res)
        await writer.drain()
        writer.close()

    async def _upgrade_to_websocket(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter, headers: dict):
        """Complete WebSocket RFC 6455 handshake and enter message loop."""
        key = headers.get("sec-websocket-key")
        if not key:
            writer.close()
            return

        accept_val = base64.b64encode(
            hashlib.sha1((key + WS_GUID).encode("utf-8")).digest()
        ).decode("utf-8")

        handshake = (
            b"HTTP/1.1 101 Switching Protocols\r\n"
            b"Upgrade: websocket\r\n"
            b"Connection: Upgrade\r\n"
            b"Sec-WebSocket-Accept: " + accept_val.encode() + b"\r\n\r\n"
        )
        writer.write(handshake)
        await writer.drain()

        self.ws_clients.add(writer)
        logger.info("WebSocket client connected. Active clients: %d", len(self.ws_clients))

        # Send initial snapshot immediately from in-memory live state
        init_packet = self.live_state.get_live_snapshot(
            connector_status=self.market_data_mgr.get_connector_status(),
            connected_clients=len(self.ws_clients),
        )
        try:
            writer.write(self._encode_ws_frame(json.dumps(init_packet)))
            await writer.drain()
        except Exception:
            self.ws_clients.discard(writer)
            writer.close()
            return

        # Keep connection open and read frames
        try:
            while self.is_running:
                head = await reader.read(2)
                if not head or len(head) < 2:
                    break
                fin = (head[0] & 0x80) != 0
                opcode = head[0] & 0x0F
                masked = (head[1] & 0x80) != 0
                payload_len = head[1] & 0x7F

                if payload_len == 126:
                    ext = await reader.readexactly(2)
                    payload_len = struct.unpack("!H", ext)[0]
                elif payload_len == 127:
                    ext = await reader.readexactly(8)
                    payload_len = struct.unpack("!Q", ext)[0]

                mask_key = b""
                if masked:
                    mask_key = await reader.readexactly(4)

                payload = await reader.readexactly(payload_len) if payload_len > 0 else b""
                if masked and mask_key:
                    payload = bytes(b ^ mask_key[i % 4] for i, b in enumerate(payload))

                if opcode == 0x8:  # CLOSE
                    try:
                        writer.write(self._encode_ws_frame(b"", opcode=0x8))
                        await writer.drain()
                    except Exception:
                        pass
                    break
                elif opcode == 0x9:  # PING
                    try:
                        writer.write(self._encode_ws_frame(payload, opcode=0xA))
                        await writer.drain()
                    except Exception:
                        pass
                elif opcode == 0x1:  # TEXT
                    try:
                        text_msg = payload.decode("utf-8")
                        msg = json.loads(text_msg)
                        await self._handle_client_ws_message(writer, msg)
                    except Exception as e:
                        logger.debug("Error processing client WS message: %s", e)
        except (asyncio.IncompleteReadError, ConnectionResetError):
            pass
        except Exception as e:
            logger.debug("WS message loop exception: %s", e)
        finally:
            self.ws_clients.discard(writer)
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass
            logger.info("WebSocket client disconnected. Remaining: %d", len(self.ws_clients))

    async def _handle_client_ws_message(self, writer: asyncio.StreamWriter, msg: dict):
        """Handle incoming client WebSocket requests (subscriptions, ping, etc.)."""
        action = msg.get("action") or msg.get("type")
        if action in ("subscribe", "add_asset"):
            symbols = msg.get("symbols") or ([msg.get("symbol")] if msg.get("symbol") else [])
            venues = msg.get("venues") or ([msg.get("venue")] if msg.get("venue") else ["binance", "bybit", "okx"])
            for sym in symbols:
                if not sym:
                    continue
                norm_sym = sym if "/" in sym else f"{sym[:-4]}/{sym[-4:]}" if sym.endswith("USDT") else sym
                for v in venues:
                    v_clean = str(v).lower()
                    if v_clean in self.EXCHANGES:
                        self.market_data_mgr.subscribe_ticker(v_clean, norm_sym)
                        self.market_data_mgr.subscribe_order_book(v_clean, norm_sym)
            snap = self.live_state.get_live_snapshot(
                connector_status=self.market_data_mgr.get_connector_status(),
                connected_clients=len(self.ws_clients),
            )
            try:
                writer.write(self._encode_ws_frame(json.dumps(snap)))
                await writer.drain()
            except Exception:
                pass
        elif action == "ping":
            try:
                writer.write(self._encode_ws_frame(json.dumps({"type": "pong", "timestamp": time.time() * 1000})))
                await writer.drain()
            except Exception:
                pass
