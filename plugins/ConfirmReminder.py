from __future__ import annotations
import json
import time
import logging
import telebot

from threading import Thread
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from fpsupport import funpayautobot as Cardinal

from bs4 import BeautifulSoup as bs
from FunPayAPI.updater.events import NewMessageEvent, NewOrderEvent, OrderStatusChangedEvent
from FunPayAPI.types import MessageTypes, OrderStatuses
from FunPayAPI.common.exceptions import RequestFailedError
from locales.localizer import Localizer
from os.path import exists

import tg_bot.static_keyboards
from tg_bot import CBT

logger = logging.getLogger("FPC.confirm_reminder")
localizer = Localizer()
_ = localizer.translate

NAME = "Order Confirmation Reminder"
VERSION = "2.3.0"
DESCRIPTION = (
    "Отправляет только одно напоминание о подтверждении заказа после заданной задержки, "
    "если покупатель его не подтвердил."
)
CREDITS = "@exfador"
UUID = "d21a77a0-a7da-47dd-84b3-3cf77c9ad8a6"
SETTINGS_PAGE = True

CBT_TOGGLE_TIME_UNIT  = "ConfRem_ToggleTimeUnit"
CBT_EDIT_DELAY        = "ConfRem_EditDelay"
CBT_EDIT_MESSAGE      = "ConfRem_EditMessage"
CBT_TG_REMINDS_NOTIFY = "ConfRem_ToggleRemindsNotify"
CBT_WAITING_INPUT     = "ConfRem_WaitingInput"
CBT_CANCEL_INPUT      = "ConfRem_CancelInput"

CONFIG_PATH = "storage/plugins/confirm_reminder.json"
CACHE_PATH  = "storage/plugins/confirm_reminder_cache.json"
STATE_PATH  = "storage/plugins/confirm_reminder_state.json"

# Основные настройки, которые доступны пользователю
SETTINGS: dict = {
    "time_unit": 0,  # 0=сек, 1=мин, 2=ч, 3=дни
    "reminder_text": "Заказ выполнен. Пожалуйста, зайдите в раздел «Покупки», выберите его в списке и нажмите кнопку «Подтвердить выполнение заказа». \n\nПодтвердите тут -> https://funpay.com/orders/{order_id}/",
    "tg_reminders_notify": True,
    "tg_reminders_chats": []
}

# Кэш (параметры, редактируемые пользователем, но не требующие отдельного поля в SETTINGS)
CACHED: dict = {
    "reminder_after": 1  # задержка до отправки напоминания
}

# Храним заказы, которым нужно отправить одно напоминание
# ACTIVE_ORDERS = {
#   "order_id": {
#       "chat_id": int,
#       "buyer_name": str,
#       "next_reminder_time": float,
#       "status": str
#   }
# }
ACTIVE_ORDERS: dict = {}

FINAL_STATUSES = [OrderStatuses.CLOSED, OrderStatuses.REFUNDED]


def load_settings():
    if exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
            SETTINGS.update(data)
        except Exception as e:
            logger.warning(f"[ConfirmReminder] Ошибка чтения SETTINGS: {e}")


def save_settings():
    try:
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(SETTINGS, f, indent=4, ensure_ascii=False)
    except Exception as e:
        logger.warning(f"[ConfirmReminder] Ошибка записи SETTINGS: {e}")


