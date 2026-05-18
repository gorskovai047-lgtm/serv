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
import json
from datetime import datetime, timedelta, timezone
from typing import Optional

import aiohttp

from config import Config
from channel_parser import ChannelParser
from post_analyzer import PostAnalyzer
from atr_monitor import ATRMonitor
from notifier import TelegramNotifier
from ai_analyst import AIAnalyst

MSK = timezone(timedelta(hours=3))


class MOEXMonitorBot:
    """Главный класс — оркестрирует все модули."""

    def __init__(self):
        self.parser = ChannelParser()
        self.analyzer = PostAnalyzer()
        self.atr_monitor = ATRMonitor()
        self.notifier = TelegramNotifier()
        self.ai_analyst = AIAnalyst()

        self._running = False
        self._monitoring_task: Optional[asyncio.Task] = None
        self._bot_task: Optional[asyncio.Task] = None

        self.today_analysis: Optional[dict] = None
        self.today_tickers: list = []
        self.today_ai_trades: list = []

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
        """Утренняя рутина: парсинг канала, анализ постов, AI рекомендации."""
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

            # === AI АНАЛИТИК ===
            await self._run_ai_analysis(posts)

            print(
                f"[Main] Утренний анализ: "
                f"{len(self.today_analysis['levels'])} уровней, "
                f"{len(self.today_tickers)} тикеров, "
                f"{len(self.today_ai_trades)} AI-сделок"
            )
        except Exception as e:
            print(f"[Main] Ошибка утренней рутины: {e}")
            await self.notifier.notify_error(f"Утренняя рутина: {e}")

    async def _run_ai_analysis(self, posts: list):
        """Запуск AI-аналитика с данными из канала и рынка."""
        try:
            # Собираем текст из канала
            channel_text = "\n".join([p.get("text", "") for p in posts if p.get("text")])
            channel_data = {
                "raw_text": channel_text[:3000],
                "levels_text": channel_text[:2000],
            }

            # Получаем рыночные данные
            market_data = await self._get_market_background()

            # Получаем данные предыдущего дня
            previous_day_data = await self._get_previous_day_data()

            # Определяем паранорм бары
            paranorm_bars = []
            for ticker, data in previous_day_data.items():
                if data.get("open") and data["open"] > 0:
                    range_pct = (data["high"] - data["low"]) / data["open"] * 100
                    if range_pct > 2.0:
                        paranorm_bars.append(f"{ticker} ({range_pct:.1f}%)")

            # Вызываем AI
            result = self.ai_analyst.morning_analysis(
                channel_data, market_data, previous_day_data, paranorm_bars
            )

            self.today_ai_trades = result.get("trades", [])

            # Отправляем результат
            if self.today_ai_trades:
                msg = self.ai_analyst.format_trades_message()
                await self.notifier.send_message(msg)
                if result.get("filtered_out", 0) > 0:
                    await self.notifier.send_message(
                        f"⚠️ Отфильтровано {result['filtered_out']} сделок "
                        f"(вчерашние лидеры падения/роста)"
                    )
            else:
                await self.notifier.send_message(
                    "🤖 AI: Нет чётких сделок. Совокупность факторов неоднозначна."
                )

            print(f"[AI] Направление: {result.get('direction')}, "
                  f"Сделок: {len(self.today_ai_trades)}")

        except Exception as e:
            print(f"[AI] Ошибка AI-анализа: {e}")
            await self.notifier.send_message(f"⚠️ AI-анализ не удался: {e}")

    async def _get_market_background(self) -> dict:
        """Получить фон рынка (нефть, фьючерсы)."""
        import urllib.request
        import json as json_lib

        result = {}
        try:
            url = "https://iss.moex.com/iss/engines/futures/markets/forts/securities.json?iss.only=marketdata"
            resp = urllib.request.urlopen(url, timeout=10)
            data = json_lib.loads(resp.read())
            cols = data["marketdata"]["columns"]
            for row in data["marketdata"]["data"]:
                d = dict(zip(cols, row))
                secid = d.get("SECID", "")
                if secid.startswith("BR-") and d.get("LASTCHANGEPRCNT"):
                    result["oil_change"] = f"{d['LASTCHANGEPRCNT']:+.1f}%"
                elif secid.startswith("Si-") and d.get("LASTCHANGEPRCNT"):
                    result["usd_change"] = f"{d['LASTCHANGEPRCNT']:+.1f}%"
                elif secid.startswith("GD-") and d.get("LASTCHANGEPRCNT"):
                    result["gold_change"] = f"{d['LASTCHANGEPRCNT']:+.1f}%"
                elif secid.startswith("MX-") and d.get("LAST"):
                    result["mx_price"] = d["LAST"]
        except Exception as e:
            print(f"[Market] Ошибка получения фона: {e}")

        result.setdefault("oil_change", "?")
        result.setdefault("usd_change", "?")
        result.setdefault("gold_change", "?")
        result.setdefault("mx_price", "?")
        result.setdefault("global_indices", "нет данных")
        return result

    async def _get_previous_day_data(self) -> dict:
        """Получить данные предыдущего торгового дня."""
        import urllib.request
        import json as json_lib

        result = {}
        # Ищем последний торговый день (не выходной)
        today = datetime.now(MSK).date()
        prev_day = today - timedelta(days=1)
        while prev_day.weekday() >= 5:  # Пропускаем выходные
            prev_day -= timedelta(days=1)

        date_str = prev_day.strftime("%Y-%m-%d")

        for ticker in Config.MOEX_INDEX_TICKERS[:25]:  # Топ-25
            try:
                url = (
                    f"https://iss.moex.com/iss/engines/stock/markets/shares/"
                    f"securities/{ticker}/candles.json?"
                    f"from={date_str}&till={date_str}&interval=24"
                )
                resp = urllib.request.urlopen(url, timeout=5)
                data = json_lib.loads(resp.read())
                candles = data["candles"]["data"]
                if candles:
                    c = candles[-1]
                    result[ticker] = {
                        "open": c[0],
                        "close": c[1],
                        "high": c[2],
                        "low": c[3],
                    }
            except Exception:
                pass

        return result

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
        self.today_ai_trades = []
        self.ai_analyst.today_trades = []
        self.ai_analyst.daily_direction = None
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
        elif text == "/trades":
            await self._cmd_trades()
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
            "/trades — AI-сделки на сегодня\n"
            "/summary — полная сводка\n"
            "/ticker SBER — детали по тикеру\n"
            "/help — справка\n\n"
            "📡 *Автоматика:*\n"
            "• 06:00 — AI-анализ + парсинг канала\n"
            "• 07:00-14:00 — мониторинг + сигналы\n"
            "• 14:00+ — только мониторинг\n"
            "• 18:45 — вечерняя сводка\n\n"
            "🧠 *Правила AI:*\n"
            "• Тейк +0.9-1.5%, стоп -0.3%\n"
            "• Не шортит вчерашних лидеров падения\n"
            "• Не лонгует вчерашних лидеров роста\n"
            "• 'Лонг' в плане + общий шорт = контртренд"
        )
        await self.notifier.send_message(text)

    async def _cmd_trades(self):
        """Показать текущие AI-сделки."""
        if self.today_ai_trades:
            msg = self.ai_analyst.format_trades_message()
            await self.notifier.send_message(msg)
        else:
            await self.notifier.send_message(
                "🤖 AI-сделок нет. Появятся после утреннего анализа (~06:00 МСК)."
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
