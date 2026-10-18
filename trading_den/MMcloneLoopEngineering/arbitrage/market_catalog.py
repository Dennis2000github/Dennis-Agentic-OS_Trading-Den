"""
Comprehensive Market Catalog and Asset Directory for DenMa Arbitrage Engine.
Provides multi-exchange market metadata, realistic baseline metrics (price, 24h volume, 24h change),
and instrument type mapping per exchange (MEXC, Binance, Bybit, OKX, Kraken, MT5)
across Spot, Perpetual Swaps (Perp), Delivery Futures, and CFDs.
Includes live connector market ingestion and universal dynamic asset resolution.
"""

import logging
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)


def format_volume_usd(vol: Optional[float]) -> str:
    """Format volume into readable human string: $38.45B, $845.20M, $120.50K, or '—' if empty."""
    if not vol or vol <= 0:
        return "—"
    if vol >= 1e9:
        return f"${vol / 1e9:.2f}B"
    elif vol >= 1e6:
        return f"${vol / 1e6:.2f}M"
    elif vol >= 1e3:
        return f"${vol / 1e3:.2f}K"
    return f"${vol:.2f}"


VENUE_INSTRUMENT_TYPES: Dict[str, List[dict]] = {
    "MEXC": [
        {"value": "Spot", "label": "Spot Market", "desc": "Direct Spot Trading"},
        {"value": "Perp", "label": "Perp (USDT-M)", "desc": "USDT-Margined Perpetuals"},
        {"value": "Futures", "label": "Futures (Delivery)", "desc": "Coin/USDT Delivery Futures"},
    ],
    "Binance": [
        {"value": "Spot", "label": "Spot Market", "desc": "Direct Spot Trading"},
        {"value": "Perp", "label": "Perp (USDT-M)", "desc": "USDT-Margined Linear Contracts"},
        {"value": "Futures", "label": "Futures (Coin-M)", "desc": "Coin-Margined Delivery"},
    ],
    "Bybit": [
        {"value": "Spot", "label": "Spot Market", "desc": "Direct Spot Trading"},
        {"value": "Perp", "label": "Perp (USDT-M)", "desc": "Linear Perpetual Swaps"},
        {"value": "Futures", "label": "Futures (Inverse)", "desc": "Inverse Delivery Contracts"},
    ],
    "OKX": [
        {"value": "Spot", "label": "Spot Market", "desc": "Direct Spot Trading"},
        {"value": "Perp", "label": "Perp (USDT Swap)", "desc": "Linear Perpetual Swaps"},
        {"value": "Futures", "label": "Futures (Delivery)", "desc": "Expiry Delivery Contracts"},
    ],
    "Kraken": [
        {"value": "Spot", "label": "Spot Market", "desc": "Direct Spot Trading"},
        {"value": "Perp", "label": "Perp (Linear)", "desc": "Linear Perpetual Swaps"},
        {"value": "Futures", "label": "Futures (Quarterly)", "desc": "Fixed Expiry Futures"},
    ],
    "MT5": [
        {"value": "CFD", "label": "CFD (Forex/Metals/Index)", "desc": "Contracts for Difference"},
        {"value": "Spot", "label": "Spot Forex & Metals", "desc": "Over-The-Counter Spot"},
        {"value": "Futures", "label": "Index & Commodity Futures", "desc": "Exchange Futures CFD"},
    ],
}


