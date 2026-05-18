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

    # Schedule
    MORNING_CHECK_HOUR = int(os.getenv("MORNING_CHECK_HOUR", "7"))
    MORNING_CHECK_MINUTE = int(os.getenv("MORNING_CHECK_MINUTE", "15"))
    PRICE_CHECK_INTERVAL = int(os.getenv("PRICE_CHECK_INTERVAL", "60"))

    # MOEX trading hours (MSK)
    MOEX_OPEN_HOUR = 10
    MOEX_OPEN_MINUTE = 0
    MOEX_CLOSE_HOUR = 18
    MOEX_CLOSE_MINUTE = 40

    # Database
    DB_PATH = os.path.join(os.path.dirname(__file__), "data", "monitor.db")

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
