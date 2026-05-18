"""
AI-анализатор постов из Telegram канала.
Извлекает уровни поддержки/сопротивления, тикеры акций и данные ATR.
"""

import re
import os
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import List, Dict, Optional

from config import Config

MSK = timezone(timedelta(hours=3))


# Маппинг различных написаний тикеров к стандартным
TICKER_ALIASES = {
    "сбер": "SBER", "сбербанк": "SBER", "sber": "SBER",
    "газпром": "GAZP", "газп": "GAZP", "gazp": "GAZP",
    "лукойл": "LKOH", "лук": "LKOH", "lkoh": "LKOH",
    "норникель": "GMKN", "гмк": "GMKN", "gmkn": "GMKN",
    "новатэк": "NVTK", "nvtk": "NVTK",
    "роснефть": "ROSN", "росн": "ROSN", "rosn": "ROSN",
    "яндекс": "YNDX", "yndx": "YNDX",
    "мтс": "MTSS", "mtss": "MTSS",
    "магнит": "MGNT", "mgnt": "MGNT",
    "алроса": "ALRS", "alrs": "ALRS",
    "северсталь": "CHMF", "chmf": "CHMF",
    "сургут": "SNGS", "sngs": "SNGS",
    "сургут-п": "SNGSP", "sngsp": "SNGSP",
    "татнефть": "TATN", "tatn": "TATN",
    "татнефть-п": "TATNP", "tatnp": "TATNP",
    "полюс": "PLZL", "plzl": "PLZL",
    "полиметалл": "POLY", "poly": "POLY",
    "втб": "VTBR", "vtbr": "VTBR",
    "мосбиржа": "MOEX", "moex": "MOEX",
    "фосагро": "PHOR", "phor": "PHOR",
    "русал": "RUAL", "rual": "RUAL",
    "интер рао": "IRAO", "irao": "IRAO",
    "фск": "FEES", "fees": "FEES",
    "русгидро": "HYDR", "hydr": "HYDR",
    "аэрофлот": "AFLT", "aflt": "AFLT",
    "пик": "PIKK", "pikk": "PIKK",
    "озон": "OZON", "ozon": "OZON",
    "тинькофф": "TCSG", "tcsg": "TCSG",
    "пятёрочка": "FIVE", "five": "FIVE", "x5": "FIVE",
    "ммк": "MAGN", "magn": "MAGN",
    "нлмк": "NLMK", "nlmk": "NLMK",
    "транснефть": "TRNFP", "trnfp": "TRNFP",
    "ростелеком": "RTKM", "rtkm": "RTKM",
    "мкб": "CBOM", "cbom": "CBOM",
    "ммвб": "IMOEX", "imoex": "IMOEX", "индекс": "IMOEX",
    "индекс мосбиржи": "IMOEX",
    # Дополнительные тикеры Лактионова:
    "югк": "UGLD", "южуралзолото": "UGLD", "ugld": "UGLD",
    "мечел": "MTLR", "mtlr": "MTLR",
    "самолет": "SMLT", "самолёт": "SMLT", "smlt": "SMLT",
    "вк": "VKCO", "vkco": "VKCO", "vk": "VKCO",
    "русснефть": "RNFT", "rnft": "RNFT",
    "сегежа": "SGZH", "sgzh": "SGZH",
    "совкомфлот": "FLOT", "flot": "FLOT",
    "банк спб": "BSPB", "бспб": "BSPB", "bspb": "BSPB",
}


