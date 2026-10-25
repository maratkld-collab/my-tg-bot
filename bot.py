"""Telegram-бот с DeepSeek: полноценный диалог с памятью контекста.

Команды:
    /start  — приветствие и очистка истории
    /reset  — очистка истории диалога
    текст   — запрос к модели deepseek-chat и ответ пользователю

Запуск:
    pip install pyTelegramBotAPI requests
    python bot.py
"""

import os

import requests
import telebot
from telebot import apihelper

# --- Telegram -----------------------------------------------------------------

# Токен лучше хранить в переменной окружения BOT_TOKEN, а не в коде.
TOKEN = os.getenv("BOT_TOKEN", "5325426517:AAECUrEK2Kny1TakJzOLfOUZIPVSsNyYuJs")

# Все методы Bot API идут не на api.telegram.org, а на наш Worker.
apihelper.API_URL = "https://orange-sun-eaa4.maratkld.workers.dev/bot{0}/{1}"

# Тайм-ауты HTTP-запросов (секунды), чтобы медленный Worker не рвал polling.
apihelper.CONNECT_TIMEOUT = 60
apihelper.READ_TIMEOUT = 60

# --- DeepSeek -----------------------------------------------------------------

# Вставьте настоящий ключ или задайте переменную окружения DEEPSEEK_API_KEY.
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "sk-ВСТАВЬТЕ_СЮДА_ВАШ_КЛЮЧ")
if not DEEPSEEK_API_KEY or "ВСТАВЬТЕ" in DEEPSEEK_API_KEY:
    raise SystemExit(
        "Укажите настоящий ключ DeepSeek: замените заглушку в bot.py "
        "или задайте переменную окружения DEEPSEEK_API_KEY."
    )

DEEPSEEK_API_URL = "https://api.deepseek.com/v1/chat/completions"
DEEPSEEK_MODEL = "deepseek-chat"
DEEPSEEK_TIMEOUT = 60

SYSTEM_PROMPT = (
    "Ты дружелюбный и полезный ИИ-ассистент. "
    "Отвечай на языке пользователя, понятно и по делу."
)

MAX_HISTORY = 20            # сколько последних сообщений помнить (10 пар)
MAX_TELEGRAM_LENGTH = 4000  # лимит Telegram — 4096 символов

# --- Погода -------------------------------------------------------------------

WEATHER_URL = (
    "https://api.open-meteo.com/v1/forecast"
    "?latitude=54.71&longitude=20.51&current_weather=true"
)

# ------------------------------------------------------------------------------

bot = telebot.TeleBot(TOKEN)

# История диалога отдельно для каждого чата.
histories: dict[int, list[dict[str, str]]] = {}


def trim_history(history: list[dict[str, str]]) -> None:
    """Оставляет последние MAX_HISTORY сообщений, начиная с реплики пользователя."""
    if len(history) > MAX_HISTORY:
        del history[:-MAX_HISTORY]
    while history and history[0]["role"] != "user":
        history.pop(0)


def ask_deepseek(history: list[dict[str, str]]) -> str:
    """Отправляет диалог в DeepSeek и возвращает ответ модели."""
    response = requests.post(
        DEEPSEEK_API_URL,
        headers={
            "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
            "Content-Type": "application/json",
        },
        json={
            "model": DEEPSEEK_MODEL,
            "messages": [{"role": "system", "content": SYSTEM_PROMPT}, *history],
            "temperature": 0.7,
            "max_tokens": 1000,
            "stream": False,
        },
        timeout=DEEPSEEK_TIMEOUT,
    )
    response.raise_for_status()

    data = response.json()
    answer = data["choices"][0]["message"]["content"].strip()
    if not answer:
        raise RuntimeError("DeepSeek вернул пустой ответ")
    return answer


def send_answer(message: telebot.types.Message, text: str) -> None:
    """Отправляет ответ, разбивая его на части под лимит Telegram."""
    for start in range(0, len(text), MAX_TELEGRAM_LENGTH):
        chunk = text[start:start + MAX_TELEGRAM_LENGTH]
        if start == 0:
            bot.reply_to(message, chunk)
        else:
            bot.send_message(message.chat.id, chunk)


def is_weather_request(text: str) -> bool:
    """Проверяет, просит ли пользователь погоду в Калининграде."""
    normalized = " ".join(text.lower().replace("ё", "е").split())
    normalized = normalized.rstrip("?!.,")
    return normalized in {"погода", "погода в калининграде"}


