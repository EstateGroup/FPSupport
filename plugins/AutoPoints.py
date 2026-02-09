import json
import os
import re
import logging
import time
from telebot import types
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton

from FunPayAPI.types import Order
import requests

#API_BASE_URL = "http://45.129.128.225:8080/api"

import requests

class APIClient:
    def __init__(self, api_key, base_url="https://api.buysteampoints.com/api/buy"):
        self.base_url = base_url
        self.api_key = api_key

    def purchase_points(self, steam_link: str, points: int) -> dict:
        payload = {
            "api_key": self.api_key,
            "puan": points,
            "steam_link": steam_link
        }
        resp = requests.post(self.base_url, json=payload, timeout=60)
        resp.raise_for_status()
        return resp.json()





def is_valid_link(link: str) -> (bool, str):
    """
    Проверяет, что ссылка ведёт на профиль Steam в формате
    https://steamcommunity.com/id/ВашID или
    https://steamcommunity.com/profiles/7656119XXXXXXXXXX
    """
    pattern = r'^https?://steamcommunity\.com/(?:id|profiles)/[A-Za-z0-9_-]+/?$'
    if re.match(pattern, link):
        return True, ""
    return False, "❌ Неверная ссылка на профиль Steam. Формат должен быть:\n" \
                 "https://steamcommunity.com/id/ВашID или\n" \
                 "https://steamcommunity.com/profiles/7656119XXXXXXXXXX"

def load_config() -> dict:
    """
    Возвращает текущую конфигурацию из глобальной переменной config.
    """
    return config




# === Метаданные плагина ===
NAME = "SteamAutoPoints"
VERSION = "0.3"
DESCRIPTION = "Telegram меню + автоответ на заказы Steam очков Настройка - /steam_points_settings"
CREDITS = "@jonnycashout_bot // https://t.me/CashoutNews"
UUID = "d3b07384-9e7b-4f6a-b834-123456abcdef"
SETTINGS_PAGE = False

# === Глобальные переменные ===
bot = None
cardinal = None
api_client = None
config = {}
received_messages = []  # сюда будем складывать все новые сообщения
pending_steam_links = {}           # chat_id -> order_id
pending_confirmations = {}         # chat_id -> ссылка
confirmed_steam_links = {}         # order_id -> ссылка (для API)


CONFIG_PATH = "storage/autopoints/config.json"
DEFAULT_CONFIG = {
    "api_key": "",
    "auto_refunds": False,
    "managers": []
}

logger = logging.getLogger("FPC.autopoints")

# === Конфиг ===
def ensure_config():
    if not os.path.exists("storage/autopoints"):
        os.makedirs("storage/autopoints")

    if not os.path.exists(CONFIG_PATH):
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(DEFAULT_CONFIG, f, indent=4, ensure_ascii=False)

    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)

def save_config():
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=4, ensure_ascii=False)




# === Главное меню ===
def main_menu():
    kb = InlineKeyboardMarkup(row_width=2)
    kb.add(
        InlineKeyboardButton("🔑 API KEY", callback_data="ap_set_api_key"),
        InlineKeyboardButton(f"Auto-refunds: {'✅' if config.get('auto_refunds') else '❌'}", callback_data="ap_toggle_refunds")
    )
    kb.add(
        InlineKeyboardButton("💰 Баланс", callback_data="ap_check_balance"),
        InlineKeyboardButton("📦 Заказы", callback_data="ap_manage_orders")
    )
    kb.add(
        InlineKeyboardButton("📈 Отчёты", callback_data="ap_stats_reports"),
        InlineKeyboardButton("💼 Офферы", callback_data="ap_offers")
    )
    kb.add(
        InlineKeyboardButton("🤖 Авто-покупки", callback_data="ap_auto_purchase"),
    )
    kb.add(
        InlineKeyboardButton("❓ Помощь", callback_data="ap_help"),
        InlineKeyboardButton("⚙️ Конфиг backup", callback_data="ap_config_backup")
    )
    kb.add(
        InlineKeyboardButton(
        text="Наш Telegram-канал",  # Надпись на кнопке
        url="https://t.me/fpc_plugins_ivagakura"  # Ссылка на канал
    )
    )
    return kb

