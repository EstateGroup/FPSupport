from __future__ import annotations
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from fpsupport import funpayautobot as Cardinal

import os
import json
import logging
import re
import requests
import threading
import queue
import concurrent.futures
from telebot import types
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
from FunPayAPI.updater.events import NewOrderEvent, NewMessageEvent
import time
import uuid
import hashlib
import traceback

try:
    import pymysql
except ImportError:
    pass

NAME = "Auto TG"
VERSION = "1.8.9"
DESCRIPTION = "Плагин для автовыдачи телеграмм номеров с использованием LOLZ"
CREDITS = "@LCNPrime"
UUID = "e7f3a9b1-5d8c-4f2e-9b6a-1c0d3e5f7a2b"
SETTINGS_PAGE = False

LOGGER_PREFIX = "[TELEGRAM_ACCOUNTS]"
logger = logging.getLogger("FPC.telegramaccounts")

CONFIG_DIR = "storage/tg"
CONFIG_PATH = f"{CONFIG_DIR}/config.json"
USER_ORDERS_PATH = f"{CONFIG_DIR}/user_orders.json"

DEFAULT_PURCHASE_TEMPLATE = """💸 БЛАГОДАРИМ ЗА ОПЛАТУ! 💸
⚠️ Перед получением кода:
✅ Включите VPN той страны которую вы купили (только при 1 входе)
✅ Запустите запись экрана (чтобы в случае бана мы могли сделать замену)
📌 Важные правила:
🔸 Не меняйте почту в первые 24 часа!
🔸 Не включайте 2FA в первые 24 часа!

📳 Данные для входа:
Телефон: {phone}

📨 Чтобы получить код подтверждения:
👉 Отправьте в этот чат:
{cd_commands}"""

DEFAULT_CODE_TEMPLATE = """🍀 Ваш код для входа в Telegram: {code}

💚 - Спасибо за покупку, не забудь подтвердить заказ: {order_link}

☀️ - Также если не сложно оставьте пожалуйста отзыв.

Заказ выполнен. Пожалуйста, зайдите в раздел «Покупки», выберите его в списке и нажмите кнопку «Подтвердить выполнение заказа».
"""

used_orders = {}
order_account_ids = {}
order_phone_numbers = {}
order_queue = queue.Queue()
executor = None
max_workers = 5
active_tasks = 0
max_concurrent_tasks = 3
task_lock = threading.Lock()
is_processing = False

ORIGIN_MAP = {
    "phishing": "Фишинг(ОПАСНО!)",
    "stealer": "Стилер(ОПАСНО!)",
    "personal": "Личный(ОТЛИЧНО!)",
    "resale": "Перепродажа(50/50)",
    "autoreg": "Авторег(ХОРОШО)",
    "samoreg": "Саморег(ХОРОШО)"
}

bot = None
cardinal_instance = None
config = {}

# === Глобальная очередь зависших заказов ===
pending_orders = []


def show_tg_settings(message: types.Message):
    kb = InlineKeyboardMarkup(row_width=2)
    kb.row(
        InlineKeyboardButton("🌍 Страны", callback_data="tg_countries"),
        InlineKeyboardButton("👥 Админы", callback_data="tg_admins")
    )
    kb.row(
        InlineKeyboardButton("🔑 LOLZ Token", callback_data="tg_lolz_token"),
        InlineKeyboardButton("💬 Шаблоны", callback_data="tg_message_templates")
    )
    kb.row(
        InlineKeyboardButton("📋 Заказы", callback_data="tg_orders"),
        InlineKeyboardButton("🔍 Происхождение", callback_data="tg_origin")
    )
    kb.row(
        InlineKeyboardButton("⚙️ Настройки", callback_data="tg_settings_menu"),
        InlineKeyboardButton("🚫 Блок", callback_data="tg_blocked_users")
    )
    kb.add(InlineKeyboardButton("🔄 Замена", callback_data="tg_replace_issue"))

    countries_count = len(config["countries"])
    admins_count = len(config["administrators"])
    blocked_count = len(config.get("blocked_users", []))

    # Получаем общую прибыль
    total_profit = get_total_profit()

    # Получаем информацию о LOLZ балансе
    lolz_balance = 0
    lolz_balance_status = ""
    if config["lolz_token"]:
        try:
            username, balance = get_lolz_user_info(config["lolz_token"])
            if username and balance is not None:
                lolz_balance = balance
                lolz_balance_status = "(нормальный)" if balance >= 400 else "(низкий!)"
        except Exception as e:
            logger.error(f"{LOGGER_PREFIX} Ошибка при получении баланса LOLZ: {e}")

    lolz_info = ""
    if config["lolz_token"]:
        try:
            username, balance = get_lolz_user_info(config["lolz_token"])
            if username:
                lolz_info = f"\n\n💰 Баланс LOLZ: {balance:.2f}₽\n👤 Пользователь: {username}"
        except Exception as e:
            logger.error(f"{LOGGER_PREFIX} Ошибка при получении баланса LOLZ: {e}")

    message_text = (
        f"🤖 <b>{NAME}</b> <code>v{VERSION}</code>\n\n"
        f"📝 <b>Описание:</b> {DESCRIPTION}\n"
        f"👨‍💻 <b>Автор:</b>@LCNPrime{lolz_info}\n\n"
        f"📊 <b>СТАТИСТИКА</b>\n"
        f"🌍 Стран: <b>{countries_count}</b>   👥 Админов: <b>{admins_count}</b>\n"
        f"💰 Прибыль: <b>{total_profit:.2f}₽</b>\n"
        f"💎 LOLZ: <b>{lolz_balance:.2f}₽</b> {lolz_balance_status}\n"
        f"🚫 Блок: <b>{blocked_count}</b>\n\n"
        f"⚙️ <b>Настройка плагина:</b>"
    )

    bot.send_message(message.chat.id, message_text, reply_markup=kb, parse_mode="HTML")


def show_tg_settings_callback(call: types.CallbackQuery):
    bot.clear_step_handler_by_chat_id(call.message.chat.id)

    kb = InlineKeyboardMarkup(row_width=2)
    kb.row(
        InlineKeyboardButton("🌍 Страны", callback_data="tg_countries"),
        InlineKeyboardButton("👥 Админы", callback_data="tg_admins")
    )
    kb.row(
        InlineKeyboardButton("🔑 LOLZ Token", callback_data="tg_lolz_token"),
        InlineKeyboardButton("💬 Шаблоны", callback_data="tg_message_templates")
    )
    kb.row(
        InlineKeyboardButton("📋 Заказы", callback_data="tg_orders"),
        InlineKeyboardButton("🔍 Происхождение", callback_data="tg_origin")
    )
    kb.row(
        InlineKeyboardButton("⚙️ Настройки", callback_data="tg_settings_menu"),
        InlineKeyboardButton("🚫 Блок", callback_data="tg_blocked_users")
    )
    kb.add(InlineKeyboardButton("🔄 Замена", callback_data="tg_replace_issue"))

    countries_count = len(config["countries"])
    admins_count = len(config["administrators"])
    blocked_count = len(config.get("blocked_users", []))

    # Получаем общую прибыль
    total_profit = get_total_profit()

    # Получаем информацию о LOLZ балансе
    lolz_balance = 0
    lolz_balance_status = ""
    if config["lolz_token"]:
        try:
            username, balance = get_lolz_user_info(config["lolz_token"])
            if username and balance is not None:
                lolz_balance = balance
                lolz_balance_status = "(нормальный)" if balance >= 400 else "(низкий!)"
        except Exception as e:
            logger.error(f"{LOGGER_PREFIX} Ошибка при получении баланса LOLZ: {e}")

    lolz_info = ""
    if config["lolz_token"]:
        try:
            username, balance = get_lolz_user_info(config["lolz_token"])
            if username:
                lolz_info = f"\n\n💰 Баланс LOLZ: {balance:.2f}₽\n👤 Пользователь: {username}"
        except Exception as e:
            logger.error(f"{LOGGER_PREFIX} Ошибка при получении баланса LOLZ: {e}")

    message_text = (
        f"🤖 <b>{NAME}</b> <code>v{VERSION}</code>\n\n"
        f"📝 <b>Описание:</b> {DESCRIPTION}\n"
        f"👨‍💻 <b>Автор:</b>@LCNPrime{lolz_info}\n\n"
        f"📊 <b>СТАТИСТИКА</b>\n"
        f"🌍 Стран: <b>{countries_count}</b>   👥 Админов: <b>{admins_count}</b>\n"
        f"💰 Прибыль: <b>{total_profit:.2f}₽</b>\n"
        f"💎 LOLZ: <b>{lolz_balance:.2f}₽</b> {lolz_balance_status}\n"
        f"🚫 Блок: <b>{blocked_count}</b>\n\n"
        f"⚙️ <b>Настройка плагина:</b>"
    )

    bot.edit_message_text(
        message_text,
        call.message.chat.id,
        call.message.message_id,
        reply_markup=kb,
        parse_mode="HTML"
    )


def get_default_countries():
    """Возвращает словарь со всеми странами по умолчанию"""
    return {
        "RU": {"name": "Россия", "min_price": 1, "max_price": 200},
        "US": {"name": "США", "min_price": 1, "max_price": 200},
        "GB": {"name": "Великобритания", "min_price": 1, "max_price": 200},
        "DE": {"name": "Германия", "min_price": 1, "max_price": 200},
        "FR": {"name": "Франция", "min_price": 1, "max_price": 200},
        "IT": {"name": "Италия", "min_price": 1, "max_price": 200},
        "ES": {"name": "Испания", "min_price": 1, "max_price": 200},
        "PL": {"name": "Польша", "min_price": 1, "max_price": 200},
        "UA": {"name": "Украина", "min_price": 1, "max_price": 200},
        "BY": {"name": "Беларусь", "min_price": 1, "max_price": 200},
        "KZ": {"name": "Казахстан", "min_price": 1, "max_price": 200},
        "UZ": {"name": "Узбекистан", "min_price": 1, "max_price": 200},
        "KG": {"name": "Кыргызстан", "min_price": 1, "max_price": 200},
        "TJ": {"name": "Таджикистан", "min_price": 1, "max_price": 200},
        "TM": {"name": "Туркменистан", "min_price": 1, "max_price": 200},
        "AZ": {"name": "Азербайджан", "min_price": 1, "max_price": 200},
        "AM": {"name": "Армения", "min_price": 1, "max_price": 200},
        "GE": {"name": "Грузия", "min_price": 1, "max_price": 200},
        "MD": {"name": "Молдова", "min_price": 1, "max_price": 200},
        "EE": {"name": "Эстония", "min_price": 1, "max_price": 200},
        "LV": {"name": "Латвия", "min_price": 1, "max_price": 200},
        "LT": {"name": "Литва", "min_price": 1, "max_price": 200},
        "CZ": {"name": "Чехия", "min_price": 1, "max_price": 200},
        "SK": {"name": "Словакия", "min_price": 1, "max_price": 200},
        "HU": {"name": "Венгрия", "min_price": 1, "max_price": 200},
        "RO": {"name": "Румыния", "min_price": 1, "max_price": 200},
        "BG": {"name": "Болгария", "min_price": 1, "max_price": 200},
        "HR": {"name": "Хорватия", "min_price": 1, "max_price": 200},
        "SI": {"name": "Словения", "min_price": 1, "max_price": 200},
        "AT": {"name": "Австрия", "min_price": 1, "max_price": 200},
        "CH": {"name": "Швейцария", "min_price": 1, "max_price": 200},
        "NL": {"name": "Нидерланды", "min_price": 1, "max_price": 200},
        "BE": {"name": "Бельгия", "min_price": 1, "max_price": 200},
        "LU": {"name": "Люксембург", "min_price": 1, "max_price": 200},
        "IE": {"name": "Ирландия", "min_price": 1, "max_price": 200},
        "DK": {"name": "Дания", "min_price": 1, "max_price": 200},
        "NO": {"name": "Норвегия", "min_price": 1, "max_price": 200},
        "SE": {"name": "Швеция", "min_price": 1, "max_price": 200},
        "FI": {"name": "Финляндия", "min_price": 1, "max_price": 200},
        "IS": {"name": "Исландия", "min_price": 1, "max_price": 200},
        "CA": {"name": "Канада", "min_price": 1, "max_price": 200},
        "MX": {"name": "Мексика", "min_price": 1, "max_price": 200},
        "BR": {"name": "Бразилия", "min_price": 1, "max_price": 200},
        "AR": {"name": "Аргентина", "min_price": 1, "max_price": 200},
        "CL": {"name": "Чили", "min_price": 1, "max_price": 200},
        "CO": {"name": "Колумбия", "min_price": 1, "max_price": 200},
        "PE": {"name": "Перу", "min_price": 1, "max_price": 200},
        "VE": {"name": "Венесуэла", "min_price": 1, "max_price": 200},
        "EC": {"name": "Эквадор", "min_price": 1, "max_price": 200},
        "UY": {"name": "Уругвай", "min_price": 1, "max_price": 200},
        "PY": {"name": "Парагвай", "min_price": 1, "max_price": 200},
        "BO": {"name": "Боливия", "min_price": 1, "max_price": 200},
        "GY": {"name": "Гайана", "min_price": 1, "max_price": 200},
        "SR": {"name": "Суринам", "min_price": 1, "max_price": 200},
        "GF": {"name": "Французская Гвиана", "min_price": 1, "max_price": 200},
        "FK": {"name": "Фолклендские острова", "min_price": 1, "max_price": 200},
        "AU": {"name": "Австралия", "min_price": 1, "max_price": 200},
        "NZ": {"name": "Новая Зеландия", "min_price": 1, "max_price": 200},
        "FJ": {"name": "Фиджи", "min_price": 1, "max_price": 200},
        "PG": {"name": "Папуа-Новая Гвинея", "min_price": 1, "max_price": 200},
        "NC": {"name": "Новая Каледония", "min_price": 1, "max_price": 200},
        "VU": {"name": "Вануату", "min_price": 1, "max_price": 200},
        "SB": {"name": "Соломоновы Острова", "min_price": 1, "max_price": 200},
        "TO": {"name": "Тонга", "min_price": 1, "max_price": 200},
        "WS": {"name": "Самоа", "min_price": 1, "max_price": 200},
        "KI": {"name": "Кирибати", "min_price": 1, "max_price": 200},
        "TV": {"name": "Тувалу", "min_price": 1, "max_price": 200},
        "NR": {"name": "Науру", "min_price": 1, "max_price": 200},
        "PW": {"name": "Палау", "min_price": 1, "max_price": 200},
        "MH": {"name": "Маршалловы Острова", "min_price": 1, "max_price": 200},
        "FM": {"name": "Микронезия", "min_price": 1, "max_price": 200},
        "JP": {"name": "Япония", "min_price": 1, "max_price": 200},
        "KR": {"name": "Южная Корея", "min_price": 1, "max_price": 200},
        "CN": {"name": "Китай", "min_price": 1, "max_price": 200},
        "TW": {"name": "Тайвань", "min_price": 1, "max_price": 200},
        "HK": {"name": "Гонконг", "min_price": 1, "max_price": 200},
        "MO": {"name": "Макао", "min_price": 1, "max_price": 200},
        "SG": {"name": "Сингапур", "min_price": 1, "max_price": 200},
        "MY": {"name": "Малайзия", "min_price": 1, "max_price": 200},
        "TH": {"name": "Таиланд", "min_price": 1, "max_price": 200},
        "VN": {"name": "Вьетнам", "min_price": 1, "max_price": 200},
        "PH": {"name": "Филиппины", "min_price": 1, "max_price": 200},
        "ID": {"name": "Индонезия", "min_price": 1, "max_price": 200},
        "MM": {"name": "Мьянма", "min_price": 1, "max_price": 200},
        "LA": {"name": "Лаос", "min_price": 1, "max_price": 200},
        "KH": {"name": "Камбоджа", "min_price": 1, "max_price": 200},
        "BN": {"name": "Бруней", "min_price": 1, "max_price": 200},
        "TL": {"name": "Восточный Тимор", "min_price": 1, "max_price": 200},
        "IN": {"name": "Индия", "min_price": 1, "max_price": 200},
        "PK": {"name": "Пакистан", "min_price": 1, "max_price": 200},
        "BD": {"name": "Бангладеш", "min_price": 1, "max_price": 200},
        "LK": {"name": "Шри-Ланка", "min_price": 1, "max_price": 200},
        "NP": {"name": "Непал", "min_price": 1, "max_price": 200},
        "BT": {"name": "Бутан", "min_price": 1, "max_price": 200},
        "MV": {"name": "Мальдивы", "min_price": 1, "max_price": 200},
        "AF": {"name": "Афганистан", "min_price": 1, "max_price": 200},
        "IR": {"name": "Иран", "min_price": 1, "max_price": 200},
        "IQ": {"name": "Ирак", "min_price": 1, "max_price": 200},
        "SY": {"name": "Сирия", "min_price": 1, "max_price": 200},
        "LB": {"name": "Ливан", "min_price": 1, "max_price": 200},
        "JO": {"name": "Иордания", "min_price": 1, "max_price": 200},
        "IL": {"name": "Израиль", "min_price": 1, "max_price": 200},
        "PS": {"name": "Палестина", "min_price": 1, "max_price": 200},
        "SA": {"name": "Саудовская Аравия", "min_price": 1, "max_price": 200},
        "YE": {"name": "Йемен", "min_price": 1, "max_price": 200},
        "OM": {"name": "Оман", "min_price": 1, "max_price": 200},
        "AE": {"name": "ОАЭ", "min_price": 1, "max_price": 200},
        "QA": {"name": "Катар", "min_price": 1, "max_price": 200},
        "BH": {"name": "Бахрейн", "min_price": 1, "max_price": 200},
        "KW": {"name": "Кувейт", "min_price": 1, "max_price": 200},
        "TR": {"name": "Турция", "min_price": 1, "max_price": 200},
        "CY": {"name": "Кипр", "min_price": 1, "max_price": 200},
        "GR": {"name": "Греция", "min_price": 1, "max_price": 200},
        "MT": {"name": "Мальта", "min_price": 1, "max_price": 200},
        "PT": {"name": "Португалия", "min_price": 1, "max_price": 200},
        "ZA": {"name": "ЮАР", "min_price": 1, "max_price": 200},
        "EG": {"name": "Египет", "min_price": 1, "max_price": 200},
        "LY": {"name": "Ливия", "min_price": 1, "max_price": 200},
        "TN": {"name": "Тунис", "min_price": 1, "max_price": 200},
        "DZ": {"name": "Алжир", "min_price": 1, "max_price": 200},
        "MA": {"name": "Марокко", "min_price": 1, "max_price": 200},
        "EH": {"name": "Западная Сахара", "min_price": 1, "max_price": 200},
        "MR": {"name": "Мавритания", "min_price": 1, "max_price": 200},
        "ML": {"name": "Мали", "min_price": 1, "max_price": 200},
        "BF": {"name": "Буркина-Фасо", "min_price": 1, "max_price": 200},
        "NE": {"name": "Нигер", "min_price": 1, "max_price": 200},
        "TD": {"name": "Чад", "min_price": 1, "max_price": 200},
        "SD": {"name": "Судан", "min_price": 1, "max_price": 200},
        "SS": {"name": "Южный Судан", "min_price": 1, "max_price": 200},
        "ET": {"name": "Эфиопия", "min_price": 1, "max_price": 200},
        "ER": {"name": "Эритрея", "min_price": 1, "max_price": 200},
        "DJ": {"name": "Джибути", "min_price": 1, "max_price": 200},
        "SO": {"name": "Сомали", "min_price": 1, "max_price": 200},
        "KE": {"name": "Кения", "min_price": 1, "max_price": 200},
        "TZ": {"name": "Танзания", "min_price": 1, "max_price": 200},
        "UG": {"name": "Уганда", "min_price": 1, "max_price": 200},
        "RW": {"name": "Руанда", "min_price": 1, "max_price": 200},
        "BI": {"name": "Бурунди", "min_price": 1, "max_price": 200},
        "MZ": {"name": "Мозамбик", "min_price": 1, "max_price": 200},
        "ZW": {"name": "Зимбабве", "min_price": 1, "max_price": 200},
        "ZM": {"name": "Замбия", "min_price": 1, "max_price": 200},
        "MW": {"name": "Малави", "min_price": 1, "max_price": 200},
        "BW": {"name": "Ботсвана", "min_price": 1, "max_price": 200},
        "NA": {"name": "Намибия", "min_price": 1, "max_price": 200},
        "LS": {"name": "Лесото", "min_price": 1, "max_price": 200},
        "SZ": {"name": "Эсватини", "min_price": 1, "max_price": 200},
        "MG": {"name": "Мадагаскар", "min_price": 1, "max_price": 200},
        "MU": {"name": "Маврикий", "min_price": 1, "max_price": 200},
        "SC": {"name": "Сейшелы", "min_price": 1, "max_price": 200},
        "KM": {"name": "Коморские острова", "min_price": 1, "max_price": 200},
        "RE": {"name": "Реюньон", "min_price": 1, "max_price": 200},
        "YT": {"name": "Майотта", "min_price": 1, "max_price": 200},
        "ST": {"name": "Сан-Томе и Принсипи", "min_price": 1, "max_price": 200},
        "CV": {"name": "Кабо-Верде", "min_price": 1, "max_price": 200},
        "GW": {"name": "Гвинея-Бисау", "min_price": 1, "max_price": 200},
        "GN": {"name": "Гвинея", "min_price": 1, "max_price": 200},
        "SL": {"name": "Сьерра-Леоне", "min_price": 1, "max_price": 200},
        "LR": {"name": "Либерия", "min_price": 1, "max_price": 200},
        "CI": {"name": "Кот-д'Ивуар", "min_price": 1, "max_price": 200},
        "GH": {"name": "Гана", "min_price": 1, "max_price": 200},
        "TG": {"name": "Того", "min_price": 1, "max_price": 200},
        "BJ": {"name": "Бенин", "min_price": 1, "max_price": 200},
        "NG": {"name": "Нигерия", "min_price": 1, "max_price": 200},
        "CM": {"name": "Камерун", "min_price": 1, "max_price": 200},
        "CF": {"name": "ЦАР", "min_price": 1, "max_price": 200},
        "GQ": {"name": "Экваториальная Гвинея", "min_price": 1, "max_price": 200},
        "GA": {"name": "Габон", "min_price": 1, "max_price": 200},
        "CG": {"name": "Республика Конго", "min_price": 1, "max_price": 200},
        "CD": {"name": "ДР Конго", "min_price": 1, "max_price": 200},
        "AO": {"name": "Ангола", "min_price": 1, "max_price": 200},
        "SN": {"name": "Сенегал", "min_price": 1, "max_price": 200},
        "GM": {"name": "Гамбия", "min_price": 1, "max_price": 200}
    }


