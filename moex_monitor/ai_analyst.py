"""
AI-аналитик на базе DeepSeek V4 Pro через Fireworks.ai.
Даёт конкретные торговые рекомендации по модели Лактионова.

КЛЮЧЕВЫЕ ПРАВИЛА (усвоены из анализа март-май 2026):
1. Тейк +0.9-1.5%, стоп ВСЕГДА -0.3%. Либо тейк, либо стоп.
2. Не более 8 сделок за день.
3. Вход с открытия (07:00 срочка / 10:00 ОС) на первых барах.
4. Выход при ATR 1% или по 1-й цели.
5. Не торгуем после 14:00 МСК.
6. Мин объём 300 млн руб.
7. НЕ шортить вчерашних лидеров падения (импульс уже отработан).
8. НЕ лонговать вчерашних лидеров роста.
9. Если Лактионов пишет "лонг" для тикера, но общий план ШОРТ —
   это НЕ рекомендация лонга! Это значит тикер выглядит лонгово
   (закрылся зелёным баром), и при ПРОБОЕ поддержки он становится
   кандидатом на контртрендовый ШОРТ.
10. 80% сделок Лактионова = шорт. При отсутствии драйвера = шорт.
11. Паранорм бары (ход > 2%) = 80% вероятность отката/проторговки следующий день.
12. Больше сделок (6-7) = лучшая диверсификация риска.
"""

import json
import os
import urllib.request
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple

MSK = timezone(timedelta(hours=3))

# Fireworks.ai настройки
FIREWORKS_API_KEY = os.getenv("FIREWORKS_API_KEY", "")
FIREWORKS_MODEL = os.getenv(
    "FIREWORKS_MODEL", "accounts/fireworks/models/deepseek-v4-pro"
)
FIREWORKS_URL = "https://api.fireworks.ai/inference/v1/chat/completions"