# Top 100+ liquid cryptocurrency assets
TOP_CRYPTO_ASSETS = [
    ("BTC", "Bitcoin", 70518.40, 2.45, 38450000000.0),
    ("ETH", "Ethereum", 3609.80, 1.85, 16800000000.0),
    ("SOL", "Solana", 164.12, -0.92, 6250000000.0),
    ("BNB", "BNB", 585.20, 1.10, 1850000000.0),
    ("XRP", "XRP", 0.5245, 0.65, 2850000000.0),
    ("DOGE", "Dogecoin", 0.1585, -1.20, 2100000000.0),
    ("ADA", "Cardano", 0.4520, 1.15, 980500000.0),
    ("AVAX", "Avalanche", 35.80, 3.20, 1250000000.0),
    ("SUI", "Sui", 1.82, 4.60, 1150000000.0),
    ("LINK", "Chainlink", 14.62, -0.45, 850400000.0),
    ("DOT", "Polkadot", 6.24, 0.80, 580000000.0),
    ("NEAR", "NEAR Protocol", 5.42, 3.75, 920000000.0),
    ("LTC", "Litecoin", 84.30, -0.30, 620000000.0),
    ("BCH", "Bitcoin Cash", 485.50, 1.45, 540000000.0),
    ("APT", "Aptos", 9.15, 3.85, 490000000.0),
    ("ARB", "Arbitrum", 0.852, 2.15, 410000000.0),
    ("OP", "Optimism", 1.94, 2.80, 380000000.0),
    ("TIA", "Celestia", 5.85, 4.20, 390000000.0),
    ("INJ", "Injective", 24.50, 5.10, 340000000.0),
    ("RENDER", "Render", 6.45, 3.90, 420000000.0),
    ("FET", "Artificial Superintelligence", 1.45, 7.10, 610000000.0),
    ("TAO", "Bittensor", 520.40, 6.40, 380000000.0),
    ("KAS", "Kaspa", 0.172, 5.20, 540000000.0),
    ("TON", "Toncoin", 5.82, 1.65, 780000000.0),
    ("WIF", "dogwifhat", 2.45, 8.40, 1450000000.0),
    ("PEPE", "Pepe", 0.0000105, 6.80, 1950000000.0),
    ("SHIB", "Shiba Inu", 0.0000214, 2.10, 950000000.0),
    ("BONK", "Bonk", 0.0000245, 5.60, 840000000.0),
    ("FLOKI", "Floki", 0.000165, 4.80, 520000000.0),
    ("NOT", "Notcoin", 0.0084, 6.20, 480000000.0),
    ("STX", "Stacks", 1.82, 2.90, 240000000.0),
    ("UNI", "Uniswap", 8.45, 1.75, 360000000.0),
    ("AAVE", "Aave", 158.40, 4.25, 410000000.0),
    ("MKR", "Maker", 2150.00, 1.95, 180000000.0),
    ("CRV", "Curve DAO", 0.325, 2.40, 150000000.0),
    ("ATOM", "Cosmos", 5.12, 1.10, 210000000.0),
    ("FTM", "Fantom", 0.725, 4.10, 310000000.0),
    ("ALGO", "Algorand", 0.145, 1.30, 160000000.0),
    ("FIL", "Filecoin", 4.50, 1.10, 320000000.0),
    ("ICP", "Internet Computer", 9.15, 2.20, 280000000.0),
    ("VET", "VeChain", 0.0265, 1.40, 140000000.0),
    ("HBAR", "Hedera", 0.0625, 2.80, 290000000.0),
    ("ETC", "Ethereum Classic", 22.40, 0.90, 230000000.0),
    ("XLM", "Stellar Lumens", 0.108, 1.25, 190000000.0),
    ("SEI", "Sei", 0.452, 4.80, 340000000.0),
    ("JUP", "Jupiter", 0.885, 3.40, 280000000.0),
    ("PYTH", "Pyth Network", 0.385, 2.60, 190000000.0),
    ("WLD", "Worldcoin", 2.15, 4.50, 390000000.0),
    ("RUNE", "THORChain", 4.85, 3.10, 260000000.0),
    ("PENDLE", "Pendle", 4.45, 5.60, 320000000.0),
    ("ONDO", "Ondo Finance", 0.825, 4.90, 350000000.0),
    ("ENA", "Ethena", 0.415, 6.20, 420000000.0),
    ("STRK", "Starknet", 0.445, 2.80, 180000000.0),
    ("DYDX", "dYdX", 1.24, 1.90, 140000000.0),
    ("GALA", "Gala", 0.0245, 3.10, 210000000.0),
    ("SAND", "The Sandbox", 0.315, 2.20, 170000000.0),
    ("MANA", "Decentraland", 0.345, 2.10, 160000000.0),
    ("CHZ", "Chiliz", 0.075, 1.50, 130000000.0),
    ("BLUR", "Blur", 0.225, 3.60, 150000000.0),
    ("AXS", "Axie Infinity", 5.45, 2.80, 180000000.0),
    ("GRT", "The Graph", 0.185, 2.40, 210000000.0),
    ("IMX", "Immutable", 1.55, 3.20, 230000000.0),
    ("LDO", "Lido DAO", 1.28, 2.60, 240000000.0),
    ("QNT", "Quant", 82.50, 1.40, 95000000.0),
    ("SNX", "Synthetix", 1.65, 2.10, 120000000.0),
    ("THETA", "Theta Network", 1.45, 2.50, 160000000.0),
    ("EOS", "EOS", 0.585, 0.80, 110000000.0),
    ("NEO", "NEO", 12.40, 1.60, 140000000.0),
    ("IOTA", "IOTA", 0.185, 2.20, 85000000.0),
    ("XMR", "Monero", 168.50, 1.10, 190000000.0),
    ("ZEC", "Zcash", 32.40, 1.40, 75000000.0),
    ("DASH", "Dash", 28.50, 0.90, 65000000.0),
    ("CAKE", "PancakeSwap", 1.95, 2.40, 110000000.0),
    ("1INCH", "1inch", 0.345, 1.80, 95000000.0),
    ("COMP", "Compound", 48.50, 2.10, 85000000.0),
    ("SUSHI", "SushiSwap", 0.85, 2.50, 75000000.0),
    ("YFI", "yearn.finance", 5850.00, 1.60, 45000000.0),
    ("APE", "ApeCoin", 0.92, 3.40, 180000000.0),
    ("ENS", "Ethereum Name Service", 18.50, 4.20, 220000000.0),
    ("JASMY", "JasmyCoin", 0.0215, 5.10, 310000000.0),
    ("WOO", "WOO", 0.225, 2.80, 120000000.0),
    ("ORDI", "Ordinals", 38.50, 4.60, 340000000.0),
    ("SATS", "1000SATS", 0.000285, 5.80, 290000000.0),
    ("PAXG", "PAX Gold", 2345.50, 0.45, 120000000.0),
    ("MX", "MX Token", 3.85, 4.10, 485200000.0),
    ("BOME", "BOOK OF MEME", 0.0095, 7.40, 420000000.0),
    ("POPCAT", "Popcat", 1.45, 9.20, 580000000.0),
    ("BRETT", "Brett", 0.115, 6.80, 340000000.0),
    ("MEW", "cat in a dogs world", 0.0092, 8.10, 310000000.0),
    ("W", "Wormhole", 0.325, 3.20, 210000000.0),
    ("POL", "Polygon Ecosystem Token", 0.425, 2.10, 280000000.0),
    ("NEIRO", "First Neiro On Ethereum", 0.00185, 12.40, 680000000.0),
    ("TURBO", "Turbo", 0.0088, 8.60, 320000000.0),
    ("GOAT", "Goatseus Maximus", 0.725, 14.20, 510000000.0),
    ("MOODENG", "Moo Deng", 0.485, 16.50, 490000000.0),
    ("PNUT", "Peanut the Squirrel", 1.15, 18.20, 720000000.0),
    ("ACT", "Act I The AI Prophecy", 0.545, 15.10, 450000000.0),
    ("VIRTUAL", "Virtuals Protocol", 1.85, 11.20, 380000000.0),
    ("AI16Z", "ai16z", 0.425, 13.50, 310000000.0),
]

