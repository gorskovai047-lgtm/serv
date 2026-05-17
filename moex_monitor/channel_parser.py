"""
Парсер Telegram канала D_LAKTIONOV_LIVE.
Собирает текстовые сообщения и фото из канала.
"""

import os
import asyncio
from datetime import datetime, timedelta, timezone
from typing import List, Optional

from telethon import TelegramClient
from telethon.tl.types import MessageMediaPhoto

from config import Config


MSK = timezone(timedelta(hours=3))


class ChannelParser:
    """Парсер публичного Telegram канала."""

    def __init__(self):
        self.client: Optional[TelegramClient] = None
        self.session_path = os.path.join(
            os.path.dirname(__file__), "data", "parser_session"
        )

    async def connect(self):
        """Подключение к Telegram API."""
        os.makedirs(os.path.dirname(self.session_path), exist_ok=True)
        self.client = TelegramClient(
            self.session_path, Config.API_ID, Config.API_HASH
        )
        await self.client.start()
        print("[Parser] Подключено к Telegram API")

    async def disconnect(self):
        """Отключение от Telegram API."""
        if self.client:
            await self.client.disconnect()
            print("[Parser] Отключено от Telegram API")

    async def get_today_morning_posts(self) -> List[dict]:
        """
        Получить утренние посты за сегодня (с 4:00 до 8:00 МСК).
        Именно в это время публикуются уровни и аналитика на день.
        """
        now_msk = datetime.now(MSK)
        morning_start = now_msk.replace(hour=4, minute=0, second=0, microsecond=0)
        morning_end = now_msk.replace(hour=8, minute=0, second=0, microsecond=0)

        return await self._get_posts_in_range(morning_start, morning_end)

    async def get_recent_posts(self, hours: int = 1) -> List[dict]:
        """Получить посты за последние N часов."""
        now_msk = datetime.now(MSK)
        start_time = now_msk - timedelta(hours=hours)

        return await self._get_posts_in_range(start_time, now_msk)

    async def get_posts_by_date(self, date: datetime) -> List[dict]:
        """Получить все посты за конкретную дату."""
        start = date.replace(hour=0, minute=0, second=0, microsecond=0, tzinfo=MSK)
        end = date.replace(hour=23, minute=59, second=59, microsecond=0, tzinfo=MSK)

        return await self._get_posts_in_range(start, end)

    async def _get_posts_in_range(
        self, start: datetime, end: datetime
    ) -> List[dict]:
        """
        Получить посты из канала в заданном временном диапазоне.

        Returns:
            Список словарей с полями:
            - id: int — ID сообщения
            - date: datetime — дата сообщения (MSK)
            - text: str — текст сообщения
            - has_photo: bool — есть ли фото
            - photo_path: str | None — путь к скачанному фото
        """
        if not self.client:
            raise RuntimeError("Client not connected. Call connect() first.")

        posts = []
        channel = await self.client.get_entity(Config.CHANNEL_USERNAME)

        async for message in self.client.iter_messages(
            channel,
            offset_date=end.astimezone(timezone.utc),
            reverse=False,
        ):
            msg_date_msk = message.date.astimezone(MSK)

            if msg_date_msk > end:
                continue

            if msg_date_msk < start:
                break

            post = {
                "id": message.id,
                "date": msg_date_msk,
                "text": message.text or "",
                "has_photo": False,
                "photo_path": None,
            }

            # Скачиваем фото если есть
            if message.media and isinstance(message.media, MessageMediaPhoto):
                post["has_photo"] = True
                photo_dir = os.path.join(
                    os.path.dirname(__file__), "data", "photos"
                )
                os.makedirs(photo_dir, exist_ok=True)
                photo_path = os.path.join(
                    photo_dir, f"{message.id}_{msg_date_msk.strftime('%Y%m%d')}.jpg"
                )
                await self.client.download_media(message.media, file=photo_path)
                post["photo_path"] = photo_path

            posts.append(post)

        posts.sort(key=lambda x: x["date"])

        print(
            f"[Parser] Найдено {len(posts)} постов "
            f"({start.strftime('%H:%M')} - {end.strftime('%H:%M')} МСК)"
        )
        return posts

    async def get_last_n_posts(self, n: int = 10) -> List[dict]:
        """Получить последние N постов из канала."""
        if not self.client:
            raise RuntimeError("Client not connected. Call connect() first.")

        posts = []
        channel = await self.client.get_entity(Config.CHANNEL_USERNAME)

        async for message in self.client.iter_messages(channel, limit=n):
            msg_date_msk = message.date.astimezone(MSK)

            post = {
                "id": message.id,
                "date": msg_date_msk,
                "text": message.text or "",
                "has_photo": isinstance(
                    getattr(message, "media", None), MessageMediaPhoto
                ),
                "photo_path": None,
            }
            posts.append(post)

        posts.sort(key=lambda x: x["date"])
        return posts


# Для тестирования
async def main():
    parser = ChannelParser()
    await parser.connect()

    print("\n=== Последние 5 постов ===")
    posts = await parser.get_last_n_posts(5)
    for post in posts:
        print(f"\n[{post['date'].strftime('%d.%m %H:%M')}] (ID: {post['id']})")
        print(f"  Текст: {post['text'][:200]}...")
        print(f"  Фото: {'Да' if post['has_photo'] else 'Нет'}")

    await parser.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
