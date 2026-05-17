"""
Модуль мониторинга ATR.
Отслеживает дневной ход акций ММВБ через ISS MOEX API.
ATR = 1% движения от цены открытия.
"""

import asyncio
import aiohttp
from datetime import datetime, timedelta, timezone
from typing import Dict, Optional, List

from config import Config

MSK = timezone(timedelta(hours=3))


class ATRMonitor:
    """Мониторинг ATR акций ММВБ."""

    def __init__(self):
        self.today_data: Dict[str, dict] = {}
        self.session: Optional[aiohttp.ClientSession] = None
        self.custom_atr: Dict[str, float] = {}

    async def start(self):
        self.session = aiohttp.ClientSession()
        print("[ATR Monitor] Запущен")

    async def stop(self):
        if self.session:
            await self.session.close()
        print("[ATR Monitor] Остановлен")

    def set_custom_atr(self, ticker: str, atr_percent: float):
        """Установить кастомный ATR% для тикера (из данных канала)."""
        self.custom_atr[ticker] = atr_percent

    def get_atr_threshold(self, ticker: str) -> float:
        return self.custom_atr.get(ticker, Config.ATR_THRESHOLD_PERCENT)

    async def fetch_moex_data(self, ticker: str) -> Optional[dict]:
        """Получить данные по тикеру с Мосбиржи (ISS API)."""
        if not self.session:
            await self.start()

        url = (
            f"https://iss.moex.com/iss/engines/stock/markets/shares/"
            f"boards/TQBR/securities/{ticker}.json"
            f"?iss.meta=off&iss.only=marketdata,securities"
        )

        try:
            async with self.session.get(url, timeout=10) as resp:
                if resp.status != 200:
                    return None
                data = await resp.json()

                md_columns = data.get("marketdata", {}).get("columns", [])
                md_data = data.get("marketdata", {}).get("data", [])
                if not md_data:
                    return None
                md_row = dict(zip(md_columns, md_data[0]))

                sec_columns = data.get("securities", {}).get("columns", [])
                sec_data = data.get("securities", {}).get("data", [])
                sec_row = dict(zip(sec_columns, sec_data[0])) if sec_data else {}

                open_price = md_row.get("OPEN") or sec_row.get("PREVPRICE") or 0
                last_price = md_row.get("LAST") or md_row.get("LCLOSEPRICE") or 0
                high_price = md_row.get("HIGH") or last_price
                low_price = md_row.get("LOW") or last_price

                if not open_price or not last_price:
                    return None

                return {
                    "open": float(open_price),
                    "high": float(high_price),
                    "low": float(low_price),
                    "last": float(last_price),
                    "volume": int(md_row.get("VOLTODAY", 0) or 0),
                    "change_pct": round(
                        ((last_price - open_price) / open_price) * 100, 3
                    ) if open_price else 0,
                }
        except Exception as e:
            print(f"[ATR Monitor] Ошибка для {ticker}: {e}")
            return None

    async def fetch_index_data(self) -> Optional[dict]:
        """Получить данные индекса ММВБ (IMOEX)."""
        if not self.session:
            await self.start()

        url = (
            "https://iss.moex.com/iss/engines/stock/markets/index/"
            "boards/SNDX/securities/IMOEX.json"
            "?iss.meta=off&iss.only=marketdata,securities"
        )

        try:
            async with self.session.get(url, timeout=10) as resp:
                if resp.status != 200:
                    return None
                data = await resp.json()

                md_columns = data.get("marketdata", {}).get("columns", [])
                md_data = data.get("marketdata", {}).get("data", [])
                if not md_data:
                    return None
                md_row = dict(zip(md_columns, md_data[0]))

                open_price = md_row.get("OPENVALUE") or md_row.get("OPEN") or 0
                last_price = md_row.get("CURRENTVALUE") or md_row.get("LAST") or 0
                high_price = md_row.get("HIGHVAL") or md_row.get("HIGH") or last_price
                low_price = md_row.get("LOWVAL") or md_row.get("LOW") or last_price

                if not open_price or not last_price:
                    return None

                return {
                    "open": float(open_price),
                    "high": float(high_price),
                    "low": float(low_price),
                    "last": float(last_price),
                    "volume": 0,
                    "change_pct": round(
                        ((last_price - open_price) / open_price) * 100, 3
                    ) if open_price else 0,
                }
        except Exception as e:
            print(f"[ATR Monitor] Ошибка индекса: {e}")
            return None

    async def check_all_tickers(self) -> List[dict]:
        """Проверить все тикеры на пробитие ATR."""
        signals = []

        index_signal = await self._check_ticker("IMOEX", is_index=True)
        if index_signal:
            signals.append(index_signal)

        for ticker in Config.MOEX_INDEX_TICKERS:
            signal = await self._check_ticker(ticker, is_index=False)
            if signal:
                signals.append(signal)
            await asyncio.sleep(0.3)

        return signals

    async def check_specific_tickers(self, tickers: List[str]) -> List[dict]:
        """Проверить конкретные тикеры."""
        signals = []
        for ticker in tickers:
            is_index = ticker == "IMOEX"
            signal = await self._check_ticker(ticker, is_index=is_index)
            if signal:
                signals.append(signal)
            await asyncio.sleep(0.3)
        return signals

    async def _check_ticker(self, ticker: str, is_index: bool = False) -> Optional[dict]:
        """Проверить один тикер на пробитие ATR."""
        if is_index:
            data = await self.fetch_index_data()
        else:
            data = await self.fetch_moex_data(ticker)

        if not data:
            return None

        prev_state = self.today_data.get(ticker, {})
        was_atr_hit = prev_state.get("atr_hit", False)

        open_price = data["open"]
        high = data["high"]
        low = data["low"]
        last = data["last"]

        max_move_up = ((high - open_price) / open_price) * 100 if open_price else 0
        max_move_down = ((open_price - low) / open_price) * 100 if open_price else 0
        max_move = max(abs(max_move_up), abs(max_move_down))
        current_move = ((last - open_price) / open_price) * 100 if open_price else 0

        threshold = self.get_atr_threshold(ticker)
        atr_hit = max_move >= threshold

        self.today_data[ticker] = {
            "open": open_price, "high": high, "low": low, "last": last,
            "max_move": max_move, "current_move": current_move,
            "atr_hit": atr_hit, "threshold": threshold,
            "last_check": datetime.now(MSK).isoformat(),
        }

        # Сигнал только если ATR пробит ВПЕРВЫЕ за день
        if atr_hit and not was_atr_hit:
            direction = "вверх" if current_move > 0 else "вниз"
            return {
                "type": "atr_exhausted",
                "ticker": ticker,
                "open": open_price, "last": last, "high": high, "low": low,
                "max_move_pct": round(max_move, 2),
                "current_move_pct": round(current_move, 2),
                "threshold_pct": threshold,
                "direction": direction,
                "time": datetime.now(MSK).strftime("%H:%M"),
            }

        return None

    def reset_daily_data(self):
        """Сброс данных в начале нового торгового дня."""
        self.today_data.clear()
        self.custom_atr.clear()
        print("[ATR Monitor] Данные дня сброшены")

    def get_status(self) -> Dict[str, dict]:
        return self.today_data.copy()

    def get_ticker_status(self, ticker: str) -> Optional[dict]:
        return self.today_data.get(ticker)

    def get_atr_summary(self) -> str:
        """Текстовая сводка по ATR."""
        if not self.today_data:
            return "Нет данных. Торги ещё не начались или данные не загружены."

        lines = ["📊 *Сводка ATR:*\n"]

        if "IMOEX" in self.today_data:
            d = self.today_data["IMOEX"]
            status = "🔴 ПРОЙДЕН" if d["atr_hit"] else "🟢 в процессе"
            lines.append(
                f"*IMOEX*: {d['current_move']:+.2f}% | "
                f"макс: {d['max_move']:.2f}% / {d['threshold']:.1f}% | {status}"
            )
            lines.append("")

        hit_tickers = []
        active_tickers = []

        for ticker, d in sorted(self.today_data.items()):
            if ticker == "IMOEX":
                continue
            if d["atr_hit"]:
                hit_tickers.append((ticker, d))
            else:
                active_tickers.append((ticker, d))

        if hit_tickers:
            lines.append("🔴 *ATR пройден:*")
            for ticker, d in hit_tickers:
                lines.append(
                    f"  {ticker}: {d['current_move']:+.2f}% "
                    f"(макс: {d['max_move']:.2f}%/{d['threshold']:.1f}%)"
                )
            lines.append("")

        if active_tickers:
            lines.append("🟢 *ATR в процессе:*")
            for ticker, d in active_tickers:
                pct_used = (d['max_move'] / d['threshold']) * 100 if d['threshold'] else 0
                bar = "▓" * int(min(pct_used, 100) / 100 * 8) + "░" * (8 - int(min(pct_used, 100) / 100 * 8))
                lines.append(
                    f"  {ticker}: {d['current_move']:+.2f}% {bar} {pct_used:.0f}%"
                )

        return "\n".join(lines)
