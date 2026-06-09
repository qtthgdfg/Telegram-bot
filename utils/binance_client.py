"""
Binance Futures client wrapper.
Handles REST calls, order placement, and account queries.
"""

import time
import hmac
import hashlib
import asyncio
import aiohttp
import pandas as pd
from typing import List, Dict, Optional, Any
from urllib.parse import urlencode
from config import (BINANCE_API_KEY, BINANCE_API_SECRET,
                    BINANCE_BASE_URL, BINANCE_TESTNET)
from utils.logger import get_logger

log = get_logger("BinanceClient")


class BinanceClient:

    def __init__(self):
        self.api_key    = BINANCE_API_KEY
        self.api_secret = BINANCE_API_SECRET
        self.base_url   = BINANCE_BASE_URL
        self._session: Optional[aiohttp.ClientSession] = None

    # ──────────────────────────────────────────────────────────────────────────
    # Session management
    # ──────────────────────────────────────────────────────────────────────────

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                headers={"X-MBX-APIKEY": self.api_key},
                connector=aiohttp.TCPConnector(ssl=True),
            )
        return self._session

    async def close(self):
        if self._session and not self._session.closed:
            await self._session.close()

    # ──────────────────────────────────────────────────────────────────────────
    # Signing
    # ──────────────────────────────────────────────────────────────────────────

    def _sign(self, params: dict) -> dict:
        params["timestamp"] = int(time.time() * 1000)
        query = urlencode(params)
        params["signature"] = hmac.new(
            self.api_secret.encode(), query.encode(), hashlib.sha256
        ).hexdigest()
        return params

    # ──────────────────────────────────────────────────────────────────────────
    # HTTP helpers
    # ──────────────────────────────────────────────────────────────────────────

    async def _get(self, path: str, params: dict = None,
                   signed: bool = False) -> Any:
        session = await self._get_session()
        p = params or {}
        if signed:
            p = self._sign(p)
        async with session.get(f"{self.base_url}{path}", params=p) as r:
            data = await r.json()
            if isinstance(data, dict) and "code" in data and data["code"] < 0:
                log.error("Binance error: %s", data)
            return data

    async def _post(self, path: str, params: dict = None) -> Any:
        session = await self._get_session()
        p = self._sign(params or {})
        async with session.post(f"{self.base_url}{path}", params=p) as r:
            data = await r.json()
            if isinstance(data, dict) and "code" in data and data["code"] < 0:
                log.error("Binance POST error: %s", data)
            return data

    async def _delete(self, path: str, params: dict = None) -> Any:
        session = await self._get_session()
        p = self._sign(params or {})
        async with session.delete(f"{self.base_url}{path}", params=p) as r:
            return await r.json()

    # ──────────────────────────────────────────────────────────────────────────
    # Market data
    # ──────────────────────────────────────────────────────────────────────────

    async def get_price(self, symbol: str) -> float:
        data = await self._get("/fapi/v1/ticker/price", {"symbol": symbol})
        return float(data.get("price", 0))

    async def get_klines(self, symbol: str, interval: str,
                         limit: int = 500) -> pd.DataFrame:
        data = await self._get(
            "/fapi/v1/klines",
            {"symbol": symbol, "interval": interval, "limit": limit},
        )
        df = pd.DataFrame(data, columns=[
            "open_time","open","high","low","close","volume",
            "close_time","quote_vol","trades","taker_base","taker_quote","ignore",
        ])
        for col in ["open","high","low","close","volume"]:
            df[col] = pd.to_numeric(df[col])
        df["open_time"] = pd.to_datetime(df["open_time"], unit="ms")
        return df.set_index("open_time")

    async def get_order_book(self, symbol: str, limit: int = 20) -> Dict:
        return await self._get("/fapi/v1/depth", {"symbol": symbol, "limit": limit})

    async def get_exchange_info(self) -> Dict:
        return await self._get("/fapi/v1/exchangeInfo")

    async def get_symbol_info(self, symbol: str) -> Optional[Dict]:
        info = await self.get_exchange_info()
        for s in info.get("symbols", []):
            if s["symbol"] == symbol:
                return s
        return None

    async def get_funding_rate(self, symbol: str) -> float:
        data = await self._get("/fapi/v1/premiumIndex", {"symbol": symbol})
        return float(data.get("lastFundingRate", 0))

    async def get_open_interest(self, symbol: str) -> float:
        data = await self._get("/fapi/v1/openInterest", {"symbol": symbol})
        return float(data.get("openInterest", 0))

    # ──────────────────────────────────────────────────────────────────────────
    # Account
    # ──────────────────────────────────────────────────────────────────────────

    async def get_account(self) -> Dict:
        return await self._get("/fapi/v2/account", signed=True)

    async def get_balance(self) -> float:
        acc = await self.get_account()
        for asset in acc.get("assets", []):
            if asset["asset"] == "USDT":
                return float(asset["walletBalance"])
        return 0.0

    async def get_available_balance(self) -> float:
        acc = await self.get_account()
        for asset in acc.get("assets", []):
            if asset["asset"] == "USDT":
                return float(asset["availableBalance"])
        return 0.0

    async def get_positions(self) -> List[Dict]:
        acc = await self.get_account()
        return [p for p in acc.get("positions", [])
                if float(p.get("positionAmt", 0)) != 0]

    async def get_open_orders(self, symbol: str = None) -> List[Dict]:
        params = {}
        if symbol:
            params["symbol"] = symbol
        return await self._get("/fapi/v1/openOrders", params, signed=True)

    # ──────────────────────────────────────────────────────────────────────────
    # Order execution
    # ──────────────────────────────────────────────────────────────────────────

    async def set_leverage(self, symbol: str, leverage: int) -> Dict:
        return await self._post("/fapi/v1/leverage",
                                {"symbol": symbol, "leverage": leverage})

    async def set_margin_type(self, symbol: str, margin_type: str = "ISOLATED") -> Dict:
        try:
            return await self._post("/fapi/v1/marginType",
                                    {"symbol": symbol, "marginType": margin_type})
        except Exception:
            return {}  # already set

    async def place_market_order(self, symbol: str, side: str,
                                 quantity: float) -> Dict:
        """MARKET order — immediate fill."""
        log.info("MARKET %s %s qty=%.6f", side, symbol, quantity)
        return await self._post("/fapi/v1/order", {
            "symbol"  : symbol,
            "side"    : side,
            "type"    : "MARKET",
            "quantity": f"{quantity:.6f}",
        })

    async def place_limit_order(self, symbol: str, side: str,
                                quantity: float, price: float,
                                time_in_force: str = "GTC") -> Dict:
        log.info("LIMIT %s %s qty=%.6f @ %.6f", side, symbol, quantity, price)
        return await self._post("/fapi/v1/order", {
            "symbol"      : symbol,
            "side"        : side,
            "type"        : "LIMIT",
            "quantity"    : f"{quantity:.6f}",
            "price"       : f"{price:.6f}",
            "timeInForce" : time_in_force,
        })

    async def place_stop_market(self, symbol: str, side: str,
                                quantity: float, stop_price: float) -> Dict:
        """Stop-market order (used for SL)."""
        return await self._post("/fapi/v1/order", {
            "symbol"          : symbol,
            "side"            : side,
            "type"            : "STOP_MARKET",
            "quantity"        : f"{quantity:.6f}",
            "stopPrice"       : f"{stop_price:.6f}",
            "closePosition"   : "true",
        })

    async def place_take_profit_market(self, symbol: str, side: str,
                                       stop_price: float) -> Dict:
        return await self._post("/fapi/v1/order", {
            "symbol"        : symbol,
            "side"          : side,
            "type"          : "TAKE_PROFIT_MARKET",
            "stopPrice"     : f"{stop_price:.6f}",
            "closePosition" : "true",
        })

    async def place_trailing_stop(self, symbol: str, side: str,
                                  quantity: float,
                                  callback_rate: float) -> Dict:
        return await self._post("/fapi/v1/order", {
            "symbol"       : symbol,
            "side"         : side,
            "type"         : "TRAILING_STOP_MARKET",
            "quantity"     : f"{quantity:.6f}",
            "callbackRate" : f"{callback_rate:.1f}",
        })

    async def cancel_order(self, symbol: str, order_id: int) -> Dict:
        return await self._delete("/fapi/v1/order",
                                  {"symbol": symbol, "orderId": order_id})

    async def cancel_all_orders(self, symbol: str) -> Dict:
        return await self._delete("/fapi/v1/allOpenOrders", {"symbol": symbol})

    async def close_position(self, symbol: str,
                             position_amt: float) -> Dict:
        """Close an open position with a market order."""
        side = "SELL" if position_amt > 0 else "BUY"
        qty  = abs(position_amt)
        return await self.place_market_order(symbol, side, qty)

    # ──────────────────────────────────────────────────────────────────────────
    # Precision helpers
    # ──────────────────────────────────────────────────────────────────────────

    async def round_quantity(self, symbol: str, qty: float) -> float:
        info = await self.get_symbol_info(symbol)
        if not info:
            return round(qty, 3)
        for f in info.get("filters", []):
            if f["filterType"] == "LOT_SIZE":
                step = float(f["stepSize"])
                decimals = len(str(step).rstrip("0").split(".")[-1])
                return round(qty - (qty % step), decimals)
        return round(qty, 3)

    async def round_price(self, symbol: str, price: float) -> float:
        info = await self.get_symbol_info(symbol)
        if not info:
            return round(price, 2)
        tick = float(info.get("filters", [{}])[0].get("tickSize", "0.01"))
        decimals = len(str(tick).rstrip("0").split(".")[-1])
        return round(price - (price % tick), decimals)
