"""
AI-аналитик на базе DeepSeek V4 Pro через Fireworks.ai.
Даёт конкретные торговые рекомендации по модели Лактионова.

v2.0: Двухэтапные сигналы + Scoring + Реалтайм ТВХ

КЛЮЧЕВЫЕ ПРАВИЛА:
1. Тейк +0.9-1.5%, стоп ВСЕГДА -0.3%. Либо тейк, либо стоп.
2. Не более 8 сделок за день.
3. Вход с открытия на первых 5-мин барах (с подтверждением!).
4. Выход при ATR 1% или по 1-й цели. НЕ ЖОПИТЬ.
5. Не торгуем после 14:00 МСК.
6. Мин объём 300 млн руб.
7. НЕ шортить вчерашних лидеров падения.
8. НЕ лонговать вчерашних лидеров роста.
9. Контртренд: "лонг" в плане + общий шорт = шорт при пробое.
10. Качество > количество. Scoring каждой сделки.
"""

import re
import json
import os
import urllib.request
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple
from enum import Enum

MSK = timezone(timedelta(hours=3))

FIREWORKS_API_KEY = os.getenv("FIREWORKS_API_KEY", "")
FIREWORKS_MODEL = os.getenv(
    "FIREWORKS_MODEL", "accounts/fireworks/models/deepseek-v4-pro"
)
FIREWORKS_URL = "https://api.fireworks.ai/inference/v1/chat/completions"


class TradeStatus(Enum):
    """Статус сделки."""
    PLANNED = "planned"          # План (до открытия)
    WAITING_CONFIRM = "waiting"  # Ждёт подтверждения первым баром
    CONFIRMED = "confirmed"      # Подтверждена — можно входить
    ACTIVE = "active"            # В работе
    TAKE_PROFIT = "take"         # Закрыта по тейку
    STOP_LOSS = "stop"           # Закрыта по стопу
    CANCELLED = "cancelled"      # Отменена (первый бар против)
    EXPIRED = "expired"          # Истекла (после 14:00)