@bot.message_handler(
    content_types=["text"],
    func=lambda message: is_weather_request(message.text or ""),
)
def weather(message: telebot.types.Message) -> None:
    """Отправляет пользователю текущую погоду в Калининграде."""
    try:
        bot.send_chat_action(message.chat.id, "typing")
    except Exception:
        pass

    print(f"[weather] запрос: {WEATHER_URL} (timeout=10)")

    response = None
    try:
        response = requests.get(
            WEATHER_URL,
            timeout=10,
            headers={
                "User-Agent": "telegram-weather-bot/1.0",
                "Accept": "application/json",
            },
        )
        # Open-Meteo отдаёт JSON в UTF-8; фиксируем кодировку явно.
        response.encoding = "utf-8"

        print(
            f"[weather] HTTP {response.status_code}, "
            f"Content-Type: {response.headers.get('Content-Type')!r}"
        )

        response.raise_for_status()
        print(f"[weather] тело ответа: {response.text[:300]!r}")

    except requests.Timeout as exc:
        print(f"[weather] тайм-аут 10 секунд: {exc!r}")
        bot.reply_to(
            message,
            "Сервис погоды не ответил за 10 секунд. Попробуй ещё раз.",
        )
        return
    except requests.HTTPError as exc:
        body = response.text[:300] if response is not None else ""
        print(f"[weather] HTTP-ошибка: {exc!r}; тело: {body!r}")
        bot.reply_to(
            message,
            "Сервис погоды вернул ошибку. Попробуй позже.",
        )
        return
    except requests.RequestException as exc:
        print(f"[weather] сетевая ошибка: {exc!r}")
        bot.reply_to(
            message,
            "Не удалось связаться с сервисом погоды. Попробуй позже.",
        )
        return
    except Exception as exc:
        print(f"[weather] неожиданная ошибка: {exc!r}")
        bot.reply_to(
            message,
            "Не получилось узнать погоду. Попробуй позже.",
        )
        return

    try:
        data = response.json()
        temperature = float(data["current_weather"]["temperature"])
        forecast = f"Сейчас в Калининграде: {temperature:+.1f}°C"
        print(f"[weather] температура: {temperature:+.1f}°C")
    except (ValueError, KeyError, TypeError) as exc:
        print(f"[weather] ошибка разбора ответа: {exc!r}")
        bot.reply_to(
            message,
            "Сервис погоды вернул неожиданный ответ. Попробуй позже.",
        )
        return

    bot.reply_to(message, forecast)


@bot.message_handler(commands=["start"])
def send_welcome(message: telebot.types.Message) -> None:
    """Приветствует пользователя и начинает диалог с чистого листа."""
    histories.pop(message.chat.id, None)
    name = message.from_user.first_name if message.from_user else "друг"
    bot.reply_to(
        message,
        f"Привет, {name}! Рад тебя видеть. "
        "Напиши мне что-нибудь, и я отвечу",
    )


@bot.message_handler(commands=["reset"])
def reset_dialog(message: telebot.types.Message) -> None:
    """Очищает историю диалога в этом чате."""
    histories.pop(message.chat.id, None)
    bot.reply_to(message, "Контекст диалога очищен. Начнём заново!")


@bot.message_handler(content_types=["text"])
def chat(message: telebot.types.Message) -> None:
    """Передаёт сообщение пользователя в DeepSeek и отвечает от имени модели."""
    if is_weather_request(message.text):
        return
    if message.text.startswith("/"):
        return

    chat_id = message.chat.id
    history = histories.setdefault(chat_id, [])
    history.append({"role": "user", "content": message.text})
    trim_history(history)

    try:
        bot.send_chat_action(chat_id, "typing")
    except Exception:
        pass

    try:
        answer = ask_deepseek(history)
    except Exception as exc:
        # Не оставляем в истории сообщение, на которое не ответили.
        history.pop()
        print(f"Ошибка DeepSeek: {exc}")
        bot.reply_to(
            message,
            "Не получилось получить ответ. Попробуй ещё раз чуть позже.",
        )
        return

    history.append({"role": "assistant", "content": answer})
    trim_history(history)
    send_answer(message, answer)


if __name__ == "__main__":
    print("Бот запущен. Модель: deepseek-chat")
    print("Для остановки нажмите Ctrl+C.")
    bot.infinity_polling(timeout=60)