# === Команда: /steam_points_settings ===
def handle_command(message: types.Message):
    if message.text == "/steam_points_settings":
        bot.send_message(message.chat.id, "Добро пожаловать в панель управления Steam Points!", reply_markup=main_menu())

# === Callback обработка ===
def handle_callback(call: types.CallbackQuery):
    data = call.data
    user_id = call.from_user.id
    
    if data.startswith("confirm_steam_link_yes:"):
        chat_id = data.split(":")[1]
        link = steam_links_confirmations.get(chat_id)
        if link:
            # здесь можно сохранить в config или базу
            logger.info(f"[autopoints] ✅ Ссылка подтверждена: {link} для чата {chat_id}")
            c.account.send_message(chat_id, "Спасибо! Ссылка сохранена.")
            steam_links_confirmations.pop(chat_id, None)
    elif data.startswith("confirm_steam_link_no:"):
        chat_id = data.split(":")[1]
        c.account.send_message(chat_id, "Пожалуйста, введите ссылку заново.")
        pending_steam_links[chat_id] = "WAITING"
        steam_links_confirmations.pop(chat_id, None)

    if data == "ap_set_api_key":
        msg = bot.send_message(user_id, "Пожалуйста, отправьте мне ваш API-KEY.")
        bot.register_next_step_handler(msg, receive_api_key)
    elif data == "ap_toggle_refunds":
        config["auto_refunds"] = not config.get("auto_refunds", False)
        save_config()
        bot.edit_message_reply_markup(call.message.chat.id, call.message.message_id, reply_markup=main_menu())
        bot.answer_callback_query(call.id, "Статус авто-возвратов обновлён.")
    elif data == "ap_check_balance":
        bot.send_message(user_id, "💰 Ваш баланс: 0₽ (заглушка)")
    elif data == "ap_manage_orders":
        bot.send_message(user_id, "📦 Управление заказами: [заглушка]")
    elif data == "ap_stats_reports":
        bot.send_message(user_id, "📈 Отчёты: [заглушка]")
    elif data == "ap_offers":
        bot.send_message(user_id, "💼 Управление офферами: [заглушка]")
    elif data == "ap_auto_purchase":
        bot.send_message(user_id, "🤖 Настройки автопокупок: [заглушка]")
    elif data == "ap_show_logs":
        log_path = "bot.log"
        if os.path.exists(log_path):
            with open(log_path, "r", encoding="utf-8") as f:
                lines = f.readlines()[-20:]
            bot.send_message(user_id, "📝 Последние логи:\n" + "".join(lines))
        else:
            bot.send_message(user_id, "📝 Логи не найдены.")
    elif data == "ap_config_backup":
        bot.send_document(user_id, open(CONFIG_PATH, "rb"))
    elif data == "ap_help":
        bot.send_message(user_id, (
            "🧾 Команды:\n"
            "🔑 API KEY — задать API\n"
            "✅ Auto-refunds — вкл/выкл\n"
            "💰 Баланс — проверить\n"
            "📦 Заказы — управление\n"
            "📈 Отчёты — статистика\n"
            "💼 Офферы — товары\n"
            "🤖 Авто-покупки — конфиг\n"
            "📝 Логи — последние записи\n"
            "⚙️ Config backup — выгрузить"
        ))
    else:
        bot.answer_callback_query(call.id, "⚠️ Недоступно или неизвестная команда.")

# === Получение API KEY ===
def receive_api_key(message: types.Message):
    config["api_key"] = message.text.strip()
    save_config()
    bot.send_message(message.chat.id, "✅ API-KEY сохранён!")

# === Обработка новых заказов ===
# ... (импорты, переменные, конфиг и is_valid_link как у тебя)

waiting_for_link = {}

