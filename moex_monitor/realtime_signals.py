"""
Модуль реалтайм сигналов — ТВХ при пробоях уровней во время торговой сессии.

Логика:
1. Получает уровни из утреннего анализа Лактионова
2. Каждые 30 сек проверяет текущие цены vs уровни
3. При пробое + совпадении с дневным направлением + ATR < 70% → НЕМЕДЛЕННЫЙ сигнал
4. Учитывает "ложные пробои" — требует закрепление ниже/выше уровня (2 тика подряд)
5. Не генерирует сигналы после 14:00 МСК

Типы сигналов:
- BREAKOUT_SHORT: пробой поддержки вниз → шорт
- BREAKOUT_LONG: пробой сопротивления вверх → лонг  
- BOUNCE_LONG: отскок от поддержки → лонг (только если дневной план = лонг)
- BOUNCE_SHORT: отскок от сопротивления → шорт (только если дневной план = шорт)
"""

import urllib.request
import json
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Set

MSK = timezone(timedelta(hours=3))


class RealtimeSignals:
    """Генератор реалтайм ТВХ на основе пробоев уровней."""

    def __init__(self):
        self.levels: List[Dict] = []
        self.daily_direction: Optional[str] = None
        self.triggered_levels: Set[str] = set()  # Уже сработавшие (не повторять)
        self._breakout_candidates: Dict[str, int] = {}  # ticker:level → тики подряд
        self.min_atr_remaining = 30  # Минимум 30% ATR должно остаться
        self.max_signals_per_session = 5
        self._signals_sent = 0

    def configure(self, levels: List[Dict], direction: str):
        """
        Настроить на новый день.
        
        Args:
            levels: [{ticker, level_type, price}, ...]
            direction: "SHORT" / "LONG" / "FENCE"
        """
        self.levels = levels
        self.daily_direction = direction
        self.triggered_levels = set()
        self._breakout_candidates = {}
        self._signals_sent = 0
        print(f"[RT] Настроен: {len(levels)} уровней, направление={direction}")

    def check(
        self,
        current_prices: Dict[str, float],
        atr_status: Dict[str, Dict],
        time_msk: datetime,
    ) -> List[Dict]:
        """
        Проверка на пробои. Вызывается каждые 30-60 секунд.
        
        Args:
            current_prices: {ticker: last_price}
            atr_status: {ticker: {"atr_used_pct": float, "open": float}}
            time_msk: текущее время МСК
            
        Returns:
            Список сигналов (может быть пустой)
        """
        signals = []

        # Не генерируем после 14:00
        if time_msk.hour >= 14:
            return signals

        # Не генерируем до 07:00 (срочный рынок ещё не открылся)
        if time_msk.hour < 7:
            return signals

        # Лимит сигналов за сессию
        if self._signals_sent >= self.max_signals_per_session:
            return signals

        # Не генерируем если направление = ЗАБОР
        if not self.daily_direction or self.daily_direction == "FENCE":
            return signals

        for level in self.levels:
            ticker = level.get("ticker", "")
            level_price = level.get("price", 0)
            level_type = level.get("level_type", "")

            if not ticker or not level_price:
                continue
            if ticker not in current_prices:
                continue

            # Уникальный ключ уровня
            level_key = f"{ticker}:{level_type}:{level_price}"
            if level_key in self.triggered_levels:
                continue

            price = current_prices[ticker]

            # Проверяем ATR
            ticker_atr = atr_status.get(ticker, {})
            atr_used = ticker_atr.get("atr_used_pct", 0)
            if atr_used > (100 - self.min_atr_remaining):
                continue  # ATR почти пройден — поздно входить

            # === ПРОБОЙ ПОДДЕРЖКИ ВНИЗ ===
            if level_type == "support" and self.daily_direction == "SHORT":
                breakout_threshold = level_price * 0.998  # 0.2% ниже = пробой
                if price < breakout_threshold:
                    signal = self._confirm_breakout(
                        level_key, ticker, price, level_price,
                        "SHORT", "BREAKOUT_SHORT",
                        f"Пробой поддержки {level_price:.2f}",
                        atr_used, time_msk
                    )
                    if signal:
                        signals.append(signal)
                else:
                    # Сброс счётчика если цена вернулась
                    self._breakout_candidates.pop(level_key, None)

            # === ПРОБОЙ СОПРОТИВЛЕНИЯ ВВЕРХ ===
            elif level_type == "resistance" and self.daily_direction == "LONG":
                breakout_threshold = level_price * 1.002
                if price > breakout_threshold:
                    signal = self._confirm_breakout(
                        level_key, ticker, price, level_price,
                        "LONG", "BREAKOUT_LONG",
                        f"Пробой сопротивления {level_price:.2f}",
                        atr_used, time_msk
                    )
                    if signal:
                        signals.append(signal)
                else:
                    self._breakout_candidates.pop(level_key, None)

            # === ОТСКОК ОТ СОПРОТИВЛЕНИЯ (шорт) ===
            elif level_type == "resistance" and self.daily_direction == "SHORT":
                # Цена подошла к сопротивлению и не пробила (отскок)
                near_threshold = level_price * 0.003  # 0.3% от уровня
                if abs(price - level_price) < near_threshold and price < level_price:
                    signal = self._confirm_breakout(
                        level_key, ticker, price, level_price,
                        "SHORT", "BOUNCE_SHORT",
                        f"Отскок от сопротивления {level_price:.2f}",
                        atr_used, time_msk
                    )
                    if signal:
                        signals.append(signal)

            # === ОТСКОК ОТ ПОДДЕРЖКИ (лонг) ===
            elif level_type == "support" and self.daily_direction == "LONG":
                near_threshold = level_price * 0.003
                if abs(price - level_price) < near_threshold and price > level_price:
                    signal = self._confirm_breakout(
                        level_key, ticker, price, level_price,
                        "LONG", "BOUNCE_LONG",
                        f"Отскок от поддержки {level_price:.2f}",
                        atr_used, time_msk
                    )
                    if signal:
                        signals.append(signal)

        return signals

    def _confirm_breakout(
        self, level_key: str, ticker: str, price: float,
        level_price: float, direction: str, signal_type: str,
        reason: str, atr_used: float, time_msk: datetime
    ) -> Optional[Dict]:
        """
        Подтверждение пробоя — требует 2 тика подряд за уровнем.
        Защита от ложных пробоев.
        """
        count = self._breakout_candidates.get(level_key, 0) + 1
        self._breakout_candidates[level_key] = count

        # Требуем 2 подряд (при интервале 30 сек = 1 минута закрепления)
        if count < 2:
            return None

        # Подтверждён! Генерируем сигнал
        self.triggered_levels.add(level_key)
        self._breakout_candidates.pop(level_key, None)
        self._signals_sent += 1

        entry = price
        if direction == "SHORT":
            take = round(entry * 0.989, 2)  # -1.1%
            stop = round(entry * 1.003, 2)  # +0.3%
        else:
            take = round(entry * 1.011, 2)  # +1.1%
            stop = round(entry * 0.997, 2)  # -0.3%

        return {
            "type": signal_type,
            "direction": direction,
            "ticker": ticker,
            "entry": entry,
            "take": take,
            "stop": stop,
            "level_price": level_price,
            "reason": reason,
            "atr_remaining": f"{100 - atr_used:.0f}%",
            "time": time_msk.strftime("%H:%M:%S"),
        }

    def get_status(self) -> str:
        """Статус модуля."""
        active = len(self.levels) - len(self.triggered_levels)
        return (
            f"RT Signals: {active} уровней активно, "
            f"{self._signals_sent}/{self.max_signals_per_session} сигналов отправлено"
        )

    def reset(self):
        """Сброс на новый день."""
        self.levels = []
        self.daily_direction = None
        self.triggered_levels = set()
        self._breakout_candidates = {}
        self._signals_sent = 0
