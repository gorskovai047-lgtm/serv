"""
Модуль сбора рыночного фона из источников Лактионова.

Источники (все через TradingView Scanner API — бесплатно, без ключей):
1. Тепловая карта ММВБ — какие акции сильнее/слабее рынка
2. Мировые индексы — S&P500, DAX, Nikkei, DXY
3. Товарный рынок — нефть, золото, газ, серебро

Также: ISS MOEX API для фьючерсов FORTS (нефть BR, USD Si, MIX, золото GD)
"""

import json
import urllib.request
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple

MSK = timezone(timedelta(hours=3))

# TradingView Scanner endpoints
TV_RUSSIA_URL = "https://scanner.tradingview.com/russia/scan"
TV_GLOBAL_URL = "https://scanner.tradingview.com/global/scan"

# Тикеры для мировых индексов
GLOBAL_INDICES_TICKERS = [
    "SP:SPX",        # S&P 500
    "TVC:DXY",       # Индекс доллара
    "TVC:NI225",     # Nikkei 225
    "XETR:DAX",      # DAX
    "TVC:UKX",       # FTSE 100
]

# Тикеры для товарного рынка
COMMODITIES_TICKERS = [
    "NYMEX:CL1!",    # Нефть WTI
    "TVC:UKOIL",     # Нефть Brent
    "TVC:GOLD",      # Золото
    "TVC:SILVER",    # Серебро
    "NYMEX:NG1!",    # Газ
]