def handle_new_order(c, event):
    order_id = event.order.id
    order = event.order
    logger.info(f"[autopoints] 📦 Новый заказ получен: Order ID={order_id}")

    # 1) Получаем полный объект заказа
    try:
        full_order = c.account.get_order(order_id)
        logger.info(f"[autopoints] 🔍 Детали заказа #{full_order.id} загружены")
    except Exception as e:
        logger.error(f"[autopoints] ❌ Не удалось получить full_order для #{order_id}: {e}")
        return

    # 2) Извлекаем chat_id
    if hasattr(full_order, "chat_id"):
        chat_id = full_order.chat_id
    elif hasattr(full_order, "chat") and hasattr(full_order.chat, "id"):
        chat_id = full_order.chat.id
    else:
        logger.error(f"[autopoints] ❌ Не удалось определить chat_id для заказа #{order_id}")
        return

    # 3) Извлекаем buyer_id
    buyer_id = getattr(full_order, "buyer_id", None)
    if buyer_id is None:
        logger.error(f"[autopoints] ❌ Не удалось определить buyer_id для заказа #{order_id}")
        return

    # 4) Отсекаем все заказы, кроме Steam Points
    if order.subcategory.id != 714:
        return


    # 4) Проверяем количество штук — должно быть кратно 100
    qty = (
        getattr(full_order, "quantity", None)
        or getattr(full_order, "count", None)
        or getattr(full_order, "amount", None)
    )
    if qty is None:
        logger.error(f"[autopoints] ❌ Не удалось получить количество в заказе #{order_id}")
        c.account.send_message(chat_id, "❌ Ошибка: не удалось определить количество купленных штук.")
        try:
            c.account.refund(order_id)
            logger.info(f"[autopoints] 🔄 Заказ #{order_id} отменён — нет данных по количеству.")
        except Exception as e:
            logger.error(f"[autopoints] ❌ Ошибка отмены заказа #{order_id}: {e}")
        return

    if qty % 100 != 0:
        logger.error(f"[autopoints] ❌ Некратное количество ({qty}) для заказа #{order_id}")
        c.account.send_message(
            chat_id,
            f"❌ Количество ({qty}) должно быть кратно 100. Заказ будет отменён."
        )
        try:
            c.account.refund(order_id)
            logger.info(f"[autopoints] 🔄 Заказ #{order_id} отменён — некратное количество.")
        except Exception as e:
            logger.error(f"[autopoints] ❌ Ошибка отмены заказа #{order_id}: {e}")
        return

    # 5) Всё ок — сохраняем состояние и просим ссылку
    waiting_for_link[order_id] = {
        'order_id': order_id,
        "buyer_id": buyer_id,
        "step": "await_link",
        "chat_id": chat_id,
        "qty":     qty   # <-- запоминаем, сколько точек покупаем
    }
    c.account.send_message(chat_id, "⁡Укажите пожалуйста ссылку на Ваш профиль Steam.\nНапример: https://steamcommunity.com/id/nickname или другие ссылки, но они должны вести на профиль (не приглашения в друзья!)")
    logger.info(
        f"[autopoints] ✅ Запрос ссылки отправлен. Ожидаем ответ от {buyer_id} в чате {chat_id}"
    )