# Comprehensive MT5 multi-asset catalog (Forex, Metals, Commodities, Indices, Crypto)
TOP_MT5_ASSETS = [
    ("XAUUSD", "Gold (Oz)", "Precious Metal CFD", "XAU", "USD", 2335.50, 0.42, 48500000000.0),
    ("XAGUSD", "Silver (Oz)", "Precious Metal CFD", "XAG", "USD", 29.40, 1.15, 8200000000.0),
    ("PAXGUSD", "PAX Gold CFD", "Precious Metal Token CFD", "PAXG", "USD", 2345.50, 0.45, 150000000.0),
    ("XPTUSD", "Platinum CFD", "Precious Metal CFD", "XPT", "USD", 1010.20, 0.65, 3200000000.0),
    ("XPDUSD", "Palladium CFD", "Precious Metal CFD", "XPD", "USD", 1022.60, -0.45, 1800000000.0),
    ("COPPER", "High Grade Copper", "Metals CFD", "COPPER", "USD", 4.52, 1.25, 5400000000.0),
    ("EURUSD", "Euro / US Dollar", "Major FX CFD", "EUR", "USD", 1.0848, -0.12, 120000000000.0),
    ("GBPUSD", "British Pound / USD", "Major FX CFD", "GBP", "USD", 1.2715, 0.22, 78000000000.0),
    ("USDJPY", "US Dollar / Yen", "Major FX CFD", "USD", "JPY", 157.35, 0.35, 95000000000.0),
    ("USDCHF", "US Dollar / Swiss Franc", "Major FX CFD", "USD", "CHF", 0.8842, -0.08, 34000000000.0),
    ("AUDUSD", "Aussie / USD", "Commodity FX CFD", "AUD", "USD", 0.6592, 0.18, 42000000000.0),
    ("USDCAD", "US Dollar / CAD", "Commodity FX CFD", "USD", "CAD", 1.3642, 0.05, 38000000000.0),
    ("NZDUSD", "NZ Dollar / USD", "Commodity FX CFD", "NZD", "USD", 0.6125, 0.15, 28000000000.0),
    ("EURGBP", "Euro / British Pound", "Minor FX Cross CFD", "EUR", "GBP", 0.8532, -0.15, 3200000000.0),
    ("EURJPY", "Euro / Japanese Yen", "Minor FX Cross CFD", "EUR", "JPY", 170.68, 0.24, 4500000000.0),
    ("GBPJPY", "British Pound / Yen", "Minor FX Cross CFD", "GBP", "JPY", 200.05, 0.42, 5200000000.0),
    ("AUDJPY", "Aussie / Yen", "Minor FX Cross CFD", "AUD", "JPY", 103.72, 0.38, 2900000000.0),
    ("CADJPY", "CAD / Yen", "Minor FX Cross CFD", "CAD", "JPY", 115.34, 0.28, 2400000000.0),
    ("EURAUD", "Euro / Aussie", "Minor FX Cross CFD", "EUR", "AUD", 1.6455, -0.22, 2600000000.0),
    ("EURCAD", "Euro / CAD", "Minor FX Cross CFD", "EUR", "CAD", 1.4800, -0.10, 2200000000.0),
    ("GBPAUD", "Pound / Aussie", "Minor FX Cross CFD", "GBP", "AUD", 1.9288, 0.12, 2800000000.0),
    ("US500", "S&P 500 Index", "US Equity Index CFD", "US500", "USD", 5460.00, 0.65, 62000000000.0),
    ("US100", "Nasdaq 100", "Tech Index CFD", "US100", "USD", 19850.00, 0.95, 84000000000.0),
    ("US30", "Dow Jones 30", "Wall St Index CFD", "US30", "USD", 39150.00, 0.32, 45000000000.0),
    ("GER40", "Germany 40 DAX", "European Index CFD", "GER40", "EUR", 18420.00, 0.48, 28000000000.0),
    ("UK100", "FTSE 100", "UK Index CFD", "UK100", "GBP", 8250.00, 0.25, 19000000000.0),
    ("JPN225", "Nikkei 225", "Japan Index CFD", "JPN225", "JPY", 39120.00, 0.55, 32000000000.0),
    ("HK50", "Hang Seng Index", "Hong Kong Index CFD", "HK50", "HKD", 18240.00, -0.65, 2400000000.0),
    ("CLUSD", "WTI Crude Oil", "Energy CFD", "CL", "USD", 79.68, -0.78, 22400000000.0),
    ("BRNUSD", "Brent Crude Oil", "Energy CFD", "BRN", "USD", 78.42, -0.85, 1850000000.0),
    ("NGAS", "Natural Gas", "Energy CFD", "NG", "USD", 2.64, 1.85, 1250000000.0),
    ("BTCUSD", "Bitcoin CFD", "Crypto CFD", "BTC", "USD", 70518.40, 2.45, 1850000000.0),
    ("ETHUSD", "Ethereum CFD", "Crypto CFD", "ETH", "USD", 3609.80, 1.85, 920000000.0),
    ("SOLUSD", "Solana CFD", "Crypto CFD", "SOL", "USD", 164.08, -0.94, 820000000.0),
    ("XRPUSD", "XRP CFD", "Crypto CFD", "XRP", "USD", 0.5242, 0.63, 410000000.0),
    ("DOGEUSD", "Dogecoin CFD", "Crypto CFD", "DOGE", "USD", 0.1584, -1.22, 310000000.0),
    ("ADAUSD", "Cardano CFD", "Crypto CFD", "ADA", "USD", 0.4519, 1.13, 180000000.0),
    ("BNBUSD", "BNB CFD", "Crypto CFD", "BNB", "USD", 585.10, 1.08, 250000000.0),
    ("AVAXUSD", "Avalanche CFD", "Crypto CFD", "AVAX", "USD", 35.77, 3.16, 190000000.0),
    ("LINKUSD", "Chainlink CFD", "Crypto CFD", "LINK", "USD", 14.61, -0.47, 160000000.0),
    ("DOTUSD", "Polkadot CFD", "Crypto CFD", "DOT", "USD", 6.23, 0.78, 110000000.0),
    ("LTCUSD", "Litecoin CFD", "Crypto CFD", "LTC", "USD", 84.27, -0.32, 140000000.0),
    ("NEARUSD", "NEAR Protocol CFD", "Crypto CFD", "NEAR", "USD", 5.41, 3.72, 130000000.0),
    ("SUIUSD", "Sui CFD", "Crypto CFD", "SUI", "USD", 1.81, 4.55, 150000000.0),
    ("TONUSD", "Toncoin CFD", "Crypto CFD", "TON", "USD", 5.80, 1.60, 120000000.0),
]


