"""
Multi-Market Scanner Module (The 5-Minute Rule).

Monitors multiple Deriv Volatility Indices: R_10, R_25, R_50, R_75, R_100.
Every 60 seconds, queries the 5-minute price change for each index:
    percentage_change = abs((current_price - price_5m_ago) / price_5m_ago * 100)
The index with the highest absolute percentage change becomes the active target_symbol.
"""

import asyncio
import json
import logging
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple
import websockets

logger = logging.getLogger("MarketScanner")


@dataclass
class MarketMomentum:
    """Momentum snapshot for a single volatility index."""
    symbol: str
    price_5m_ago: float
    current_price: float
    pct_change: float
    pip_size: int


class MultiMarketScanner:
    """
    Scans candidate Volatility Indices simultaneously to find the one leaping across
    the largest price range in the last 5 minutes.
    """

    def __init__(self, symbols: Optional[List[str]] = None, public_ws_url: str = "wss://api.derivws.com/trading/v1/options/ws/public"):
        """
        Initialize scanner.
        
        Args:
            symbols: List of volatility symbols to scan (default: R_10, R_25, R_50, R_75, R_100).
            public_ws_url: WebSocket URL to query ticks_history.
        """
        self.symbols = symbols or ["R_10", "R_25", "R_50", "R_75", "R_100"]
        self.public_ws_url = public_ws_url
        self.active_symbol: str = self.symbols[0]
        self.last_scan_results: Dict[str, MarketMomentum] = {}

    async def scan_highest_momentum(self, client=None) -> Tuple[str, float, Dict[str, MarketMomentum]]:
        """
        Scan all candidate symbols and return the one with the highest 5-minute price change.
        
        Can query either via the existing connected DerivClient or via a dedicated public WS probe.
        
        Returns:
            Tuple of (top_symbol: str, top_pct: float, all_results: Dict[str, MarketMomentum])
        """
        logger.info(f"🔍 [SCANNER] Scanning 5-minute momentum across {len(self.symbols)} indices: {self.symbols}...")
        results: Dict[str, MarketMomentum] = {}

        if client and client.is_connected:
            # Query directly through connected client
            for symbol in self.symbols:
                try:
                    res = await client.fetch_candles(symbol, granularity=60, count=6)
                    candles = res.get("candles", [])
                    pip_size = int(res.get("pip_size", 2))
                    if len(candles) >= 2:
                        p_5m = float(candles[0].get("open", 0.0))
                        p_cur = float(candles[-1].get("close", 0.0))
                        if p_5m > 0:
                            pct = abs((p_cur - p_5m) / p_5m * 100.0)
                            results[symbol] = MarketMomentum(
                                symbol=symbol,
                                price_5m_ago=p_5m,
                                current_price=p_cur,
                                pct_change=pct,
                                pip_size=pip_size
                            )
                except Exception as e:
                    logger.warning(f"Failed to scan {symbol} via client: {e}")
        else:
            # Fallback to public WS connection
            try:
                async with websockets.connect(self.public_ws_url, open_timeout=8.0) as ws:
                    for symbol in self.symbols:
                        req = {
                            "ticks_history": symbol,
                            "end": "latest",
                            "style": "candles",
                            "granularity": 60,
                            "count": 6
                        }
                        await ws.send(json.dumps(req))
                        raw = await asyncio.wait_for(ws.recv(), timeout=6.0)
                        res = json.loads(raw)
                        candles = res.get("candles", [])
                        pip_size = int(res.get("pip_size", 2))
                        if len(candles) >= 2:
                            p_5m = float(candles[0].get("open", 0.0))
                            p_cur = float(candles[-1].get("close", 0.0))
                            if p_5m > 0:
                                pct = abs((p_cur - p_5m) / p_5m * 100.0)
                                results[symbol] = MarketMomentum(
                                    symbol=symbol,
                                    price_5m_ago=p_5m,
                                    current_price=p_cur,
                                    pct_change=pct,
                                    pip_size=pip_size
                                )
            except Exception as e:
                logger.error(f"Error querying public WS during momentum scan: {e}")

        if not results:
            logger.warning(f"Scanner could not retrieve market data. Retaining active symbol: {self.active_symbol}")
            return self.active_symbol, 0.0, {}

        self.last_scan_results = results

        # Format and log the ranking
        sorted_results = sorted(results.values(), key=lambda m: m.pct_change, reverse=True)
        top = sorted_results[0]
        self.active_symbol = top.symbol

        logger.info("=" * 68)
        logger.info("📊 5-MINUTE MULTI-MARKET MOMENTUM RANKING:")
        for idx, m in enumerate(sorted_results, 1):
            winner_mark = " 🏆 [WINNER - ACTIVE]" if m.symbol == top.symbol else ""
            logger.info(
                f"   {idx}. {m.symbol:<7} | 5m Move: {m.pct_change:7.4f}% "
                f"(From {m.price_5m_ago:.{m.pip_size}f} -> {m.current_price:.{m.pip_size}f}){winner_mark}"
            )
        logger.info("=" * 68)

        return top.symbol, top.pct_change, results
