"""
Главный скрипт запуска системы мониторинга ММВБ.

Расписание (МСК):
- 07:15 — парсинг утренних постов, извлечение уровней/ATR
- 10:00-18:40 — мониторинг ATR каждые 60 секунд
- 18:45 — вечерняя сводка
- 24/7 — бот слушает команды: /status, /levels, /summary, /ticker

Запуск: python main.py
"""

import os
import sys
import asyncio
import signal
from datetime import datetime, timedelta, timezone
from typing import Optional

import aiohttp

from config import Config
from channel_parser import ChannelParser
from post_analyzer import PostAnalyzer
from atr_monitor import ATRMonitor
from notifier import TelegramNotifier

MSK = timezone(timedelta(hours=3))


class MOEXMonitorBot:
    """Главный класс — оркестрирует все модули."""

    def __init__(self):
        self.parser = ChannelParser()
        self.analyzer = PostAnalyzer()
        self.atr_monitor = ATRMonitor()
        self.notifier = TelegramNotifier()

        self._running = False
        self._monitoring_task: Optional[asyncio.Task] = None
        self._bot_task: Optional[asyncio.Task] = None

        self.today_analysis: Optional[dict] = None
        self.today_tickers: list = []

    async def start(self):
        """Запуск всей системы."""
        print("=" * 50)
        print("  MOEX Monitor — Система мониторинга ММВБ")
        print("=" * 50)
        print(f"  Время: {datetime.now(MSK).strftime('%d.%m.%Y %H:%M')} МСК")
        print(f"  Канал: @{Config.CHANNEL_USERNAME}")
        print(f"  ATR порог: {Config.ATR_THRESHOLD_PERCENT}%")
        print(f"  Тикеров: {len(Config.MOEX_INDEX_TICKERS)}")
        print("=" * 50)

        self._running = True

        await self.atr_monitor.start()
        await self.notifier.start()

        try:
            await self.parser.connect()
            print("[Main] Парсер канала подключён")
        except Exception as e:
            print(f"[Main] ⚠️ Парсер не подключён (нужны API_ID/API_HASH): {e}")
            print("[Main] Работаем только с мониторингом ATR через MOEX API")

        await self.notifier.notify_startup()

        self._monitoring_task = asyncio.create_task(self._monitoring_loop())
        self._bot_task = asyncio.create_task(self._bot_commands_loop())

        now = datetime.now(MSK)
        if now.hour >= Config.MORNING_CHECK_HOUR and not self.today_analysis:
            await self._morning_routine()

        print("[Main] Все задачи запущены. Ожидание событий...")

        try:
            await asyncio.gather(self._monitoring_task, self._bot_task)
        except asyncio.CancelledError:
            pass

    async def stop(self):
        """Остановка системы."""
        print("\n[Main] Остановка...")
        self._running = False
        if self._monitoring_task:
            self._monitoring_task.cancel()
        if self._bot_task:
            self._bot_task.cancel()
        await self.atr_monitor.stop()
        await self.notifier.stop()
        await self.parser.disconnect()
        print("[Main] Остановлено")

    async def _morning_routine(self):
        """Утренняя рутина: парсинг канала, анализ постов."""
        print(f"\n[Main] === Утренняя рутина ({datetime.now(MSK).strftime('%H:%M')}) ===")

        try:
            posts = await self.parser.get_today_morning_posts()
            if not posts:
                posts = await self.parser.get_recent_posts(hours=4)

            if not posts:
                print("[Main] Утренних постов не найдено")
                await self.notifier.send_message(
                    "⚠️ Утренних постов в канале пока нет."
                )
                return

            self.today_analysis = self.analyzer.analyze_posts(posts)
            self.today_tickers = self.today_analysis.get("tickers_mentioned", [])

            for atr_data in self.today_analysis.get("atr_data", []):
                self.atr_monitor.set_custom_atr(
                    atr_data["ticker"], atr_data["atr_value"]
                )

            await self.notifier.notify_morning_analysis(self.today_analysis)

            print(
                f"[Main] Утренний анализ: "
                f"{len(self.today_analysis['levels'])} уровней, "
                f"{len(self.today_tickers)} тикеров"
            )
        except Exception as e:
            print(f"[Main] Ошибка утренней рутины: {e}")
            await self.notifier.notify_error(f"Утренняя рутина: {e}")

    async def _monitoring_loop(self):
        """Основной цикл мониторинга ATR (10:00-18:40 МСК)."""
        while self._running:
            now = datetime.now(MSK)

            if self._is_trading_time(now):
                try:
                    await self._check_atr()
                    await self._check_levels()
                except Exception as e:
                    print(f"[Main] Ошибка мониторинга: {e}")

            # Утренняя рутина
            if (now.hour == Config.MORNING_CHECK_HOUR
                    and now.minute == Config.MORNING_CHECK_MINUTE
                    and not self.today_analysis):
                await self._morning_routine()

            # Сброс данных (09:55)
            if now.hour == 9 and now.minute == 55:
                self._reset_day()

            # Вечерняя сводка (18:45)
            if now.hour == 18 and now.minute == 45:
                await self._evening_summary()

            await asyncio.sleep(Config.PRICE_CHECK_INTERVAL)

    async def _check_atr(self):
        """Проверка ATR."""
        if self.today_tickers:
            tickers = list(set(self.today_tickers + ["IMOEX"]))
            signals = await self.atr_monitor.check_specific_tickers(tickers)
        else:
            signals = await self.atr_monitor.check_all_tickers()

        for signal in signals:
            print(f"[Main] 🔴 ATR пройден: {signal['ticker']} ({signal['max_move_pct']}%)")
            await self.notifier.notify_atr_exhausted(signal)

    async def _check_levels(self):
        """Проверка приближения к уровням."""
        if not self.today_analysis:
            return
        for level in self.today_analysis.get("levels", []):
            ticker = level["ticker"]
            if ticker == "IMOEX":
                continue
            ticker_data = self.atr_monitor.get_ticker_status(ticker)
            if not ticker_data:
                continue
            current_price = ticker_data.get("last", 0)
            level_price = level["price"]
            if not current_price or not level_price:
                continue
            distance_pct = abs((current_price - level_price) / level_price) * 100
            if distance_pct <= 0.3:
                await self.notifier.notify_level_approach(
                    ticker, level["level_type"], level_price, current_price
                )

    async def _evening_summary(self):
        """Вечерняя сводка."""
        summary = self.atr_monitor.get_atr_summary()
        if summary:
            header = f"🌆 *Итоги дня {datetime.now(MSK).strftime('%d.%m.%Y')}:*\n\n"
            await self.notifier.send_message(header + summary)

    def _reset_day(self):
        """Сброс данных перед новым днём."""
        self.atr_monitor.reset_daily_data()
        self.notifier.reset_daily()
        self.today_analysis = None
        self.today_tickers = []
        print("[Main] Новый день: данные сброшены")

    @staticmethod
    def _is_trading_time(now: datetime) -> bool:
        """Торги идут? (10:00-18:40, пн-пт)"""
        if now.weekday() >= 5:
            return False
        start = now.replace(hour=Config.MOEX_OPEN_HOUR, minute=Config.MOEX_OPEN_MINUTE, second=0)
        end = now.replace(hour=Config.MOEX_CLOSE_HOUR, minute=Config.MOEX_CLOSE_MINUTE, second=0)
        return start <= now <= end

    # === Обработка команд бота ===

    async def _bot_commands_loop(self):
        """Long polling для команд бота."""
        base_url = f"https://api.telegram.org/bot{Config.BOT_TOKEN}"
        offset = 0

        async with aiohttp.ClientSession() as session:
            while self._running:
                try:
                    url = f"{base_url}/getUpdates?offset={offset}&timeout=30"
                    async with session.get(url, timeout=35) as resp:
                        if resp.status != 200:
                            await asyncio.sleep(5)
                            continue
                        data = await resp.json()
                        for update in data.get("result", []):
                            offset = update["update_id"] + 1
                            await self._handle_update(update)
                except asyncio.TimeoutError:
                    continue
                except asyncio.CancelledError:
                    break
                except Exception as e:
                    print(f"[Bot] Ошибка: {e}")
                    await asyncio.sleep(5)

    async def _handle_update(self, update: dict):
        """Обработка команд."""
        message = update.get("message", {})
        text = message.get("text", "")
        chat_id = message.get("chat", {}).get("id")

        if chat_id != Config.USER_ID:
            return

        if text == "/status":
            await self._cmd_status()
        elif text == "/levels":
            await self._cmd_levels()
        elif text == "/summary":
            await self._cmd_summary()
        elif text in ("/help", "/start"):
            await self._cmd_help()
        elif text.startswith("/ticker "):
            ticker = text.split(" ", 1)[1].strip().upper()
            await self._cmd_ticker(ticker)

    async def _cmd_status(self):
        summary = self.atr_monitor.get_atr_summary()
        if summary:
            await self.notifier.send_message(summary)
        else:
            now = datetime.now(MSK)
            msg = "⏳ Данные загружаются..." if self._is_trading_time(now) else "💤 Биржа закрыта."
            await self.notifier.send_message(msg)

    async def _cmd_levels(self):
        levels = self.analyzer.get_today_levels()
        if not levels:
            await self.notifier.send_message("📐 Уровней нет. Появятся после ~07:15 МСК.")
            return
        lines = ["📐 *Уровни на сегодня:*\n"]
        current_ticker = ""
        for level in levels:
            if level["ticker"] != current_ticker:
                current_ticker = level["ticker"]
                lines.append(f"\n*{current_ticker}:*")
            emoji = "🟢" if level["level_type"] == "support" else "🔵"
            label = "подд" if level["level_type"] == "support" else "сопр"
            lines.append(f"  {emoji} {label}: {level['price']}")
        await self.notifier.send_message("\n".join(lines))

    async def _cmd_summary(self):
        lines = [f"📋 *Сводка ({datetime.now(MSK).strftime('%d.%m %H:%M')}):*\n"]
        summary = self.atr_monitor.get_atr_summary()
        if summary:
            lines.append(summary)
        levels = self.analyzer.get_today_levels()
        if levels:
            lines.append("\n📐 *Уровни:*")
            for level in levels[:10]:
                emoji = "🟢" if level["level_type"] == "support" else "🔵"
                lines.append(f"  {emoji} {level['ticker']}: {level['price']}")
        await self.notifier.send_message("\n".join(lines))

    async def _cmd_ticker(self, ticker: str):
        data = self.atr_monitor.get_ticker_status(ticker)
        if not data:
            await self.notifier.send_message(f"❌ Нет данных по {ticker}.")
            return
        status = "🔴 ПРОЙДЕН" if data["atr_hit"] else "🟢 в процессе"
        pct_used = (data['max_move'] / data['threshold']) * 100 if data['threshold'] else 0
        text = (
            f"📊 *{ticker}*\n\n"
            f"💰 Открытие: {data['open']:.2f}\n"
            f"💰 Текущая: {data['last']:.2f}\n"
            f"📈 Ход: {data['current_move']:+.2f}%\n"
            f"📊 Макс ход: {data['max_move']:.2f}%\n"
            f"🎯 Порог ATR: {data['threshold']:.1f}%\n"
            f"ATR использован: {pct_used:.0f}%\n"
            f"Статус: {status}"
        )
        await self.notifier.send_message(text)

    async def _cmd_help(self):
        text = (
            "🤖 *MOEX Monitor — Команды:*\n\n"
            "/status — статус ATR\n"
            "/levels — уровни на сегодня\n"
            "/summary — полная сводка\n"
            "/ticker SBER — детали по тикеру\n"
            "/help — справка\n\n"
            "📡 *Автоматика:*\n"
            "• 07:15 — разбор из канала\n"
            "• 10:00-18:40 — мониторинг ATR\n"
            "• 18:45 — вечерняя сводка"
        )
        await self.notifier.send_message(text)


async def run():
    bot = MOEXMonitorBot()
    loop = asyncio.get_event_loop()

    def shutdown():
        asyncio.create_task(bot.stop())

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, shutdown)
        except NotImplementedError:
            pass

    try:
        await bot.start()
    except KeyboardInterrupt:
        await bot.stop()


if __name__ == "__main__":
    print("Запуск MOEX Monitor...")
    asyncio.run(run())
