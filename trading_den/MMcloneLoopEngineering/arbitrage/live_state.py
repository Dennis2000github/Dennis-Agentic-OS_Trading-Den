"""
In-Memory Live State Layer for DenMa Arbitrage Engine.
Maintains high-frequency live market data (order books, tickers, funding rates,
computed cross-exchange opportunities, triangular routes, and MT5 spreads)
entirely in RAM with zero disk I/O overhead.
"""

import time
from typing import Any, Dict, List, Optional, Tuple

from .models import (
    FundingRate,
    OrderBook,
    SpreadResult,
    RawSpreadResult,
    MakerHedgeCandidate,
    DirectionEvaluationResult,
    DirectionalOpportunity,
    OpportunityEvaluationResult,
    Ticker,
    TriangularOpportunity,
    NormalizedMarketState,
)
from .market_catalog import format_volume_usd


class LiveStateManager:
    """
    Centralized In-Memory Live State Store.
    Provides fast, thread-safe in-memory caching and change detection
    for real-time order books, tickers, spreads, and opportunities.
    """

    def __init__(self):
        # Hot in-memory caches: key = f"{VENUE}:{SYMBOL}"
        self._books: Dict[str, OrderBook] = {}
        self._tickers: Dict[str, Ticker] = {}
        self._funding: Dict[str, FundingRate] = {}
        self._normalized_states: Dict[str, NormalizedMarketState] = {}
        self._raw_spreads: Dict[str, RawSpreadResult] = {}
        self._direction_candidates: Dict[str, DirectionEvaluationResult] = {}
        self._directional_opportunities: Dict[str, OpportunityEvaluationResult] = {}

        # Computed live opportunities
        self._opportunities: List[dict] = []
        self._triangular_routes: List[dict] = []
        self._mt5_spreads: List[dict] = []
        self._funding_matrix: List[dict] = []

        # Versioning and timestamping for change detection and cache invalidation
        self._version: int = 0
        self._last_opportunity_update: float = 0.0
        self._last_update: float = time.time()

        # Step 12 Strategy Modes and Decisions
        self._strategy_mode: str = "SPREAD"
        self._spread_decisions: Dict[str, dict] = {}
        self._funding_decisions: Dict[str, dict] = {}

    # -------------------------------------------------------------------------
    # Order Books & Tickers (In-Memory Hot Path)
    # -------------------------------------------------------------------------

    def set_order_book(self, venue: str, symbol: str, book: OrderBook) -> bool:
        """Store order book in memory. Returns True if best bid/ask changed."""
        key = f"{venue.upper()}:{symbol}"
        prev = self._books.get(key)
        changed = (
            prev is None
            or prev.best_bid != book.best_bid
            or prev.best_ask != book.best_ask
        )
        self._books[key] = book
        self._last_update = time.time()
        if changed:
            self._version += 1
        return changed

    def get_order_book(self, venue: str, symbol: str) -> Optional[OrderBook]:
        """Retrieve latest order book from in-memory cache."""
        return self._books.get(f"{venue.upper()}:{symbol}")

    def get_all_books(self) -> Dict[str, OrderBook]:
        """Return a copy of all cached books."""
        return dict(self._books)

    def set_ticker(self, venue: str, symbol: str, ticker: Ticker):
        """Store ticker quote in memory."""
        key = f"{venue.upper()}:{symbol}"
        self._tickers[key] = ticker
        self._last_update = time.time()

    def get_ticker(self, venue: str, symbol: str) -> Optional[Ticker]:
        """Retrieve latest ticker from memory."""
        return self._tickers.get(f"{venue.upper()}:{symbol}")

    def get_all_tickers(self) -> Dict[str, Ticker]:
        """Return copy of all cached tickers."""
        return dict(self._tickers)

    def set_funding_rate(self, venue: str, symbol: str, rate: FundingRate):
        """Store funding rate in memory."""
        key = f"{venue.upper()}:{symbol}"
        self._funding[key] = rate
        self._last_update = time.time()

    def get_funding_rate(self, venue: str, symbol: str) -> Optional[FundingRate]:
        """Retrieve funding rate from memory."""
        return self._funding.get(f"{venue.upper()}:{symbol}")

    def get_all_funding(self) -> Dict[str, FundingRate]:
        """Return copy of all cached funding rates."""
        return dict(self._funding)

    # -------------------------------------------------------------------------
    # Normalized Market State (Step 2 Hybrid State)
    # -------------------------------------------------------------------------

    def set_normalized_state(self, venue: str, symbol: str, state: NormalizedMarketState):
        """Store or update complete normalized state for venue:symbol."""
        key = f"{venue.upper()}:{symbol}"
        self._normalized_states[key] = state
        self._last_update = time.time()

    def get_normalized_state(self, venue: str, symbol: str) -> Optional[NormalizedMarketState]:
        """Retrieve normalized state for venue:symbol."""
        return self._normalized_states.get(f"{venue.upper()}:{symbol}")

    def get_all_normalized_states(self) -> Dict[str, NormalizedMarketState]:
        """Return copy of all normalized states."""
        return dict(self._normalized_states)

    def is_symbol_ready(self, venue: str, symbol: str, stale_threshold_seconds: float = 10.0) -> bool:
        """Check whether a venue:symbol pair is execution-ready."""
        state = self.get_normalized_state(venue, symbol)
        if state is None:
            return False
        return state.evaluate_readiness(stale_threshold_seconds=stale_threshold_seconds)

    # -------------------------------------------------------------------------
    # Raw Spreads (Step 5 - Reference/Priced Relationships)
    # -------------------------------------------------------------------------

    def set_raw_spread(self, spread: RawSpreadResult) -> bool:
        """Store raw spread calculation. Returns True if spread/validity/direction changed."""
        key = f"{spread.reference_venue.upper()}->{spread.priced_venue.upper()}:{spread.symbol.upper()}"
        prev = self._raw_spreads.get(key)
        changed = (
            prev is None
            or prev.raw_spread_pct != spread.raw_spread_pct
            or prev.validity != spread.validity
            or prev.direction != spread.direction
        )
        self._raw_spreads[key] = spread
        self._last_update = time.time()
        if changed:
            self._version += 1
        return changed

    def get_raw_spread(self, ref_venue: str, priced_venue: str, symbol: str) -> Optional[RawSpreadResult]:
        """Lookup raw spread for a specific reference venue, priced venue, and symbol."""
        key = f"{ref_venue.upper()}->{priced_venue.upper()}:{symbol.upper()}"
        if key in self._raw_spreads:
            return self._raw_spreads[key]
        if "/" in symbol:
            alt_key = f"{ref_venue.upper()}->{priced_venue.upper()}:{symbol.replace('/', '').upper()}"
            if alt_key in self._raw_spreads:
                return self._raw_spreads[alt_key]
        else:
            # If input had no slash, also check slash version
            for k, val in self._raw_spreads.items():
                if k.startswith(f"{ref_venue.upper()}->{priced_venue.upper()}:"):
                    k_sym = k.split(":", 1)[1]
                    if k_sym.replace("/", "") == symbol.upper():
                        return val
        return None

    def get_all_raw_spreads(self) -> List[RawSpreadResult]:
        """Return list of all current raw spread results."""
        return list(self._raw_spreads.values())

    def get_all_raw_spreads_dict(self) -> Dict[str, dict]:
        """Return dict of all current raw spreads keyed by REF->PRICED:SYM and normalized."""
        out: Dict[str, dict] = {}
        for key, spread in self._raw_spreads.items():
            d = spread.to_dict()
            out[key] = d
            if "/" in key:
                noslash = key.replace("/", "")
                if noslash != key:
                    out[noslash] = d
        return out

    # -------------------------------------------------------------------------
    # Direction Candidates (Step 6 - Dynamic Maker/Hedge)
    # -------------------------------------------------------------------------

    def set_direction_candidates(self, result: DirectionEvaluationResult) -> bool:
        """Store directional execution candidates. Returns True if candidates changed."""
        key = f"{result.venue_a.upper()}<->{result.venue_b.upper()}:{result.symbol.upper()}"
        prev = self._direction_candidates.get(key)
        changed = (
            prev is None
            or prev.candidate_a.validity != result.candidate_a.validity
            or prev.candidate_a.maker_price != result.candidate_a.maker_price
            or prev.candidate_b.maker_price != result.candidate_b.maker_price
        )
        self._direction_candidates[key] = result
        self._last_update = time.time()
        if changed:
            self._version += 1
        return changed

    def get_direction_candidates(self, venue_a: str, venue_b: str, symbol: str) -> Optional[DirectionEvaluationResult]:
        """Lookup directional execution candidates between two venues for a symbol."""
        v_a = venue_a.upper()
        v_b = venue_b.upper()
        sym = symbol.upper()
        key1 = f"{v_a}<->{v_b}:{sym}"
        key2 = f"{v_b}<->{v_a}:{sym}"
        if key1 in self._direction_candidates:
            return self._direction_candidates[key1]
        if key2 in self._direction_candidates:
            return self._direction_candidates[key2]
        if "/" in sym:
            noslash = sym.replace("/", "")
            k1 = f"{v_a}<->{v_b}:{noslash}"
            k2 = f"{v_b}<->{v_a}:{noslash}"
            if k1 in self._direction_candidates:
                return self._direction_candidates[k1]
            if k2 in self._direction_candidates:
                return self._direction_candidates[k2]
        return None

    def get_all_direction_candidates_dict(self) -> Dict[str, dict]:
        """Return dict of all evaluated direction candidates."""
        out: Dict[str, dict] = {}
        for key, res in self._direction_candidates.items():
            d = res.to_dict()
            out[key] = d
            if "/" in key:
                noslash = key.replace("/", "")
                if noslash != key:
                    out[noslash] = d
        return out

    # -------------------------------------------------------------------------
    # Step 8: Directional Opportunities (In-Memory Hot Path)
    # -------------------------------------------------------------------------

    def set_directional_opportunities(self, result: OpportunityEvaluationResult) -> bool:
        """Store evaluated directional opportunities result in memory."""
        v_a = result.venue_a.upper()
        v_b = result.venue_b.upper()
        sym = result.symbol.upper()
        key = f"{v_a}<->{v_b}:{sym}"
        prev = self._directional_opportunities.get(key)
        changed = (
            prev is None
            or prev.validity != result.validity
            or (prev.highest_net_opportunity is not None and result.highest_net_opportunity is not None and
                prev.highest_net_opportunity.net_spread_pct != result.highest_net_opportunity.net_spread_pct)
        )
        self._directional_opportunities[key] = result
        self._last_update = time.time()
        if changed:
            self._version += 1
        return changed

    def get_directional_opportunities(self, venue_a: str, venue_b: str, symbol: str) -> Optional[OpportunityEvaluationResult]:
        """Lookup directional opportunities between two venues for a symbol."""
        v_a = venue_a.upper()
        v_b = venue_b.upper()
        sym = symbol.upper()
        key1 = f"{v_a}<->{v_b}:{sym}"
        key2 = f"{v_b}<->{v_a}:{sym}"
        if key1 in self._directional_opportunities:
            return self._directional_opportunities[key1]
        if key2 in self._directional_opportunities:
            return self._directional_opportunities[key2]
        if "/" in sym:
            noslash = sym.replace("/", "")
            k1 = f"{v_a}<->{v_b}:{noslash}"
            k2 = f"{v_b}<->{v_a}:{noslash}"
            if k1 in self._directional_opportunities:
                return self._directional_opportunities[k1]
            if k2 in self._directional_opportunities:
                return self._directional_opportunities[k2]
        return None

    def get_all_directional_opportunities_dict(self) -> Dict[str, dict]:
        """Return dict of all evaluated directional opportunities."""
        out: Dict[str, dict] = {}
        for key, res in self._directional_opportunities.items():
            d = res.to_dict()
            out[key] = d
            if "/" in key:
                noslash = key.replace("/", "")
                if noslash != key:
                    out[noslash] = d
        return out

    # -------------------------------------------------------------------------
    # Computed Live Opportunities (In-Memory Hot Path)
    # -------------------------------------------------------------------------

    def set_opportunities(self, opps: List[dict]):
        """Update live screener opportunities in memory."""
        self._opportunities = opps
        self._last_opportunity_update = time.time()
        self._version += 1

    def get_opportunities(self) -> List[dict]:
        """Get copy of current screener opportunities."""
        return list(self._opportunities)

    def set_triangular_routes(self, routes: List[dict]):
        """Update live triangular routes in memory."""
        self._triangular_routes = routes

    def get_triangular_routes(self) -> List[dict]:
        """Get copy of triangular routes."""
        return list(self._triangular_routes)

    def set_mt5_spreads(self, spreads: List[dict]):
        """Update live MT5 reference spreads in memory."""
        self._mt5_spreads = spreads

    def get_mt5_spreads(self) -> List[dict]:
        """Get copy of MT5 reference spreads."""
        return list(self._mt5_spreads)

    def set_funding_matrix(self, matrix: List[dict]):
        """Update live funding matrix in memory."""
        self._funding_matrix = matrix

    def get_funding_matrix(self) -> List[dict]:
        """Get copy of funding matrix."""
        return list(self._funding_matrix)

    # -------------------------------------------------------------------------
    # Strategy Modes & Decisions (Step 12 Integration)
    # -------------------------------------------------------------------------

    def set_strategy_mode(self, mode: str) -> bool:
        """Set active strategy mode ('SPREAD' or 'FUNDING')."""
        norm_mode = mode.upper().strip() if mode else "SPREAD"
        if norm_mode in ("SPREAD", "SPREAD_ARBITRAGE"):
            norm_mode = "SPREAD"
        elif norm_mode in ("FUNDING", "FUNDING_ARBITRAGE"):
            norm_mode = "FUNDING"
        else:
            norm_mode = "SPREAD"
        changed = (self._strategy_mode != norm_mode)
        self._strategy_mode = norm_mode
        if changed:
            self._version += 1
        return changed

    def get_strategy_mode(self) -> str:
        """Get current strategy mode ('SPREAD' or 'FUNDING')."""
        return self._strategy_mode

    def set_spread_decisions(self, decisions: Dict[str, dict]):
        """Update live Spread Arbitrage decisions."""
        self._spread_decisions = decisions

    def get_spread_decisions(self) -> Dict[str, dict]:
        """Get copy of Spread Arbitrage decisions."""
        return dict(self._spread_decisions)

    def set_funding_decisions(self, decisions: Dict[str, dict]):
        """Update live Funding Arbitrage decisions."""
        self._funding_decisions = decisions

    def get_funding_decisions(self) -> Dict[str, dict]:
        """Get copy of Funding Arbitrage decisions."""
        return dict(self._funding_decisions)

    # -------------------------------------------------------------------------
    # Unified Snapshot Generation (Zero Disk I/O)
    # -------------------------------------------------------------------------

    def get_all_tickers_dict(self) -> Dict[str, dict]:
        """Serialize all in-memory tickers into light JSON dict with dual keys (with and without slash)."""
        out = {}
        for key, t in self._tickers.items():
            entry = {
                "symbol": t.symbol,
                "venue": t.venue,
                "last": t.last or ((t.bid + t.ask) / 2 if (t.bid and t.ask) else 0.0),
                "bid": t.bid,
                "ask": t.ask,
                "bid_size": t.bid_size,
                "ask_size": t.ask_size,
                "change24h": t.change_24h_pct,
                "volume24h": t.volume_24h,
                "volumeFormatted": format_volume_usd(t.volume_24h) if t.volume_24h > 0 else "-",
                "high24h": t.high_24h,
                "low24h": t.low_24h,
                "timestamp": t.timestamp,
                "source": t.source,
            }
            out[key] = entry
            noslash = key.replace("/", "")
            if noslash != key:
                out[noslash] = entry

        # Supplement with normalized states
        for key, s in self._normalized_states.items():
            if key not in out:
                mid = (s.best_bid + s.best_ask) / 2.0 if (s.best_bid > 0 and s.best_ask > 0) else (s.best_bid or s.best_ask or 0.0)
                if mid > 0:
                    entry = {
                        "symbol": s.symbol,
                        "venue": s.venue,
                        "last": mid,
                        "bid": s.best_bid,
                        "ask": s.best_ask,
                        "bid_size": s.bid_size,
                        "ask_size": s.ask_size,
                        "change24h": 0.0,
                        "volume24h": s.volume_24h,
                        "volumeFormatted": format_volume_usd(s.volume_24h) if s.volume_24h > 0 else "-",
                        "high24h": 0.0,
                        "low24h": 0.0,
                        "timestamp": s.timestamp,
                        "source": "NORMALIZED_WS",
                    }
                    out[key] = entry
                    noslash = key.replace("/", "")
                    if noslash != key:
                        out[noslash] = entry
        return out

    def get_live_prices_summary(self) -> Dict[str, dict]:
        """
        Aggregate multi-venue prices per base asset symbol for instant frontend consumption.
        Produces { "BTC": { "mid": ..., "ref": ..., "spread": ..., "venues": {...}, "volumeFormatted": ..., "change24h": ... } }
        """
        summary: Dict[str, dict] = {}
        known_coins = [
            "BTC", "ETH", "SOL", "XRP", "DOGE", "ADA", "AVAX", "LINK", "LTC", "NEAR",
            "BNB", "DOT", "MATIC", "SUI", "PEPE", "SHIB", "FIL", "OKB", "MNT"
        ]

        coin_venues: Dict[str, Dict[str, float]] = {c: {} for c in known_coins}
        coin_vols: Dict[str, float] = {c: 0.0 for c in known_coins}
        coin_chgs: Dict[str, float] = {c: 0.0 for c in known_coins}

        # 1. From tickers
        for key, t in self._tickers.items():
            sym = t.symbol.upper()
            base = sym.split("/")[0] if "/" in sym else sym.replace("USDT", "").replace("USD", "")
            if base not in coin_venues:
                coin_venues[base] = {}
                coin_vols[base] = 0.0
                coin_chgs[base] = 0.0

            p = t.last or ((t.bid + t.ask) / 2 if (t.bid and t.ask) else 0.0)
            if p > 0:
                v_clean = t.venue.upper()
                coin_venues[base][v_clean] = p
                v_name = "MEXC" if v_clean == "MEXC" else "OKX" if v_clean == "OKX" else t.venue.capitalize()
                coin_venues[base][v_name] = p
                if t.volume_24h > coin_vols[base]:
                    coin_vols[base] = t.volume_24h
                if t.change_24h_pct != 0:
                    coin_chgs[base] = t.change_24h_pct

        # 2. From normalized states
        for key, state in self._normalized_states.items():
            sym = state.symbol.upper()
            base = sym.split("/")[0] if "/" in sym else sym.replace("USDT", "").replace("USD", "")
            if base not in coin_venues:
                coin_venues[base] = {}
                coin_vols[base] = 0.0
                coin_chgs[base] = 0.0

            p = 0.0
            if state.best_bid > 0 and state.best_ask > 0:
                p = (state.best_bid + state.best_ask) / 2.0
            elif state.best_bid > 0:
                p = state.best_bid
            elif state.best_ask > 0:
                p = state.best_ask

            if p > 0:
                v_clean = state.venue.upper()
                if v_clean not in coin_venues[base]:
                    coin_venues[base][v_clean] = p
                v_name = "MEXC" if v_clean == "MEXC" else "OKX" if v_clean == "OKX" else state.venue.capitalize()
                if v_name not in coin_venues[base]:
                    coin_venues[base][v_name] = p
                if state.volume_24h > coin_vols[base]:
                    coin_vols[base] = state.volume_24h

        # 3. From in-memory order books
        for key, bk in self._books.items():
            sym = bk.symbol.upper()
            base = sym.split("/")[0] if "/" in sym else sym.replace("USDT", "").replace("USD", "")
            if base not in coin_venues:
                coin_venues[base] = {}
                coin_vols[base] = 0.0
                coin_chgs[base] = 0.0

            p = bk.mid_price or 0.0
            if p > 0:
                v_clean = bk.venue.upper()
                if v_clean not in coin_venues[base]:
                    coin_venues[base][v_clean] = p
                v_name = "MEXC" if v_clean == "MEXC" else "OKX" if v_clean == "OKX" else bk.venue.capitalize()
                if v_name not in coin_venues[base]:
                    coin_venues[base][v_name] = p

        for item in self._mt5_spreads:
            sym = str(item.get("symbol", "")).upper()
            base = sym.replace("USD", "").replace("/USDT", "")
            mt5_val = item.get("mt5_mid_converted") or item.get("mt5_mid")
            if base in coin_venues and mt5_val:
                coin_venues[base]["MT5"] = float(mt5_val)

        for base, venues in coin_venues.items():
            if not venues:
                continue

            ref_price = (
                venues.get("Binance") or venues.get("BINANCE") or
                venues.get("Bybit") or venues.get("BYBIT") or
                venues.get("OKX") or
                venues.get("Kraken") or venues.get("KRAKEN") or
                venues.get("MT5") or
                list(venues.values())[0]
            )

            # Only use real prices that actually exist from real connectors. Never synthesize prices!
            exec_price = venues.get("MEXC") or venues.get("Binance") or venues.get("Bybit") or ref_price
            spread_pct = ((exec_price - ref_price) / ref_price * 100.0) if (ref_price > 0 and exec_price != ref_price) else 0.0

            summary[base] = {
                "base": base,
                "mid": exec_price,
                "ref": ref_price,
                "spread": round(spread_pct, 4),
                "venues": venues,
                "volume24h": coin_vols[base],
                "volumeFormatted": format_volume_usd(coin_vols[base]) if coin_vols[base] > 0 else "-",
                "change24h": round(coin_chgs[base], 2),
                "timestamp": time.time(),
            }

        return summary

    def get_live_snapshot(self, connector_status: Optional[dict] = None, connected_clients: int = 0) -> dict:
        """
        Assemble a complete live snapshot dictionary directly from memory.
        Used for WebSocket broadcast and REST snapshot polling.
        """
        return {
            "type": "live_snapshot",
            "timestamp": time.time() * 1000.0,
            "version": self._version,
            "opportunities": self._opportunities,
            "triangular": self._triangular_routes,
            "funding": self._funding_matrix,
            "mt5": self._mt5_spreads,
            "tickers": self.get_all_tickers_dict(),
            "prices": self.get_live_prices_summary(),
            "normalized_states": {k: v.to_dict() for k, v in self._normalized_states.items()},
            "raw_spreads": [s.to_dict() for s in self._raw_spreads.values()],
            "raw_spreads_dict": self.get_all_raw_spreads_dict(),
            "direction_candidates": self.get_all_direction_candidates_dict(),
            "directional_opportunities": self.get_all_directional_opportunities_dict(),
            "strategy_mode": self._strategy_mode,
            "spread_decisions": self._spread_decisions,
            "funding_decisions": self._funding_decisions,
            "connectors": connector_status or {},
            "connected_clients": connected_clients,
        }

    @property
    def version(self) -> int:
        return self._version

    @property
    def last_update(self) -> float:
        return self._last_update
