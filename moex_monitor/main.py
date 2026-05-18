"""
MOEX Monitor — Система мониторинга ММВБ v3.0

Расписание (МСК):
- 06:45 — ПЛАН: парсинг канала + AI анализ + сигнал за 15 мин до открытия
- 07:05 — ПОДТВЕРЖДЕНИЕ: проверка первого 5-мин бара срочного рынка
- 07:00-14:00 — РЕАЛТАЙМ ТВХ: пробои уровней, мониторинг тейков/стопов
- 14:00-18:40 — МОНИТОРИНГ: только ATR, без новых сделок
- 18:45 — ВЕЧЕРНЯЯ СВОДКА: итоги + паранорм бары на завтра
- 24/7 — БОТ: команды /status, /levels, /trades, /ticker

Запуск: python main.py
"""

import os
import sys
import asyncio
import signal
import json
import urllib.request
from datetime import datetime, timedelta, timezone
from typing import Optional, Dict, List

import aiohttp

from config import Config
from channel_parser import ChannelParser
from post_analyzer import PostAnalyzer
from atr_monitor import ATRMonitor
from notifier import TelegramNotifier
from ai_analyst import AIAnalyst
from realtime_signals import RealtimeSignals

MSK = timezone(timedelta(hours=3))


class MOEXMonitorBot:
    """Главный оркестратор всех модулей."""

    def __init__(self):
        self.parser = ChannelParser()
        self.analyzer = PostAnalyzer()
        self.atr_monitor = ATRMonitor()
        self.notifier = TelegramNotifier()
        self.ai_analyst = AIAnalyst()
        self.rt_signals = RealtimeSignals()

        self._running = False
        self._monitoring_task: Optional[asyncio.Task] = None
        self._bot_task: Optional[asyncio.Task] = None

        self.today_analysis: Optional[dict] = None
        self.today_tickers: list = []

        # Флаги чтобы не повторять
        self._plan_sent = False
        self._confirmation_sent = False
        self._day_reset_done = False

    async def start(self):
        """Запуск системы."""
        print("=" * 55)
        print("  MOEX Monitor v3.0 — Двухэтапные сигналы + Реалтайм")
        print("=" * 55)
        print(f"  Время: {datetime.now(MSK).strftime('%d.%m.%Y %H:%M')} МСК")
        print(f"  Канал: @{Config.CHANNEL_USERNAME}")
        print(f"  ATR порог: {Config.ATR_THRESHOLD_PERCENT}%")
        print(f"  Проверка цен: каждые {Config.PRICE_CHECK_INTERVAL} сек")
        print(f"  План: {Config.PRE_OPEN_HOUR}:{Config.PRE_OPEN_MINUTE:02d} МСК")
        print(f"  Подтверждение: +{Config.CONFIRM_DELAY_MINUTES} мин после открытия")
        print(f"  Новые сделки до: {Config.NO_NEW_TRADES_HOUR}:00 МСК")
        print("=" * 55)

        self._running = True

        await self.atr_monitor.start()
        await self.notifier.start()

        try:
            await self.parser.connect()
            print("[Main] Парсер канала подключён")
        except Exception as e:
            print(f"[Main] ⚠️ Парсер не подключён: {e}")

        await self.notifier.notify_startup()

        self._monitoring_task = asyncio.create_task(self._main_loop())
        self._bot_task = asyncio.create_task(self._bot_commands_loop())

        # Если запустились после 06:45 но до 14:00 — догоним
        now = datetime.now(MSK)
        if now.hour >= Config.PRE_OPEN_HOUR and not self._plan_sent:
            await self._stage1_morning_plan()

        print("[Main] Все задачи запущены.")

        try:
            await asyncio.gather(self._monitoring_task, self._bot_task)
        except asyncio.CancelledError:
            pass

    async def stop(self):
        """Остановка."""
        self._running = False
        if self._monitoring_task:
            self._monitoring_task.cancel()
        if self._bot_task:
            self._bot_task.cancel()
        await self.atr_monitor.stop()
        await self.notifier.stop()
        await self.parser.disconnect()
        print("[Main] Остановлено")

    # ═══════════════════════════════════════════════════
    # ГЛАВНЫЙ ЦИКЛ
    # ═══════════════════════════════════════════════════

    async def _main_loop(self):
        """Единый цикл: план → подтверждение → реалтайм → вечер."""
        while self._running:
            now = datetime.now(MSK)
            try:
                # Сброс дня (05:00)
                if now.hour == 5 and not self._day_reset_done:
                    self._reset_day()

                # ЭТАП 1: План (06:45)
                if (now.hour == Config.PRE_OPEN_HOUR
                        and now.minute >= Config.PRE_OPEN_MINUTE
                        and not self._plan_sent):
                    await self._stage1_morning_plan()

                # ЭТАП 2: Подтверждение первым баром (07:05)
                confirm_hour = Config.FORTS_OPEN_HOUR
                confirm_minute = Config.FORTS_OPEN_MINUTE + Config.CONFIRM_DELAY_MINUTES
                if (now.hour == confirm_hour
                        and now.minute >= confirm_minute
                        and self._plan_sent
                        and not self._confirmation_sent):
                    await self._stage2_confirm_first_bar()

                # ЭТАП 3: Реалтайм мониторинг (07:00-18:40)
                if self._is_trading_time(now):
                    await self._stage3_realtime_monitoring()

                # Вечерняя сводка (18:45)
                if (now.hour == Config.EVENING_SUMMARY_HOUR
                        and now.minute == Config.EVENING_SUMMARY_MINUTE):
                    await self._evening_summary()

            except Exception as e:
                print(f"[Main] Ошибка в цикле: {e}")

            await asyncio.sleep(Config.PRICE_CHECK_INTERVAL)

    # ═══════════════════════════════════════════════════
    # ЭТАП 1: УТРЕННИЙ ПЛАН (06:45 МСК)
    # ═══════════════════════════════════════════════════

    async def _stage1_morning_plan(self):
        """За 15 мин до открытия: парсинг + AI → план."""
        print(f"\n[ЭТАП 1] Утренний план ({datetime.now(MSK).strftime('%H:%M')})")
        self._plan_sent = True

        try:
            # Парсим канал
            posts = await self.parser.get_today_morning_posts()
            if not posts:
                posts = await self.parser.get_recent_posts(hours=6)

            if posts:
                self.today_analysis = self.analyzer.analyze_posts(posts)
                self.today_tickers = self.today_analysis.get("tickers_mentioned", [])
                await self.notifier.notify_morning_analysis(self.today_analysis)
            else:
                await self.notifier.send_message("⚠️ Утренних постов не найдено")

            # AI анализ
            await self._run_ai_plan(posts or [])

            # Настраиваем реалтайм сигналы
            if self.today_analysis:
                levels = self.today_analysis.get("levels", [])
                direction = self.ai_analyst.daily_direction or "UNKNOWN"
                self.rt_signals.configure(levels, direction)

        except Exception as e:
            print(f"[ЭТАП 1] Ошибка: {e}")
            await self.notifier.send_message(f"⚠️ Ошибка утреннего плана: {e}")

    async def _run_ai_plan(self, posts: list):
        """AI анализ → план сделок."""
        try:
            channel_text = "\n".join([p.get("text", "") for p in posts if p.get("text")])
            channel_data = {"raw_text": channel_text[:3000], "levels_text": channel_text[:2000]}

            market_data = await self._get_market_background()
            previous_day_data = await self._get_previous_day_data()

            paranorm_bars = []
            for ticker, data in previous_day_data.items():
                if data.get("open") and data["open"] > 0:
                    range_pct = (data["high"] - data["low"]) / data["open"] * 100
                    if range_pct > 2.0:
                        paranorm_bars.append(f"{ticker} ({range_pct:.1f}%)")

            result = self.ai_analyst.morning_plan(
                channel_data, market_data, previous_day_data, paranorm_bars
            )

            if result.get("trades"):
                msg = self.ai_analyst.format_plan_message()
                await self.notifier.send_message(msg)
            else:
                await self.notifier.send_message(
                    "🤖 AI: Нет чётких сделок. Факторы неоднозначны. ЗАБОР."
                )

            print(f"[AI] План: {result.get('direction')}, {len(result.get('trades', []))} сделок")

        except Exception as e:
            print(f"[AI] Ошибка: {e}")
            await self.notifier.send_message(f"⚠️ AI не смог дать план: {e}")

    # ═══════════════════════════════════════════════════
    # ЭТАП 2: ПОДТВЕРЖДЕНИЕ ПЕРВЫМ БАРОМ (07:05 МСК)
    # ═══════════════════════════════════════════════════

    async def _stage2_confirm_first_bar(self):
        """Через 5 мин после открытия: проверяем первый бар."""
        print(f"\n[ЭТАП 2] Подтверждение первым баром ({datetime.now(MSK).strftime('%H:%M')})")
        self._confirmation_sent = True

        try:
            # Получаем текущие цены (это и есть "закрытие первого бара")
            first_bar_data = await self._get_current_prices_as_bar()

            if first_bar_data:
                confirmed = self.ai_analyst.confirm_by_first_bar(first_bar_data)

                if confirmed:
                    msg = self.ai_analyst.format_confirmation_message(confirmed)
                    await self.notifier.send_message(msg)
                    print(f"[ЭТАП 2] Подтверждено: {len(confirmed)} сделок")
                else:
                    await self.notifier.send_message(
                        "⏳ Первый бар не подтвердил. Жду второй бар..."
                    )
                    # Запланируем повторную проверку через 5 мин
                    asyncio.create_task(self._retry_confirmation())
            else:
                await self.notifier.send_message("⚠️ Нет данных для подтверждения")

        except Exception as e:
            print(f"[ЭТАП 2] Ошибка: {e}")

    async def _retry_confirmation(self):
        """Повторная проверка подтверждения через 5 мин."""
        await asyncio.sleep(300)  # 5 минут
        try:
            bar_data = await self._get_current_prices_as_bar()
            if bar_data:
                confirmed = self.ai_analyst.confirm_by_first_bar(bar_data)
                if confirmed:
                    msg = self.ai_analyst.format_confirmation_message(confirmed)
                    await self.notifier.send_message(msg)
        except Exception as e:
            print(f"[Retry] Ошибка: {e}")

    async def _get_current_prices_as_bar(self) -> Dict:
        """Получает текущие цены как 'первый бар'."""
        result = {}
        try:
            url = ("https://iss.moex.com/iss/engines/stock/markets/shares/"
                   "securities.json?iss.only=marketdata")
            resp = urllib.request.urlopen(url, timeout=10)
            data = json.loads(resp.read())
            cols = data["marketdata"]["columns"]
            for row in data["marketdata"]["data"]:
                d = dict(zip(cols, row))
                ticker = d.get("SECID", "")
                if ticker in Config.MOEX_INDEX_TICKERS and d.get("OPEN") and d.get("LAST"):
                    result[ticker] = {
                        "open": d["OPEN"],
                        "close": d["LAST"],
                        "high": d.get("HIGH", d["LAST"]),
                        "low": d.get("LOW", d["LAST"]),
                    }
        except Exception as e:
            print(f"[Bar] Ошибка: {e}")
        return result

    # ═══════════════════════════════════════════════════
    # ЭТАП 3: РЕАЛТАЙМ МОНИТОРИНГ (07:00-18:40)
    # ═══════════════════════════════════════════════════

    async def _stage3_realtime_monitoring(self):
        """Каждые 30 сек: реалтайм ТВХ + тейки/стопы + ATR."""
        now = datetime.now(MSK)

        # Получаем текущие цены
        current_prices = await self._get_current_prices()
        if not current_prices:
            return

        # 1. Реалтайм ТВХ (пробои уровней) — только до 14:00
        if now.hour < Config.NO_NEW_TRADES_HOUR:
            atr_status = self._get_atr_status_dict(current_prices)
            rt_signals = self.rt_signals.check(current_prices, atr_status, now)
            for sig in rt_signals:
                msg = self.ai_analyst.format_realtime_signal(sig)
                await self.notifier.send_message(msg)
                print(f"[RT] ⚡ {sig['direction']} {sig['ticker']} от {sig['entry']}")

        # 2. Проверка тейков/стопов активных сделок
        trade_signals = self.ai_analyst.check_active_trades(current_prices)
        for sig in trade_signals:
            emoji = "✅" if sig["type"] == "TAKE_PROFIT" else "❌"
            msg = (f"{emoji} <b>{sig['type']}</b>: {sig['ticker']}\n"
                   f"   P&L: {sig['pnl']:+.2f}% | Цена: {sig['price']}")
            await self.notifier.send_message(msg)
            print(f"[Trade] {sig['type']} {sig['ticker']} {sig['pnl']:+.2f}%")

        # 2.5. Проверка перезахода после ложного выноса
        if now.hour < 14:
            reentry_signals = self.ai_analyst.check_reentry(current_prices, atr_status, now)
            for sig in reentry_signals:
                msg = self.ai_analyst.format_reentry_signal(sig)
                await self.notifier.send_message(msg)
                print(f"[Reentry] 🔄 {sig['direction']} {sig['ticker']} от {sig['entry']}")

        # 3. ATR мониторинг
        await self._check_atr()

    def _get_atr_status_dict(self, current_prices: Dict) -> Dict:
        """Формирует ATR статус для реалтайм модуля."""
        result = {}
        for ticker, price in current_prices.items():
            status = self.atr_monitor.get_ticker_status(ticker)
            if status:
                threshold = status.get("threshold", 1.0)
                max_move = status.get("max_move", 0)
                atr_used_pct = (max_move / threshold * 100) if threshold else 0
                result[ticker] = {"atr_used_pct": atr_used_pct, "open": status.get("open", 0)}
        return result

    async def _get_current_prices(self) -> Dict[str, float]:
        """Получить текущие цены всех тикеров."""
        result = {}
        try:
            url = ("https://iss.moex.com/iss/engines/stock/markets/shares/"
                   "securities.json?iss.only=marketdata")
            resp = urllib.request.urlopen(url, timeout=10)
            data = json.loads(resp.read())
            cols = data["marketdata"]["columns"]
            for row in data["marketdata"]["data"]:
                d = dict(zip(cols, row))
                ticker = d.get("SECID", "")
                if ticker in Config.MOEX_INDEX_TICKERS and d.get("LAST"):
                    result[ticker] = d["LAST"]
        except Exception as e:
            print(f"[Prices] Ошибка: {e}")
        return result

    # ═══════════════════════════════════════════════════
    # ВСПОМОГАТЕЛЬНЫЕ МЕТОДЫ
    # ═══════════════════════════════════════════════════

    async def _check_atr(self):
        """Проверка ATR."""
        if self.today_tickers:
            tickers = list(set(self.today_tickers + ["IMOEX"]))
            signals = await self.atr_monitor.check_specific_tickers(tickers)
        else:
            signals = await self.atr_monitor.check_all_tickers()

        for sig in signals:
            await self.notifier.notify_atr_exhausted(sig)

    async def _evening_summary(self):
        """Вечерняя сводка."""
        summary = self.atr_monitor.get_atr_summary()
        if summary:
            header = f"🌆 <b>Итоги {datetime.now(MSK).strftime('%d.%m.%Y')}:</b>\n\n"
            await self.notifier.send_message(header + summary)

    def _reset_day(self):
        """Сброс на новый день."""
        self.atr_monitor.reset_daily_data()
        self.notifier.reset_daily()
        self.today_analysis = None
        self.today_tickers = []
        self.ai_analyst.today_trades = []
        self.ai_analyst.daily_direction = None
        self.ai_analyst._confirmed_trades = []
        self.rt_signals.reset()
        self._plan_sent = False
        self._confirmation_sent = False
        self._day_reset_done = True
        print("[Main] Новый день: все данные сброшены")

    @staticmethod
    def _is_trading_time(now: datetime) -> bool:
        """Торги идут?"""
        if now.weekday() >= 5:
            return False
        start = now.replace(hour=Config.FORTS_OPEN_HOUR, minute=0, second=0)
        end = now.replace(hour=Config.MOEX_CLOSE_HOUR, minute=Config.MOEX_CLOSE_MINUTE, second=0)
        return start <= now <= end

    async def _get_market_background(self) -> dict:
        """Фон рынка."""
        result = {}
        try:
            url = "https://iss.moex.com/iss/engines/futures/markets/forts/securities.json?iss.only=marketdata"
            resp = urllib.request.urlopen(url, timeout=10)
            data = json.loads(resp.read())
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
            print(f"[Market] Ошибка: {e}")
        result.setdefault("oil_change", "?")
        result.setdefault("usd_change", "?")
        result.setdefault("gold_change", "?")
        result.setdefault("mx_price", "?")
        result.setdefault("global_indices", "нет данных")
        return result

    async def _get_previous_day_data(self) -> dict:
        """Данные предыдущего торгового дня."""
        result = {}
        today = datetime.now(MSK).date()
        prev_day = today - timedelta(days=1)
        while prev_day.weekday() >= 5:
            prev_day -= timedelta(days=1)
        date_str = prev_day.strftime("%Y-%m-%d")

        for ticker in Config.MOEX_INDEX_TICKERS[:25]:
            try:
                url = (f"https://iss.moex.com/iss/engines/stock/markets/shares/"
                       f"securities/{ticker}/candles.json?from={date_str}&till={date_str}&interval=24")
                resp = urllib.request.urlopen(url, timeout=5)
                data = json.loads(resp.read())
                candles = data["candles"]["data"]
                if candles:
                    c = candles[-1]
                    result[ticker] = {"open": c[0], "close": c[1], "high": c[2], "low": c[3]}
            except Exception:
                pass
        return result

    # ═══════════════════════════════════════════════════
    # КОМАНДЫ БОТА
    # ═══════════════════════════════════════════════════

    async def _bot_commands_loop(self):
        """Long polling."""
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
            summary = self.atr_monitor.get_atr_summary()
            await self.notifier.send_message(summary or "💤 Нет данных")
        elif text == "/trades":
            if self.ai_analyst.today_trades:
                await self.notifier.send_message(self.ai_analyst.format_plan_message())
            else:
                await self.notifier.send_message("🤖 Сделок нет. Ждём 06:45 МСК.")
        elif text == "/levels":
            levels = self.analyzer.get_today_levels()
            if levels:
                lines = ["📐 <b>Уровни:</b>\n"]
                for lv in levels[:15]:
                    e = "🟢" if lv["level_type"] == "support" else "🔵"
                    lines.append(f"  {e} {lv['ticker']}: {lv['price']}")
                await self.notifier.send_message("\n".join(lines))
            else:
                await self.notifier.send_message("📐 Уровней нет")
        elif text.startswith("/ticker "):
            ticker = text.split(" ", 1)[1].strip().upper()
            data = self.atr_monitor.get_ticker_status(ticker)
            if data:
                pct = (data['max_move'] / data['threshold'] * 100) if data['threshold'] else 0
                status = "🔴 ПРОЙДЕН" if data["atr_hit"] else "🟢 в процессе"
                msg = (f"📊 <b>{ticker}</b>\n"
                       f"Открытие: {data['open']:.2f} | Текущая: {data['last']:.2f}\n"
                       f"ATR: {pct:.0f}% использовано | {status}")
                await self.notifier.send_message(msg)
            else:
                await self.notifier.send_message(f"❌ Нет данных по {ticker}")
        elif text in ("/help", "/start"):
            await self.notifier.send_message(
                "🤖 <b>MOEX Monitor v3.0</b>\n\n"
                "/trades — AI-сделки\n"
                "/status — ATR статус\n"
                "/levels — уровни\n"
                "/ticker SBER — детали\n\n"
                "📡 <b>Автоматика:</b>\n"
                "• 06:45 — ПЛАН (за 15 мин до открытия)\n"
                "• 07:05 — ПОДТВЕРЖДЕНИЕ (первый бар)\n"
                "• 07:00-14:00 — РЕАЛТАЙМ ТВХ\n"
                "• 14:00+ — только мониторинг\n"
                "• 18:45 — итоги дня"
            )


async def run():
    bot = MOEXMonitorBot()
    loop = asyncio.get_event_loop()

    def shutdown():
        asyncio.create_task(bot.stop())

    for sig_name in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig_name, shutdown)
        except NotImplementedError:
            pass

    try:
        await bot.start()
    except KeyboardInterrupt:
        await bot.stop()


if __name__ == "__main__":
    print("Запуск MOEX Monitor v3.0...")
    asyncio.run(run())