def load_cache():
    if exists(CACHE_PATH):
        try:
            with open(CACHE_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
            CACHED.update(data)
        except Exception as e:
            logger.warning(f"[ConfirmReminder] Ошибка чтения CACHED: {e}")


def save_cache():
    try:
        with open(CACHE_PATH, "w", encoding="utf-8") as f:
            json.dump(CACHED, f, indent=4, ensure_ascii=False)
    except Exception as e:
        logger.warning(f"[ConfirmReminder] Ошибка записи CACHED: {e}")


def load_state():
    if exists(STATE_PATH):
        try:
            with open(STATE_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
            ACTIVE_ORDERS.update(data)
        except Exception as e:
            logger.warning(f"[ConfirmReminder] Ошибка чтения STATE: {e}")


def save_state():
    try:
        with open(STATE_PATH, "w", encoding="utf-8") as f:
            json.dump(ACTIVE_ORDERS, f, indent=4, ensure_ascii=False)
    except Exception as e:
        logger.warning(f"[ConfirmReminder] Ошибка записи STATE: {e}")


def to_seconds(value: int, time_unit: int) -> int:
    if time_unit == 1:
        return value * 60
    elif time_unit == 2:
        return value * 3600
    elif time_unit == 3:
        return value * 86400
    return value


def time_unit_label(unit: int) -> str:
    return {0: "секунды", 1: "минуты", 2: "часы", 3: "дни"}.get(unit, "???")


def safe_send_message(cardinal: Cardinal, chat_id: int, text: str, attempts: int = 3) -> bool:
    for i in range(attempts):
        try:
            cardinal.send_message(chat_id, text)
            return True
        except RequestFailedError as e:
            if e.status_code == 502:
                logger.warning(f"[ConfirmReminder] 502 Bad Gateway (попытка {i + 1}/{attempts})...")
                time.sleep(3)
                continue
            else:
                raise
        except Exception:
            raise
    return False


def on_new_order(cardinal: Cardinal, event: NewOrderEvent):
    order_shortcut = event.order
    full_order = cardinal.get_order_from_object(order_shortcut)
    if not full_order or not full_order.chat_id:
        return

    buyer_name = f"ID{full_order.buyer_id}"
    if full_order.buyer_id:
        try:
            profile = cardinal.account.get_user(full_order.buyer_id)
            if profile.username:
                buyer_name = profile.username
        except:
            pass

    sec_delay = to_seconds(CACHED["reminder_after"], SETTINGS["time_unit"])
    now = time.time()

    ACTIVE_ORDERS[str(full_order.id)] = {
        "chat_id": full_order.chat_id,
        "buyer_name": buyer_name,
        "next_reminder_time": now + sec_delay,
        "status": str(full_order.status)
    }
    save_state()
    logger.info(f"[ConfirmReminder] Новый заказ #{full_order.id} (покупатель: {buyer_name}).")


def on_order_status_changed(cardinal: Cardinal, event: OrderStatusChangedEvent):
    order_shortcut = event.order
    full_order = cardinal.get_order_from_object(order_shortcut)
    if not full_order:
        return
    oid = str(full_order.id)

    if oid not in ACTIVE_ORDERS:
        return

    if full_order.status in FINAL_STATUSES:
        ACTIVE_ORDERS.pop(oid, None)
        save_state()
        logger.info(f"[ConfirmReminder] Заказ #{oid} убран (статус: {full_order.status}).")
    else:
        ACTIVE_ORDERS[oid]["status"] = str(full_order.status)
        save_state()


def reminder_loop(cardinal: Cardinal):
    while True:
        now = time.time()
        for oid, data in list(ACTIVE_ORDERS.items()):
            if data["status"] != str(OrderStatuses.PAID):
                ACTIVE_ORDERS.pop(oid, None)
                save_state()
                continue

            if now >= data["next_reminder_time"]:
                txt = SETTINGS["reminder_text"].format(order_id=oid)
                chat_id = data["chat_id"]
                try:
                    if safe_send_message(cardinal, chat_id, txt, attempts=3):
                        logger.info(f"[ConfirmReminder] Напоминание для #{oid} отправлено.")
                        if SETTINGS["tg_reminders_notify"] and SETTINGS["tg_reminders_chats"]:
                            notify_txt = (
                                f"Напоминание отправлено по заказу #{oid}\n"
                                f"Покупатель: {data['buyer_name']}"
                            )
                            kb = telebot.types.InlineKeyboardMarkup()
                            kb.add(
                                telebot.types.InlineKeyboardButton(
                                    "Открыть заказ", url=f"https://funpay.com/orders/{oid}/"
                                )
                            )
                            for c_id in SETTINGS["tg_reminders_chats"]:
                                try:
                                    cardinal.telegram.bot.send_message(
                                        c_id, notify_txt, parse_mode="HTML", reply_markup=kb
                                    )
                                except Exception as e:
                                    logger.warning(
                                        f"[ConfirmReminder] Ошибка уведомления чата {c_id}: {e}"
                                    )
                    else:
                        logger.warning(f"[ConfirmReminder] 502 => не смогли отправить #{oid} (3 попытки).")
                except Exception as ex:
                    logger.warning(f"[ConfirmReminder] Ошибка при отправке напоминания для #{oid}: {ex}")

                ACTIVE_ORDERS.pop(oid, None)
                save_state()
        time.sleep(5)


def init(cardinal: Cardinal):
    load_settings()
    load_cache()
    load_state()
    Thread(target=reminder_loop, args=(cardinal,), daemon=True).start()
    if cardinal.telegram:
        register_telegram_handlers(cardinal.telegram, cardinal)


def register_telegram_handlers(tg, cardinal: Cardinal):
    bot = tg.bot

    def cancel_input(call: telebot.types.CallbackQuery):
        tg.clear_state(call.message.chat.id, call.from_user.id, True)
        open_settings(call)

    def on_waiting_input(message: telebot.types.Message):
        st_data = tg.get_state(message.chat.id, message.from_user.id)["data"]
        param_name = st_data.get("param")
        tg.clear_state(message.chat.id, message.from_user.id, True)
        if not param_name:
            bot.reply_to(message, "Неизвестная настройка.")
            return

        val = message.text.strip()
        if param_name == "reminder_after":
            if not val.isdigit():
                bot.reply_to(message, "Введите целое число.")
                return
            CACHED[param_name] = int(val)
            save_cache()
        elif param_name == "reminder_text":
            SETTINGS["reminder_text"] = val
            save_settings()
        else:
            bot.reply_to(message, "Неизвестная настройка.")
            return

        kb = telebot.types.InlineKeyboardMarkup()
        kb.add(
            telebot.types.InlineKeyboardButton(
                "⬅️ Вернуться в меню",
                callback_data=f"{CBT.PLUGIN_SETTINGS}:{UUID}"
            )
        )
        bot.reply_to(message, "Настройка обновлена.", reply_markup=kb)

    tg.msg_handler(on_waiting_input, func=lambda m: tg.check_state(m.chat.id, m.from_user.id, CBT_WAITING_INPUT))

    def ask_user_for_param(call: telebot.types.CallbackQuery, param_name: str, prompt: str):
        kb = telebot.types.InlineKeyboardMarkup()
        kb.add(telebot.types.InlineKeyboardButton("❌ Отмена", callback_data=CBT_CANCEL_INPUT))
        bot.edit_message_text(prompt, call.message.chat.id, call.message.id, reply_markup=kb)
        tg.set_state(call.message.chat.id, call.message.id, call.from_user.id, CBT_WAITING_INPUT, {"param": param_name})

    def toggle_time_unit(call: telebot.types.CallbackQuery):
        old_val = SETTINGS["time_unit"]
        new_val = (old_val + 1) % 4
        SETTINGS["time_unit"] = new_val
        save_settings()
        open_settings(call)

    def toggle_tg_reminds_notify(call: telebot.types.CallbackQuery):
        old_val = SETTINGS["tg_reminders_notify"]
        SETTINGS["tg_reminders_notify"] = not old_val
        chat_id = call.message.chat.id
        if SETTINGS["tg_reminders_notify"]:
            if chat_id not in SETTINGS["tg_reminders_chats"]:
                SETTINGS["tg_reminders_chats"].append(chat_id)
            bot.answer_callback_query(call.id, "Уведомления о напоминаниях: включены.")
        else:
            if chat_id in SETTINGS["tg_reminders_chats"]:
                SETTINGS["tg_reminders_chats"].remove(chat_id)
            bot.answer_callback_query(call.id, "Уведомления о напоминаниях: выключены.")
        save_settings()
        open_settings(call)

    def edit_delay(call: telebot.types.CallbackQuery):
        ask_user_for_param(call, "reminder_after", "Введите задержку (целое число).")

    def edit_message(call: telebot.types.CallbackQuery):
        ask_user_for_param(call, "reminder_text", "Введите текст напоминания.")

    def open_settings(call: telebot.types.CallbackQuery):
        kb = telebot.types.InlineKeyboardMarkup()
        tu_label = time_unit_label(SETTINGS["time_unit"])
        delay_val = CACHED["reminder_after"]
        msg_short = SETTINGS["reminder_text"]
        if len(msg_short) > 30:
            msg_short = msg_short[:30] + "..."

        kb.row(
            telebot.types.InlineKeyboardButton(
                f"Единица времени: {tu_label}",
                callback_data=CBT_TOGGLE_TIME_UNIT
            )
        )
        kb.row(
            telebot.types.InlineKeyboardButton(
                f"⏰ Задержка: {delay_val} ({tu_label})",
                callback_data=CBT_EDIT_DELAY
            )
        )
        kb.row(
            telebot.types.InlineKeyboardButton(
                f"✏️ Текст: {msg_short}",
                callback_data=CBT_EDIT_MESSAGE
            )
        )

        notify_symbol = "🟢" if SETTINGS["tg_reminders_notify"] else "🔴"
        kb.row(
            telebot.types.InlineKeyboardButton(
                f"{notify_symbol} Уведомления о напоминаниях",
                callback_data=CBT_TG_REMINDS_NOTIFY
            )
        )

        kb.row(
            telebot.types.InlineKeyboardButton(
                "◀️ Назад",
                callback_data=f"{CBT.EDIT_PLUGIN}:{UUID}:0"
            )
        )

        text = (
            "<b>Настройки «Order Confirmation Reminder»</b>\n\n"
            f"<i>"
            f"• Единица времени: {tu_label}\n"
            f"• Задержка (после оплаты): {delay_val}\n"
            f"• Текст напоминания:\n{SETTINGS['reminder_text']}\n\n"
            f"• Уведомления о напоминаниях: {'ВКЛ' if SETTINGS['tg_reminders_notify'] else 'ВЫКЛ'}"
            "</i>"
        )
        try:
            bot.edit_message_text(text, call.message.chat.id, call.message.id, parse_mode="HTML", reply_markup=kb)
            bot.answer_callback_query(call.id)
        except telebot.apihelper.ApiTelegramException as e:
            if "message to edit not found" in str(e):
                bot.send_message(call.message.chat.id, text, parse_mode="HTML", reply_markup=kb)

    tg.cbq_handler(open_settings,         func=lambda c: f"{CBT.PLUGIN_SETTINGS}:{UUID}" in c.data)
    tg.cbq_handler(cancel_input,          func=lambda c: CBT_CANCEL_INPUT in c.data)
    tg.cbq_handler(edit_delay,           func=lambda c: c.data == CBT_EDIT_DELAY)
    tg.cbq_handler(edit_message,         func=lambda c: c.data == CBT_EDIT_MESSAGE)
    tg.cbq_handler(toggle_time_unit,     func=lambda c: c.data == CBT_TOGGLE_TIME_UNIT)
    tg.cbq_handler(toggle_tg_reminds_notify, func=lambda c: c.data == CBT_TG_REMINDS_NOTIFY)


BIND_TO_PRE_INIT = [init]
BIND_TO_NEW_ORDER = [on_new_order]
BIND_TO_ORDER_STATUS_CHANGED = [on_order_status_changed]
BIND_TO_NEW_MESSAGE = [lambda c, e: None]
BIND_TO_DELETE = None