class MarketBackground:
    """Сбор рыночного фона для утреннего анализа."""

    def __init__(self):
        self._cache: Dict = {}
        self._cache_time: Optional[datetime] = None
        self._cache_ttl = 120  # Кэш 2 минуты

    def get_full_background(self) -> Dict:
        """
        Собирает полный фон рынка из всех источников.
        Возвращает структурированные данные для AI-аналитика.
        """
        now = datetime.now(MSK)

        # Проверяем кэш
        if (self._cache_time and
                (now - self._cache_time).total_seconds() < self._cache_ttl):
            return self._cache

        result = {
            "heatmap": self._get_heatmap(),
            "global_indices": self._get_global_indices(),
            "commodities": self._get_commodities(),
            "forts": self._get_forts_data(),
            "summary": {},
            "timestamp": now.strftime("%H:%M МСК"),
        }

        # Формируем краткое резюме
        result["summary"] = self._build_summary(result)

        self._cache = result
        self._cache_time = now
        return result

    # ═══════════════════════════════════════════════════
    # 1. ТЕПЛОВАЯ КАРТА ММВБ
    # ═══════════════════════════════════════════════════

    def _get_heatmap(self) -> Dict:
        """Тепловая карта: топ-30 акций по капитализации с изменениями."""
        try:
            payload = json.dumps({
                "columns": ["name", "close", "change", "market_cap_basic", "sector", "volume"],
                "filter": [{"left": "market_cap_basic", "operation": "nempty"}],
                "range": [0, 30],
                "sort": {"sortBy": "market_cap_basic", "sortOrder": "desc"},
                "symbols": {"query": {"types": ["stock"]}, "tickers": []},
            }).encode()

            req = urllib.request.Request(
                TV_RUSSIA_URL, data=payload,
                headers={"Content-Type": "application/json"}
            )
            resp = urllib.request.urlopen(req, timeout=10)
            data = json.loads(resp.read())

            stocks = []
            for item in data.get("data", []):
                d = item.get("d", [])
                if len(d) >= 4:
                    ticker = item.get("s", "").replace("RUS:", "")
                    stocks.append({
                        "ticker": ticker,
                        "price": d[1],
                        "change": d[2] or 0,
                        "market_cap": d[3],
                        "sector": d[4] if len(d) > 4 else "",
                    })

            # Статистика
            greens = sum(1 for s in stocks if s["change"] > 0.3)
            reds = sum(1 for s in stocks if s["change"] < -0.3)
            neutral = len(stocks) - greens - reds

            # Лидеры роста/падения
            sorted_stocks = sorted(stocks, key=lambda x: x["change"])
            leaders_down = sorted_stocks[:3]
            leaders_up = sorted_stocks[-3:][::-1]

            return {
                "stocks": stocks,
                "greens": greens,
                "reds": reds,
                "neutral": neutral,
                "sentiment": "POSITIVE" if greens > reds * 2 else (
                    "NEGATIVE" if reds > greens * 2 else "NEUTRAL"
                ),
                "leaders_up": leaders_up,
                "leaders_down": leaders_down,
            }

        except Exception as e:
            print(f"[Background] Ошибка тепловой карты: {e}")
            return {"stocks": [], "sentiment": "UNKNOWN", "error": str(e)}

    # ═══════════════════════════════════════════════════
    # 2. МИРОВЫЕ ИНДЕКСЫ
    # ═══════════════════════════════════════════════════

    def _get_global_indices(self) -> Dict:
        """Мировые индексы: S&P500, DAX, Nikkei, DXY."""
        try:
            payload = json.dumps({
                "columns": ["name", "close", "change", "description"],
                "symbols": {"tickers": GLOBAL_INDICES_TICKERS},
            }).encode()

            req = urllib.request.Request(
                TV_GLOBAL_URL, data=payload,
                headers={"Content-Type": "application/json"}
            )
            resp = urllib.request.urlopen(req, timeout=10)
            data = json.loads(resp.read())

            indices = {}
            for item in data.get("data", []):
                s = item.get("s", "")
                d = item.get("d", [])
                if len(d) >= 3:
                    indices[s] = {
                        "name": d[0],
                        "price": d[1],
                        "change": d[2] or 0,
                    }

            # Оценка фона
            changes = [v["change"] for v in indices.values() if v["change"]]
            avg_change = sum(changes) / len(changes) if changes else 0

            return {
                "indices": indices,
                "avg_change": avg_change,
                "sentiment": "POSITIVE" if avg_change > 0.3 else (
                    "NEGATIVE" if avg_change < -0.3 else "NEUTRAL"
                ),
            }

        except Exception as e:
            print(f"[Background] Ошибка мировых индексов: {e}")
            return {"indices": {}, "sentiment": "UNKNOWN", "error": str(e)}

    # ═══════════════════════════════════════════════════
    # 3. ТОВАРНЫЙ РЫНОК
    # ═══════════════════════════════════════════════════

    def _get_commodities(self) -> Dict:
        """Товарный рынок: нефть, золото, газ, серебро."""
        try:
            payload = json.dumps({
                "columns": ["name", "close", "change", "description"],
                "symbols": {"tickers": COMMODITIES_TICKERS},
            }).encode()

            req = urllib.request.Request(
                TV_GLOBAL_URL, data=payload,
                headers={"Content-Type": "application/json"}
            )
            resp = urllib.request.urlopen(req, timeout=10)
            data = json.loads(resp.read())

            commodities = {}
            for item in data.get("data", []):
                s = item.get("s", "")
                d = item.get("d", [])
                if len(d) >= 3:
                    commodities[s] = {
                        "name": d[0],
                        "price": d[1],
                        "change": d[2] or 0,
                    }

            # Нефть Brent (приоритет)
            oil_change = 0
            for key in ["TVC:UKOIL", "NYMEX:CL1!"]:
                if key in commodities:
                    oil_change = commodities[key]["change"]
                    break

            return {
                "commodities": commodities,
                "oil_change": oil_change,
                "oil_text": f"{oil_change:+.1f}%",
                "gold_change": commodities.get("TVC:GOLD", {}).get("change", 0),
            }

        except Exception as e:
            print(f"[Background] Ошибка товарного рынка: {e}")
            return {"commodities": {}, "oil_change": 0, "error": str(e)}

    # ═══════════════════════════════════════════════════
    # 4. ФЬЮЧЕРСЫ FORTS (MOEX API)
    # ═══════════════════════════════════════════════════

    def _get_forts_data(self) -> Dict:
        """Фьючерсы FORTS: BR (нефть), Si (доллар), MX (индекс), GD (золото)."""
        result = {}
        try:
            url = ("https://iss.moex.com/iss/engines/futures/markets/forts/"
                   "securities.json?iss.only=marketdata")
            resp = urllib.request.urlopen(url, timeout=10)
            data = json.loads(resp.read())
            cols = data["marketdata"]["columns"]

            for row in data["marketdata"]["data"]:
                d = dict(zip(cols, row))
                secid = d.get("SECID", "")

                if secid.startswith("BR-") and d.get("LAST"):
                    result["oil_br"] = {
                        "price": d["LAST"],
                        "change": d.get("LASTCHANGEPRCNT", 0),
                    }
                elif secid.startswith("Si-") and d.get("LAST"):
                    result["usd_si"] = {
                        "price": d["LAST"],
                        "change": d.get("LASTCHANGEPRCNT", 0),
                    }
                elif secid.startswith("MX-") and d.get("LAST"):
                    result["mix"] = {
                        "price": d["LAST"],
                        "change": d.get("LASTCHANGEPRCNT", 0),
                    }
                elif secid.startswith("GD-") and d.get("LAST"):
                    result["gold_gd"] = {
                        "price": d["LAST"],
                        "change": d.get("LASTCHANGEPRCNT", 0),
                    }

        except Exception as e:
            print(f"[Background] Ошибка FORTS: {e}")

        return result

    # ═══════════════════════════════════════════════════
    # РЕЗЮМЕ
    # ═══════════════════════════════════════════════════

    def _build_summary(self, data: Dict) -> Dict:
        """Формирует краткое резюме для AI."""
        heatmap = data.get("heatmap", {})
        indices = data.get("global_indices", {})
        commodities = data.get("commodities", {})
        forts = data.get("forts", {})

        # Оценка общего фона
        factors = []
        if indices.get("sentiment") == "POSITIVE":
            factors.append("индексы_плюс")
        elif indices.get("sentiment") == "NEGATIVE":
            factors.append("индексы_минус")

        oil_change = commodities.get("oil_change", 0)
        if oil_change > 1:
            factors.append("нефть_плюс")
        elif oil_change < -1:
            factors.append("нефть_минус")

        if heatmap.get("sentiment") == "POSITIVE":
            factors.append("карта_зелёная")
        elif heatmap.get("sentiment") == "NEGATIVE":
            factors.append("карта_красная")

        # Итоговый вердикт
        positive_count = sum(1 for f in factors if "плюс" in f or "зелёная" in f)
        negative_count = sum(1 for f in factors if "минус" in f or "красная" in f)

        if positive_count > negative_count:
            overall = "POSITIVE"
        elif negative_count > positive_count:
            overall = "NEGATIVE"
        else:
            overall = "NEUTRAL"

        return {
            "overall": overall,
            "factors": factors,
            "oil_change": f"{oil_change:+.1f}%",
            "indices_sentiment": indices.get("sentiment", "?"),
            "heatmap_sentiment": heatmap.get("sentiment", "?"),
            "heatmap_ratio": f"🟢{heatmap.get('greens', 0)} ⚪{heatmap.get('neutral', 0)} 🔴{heatmap.get('reds', 0)}",
        }

    # ═══════════════════════════════════════════════════
    # ФОРМАТИРОВАНИЕ ДЛЯ AI И TELEGRAM
    # ═══════════════════════════════════════════════════

    def format_for_ai(self) -> Dict:
        """Форматирует данные для промпта AI-аналитика."""
        bg = self.get_full_background()
        summary = bg.get("summary", {})
        commodities = bg.get("commodities", {})
        indices = bg.get("global_indices", {})
        forts = bg.get("forts", {})

        # Текст для AI
        oil_br = forts.get("oil_br", {})
        usd_si = forts.get("usd_si", {})
        mix = forts.get("mix", {})

        return {
            "oil_change": summary.get("oil_change", "?"),
            "gold_change": f"{commodities.get('gold_change', 0):+.1f}%",
            "usd_change": f"{usd_si.get('change', 0):+.1f}%" if usd_si else "?",
            "mx_price": mix.get("price", "?"),
            "global_indices": f"{indices.get('sentiment', '?')} (avg: {indices.get('avg_change', 0):+.1f}%)",
            "heatmap_summary": summary.get("heatmap_ratio", "?"),
            "overall_sentiment": summary.get("overall", "?"),
        }

    def format_for_telegram(self) -> str:
        """Форматирует фон для отправки в Telegram."""
        bg = self.get_full_background()
        summary = bg.get("summary", {})
        indices = bg.get("global_indices", {})
        commodities = bg.get("commodities", {})
        heatmap = bg.get("heatmap", {})
        forts = bg.get("forts", {})

        overall_emoji = {"POSITIVE": "🟢", "NEGATIVE": "🔴", "NEUTRAL": "⚪"}

        msg = f"📡 <b>ФОН РЫНКА ({bg.get('timestamp', '?')})</b>\n\n"

        # Общий вердикт
        msg += f"Общий фон: {overall_emoji.get(summary.get('overall', ''), '❓')} "
        msg += f"<b>{summary.get('overall', '?')}</b>\n\n"

        # Мировые индексы
        msg += "🌍 <b>Индексы:</b>\n"
        for key, val in indices.get("indices", {}).items():
            short_name = key.split(":")[-1]
            change = val.get("change", 0)
            emoji = "🟢" if change > 0.3 else ("🔴" if change < -0.3 else "⚪")
            msg += f"  {emoji} {short_name}: {change:+.2f}%\n"

        # Товарка
        msg += "\n🛢 <b>Товарка:</b>\n"
        for key, val in commodities.get("commodities", {}).items():
            short_name = key.split(":")[-1]
            change = val.get("change", 0)
            emoji = "🟢" if change > 0.3 else ("🔴" if change < -0.3 else "⚪")
            msg += f"  {emoji} {short_name}: {val.get('price', '?')} ({change:+.1f}%)\n"

        # FORTS
        msg += "\n📊 <b>FORTS:</b>\n"
        if forts.get("oil_br"):
            msg += f"  🛢 BR: {forts['oil_br']['price']} ({forts['oil_br']['change']:+.1f}%)\n"
        if forts.get("usd_si"):
            msg += f"  💵 Si: {forts['usd_si']['price']} ({forts['usd_si']['change']:+.1f}%)\n"
        if forts.get("mix"):
            msg += f"  📈 MIX: {forts['mix']['price']} ({forts['mix']['change']:+.1f}%)\n"

        # Тепловая карта
        msg += f"\n🗺 <b>Тепловая карта:</b> {summary.get('heatmap_ratio', '?')}\n"

        # Лидеры
        if heatmap.get("leaders_up"):
            leaders = ", ".join([f"{s['ticker']} {s['change']:+.1f}%" for s in heatmap["leaders_up"][:3]])
            msg += f"  ⬆️ Лидеры: {leaders}\n"
        if heatmap.get("leaders_down"):
            leaders = ", ".join([f"{s['ticker']} {s['change']:+.1f}%" for s in heatmap["leaders_down"][:3]])
            msg += f"  ⬇️ Аутсайдеры: {leaders}\n"

        return msg

    def get_worse_than_market(self) -> List[str]:
        """Возвращает тикеры которые ХУЖЕ рынка (для приоритета шорта)."""
        bg = self.get_full_background()
        stocks = bg.get("heatmap", {}).get("stocks", [])
        if not stocks:
            return []

        avg = sum(s["change"] for s in stocks) / len(stocks) if stocks else 0
        return [s["ticker"] for s in stocks if s["change"] < avg - 0.5]

    def get_better_than_market(self) -> List[str]:
        """Возвращает тикеры которые ЛУЧШЕ рынка (НЕ шортить)."""
        bg = self.get_full_background()
        stocks = bg.get("heatmap", {}).get("stocks", [])
        if not stocks:
            return []

        avg = sum(s["change"] for s in stocks) / len(stocks) if stocks else 0
        return [s["ticker"] for s in stocks if s["change"] > avg + 0.5]