def _build_exchange_catalog() -> Dict[str, Dict[str, List[dict]]]:
    catalog: Dict[str, Dict[str, List[dict]]] = {
        "mexc": {"spot": [], "perp": [], "futures": []},
        "binance": {"spot": [], "perp": [], "futures": []},
        "bybit": {"spot": [], "perp": [], "futures": []},
        "okx": {"spot": [], "perp": [], "futures": []},
        "kraken": {"spot": [], "perp": [], "futures": []},
        "mt5": {"cfd": [], "spot": [], "futures": []},
    }

    # Populate crypto exchanges without mock prices (prices populated exclusively via real live state)
    for item_tuple in TOP_CRYPTO_ASSETS:
        base = item_tuple[0]
        name = item_tuple[1]
        # MEXC
        catalog["mexc"]["spot"].append({
            "symbol": f"{base}USDT",
            "displayName": f"{base}USDT Spot",
            "name": name,
            "desc": f"{base}/USDT Spot",
            "base": base,
            "quote": "USDT",
            "price": None,
            "change24h": None,
            "volume24h": 0.0,
            "high24h": 0.0,
            "low24h": 0.0,
        })
        catalog["mexc"]["perp"].append({
            "symbol": f"{base}USDT",
            "displayName": f"{base}USDT Perpetual",
            "name": name,
            "desc": f"{base} Linear Perpetual",
            "base": base,
            "quote": "USDT",
            "price": None,
            "change24h": None,
            "volume24h": 0.0,
            "high24h": 0.0,
            "low24h": 0.0,
        })

        # BINANCE
        catalog["binance"]["spot"].append({
            "symbol": f"{base}USDT",
            "displayName": f"{base}USDT Spot",
            "name": name,
            "desc": f"{base}/USDT Spot",
            "base": base,
            "quote": "USDT",
            "price": None,
            "change24h": None,
            "volume24h": 0.0,
            "high24h": 0.0,
            "low24h": 0.0,
        })
        catalog["binance"]["perp"].append({
            "symbol": f"{base}USDT",
            "displayName": f"{base}USDT Perpetual",
            "name": name,
            "desc": f"USDT-M Linear Perpetual",
            "base": base,
            "quote": "USDT",
            "price": None,
            "change24h": None,
            "volume24h": 0.0,
            "high24h": 0.0,
            "low24h": 0.0,
        })

        # BYBIT
        catalog["bybit"]["spot"].append({
            "symbol": f"{base}USDT",
            "displayName": f"{base}USDT Spot",
            "name": name,
            "desc": f"{base}/USDT Spot",
            "base": base,
            "quote": "USDT",
            "price": None,
            "change24h": None,
            "volume24h": 0.0,
            "high24h": 0.0,
            "low24h": 0.0,
        })
        catalog["bybit"]["perp"].append({
            "symbol": f"{base}USDT",
            "displayName": f"{base}USDT Linear Perp",
            "name": name,
            "desc": f"{base} Linear Perp",
            "base": base,
            "quote": "USDT",
            "price": None,
            "change24h": None,
            "volume24h": 0.0,
            "high24h": 0.0,
            "low24h": 0.0,
        })

        # OKX
        catalog["okx"]["spot"].append({
            "symbol": f"{base}-USDT",
            "displayName": f"{base}-USDT Spot",
            "name": name,
            "desc": f"{base}/USDT Spot",
            "base": base,
            "quote": "USDT",
            "price": None,
            "change24h": None,
            "volume24h": 0.0,
            "high24h": 0.0,
            "low24h": 0.0,
        })
        catalog["okx"]["perp"].append({
            "symbol": f"{base}USDT",
            "displayName": f"{base}-USDT-SWAP",
            "name": name,
            "desc": f"{base} Linear Swap",
            "base": base,
            "quote": "USDT",
            "price": None,
            "change24h": None,
            "volume24h": 0.0,
            "high24h": 0.0,
            "low24h": 0.0,
        })

        # KRAKEN
        catalog["kraken"]["spot"].append({
            "symbol": f"{base}USD",
            "displayName": f"{base}USD Spot",
            "name": f"{name} USD",
            "desc": f"{base}/USD Spot",
            "base": base,
            "quote": "USD",
            "price": None,
            "change24h": None,
            "volume24h": 0.0,
            "high24h": 0.0,
            "low24h": 0.0,
        })
        catalog["kraken"]["perp"].append({
            "symbol": f"{base}USD",
            "displayName": f"PI_{base}USD Perp",
            "name": f"{name} Perpetual",
            "desc": f"{base} Perpetual Swap",
            "base": base,
            "quote": "USD",
            "price": None,
            "change24h": None,
            "volume24h": 0.0,
            "high24h": 0.0,
            "low24h": 0.0,
        })

        # Futures for major coins
        if base in ("BTC", "ETH", "SOL", "XRP", "DOGE", "AVAX", "ADA", "LTC"):
            for ex in ["binance", "bybit", "okx", "kraken", "mexc"]:
                f_sym = f"{base}-USD-260327" if ex == "okx" else f"{base}USD_QUARTER" if ex == "kraken" else f"{base}USD_260327"
                f_disp = f"{base} Quarterly 0327"
                catalog[ex]["futures"].append({
                    "symbol": f_sym,
                    "displayName": f_disp,
                    "name": f"{name} Quarterly Delivery",
                    "desc": f"{base} Delivery Futures",
                    "base": base,
                    "quote": "USD",
                    "price": None,
                    "change24h": None,
                    "volume24h": 0.0,
                    "high24h": 0.0,
                    "low24h": 0.0,
                })

    # Populate MT5 catalog without mock prices
    for item_tuple in TOP_MT5_ASSETS:
        sym = item_tuple[0]
        name = item_tuple[1]
        desc = item_tuple[2]
        base = item_tuple[3]
        quote = item_tuple[4]
        catalog["mt5"]["cfd"].append({
            "symbol": sym,
            "displayName": f"{sym} {name} CFD" if "CFD" not in name else f"{sym} CFD",
            "name": name,
            "desc": desc,
            "base": base,
            "quote": quote,
            "price": None,
            "change24h": None,
            "volume24h": 0.0,
            "high24h": 0.0,
            "low24h": 0.0,
        })
        if any(base.startswith(k) for k in ["EUR", "GBP", "USD", "AUD", "NZD", "CAD", "CHF", "XAU", "XAG", "BTC", "ETH"]):
            catalog["mt5"]["spot"].append({
                "symbol": sym,
                "displayName": f"{sym} Spot",
                "name": name,
                "desc": f"{name} OTC Spot",
                "base": base,
                "quote": quote,
                "price": None,
                "change24h": None,
                "volume24h": 0.0,
                "high24h": 0.0,
                "low24h": 0.0,
            })
        if any(base.startswith(k) for k in ["US", "GER", "UK", "JPN", "HK", "CL", "BRN", "NG", "XAU", "XAG"]):
            catalog["mt5"]["futures"].append({
                "symbol": sym,
                "displayName": f"{sym} Futures",
                "name": name,
                "desc": f"{name} Futures Contract",
                "base": base,
                "quote": quote,
                "price": None,
                "change24h": None,
                "volume24h": 0.0,
                "high24h": 0.0,
                "low24h": 0.0,
            })

    return catalog