def ensure_config_exists():
    if not os.path.exists(CONFIG_DIR):
        os.makedirs(CONFIG_DIR)

    if not os.path.exists(CONFIG_PATH):
        default_config = {
            "countries": {},
            "administrators": [],
            "blocked_users": [],
            "auto_returns": True,
            "lolz_token": "",
            "origins": ["autoreg", "samoreg", "personal"],
            "purchase_template": DEFAULT_PURCHASE_TEMPLATE,
            "code_template": DEFAULT_CODE_TEMPLATE,
            "orders_profit": {},
            "buy_cheapest": True,
            "check_accounts": True,
            "low_balance_threshold": 500,
            "buy_geo_spamblock": False,
            "buy_spamblock": False,
            "multi_account_delivery": False,
            "auto_deactivate_tg_lots_on_api_fail": False,
            "notify_api_check": False,
            "notifications": {
                "success": True,
                "error": True,
                "code_request": True,
                "refund": True,
                "low_balance": True
            }
        }
        with open(CONFIG_PATH, 'w', encoding='utf-8') as f:
            json.dump(default_config, f, ensure_ascii=False, indent=4)
        return default_config

    with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
        config_data = json.load(f)

        if "origin" in config_data and "origins" not in config_data:
            config_data["origins"] = [config_data["origin"]]
            del config_data["origin"]

        if "origins" not in config_data:
            config_data["origins"] = ["autoreg", "samoreg", "personal"]

        if "purchase_template" not in config_data:
            config_data["purchase_template"] = DEFAULT_PURCHASE_TEMPLATE

        if "code_template" not in config_data:
            config_data["code_template"] = DEFAULT_CODE_TEMPLATE

        if "orders_profit" not in config_data:
            config_data["orders_profit"] = {}

        if "low_balance_threshold" not in config_data:
            config_data["low_balance_threshold"] = 500

        if "buy_geo_spamblock" not in config_data:
            config_data["buy_geo_spamblock"] = False

        if "buy_spamblock" not in config_data:
            config_data["buy_spamblock"] = False

        if "multi_account_delivery" not in config_data:
            config_data["multi_account_delivery"] = False

        if "auto_deactivate_tg_lots_on_api_fail" not in config_data:
            config_data["auto_deactivate_tg_lots_on_api_fail"] = False
        if "notify_api_check" not in config_data:
            config_data["notify_api_check"] = False

        if "notifications" not in config_data:
            config_data["notifications"] = {
                "success": True,
                "error": True,
                "code_request": True,
                "refund": True,
                "low_balance": True
            }

        if "blocked_users" not in config_data:
            config_data["blocked_users"] = []

        with open(CONFIG_PATH, 'w', encoding='utf-8') as f_write:
            json.dump(config_data, f_write, ensure_ascii=False, indent=4)

        return config_data


def find_available_accounts(country_code, min_price, max_price):
    available_accounts = []

    try:
        timer = threading.Timer(3.0, lambda: None)
        timer.start()
        timer.join()

        url = f"https://api.lzt.market/telegram?order_by=price_to_up&pmin={min_price}&pmax={max_price}"

        for origin in config["origins"]:
            url += f"&origin[]={origin}"

        # Настройки спам-блока
        if config.get("buy_spamblock", False):
            url += "&spam=yes"
        else:
            url += "&spam=no"

        # Настройки гео-спам-блока
        if config.get("buy_geo_spamblock", False):
            url += "&allow_geo_spamblock=true"
        else:
            url += "&allow_geo_spamblock=false"

        url += f"&password=no&country[]={country_code}"

        headers = {
            "accept": "application/json",
            "authorization": f"Bearer {config['lolz_token']}"
        }

        response = requests.get(url, headers=headers)

        if response.status_code == 200:
            response_data = response.json()

            if 'items' in response_data and response_data['items']:
                items = response_data['items']

                if config.get("check_accounts", True):
                    checked_accounts = []
                    for account in items:
                        if check_account(account.get('item_id')):
                            checked_accounts.append(account)
                    available_accounts = checked_accounts
                else:
                    available_accounts = items

    except Exception as e:
        logger.error(f"{LOGGER_PREFIX} Ошибка при поиске аккаунтов: {e}")

    return available_accounts


def check_account(item_id):
    try:
        url = f"https://api.lzt.market/{item_id}/check"
        headers = {
            "accept": "application/json",
            "authorization": f"Bearer {config['lolz_token']}"
        }

        response = requests.get(url, headers=headers)
        if response.status_code == 200:
            result = response.json()
            if result.get('status') == 'ok':
                return True
            else:
                return False
        else:
            return False
    except Exception as e:
        return False


def try_purchase_accounts(accounts):
    insufficient_funds = False

    if config.get("buy_cheapest", True):
        accounts = sorted(accounts, key=lambda x: x.get('price', 0))

    for account in accounts:
        item_id = account.get('item_id')
        price = account.get('price')
        logger.info(f"{LOGGER_PREFIX} Попытка покупки аккаунта ID: {item_id}, цена: {price}₽")

        purchase_result = purchase_account(item_id)

        if purchase_result and 'item' in purchase_result:
            login_data = purchase_result['item'].get('loginData', {})
            login = login_data.get('login', '')
            password = login_data.get('password', '')
            telegram_id = purchase_result['item'].get('telegram_id', '')
            telegram_phone = purchase_result['item'].get('telegram_phone', '')
            telegram_username = purchase_result['item'].get('telegram_username', '')

            account_data = {
                'login': login,
                'password': password,
                'telegram_id': telegram_id,
                'telegram_phone': telegram_phone,
                'telegram_username': telegram_username
            }

            return purchase_result, account_data, insufficient_funds

        elif purchase_result and 'errors' in purchase_result:
            error_msg = ', '.join(purchase_result.get('errors', []))

            for fund_error in ["недостаточно средств", "недостаточно баланса", "Пополнить баланс"]:
                if fund_error.lower() in error_msg.lower():
                    insufficient_funds = True
                    admin_alert = f"💰 ВНИМАНИЕ! Недостаточно средств на балансе LOLZ Market для покупки аккаунта ID {item_id} по цене {price}₽. Пожалуйста, пополните баланс!"
                    notify_admins(admin_alert)
                    return None, None, insufficient_funds

            ignorable_errors = [
                "Аккаунт продан",
                "Произошло более 20 ошибок во время проверки аккаунта",
                "произошло более 20 ошибок",
                "не прошел проверку",
                "уже продан",
                "в данный момент недоступен",
                "retry_request"
            ]

            should_continue = False
            for ignorable_error in ignorable_errors:
                if ignorable_error.lower() in error_msg.lower():
                    should_continue = True
                    break

            if not should_continue:
                break

    return None, None, insufficient_funds


