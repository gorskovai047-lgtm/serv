"""
Парсер канала Лактионова через веб-превью Telegram.
НЕ требует api_id/api_hash! Работает через https://t.me/s/CHANNEL

Логика:
1. Парсит публичную веб-версию канала (t.me/s/D_LAKTIONOV_LIVE)
2. Извлекает текст постов с датами
3. Фильтрует по времени (утренние посты, последние N часов)
4. Возвращает структурированные данные для анализа
"""

import re
import urllib.request
from datetime import datetime, timedelta, timezone
from typing import List, Dict, Optional
from html import unescape

from config import Config

MSK = timezone(timedelta(hours=3))

CHANNEL_WEB_URL = f"https://t.me/s/{Config.CHANNEL_USERNAME}"


class ChannelParser:
    """Парсер канала через веб-превью (без Telethon, без api_id)."""

    def __init__(self):
        self._last_post_id: int = 0
        self._connected = False

    async def connect(self):
        """Имитация подключения (для совместимости с main.py)."""
        # Проверяем доступность канала
        try:
            url = CHANNEL_WEB_URL
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            resp = urllib.request.urlopen(req, timeout=10)
            if resp.status == 200:
                self._connected = True
                print(f"[Parser] Веб-парсер подключён к @{Config.CHANNEL_USERNAME}")
            else:
                raise Exception(f"HTTP {resp.status}")
        except Exception as e:
            self._connected = False
            raise Exception(f"Канал недоступен: {e}")

    async def disconnect(self):
        """Отключение (для совместимости)."""
        self._connected = False

    async def get_today_morning_posts(self) -> List[Dict]:
        """
        Получает утренние посты за сегодня (04:00-07:00 МСК).
        Это основной метод для утреннего анализа.
        """
        now = datetime.now(MSK)
        today_str = now.strftime("%Y-%m-%d")

        # Парсим последнюю страницу канала
        posts = self._fetch_posts_from_web()

        # Фильтруем: только сегодня, только утро (до 07:30)
        morning_posts = []
        for post in posts:
            post_date = post["date"]
            if post_date.strftime("%Y-%m-%d") != today_str:
                continue
            hour = post_date.hour
            if hour < 8:  # До 08:00 МСК = утренние посты
                morning_posts.append(post)

        if not morning_posts:
            # Может Лактионов ещё не опубликовал — берём последние за ночь/вечер
            # (он иногда публикует план в 04:00-05:00)
            for post in posts:
                post_date = post["date"]
                if post_date.strftime("%Y-%m-%d") == today_str:
                    morning_posts.append(post)

        return morning_posts

    async def get_recent_posts(self, hours: int = 6) -> List[Dict]:
        """
        Получает посты за последние N часов.
        Используется если утренних постов ещё нет.
        """
        now = datetime.now(MSK)
        cutoff = now - timedelta(hours=hours)

        posts = self._fetch_posts_from_web()

        recent = [p for p in posts if p["date"] >= cutoff]
        return recent

    def _fetch_posts_from_web(self, before_id: Optional[int] = None) -> List[Dict]:
        """
        Парсит посты из веб-версии канала.
        Возвращает список dict: {id, date, text}
        """
        try:
            if before_id:
                url = f"{CHANNEL_WEB_URL}?before={before_id}"
            else:
                url = CHANNEL_WEB_URL

            req = urllib.request.Request(url, headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
            })
            resp = urllib.request.urlopen(req, timeout=15)
            html = resp.read().decode("utf-8")

            return self._parse_html(html)

        except Exception as e:
            print(f"[Parser] Ошибка загрузки: {e}")
            return []

    def _parse_html(self, html: str) -> List[Dict]:
        """Парсит HTML страницы канала и извлекает посты."""
        posts = []

        blocks = re.split(r'tgme_widget_message_wrap', html)

        for block in blocks:
            # Извлекаем дату
            date_match = re.search(r'datetime="(.*?)"', block)
            # Извлекаем ID поста
            post_match = re.search(
                r'data-post="' + re.escape(Config.CHANNEL_USERNAME) + r'/(\d+)"', block
            )
            # Извлекаем текст
            text_match = re.search(
                r'<div class="tgme_widget_message_text.*?">(.*?)</div>', block, re.DOTALL
            )

            if date_match and post_match and text_match:
                # Парсим дату (UTC) → МСК
                date_str = date_match.group(1)
                try:
                    # Формат: 2026-05-18T07:22:25+00:00
                    dt_utc = datetime.fromisoformat(date_str.replace("+00:00", "+00:00"))
                    if dt_utc.tzinfo is None:
                        dt_utc = dt_utc.replace(tzinfo=timezone.utc)
                    dt_msk = dt_utc.astimezone(MSK)
                except Exception:
                    # Fallback: ручной парсинг
                    try:
                        dt_utc = datetime.strptime(date_str[:19], "%Y-%m-%dT%H:%M:%S")
                        dt_utc = dt_utc.replace(tzinfo=timezone.utc)
                        dt_msk = dt_utc.astimezone(MSK)
                    except Exception:
                        continue

                # Парсим текст
                raw_text = text_match.group(1)
                # <br> → перенос строки
                text = re.sub(r'<br\s*/?>', '\n', raw_text)
                # Убираем HTML теги
                text = re.sub(r'<[^>]+>', '', text)
                # Декодируем HTML entities
                text = unescape(text).strip()

                if len(text) < 10:
                    continue  # Пропускаем слишком короткие

                post_id = int(post_match.group(1))

                posts.append({
                    "id": post_id,
                    "date": dt_msk,
                    "text": text,
                })

                # Запоминаем последний ID
                if post_id > self._last_post_id:
                    self._last_post_id = post_id

        # Сортируем по дате
        posts.sort(key=lambda x: x["date"])
        return posts

    async def get_posts_for_date(self, target_date: str) -> List[Dict]:
        """
        Получает все посты за конкретную дату (формат: YYYY-MM-DD).
        Ищет через пагинацию если нужно.
        """
        all_posts = []
        before_id = None
        max_pages = 5  # Максимум 5 страниц назад

        for _ in range(max_pages):
            posts = self._fetch_posts_from_web(before_id)
            if not posts:
                break

            for post in posts:
                if post["date"].strftime("%Y-%m-%d") == target_date:
                    all_posts.append(post)

            # Проверяем нужно ли листать дальше
            earliest_date = min(p["date"] for p in posts).strftime("%Y-%m-%d")
            if earliest_date < target_date:
                break  # Уже прошли нужную дату

            # Листаем назад
            before_id = min(p["id"] for p in posts)

        all_posts.sort(key=lambda x: x["date"])
        return all_posts

    async def check_new_posts(self) -> List[Dict]:
        """
        Проверяет новые посты (после последнего известного ID).
        Используется для реалтайм мониторинга канала.
        """
        posts = self._fetch_posts_from_web()
        new_posts = [p for p in posts if p["id"] > self._last_post_id]

        if new_posts:
            self._last_post_id = max(p["id"] for p in new_posts)

        return new_posts
