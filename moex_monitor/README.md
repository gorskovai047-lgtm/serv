# MOEX Monitor — Мониторинг ATR и уровней акций ММВБ

Автоматическая система, которая:
1. Парсит утренние посты из Telegram-канала [@D_LAKTIONOV_LIVE](https://t.me/D_LAKTIONOV_LIVE)
2. Извлекает уровни поддержки/сопротивления и ATR по акциям
3. Мониторит дневной ход акций в реальном времени через API Мосбиржи
4. Отправляет сигналы в Telegram когда ATR пройден или цена подходит к уровню

---

## Быстрый старт

### 1. Установка

```bash
cd moex_monitor
pip install -r requirements.txt
```

### 2. Telegram API ключи

Для парсинга канала нужны `api_id` и `api_hash`:

1. Перейди на https://my.telegram.org
2. Авторизуйся через номер телефона
3. "API development tools" → создай приложение
4. Скопируй **App api_id** и **App api_hash**

### 3. Настройка .env

Скопируй `.env.example` в `.env` и заполни:

```bash
cp .env.example .env
```

```env
TELEGRAM_API_ID=12345678
TELEGRAM_API_HASH=abcdef1234...
TELEGRAM_BOT_TOKEN=твой_бот_токен
TELEGRAM_USER_ID=твой_user_id
```

### 4. Запуск

```bash
python main.py
```

При первом запуске Telethon попросит номер телефона и код из Telegram.
После этого создастся сессия и повторная авторизация не нужна.

---

## Расписание (МСК)

| Время | Действие |
|-------|----------|
| 07:15 | Парсинг канала, извлечение уровней и ATR |
| 09:55 | Сброс данных перед новым днём |
| 10:00 – 18:40 | Мониторинг ATR каждые 60 сек |
| 18:45 | Вечерняя сводка |
| 24/7 | Бот слушает команды |

## Логика ATR

- **ATR = 1%** дневного хода от цены открытия (дефолт)
- Если канал указывает другой ATR → используется он
- Когда ход >= ATR → сигнал «пик ATR пройден, потенциал исчерпан»
- Сигнал отправляется **один раз** за день

## Команды бота

| Команда | Описание |
|---------|----------|
| `/status` | Статус ATR всех тикеров |
| `/levels` | Уровни на сегодня |
| `/summary` | Полная сводка |
| `/ticker SBER` | Детали по тикеру |
| `/help` | Справка |

---

## Структура

```
moex_monitor/
├── main.py            # Точка входа, scheduler
├── config.py          # Конфигурация
├── channel_parser.py  # Парсер Telegram канала
├── post_analyzer.py   # Анализ текста: уровни, ATR, тикеры
├── atr_monitor.py     # Мониторинг ATR (ISS MOEX API)
├── notifier.py        # Telegram уведомления
├── requirements.txt
├── .env.example
├── .gitignore
└── data/              # Авто-создаётся
    ├── monitor.db
    ├── photos/
    └── parser_session
```

## Деплой на сервере

### systemd

```ini
[Unit]
Description=MOEX Monitor
After=network.target

[Service]
Type=simple
WorkingDirectory=/path/to/moex_monitor
ExecStart=/usr/bin/python3 main.py
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

### Docker

```dockerfile
FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt
COPY . .
CMD ["python", "main.py"]
```

### screen

```bash
screen -S moex
python main.py
# Ctrl+A, D — отключиться
```

---

## Без парсера канала

Если нет API_ID/API_HASH — система работает только с мониторингом ATR:
- Все 35 тикеров индекса ММВБ
- Сигналы при пробитии 1%
- Команды бота