BASE_MARKET_CATALOG: Dict[str, Dict[str, List[dict]]] = _build_exchange_catalog()

# Runtime cache for symbols loaded dynamically from active exchange connectors (CCXT)
CONNECTOR_MARKETS_CACHE: Dict[str, Dict[str, List[dict]]] = {
    "mexc": {"spot": [], "perp": [], "futures": []},
    "binance": {"spot": [], "perp": [], "futures": []},
    "bybit": {"spot": [], "perp": [], "futures": []},
    "okx": {"spot": [], "perp": [], "futures": []},
    "kraken": {"spot": [], "perp": [], "futures": []},
    "mt5": {"cfd": [], "spot": [], "futures": []},
}


def register_connector_markets(exchange: str, markets: dict):
    """
    Ingest markets loaded via live CCXT exchange connector.
    Allows real-time exchange symbols to be indexed and available for navigation.
    """
    ex_key = exchange.lower().strip()
    if ex_key not in CONNECTOR_MARKETS_CACHE:
        CONNECTOR_MARKETS_CACHE[ex_key] = {"spot": [], "perp": [], "futures": []}

    dest = CONNECTOR_MARKETS_CACHE[ex_key]
    existing_syms = {
        m["symbol"]
        for sub in dest.values()
        for m in sub
    }

    count = 0
    for m in markets.values():
        sym = m.get("id") or m.get("symbol", "").replace("/", "").replace(":", "")
        if not sym or sym in existing_syms:
            continue

        base = m.get("base") or sym
        quote = m.get("quote") or "USDT"
        is_swap = bool(m.get("swap") or m.get("linear") or m.get("inverse"))
        is_future = bool(m.get("future"))
        is_spot = bool(m.get("spot", True) and not is_swap and not is_future)

        type_bucket = "perp" if is_swap else "futures" if is_future else "spot"
        disp = (
            f"{sym} Perpetual" if is_swap
            else f"{sym} Delivery" if is_future
            else f"{sym} Spot"
        )

        price_val = None
        if m.get("last") and float(m["last"]) > 0:
            price_val = float(m["last"])
        elif m.get("close") and float(m["close"]) > 0:
            price_val = float(m["close"])

        vol_val = float(m.get("quoteVolume")) if (m.get("quoteVolume") and float(m["quoteVolume"]) > 0) else 0.0

        item = {
            "symbol": sym,
            "displayName": disp,
            "name": base,
            "desc": f"{base}/{quote} ({type_bucket.capitalize()})",
            "base": base,
            "quote": quote,
            "price": price_val,
            "change24h": float(m.get("percentage")) if m.get("percentage") is not None else None,
            "volume24h": vol_val,
            "high24h": float(m.get("high")) if m.get("high") is not None else 0.0,
            "low24h": float(m.get("low")) if m.get("low") is not None else 0.0,
            "exchange": ex_key.upper(),
            "instType": type_bucket.capitalize(),
        }
        dest[type_bucket].append(item)
        existing_syms.add(sym)
        count += 1

    if count > 0:
        logger.info("Registered %d connector markets for %s", count, exchange.upper())


