"""
Модуль отправки уведомлений через Telegram бота.
"""

import asyncio
import aiohttp
from typing import Optional
from datetime import datetime, timedelta, timezone

from config import Config

MSK = timezone(timedelta(hours=3))


class TelegramNotifier:
    """Отправка уведомлений через Telegram Bot API."""

    def __init__(self):
        self.bot_token = Config.BOT_TOKEN
        self.user_id = Config.USER_ID
        self.base_url = f"https://api.telegram.org/bot{self.bot_token}"
        self.session: Optional[aiohttp.ClientSession] = None
        self._sent_signals: set = set()

    async def start(self):
        self.session = aiohttp.ClientSession()
        print("[Notifier] Запущен")

    async def stop(self):
        if self.session:
            await self.session.close()
        print("[Notifier] Остановлен")

    async def send_message(self, text: str, parse_mode: str = "Markdown") -> bool:
        """Отправить сообщение пользователю."""
        if not self.session:
            await self.start()

        url = f"{self.base_url}/sendMessage"
        payload = {
            "chat_id": self.user_id,
            "text": text,
            "parse_mode": parse_mode,
            "disable_web_page_preview": True,
        }

        try:
            async with self.session.post(url, json=payload, timeout=10) as resp:
                if resp.status == 200:
                    return True
                else:
                    error = await resp.text()
                    print(f"[Notifier] Ошибка: {resp.status} - {error}")
                    if resp.status == 400 and "parse" in error.lower():
                        return await self._send_plain(text)
                    return False
        except Exception as e:
            print(f"[Notifier] Ошибка: {e}")
            return False

    async def _send_plain(self, text: str) -> bool:
        """Отправить без разметки (fallback)."""
        url = f"{self.base_url}/sendMessage"
        clean_text = text.replace("*", "").replace("_", "").replace("`", "")
        payload = {"chat_id": self.user_id, "text": clean_text}
        try:
            async with self.session.post(url, json=payload, timeout=10) as resp:
                return resp.status == 200
        except Exception:
            return False

    async def notify_atr_exhausted(self, signal: dict) -> bool:
        """Уведомление: ATR пройден."""
        ticker = signal["ticker"]
        signal_key = f"atr_{ticker}_{datetime.now(MSK).strftime('%Y%m%d')}"
        if signal_key in self._sent_signals:
            return False
        self._sent_signals.add(signal_key)

        text = (
            f"🔴 *ATR ПРОЙДЕН: {ticker}*\n\n"
            f"⏰ Время: {signal.get('time', '')}\n"
            f"📈 Направление: {signal.get('direction', '?')}\n"
            f"💰 Открытие: {signal.get('open', 0):.2f}\n"
            f"💰 Текущая: {signal.get('last', 0):.2f}\n"
            f"📊 Макс. ход: {signal.get('max_move_pct', 0):.2f}%\n"
            f"🎯 Порог ATR: {signal.get('threshold_pct', 1.0):.1f}%\n\n"
            f"⚠️ _Потенциал дневного хода исчерпан._\n"
            f"_Новые позиции по тренду — повышенный риск._"
        )
        return await self.send_message(text)

    async def notify_morning_analysis(self, analysis: dict) -> bool:
        """Утренняя сводка из канала."""
        lines = [
            "☀️ *Утренний разбор из канала:*",
            f"📅 {datetime.now(MSK).strftime('%d.%m.%Y')}", "",
        ]

        if analysis.get("levels"):
            lines.append("📐 *Уровни на сегодня:*")
            by_ticker = {}
            for level in analysis["levels"]:
                t = level["ticker"]
                if t not in by_ticker:
                    by_ticker[t] = {"support": [], "resistance": []}
                by_ticker[t][level["level_type"]].append(level["price"])

            for ticker, lvls in sorted(by_ticker.items()):
                support_str = ", ".join(f"{p}" for p in sorted(lvls["support"]))
                resist_str = ", ".join(f"{p}" for p in sorted(lvls["resistance"]))
                line = f"  *{ticker}*:"
                if support_str:
                    line += f" подд: {support_str}"
                if resist_str:
                    line += f" | сопр: {resist_str}"
                lines.append(line)
            lines.append("")

        if analysis.get("atr_data"):
            lines.append("📊 *ATR из канала:*")
            for atr in analysis["atr_data"]:
                lines.append(f"  {atr['ticker']}: ATR {atr['atr_value']}%")
            lines.append("")

        if analysis.get("raw_signals"):
            lines.append("🎯 *Сигналы:*")
            for sig in analysis["raw_signals"]:
                tickers_str = ", ".join(sig.get("tickers", []))
                direction = sig.get("direction", "")
                parts = []
                if direction:
                    emoji = "📈" if direction == "long" else "📉"
                    parts.append(f"{emoji} {direction}")
                if sig.get("atr_signal"):
                    parts.append("⚠️ ATR исчерпан")
                lines.append(f"  {tickers_str}: {' | '.join(parts)}")
            lines.append("")

        if analysis.get("tickers_mentioned"):
            tickers_list = ", ".join(analysis["tickers_mentioned"][:15])
            lines.append(f"👀 *В фокусе:* {tickers_list}")

        return await self.send_message("\n".join(lines))

    async def notify_level_approach(
        self, ticker: str, level_type: str, level_price: float, current_price: float
    ) -> bool:
        """Уведомление: цена приближается к уровню."""
        signal_key = f"level_{ticker}_{level_price}_{datetime.now(MSK).strftime('%Y%m%d')}"
        if signal_key in self._sent_signals:
            return False
        self._sent_signals.add(signal_key)

        distance_pct = abs((current_price - level_price) / level_price) * 100
        emoji = "🟢" if level_type == "support" else "🔵"
        label = "ПОДДЕРЖКА" if level_type == "support" else "СОПРОТИВЛЕНИЕ"

        text = (
            f"{emoji} *{ticker} подходит к {label}*\n\n"
            f"📍 Уровень: {level_price:.2f}\n"
            f"💰 Текущая: {current_price:.2f}\n"
            f"📏 Расстояние: {distance_pct:.2f}%\n\n"
            f"_Следи за реакцией на уровне!_"
        )
        return await self.send_message(text)

    async def notify_error(self, error_msg: str) -> bool:
        text = f"⚠️ *Ошибка системы:*\n\n`{error_msg}`"
        return await self.send_message(text)

    async def notify_startup(self) -> bool:
        text = (
            "✅ *Система мониторинга запущена*\n\n"
            f"📅 {datetime.now(MSK).strftime('%d.%m.%Y %H:%M')} МСК\n"
            f"📺 Канал: @{Config.CHANNEL_USERNAME}\n"
            f"🎯 Порог ATR: {Config.ATR_THRESHOLD_PERCENT}%\n"
            f"📊 Тикеров: {len(Config.MOEX_INDEX_TICKERS)}\n\n"
            "Команды:\n"
            "/status — текущий статус ATR\n"
            "/levels — уровни на сегодня\n"
            "/summary — полная сводка\n"
            "/ticker SBER — детали по тикеру"
        )
        return await self.send_message(text)

    def reset_daily(self):
        self._sent_signals.clear()