def load_user_orders():
    if not os.path.exists(USER_ORDERS_PATH):
        user_orders_data = {
            "user_orders": {},
            "phone_users": {}
        }
        with open(USER_ORDERS_PATH, 'w', encoding='utf-8') as f:
            json.dump(user_orders_data, f, ensure_ascii=False, indent=4)
        return user_orders_data

    try:
        with open(USER_ORDERS_PATH, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception as e:
        return {"user_orders": {}, "phone_users": {}}


def save_user_orders(data):
    try:
        with open(USER_ORDERS_PATH, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=4)
        return True
    except Exception as e:
        return False


def save_config():
    with open(CONFIG_PATH, 'w', encoding='utf-8') as f:
        json.dump(config, f, ensure_ascii=False, indent=4)


def save_order_profit(order_id, fp_sum, lolz_cost, country_code=None):
    try:
        fp_net = float(fp_sum) * 0.97
        profit = fp_net - float(lolz_cost)
        config["orders_profit"][str(order_id)] = {
            "fp_sum": fp_sum,
            "fp_net": fp_net,
            "lolz_cost": lolz_cost,
            "profit": profit,
            "country": country_code,
            "date": time.strftime("%Y-%m-%d %H:%M:%S")
        }
        save_config()
        return True
    except Exception as e:
        return False


def get_order_profit(order_id):
    return config["orders_profit"].get(str(order_id))


def get_total_profit():
    total = 0
    user_orders_data = load_user_orders()

    # Группируем заказы по основному ID заказа
    order_groups = {}

    for uid, orders in user_orders_data["user_orders"].items():
        for order_id, order_data in orders.items():
            # Извлекаем основной ID заказа (без суффикса _1, _2, etc.)
            base_order_id = order_id.split('_')[0]

            if base_order_id not in order_groups:
                order_groups[base_order_id] = 0

            # Суммируем прибыль
            if order_id in config["orders_profit"]:
                profit_data = config["orders_profit"][order_id]
                order_groups[base_order_id] += profit_data.get("profit", 0)
            elif base_order_id in config["orders_profit"]:
                # Если основного заказа нет, но есть в прибыли
                profit_data = config["orders_profit"][base_order_id]
                order_groups[base_order_id] = profit_data.get("profit", 0)

    # Суммируем общую прибыль
    for profit in order_groups.values():
        total += profit

    return total


def set_origin(call: types.CallbackQuery):
    origin_code = call.data.split("_")[-1]

    if origin_code == "self_reg":
        origin_code = "self_registration"

    if origin_code in ORIGIN_MAP:
        if origin_code in config["origins"]:
            if len(config["origins"]) > 1:
                config["origins"].remove(origin_code)
                action_text = "удалено"
            else:
                bot.answer_callback_query(call.id, "Нельзя удалить последний тип происхождения")
                return
        else:
            config["origins"].append(origin_code)
            action_text = "добавлено"

        save_config()
        bot.answer_callback_query(call.id, f"Происхождение '{ORIGIN_MAP[origin_code]}' {action_text}")

    show_tg_settings_callback(call)


def import_existing_orders(c: Cardinal):
    try:
        user_orders_data = load_user_orders()

        next_order, orders = c.account.get_sells()

        imported_count = 0

        for order in orders:
            if order.id in order_phone_numbers and order.id in order_account_ids:
                phone = order_phone_numbers[order.id]
                item_id = order_account_ids[order.id]
                user_id = str(order.buyer_username)

                if user_id not in user_orders_data["user_orders"]:
                    user_orders_data["user_orders"][user_id] = {}

                if str(order.id) not in user_orders_data["user_orders"][user_id]:
                    user_orders_data["user_orders"][user_id][str(order.id)] = {
                        "phone": phone,
                        "item_id": item_id
                    }
                    user_orders_data["phone_users"][phone] = user_id
                    imported_count += 1

        save_user_orders(user_orders_data)
    except Exception as e:
        logger.error(f"{LOGGER_PREFIX} Ошибка при импорте существующих заказов: {e}")


def get_lolz_user_info(token):
    try:
        url = "https://api.lzt.market/me"
        headers = {
            "accept": "application/json",
            "authorization": f"Bearer {token}"
        }
        response = requests.get(url, headers=headers, timeout=10)
        if response.status_code == 200:
            data = response.json()
            username = data.get('user', {}).get('username', 'Неизвестно')
            balance = float(data.get('user', {}).get('balance', 0))
            return username, balance
        else:
            return None, None
    except Exception as e:
        return None, None


def init_commands(c_: Cardinal):
    global bot, cardinal_instance, config, executor

    cardinal_instance = c_
    bot = c_.telegram.bot
    config = ensure_config_exists()

    c_.add_telegram_commands(
        UUID,
        [
            ("tg_settings", "настройка автовыдачи телеграм номеров", True),
        ],
    )

    load_user_orders()

    executor = concurrent.futures.ThreadPoolExecutor(max_workers=max_workers)

    threading.Thread(target=import_existing_orders, args=(c_,), daemon=True).start()

    threading.Thread(target=process_order_queue, daemon=True).start()

    threading.Thread(target=api_health_check_loop, daemon=True).start()

    _all_handlers = [handler for handler_group in bot.callback_query_handlers for handler in handler_group]

    @bot.callback_query_handler(func=lambda call: call.data.startswith('tg_'))
    def handle_all_callbacks(call: types.CallbackQuery):
        if call.data == "samoreg" or call.data == "samoreg":
            set_origin(call)
            return

        if call.data == "tg_activate":
            # Просто возвращаемся в главное меню
            show_tg_settings_callback(call)
            return

        if call.data.startswith("tg_edit_country_name_"):
            handle_edit_country_name(call)
        elif call.data.startswith("tg_edit_country_min_"):
            handle_edit_country_min(call)
        elif call.data.startswith("tg_edit_country_max_"):
            handle_edit_country_max(call)
        elif call.data.startswith("tg_edit_country_"):
            handle_edit_country_menu(call)
        elif call.data == "tg_countries" or call.data.startswith("tg_countries_page_"):
            handle_countries_menu(call)
        elif call.data == "tg_countries_info":
            bot.answer_callback_query(call.id, "Информация о страницах")
        elif call.data == "tg_add_country":
            handle_add_country(call)
        elif call.data == "tg_load_default_countries":
            handle_load_default_countries(call)
        elif call.data.startswith("tg_delete_country_"):
            handle_delete_country(call)
        elif call.data.startswith("tg_confirm_delete_country_"):
            handle_confirm_delete_country(call)
        elif call.data == "tg_delete_all_countries":
            handle_delete_all_countries(call)
        elif call.data == "tg_confirm_delete_all_countries":
            handle_confirm_delete_all_countries(call)
        elif call.data == "tg_admins":
            admin_menu(call)
        elif call.data == "tg_auto_returns":
            auto_returns_menu(call)
        elif call.data == "tg_auto_returns_on":
            auto_returns_on(call)
        elif call.data == "tg_auto_returns_off":
            auto_returns_off(call)
        elif call.data == "tg_lolz_token":
            lolz_token_menu(call)
        elif call.data == "tg_add_lolz_token" or call.data == "tg_edit_lolz_token":
            add_edit_lolz_token(call)
        elif call.data == "tg_delete_lolz_token":
            delete_lolz_token_confirm(call)
        elif call.data == "tg_confirm_delete_lolz_token":
            delete_lolz_token_confirmed(call)
        elif call.data == "tg_check_lolz_token":
            check_lolz_token(call)
        elif call.data == "tg_origin":
            origin_menu(call)
        elif call.data == "tg_add_admin":
            add_admin(call)
        elif call.data.startswith("tg_set_origin_"):
            set_origin(call)
        elif call.data == "tg_setup_plugin":
            plugin_setup_menu(call)
        elif call.data == "tg_settings_menu":
            plugin_setup_menu(call)
        elif call.data == "tg_back_to_main":
            show_tg_settings_callback(call)
        elif call.data == "tg_message_templates":
            message_templates_menu(call)
        elif call.data == "tg_edit_purchase_template":
            edit_purchase_template(call)
        elif call.data == "tg_edit_code_template":
            edit_code_template(call)
        elif call.data == "tg_orders":
            orders_menu(call)
        elif call.data.startswith("tg_page_") and "orders" in call.data:
            orders_menu(call)
        elif call.data.startswith("tg_order_"):
            order_details(call)
        elif call.data == "tg_toggle_cheapest":
            toggle_cheapest(call)
        elif call.data == "tg_toggle_check":
            toggle_check(call)
        elif call.data == "tg_set_balance_threshold":
            set_balance_threshold(call)
        elif call.data == "tg_notifications":
            notifications_menu(call)
        elif call.data == "tg_toggle_notif_success":
            toggle_notification(call, "success")
        elif call.data == "tg_toggle_notif_error":
            toggle_notification(call, "error")
        elif call.data == "tg_toggle_notif_code_request":
            toggle_notification(call, "code_request")
        elif call.data == "tg_toggle_notif_refund":
            toggle_notification(call, "refund")
        elif call.data == "tg_toggle_notif_low_balance":
            toggle_notification(call, "low_balance")
        elif call.data == "tg_country_stats":
            country_stats_menu(call)
        elif call.data == "tg_toggle_geo_spamblock":
            toggle_geo_spamblock(call)
        elif call.data == "tg_toggle_spamblock":
            toggle_spamblock(call)
        elif call.data == "tg_toggle_multi_account_delivery":
            toggle_multi_account_delivery(call)
        elif call.data == "tg_toggle_auto_deactivate_tg_lots":
            toggle_auto_deactivate_tg_lots(call)
        elif call.data == "tg_toggle_notify_api_check":
            toggle_notify_api_check(call)
        elif call.data == "tg_replace_issue":
            replace_issue_start(call)
        elif call.data == "tg_blocked_users":
            blocked_users_menu(call)
        elif call.data == "tg_add_blocked_user":
            add_blocked_user_start(call)
        elif call.data.startswith("tg_remove_blocked_user_"):
            remove_blocked_user_confirm(call)
        elif call.data.startswith("tg_confirm_remove_blocked_user_"):
            remove_blocked_user_confirmed(call)
        else:
            bot.answer_callback_query(call.id, "@LCNPrime Напортачил)")

    def set_balance_threshold(call: types.CallbackQuery):
        current = config.get("low_balance_threshold", 5000)
        thresholds = [100, 200, 300, 400, 5000]

        if current in thresholds:
            index = thresholds.index(current)
            next_index = (index + 1) % len(thresholds)
            new_threshold = thresholds[next_index]
        else:
            new_threshold = 100

        config["low_balance_threshold"] = new_threshold
        save_config()

        bot.answer_callback_query(call.id, f"Порог уведомления о балансе: {new_threshold}₽")
        plugin_setup_menu(call)

    def toggle_cheapest(call: types.CallbackQuery):
        config["buy_cheapest"] = not config.get("buy_cheapest", True)
        save_config()
        bot.answer_callback_query(call.id,
                                  f"Покупка самого дешевого {'включена' if config['buy_cheapest'] else 'выключена'}")
        plugin_setup_menu(call)

    def toggle_check(call: types.CallbackQuery):
        config["check_accounts"] = not config.get("check_accounts", True)
        save_config()
        bot.answer_callback_query(call.id,
                                  f"Проверка аккаунтов {'включена' if config['check_accounts'] else 'выключена'}")
        plugin_setup_menu(call)

    def toggle_notification(call: types.CallbackQuery, setting_name: str):
        config["notifications"][setting_name] = not config["notifications"][setting_name]
        save_config()
        bot.answer_callback_query(call.id, "Настройка обновлена!")
        notifications_menu(call)

    def notifications_menu(call: types.CallbackQuery):
        settings = config["notifications"]
        notify_api_check_text = "✅ Уведомлять о проверке Api" if config.get("notify_api_check",
                                                                            False) else "❌ Уведомлять о проверке Api"
        kb = InlineKeyboardMarkup(row_width=1)

        kb.add(InlineKeyboardButton(
            f"{'✅' if settings['success'] else '❌'} Успешная выдача аккаунта",
            callback_data="tg_toggle_notif_success"
        ))
        kb.add(InlineKeyboardButton(
            f"{'✅' if settings['error'] else '❌'} Ошибки при выдаче",
            callback_data="tg_toggle_notif_error"
        ))
        kb.add(InlineKeyboardButton(
            f"{'✅' if settings['code_request'] else '❌'} Запросы кодов",
            callback_data="tg_toggle_notif_code_request"
        ))
        kb.add(InlineKeyboardButton(
            f"{'✅' if settings['refund'] else '❌'} Автовозвраты",
            callback_data="tg_toggle_notif_refund"
        ))
        kb.add(InlineKeyboardButton(
            f"{'✅' if settings['low_balance'] else '❌'} Низкий баланс LOLZ",
            callback_data="tg_toggle_notif_low_balance"
        ))
        kb.add(InlineKeyboardButton(notify_api_check_text, callback_data="tg_toggle_notify_api_check"))
        kb.add(InlineKeyboardButton("🔙 Назад", callback_data="tg_back_to_main"))

        bot.edit_message_text(
            "🔔 <b>Настройки уведомлений</b>\n\nВыберите типы событий, о которых вы хотите получать уведомления:",
            call.message.chat.id,
            call.message.message_id,
            reply_markup=kb,
            parse_mode="HTML"
        )

    def country_stats_menu(call: types.CallbackQuery):
        country_stats = {}
        user_orders_data = load_user_orders()

        # Группируем заказы по основному ID заказа
        order_groups = {}

        for uid, orders in user_orders_data["user_orders"].items():
            for order_id, order_data in orders.items():
                # Извлекаем основной ID заказа (без суффикса _1, _2, etc.)
                base_order_id = order_id.split('_')[0]

                if base_order_id not in order_groups:
                    order_groups[base_order_id] = {
                        "order_id": base_order_id,
                        "profit": 0,
                        "country": None
                    }

                # Суммируем прибыль
                if order_id in config["orders_profit"]:
                    profit_data = config["orders_profit"][order_id]
                    order_groups[base_order_id]["profit"] += profit_data.get("profit", 0)
                    if order_groups[base_order_id]["country"] is None:
                        order_groups[base_order_id]["country"] = profit_data.get("country")
                elif base_order_id in config["orders_profit"]:
                    # Если основного заказа нет, но есть в прибыли
                    profit_data = config["orders_profit"][base_order_id]
                    order_groups[base_order_id]["profit"] = profit_data.get("profit", 0)
                    order_groups[base_order_id]["country"] = profit_data.get("country")

        # Обрабатываем статистику по странам
        for order_id, data in order_groups.items():
            country_code = data.get("country")
            if not country_code:
                continue

            if country_code not in country_stats:
                country_stats[country_code] = {
                    "count": 0,
                    "total_profit": 0,
                    "name": config["countries"].get(country_code, {}).get("name", f"Неизвестно ({country_code})")
                }

            country_stats[country_code]["count"] += 1
            country_stats[country_code]["total_profit"] += data.get("profit", 0)

        if not country_stats:
            bot.answer_callback_query(call.id, "Нет данных для статистики по странам")
            return orders_menu(call)

        sorted_stats = sorted(country_stats.items(), key=lambda x: x[1]["total_profit"], reverse=True)

        message_text = "📊 <b>Статистика по странам:</b>\n\n"
        for code, data in sorted_stats:
            message_text += (
                f"🌍 <b>{data['name']}</b>\n"
                f"├ Заказов: {data['count']}\n"
                f"└ Прибыль: {data['total_profit']:.2f}₽\n\n"
            )

        kb = InlineKeyboardMarkup()
        kb.add(InlineKeyboardButton("🔙 Назад", callback_data="tg_orders"))

        bot.edit_message_text(
            message_text,
            call.message.chat.id,
            call.message.message_id,
            reply_markup=kb,
            parse_mode="HTML"
        )

    @bot.message_handler(commands=['tg_settings', 'tg'])
    def tg_settings_command(message: types.Message):
        show_tg_settings(message)

    def handle_countries_menu(call: types.CallbackQuery):
        # Определяем страницу из callback_data
        page = 0
        if call.data.startswith("tg_countries_page_"):
            try:
                page = int(call.data.split("_")[-1])
            except ValueError:
                page = 0

        kb = InlineKeyboardMarkup(row_width=1)

        # Всегда добавляем кнопки управления в начало
        kb.add(InlineKeyboardButton("➕ Добавить страну", callback_data="tg_add_country"))
        kb.add(InlineKeyboardButton("📥 Загрузить все страны", callback_data="tg_load_default_countries"))

        if config["countries"]:
            # Добавляем кнопку удаления всех стран
            kb.add(InlineKeyboardButton("🗑️ Удалить все страны", callback_data="tg_delete_all_countries"))

            # Разбиваем страны на страницы (максимум 8 стран на страницу)
            countries_list = list(config["countries"].items())
            countries_per_page = 8
            total_pages = (len(countries_list) - 1) // countries_per_page + 1
            page = min(page, total_pages - 1)

            start_idx = page * countries_per_page
            end_idx = min(start_idx + countries_per_page, len(countries_list))

            # Добавляем страны для текущей страницы
            for code, country_data in countries_list[start_idx:end_idx]:
                callback_data = f"tg_edit_country_{code.strip()}"
                kb.add(InlineKeyboardButton(
                    f"{country_data['name']} ({code}) - {country_data['min_price']}₽-{country_data['max_price']}₽",
                    callback_data=callback_data
                ))

            # Добавляем навигацию по страницам
            nav_buttons = []
            if page > 0:
                nav_buttons.append(InlineKeyboardButton("⬅️ Назад", callback_data=f"tg_countries_page_{page - 1}"))
            if page < total_pages - 1:
                nav_buttons.append(InlineKeyboardButton("Далее ➡️", callback_data=f"tg_countries_page_{page + 1}"))

            if nav_buttons:
                kb.row(*nav_buttons)

            # Добавляем информацию о странице
            if total_pages > 1:
                kb.add(InlineKeyboardButton(f"📄 Страница {page + 1}/{total_pages}", callback_data="tg_countries_info"))

        # Всегда добавляем кнопку "Назад" в конец
        kb.add(InlineKeyboardButton("🔙 Назад", callback_data="tg_back_to_main"))

        message_text = "🌍 Управление странами:"
        if config["countries"] and len(config["countries"]) > 8:
            message_text += f"\n📄 Страница {page + 1}/{(len(config['countries']) - 1) // 8 + 1}"

        bot.edit_message_text(message_text, call.message.chat.id, call.message.message_id, reply_markup=kb)

    def handle_add_country(call: types.CallbackQuery):
        msg = bot.edit_message_text(
            "Введите краткий код страны (например, RU для России):",
            call.message.chat.id,
            call.message.message_id,
            reply_markup=InlineKeyboardMarkup().add(
                InlineKeyboardButton("🔙 Отмена", callback_data="tg_back_to_main")
            )
        )
        bot.register_next_step_handler(msg, add_country_step2)

    def handle_edit_country_menu(call: types.CallbackQuery):
        country_code = call.data.replace("tg_edit_country_", "")

        if country_code not in config["countries"]:
            bot.answer_callback_query(call.id, "Страна не найдена!")
            return handle_countries_menu(call)

        try:
            country_data = config["countries"][country_code]
            kb = InlineKeyboardMarkup(row_width=1)
            kb.add(
                InlineKeyboardButton("✏️ Изменить название", callback_data=f"tg_edit_country_name_{country_code}"),
                InlineKeyboardButton("💰 Изменить мин. цену", callback_data=f"tg_edit_country_min_{country_code}"),
                InlineKeyboardButton("💎 Изменить макс. цену", callback_data=f"tg_edit_country_max_{country_code}"),
                InlineKeyboardButton("🗑️ Удалить страну", callback_data=f"tg_delete_country_{country_code}"),
                InlineKeyboardButton("🔙 Назад", callback_data="tg_countries")
            )

            bot.edit_message_text(
                f"Настройки страны: {country_data['name']} ({country_code})\n"
                f"Минимальная цена: {country_data['min_price']}₽\n"
                f"Максимальная цена: {country_data['max_price']}₽",
                call.message.chat.id,
                call.message.message_id,
                reply_markup=kb
            )
        except Exception as e:
            bot.answer_callback_query(call.id, "Произошла ошибка при обработке запроса")
            handle_countries_menu(call)

    def handle_edit_country_name(call: types.CallbackQuery):
        country_code = call.data.replace("tg_edit_country_name_", "")

        if country_code not in config["countries"]:
            bot.answer_callback_query(call.id, f"Ошибка: страна с кодом {country_code} больше не существует!")
            return handle_countries_menu(call)

        try:
            msg = bot.edit_message_text(
                f"Введите новое название для страны {country_code}:",
                call.message.chat.id,
                call.message.message_id,
                reply_markup=InlineKeyboardMarkup().add(
                    InlineKeyboardButton("🔙 Отмена", callback_data=f"tg_edit_country_{country_code}")
                )
            )
            bot.register_next_step_handler(msg, process_country_name_edit, country_code)
        except Exception as e:
            bot.answer_callback_query(call.id, "Произошла ошибка при обработке запроса")
            handle_countries_menu(call)

    def handle_edit_country_min(call: types.CallbackQuery):
        country_code = call.data.replace("tg_edit_country_min_", "")

        if country_code not in config["countries"]:
            bot.answer_callback_query(call.id, f"Ошибка: страна с кодом {country_code} больше не существует!")
            return handle_countries_menu(call)

        try:
            msg = bot.edit_message_text(
                f"Введите новую минимальную цену для страны {config['countries'][country_code]['name']} ({country_code}):",
                call.message.chat.id,
                call.message.message_id,
                reply_markup=InlineKeyboardMarkup().add(
                    InlineKeyboardButton("🔙 Отмена", callback_data=f"tg_edit_country_{country_code}")
                )
            )
            bot.register_next_step_handler(msg, process_country_min_edit, country_code)
        except Exception as e:
            bot.answer_callback_query(call.id, "Произошла ошибка при обработке запроса")
            handle_countries_menu(call)

    def handle_edit_country_max(call: types.CallbackQuery):
        country_code = call.data.replace("tg_edit_country_max_", "")

        if country_code not in config["countries"]:
            bot.answer_callback_query(call.id, f"Ошибка: страна с кодом {country_code} больше не существует!")
            return handle_countries_menu(call)

        try:
            msg = bot.edit_message_text(
                f"Введите новую максимальную цену для страны {config['countries'][country_code]['name']} ({country_code}):",
                call.message.chat.id,
                call.message.message_id,
                reply_markup=InlineKeyboardMarkup().add(
                    InlineKeyboardButton("🔙 Отмена", callback_data=f"tg_edit_country_{country_code}")
                )
            )
            bot.register_next_step_handler(msg, process_country_max_edit, country_code)
        except Exception as e:
            bot.answer_callback_query(call.id, "Произошла ошибка при обработке запроса")
            handle_countries_menu(call)

    def handle_delete_country(call: types.CallbackQuery):
        country_code = call.data.replace("tg_delete_country_", "")
        kb = InlineKeyboardMarkup(row_width=2)
        kb.add(
            InlineKeyboardButton("✅ Да", callback_data=f"tg_confirm_delete_country_{country_code}"),
            InlineKeyboardButton("❌ Нет", callback_data=f"tg_edit_country_{country_code}")
        )

        bot.edit_message_text(
            f"Вы уверены, что хотите удалить страну {config['countries'][country_code]['name']} ({country_code})?",
            call.message.chat.id,
            call.message.message_id,
            reply_markup=kb
        )

    def handle_confirm_delete_country(call: types.CallbackQuery):
        country_code = call.data.replace("tg_confirm_delete_country_", "")
        country_name = config["countries"][country_code]["name"]
        del config["countries"][country_code]
        save_config()

        bot.answer_callback_query(call.id, f"Страна {country_name} удалена!")
        handle_countries_menu(call)

    def handle_load_default_countries(call: types.CallbackQuery):
        default_countries = get_default_countries()
        added_count = 0
        skipped_count = 0

        for code, country_data in default_countries.items():
            if code not in config["countries"]:
                config["countries"][code] = country_data
                added_count += 1
            else:
                skipped_count += 1

        save_config()

        message = f"✅ Загружено {added_count} новых стран"
        if skipped_count > 0:
            message += f"\n⏭️ Пропущено {skipped_count} существующих стран"

        bot.answer_callback_query(call.id, message)
        handle_countries_menu(call)

    def handle_delete_all_countries(call: types.CallbackQuery):
        if not config["countries"]:
            bot.answer_callback_query(call.id, "Нет стран для удаления!")
            return handle_countries_menu(call)

        countries_count = len(config["countries"])
        kb = InlineKeyboardMarkup(row_width=2)
        kb.add(
            InlineKeyboardButton("✅ Да, удалить все", callback_data="tg_confirm_delete_all_countries"),
            InlineKeyboardButton("❌ Нет, отменить", callback_data="tg_countries")
        )

        bot.edit_message_text(
            f"⚠️ <b>ВНИМАНИЕ!</b>\n\n"
            f"Вы уверены, что хотите удалить <b>ВСЕ {countries_count} стран</b>?\n\n"
            f"Это действие нельзя отменить!",
            call.message.chat.id,
            call.message.message_id,
            reply_markup=kb,
            parse_mode="HTML"
        )

    def handle_confirm_delete_all_countries(call: types.CallbackQuery):
        countries_count = len(config["countries"])
        config["countries"].clear()
        save_config()

        bot.answer_callback_query(call.id, f"✅ Удалено {countries_count} стран!")
        handle_countries_menu(call)

    @bot.callback_query_handler(func=lambda call: call.data == "tg_admins")
    def admin_menu(call: types.CallbackQuery):
        kb = InlineKeyboardMarkup(row_width=1)
        kb.add(InlineKeyboardButton("➕ Добавить администратора", callback_data="tg_add_admin"))

        for admin_id in config["administrators"]:
            kb.add(InlineKeyboardButton(
                f"ID: {admin_id}",
                callback_data=f"tg_delete_admin_{admin_id}"
            ))

        kb.add(InlineKeyboardButton("🔙 Назад", callback_data="tg_back_to_main"))
        bot.edit_message_text(
            "👥 Администраторы (получают уведомления):",
            call.message.chat.id,
            call.message.message_id,
            reply_markup=kb
        )

    @bot.callback_query_handler(func=lambda call: call.data == "tg_add_admin")
    def add_admin(call: types.CallbackQuery):
        msg = bot.edit_message_text(
            "Введите ID пользователя Telegram для добавления в администраторы:",
            call.message.chat.id,
            call.message.message_id,
            reply_markup=InlineKeyboardMarkup().add(
                InlineKeyboardButton("🔙 Отмена", callback_data="tg_admins")
            )
        )
        bot.register_next_step_handler(msg, process_add_admin)

    def process_add_admin(message: types.Message):
        if message.text is None:
            return

        try:
            bot.delete_message(message.chat.id, message.message_id - 1)
        except Exception:
            pass

        try:
            admin_id = int(message.text.strip())
            if admin_id in config["administrators"]:
                bot.send_message(message.chat.id, "❌ Этот пользователь уже является администратором!")
                bot.clear_step_handler_by_chat_id(message.chat.id)
                return show_tg_settings(message)

            config["administrators"].append(admin_id)
            save_config()
            bot.clear_step_handler_by_chat_id(message.chat.id)
            bot.send_message(message.chat.id, f"✅ Администратор с ID {admin_id} добавлен!")
            show_tg_settings(message)
        except ValueError:
            bot.clear_step_handler_by_chat_id(message.chat.id)
            bot.send_message(message.chat.id, "❌ Введите корректный числовой ID!")
            show_tg_settings(message)

    @bot.callback_query_handler(func=lambda call: call.data.startswith("tg_delete_admin_"))
    def delete_admin_confirm(call: types.CallbackQuery):
        admin_id = int(call.data.split("_")[-1])
        kb = InlineKeyboardMarkup(row_width=2)
        kb.add(
            InlineKeyboardButton("✅ Да", callback_data=f"tg_confirm_delete_admin_{admin_id}"),
            InlineKeyboardButton("❌ Нет", callback_data="tg_admins")
        )

        bot.edit_message_text(
            f"Вы уверены, что хотите удалить администратора с ID {admin_id}?",
            call.message.chat.id,
            call.message.message_id,
            reply_markup=kb
        )

    @bot.callback_query_handler(func=lambda call: call.data.startswith("tg_confirm_delete_admin_"))
    def delete_admin_confirmed(call: types.CallbackQuery):
        admin_id = int(call.data.split("_")[-1])
        if admin_id in config["administrators"]:
            config["administrators"].remove(admin_id)
            save_config()

        bot.answer_callback_query(call.id, f"Администратор {admin_id} удален!")
        admin_menu(call)

    @bot.callback_query_handler(func=lambda call: call.data == "tg_auto_returns")
    def auto_returns_menu(call: types.CallbackQuery):
        kb = InlineKeyboardMarkup(row_width=2)
        current_status = "✅ Включены" if config["auto_returns"] else "❌ Выключены"

        kb.add(
            InlineKeyboardButton("✅ Включить", callback_data="tg_auto_returns_on"),
            InlineKeyboardButton("❌ Выключить", callback_data="tg_auto_returns_off")
        )
        kb.add(InlineKeyboardButton("🔙 Назад", callback_data="tg_back_to_main"))

        bot.edit_message_text(
            f"🔄 Автовозвраты: {current_status}\n\n"
            "При включенных автовозвратах система автоматически возвращает средства пользователю "
            "в случае проблем с номером и отправляет уведомление администраторам.\n\n"
            "При выключенных автовозвратах пользователю показывается причина проблемы и "
            "предлагается кнопка для ручного возврата через администратора.\n\n"
            "P.S. лучше не включать чтобы не потерять закреп.",
            call.message.chat.id,
            call.message.message_id,
            reply_markup=kb
        )

    @bot.callback_query_handler(func=lambda call: call.data == "tg_auto_returns_on")
    def auto_returns_on(call: types.CallbackQuery):
        config["auto_returns"] = True
        save_config()
        bot.answer_callback_query(call.id, "Автовозвраты включены!")
        auto_returns_menu(call)

    @bot.callback_query_handler(func=lambda call: call.data == "tg_auto_returns_off")
    def auto_returns_off(call: types.CallbackQuery):
        config["auto_returns"] = False
        save_config()
        bot.answer_callback_query(call.id, "Автовозвраты выключены!")
        auto_returns_menu(call)

    @bot.callback_query_handler(func=lambda call: call.data == "tg_lolz_token")
    def lolz_token_menu(call: types.CallbackQuery):
        kb = InlineKeyboardMarkup(row_width=1)

        if config["lolz_token"]:
            masked_token = config["lolz_token"][:4] + "*" * (len(config["lolz_token"]) - 8) + config["lolz_token"][-4:]
            kb.add(
                InlineKeyboardButton("✏️ Редактировать токен", callback_data="tg_edit_lolz_token"),
                InlineKeyboardButton("🗑️ Удалить токен", callback_data="tg_delete_lolz_token"),
                InlineKeyboardButton("✅ Проверить токен", callback_data="tg_check_lolz_token")
            )
            token_status = f"🔑 Текущий токен: {masked_token}"
        else:
            kb.add(InlineKeyboardButton("➕ Добавить токен", callback_data="tg_add_lolz_token"))
            token_status = "❌ Токен не настроен"

        kb.add(InlineKeyboardButton("🔙 Назад", callback_data="tg_back_to_main"))

        bot.edit_message_text(
            f"LOLZ TOKEN: {token_status}",
            call.message.chat.id,
            call.message.message_id,
            reply_markup=kb
        )

    @bot.callback_query_handler(func=lambda call: call.data == "tg_add_lolz_token" or call.data == "tg_edit_lolz_token")
    def add_edit_lolz_token(call: types.CallbackQuery):
        action = "Введите" if call.data == "tg_add_lolz_token" else "Введите новый"
        msg = bot.edit_message_text(
            f"{action} LOLZ токен:",
            call.message.chat.id,
            call.message.message_id,
            reply_markup=InlineKeyboardMarkup().add(
                InlineKeyboardButton("🔙 Отмена", callback_data="tg_lolz_token")
            )
        )
        bot.register_next_step_handler(msg, process_lolz_token)

    def process_lolz_token(message: types.Message):
        if message.text is None:
            return

        try:
            bot.delete_message(message.chat.id, message.message_id - 1)
        except Exception:
            pass

        token = message.text.strip()
        config["lolz_token"] = token
        save_config()

        bot.clear_step_handler_by_chat_id(message.chat.id)

        bot.delete_message(message.chat.id, message.message_id)

        bot.send_message(message.chat.id, "✅ LOLZ токен успешно сохранен!")
        show_tg_settings(message)

    @bot.callback_query_handler(func=lambda call: call.data == "tg_delete_lolz_token")
    def delete_lolz_token_confirm(call: types.CallbackQuery):
        kb = InlineKeyboardMarkup(row_width=2)
        kb.add(
            InlineKeyboardButton("✅ Да", callback_data="tg_confirm_delete_lolz_token"),
            InlineKeyboardButton("❌ Нет", callback_data="tg_lolz_token")
        )

        bot.edit_message_text(
            "Вы уверены, что хотите удалить LOLZ токен?",
            call.message.chat.id,
            call.message.message_id,
            reply_markup=kb
        )

    @bot.callback_query_handler(func=lambda call: call.data == "tg_confirm_delete_lolz_token")
    def delete_lolz_token_confirmed(call: types.CallbackQuery):
        config["lolz_token"] = ""
        save_config()
        bot.answer_callback_query(call.id, "LOLZ токен удален!")
        lolz_token_menu(call)

    def check_lolz_token(call: types.CallbackQuery):
        token = config.get("lolz_token")
        if not token:
            bot.answer_callback_query(call.id, "❌ Токен не настроен")
            return

        try:
            bot.answer_callback_query(call.id, "🔄 Проверяем токен...")

            username, balance = get_lolz_user_info(token)
            if username is None or balance is None:
                raise Exception("Не удалось получить данные. Проверьте токен.")

            message_text = (
                f"✅ Токен действителен!\n\n"
                f"👤 Имя пользователя: <code>{username}</code>\n"
                f"💰 Баланс: <code>{balance:.2f}</code> RUB\n"
                f"🔑 Токен: <code>{token[:4]}...{token[-4:]}</code>"
            )
        except Exception as e:
            message_text = f"❌ Ошибка при проверке токена: {e}"

        try:
            bot.edit_message_text(
                message_text,
                call.message.chat.id,
                call.message.message_id,
                reply_markup=InlineKeyboardMarkup().add(
                    InlineKeyboardButton("🔙 Назад", callback_data="tg_lolz_token")
                ),
                parse_mode="HTML"
            )
        except Exception as e:
            pass

    @bot.callback_query_handler(func=lambda call: call.data == "tg_origin")
    def origin_menu(call: types.CallbackQuery):
        kb = InlineKeyboardMarkup(row_width=1)

        selected_origins = config["origins"]

        for origin_code, origin_name in ORIGIN_MAP.items():
            callback_data = f"tg_set_origin_{origin_code}"
            if origin_code in selected_origins:
                mark = "✅ "
            else:
                mark = "❌ "

            if origin_code == "self_registration":
                callback_data = "tg_set_origin_self_reg"

            kb.add(InlineKeyboardButton(f"{mark}{origin_name}", callback_data=callback_data))

        kb.add(InlineKeyboardButton("🔄 Сохранить и вернуться", callback_data="tg_back_to_main"))

        selected_names = [ORIGIN_MAP.get(code, "Неизвестно") for code in selected_origins]
        selected_text = ", ".join(selected_names)

        bot.edit_message_text(
            f"🔍 Выберите типы происхождения номеров (можно выбрать несколько):\n\n"
            f"Текущий выбор: {selected_text}\n\n"
            f"✅ - выбранные типы, нажмите для отмены\n"
            f"❌ - не выбраные типы, нажмите для выбора",
            call.message.chat.id,
            call.message.message_id,
            reply_markup=kb
        )

    def plugin_setup_menu(call: types.CallbackQuery):
        buy_cheapest_text = "✅ Покупать самый дешевый" if config.get("buy_cheapest",
                                                                     True) else "❌ Не покупать самый дешевый"
        check_accounts_text = "✅ Проверять аккаунты" if config.get("check_accounts",
                                                                   True) else "❌ Не проверять аккаунты"
        geo_spamblock_text = "✅ Покупать со спамблок гео" if config.get("buy_geo_spamblock",
                                                                        False) else "❌ Не покупать со спамблок гео"
        spamblock_text = "✅ Покупать со спамблоком" if config.get("buy_spamblock",
                                                                  False) else "❌ Не покупать со спамблоком"
        multi_account_text = "✅ Выдавать несколько аккаунтов за 1 заказ" if config.get("multi_account_delivery",
                                                                                       False) else "❌ Только 1 аккаунт за заказ"
        low_balance_threshold = config.get("low_balance_threshold", 500)
        threshold_text = f"⚠️ Порог баланса: {low_balance_threshold}₽"
        auto_deactivate_text = "✅ Авто-деактивация лотов при сбое API" if config.get(
            "auto_deactivate_tg_lots_on_api_fail", False) else "❌ Авто-деактивация лотов при сбое API"
        auto_returns_text = "✅ Автовозвраты включены" if config.get("auto_returns",
                                                                    True) else "❌ Автовозвраты выключены"

        kb = InlineKeyboardMarkup(row_width=1)
        kb.add(
            InlineKeyboardButton(buy_cheapest_text, callback_data="tg_toggle_cheapest"),
            InlineKeyboardButton(check_accounts_text, callback_data="tg_toggle_check"),
            InlineKeyboardButton(geo_spamblock_text, callback_data="tg_toggle_geo_spamblock"),
            InlineKeyboardButton(spamblock_text, callback_data="tg_toggle_spamblock"),
            InlineKeyboardButton(multi_account_text, callback_data="tg_toggle_multi_account_delivery"),
            InlineKeyboardButton(threshold_text, callback_data="tg_set_balance_threshold"),
            InlineKeyboardButton(auto_deactivate_text, callback_data="tg_toggle_auto_deactivate_tg_lots"),
            InlineKeyboardButton(auto_returns_text, callback_data="tg_auto_returns"),
            InlineKeyboardButton("🔄 Сохранить и вернуться", callback_data="tg_back_to_main"),
            InlineKeyboardButton("🔙 Назад", callback_data="tg_back_to_main")
        )

        message_text = (
            "⚙️ <b>Настройка плагина</b>\n\n"
            "1. <b>Покупать самый дешевый:</b> При включении всегда выбирает самый дешевый подходящий аккаунт.\n"
            "2. <b>Проверять аккаунты:</b> Проверяет аккаунт перед покупкой на блокировки и проблемы.\n"
            "3. <b>Покупка со спамблок гео:</b> Включает/выключает покупку аккаунтов с гео-спамблоком.\n"
            "4. <b>Покупка со спамблоком:</b> Включает/выключает покупку аккаунтов со спамблоком.\n"
            f"5. <b>Выдача нескольких аккаунтов:</b> {'включена' if config['multi_account_delivery'] else 'выключена'}\n"
            f"6. <b>Порог баланса:</b> Уведомление при балансе ниже {low_balance_threshold}₽\n"
            f"7. <b>Авто-деактивация лотов при сбое API:</b> {'включена' if config['auto_deactivate_tg_lots_on_api_fail'] else 'выключена'}\n"
            f"8. <b>Автовозвраты:</b> {'включены' if config.get('auto_returns', True) else 'выключены'}\n\n"
            "Текущие настройки:"
        )

        bot.edit_message_text(
            message_text,
            call.message.chat.id,
            call.message.message_id,
            reply_markup=kb,
            parse_mode="HTML"
        )

    def toggle_geo_spamblock(call: types.CallbackQuery):
        config["buy_geo_spamblock"] = not config.get("buy_geo_spamblock", False)
        save_config()
        bot.answer_callback_query(call.id,
                                  f"Покупка со спамблок гео {'включена' if config['buy_geo_spamblock'] else 'выключена'}")
        plugin_setup_menu(call)

    def toggle_spamblock(call: types.CallbackQuery):
        config["buy_spamblock"] = not config.get("buy_spamblock", False)
        save_config()
        bot.answer_callback_query(call.id,
                                  f"Покупка со спамблоком {'включена' if config['buy_spamblock'] else 'выключена'}")
        plugin_setup_menu(call)

    def toggle_multi_account_delivery(call: types.CallbackQuery):
        config["multi_account_delivery"] = not config.get("multi_account_delivery", False)
        save_config()
        bot.answer_callback_query(call.id,
                                  f"Выдача нескольких аккаунтов {'включена' if config['multi_account_delivery'] else 'выключена'}")
        plugin_setup_menu(call)

    def toggle_auto_deactivate_tg_lots(call: types.CallbackQuery):
        config["auto_deactivate_tg_lots_on_api_fail"] = not config.get("auto_deactivate_tg_lots_on_api_fail", False)
        save_config()
        bot.answer_callback_query(call.id,
                                  f"Авто-деактивация лотов при сбое API {'включена' if config['auto_deactivate_tg_lots_on_api_fail'] else 'выключена'}")
        plugin_setup_menu(call)

    def toggle_notify_api_check(call: types.CallbackQuery):
        config["notify_api_check"] = not config.get("notify_api_check", False)
        save_config()
        bot.answer_callback_query(call.id,
                                  f"Уведомление о проверке Api {'включено' if config['notify_api_check'] else 'выключено'}")
        notifications_menu(call)

    def orders_menu(call: types.CallbackQuery):
        page = 0
        if '_' in call.data:
            parts = call.data.split('_')
            if len(parts) > 2 and parts[1] == 'page':
                try:
                    page = int(parts[2])
                except ValueError:
                    page = 0

        PAGE_SIZE = 5

        user_orders_data = load_user_orders()
        kb = InlineKeyboardMarkup(row_width=1)

        total_profit = get_total_profit()

        country_stats = {}
        for order_id, data in config["orders_profit"].items():
            country_code = data.get("country")
            if not country_code:
                continue

            if country_code not in country_stats:
                country_stats[country_code] = {
                    "count": 0,
                    "total_profit": 0,
                    "name": config["countries"].get(country_code, {}).get("name", f"Неизвестно ({country_code})")
                }

            country_stats[country_code]["count"] += 1
            country_stats[country_code]["total_profit"] += data.get("profit", 0)

        most_popular = None
        most_profitable = None
        if country_stats:
            most_popular = max(country_stats.values(), key=lambda x: x["count"])
            most_profitable = max(country_stats.values(), key=lambda x: x["total_profit"])

        message_text = (
            f"📋 <b>Заказы и профит</b>\n\n"
            f"💰 <b>Общая чистая прибыль:</b> {total_profit:.2f} ₽\n\n"
        )

        if most_popular:
            message_text += f"🌟 <b>Самая популярная страна:</b> {most_popular['name']} ({most_popular['count']} заказов)\n"
        if most_profitable:
            message_text += f"💎 <b>Самая прибыльная страна:</b> {most_profitable['name']} ({most_profitable['total_profit']:.2f}₽)\n\n"

        all_orders = []
        # Группируем заказы по основному ID заказа
        order_groups = {}

        for user_id, orders in user_orders_data["user_orders"].items():
            for order_id, order_data in orders.items():
                # Извлекаем основной ID заказа (без суффикса _1, _2, etc.)
                base_order_id = order_id.split('_')[0]

                if base_order_id not in order_groups:
                    order_groups[base_order_id] = {
                        "order_id": base_order_id,
                        "user_id": user_id,
                        "phones": [],
                        "profit": 0,
                        "date": "Нет данных",
                        "account_count": 0
                    }

                # Добавляем телефон в список
                phone = order_data.get("phone", "Нет данных")
                if phone not in order_groups[base_order_id]["phones"]:
                    order_groups[base_order_id]["phones"].append(phone)
                    order_groups[base_order_id]["account_count"] += 1

                # Суммируем прибыль
                if order_id in config["orders_profit"]:
                    profit_data = config["orders_profit"][order_id]
                    order_groups[base_order_id]["profit"] += profit_data.get("profit", 0)
                    if order_groups[base_order_id]["date"] == "Нет данных":
                        order_groups[base_order_id]["date"] = profit_data.get("date", "Нет данных")
                elif base_order_id in config["orders_profit"]:
                    # Если основного заказа нет, но есть в прибыли
                    profit_data = config["orders_profit"][base_order_id]
                    order_groups[base_order_id]["profit"] = profit_data.get("profit", 0)
                    order_groups[base_order_id]["date"] = profit_data.get("date", "Нет данных")

        # Преобразуем группы в список заказов
        for base_order_id, group_data in order_groups.items():
            all_orders.append(group_data)

        all_orders.sort(key=lambda x: x.get("date", ""), reverse=True)

        if all_orders:
            total_pages = (len(all_orders) - 1) // PAGE_SIZE + 1
            page = min(page, total_pages - 1)

            start_idx = page * PAGE_SIZE
            end_idx = min(start_idx + PAGE_SIZE, len(all_orders))

            current_page_orders = all_orders[start_idx:end_idx]

            message_text += f"<b>Заказы (страница {page + 1}/{total_pages}):</b>\n"

            for order in current_page_orders:
                profit_str = f"+{order['profit']:.2f} ₽" if order['profit'] > 0 else f"{order['profit']:.2f} ₽"
                account_count = order.get('account_count', 1)
                account_text = f" ({account_count} акк.)" if account_count > 1 else ""
                message_text += f"• Заказ #{order['order_id']}{account_text} - {profit_str}\n"
                kb.add(InlineKeyboardButton(f"Заказ #{order['order_id']}{account_text} ({profit_str})",
                                            callback_data=f"tg_order_{order['order_id']}"))

            nav_buttons = []

            if page > 0:
                nav_buttons.append(InlineKeyboardButton("⬅️ Назад", callback_data=f"tg_page_{page - 1}_orders"))

            if page < total_pages - 1:
                nav_buttons.append(InlineKeyboardButton("Далее ➡️", callback_data=f"tg_page_{page + 1}_orders"))

            if nav_buttons:
                kb.row(*nav_buttons)
        else:
            message_text += "📭 У вас пока нет заказов."

        if country_stats:
            kb.add(InlineKeyboardButton("🌍 Статистика по странам", callback_data="tg_country_stats"))

        kb.add(InlineKeyboardButton("🔙 Назад", callback_data="tg_back_to_main"))

        bot.edit_message_text(
            message_text,
            call.message.chat.id,
            call.message.message_id,
            reply_markup=kb,
            parse_mode="HTML"
        )

    def order_details(call: types.CallbackQuery):
        order_id = call.data.split('_')[-1]
        user_orders_data = load_user_orders()

        # Группируем заказы по основному ID заказа
        order_groups = {}

        for uid, orders in user_orders_data["user_orders"].items():
            for o_id, order_data in orders.items():
                # Извлекаем основной ID заказа (без суффикса _1, _2, etc.)
                base_order_id = o_id.split('_')[0]

                if base_order_id not in order_groups:
                    order_groups[base_order_id] = {
                        "order_id": base_order_id,
                        "user_id": uid,
                        "phones": [],
                        "profit": 0,
                        "date": "Нет данных",
                        "account_count": 0,
                        "item_ids": []
                    }

                # Добавляем телефон в список
                phone = order_data.get("phone", "Нет данных")
                if phone not in order_groups[base_order_id]["phones"]:
                    order_groups[base_order_id]["phones"].append(phone)
                    order_groups[base_order_id]["account_count"] += 1

                # Добавляем item_id
                item_id = order_data.get("item_id", "Нет данных")
                if item_id not in order_groups[base_order_id]["item_ids"]:
                    order_groups[base_order_id]["item_ids"].append(item_id)

                # Суммируем прибыль
                if o_id in config["orders_profit"]:
                    profit_data = config["orders_profit"][o_id]
                    order_groups[base_order_id]["profit"] += profit_data.get("profit", 0)
                    if order_groups[base_order_id]["date"] == "Нет данных":
                        order_groups[base_order_id]["date"] = profit_data.get("date", "Нет данных")
                elif base_order_id in config["orders_profit"]:
                    # Если основного заказа нет, но есть в прибыли
                    profit_data = config["orders_profit"][base_order_id]
                    order_groups[base_order_id]["profit"] = profit_data.get("profit", 0)
                    order_groups[base_order_id]["date"] = profit_data.get("date", "Нет данных")

        all_orders = list(order_groups.values())
        all_orders.sort(key=lambda x: x.get("date", ""), reverse=True)

        order_index = -1
        for i, order in enumerate(all_orders):
            if order["order_id"] == order_id:
                order_index = i
                break

        PAGE_SIZE = 5
        page = order_index // PAGE_SIZE if order_index != -1 else 0

        kb = InlineKeyboardMarkup(row_width=1)
        if page > 0:
            kb.add(InlineKeyboardButton("🔙 К списку заказов (стр. " + str(page + 1) + ")",
                                        callback_data=f"tg_page_{page}_orders"))
        else:
            kb.add(InlineKeyboardButton("🔙 К списку заказов", callback_data="tg_orders"))

        found_order = False
        order_data = None

        for order in all_orders:
            if order["order_id"] == order_id:
                found_order = True
                order_data = order
                break

        if found_order:
            user_id = order_data["user_id"]
            phones = order_data["phones"]
            item_ids = order_data["item_ids"]
            account_count = order_data["account_count"]

            # Получаем информацию о прибыли
            profit_info = config["orders_profit"].get(order_id, {})
            fp_sum = profit_info.get("fp_sum", 0)
            fp_net = profit_info.get("fp_net", 0)
            lolz_cost = profit_info.get("lolz_cost", 0)
            profit = order_data["profit"]
            date = order_data["date"]
            country_code = profit_info.get("country", "Неизвестно")
            country_name = config["countries"].get(country_code, {}).get("name", country_code)

            # Формируем список телефонов
            phones_text = ", ".join([f"+{phone}" for phone in phones])

            # Формируем список ID аккаунтов
            item_ids_text = "\n".join([f"🆔 {item_id}" for item_id in item_ids])

            message_text = (
                f"📋 <b>Информация о заказе #{order_id}</b>\n\n"
                f"👤 <b>Покупатель:</b> {user_id}\n"
                f"📦 <b>Количество аккаунтов:</b> {account_count}\n"
                f"🌍 <b>Страна:</b> {country_name}\n"
                f"📅 <b>Дата:</b> {date}\n\n"
                f"<b>Телефоны:</b>\n{phones_text}\n\n"
                f"🆔 <b>ID аккаунтов LOLZ:</b>\n{item_ids_text}\n\n"
                f"💰 <b>Финансы:</b>\n"
                f"• Цена на FunPay: {fp_sum} ₽\n"
                f"• Цена после комиссии FP (97%): {fp_net:.2f} ₽\n"
                f"• Цена на LOLZ: {lolz_cost} ₽\n"
                f"• <b>Чистая прибыль:</b> {profit:.2f} ₽\n"
            )

            kb.add(InlineKeyboardButton("🌐 Открыть заказ на FunPay", url=f"https://funpay.com/orders/{order_id}/"))
        else:
            message_text = f"❌ Заказ #{order_id} не найден в базе данных."

        bot.edit_message_text(
            message_text,
            call.message.chat.id,
            call.message.message_id,
            reply_markup=kb,
            parse_mode="HTML"
        )

    def message_templates_menu(call: types.CallbackQuery):
        bot.clear_step_handler_by_chat_id(call.message.chat.id)

        kb = InlineKeyboardMarkup(row_width=1)
        kb.add(
            InlineKeyboardButton("📝 Шаблон сообщения после покупки", callback_data="tg_edit_purchase_template"),
            InlineKeyboardButton("🔢 Шаблон сообщения с кодом", callback_data="tg_edit_code_template"),
            InlineKeyboardButton("🔙 Назад", callback_data="tg_back_to_main")
        )

        message_text = (
            "💬 <b>Настройка шаблонов сообений</b>\n\n"
            "<b>Доступные переменные для шаблона после покупки:</b>\n"
            "<code>- {phone} - номер(а) телефона аккаунта</code>\n"
            "<code>- {cd_commands} - команды для получения кода (cd ...)</code>\n\n"
            "<b>Доступные переменные для шаблона с кодом:</b>\n"
            "<code>- {code} - код подтверждения\n"
            "- {order_link} - ссылка для подтверждения заказа\n"
            "- {order_id} - номер заказа</code>"
        )

        bot.edit_message_text(
            message_text,
            call.message.chat.id,
            call.message.message_id,
            reply_markup=kb,
            parse_mode="HTML"
        )

    def edit_purchase_template(call: types.CallbackQuery):
        try:
            current_template = config.get("purchase_template", DEFAULT_PURCHASE_TEMPLATE)
            msg = bot.edit_message_text(
                f"📝 <b>Текущий шаблон сообщения после покупки:</b>\n\n<pre>{current_template}</pre>\n\n"
                f"Введите новый шаблон сообщения. Доступные переменные:\n"
                f"- {{phone}} - номер(а) телефона аккаунта\n"
                f"- {{cd_commands}} - команды для получения кода (cd ...)",
                call.message.chat.id,
                call.message.message_id,
                reply_markup=InlineKeyboardMarkup().add(
                    InlineKeyboardButton("🔙 Отмена", callback_data="tg_message_templates")
                ),
                parse_mode="HTML"
            )
            bot.register_next_step_handler(msg, process_purchase_template_edit)
        except Exception as e:
            bot.answer_callback_query(call.id, "Произошла ошибка при обработке запроса")
            message_templates_menu(call)

    def process_purchase_template_edit(message: types.Message):
        if message.text is None:
            bot.clear_step_handler_by_chat_id(message.chat.id)
            bot.send_message(message.chat.id, "❌ Нетекстовое сообщение. Редактирование шаблона отменено.")
            show_tg_settings(message)
            return

        try:
            bot.delete_message(message.chat.id, message.message_id - 1)
        except Exception:
            pass

        new_template = message.text
        if not new_template.strip():
            bot.clear_step_handler_by_chat_id(message.chat.id)
            bot.send_message(message.chat.id, "❌ Пустой шаблон не допускается. Редактирование отменено.")
            show_tg_settings(message)
            return

        config["purchase_template"] = new_template
        save_config()

        bot.clear_step_handler_by_chat_id(message.chat.id)

        bot.send_message(message.chat.id, "✅ Шаблон сообщения после покупки успешно обновлен!")
        show_tg_settings(message)

    def edit_code_template(call: types.CallbackQuery):
        try:
            current_template = config.get("code_template", DEFAULT_CODE_TEMPLATE)
            msg = bot.edit_message_text(
                f"🔢 <b>Текущий шаблон сообщения с кодом:</b>\n\n<pre>{current_template}</pre>\n\n"
                f"Введите новый шаблон сообщения. Доступные переменные:\n"
                f"- {{code}} - код подтверждения\n"
                f"- {{order_link}} - ссылка для подтверждения заказа\n"
                f"- {{order_id}} - номер заказа",
                call.message.chat.id,
                call.message.message_id,
                reply_markup=InlineKeyboardMarkup().add(
                    InlineKeyboardButton("🔙 Отмена", callback_data="tg_message_templates")
                ),
                parse_mode="HTML"
            )
            bot.register_next_step_handler(msg, process_code_template_edit)
        except Exception as e:
            bot.answer_callback_query(call.id, "Произошла ошибка при обработке запроса")
            message_templates_menu(call)

    def process_code_template_edit(message: types.Message):
        if message.text is None:
            bot.clear_step_handler_by_chat_id(message.chat.id)
            bot.send_message(message.chat.id, "❌ Нетекстовое сообщение. Редактирование шаблона отменено.")
            show_tg_settings(message)
            return

        try:
            bot.delete_message(message.chat.id, message.message_id - 1)
        except Exception:
            pass

        new_template = message.text
        if not new_template.strip():
            bot.clear_step_handler_by_chat_id(message.chat.id)
            bot.send_message(message.chat.id, "❌ Пустой шаблон не допускается. Редактирование отменено.")
            show_tg_settings(message)
            return

        config["code_template"] = new_template
        save_config()

        bot.clear_step_handler_by_chat_id(message.chat.id)

        bot.send_message(message.chat.id, "✅ Шаблон сообщения с кодом успешно обновлен!")
        show_tg_settings(message)

    @bot.callback_query_handler(func=lambda call: call.data == "tg_replace_issue")
    def replace_issue_start(call: types.CallbackQuery):
        msg = bot.edit_message_text(
            "Введите номер заказа (без #):",
            call.message.chat.id,
            call.message.message_id,
            reply_markup=InlineKeyboardMarkup().add(
                InlineKeyboardButton("🔙 Отмена", callback_data="tg_back_to_main")
            )
        )
        bot.register_next_step_handler(msg, replace_issue_country)

    def replace_issue_country(message: types.Message):
        if message.text is None:
            return
        order_id = message.text.strip()
        try:
            bot.delete_message(message.chat.id, message.message_id - 1)
        except Exception:
            pass
        msg = bot.send_message(
            message.chat.id,
            f"Введите код страны (например, RU):",
            reply_markup=InlineKeyboardMarkup().add(
                InlineKeyboardButton("🔙 Отмена", callback_data="tg_back_to_main")
            )
        )
        bot.register_next_step_handler(msg, replace_issue_amount, order_id)

    def replace_issue_amount(message: types.Message, order_id: str):
        if message.text is None:
            return
        country_code = message.text.strip().upper()
        try:
            bot.delete_message(message.chat.id, message.message_id - 1)
        except Exception:
            pass
        msg = bot.send_message(
            message.chat.id,
            f"Введите количество аккаунтов для замены:",
            reply_markup=InlineKeyboardMarkup().add(
                InlineKeyboardButton("🔙 Отмена", callback_data="tg_back_to_main")
            )
        )
        bot.register_next_step_handler(msg, replace_issue_process, order_id, country_code)

    def replace_issue_process(message: types.Message, order_id: str, country_code: str):
        global cardinal_instance

        if message.text is None:
            return
        try:
            amount = int(message.text.strip())
        except ValueError:
            bot.send_message(message.chat.id, "❌ Введите корректное число!")
            return
        try:
            bot.delete_message(message.chat.id, message.message_id - 1)
        except Exception:
            pass

        # Покупка аккаунтов
        min_price = config["countries"].get(country_code, {}).get("min_price", 1)
        max_price = config["countries"].get(country_code, {}).get("max_price", 200)
        purchased_phones = []
        purchased_item_ids = []

        # Проверяем существование заказа в FunPay
        order_exists = False
        try:
            if cardinal_instance:
                try:
                    full_order = cardinal_instance.account.get_order(order_id)
                    order_exists = True
                    buyer_username = full_order.buyer_username
                except Exception:
                    order_exists = False
        except Exception:
            order_exists = False

        if not order_exists:
            bot.send_message(message.chat.id, f"❌ Заказ #{order_id} не найден в системе FunPay")
            show_tg_settings(message)
            return

        for i in range(amount):
            available_accounts = find_available_accounts(country_code, min_price, max_price)
            if not available_accounts:
                break
            purchase_result, account_data, _ = try_purchase_accounts(available_accounts)
            if purchase_result and account_data and 'telegram_phone' in account_data:
                phone = account_data['telegram_phone']
                item_id = purchase_result['item'].get('item_id')
                purchased_phones.append(phone)
                purchased_item_ids.append(item_id)

        # Сообщение админу
        if purchased_phones:
            bot.send_message(message.chat.id, f"✅ Замена куплена и выдана! Куплено {len(purchased_phones)} аккаунтов")

            # Сохраняем в систему для команды cd
            user_orders_data = load_user_orders()

            # Ищем пользователя по заказу
            buyer_username_from_db = None
            for uid, orders in user_orders_data["user_orders"].items():
                if order_id in orders:
                    buyer_username_from_db = uid
                    break

            # Если не нашли по точному совпадению, ищем по частичному совпадению
            if not buyer_username_from_db:
                for uid, orders in user_orders_data["user_orders"].items():
                    for order_key in orders.keys():
                        if order_id in order_key:
                            buyer_username_from_db = uid
                            break
                    if buyer_username_from_db:
                        break

            # Используем имя пользователя из FunPay, если оно доступно
            if buyer_username:
                final_buyer_username = buyer_username
            elif buyer_username_from_db:
                final_buyer_username = buyer_username_from_db
            else:
                bot.send_message(message.chat.id, f"❌ Не удалось найти пользователя для заказа #{order_id}")
                show_tg_settings(message)
                return

            if final_buyer_username:
                # Добавляем новые аккаунты в систему
                if final_buyer_username not in user_orders_data["user_orders"]:
                    user_orders_data["user_orders"][final_buyer_username] = {}

                for i, phone in enumerate(purchased_phones):
                    replace_key = f"{order_id}_replace_{i + 1}"
                    user_orders_data["user_orders"][final_buyer_username][replace_key] = {
                        "phone": phone,
                        "item_id": purchased_item_ids[i] if i < len(purchased_item_ids) else None
                    }
                    user_orders_data["phone_users"][phone] = final_buyer_username

                save_user_orders(user_orders_data)

                # Формируем сообщение для покупателя
                if len(purchased_phones) == 1:
                    cd_commands = f"cd {purchased_phones[0]}"
                else:
                    cd_commands = ", ".join([f"cd {phone}" for phone in purchased_phones])

                phones_text = ", ".join([f"{phone}" for phone in purchased_phones])

                # Используем шаблон покупки для замены
                purchase_template = config.get("purchase_template", DEFAULT_PURCHASE_TEMPLATE)
                replace_message = f"🔄 ЗАМЕНА ВЫДАНА 🔄\n\n" + purchase_template.format(
                    phone=phones_text,
                    cd_commands=cd_commands
                )

                # Отправляем сообщение в FunPay чат
                try:
                    if cardinal_instance:
                        send_message_to_buyer(cardinal_instance, final_buyer_username, replace_message)
                        bot.send_message(message.chat.id,
                                         f"✅ Сообщение отправлено покупателю {final_buyer_username} в FunPay чат")
                    else:
                        bot.send_message(message.chat.id, f"⚠️ Cardinal не доступен, сообщение не отправлено в FunPay")

                    # Уведомляем админов
                    admin_notification = (
                        f"Замена выдана для заказа #{order_id}:\n"
                        f"Покупатель: {final_buyer_username}\n"
                        f"Телефоны: {', '.join(purchased_phones)}\n"
                        f"Страна: {country_code}\n"
                        f"Количество: {len(purchased_phones)}"
                    )
                    notify_admins(admin_notification, order_id)

                except Exception as e:
                    bot.send_message(message.chat.id, f"❌ Ошибка при отправке сообщения: {e}")
            else:
                bot.send_message(message.chat.id, f"❌ Не удалось найти пользователя для заказа #{order_id}")
        else:
            bot.send_message(message.chat.id, "❌ Не удалось купить аккаунты для замены.")

        show_tg_settings(message)


def add_country_step2(message: types.Message):
    if message.text is None:
        return

    try:
        bot.delete_message(message.chat.id, message.message_id - 1)
    except Exception:
        pass

    country_code = message.text.strip().upper()
    if country_code in config["countries"]:
        bot.clear_step_handler_by_chat_id(message.chat.id)
        bot.send_message(message.chat.id, "❌ Страна с таким кодом уже существует!")
        return show_tg_settings(message)

    msg = bot.send_message(
        message.chat.id,
        "Введите полное название страны:",
        reply_markup=InlineKeyboardMarkup().add(
            InlineKeyboardButton("🔙 Отмена", callback_data="tg_back_to_main")
        )
    )
    bot.register_next_step_handler(msg, add_country_step3, country_code)


def add_country_step3(message: types.Message, country_code: str):
    if message.text is None:
        return

    try:
        bot.delete_message(message.chat.id, message.message_id - 1)
    except Exception:
        pass

    country_name = message.text.strip()
    msg = bot.send_message(
        message.chat.id,
        "Введите минимальную цену (целое число):",
        reply_markup=InlineKeyboardMarkup().add(
            InlineKeyboardButton("🔙 Отмена", callback_data="tg_back_to_main")
        )
    )
    bot.register_next_step_handler(msg, add_country_step4, country_code, country_name)


def add_country_step4(message: types.Message, country_code: str, country_name: str):
    if message.text is None:
        return

    try:
        bot.delete_message(message.chat.id, message.message_id - 1)
    except Exception:
        pass

    try:
        min_price = int(message.text.strip())
        msg = bot.send_message(
            message.chat.id,
            "Введите максимальную цену (целое число):",
            reply_markup=InlineKeyboardMarkup().add(
                InlineKeyboardButton("🔙 Отмена", callback_data="tg_back_to_main")
            )
        )
        bot.register_next_step_handler(msg, add_country_step5, country_code, country_name, min_price)
    except ValueError:
        bot.clear_step_handler_by_chat_id(message.chat.id)
        bot.send_message(message.chat.id, "❌ Введите корректное целое число!")
        show_tg_settings(message)


def add_country_step5(message: types.Message, country_code: str, country_name: str, min_price: int):
    if message.text is None:
        return

    try:
        bot.delete_message(message.chat.id, message.message_id - 1)
    except Exception:
        pass

    try:
        max_price = int(message.text.strip())
        if max_price < min_price:
            bot.clear_step_handler_by_chat_id(message.chat.id)
            bot.send_message(message.chat.id, "❌ Максимальная цена не может быть меньше минимальной!")
            return show_tg_settings(message)

        config["countries"][country_code] = {
            "name": country_name,
            "min_price": min_price,
            "max_price": max_price
        }
        save_config()

        bot.clear_step_handler_by_chat_id(message.chat.id)
        bot.send_message(
            message.chat.id,
            f"✅ Страна {country_name} ({country_code}) успешно добавлена!"
        )
        show_tg_settings(message)
    except ValueError:
        bot.clear_step_handler_by_chat_id(message.chat.id)
        bot.send_message(message.chat.id, "❌ Введите корректное целое число!")
        show_tg_settings(message)


def process_country_name_edit(message: types.Message, country_code: str):
    if message.text is None:
        return

    try:
        bot.delete_message(message.chat.id, message.message_id - 1)
    except Exception:
        pass

    try:
        new_name = message.text.strip()
        if country_code not in config["countries"]:
            bot.clear_step_handler_by_chat_id(message.chat.id)
            bot.send_message(message.chat.id, f"❌ Ошибка: страна с кодом {country_code} больше не существует!")
            return show_tg_settings(message)

        config["countries"][country_code]["name"] = new_name
        save_config()
        bot.clear_step_handler_by_chat_id(message.chat.id)
        bot.send_message(message.chat.id, f"✅ Название страны {country_code} изменено на {new_name}!")
        show_tg_settings(message)
    except Exception as e:
        bot.clear_step_handler_by_chat_id(message.chat.id)
        bot.send_message(message.chat.id, "❌ Произошла ошибка при сохранении названия страны!")
        show_tg_settings(message)


def process_country_min_edit(message: types.Message, country_code: str):
    if message.text is None:
        return

    try:
        bot.delete_message(message.chat.id, message.message_id - 1)
    except Exception:
        pass

    try:
        if country_code not in config["countries"]:
            bot.clear_step_handler_by_chat_id(message.chat.id)
            bot.send_message(message.chat.id, f"❌ Ошибка: страна с кодом {country_code} больше не существует!")
            return show_tg_settings(message)

        new_min = int(message.text.strip())
        if new_min > config["countries"][country_code]["max_price"]:
            bot.clear_step_handler_by_chat_id(message.chat.id)
            bot.send_message(message.chat.id, "❌ Минимальная цена не может быть больше максимальной!")
            return show_tg_settings(message)

        config["countries"][country_code]["min_price"] = new_min
        save_config()
        bot.clear_step_handler_by_chat_id(message.chat.id)
        bot.send_message(message.chat.id, f"✅ Минимальная цена для страны {country_code} изменена на {new_min}₽!")
        show_tg_settings(message)
    except ValueError:
        bot.clear_step_handler_by_chat_id(message.chat.id)
        bot.send_message(message.chat.id, "❌ Введите корректное целое число!")
        show_tg_settings(message)
    except Exception as e:
        bot.clear_step_handler_by_chat_id(message.chat.id)
        bot.send_message(message.chat.id, "❌ Произошла ошибка при сохранении минимальной цены!")
        show_tg_settings(message)


def process_country_max_edit(message: types.Message, country_code: str):
    if message.text is None:
        return

    try:
        bot.delete_message(message.chat.id, message.message_id - 1)
    except Exception:
        pass

    try:
        if country_code not in config["countries"]:
            bot.clear_step_handler_by_chat_id(message.chat.id)
            bot.send_message(message.chat.id, f"❌ Ошибка: страна с кодом {country_code} больше не существует!")
            return show_tg_settings(message)

        new_max = int(message.text.strip())
        if new_max < config["countries"][country_code]["min_price"]:
            bot.clear_step_handler_by_chat_id(message.chat.id)
            bot.send_message(message.chat.id, "❌ Максимальная цена не может быть меньше минимальной!")
            return show_tg_settings(message)

        config["countries"][country_code]["max_price"] = new_max
        save_config()
        bot.clear_step_handler_by_chat_id(message.chat.id)
        bot.send_message(message.chat.id, f"✅ Максимальная цена для страны {country_code} изменена на {new_max}₽!")
        show_tg_settings(message)
    except ValueError:
        bot.clear_step_handler_by_chat_id(message.chat.id)
        bot.send_message(message.chat.id, "❌ Введите корректное целое число!")
        show_tg_settings(message)
    except Exception as e:
        bot.clear_step_handler_by_chat_id(message.chat.id)
        bot.send_message(message.chat.id, "❌ Произошла ошибка при сохранении максимальной цены!")
        show_tg_settings(message)


def handle_new_order(c: Cardinal, e: NewOrderEvent, *args):
    order_id = e.order.id
    logger.info(f"{LOGGER_PREFIX} Новый заказ #{order_id} добавлен в очередь на обработку")

    order_queue.put({
        'cardinal': c,
        'event': e
    })


def send_message_to_buyer(c: Cardinal, username: str, message: str):
    try:
        chat_id = c.account.get_chat_by_name(username, make_request=True)
        if chat_id:
            c.account.send_message(chat_id.id, message)
            return True
        else:
            return False
    except Exception as e:
        return False


def find_available_accounts(country_code, min_price, max_price):
    available_accounts = []

    try:
        timer = threading.Timer(3.0, lambda: None)
        timer.start()
        timer.join()

        url = f"https://api.lzt.market/telegram?order_by=price_to_up&pmin={min_price}&pmax={max_price}"

        for origin in config["origins"]:
            url += f"&origin[]={origin}"

        # Настройки спам-блока
        if config.get("buy_spamblock", False):
            url += "&spam=yes"
        else:
            url += "&spam=no"

        # Настройки гео-спам-блока
        if config.get("buy_geo_spamblock", False):
            url += "&allow_geo_spamblock=true"
        else:
            url += "&allow_geo_spamblock=false"

        url += f"&password=no&country[]={country_code}"

        headers = {
            "accept": "application/json",
            "authorization": f"Bearer {config['lolz_token']}"
        }

        response = requests.get(url, headers=headers)

        if response.status_code == 200:
            response_data = response.json()

            if 'items' in response_data and response_data['items']:
                items = response_data['items']
                available_accounts = items

    except Exception as e:
        logger.error(f"{LOGGER_PREFIX} Ошибка при поиске аккаунтов: {e}")

    return available_accounts


def purchase_account(item_id):
    try:
        timer = threading.Timer(3.0, lambda: None)
        timer.start()
        timer.join()

        url = f"https://api.lzt.market/{item_id}/fast-buy"

        headers = {
            "accept": "application/json",
            "authorization": f"Bearer {config['lolz_token']}"
        }

        response = requests.post(url, headers=headers)

        if response.status_code == 200:
            result = response.json()
            return result
        else:
            result = response.json()
            return result

    except Exception as e:
        return {"errors": [str(e)]}


def notify_admins(message, order_id=None):
    if not config["administrators"]:
        return

    notification_type = "other"
    if "успешно" in message.lower() or "куплен" in message.lower():
        notification_type = "success"
    elif "ошибка" in message.lower() or "не удалось" in message.lower():
        notification_type = "error"
    elif "запрос кода" in message.lower() or "код" in message.lower():
        notification_type = "code_request"
    elif "возврат" in message.lower() or "refund" in message.lower():
        notification_type = "refund"
    elif "баланс" in message.lower() or "баланса" in message.lower():
        notification_type = "low_balance"

    if not config["notifications"].get(notification_type, True):
        return

    # Создаем красивое уведомление в зависимости от типа
    if notification_type == "success":
        # Уведомление об успешной покупке
        if "Успешно куплен аккаунт" in message:
            # Извлекаем информацию из сообщения
            lines = message.split('\n')
            order_info = {}
            for line in lines:
                if "Заказ:" in line or "Покупатель:" in line or "Телефон:" in line or "Страна:" in line:
                    key, value = line.split(':', 1)
                    order_info[key.strip()] = value.strip()

            formatted_message = f"""🎉 Успешная покупка и выдача аккаунта!

📋 Заказ: {order_info.get('Заказ', 'N/A')}
👤 Покупатель: {order_info.get('Покупатель', 'N/A')}
Телефон: {order_info.get('Телефон', 'N/A')}
🌍 Страна: {order_info.get('Страна', 'N/A')}"""
        else:
            formatted_message = f"✅ {message}"

    elif notification_type == "error":
        formatted_message = f"❌ {message}"

    elif notification_type == "code_request":
        formatted_message = f"🔐 {message}"

    elif notification_type == "refund":
        formatted_message = f"💰 {message}"

    elif notification_type == "low_balance":
        formatted_message = f"⚠️ {message}"

    else:
        formatted_message = f"ℹ️ {message}"

    if order_id and order_id in config["orders_profit"]:
        profit_data = config["orders_profit"][order_id]
        profit_info = (
            f"\n💰 Финансовая информация:\n"
            f"• Цена на FunPay: {profit_data.get('fp_sum', 0)} ₽\n"
            f"• Цена после комиссии: {profit_data.get('fp_net', 0):.2f} ₽\n"
            f"• Цена на LOLZ: {profit_data.get('lolz_cost', 0)} ₽\n"
            f"• Чистая прибыль: {profit_data.get('profit', 0):.2f} ₽"
        )
        formatted_message += profit_info

    for admin_id in config["administrators"]:
        try:
            if order_id:
                kb = InlineKeyboardMarkup()
                kb.add(InlineKeyboardButton("Перейти к заказу", url=f"https://funpay.com/orders/{order_id}/"))
                bot.send_message(admin_id, formatted_message, reply_markup=kb)
            else:
                bot.send_message(admin_id, formatted_message)
        except Exception:
            pass


def get_telegram_codes(item_id):
    max_retries = 10
    retry_delay = 3

    for attempt in range(max_retries):
        try:
            if attempt > 0:
                time.sleep(retry_delay * attempt)

            url = f"https://api.lzt.market/{item_id}/telegram-login-code"

            headers = {
                "accept": "application/json",
                "authorization": f"Bearer {config['lolz_token']}"
            }

            response = requests.get(url, headers=headers)

            if response.status_code == 200:
                result = response.json()
                return result
            else:
                try:
                    result = response.json()

                    if 'errors' in result and 'retry_request' in result['errors']:
                        continue

                except ValueError:
                    pass

                if attempt == max_retries - 1:
                    return None

        except Exception as e:
            if attempt == max_retries - 1:
                return None

    return None


def handle_plus_message(c: Cardinal, e: NewMessageEvent):
    try:
        if not e.message.text or (
                not e.message.text.strip().lower().startswith("cd") and e.message.text.strip() != "+"):
            return

        if e.message.text.strip() == "+":
            return

        user_username = e.message.chat_name
        if is_user_blocked(user_username):
            c.account.send_message(
                e.message.chat_id,
                "❌ Вы заблокированы в системе. Обратитесь к администратору.",
                chat_name=e.message.chat_name
            )
            notify_admins(f"Заблокированный пользователь {user_username} попытался получить код")
            return

        if e.message.text.strip().lower() == "cd":
            user_id = str(e.message.chat_name)
            user_orders_data = load_user_orders()
            user_phones = set()
            if user_id in user_orders_data["user_orders"]:
                for order_data in user_orders_data["user_orders"][user_id].values():
                    if "phone" in order_data:
                        user_phones.add(order_data["phone"])

            next_order, orders = c.account.get_sells()
            user_orders = [order for order in orders if order.buyer_username == e.message.chat_name]

            for order in user_orders:
                if order.id in order_phone_numbers:
                    user_phones.add(order_phone_numbers[order.id])
                for i in range(1, 10):
                    multi_key = f"{order.id}_{i}"
                    if multi_key in order_phone_numbers:
                        user_phones.add(order_phone_numbers[multi_key])

            if user_phones:
                phones_list = ", ".join([f"• {phone}" for phone in sorted(user_phones)])
                message_text = f"Ваши номера:\n\n{phones_list}\n\nДля получения кода отправьте: cd номер"
                c.account.send_message(e.message.chat_id, message_text, chat_name=e.message.chat_name)
            else:
                c.account.send_message(
                    e.message.chat_id,
                    "❌ У вас нет доступных номеров телефонов. Приобретите телеграм аккаунт.",
                    chat_name=e.message.chat_name
                )
            return

        cd_match = re.match(r'^cd\s+(\d+)$', e.message.text.strip(), re.IGNORECASE)
        if not cd_match:
            return

        phone_number = cd_match.group(1)

        user_orders_data = load_user_orders()

        user_id = str(e.message.chat_name)
        if phone_number in user_orders_data["phone_users"] and user_orders_data["phone_users"][phone_number] != user_id:
            c.account.send_message(
                e.message.chat_id,
                f"❌ Номер {phone_number} не принадлежит вам. Вы можете получать коды только для своих номеров.",
                chat_name=e.message.chat_name
            )
            return

        found_order_id = None
        item_id = None

        if user_id in user_orders_data["user_orders"]:
            for order_id, order_data in user_orders_data["user_orders"][user_id].items():
                if order_data.get("phone") == phone_number:
                    found_order_id = order_id
                    item_id = order_data.get("item_id")
                    break

        if not found_order_id:
            next_order, orders = c.account.get_sells()
            user_orders = [order for order in orders if order.buyer_username == e.message.chat_name]

            if not user_orders:
                c.account.send_message(
                    e.message.chat_id,
                    f"❌ Номер {phone_number} не найден среди ваших заказов.",
                    chat_name=e.message.chat_name
                )
                return
            for order in user_orders:
                if order.id in order_phone_numbers and order_phone_numbers[order.id] == phone_number:
                    found_order_id = order.id
                    if order.id in order_account_ids:
                        item_id = order_account_ids[order.id]
                    break
                for i in range(1, 10):
                    multi_key = f"{order.id}_{i}"
                    if multi_key in order_phone_numbers and order_phone_numbers[multi_key] == phone_number:
                        found_order_id = order.id
                        if multi_key in order_account_ids:
                            item_id = order_account_ids[multi_key]
                        break

        if not item_id:
            c.account.send_message(
                e.message.chat_id,
                f"❌ Для номера {phone_number} невозможно получить код. Обратитесь к администратору.",
                chat_name=e.message.chat_name
            )
            notify_admins(f"Запрос кода для номера {phone_number}, но item_id не найден", found_order_id)
            return

        if not config["lolz_token"]:
            c.account.send_message(
                e.message.chat_id,
                "❌ Не настроен токен ... для получения кодов. Администратор свяжется с вами в ближайшее время.",
                chat_name=e.message.chat_name
            )
            notify_admins(f"Запрос кода для номера {phone_number}, но не настроен токен LOLZ", found_order_id)
            return

        c.account.send_message(
            e.message.chat_id,
            "🔄 Запрос кода отправлен. Примерное время ожидания: 5 минут. Наберитесь терпения.",
            chat_name=e.message.chat_name
        )

        codes_data = get_telegram_codes(item_id)

        if not codes_data or 'codes' not in codes_data or not codes_data['codes']:
            c.account.send_message(
                e.message.chat_id,
                f"❌ Не удалось получить код для номера {phone_number}. Код может появиться через несколько минут, попробуйте позже.",
                chat_name=e.message.chat_name
            )
            notify_admins(f"Не удалось получить код для номера {phone_number}, item_id: {item_id}", found_order_id)
            return

        latest_code = codes_data['codes'][0]['code']

        code_template = config.get("code_template", DEFAULT_CODE_TEMPLATE)

        base_order_id = str(found_order_id).split('_')[0]
        order_link = f"https://funpay.com/orders/{base_order_id}/"
        message_text = code_template.format(
            code=latest_code,
            order_link=order_link,
            order_id=found_order_id
        )

        c.account.send_message(
            e.message.chat_id,
            message_text,
            chat_name=e.message.chat_name
        )

        if user_id not in user_orders_data["user_orders"]:
            user_orders_data["user_orders"][user_id] = {}

        if found_order_id not in user_orders_data["user_orders"][user_id]:
            user_orders_data["user_orders"][user_id][found_order_id] = {
                "phone": phone_number,
                "item_id": item_id
            }

        user_orders_data["phone_users"][phone_number] = user_id
        save_user_orders(user_orders_data)

    except Exception as ex:
        try:
            c.account.send_message(
                e.message.chat_id,
                "❌ Произошла техническая ошибка при получении кода. Попробуйте позже или свяжитесь с администратором",
                chat_name=e.message.chat_name
            )
        except Exception:
            pass

        error_details = f"⚠️ Ошибка при обработке запроса кода от {e.message.chat_name}\n"
        error_details += f"Номер: {phone_number if 'phone_number' in locals() else 'неизвестен'}\n"
        error_details += f"Item ID: {item_id if 'item_id' in locals() else 'неизвестен'}\n"
        error_details += f"Ошибка: {str(ex)}"

        notify_admins(f"Ошибка при обработке заказа: {error_details}",
                      found_order_id if 'found_order_id' in locals() else None)


def process_order_queue():
    global active_tasks

    while True:
        try:
            with task_lock:
                can_process = active_tasks < max_concurrent_tasks

            if can_process and not order_queue.empty():
                order_data = order_queue.get()
                cardinal = order_data['cardinal']
                event = order_data['event']

                with task_lock:
                    active_tasks += 1

                future = executor.submit(process_order, cardinal, event)
                future.add_done_callback(lambda f: handle_processing_complete(f))

            time.sleep(0.5)
        except Exception:
            time.sleep(1)


def handle_processing_complete(future):
    global active_tasks

    try:
        result = future.result()
    except Exception:
        pass
    finally:
        with task_lock:
            active_tasks -= 1


def process_order(c: Cardinal, e: NewOrderEvent):
    order_id = e.order.id
    try:
        buyer_username = e.order.buyer_username
        if is_user_blocked(buyer_username):
            try:
                c.account.refund(e.order.id)
                message_text = "❌ Вы заблокированы в системе. Средства возвращены."
                send_message_to_buyer(c, buyer_username, message_text)

                admin_message = f"Заблокированный пользователь {buyer_username} попытался купить аккаунт. Заказ #{order_id} возвращен."
                notify_admins(admin_message, order_id)

                return f"Заблокированный пользователь {buyer_username} попытался купить аккаунт. Заказ #{order_id} возвращен."
            except Exception as ex:
                logger.error(f"{LOGGER_PREFIX} Ошибка при возврате средств заблокированному пользователю: {ex}")
                return f"Ошибка при возврате средств заблокированному пользователю {buyer_username}: {ex}"

        try:
            resp = requests.get("https://api.lzt.market/me", headers={"accept": "application/json",
                                                                      "authorization": f"Bearer {config.get('lolz_token', '')}"},
                                timeout=10)
            api_ok = resp.status_code == 200
        except Exception:
            api_ok = False
        if not api_ok:
            global pending_orders
            if (c, e) not in pending_orders:
                pending_orders.append((c, e))
            send_message_to_buyer(c, e.order.buyer_username,
                                  "⚠️ Временная техническая ошибка\n✨ Ваш аккаунт будет выдан автоматически\n🔄 Система работает над устранением проблемы\n⏰ Максимальное время ожидания: 1 час")
            return f"API не работает, заказ {order_id} поставлен в очередь"

        full_order = c.account.get_order(order_id)
        if hasattr(full_order, 'status') and full_order.status in ['refund', 'cancelled', 'closed']:
            logger.info(f"{LOGGER_PREFIX} Заказ #{order_id} имеет статус {full_order.status}, пропускаем обработку")
            return f"Заказ #{order_id} имеет статус {full_order.status}, обработка пропущена"

        description = e.order.description or ""
        full_desc = full_order.full_description or ""

        has_tg_prefix = False
        tg_match = None

        if 'tg:' in full_desc.lower():
            tg_match = re.search(r'tg:\s*(\w+)', full_desc, re.IGNORECASE)
            if tg_match:
                has_tg_prefix = True

        if not has_tg_prefix and 'tg:' in description.lower():
            tg_match = re.search(r'tg:\s*(\w+)', description, re.IGNORECASE)
            if tg_match:
                has_tg_prefix = True

        if not has_tg_prefix or not tg_match:
            return f"Нет метки 'tg:' в заказе #{order_id}"

        tg_id = tg_match.group(1).upper()

        if tg_id == "DEL":
            try:
                c.account.refund(full_order.id)
                message_text = "❗Данный лот не нужно покупать, данный лот является рекламным, просто напишите какую страну вы хотите купить и какой у вас бюджет.❗"
                send_message_to_buyer(c, e.order.buyer_username, message_text)

                admin_message = f"Автоматический возврат выполнен для заказа #{full_order.id} (рекламный лот DEL)"
                notify_admins(admin_message, full_order.id)

                return f"Автоматический возврат для заказа #{order_id} (рекламный лот DEL)"
            except Exception as ex:
                logger.error(f"{LOGGER_PREFIX} Ошибка при возврате средств для рекламного лота DEL: {ex}")
                return f"Ошибка при возврате средств для заказа #{order_id}: {ex}"

        try:
            if hasattr(e.order, 'parse_amount') and callable(e.order.parse_amount):
                amount = e.order.parse_amount()
            elif hasattr(e.order, 'amount') and e.order.amount is not None:
                amount = e.order.amount
            else:
                amount = 1
                if hasattr(full_order, 'amount') and full_order.amount is not None:
                    amount = full_order.amount

            if amount > 1 and not config.get("multi_account_delivery", False):
                try:
                    c.account.refund(full_order.id)
                    message_text = (
                        "❌Извините, но заказ телеграм аккаунта можно оформлять только в количестве 1 штуки.\n\n"
                        "Ваши средства были автоматически возвращены. Пожалуйста, создайте новый заказ, "
                        "указав количество товара 1 шт.❌"
                    )
                    send_message_to_buyer(c, e.order.buyer_username, message_text)

                    admin_message = f"Автоматический возврат для заказа #{full_order.id} из-за неверного количества товара ({amount})"
                    notify_admins(admin_message, full_order.id)
                    return f"Автоматический возврат для заказа #{order_id} из-за неверного количества товара ({amount})"
                except Exception:
                    pass

            if amount > 1:
                logger.info(
                    f"{LOGGER_PREFIX} Заказ #{order_id} содержит {amount} аккаунтов, начинаем последовательную обработку")
        except Exception:
            pass

        country_info = ""
        country_code = ""
        min_price = 0
        max_price = 0
        for code, country_data in config["countries"].items():
            if tg_id.startswith(code):
                country_code = code
                min_price = country_data['min_price']
                max_price = country_data['max_price']
                break

        if config["lolz_token"]:
            try:
                username, balance = get_lolz_user_info(config["lolz_token"])
                if balance is not None and balance < config["low_balance_threshold"]:
                    notify_admins(
                        f"ВНИМАНИЕ! Баланс на LOLZ Market ({balance:.2f}₽) ниже установленного порога ({config['low_balance_threshold']}₽). Пожалуйста, пополните баланс!")
            except Exception:
                pass

        purchase_template = config.get("purchase_template", DEFAULT_PURCHASE_TEMPLATE)

        message_text = "Спасибо за покупку!"
        purchase_result = None
        account_data = None
        purchased_accounts = []
        total_lolz_cost = 0

        if country_code and config["lolz_token"]:
            try:
                purchase_failed = False
                purchase_success = False
                insufficient_funds = False
                successful_purchases = 0

                max_accounts = amount
                for account_num in range(1, max_accounts + 1):
                    logger.info(f"{LOGGER_PREFIX} Обрабатываем аккаунт {account_num}/{amount} для заказа #{order_id}")

                    available_accounts = find_available_accounts(country_code, min_price, max_price)

                    if available_accounts:
                        purchase_result, account_data, funds_issue = try_purchase_accounts(available_accounts)

                        if funds_issue:
                            insufficient_funds = True
                            purchase_failed = True
                            break

                        if purchase_result and 'item' in purchase_result:
                            item_id = purchase_result['item'].get('item_id')
                            order_account_ids[f"{full_order.id}_{account_num}"] = item_id

                            if account_data and 'telegram_phone' in account_data:
                                phone = account_data['telegram_phone']
                                purchased_accounts.append(phone)
                                order_phone_numbers[f"{full_order.id}_{account_num}"] = phone
                                user_id = str(e.order.buyer_username)
                                user_orders_data = load_user_orders()

                                if user_id not in user_orders_data["user_orders"]:
                                    user_orders_data["user_orders"][user_id] = {}

                                user_orders_data["user_orders"][user_id][f"{full_order.id}_{account_num}"] = {
                                    "phone": phone,
                                    "item_id": item_id
                                }

                                user_orders_data["phone_users"][phone] = user_id
                                save_user_orders(user_orders_data)

                                lolz_cost = purchase_result['item'].get('price', 0)
                                total_lolz_cost += lolz_cost
                                successful_purchases += 1

                                logger.info(f"{LOGGER_PREFIX} Успешно куплен аккаунт {account_num}/{amount}: {phone}")

                            if account_data:
                                admin_notification = (
                                    f"Успешно куплен аккаунт {account_num}/{amount} для заказа: #{full_order.id}:\n"
                                    f"Заказ: #{full_order.id}\n"
                                    f"Покупатель: {e.order.buyer_username}\n"
                                    f"Телефон: +{account_data['telegram_phone']}\n"
                                    f"Страна: {country_code}\n"
                                )
                                notify_admins(admin_notification, full_order.id)

                        else:
                            logger.warning(f"{LOGGER_PREFIX} Не удалось купить аккаунт {account_num}/{amount}")
                            purchase_failed = True
                            break
                    else:
                        logger.warning(f"{LOGGER_PREFIX} Нет доступных аккаунтов для покупки {account_num}/{amount}")
                        purchase_failed = True
                        break

                if successful_purchases > 0:
                    fp_sum = full_order.sum if hasattr(full_order, 'sum') else e.order.price
                    save_order_profit(full_order.id, fp_sum, total_lolz_cost, country_code)

                    if successful_purchases == max_accounts:
                        phones_text = ", ".join([f"{phone}" for phone in purchased_accounts])
                        if successful_purchases == 1:
                            cd_commands = f"cd {purchased_accounts[0]}"
                        else:
                            cd_commands = ", ".join([f"cd {phone}" for phone in purchased_accounts])

                        purchase_template = config.get("purchase_template", DEFAULT_PURCHASE_TEMPLATE)
                        message_text = purchase_template.format(
                            phone=phones_text,
                            cd_commands=cd_commands
                        )
                        purchase_success = True
                    else:
                        phones_text = ", ".join([f"{phone}" for phone in purchased_accounts])
                        if successful_purchases == 1:
                            cd_commands = f"cd {purchased_accounts[0]}"
                        elif successful_purchases > 1:
                            cd_commands = ", ".join([f"cd {phone}" for phone in purchased_accounts])
                        else:
                            cd_commands = "cd номер"

                        purchase_template = config.get("purchase_template", DEFAULT_PURCHASE_TEMPLATE)
                        message_text = f"⚠️ Частично выполнено! Куплено {successful_purchases} из {max_accounts} аккаунтов.\n\n" + purchase_template.format(
                            phone=phones_text,
                            cd_commands=cd_commands
                        )
                        purchase_success = True

                if not purchase_success:
                    if insufficient_funds:
                        if successful_purchases > 0:
                            phones_text = ", ".join([f"{phone}" for phone in purchased_accounts])
                            purchase_template = config.get("purchase_template", DEFAULT_PURCHASE_TEMPLATE)
                            template_part = purchase_template.format(
                                phone=phones_text,
                                cd_commands="cd номер"
                            )
                            message_text = f"⚠️ Частично выполнено! Куплено {successful_purchases} из {amount} аккаунтов.\n\n{template_part}\n\nУвы, произошла ошибка. Чтобы выдать оставшуюся часть аккаунтов вам нужно подождать когда продавец увидит это сообщение."

                            admin_message = f"Частично выполнено для заказа #{full_order.id}: куплено {successful_purchases} из {amount} аккаунтов. Недостаточно средств на балансе LOLZ Market"
                            notify_admins(admin_message, full_order.id)
                        else:
                            try:
                                c.account.refund(full_order.id)
                                message_text = f"К сожалению, произошла ошибка при покупке аккаунтов для страны {country_code}. Средства автоматически возвращены."
                                notify_admins(
                                    f"Автоматический возврат выполнен для заказа #{full_order.id} из-за недостатка средств на балансе LOLZ Market",
                                    full_order.id)
                            except Exception:
                                message_text = f"Спасибо за покупку! Вы приобрели телеграм аккаунт с ID:{tg_id}.{country_info}\n\nВаш заказ принят и будет обработан оператором в ближайшее время."
                                admin_message = f"СРОЧНО! Недостаточно средств на балансе LOLZ Market для обработки заказа #{full_order.id}. Пополните баланс!"
                                notify_admins(admin_message, full_order.id)
                    elif purchase_failed:
                        if successful_purchases > 0:
                            phones_text = ", ".join([f"{phone}" for phone in purchased_accounts])
                            purchase_template = config.get("purchase_template", DEFAULT_PURCHASE_TEMPLATE)
                            template_part = purchase_template.format(
                                phone=phones_text,
                                cd_commands="cd номер"
                            )
                            message_text = f"⚠️ Частично выполнено! Куплено {successful_purchases} из {amount} аккаунтов.\n\n{template_part}\n\nУвы, произошла ошибка. Чтобы выдать оставшуюся часть аккаунтов вам нужно подождать когда продавец увидит это сообщение."

                            admin_message = f"Частично выполнено для заказа #{full_order.id}: куплено {successful_purchases} из {amount} аккаунтов"
                            notify_admins(admin_message, full_order.id)
                        else:
                            message_text = f"Спасибо за покупку! Вы приобрели телеграм аккаунт с ID:{tg_id}.{country_info}\n\nК сожалению, произошла ошибка, все аккаунты кончились"

                            admin_message = f"Не удалось купить ни один аккаунт для заказа #{full_order.id}. Все доступные аккаунты оказались проданы."
                            notify_admins(admin_message, full_order.id)

                            if config["auto_returns"]:
                                try:
                                    c.account.refund(full_order.id)
                                    message_text = f"К сожалению, произошла ошибка при покупке аккаунтов для страны {country_code}. Средства автоматически возвращены."
                                    notify_admins(f"Автоматический возврат выполнен для заказа #{full_order.id}",
                                                  full_order.id)
                                except Exception:
                                    pass
                    else:
                        message_text = f"Спасибо за покупку! Вы приобрели телеграм аккаунт с ID: {tg_id}.{country_info}\n\nВ настоящий момент нет доступных аккаунтов для этой страны. Наш администратор свяжется с вами в ближайшее время."

                        admin_message = f"Нет доступных аккаунтов для заказа #{full_order.id}, страна: {country_code}"
                        notify_admins(admin_message, full_order.id)

                        if config["auto_returns"]:
                            try:
                                c.account.refund(full_order.id)
                                message_text = f"К сожалению, в данный момент нет доступных аккаунтов для страны {country_code}. Средства автоматически возвращены."
                                notify_admins(f"Автоматический возврат выполнен для заказа #{full_order.id}",
                                              full_order.id)
                            except Exception:
                                pass
            except Exception as ex:
                message_text = f"Спасибо за покупку! Вы приобрели телеграм аккаунт с ID: {tg_id}.{country_info}"

                admin_message = f"Ошибка при обработке заказа #{full_order.id}: {ex}"
                notify_admins(admin_message, full_order.id)

                if config["auto_returns"]:
                    try:
                        c.account.refund(full_order.id)
                        message_text = f"К сожалению, произошла техническая ошибка при обработке заказа. Средства автоматически возвращены."
                        notify_admins(f"Автоматический возврат выполнен для заказа #{full_order.id}", full_order.id)
                    except Exception:
                        pass

        send_message_to_buyer(c, e.order.buyer_username, message_text)
        return f"Заказ #{order_id} успешно обработан"

    except Exception as ex:
        return f"Ошибка при обработке заказа #{order_id}: {ex}"


def shutdown():
    global executor
    if executor:
        executor.shutdown(wait=True)


def api_health_check_loop():
    last_api_status = None
    global pending_orders
    while True:
        try:
            if not config.get("auto_deactivate_tg_lots_on_api_fail", False):
                last_api_status = None
                time.sleep(360)
                continue
            # Проверка API
            try:
                resp = requests.get("https://api.lzt.market/me", headers={"accept": "application/json",
                                                                          "authorization": f"Bearer {config.get('lolz_token', '')}"},
                                    timeout=10)
                api_ok = resp.status_code == 200
            except Exception:
                api_ok = False
            # Уведомление о проверке
            if config.get("notify_api_check", False):
                notify_admins(f"[API CHECK] Проверка API: {'OK' if api_ok else 'FAIL'}")
            # Если API восстановился и есть зависшие заказы — обработать их
            if last_api_status is False and api_ok and pending_orders:
                for c, e in pending_orders[:]:
                    try:
                        process_order(c, e)
                        pending_orders.remove((c, e))
                    except Exception as ex:
                        logger.error(f"[API CHECK] Ошибка при обработке зависшего заказа: {ex}")
            last_api_status = api_ok
        except Exception as e:
            logger.error(f"[API CHECK] Ошибка: {e}\n{traceback.format_exc()}")
        time.sleep(360)


BIND_TO_PRE_INIT = [init_commands]
BIND_TO_NEW_MESSAGE = [handle_plus_message]
BIND_TO_NEW_ORDER = [handle_new_order]
BIND_TO_EXIT = [shutdown]
BIND_TO_DELETE = [
    {"pattern": "tg_set_origin_self_registration", "handler": set_origin,
     "description": "Обработчик кнопки выбора происхождения 'Саморег'"},
    {"pattern": "tg_set_origin_self_reg", "handler": set_origin,
     "description": "Обработчик сокращенной кнопки выбора происхождения 'Саморег'"}
]


def is_user_blocked(username):
    """Проверяет, заблокирован ли пользователь"""
    return username in config.get("blocked_users", [])


def add_blocked_user(username):
    """Добавляет пользователя в список заблокированных"""
    if username not in config["blocked_users"]:
        config["blocked_users"].append(username)
        save_config()
        return True
    return False


def remove_blocked_user(username):
    """Удаляет пользователя из списка заблокированных"""
    if username in config["blocked_users"]:
        config["blocked_users"].remove(username)
        save_config()
        return True
    return False


def get_blocked_users():
    """Возвращает список заблокированных пользователей"""
    return config.get("blocked_users", [])


def blocked_users_menu(call: types.CallbackQuery):
    kb = InlineKeyboardMarkup(row_width=1)
    kb.add(InlineKeyboardButton("➕ Добавить заблокированного пользователя", callback_data="tg_add_blocked_user"))

    blocked_users = get_blocked_users()
    if blocked_users:
        for username in blocked_users:
            kb.add(InlineKeyboardButton(
                f"🚫 {username}",
                callback_data=f"tg_remove_blocked_user_{username}"
            ))
    else:
        kb.add(InlineKeyboardButton("📭 Список пуст", callback_data="tg_blocked_users"))

    kb.add(InlineKeyboardButton("🔙 Назад", callback_data="tg_back_to_main"))
    bot.edit_message_text(
        "🚫 Управление заблокированными пользователями:\n\nЗаблокированные пользователи не смогут получать аккаунты при покупке.",
        call.message.chat.id,
        call.message.message_id,
        reply_markup=kb
    )


def add_blocked_user_start(call: types.CallbackQuery):
    msg = bot.edit_message_text(
        "Введите ник пользователя для блокировки:",
        call.message.chat.id,
        call.message.message_id,
        reply_markup=InlineKeyboardMarkup().add(
            InlineKeyboardButton("🔙 Отмена", callback_data="tg_blocked_users")
        )
    )
    bot.register_next_step_handler(msg, process_add_blocked_user)


def process_add_blocked_user(message: types.Message):
    if message.text is None:
        return

    try:
        bot.delete_message(message.chat.id, message.message_id - 1)
    except Exception:
        pass

    username = message.text.strip()
    if add_blocked_user(username):
        bot.clear_step_handler_by_chat_id(message.chat.id)
        bot.send_message(message.chat.id, f"✅ Пользователь {username} заблокирован!")
        show_tg_settings(message)
    else:
        bot.clear_step_handler_by_chat_id(message.chat.id)
        bot.send_message(message.chat.id, f"❌ Пользователь {username} уже заблокирован!")
        show_tg_settings(message)


def remove_blocked_user_confirm(call: types.CallbackQuery):
    username = call.data.split("_")[-1]
    kb = InlineKeyboardMarkup(row_width=2)
    kb.add(
        InlineKeyboardButton("✅ Да", callback_data=f"tg_confirm_remove_blocked_user_{username}"),
        InlineKeyboardButton("❌ Нет", callback_data="tg_blocked_users")
    )

    bot.edit_message_text(
        f"Вы уверены, что хотите разблокировать пользователя {username}?",
        call.message.chat.id,
        call.message.message_id,
        reply_markup=kb
    )


def remove_blocked_user_confirmed(call: types.CallbackQuery):
    username = call.data.split("_")[-1]
    if remove_blocked_user(username):
        bot.answer_callback_query(call.id, f"Пользователь {username} разблокирован!")
    else:
        bot.answer_callback_query(call.id, f"Пользователь {username} не найден в списке заблокированных!")
    blocked_users_menu(call)


def send_disabled_message(chat_id, chat_name=None):
    message_text = (
        "🚫 <b>Плагин отключен</b>\n\n"
        "❌ Данная версия плагина была отключена администратором.\n\n"
        "📞 <b>Для получения обновленной версии свяжитесь с разработчиком:</b>\n"
        "👨‍💻 <b>@LCNPrime</b>\n\n"
        "💬 <b>Причины отключения:</b>\n"
        "• Обновление системы\n"
        "• Техническое обслуживание\n"
        "• Безопасность пользователей\n\n"
        "⏰ <b>Время отключения:</b> {time.strftime('%d.%m.%Y %H:%M:%S')}\n"
        "🔄 <b>Статус:</b> Временно недоступен"
    )

    try:
        if chat_name:
            cardinal_instance.account.send_message(chat_id, message_text, chat_name=chat_name)
        else:
            bot.send_message(chat_id, message_text, parse_mode="HTML")
    except Exception as e:
        logger.error(f"{LOGGER_PREFIX} Ошибка при отправке сообщения об отключении: {e}")