class AIAnalyst:
    """AI-аналитик для интрадей торговли по модели Лактионова."""

    def __init__(self):
        self.api_key = FIREWORKS_API_KEY
        self.model = FIREWORKS_MODEL
        self.today_trades: List[Dict] = []
        self.daily_direction: Optional[str] = None
        self.max_trades_per_day = 8

    def _call_llm(self, system_prompt: str, user_prompt: str, max_tokens: int = 1500) -> str:
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

    def morning_analysis(
        self,
        channel_data: Dict,
        market_data: Dict,
        previous_day_data: Dict,
        paranorm_bars: List[str],
    ) -> Dict:
        """
        Утренний анализ (06:00 МСК). Определяет направление дня и сделки.

        Args:
            channel_data: Данные из канала Лактионова (уровни, план)
            market_data: Текущий фон (нефть, фьючерсы, товарка)
            previous_day_data: Данные предыдущего торгового дня (O/H/L/C по тикерам)
            paranorm_bars: Список тикеров с паранорм барами вчера (ход > 2%)
        """
        # Определяем вчерашних лидеров падения/роста
        prev_moves = {}
        for ticker, data in previous_day_data.items():
            if data.get("open") and data["open"] > 0:
                move = (data["close"] - data["open"]) / data["open"] * 100
                prev_moves[ticker] = move

        yesterday_fallers = [t for t, m in prev_moves.items() if m < -1.5]
        yesterday_risers = [t for t, m in prev_moves.items() if m > 1.5]

        # Формируем промпт
        system_prompt = self._get_system_prompt()

        user_prompt = self._format_morning_prompt(
            channel_data, market_data, prev_moves,
            yesterday_fallers, yesterday_risers, paranorm_bars
        )

        # Вызываем LLM
        response = self._call_llm(system_prompt, user_prompt, max_tokens=2000)

        # Парсим ответ
        trades = self._parse_trades_from_response(response)

        # Фильтруем сделки по правилам
        filtered_trades = self._filter_trades(
            trades, yesterday_fallers, yesterday_risers, previous_day_data
        )

        self.today_trades = filtered_trades[:self.max_trades_per_day]
        self.daily_direction = self._determine_direction(response)

        return {
            "direction": self.daily_direction,
            "trades": self.today_trades,
            "analysis": response,
            "filtered_out": len(trades) - len(filtered_trades),
        }

    def intraday_check(
        self,
        current_prices: Dict,
        atr_status: Dict,
        time_msk: datetime,
    ) -> List[Dict]:
        """
        Интрадей проверка (каждые 5-10 минут).
        Возвращает список сигналов если есть.
        """
        signals = []
        hour = time_msk.hour

        # После 14:00 — не рекомендуем новые входы
        if hour >= 14:
            return signals

        for trade in self.today_trades:
            ticker = trade["ticker"]
            if ticker not in current_prices:
                continue

            price = current_prices[ticker]
            entry = trade["entry"]
            take = trade["take"]
            stop = trade["stop"]
            direction = trade["direction"]

            # Проверяем тейк
            if direction == "SHORT" and price <= take:
                signals.append({
                    "type": "TAKE_PROFIT",
                    "ticker": ticker,
                    "price": price,
                    "pnl": (entry - price) / entry * 100,
                })
            elif direction == "LONG" and price >= take:
                signals.append({
                    "type": "TAKE_PROFIT",
                    "ticker": ticker,
                    "price": price,
                    "pnl": (price - entry) / entry * 100,
                })

            # Проверяем стоп
            if direction == "SHORT" and price >= stop:
                signals.append({
                    "type": "STOP_LOSS",
                    "ticker": ticker,
                    "price": price,
                    "pnl": -((price - entry) / entry * 100),
                })
            elif direction == "LONG" and price <= stop:
                signals.append({
                    "type": "STOP_LOSS",
                    "ticker": ticker,
                    "price": price,
                    "pnl": -((entry - price) / entry * 100),
                })

        return signals

    def evening_summary(self, day_results: Dict) -> str:
        """Вечерний итог дня + паранорм бары на завтра."""
        system_prompt = (
            "Ты аналитик интрадея ММВБ. Подведи итоги дня кратко. "
            "Укажи паранорм бары (ход > 2%) и что это значит на завтра."
        )

        user_prompt = f"""Результаты дня:
{json.dumps(day_results, ensure_ascii=False, indent=2)}

Формат ответа:
1. Итоги дня (2-3 строки)
2. Паранорм бары (тикеры с ходом > 2%)
3. Что это значит на завтра
4. Предварительный план на завтра (1 строка)"""

        return self._call_llm(system_prompt, user_prompt, max_tokens=800)

    def _get_system_prompt(self) -> str:
        """Системный промпт с полной моделью Лактионова."""
        return """Ты интрадей-аналитик ММВБ. Торгуешь строго по модели Лактионова (280 сделок, 81% винрейт).

ЖЕЛЕЗНЫЕ ПРАВИЛА:
1. Тейк: +0.9-1.5%. Стоп: ВСЕГДА -0.3%. Либо тейк, либо стоп.
2. Максимум 6-7 сделок за день (не менее 4 для диверсификации).
3. Вход с ОТКРЫТИЯ на первых 5-минутных барах.
4. Выход по 1-й цели или при ATR 1%. НЕ ЖОПИТЬ.
5. Не торгуем после 14:00 МСК.
6. Мин объём инструмента 300 млн руб/день.

ВЫБОР НАПРАВЛЕНИЯ:
- Нет драйвера роста + спеклонг → ШОРТ (80% сделок)
- Паранорм бары вчера вниз + нет негатива → ЛОНГ
- Фон негативный + индексы минус + товарка минус → ШОРТ
- Нефть растёт но рынок НЕ реагирует → РАСХОЖДЕНИЕ = ШОРТ (рынок прав, нефть догонит)

КРИТИЧЕСКИЕ ПРАВИЛА ОТБОРА ИНСТРУМЕНТОВ:
- НИКОГДА не шорти вчерашних лидеров падения (> -1.5%). Импульс отработан!
- НИКОГДА не лонгуй вчерашних лидеров роста (> +1.5%). Фиксация!
- Приоритет: тикеры которые Лактионов ЯВНО пометил как шорт/лонг.
- "Хуже рынка" = приоритет для шорта. "Лучше рынка" = НЕ шортить.

ВАЖНО О КОНТРТРЕНДЕ:
Когда Лактионов пишет "ВТБ 91 лонг под 90-89" но общий план = ШОРТ:
- Это НЕ значит "покупай ВТБ"!
- Это значит: ВТБ закрылся зелёным баром (ВЫГЛЯДИТ лонгово)
- При ПРОБОЕ поддержки 90 = ШОРТ (контртренд, ловушка для лонгистов)
- Такие тикеры — ПРИОРИТЕТ для шорта при пробое!

ФОРМАТ ОТВЕТА (СТРОГО):
НАПРАВЛЕНИЕ: [ШОРТ/ЛОНГ/ЗАБОР]
ЛОГИКА: [2-3 предложения]
СДЕЛКА 1: [ШОРТ/ЛОНГ] [ТИКЕР] от [ЦЕНА], тейк [ЦЕНА], стоп [ЦЕНА]
СДЕЛКА 2: ...
...
НЕ ТОРГУЕМ: [тикеры и причины]

Дай 5-7 сделок. НЕ пиши ничего лишнего."""

    def _format_morning_prompt(
        self,
        channel_data: Dict,
        market_data: Dict,
        prev_moves: Dict,
        yesterday_fallers: List[str],
        yesterday_risers: List[str],
        paranorm_bars: List[str],
    ) -> str:
        """Формирует утренний промпт с данными."""

        # Форматируем данные канала
        channel_text = channel_data.get("raw_text", "Нет данных из канала")
        levels_text = channel_data.get("levels_text", "")

        # Форматируем предыдущий день
        prev_day_text = "ПРЕДЫДУЩИЙ ДЕНЬ:\n"
        sorted_moves = sorted(prev_moves.items(), key=lambda x: x[1])
        for ticker, move in sorted_moves[:5]:
            prev_day_text += f"  {ticker}: {move:+.2f}% {'⚠️ ЛИДЕР ПАДЕНИЯ' if move < -1.5 else ''}\n"
        prev_day_text += "  ...\n"
        for ticker, move in sorted_moves[-5:]:
            prev_day_text += f"  {ticker}: {move:+.2f}% {'⚠️ ЛИДЕР РОСТА' if move > 1.5 else ''}\n"

        # Паранорм бары
        paranorm_text = "Нет" if not paranorm_bars else ", ".join(paranorm_bars)

        prompt = f"""ДАТА: {datetime.now(MSK).strftime('%d.%m.%Y')} (утро, 06:50 МСК)

АНАЛИТИКА ИЗ КАНАЛА ЛАКТИОНОВА:
{channel_text}

УРОВНИ:
{levels_text}

ФОН НА ОТКРЫТИЕ:
- Нефть Brent: {market_data.get('oil_change', '?')}
- Фьючерс ММВБ (MXM): {market_data.get('mx_price', '?')}
- Золото: {market_data.get('gold_change', '?')}
- USD/RUB: {market_data.get('usd_change', '?')}
- Индексы мировые: {market_data.get('global_indices', '?')}

{prev_day_text}

ПАРАНОРМ БАРЫ ВЧЕРА (ход > 2%): {paranorm_text}

⚠️ НЕ ШОРТИТЬ (вчера уже упали > 1.5%): {', '.join(yesterday_fallers) if yesterday_fallers else 'нет'}
⚠️ НЕ ЛОНГОВАТЬ (вчера уже выросли > 1.5%): {', '.join(yesterday_risers) if yesterday_risers else 'нет'}

ЗАДАЧА: Дай конкретные сделки на открытие. 5-7 штук.
Помни: если тикер помечен "лонг" но общий план ШОРТ = это контртренд, шорти при пробое поддержки."""

        return prompt

    def _parse_trades_from_response(self, response: str) -> List[Dict]:
        """Парсит сделки из ответа LLM."""
        trades = []
        lines = response.split("\n")

        for line in lines:
            line = line.strip()
            # Ищем строки вида: СДЕЛКА N: ШОРТ TICKER от PRICE, тейк PRICE, стоп PRICE
            match = re.search(
                r'(?:СДЕЛКА\s*\d*:?\s*)?(ШОРТ|ЛОНГ|SHORT|LONG)\s+'
                r'(\w+)\s+от\s+([\d.,]+).*?'
                r'тейк\s+([\d.,]+).*?'
                r'стоп\s+([\d.,]+)',
                line, re.IGNORECASE
            )
            if match:
                direction = "SHORT" if match.group(1).upper() in ("ШОРТ", "SHORT") else "LONG"
                ticker = match.group(2).upper()
                entry = float(match.group(3).replace(",", "."))
                take = float(match.group(4).replace(",", "."))
                stop = float(match.group(5).replace(",", "."))

                trades.append({
                    "direction": direction,
                    "ticker": ticker,
                    "entry": entry,
                    "take": take,
                    "stop": stop,
                })

        return trades

    def _filter_trades(
        self,
        trades: List[Dict],
        yesterday_fallers: List[str],
        yesterday_risers: List[str],
        previous_day_data: Dict,
    ) -> List[Dict]:
        """
        Фильтрует сделки по правилам:
        - Не шортить вчерашних лидеров падения
        - Не лонговать вчерашних лидеров роста
        - Проверить корректность тейка/стопа
        """
        filtered = []

        for trade in trades:
            ticker = trade["ticker"]
            direction = trade["direction"]

            # Правило: НЕ шортить то что вчера уже упало сильно
            if direction == "SHORT" and ticker in yesterday_fallers:
                print(f"[AI] ФИЛЬТР: {ticker} исключён — вчера уже упал (лидер падения)")
                continue

            # Правило: НЕ лонговать то что вчера уже выросло сильно
            if direction == "LONG" and ticker in yesterday_risers:
                print(f"[AI] ФИЛЬТР: {ticker} исключён — вчера уже вырос (лидер роста)")
                continue

            # Проверяем корректность тейка/стопа
            entry = trade["entry"]
            take = trade["take"]
            stop = trade["stop"]

            if direction == "SHORT":
                # Для шорта: тейк должен быть НИЖЕ входа, стоп ВЫШЕ
                if take >= entry or stop <= entry:
                    # Пересчитываем
                    trade["take"] = round(entry * 0.989, 2)  # -1.1%
                    trade["stop"] = round(entry * 1.003, 2)  # +0.3%
            else:
                # Для лонга: тейк ВЫШЕ входа, стоп НИЖЕ
                if take <= entry or stop >= entry:
                    trade["take"] = round(entry * 1.011, 2)  # +1.1%
                    trade["stop"] = round(entry * 0.997, 2)  # -0.3%

            # Проверяем что стоп не слишком узкий (минимум 0.2%)
            if direction == "SHORT":
                stop_pct = (trade["stop"] - entry) / entry * 100
            else:
                stop_pct = (entry - trade["stop"]) / entry * 100

            if stop_pct < 0.2:
                # Стоп слишком узкий — расширяем до 0.3%
                if direction == "SHORT":
                    trade["stop"] = round(entry * 1.003, 2)
                else:
                    trade["stop"] = round(entry * 0.997, 2)

            filtered.append(trade)

        return filtered

    def _determine_direction(self, response: str) -> str:
        """Определяет общее направление из ответа LLM."""
        response_upper = response.upper()
        if "НАПРАВЛЕНИЕ: ШОРТ" in response_upper or "НАПРАВЛЕНИЕ: SHORT" in response_upper:
            return "SHORT"
        elif "НАПРАВЛЕНИЕ: ЛОНГ" in response_upper or "НАПРАВЛЕНИЕ: LONG" in response_upper:
            return "LONG"
        elif "ЗАБОР" in response_upper:
            return "FENCE"
        return "UNKNOWN"

    def format_trades_message(self) -> str:
        """Форматирует сделки для отправки в Telegram."""
        if not self.today_trades:
            return "🤖 Нет сделок на сегодня"

        direction_emoji = {"SHORT": "🔴", "LONG": "🟢", "FENCE": "🟡"}
        direction_text = {"SHORT": "ШОРТ", "LONG": "ЛОНГ", "FENCE": "ЗАБОР"}

        msg = f"🤖 <b>AI РЕКОМЕНДАЦИИ</b>\n\n"
        msg += f"Направление: {direction_emoji.get(self.daily_direction, '⚪')} "
        msg += f"<b>{direction_text.get(self.daily_direction, '?')}</b>\n\n"

        for i, trade in enumerate(self.today_trades, 1):
            d = "🔴" if trade["direction"] == "SHORT" else "🟢"
            ticker = trade["ticker"]
            entry = trade["entry"]
            take = trade["take"]
            stop = trade["stop"]
            take_pct = abs(take - entry) / entry * 100
            stop_pct = abs(stop - entry) / entry * 100

            msg += f"{d} <b>СДЕЛКА {i}:</b> "
            msg += f"{'ШОРТ' if trade['direction'] == 'SHORT' else 'ЛОНГ'} {ticker}\n"
            msg += f"   Вход: {entry} | Тейк: {take} (+{take_pct:.1f}%) | Стоп: {stop} (-{stop_pct:.1f}%)\n\n"

        msg += f"<i>Всего сделок: {len(self.today_trades)}/{self.max_trades_per_day}</i>"
        return msg


# Импорт re в начало файла
import re
