"""
DenMa Arbitrage & Market Making Engine + Live Web Dashboard.
Serves the HTML dashboard with real-time REST API endpoints and live WebSocket feeds.
Includes safe try/except fallback for MT5 on macOS while streaming live crypto data.
"""

import argparse
import asyncio
import logging
import sys

from arbitrage import MT5_AVAILABLE
from arbitrage.server import ArbitrageServer
from arbitrage.engine import ArbitrageEngine

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("app")


def print_banner(mode: str = "SERVER"):
    print("=" * 78)
    print("          DENMA ARBITRAGE & ORDER BOOK MARKET MAKING PLATFORM          ")
    print("=" * 78)
    platform_info = f"{sys.platform} (macOS Safe Mode)" if sys.platform == "darwin" else sys.platform
    mt5_status = "Available (Native Windows)" if MT5_AVAILABLE else "Guarded (macOS Simulation Fallback)"
    print(f"[*] Platform   : {platform_info}")
    print(f"[*] MT5 Driver : {mt5_status}")
    print(f"[*] Mode       : {mode}")
    print("=" * 78)


async def run_server(host: str = "127.0.0.1", port: int = 8080):
    print_banner(mode="LIVE WEB DASHBOARD & WEBSOCKET ENGINE")
    print(f"\n[+] Serving Dashboard   : http://{host}:{port}/")
    print(f"[+] WebSocket Feed      : ws://{host}:{port}/ws")
    print(f"[+] REST API Status     : http://{host}:{port}/api/status")
    print(f"[+] REST Screener Feed  : http://{host}:{port}/api/screener")
    print(f"[+] REST Triangular Feed: http://{host}:{port}/api/triangular")
    print(f"[+] REST MT5 Pricing    : http://{host}:{port}/api/mt5-pricing")
    print(f"[+] REST Historical PNL : http://{host}:{port}/api/history/pnl")
    print(f"[+] REST CSV Export     : http://{host}:{port}/api/history/export-csv\n")
    print("[*] Press Ctrl+C to stop.\n")

    server = ArbitrageServer(host=host, port=port)
    await server.start()

    try:
        while True:
            await asyncio.sleep(3600)
    except (asyncio.CancelledError, KeyboardInterrupt):
        logger.info("Stopping server...")
    finally:
        await server.stop()
        logger.info("Server stopped.")


async def run_cli_scanner():
    print_banner(mode="CLI SCANNER")
    engine = ArbitrageEngine(
        crypto_exchanges=["binance", "bybit", "okx"],
        mt5_mock_on_mac=True,
    )
    await engine.start()

    try:
        print("\n>>> 1. CROSS-EXCHANGE SPREADS (Binance vs Bybit)")
        print("-" * 78)
        for sym in ["BTC/USDT", "ETH/USDT", "SOL/USDT"]:
            spread = await engine.get_cross_exchange_spread(sym, ("binance", "bybit"))
            if spread:
                print(f"Pair: {spread.symbol:10} | Best: {spread.best_route:16}")
                print(f"  {spread.venue_a}: Bid={spread.bid_a:<10.2f} Ask={spread.ask_a:<10.2f}")
                print(f"  {spread.venue_b}: Bid={spread.bid_b:<10.2f} Ask={spread.ask_b:<10.2f}")
                print(f"  Gross: {spread.gross_spread_pct:>+7.4f}% | Net (after fees): {spread.net_spread_pct:>+7.4f}%\n")

        print(">>> 2. TRIANGULAR ARBITRAGE (Binance USDT -> BTC -> ETH -> USDT)")
        print("-" * 78)
        tri_opps = await engine.get_triangular_arbitrage("binance", "BTC/USDT", "ETH/USDT", "ETH/BTC")
        for opp in tri_opps:
            print(f"Route: {' -> '.join(opp.route)}")
            print(f"  Gross: {opp.gross_profit_pct:>+7.4f}% | Net: {opp.net_profit_pct:>+7.4f}%\n")

        print(">>> 3. REFERENCE VENUE PRICING (Crypto vs MT5)")
        print("-" * 78)
        mt5_spread = await engine.get_mt5_vs_crypto_spread("binance", "BTC/USDT", "BTCUSD")
        if mt5_spread:
            print(f"Asset: {mt5_spread['symbol']}")
            print(f"  Binance: ${mt5_spread['crypto_mid']:,.2f} | MT5: ${mt5_spread['mt5_mid_converted']:,.2f}")
            print(f"  Spread : {mt5_spread['spread_pct']:+.4f}%\n")

    finally:
        await engine.stop()


def main():
    parser = argparse.ArgumentParser(description="DenMa Arbitrage Engine & Live Dashboard")
    parser.add_argument("--cli", action="store_true", help="Run once in CLI scanner mode")
    parser.add_argument("--host", default="127.0.0.1", help="Web dashboard host (default: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=8080, help="Web dashboard port (default: 8080)")

    args = parser.parse_args()

    if args.cli:
        asyncio.run(run_cli_scanner())
    else:
        asyncio.run(run_server(host=args.host, port=args.port))


if __name__ == "__main__":
    main()