class PostAnalyzer:
    """Анализатор текстов постов — извлекает уровни, тикеры, ATR."""

    def __init__(self):
        self._init_db()

    def _init_db(self):
        """Инициализация SQLite базы данных."""
        os.makedirs(os.path.dirname(Config.DB_PATH), exist_ok=True)
        self.db = sqlite3.connect(Config.DB_PATH)
        self.db.execute("""
            CREATE TABLE IF NOT EXISTS levels (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                date TEXT NOT NULL,
                ticker TEXT NOT NULL,
                level_type TEXT NOT NULL,
                price REAL NOT NULL,
                source_post_id INTEGER,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)
        self.db.execute("""
            CREATE TABLE IF NOT EXISTS daily_analysis (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                date TEXT NOT NULL,
                ticker TEXT NOT NULL,
                atr_value REAL,
                open_price REAL,
                direction TEXT,
                notes TEXT,
                source_post_id INTEGER,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)
        self.db.execute("""
            CREATE TABLE IF NOT EXISTS raw_posts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                post_id INTEGER UNIQUE,
                date TEXT NOT NULL,
                text TEXT,
                analyzed INTEGER DEFAULT 0,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)
        self.db.commit()

    def analyze_posts(self, posts: List[dict]) -> Dict[str, list]:
        """
        Анализирует список постов и извлекает структурированные данные.
        """
        result = {
            "levels": [],
            "atr_data": [],
            "tickers_mentioned": set(),
            "raw_signals": [],
        }

        for post in posts:
            if not post.get("text"):
                continue

            self._save_raw_post(post)

            text = post["text"]
            post_id = post["id"]
            date_str = post["date"].strftime("%Y-%m-%d")

            tickers = self._extract_tickers(text)
            result["tickers_mentioned"].update(tickers)

            levels = self._extract_levels(text, tickers, date_str, post_id)
            result["levels"].extend(levels)

            atr_data = self._extract_atr_data(text, tickers, date_str, post_id)
            result["atr_data"].extend(atr_data)

            signals = self._extract_signals(text, tickers, date_str)
            result["raw_signals"].extend(signals)

        self._save_levels(result["levels"])
        self._save_atr_data(result["atr_data"])

        result["tickers_mentioned"] = list(result["tickers_mentioned"])

        print(
            f"[Analyzer] Обработано {len(posts)} постов: "
            f"{len(result['levels'])} уровней, "
            f"{len(result['atr_data'])} ATR записей, "
            f"{len(result['tickers_mentioned'])} тикеров"
        )

        return result

    def _extract_tickers(self, text: str) -> List[str]:
        """Извлекает тикеры из текста поста."""
        found_tickers = set()
        text_lower = text.lower()

        for alias, ticker in TICKER_ALIASES.items():
            if alias in text_lower:
                found_tickers.add(ticker)

        ticker_pattern = re.findall(r'\b([A-Z]{4,5}[P]?)\b', text)
        for t in ticker_pattern:
            if t in Config.MOEX_INDEX_TICKERS or t == "IMOEX":
                found_tickers.add(t)

        return list(found_tickers)

    def _extract_levels(
        self, text: str, tickers: List[str], date_str: str, post_id: int
    ) -> List[dict]:
        """Извлекает уровни поддержки и сопротивления из текста."""
        levels = []

        support_patterns = [
            r'подд(?:ержка|\.)?[:\s]+(\d+[\.,]?\d*)',
            r'support[:\s]+(\d+[\.,]?\d*)',
            r'лоу[:\s]+(\d+[\.,]?\d*)',
            r'мин(?:имум)?[:\s]+(\d+[\.,]?\d*)',
        ]

        resistance_patterns = [
            r'сопр(?:отивление|\.)?[:\s]+(\d+[\.,]?\d*)',
            r'resistance[:\s]+(\d+[\.,]?\d*)',
            r'хай[:\s]+(\d+[\.,]?\d*)',
            r'макс(?:имум)?[:\s]+(\d+[\.,]?\d*)',
            r'цель[:\s]+(\d+[\.,]?\d*)',
        ]

        text_lower = text.lower()

        for pattern in support_patterns:
            matches = re.findall(pattern, text_lower)
            for match in matches:
                price = float(match.replace(",", "."))
                if price > 0:
                    levels.append({
                        "date": date_str,
                        "ticker": tickers[0] if tickers else "IMOEX",
                        "level_type": "support",
                        "price": price,
                        "source_post_id": post_id,
                    })

        for pattern in resistance_patterns:
            matches = re.findall(pattern, text_lower)
            for match in matches:
                price = float(match.replace(",", "."))
                if price > 0:
                    levels.append({
                        "date": date_str,
                        "ticker": tickers[0] if tickers else "IMOEX",
                        "level_type": "resistance",
                        "price": price,
                        "source_post_id": post_id,
                    })

        # Диапазоны: "2750 - 2800"
        range_pattern = r'(\d{3,6}[\.,]?\d*)\s*[-\u2013\u2014]\s*(\d{3,6}[\.,]?\d*)'
        ranges = re.findall(range_pattern, text)
        for low, high in ranges:
            low_price = float(low.replace(",", "."))
            high_price = float(high.replace(",", "."))
            if low_price > 0 and high_price > low_price:
                ticker = tickers[0] if tickers else "IMOEX"
                levels.append({
                    "date": date_str, "ticker": ticker,
                    "level_type": "support", "price": low_price,
                    "source_post_id": post_id,
                })
                levels.append({
                    "date": date_str, "ticker": ticker,
                    "level_type": "resistance", "price": high_price,
                    "source_post_id": post_id,
                })

        return levels

    def _extract_atr_data(
        self, text: str, tickers: List[str], date_str: str, post_id: int
    ) -> List[dict]:
        """Извлекает данные ATR из текста."""
        atr_data = []
        text_lower = text.lower()

        atr_pct_patterns = [
            r'atr[:\s]+(\d+[\.,]?\d*)%',
            r'атр[:\s]+(\d+[\.,]?\d*)%',
            r'дневн(?:ой|ая)?\s+ход[:\s]+(\d+[\.,]?\d*)%',
            r'волатильность[:\s]+(\d+[\.,]?\d*)%',
        ]

        for pattern in atr_pct_patterns:
            matches = re.findall(pattern, text_lower)
            for match in matches:
                atr_val = float(match.replace(",", "."))
                for ticker in (tickers or ["IMOEX"]):
                    atr_data.append({
                        "date": date_str,
                        "ticker": ticker,
                        "atr_value": atr_val,
                        "source_post_id": post_id,
                    })

        return atr_data

    def _extract_signals(
        self, text: str, tickers: List[str], date_str: str
    ) -> List[dict]:
        """Извлекает торговые сигналы из текста."""
        signals = []
        text_lower = text.lower()

        long_keywords = ["лонг", "long", "покупка", "бай", "buy", "рост"]
        short_keywords = ["шорт", "short", "продажа", "селл", "sell", "падение"]

        direction = None
        for kw in long_keywords:
            if kw in text_lower:
                direction = "long"
                break
        if not direction:
            for kw in short_keywords:
                if kw in text_lower:
                    direction = "short"
                    break

        atr_exhausted_keywords = [
            "пик atr пройден", "пик атр пройден",
            "потенциал исчерпан", "ход исчерпан",
            "atr выбран", "атр выбран", "дневной ход выбран",
        ]

        atr_signal = None
        for kw in atr_exhausted_keywords:
            if kw in text_lower:
                atr_signal = "atr_exhausted"
                break

        if direction or atr_signal:
            signals.append({
                "date": date_str,
                "tickers": tickers,
                "direction": direction,
                "atr_signal": atr_signal,
                "text_snippet": text[:200],
            })

        return signals

    def _save_raw_post(self, post: dict):
        try:
            self.db.execute(
                "INSERT OR IGNORE INTO raw_posts (post_id, date, text) VALUES (?, ?, ?)",
                (post["id"], post["date"].isoformat(), post.get("text", "")),
            )
            self.db.commit()
        except Exception as e:
            print(f"[Analyzer] Ошибка сохранения поста: {e}")

    def _save_levels(self, levels: List[dict]):
        for level in levels:
            try:
                self.db.execute(
                    """INSERT INTO levels (date, ticker, level_type, price, source_post_id)
                       VALUES (?, ?, ?, ?, ?)""",
                    (level["date"], level["ticker"], level["level_type"],
                     level["price"], level["source_post_id"]),
                )
            except Exception as e:
                print(f"[Analyzer] Ошибка сохранения уровня: {e}")
        self.db.commit()

    def _save_atr_data(self, atr_data: List[dict]):
        for data in atr_data:
            try:
                self.db.execute(
                    """INSERT INTO daily_analysis (date, ticker, atr_value, source_post_id)
                       VALUES (?, ?, ?, ?)""",
                    (data["date"], data["ticker"], data["atr_value"],
                     data["source_post_id"]),
                )
            except Exception as e:
                print(f"[Analyzer] Ошибка сохранения ATR: {e}")
        self.db.commit()

    def get_today_levels(self, ticker: Optional[str] = None) -> List[dict]:
        """Получить уровни на сегодня."""
        today = datetime.now(MSK).strftime("%Y-%m-%d")
        query = "SELECT ticker, level_type, price FROM levels WHERE date = ?"
        params = [today]
        if ticker:
            query += " AND ticker = ?"
            params.append(ticker)
        query += " ORDER BY ticker, price"
        cursor = self.db.execute(query, params)
        return [
            {"ticker": row[0], "level_type": row[1], "price": row[2]}
            for row in cursor.fetchall()
        ]

    def get_today_atr(self, ticker: Optional[str] = None) -> List[dict]:
        """Получить ATR данные на сегодня."""
        today = datetime.now(MSK).strftime("%Y-%m-%d")
        query = "SELECT ticker, atr_value FROM daily_analysis WHERE date = ?"
        params = [today]
        if ticker:
            query += " AND ticker = ?"
            params.append(ticker)
        cursor = self.db.execute(query, params)
        return [
            {"ticker": row[0], "atr_value": row[1]}
            for row in cursor.fetchall()
        ]
