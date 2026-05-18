import os
from dotenv import load_dotenv

load_dotenv()


class Config:
    # Telegram API
    API_ID = int(os.getenv("TELEGRAM_API_ID", "0"))
    API_HASH = os.getenv("TELEGRAM_API_HASH", "")

    # Bot
    BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
    USER_ID = int(os.getenv("TELEGRAM_USER_ID", "0"))

    # Channel
    CHANNEL = os.getenv("TELEGRAM_CHANNEL", "https://t.me/D_LAKTIONOV_LIVE")
    CHANNEL_USERNAME = "D_LAKTIONOV_LIVE"

    # AI (Fireworks.ai / DeepSeek V4 Pro)
    FIREWORKS_API_KEY = os.getenv("FIREWORKS_API_KEY", "")
    FIREWORKS_MODEL = os.getenv(
        "FIREWORKS_MODEL", "accounts/fireworks/models/deepseek-v4-pro"
    )

    # ATR
    ATR_THRESHOLD_PERCENT = float(os.getenv("ATR_THRESHOLD_PERCENT", "1.0"))

    # ═══ TIMING (всё в МСК) ═══
    # Утренний план — за 15 мин до открытия срочки
    PRE_OPEN_HOUR = int(os.getenv("PRE_OPEN_HOUR", "6"))
    PRE_OPEN_MINUTE = int(os.getenv("PRE_OPEN_MINUTE", "45"))

    # Подтверждение первым баром — через 5 мин после открытия
    CONFIRM_DELAY_MINUTES = int(os.getenv("CONFIRM_DELAY_MINUTES", "5"))

    # Открытие срочного рынка (FORTS)
    FORTS_OPEN_HOUR = 7
    FORTS_OPEN_MINUTE = 0

    # Открытие основной сессии (акции)
    MOEX_OPEN_HOUR = 10
    MOEX_OPEN_MINUTE = 0

    # Закрытие
    MOEX_CLOSE_HOUR = 18
    MOEX_CLOSE_MINUTE = 40

    # Крайнее время для новых сделок
    NO_NEW_TRADES_HOUR = 14
    NO_NEW_TRADES_MINUTE = 0

    # Вечерняя сводка
    EVENING_SUMMARY_HOUR = 18
    EVENING_SUMMARY_MINUTE = 45

    # Интервал проверки цен (секунды) — чаще = быстрее реакция
    PRICE_CHECK_INTERVAL = int(os.getenv("PRICE_CHECK_INTERVAL", "30"))

    # Интервал проверки реалтайм сигналов (секунды)
    REALTIME_CHECK_INTERVAL = int(os.getenv("REALTIME_CHECK_INTERVAL", "30"))

    # Устаревший (для обратной совместимости)
    MORNING_CHECK_HOUR = PRE_OPEN_HOUR
    MORNING_CHECK_MINUTE = PRE_OPEN_MINUTE

    # Database
    DB_PATH = os.path.join(os.path.dirname(__file__), "data", "monitor.db")

    # ═══ ТОРГОВЫЕ ПРАВИЛА ═══
    TAKE_PROFIT_PCT = 1.1     # Тейк +1.1%
    STOP_LOSS_PCT = 0.3       # Стоп -0.3%
    MIN_VOLUME_RUB = 300_000_000  # Мин объём 300 млн руб
    MAX_TRADES_PER_DAY = 8
    MIN_ATR_REMAINING_PCT = 30  # Мин остаток ATR для входа

    # Тикеры акций индекса ММВБ (основные)
    MOEX_INDEX_TICKERS = [
        "SBER", "GAZP", "LKOH", "GMKN", "NVTK",
        "ROSN", "YNDX", "MTSS", "MGNT", "ALRS",
        "CHMF", "SNGS", "SNGSP", "TATN", "TATNP",
        "PLZL", "POLY", "VTBR", "MOEX", "PHOR",
        "RUAL", "IRAO", "FEES", "HYDR", "AFLT",
        "PIKK", "OZON", "TCSG", "FIVE", "MAGN",
        "NLMK", "SBRF", "TRNFP", "RTKM", "CBOM",
    ]