def get_market_items(
    exchange: str,
    inst_type: str,
    search_q: str = "",
    limit: int = 100,
) -> List[dict]:
    """
    Retrieve comprehensive market items for a given exchange and instrument type.
    Combines:
    1. Base catalog of top crypto/forex instruments (100+ per venue)
    2. Dynamic live symbols ingested from CCXT exchange connectors
    3. Universal on-the-fly resolution for any queried instrument token
    """
    ex_key = exchange.lower().strip()
    type_key = inst_type.lower().strip()

    if type_key in ("perp", "perpetual", "swap"):
        type_key = "perp"
    elif type_key in ("spot",):
        type_key = "spot"
    elif type_key in ("futures", "delivery"):
        type_key = "futures"
    elif type_key in ("cfd",):
        type_key = "cfd" if ex_key == "mt5" else "perp"
    else:
        type_key = "cfd" if ex_key == "mt5" else "perp"

    # 1. Base items
    ex_dict = BASE_MARKET_CATALOG.get(ex_key) or BASE_MARKET_CATALOG.get("binance", {})
    items = list(ex_dict.get(type_key) or ex_dict.get("spot") or ex_dict.get("perp") or ex_dict.get("cfd") or [])

    # 2. Connector live items
    conn_dict = CONNECTOR_MARKETS_CACHE.get(ex_key, {})
    conn_items = conn_dict.get(type_key, [])
    if conn_items:
        existing_syms = {it["symbol"] for it in items}
        for ci in conn_items:
            if ci["symbol"] not in existing_syms:
                items.append(ci)
                existing_syms.add(ci["symbol"])

    q = (search_q or "").strip().upper()
    results = []
    seen = set()

    for raw in items:
        item = dict(raw)
        sym = item.get("symbol", "")
        if sym in seen:
            continue
        seen.add(sym)

        base = item.get("base", "")
        quote = item.get("quote", "")
        disp = item.get("displayName", sym)
        name = item.get("name", "")

        if q:
            if (
                q not in sym.upper()
                and q not in disp.upper()
                and q not in name.upper()
                and q not in base.upper()
                and q not in quote.upper()
            ):
                continue

        vol = float(item.get("volume24h") or 0.0)
        item["volumeFormatted"] = format_volume_usd(vol)
        item["exchange"] = ex_key.upper()
        item["instType"] = (
            "Perp" if type_key == "perp"
            else "Spot" if type_key == "spot"
            else "Futures" if type_key == "futures"
            else "CFD"
        )
        results.append(item)

    # 3. Universal Dynamic Fallback Resolver
    # If the user searched for ANY symbol and no exact match exists,
    # generate the matching exchange instrument contract so all symbols of an exchange are available.
    if q:
        has_exact = any(it["symbol"].upper() == q or it["base"].upper() == q for it in results)
        if not has_exact:
            clean_q = q.replace("/", "").replace("-", "").replace("_", "").strip()
            if clean_q.endswith("USDT") and len(clean_q) > 4:
                base_tok = clean_q[:-4]
                forced_quote = "USDT"
            elif clean_q.endswith("USDC") and len(clean_q) > 4:
                base_tok = clean_q[:-4]
                forced_quote = "USDC"
            elif clean_q.endswith("USD") and len(clean_q) > 3:
                base_tok = clean_q[:-3]
                forced_quote = "USD"
            elif clean_q.endswith("EUR") and len(clean_q) > 3:
                base_tok = clean_q[:-3]
                forced_quote = "EUR"
            else:
                base_tok = clean_q
                forced_quote = None

            quote = forced_quote or ("USD" if ex_key in ("kraken", "mt5") else "USDT")
            inst_label = (
                "Perp" if type_key == "perp"
                else "Spot" if type_key == "spot"
                else "Futures" if type_key == "futures"
                else "CFD"
            )

            if ex_key == "kraken":
                if type_key == "perp":
                    syn_sym = f"PI_{base_tok}USD"
                    syn_disp = f"PI_{base_tok}USD Perp"
                    syn_desc = f"{base_tok} Perpetual Swap"
                else:
                    syn_sym = f"{base_tok}{quote}"
                    syn_disp = f"{base_tok}{quote} Spot"
                    syn_desc = f"{base_tok}/{quote} Spot"
            elif ex_key == "okx":
                if type_key == "perp":
                    syn_sym = f"{base_tok}USDT"
                    syn_disp = f"{base_tok}-{quote}-SWAP"
                    syn_desc = f"{base_tok} Linear Swap"
                else:
                    syn_sym = f"{base_tok}-{quote}"
                    syn_disp = f"{base_tok}-{quote} Spot"
                    syn_desc = f"{base_tok}/{quote} Spot"
            elif ex_key == "mt5":
                syn_sym = f"{base_tok}{quote}"
                syn_disp = f"{base_tok}{quote} CFD"
                syn_desc = f"{base_tok} Contract for Difference"
            else:
                syn_sym = f"{base_tok}{quote}"
                syn_disp = f"{base_tok}{quote} Perpetual" if type_key == "perp" else f"{base_tok}{quote} Spot"
                syn_desc = f"{base_tok} Linear Perpetual" if type_key == "perp" else f"{base_tok}/{quote} Spot"

            syn_item = {
                "symbol": syn_sym,
                "displayName": syn_disp,
                "name": base_tok,
                "desc": syn_desc,
                "base": base_tok,
                "quote": quote,
                "price": None,
                "change24h": None,
                "volume24h": 0.0,
                "volumeFormatted": "—",
                "high24h": 0.0,
                "low24h": 0.0,
                "exchange": ex_key.upper(),
                "instType": inst_label,
            }
            results.insert(0, syn_item)

    if q:
        def sort_key(x):
            s = x["symbol"].upper()
            d = x["displayName"].upper()
            b = x["base"].upper()
            is_exact = 1 if (s == q or b == q) else 0
            is_prefix = 1 if (s.startswith(q) or b.startswith(q)) else 0
            vol_val = float(x.get("volume24h") or 0.0)
            return (-is_exact, -is_prefix, -vol_val)
        results.sort(key=sort_key)
    else:
        results.sort(key=lambda x: -float(x.get("volume24h") or 0.0))

    return results[:limit]