class AIAnalyst:
    """AI-аналитик для интрадей торговли по модели Лактионова."""

    def __init__(self):
        self.api_key = FIREWORKS_API_KEY
        self.model = FIREWORKS_MODEL
        self.today_trades: List[Dict] = []
        self.daily_direction: Optional[str] = None
        self.daily_logic: str = ""
        self.max_trades_per_day = 8
        self._confirmed_trades: List[Dict] = []

    def _call_llm(self, system_prompt: str, user_prompt: str, max_tokens: int = 2500) -> str:
        """Вызов DeepSeek V4 Pro через Fireworks.ai."""
        if not self.api_key:
            return "[ОШИБКА] Нет FIREWORKS_API_KEY"

        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "max_tokens": max_tokens,
            "temperature": 0.2,
        }

        try:
            req = urllib.request.Request(
                FIREWORKS_URL,
                data=json.dumps(payload).encode("utf-8"),
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
            )
            resp = urllib.request.urlopen(req, timeout=90)
            result = json.loads(resp.read())
            content = result["choices"][0]["message"]["content"]
            return content
        except Exception as e:
            return f"[ОШИБКА LLM] {e}"



    # ═══════════════════════════════════════════════════
    # SCORING СИСТЕМА
    # ═══════════════════════════════════════════════════

    def _score_trade(
        self,
        trade: Dict,
        channel_data: Dict,
        market_data: Dict,
        prev_moves: Dict,
        paranorm_bars: List[str],
    ) -> int:
        """
        Оценка качества сделки (1-5 звёзд).
        
        Факторы (+1 каждый):
        1. Лактионов ЯВНО пометил тикер как шорт/лонг
        2. Тикер хуже/лучше рынка (совпадает с направлением)
        3. Паранорм бар вчера (откат ожидается)
        4. Фон совпадает с направлением
        5. Контртренд-ловушка (лонг в шортовый день = пробой)
        """
        score = 0
        ticker = trade["ticker"]
        direction = trade["direction"]
        channel_text = channel_data.get("raw_text", "").lower()

        # 1. Лактионов явно пометил тикер
        ticker_aliases = {
            "SBER": ["сбер"], "VTBR": ["втб"], "GAZP": ["газпром", "гп"],
            "LKOH": ["лукойл", "лук"], "ROSN": ["роснефть"],
            "GMKN": ["гмк", "норникель"], "NVTK": ["новатэк"],
            "OZON": ["озон"], "TCSG": ["тиньков", "тинькофф"],
            "AFLT": ["аэрофлот"], "MAGN": ["ммк"], "NLMK": ["нлмк"],
            "MGNT": ["магнит"], "ALRS": ["алроса"], "TATN": ["татнефть"],
            "UGLD": ["югк", "южуралзолото"], "MTLR": ["мечел"],
            "SMLT": ["самолет", "самолёт"], "VKCO": ["вк"],
            "RNFT": ["русснефть"], "CHMF": ["северсталь"],
            "YNDX": ["яндекс"], "PLZL": ["полюс"],
        }
        aliases = ticker_aliases.get(ticker, [ticker.lower()])
        dir_word = "шорт" if direction == "SHORT" else "лонг"
        for alias in aliases:
            if alias in channel_text and dir_word in channel_text:
                score += 1
                break

        # 2. Хуже/лучше рынка
        if ticker in prev_moves:
            imoex_move = prev_moves.get("IMOEX", 0)
            ticker_move = prev_moves[ticker]
            relative = ticker_move - imoex_move
            if direction == "SHORT" and relative < -0.5:
                score += 1  # Хуже рынка = хорош для шорта
            elif direction == "LONG" and relative > 0.5:
                score += 1  # Лучше рынка = хорош для лонга

        # 3. Паранорм бар вчера по этому тикеру
        for pb in paranorm_bars:
            if ticker in pb:
                score += 1
                break

        # 4. Фон совпадает с направлением
        oil = market_data.get("oil_change", "")
        if direction == "SHORT" and ("-" in oil or "минус" in str(market_data.get("global_indices", ""))):
            score += 1
        elif direction == "LONG" and ("+" in oil):
            score += 1

        # 5. Контртренд-ловушка
        if direction == "SHORT":
            for alias in aliases:
                if alias in channel_text and "лонг" in channel_text:
                    # Тикер помечен как "лонг" но мы шортим = контртренд
                    score += 1
                    break

        return min(score + 1, 5)  # Минимум 1 звезда, макс 5



    # ═══════════════════════════════════════════════════
    # ЭТАП 1: УТРЕННИЙ ПЛАН (06:45 МСК — за 15 мин до открытия)
    # ═══════════════════════════════════════════════════

    def morning_plan(
        self,
        channel_data: Dict,
        market_data: Dict,
        previous_day_data: Dict,
        paranorm_bars: List[str],
    ) -> Dict:
        """
        Этап 1: Утренний план (06:45 МСК).
        Определяет направление дня и ПЛАН сделок (ещё не подтверждённых).
        Сигнал приходит за 15 минут до открытия срочного рынка.
        """
        prev_moves = {}
        for ticker, data in previous_day_data.items():
            if data.get("open") and data["open"] > 0:
                move = (data["close"] - data["open"]) / data["open"] * 100
                prev_moves[ticker] = move

        yesterday_fallers = [t for t, m in prev_moves.items() if m < -1.5]
        yesterday_risers = [t for t, m in prev_moves.items() if m > 1.5]

        system_prompt = self._get_system_prompt()
        user_prompt = self._format_morning_prompt(
            channel_data, market_data, prev_moves,
            yesterday_fallers, yesterday_risers, paranorm_bars
        )

        response = self._call_llm(system_prompt, user_prompt, max_tokens=2500)
        trades = self._parse_trades_from_response(response)
        filtered_trades = self._filter_trades(
            trades, yesterday_fallers, yesterday_risers, previous_day_data
        )

        # Scoring каждой сделки
        for trade in filtered_trades:
            trade["score"] = self._score_trade(
                trade, channel_data, market_data, prev_moves, paranorm_bars
            )
            trade["status"] = TradeStatus.PLANNED.value

        # Сортируем по score (лучшие сверху)
        filtered_trades.sort(key=lambda x: x["score"], reverse=True)

        # Берём только лучшие (score >= 2)
        quality_trades = [t for t in filtered_trades if t["score"] >= 2]
        if not quality_trades:
            quality_trades = filtered_trades[:3]  # Хотя бы 3 если все слабые

        self.today_trades = quality_trades[:self.max_trades_per_day]
        self.daily_direction = self._determine_direction(response)
        self.daily_logic = self._extract_logic(response)

        return {
            "direction": self.daily_direction,
            "logic": self.daily_logic,
            "trades": self.today_trades,
            "analysis": response,
            "filtered_out": len(trades) - len(quality_trades),
            "stage": "PLAN",
        }

    # ═══════════════════════════════════════════════════
    # ЭТАП 2: ПОДТВЕРЖДЕНИЕ ПЕРВЫМ БАРОМ (07:05 / 10:05)
    # ═══════════════════════════════════════════════════

    def confirm_by_first_bar(self, first_bar_data: Dict) -> List[Dict]:
        """
        Этап 2: Подтверждение первым 5-мин баром.
        
        Args:
            first_bar_data: {ticker: {"open": x, "close": y, "high": z, "low": w}}
        
        Returns:
            Список подтверждённых/отменённых сделок
        """
        confirmed = []
        cancelled = []

        for trade in self.today_trades:
            if trade["status"] != TradeStatus.PLANNED.value:
                continue

            ticker = trade["ticker"]
            direction = trade["direction"]

            if ticker not in first_bar_data:
                # Нет данных — оставляем в плане
                trade["status"] = TradeStatus.WAITING_CONFIRM.value
                continue

            bar = first_bar_data[ticker]
            bar_open = bar["open"]
            bar_close = bar["close"]
            bar_direction = "DOWN" if bar_close < bar_open else "UP"

            # Подтверждение: первый бар в нашу сторону
            if direction == "SHORT" and bar_direction == "DOWN":
                trade["status"] = TradeStatus.CONFIRMED.value
                trade["entry"] = bar_close  # Входим по закрытию 1-го бара
                trade["take"] = round(bar_close * 0.989, 2)
                trade["stop"] = round(bar_close * 1.003, 2)
                confirmed.append(trade)

            elif direction == "LONG" and bar_direction == "UP":
                trade["status"] = TradeStatus.CONFIRMED.value
                trade["entry"] = bar_close
                trade["take"] = round(bar_close * 1.011, 2)
                trade["stop"] = round(bar_close * 0.997, 2)
                confirmed.append(trade)

            else:
                # Первый бар ПРОТИВ нашего направления
                # Не отменяем сразу — ждём ещё 1 бар (может быть ложный)
                trade["status"] = TradeStatus.WAITING_CONFIRM.value
                trade["_against_count"] = trade.get("_against_count", 0) + 1

                if trade["_against_count"] >= 2:
                    # 2 бара против — отменяем
                    trade["status"] = TradeStatus.CANCELLED.value
                    cancelled.append(trade)

        self._confirmed_trades = confirmed
        return confirmed



    # ═══════════════════════════════════════════════════
    # РЕАЛТАЙМ ТВХ (во время сессии)
    # ═══════════════════════════════════════════════════

    def check_realtime_entry(
        self,
        current_prices: Dict,
        levels: List[Dict],
        atr_status: Dict,
        time_msk: datetime,
    ) -> List[Dict]:
        """
        Проверка реалтайм ТВХ при пробоях уровней.
        Вызывается каждые 30-60 секунд.
        
        Генерирует НЕМЕДЛЕННЫЙ сигнал если:
        1. Цена пробивает уровень поддержки/сопротивления
        2. Направление совпадает с дневным планом
        3. ATR инструмента < 70% (ещё есть потенциал хода)
        4. Время до 14:00 МСК
        """
        signals = []
        hour = time_msk.hour

        # После 14:00 — не ищем ТВХ
        if hour >= 14:
            return signals

        # Не генерируем если нет направления
        if not self.daily_direction or self.daily_direction == "FENCE":
            return signals

        for level in levels:
            ticker = level.get("ticker", "")
            level_price = level.get("price", 0)
            level_type = level.get("level_type", "")

            if not ticker or ticker not in current_prices or not level_price:
                continue

            price = current_prices[ticker]

            # Проверяем ATR — если уже пройден > 70%, не входим
            ticker_atr = atr_status.get(ticker, {})
            atr_used_pct = ticker_atr.get("atr_used_pct", 0)
            if atr_used_pct > 70:
                continue

            # Проверяем не открыта ли уже сделка по этому тикеру
            active_tickers = [t["ticker"] for t in self.today_trades
                            if t["status"] in (TradeStatus.CONFIRMED.value, TradeStatus.ACTIVE.value)]
            if ticker in active_tickers:
                continue

            # Лимит сделок
            active_count = len([t for t in self.today_trades
                              if t["status"] in (TradeStatus.CONFIRMED.value, TradeStatus.ACTIVE.value)])
            if active_count >= self.max_trades_per_day:
                continue

            # ПРОБОЙ ПОДДЕРЖКИ ВНИЗ → ШОРТ (если дневной план = шорт)
            if (level_type == "support"
                    and price < level_price * 0.998  # Пробой на 0.2%+
                    and self.daily_direction == "SHORT"):

                entry = price
                signal = {
                    "type": "REALTIME_ENTRY",
                    "direction": "SHORT",
                    "ticker": ticker,
                    "entry": entry,
                    "take": round(entry * 0.989, 2),
                    "stop": round(entry * 1.003, 2),
                    "reason": f"Пробой поддержки {level_price:.2f} вниз",
                    "atr_remaining": f"{100 - atr_used_pct:.0f}%",
                    "time": time_msk.strftime("%H:%M"),
                }
                signals.append(signal)

                # Добавляем в today_trades
                self.today_trades.append({
                    **signal,
                    "status": TradeStatus.ACTIVE.value,
                    "score": 3,  # Пробой = автоматически 3 звезды
                })

            # ПРОБОЙ СОПРОТИВЛЕНИЯ ВВЕРХ → ЛОНГ (если дневной план = лонг)
            elif (level_type == "resistance"
                  and price > level_price * 1.002
                  and self.daily_direction == "LONG"):

                entry = price
                signal = {
                    "type": "REALTIME_ENTRY",
                    "direction": "LONG",
                    "ticker": ticker,
                    "entry": entry,
                    "take": round(entry * 1.011, 2),
                    "stop": round(entry * 0.997, 2),
                    "reason": f"Пробой сопротивления {level_price:.2f} вверх",
                    "atr_remaining": f"{100 - atr_used_pct:.0f}%",
                    "time": time_msk.strftime("%H:%M"),
                }
                signals.append(signal)

                self.today_trades.append({
                    **signal,
                    "status": TradeStatus.ACTIVE.value,
                    "score": 3,
                })

        return signals



    # ═══════════════════════════════════════════════════
    # МОНИТОРИНГ ТЕЙКОВ/СТОПОВ АКТИВНЫХ СДЕЛОК
    # ═══════════════════════════════════════════════════

    def check_active_trades(self, current_prices: Dict) -> List[Dict]:
        """Проверяет активные сделки на тейк/стоп."""
        signals = []

        for trade in self.today_trades:
            if trade["status"] not in (TradeStatus.CONFIRMED.value, TradeStatus.ACTIVE.value):
                continue

            ticker = trade["ticker"]
            if ticker not in current_prices:
                continue

            price = current_prices[ticker]
            entry = trade["entry"]
            take = trade["take"]
            stop = trade["stop"]
            direction = trade["direction"]

            if direction == "SHORT":
                if price <= take:
                    trade["status"] = TradeStatus.TAKE_PROFIT.value
                    pnl = (entry - price) / entry * 100
                    signals.append({"type": "TAKE_PROFIT", "ticker": ticker, "pnl": pnl, "price": price})
                elif price >= stop:
                    trade["status"] = TradeStatus.STOP_LOSS.value
                    trade["_stop_time"] = datetime.now(MSK)
                    pnl = -((price - entry) / entry * 100)
                    signals.append({"type": "STOP_LOSS", "ticker": ticker, "pnl": pnl, "price": price})
            else:  # LONG
                if price >= take:
                    trade["status"] = TradeStatus.TAKE_PROFIT.value
                    pnl = (price - entry) / entry * 100
                    signals.append({"type": "TAKE_PROFIT", "ticker": ticker, "pnl": pnl, "price": price})
                elif price <= stop:
                    trade["status"] = TradeStatus.STOP_LOSS.value
                    trade["_stop_time"] = datetime.now(MSK)
                    pnl = -((entry - price) / entry * 100)
                    signals.append({"type": "STOP_LOSS", "ticker": ticker, "pnl": pnl, "price": price})

        return signals

    # ═══════════════════════════════════════════════════
    # ПЕРЕЗАХОД ПОСЛЕ СТОПА
    # ═══════════════════════════════════════════════════

    def check_reentry(
        self,
        current_prices: Dict,
        atr_status: Dict,
        time_msk: datetime,
    ) -> List[Dict]:
        """
        Проверка возможности перезахода после стопа.
        
        Логика Лактионова:
        - Если стоп сработал на ЛОЖНОМ выносе (без объёма, шпилька),
          и цена вернулась в нашу сторону — ПЕРЕЗАХОДИМ.
        - Перезаход только 1 раз (не бесконечно).
        - Перезаход только до 14:00 МСК.
        - ATR должен быть < 60% (ещё есть запас хода).
        - Цена должна вернуться НИЖЕ стопа (для шорта) после выноса.
        
        Условия перезахода:
        1. Сделка закрыта по стопу (status = STOP_LOSS)
        2. Прошло 5-15 минут после стопа
        3. Цена вернулась в направлении сделки (вынос = ложный)
        4. Дневное направление не изменилось
        5. Перезаход ещё не делался по этой сделке
        6. ATR < 60%
        """
        signals = []
        hour = time_msk.hour

        # Не перезаходим после 14:00
        if hour >= 14:
            return signals

        # Не перезаходим если направление поменялось
        if not self.daily_direction or self.daily_direction == "FENCE":
            return signals

        for trade in self.today_trades:
            # Только стопнутые сделки
            if trade.get("status") != TradeStatus.STOP_LOSS.value:
                continue

            # Перезаход только 1 раз
            if trade.get("_reentry_done", False):
                continue

            # Проверяем что прошло достаточно времени (через _stop_time)
            stop_time = trade.get("_stop_time")
            if stop_time:
                elapsed = (time_msk - stop_time).total_seconds()
                if elapsed < 300:  # Минимум 5 минут
                    continue
                if elapsed > 1800:  # Максимум 30 минут
                    continue

            ticker = trade["ticker"]
            direction = trade["direction"]

            if ticker not in current_prices:
                continue

            price = current_prices[ticker]
            original_entry = trade["entry"]
            original_stop = trade["stop"]

            # Проверяем ATR
            ticker_atr = atr_status.get(ticker, {})
            atr_used_pct = ticker_atr.get("atr_used_pct", 0)
            if atr_used_pct > 60:
                continue  # ATR уже 60%+ — не стоит перезаходить

            # Проверяем что цена ВЕРНУЛАСЬ в нашу сторону
            # Для шорта: цена должна быть НИЖЕ оригинального входа
            # (т.е. вынос вверх был ложным, цена откатилась)
            reentry_confirmed = False

            if direction == "SHORT":
                # Цена ниже оригинального входа = вынос был ложный
                if price < original_entry:
                    reentry_confirmed = True
            elif direction == "LONG":
                # Цена выше оригинального входа = вынос был ложный
                if price > original_entry:
                    reentry_confirmed = True

            if not reentry_confirmed:
                continue

            # Лимит активных сделок
            active_count = len([t for t in self.today_trades
                              if t.get("status") in (TradeStatus.CONFIRMED.value, TradeStatus.ACTIVE.value)])
            if active_count >= self.max_trades_per_day:
                continue

            # ПЕРЕЗАХОДИМ!
            trade["_reentry_done"] = True

            new_entry = price
            if direction == "SHORT":
                new_take = round(new_entry * 0.989, 2)
                new_stop = round(new_entry * 1.003, 2)
            else:
                new_take = round(new_entry * 1.011, 2)
                new_stop = round(new_entry * 0.997, 2)

            reentry_trade = {
                "type": "REENTRY",
                "direction": direction,
                "ticker": ticker,
                "entry": new_entry,
                "take": new_take,
                "stop": new_stop,
                "status": TradeStatus.ACTIVE.value,
                "score": trade.get("score", 3),
                "reason": f"Перезаход после ложного выноса (стоп {original_stop:.2f})",
                "time": time_msk.strftime("%H:%M"),
                "_reentry_done": True,  # Не перезаходим дважды
            }

            self.today_trades.append(reentry_trade)

            signals.append({
                "type": "REENTRY",
                "direction": direction,
                "ticker": ticker,
                "entry": new_entry,
                "take": new_take,
                "stop": new_stop,
                "reason": f"Ложный вынос отработан. Цена вернулась к {new_entry:.2f}",
                "atr_remaining": f"{100 - atr_used_pct:.0f}%",
                "time": time_msk.strftime("%H:%M"),
            })

        return signals

    def format_reentry_signal(self, signal: Dict) -> str:
        """Форматирует сигнал перезахода для Telegram."""
        d = "🔴" if signal["direction"] == "SHORT" else "🟢"
        msg = f"🔄 <b>ПЕРЕЗАХОД ({signal['time']})</b>\n\n"
        msg += f"{d} <b>{signal['ticker']}</b> "
        msg += f"{'ШОРТ' if signal['direction'] == 'SHORT' else 'ЛОНГ'}\n"
        msg += f"   Вход: {signal['entry']} | Тейк: {signal['take']} | Стоп: {signal['stop']}\n"
        msg += f"   Причина: {signal['reason']}\n"
        msg += f"   ATR остаток: {signal['atr_remaining']}\n"
        msg += f"   <i>⚠️ Перезаход — максимум 1 раз по инструменту</i>\n"
        return msg

    # ═══════════════════════════════════════════════════
    # ФОРМАТИРОВАНИЕ СООБЩЕНИЙ
    # ═══════════════════════════════════════════════════

    def format_plan_message(self) -> str:
        """Форматирует ПЛАН (этап 1) для Telegram."""
        if not self.today_trades:
            return "🤖 Нет сделок на сегодня"

        dir_emoji = {"SHORT": "🔴", "LONG": "🟢", "FENCE": "🟡"}
        dir_text = {"SHORT": "ШОРТ", "LONG": "ЛОНГ", "FENCE": "ЗАБОР"}
        stars = {1: "★", 2: "★★", 3: "★★★", 4: "★★★★", 5: "★★★★★"}

        msg = "🤖 <b>ПЛАН НА СЕССИЮ</b> (ждём подтверждения)\n\n"
        msg += f"Направление: {dir_emoji.get(self.daily_direction, '⚪')} "
        msg += f"<b>{dir_text.get(self.daily_direction, '?')}</b>\n"
        if self.daily_logic:
            msg += f"Логика: <i>{self.daily_logic}</i>\n\n"

        for i, trade in enumerate(self.today_trades, 1):
            d = "🔴" if trade["direction"] == "SHORT" else "🟢"
            score = trade.get("score", 1)
            ticker = trade["ticker"]
            entry = trade["entry"]
            take = trade["take"]
            stop = trade["stop"]

            msg += f"{d} <b>{ticker}</b> {stars.get(score, '★')} "
            msg += f"{'ШОРТ' if trade['direction'] == 'SHORT' else 'ЛОНГ'}\n"
            msg += f"   Вход: ~{entry} | Тейк: {take} | Стоп: {stop}\n"
            msg += f"   <i>Статус: ожидает подтверждения 1-м баром</i>\n\n"

        msg += "⏳ <b>Подтверждение придёт через 5 мин после открытия</b>"
        return msg

    def format_confirmation_message(self, confirmed: List[Dict]) -> str:
        """Форматирует ПОДТВЕРЖДЕНИЕ (этап 2)."""
        if not confirmed:
            return "⚠️ Ни одна сделка не подтверждена первым баром. Жду следующий бар."

        msg = "✅ <b>ПОДТВЕРЖДЕНИЕ — ВХОДИМ!</b>\n\n"
        for trade in confirmed:
            d = "🔴" if trade["direction"] == "SHORT" else "🟢"
            msg += f"{d} <b>{trade['ticker']}</b> "
            msg += f"{'ШОРТ' if trade['direction'] == 'SHORT' else 'ЛОНГ'}\n"
            msg += f"   Вход: {trade['entry']} | Тейк: {trade['take']} | Стоп: {trade['stop']}\n"
            msg += f"   ✅ Первый бар подтвердил направление\n\n"

        return msg

    def format_realtime_signal(self, signal: Dict) -> str:
        """Форматирует реалтайм ТВХ."""
        d = "🔴" if signal["direction"] == "SHORT" else "🟢"
        msg = f"⚡ <b>РЕАЛТАЙМ ТВХ ({signal['time']})</b>\n\n"
        msg += f"{d} <b>{signal['ticker']}</b> "
        msg += f"{'ШОРТ' if signal['direction'] == 'SHORT' else 'ЛОНГ'}\n"
        msg += f"   Вход: {signal['entry']} | Тейк: {signal['take']} | Стоп: {signal['stop']}\n"
        msg += f"   Причина: {signal['reason']}\n"
        msg += f"   ATR остаток: {signal['atr_remaining']}\n"
        return msg



    # ═══════════════════════════════════════════════════
    # ВСПОМОГАТЕЛЬНЫЕ МЕТОДЫ
    # ═══════════════════════════════════════════════════

    def _get_system_prompt(self) -> str:
        """Системный промпт с моделью Лактионова."""
        return """Ты интрадей-аналитик ММВБ. Торгуешь строго по модели Лактионова (280 сделок, 81% винрейт).

ЖЕЛЕЗНЫЕ ПРАВИЛА:
1. Тейк: +0.9-1.5%. Стоп: ВСЕГДА -0.3%. Либо тейк, либо стоп.
2. Качество важнее количества. Лучше 3 сделки на ★★★★★ чем 7 на ★★.
3. Вход с ОТКРЫТИЯ на первых 5-минутных барах.
4. Выход по 1-й цели или при ATR 1%. НЕ ЖОПИТЬ.
5. Не торгуем после 14:00 МСК.
6. Мин объём инструмента 300 млн руб/день.

ВЫБОР НАПРАВЛЕНИЯ:
- Нет драйвера роста + спеклонг → ШОРТ (80% сделок)
- Паранорм бары вчера вниз + нет негатива → ЛОНГ
- Фон негативный + индексы минус + товарка минус → ШОРТ
- Нефть растёт но рынок НЕ реагирует → РАСХОЖДЕНИЕ = ШОРТ

КРИТИЧЕСКИЕ ПРАВИЛА ОТБОРА ИНСТРУМЕНТОВ:
- НИКОГДА не шорти вчерашних лидеров падения (> -1.5%). Импульс отработан!
- НИКОГДА не лонгуй вчерашних лидеров роста (> +1.5%). Фиксация!
- Приоритет: тикеры которые Лактионов ЯВНО пометил как шорт/лонг.
- "Хуже рынка" = приоритет для шорта. "Лучше рынка" = НЕ шортить.

ВАЖНО О КОНТРТРЕНДЕ:
Когда Лактионов пишет "ВТБ 91 лонг под 90-89" но общий план = ШОРТ:
- Это НЕ значит "покупай ВТБ"!
- Это значит: ВТБ выглядит лонгово (зелёный бар)
- При ПРОБОЕ поддержки 90 = ШОРТ (контртренд, ловушка)
- Такие тикеры — ПРИОРИТЕТ для шорта при пробое!

ФОРМАТ ОТВЕТА (СТРОГО):
НАПРАВЛЕНИЕ: [ШОРТ/ЛОНГ/ЗАБОР]
ЛОГИКА: [1-2 предложения почему именно это направление]
СДЕЛКА 1: [ШОРТ/ЛОНГ] [ТИКЕР] от [ЦЕНА], тейк [ЦЕНА], стоп [ЦЕНА]
СДЕЛКА 2: ...
НЕ ТОРГУЕМ: [тикеры и причины]

Дай 4-6 ЛУЧШИХ сделок. Качество > количество."""

    def _format_morning_prompt(
        self, channel_data, market_data, prev_moves,
        yesterday_fallers, yesterday_risers, paranorm_bars
    ) -> str:
        """Формирует утренний промпт."""
        channel_text = channel_data.get("raw_text", "Нет данных")
        levels_text = channel_data.get("levels_text", "")

        prev_day_text = "ПРЕДЫДУЩИЙ ДЕНЬ:\n"
        sorted_moves = sorted(prev_moves.items(), key=lambda x: x[1])
        for ticker, move in sorted_moves[:5]:
            prev_day_text += f"  {ticker}: {move:+.2f}%{' ⚠️ЛИДЕР ПАДЕНИЯ' if move < -1.5 else ''}\n"
        prev_day_text += "  ...\n"
        for ticker, move in sorted_moves[-5:]:
            prev_day_text += f"  {ticker}: {move:+.2f}%{' ⚠️ЛИДЕР РОСТА' if move > 1.5 else ''}\n"

        paranorm_text = "Нет" if not paranorm_bars else ", ".join(paranorm_bars)

        return f"""ДАТА: {datetime.now(MSK).strftime('%d.%m.%Y')} (06:45 МСК, за 15 мин до открытия)

АНАЛИТИКА ЛАКТИОНОВА:
{channel_text[:2500]}

УРОВНИ:
{levels_text[:1500]}

ФОН:
- Нефть: {market_data.get('oil_change', '?')}
- Фьючерс ММВБ: {market_data.get('mx_price', '?')}
- Золото: {market_data.get('gold_change', '?')}
- USD/RUB: {market_data.get('usd_change', '?')}

{prev_day_text}

ПАРАНОРМ БАРЫ ВЧЕРА: {paranorm_text}
⚠️ НЕ ШОРТИТЬ: {', '.join(yesterday_fallers) if yesterday_fallers else 'нет'}
⚠️ НЕ ЛОНГОВАТЬ: {', '.join(yesterday_risers) if yesterday_risers else 'нет'}

ЗАДАЧА: Дай 4-6 ЛУЧШИХ сделок. Качество важнее количества.
Помни контртренд: "лонг" в плане + общий ШОРТ = шорти при пробое поддержки."""

    def _parse_trades_from_response(self, response: str) -> List[Dict]:
        """Парсит сделки из ответа LLM."""
        trades = []
        for line in response.split("\n"):
            line = line.strip()
            match = re.search(
                r'(?:СДЕЛКА\s*\d*:?\s*)?(ШОРТ|ЛОНГ|SHORT|LONG)\s+'
                r'(\w+)\s+от\s+([\d.,]+).*?'
                r'тейк\s+([\d.,]+).*?'
                r'стоп\s+([\d.,]+)',
                line, re.IGNORECASE
            )
            if match:
                direction = "SHORT" if match.group(1).upper() in ("ШОРТ", "SHORT") else "LONG"
                trades.append({
                    "direction": direction,
                    "ticker": match.group(2).upper(),
                    "entry": float(match.group(3).replace(",", ".")),
                    "take": float(match.group(4).replace(",", ".")),
                    "stop": float(match.group(5).replace(",", ".")),
                })
        return trades

    def _filter_trades(self, trades, yesterday_fallers, yesterday_risers, previous_day_data):
        """Фильтрует сделки по правилам."""
        filtered = []
        for trade in trades:
            ticker = trade["ticker"]
            direction = trade["direction"]

            if direction == "SHORT" and ticker in yesterday_fallers:
                print(f"[AI] ФИЛЬТР: {ticker} — вчера лидер падения")
                continue
            if direction == "LONG" and ticker in yesterday_risers:
                print(f"[AI] ФИЛЬТР: {ticker} — вчера лидер роста")
                continue

            entry = trade["entry"]
            if direction == "SHORT":
                if trade["take"] >= entry or trade["stop"] <= entry:
                    trade["take"] = round(entry * 0.989, 2)
                    trade["stop"] = round(entry * 1.003, 2)
            else:
                if trade["take"] <= entry or trade["stop"] >= entry:
                    trade["take"] = round(entry * 1.011, 2)
                    trade["stop"] = round(entry * 0.997, 2)

            filtered.append(trade)
        return filtered

    def _determine_direction(self, response: str) -> str:
        """Определяет направление из ответа."""
        upper = response.upper()
        if "НАПРАВЛЕНИЕ: ШОРТ" in upper or "НАПРАВЛЕНИЕ: SHORT" in upper:
            return "SHORT"
        elif "НАПРАВЛЕНИЕ: ЛОНГ" in upper or "НАПРАВЛЕНИЕ: LONG" in upper:
            return "LONG"
        elif "ЗАБОР" in upper:
            return "FENCE"
        return "UNKNOWN"

    def _extract_logic(self, response: str) -> str:
        """Извлекает логику из ответа."""
        match = re.search(r'ЛОГИКА:\s*(.+?)(?:\n|СДЕЛКА)', response, re.IGNORECASE | re.DOTALL)
        if match:
            return match.group(1).strip()[:200]
        return ""

    # Обратная совместимость
    def morning_analysis(self, channel_data, market_data, previous_day_data, paranorm_bars):
        """Обратная совместимость с main.py."""
        return self.morning_plan(channel_data, market_data, previous_day_data, paranorm_bars)

    def format_trades_message(self) -> str:
        """Обратная совместимость."""
        return self.format_plan_message()