def handle_new_message(c, event):
    msg = event.message
    # сохраним это сообщение в нашем списке
    received_messages.append(msg)
    # оставим только последние N (чтобы список не рос безгранично)
    if len(received_messages) > 1000:
        received_messages.pop(0)
    chat_id = getattr(msg, "chat_id", None)
    text = getattr(msg, "content", None) or getattr(msg, "text", None)
    author_id = getattr(msg, "author_id", None)

    logger.warning(f"[autopoints] 🚨 handle_new_message вызван. ChatID={chat_id}, AuthorID={author_id}")
    if text is None or chat_id is None or author_id is None:
        logger.error("[autopoints] ❌ Недостаточно данных для обработки сообщения.")
        return

    text = text.replace("\u2061", "").strip()
    logger.info(f"[autopoints] 📥 Сообщение от чата {chat_id}: {text}")

    for order_id, data in waiting_for_link.items():
        if data["buyer_id"] == author_id:
            if data["step"] == "await_link":
                link_match = re.search(r'(https?://\S+)', text)
                if not link_match:
                    c.account.send_message(chat_id, "❌ Неверная ссылка, повторите...")
                    return
                link = link_match.group(0)
                ok, reason = is_valid_link(link)
                if not ok:
                    c.account.send_message(chat_id, reason)
                    return

                data["link"] = link
                cfg = load_config()
                confirm_link = cfg.get("confirm_link", True)

                if confirm_link:
                    data["step"] = "await_confirm"
                    c.account.send_message(chat_id, f"✅ Ссылка принята: {link}\nПодтвердите: + / -")
                    return
                else:
                    process_link_without_confirmation(c, data)
                    return

            elif data["step"] == "await_confirm":
                if text.lower() == "+":
                    process_link_without_confirmation(c, data)
                    return
                elif text.lower() == "-":
                    data["step"] = "await_link"
                    c.account.send_message(chat_id, "❌ Подтверждение отклонено. Введите другую ссылку.")
                    return
                else:
                    c.account.send_message(chat_id, "❌ Используйте + или -. Повторите.")
                    return


def process_link_without_confirmation(c, data):
    chat_id = data["chat_id"]
    link    = data["link"]
    qty     = data.get("qty", 0)
    order_id = data["order_id"]

    # Уведомляем пользователя
    c.account.send_message(
        chat_id,
        f"📨 Ссылка подтверждена: {link}\n"
        f"Начинаю отправку {qty} очков…\n\nСреднее время завершения: 3 минуты."
    )
    logger.info(f"[autopoints] ✅ Подтверждена ссылка {link}, qty={qty}")

    # Собственно API–запрос
    try:
        result = api_client.purchase_points(link, qty)
        time.sleep(150)
        c.account.send_message(
            chat_id,
            f"✅ Успешно отправлено {qty} очков! Не забудьте подтвердить заказ https://funpay.com/orders/{order_id}/ и оставить отзыв, займет секунду, а мне будет приятно ❤️\n"
            f"Ссылка для просмотра количества очков: {link}/awards/"
        )
        logger.info(f"[autopoints] 🌐 API purchase response: {result}")
    except Exception as e:
        logger.error(f"[autopoints] ❌ Ошибка API purchase_points: {e}")
        c.account.send_message(
            chat_id,
            "❌ Не удалось совершить покупку очков через API. Попробуйте позже."
        )
        try:
            c.account.refund(order_id)
            logger.info(f"[autopoints] 🔄 Средства за заказ #{order_id} возвращены покупателю.")
        except Exception as refund_err:
            logger.error(f"[autopoints] ❌ Ошибка при возврате средств за #{order_id}: {refund_err}")
    finally:
        # убираем из ожидающих
        # находим ключ по объекту data
        order_id = next(k for k, v in waiting_for_link.items() if v is data)
        del waiting_for_link[order_id]



# === Инициализация плагина ===
def init_commands(c):
    global bot, cardinal, config, api_client
    cardinal = c
    bot = c.telegram.bot
    config = ensure_config()

    api_client = APIClient(api_key=config.get("api_key"))
    logger.info(f"[autopoints] 🔑 Использую API-KEY: {config.get('api_key')!r}")

    bot.register_message_handler(handle_command, commands=["steam_points_settings"])
    bot.register_callback_query_handler(handle_callback, func=lambda call: call.data.startswith("ap_"))
    bot.register_callback_query_handler(handle_callback, func=lambda call: call.data.startswith("confirm_steam_link_"))


# === Bindings ===
BIND_TO_PRE_INIT    = [init_commands]
BIND_TO_NEW_ORDER   = [handle_new_order]
BIND_TO_NEW_MESSAGE = [handle_new_message]
BIND_TO_DELETE      = []
