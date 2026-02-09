# -*- coding: utf-8 -*-
from __future__ import annotations

from typing import TYPE_CHECKING, Set
import logging

from FunPayAPI.updater.events import OrderStatusChangedEvent
from FunPayAPI.common.enums import OrderStatuses
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton

if TYPE_CHECKING:
    from fpsupport import funpayautobot as Cardinal

# ═══════════════════════════════════════════════════════════════════════════
# МЕТАДАННЫЕ
# ═══════════════════════════════════════════════════════════════════════════

NAME = "ReviewBonus"
VERSION = "1.0"
DESCRIPTION = "Автоматическая отправка бонусного сообщения после оставления отзыва"
CREDITS = "@EstateGroup0"
UUID = "f8e7d6c5-4b3a-4c2d-9e1f-0a8b7c6d5e4f"
SETTINGS_PAGE = False

logger = logging.getLogger("FunPayAutobot")
LOGGER_PREFIX = "[ReviewBonus]"


class ReviewBonusPlugin:
    def __init__(self) -> None:
        self.running = True
        self.bonus_message = (
            "🎁 Спасибо за отзыв!\n\n"
            "💎 В качестве бонуса получите:\n"
            "✨ Скидку 10% на следующую покупку\n"
            "✨ Промокод: REVIEW10\n\n"
            "Ждём вас снова! ❤️"
        )
        self.send_once_per_user = True
        self.rewarded_users: Set[str] = set()

    # ───────────────────── Обработка отзывов ─────────────────────

    def handle_order_status_changed(
        self, cardinal: "Cardinal", event: OrderStatusChangedEvent, *args
    ) -> None:
        """Обработка подтверждения заказа — отправка бонусного сообщения за отзыв."""
        if not self.running:
            return

        try:
            order = event.order
            
            # Проверяем что заказ подтверждён
            if order.status != OrderStatuses.CLOSED:
                return

            # Получаем полную информацию о заказе
            try:
                full_order = cardinal.account.get_order(order.id)
            except Exception as e:
                logger.error(f"{LOGGER_PREFIX} Ошибка получения заказа #{order.id}: {e}")
                return

            # Проверяем наличие отзыва
            if not hasattr(full_order, 'review') or full_order.review is None:
                logger.info(f"{LOGGER_PREFIX} Заказ #{order.id} подтверждён без отзыва")
                return

            if not hasattr(full_order.review, 'stars') or full_order.review.stars is None:
                logger.info(f"{LOGGER_PREFIX} Заказ #{order.id} — отзыв без звёзд")
                return

            # Получаем username покупателя
            buyer_username = full_order.buyer_username if hasattr(full_order, 'buyer_username') else None
            if not buyer_username:
                buyer_username = order.buyer_username if hasattr(order, 'buyer_username') else "Unknown"

            # Проверяем: отправлять ли повторно этому пользователю
            if self.send_once_per_user and buyer_username.lower() in self.rewarded_users:
                logger.info(
                    f"{LOGGER_PREFIX} Пользователь {buyer_username} уже получал бонус, пропуск"
                )
                return

            logger.info(
                f"{LOGGER_PREFIX} Заказ #{order.id} — отзыв оставлен ({full_order.review.stars}⭐), "
                f"отправляю бонус пользователю {buyer_username}"
            )

            # Отправляем бонусное сообщение
            try:
                cardinal.account.send_message(order.chat_id, self.bonus_message)
                logger.info(f"{LOGGER_PREFIX} ✅ Бонусное сообщение отправлено в чат {order.chat_id}")
                
                # Помечаем пользователя как получившего бонус
                if self.send_once_per_user:
                    self.rewarded_users.add(buyer_username.lower())

            except Exception as e:
                logger.error(f"{LOGGER_PREFIX} Ошибка отправки бонусного сообщения: {e}")

        except Exception as e:
            logger.error(f"{LOGGER_PREFIX} Ошибка обработки заказа: {e}")

    # ───────────────────── Telegram-панель ─────────────────────

    def show_panel(self, cardinal: "Cardinal", chat_id: int) -> None:
        """Показывает панель управления плагином."""
        keyboard = InlineKeyboardMarkup(row_width=2)

        status = "🟢 ВКЛ" if self.running else "🔴 ВЫКЛ"
        once_status = "🟢 ВКЛ" if self.send_once_per_user else "🔴 ВЫКЛ"

        keyboard.row(
            InlineKeyboardButton(f"Статус: {status}", callback_data="rb_show_status"),
            InlineKeyboardButton("🔄 Вкл/Выкл", callback_data="rb_toggle"),
        )
        keyboard.row(
            InlineKeyboardButton(
                f"1 раз на юзера: {once_status}", callback_data="rb_toggle_once"
            ),
        )
        keyboard.row(
            InlineKeyboardButton("✏️ Изменить сообщение", callback_data="rb_edit_message"),
        )
        keyboard.row(
            InlineKeyboardButton("📋 Показать сообщение", callback_data="rb_show_message"),
        )
        keyboard.row(
            InlineKeyboardButton("🗑️ Очистить список", callback_data="rb_clear_rewarded"),
        )

        text = (
            f"🎁 <b>ReviewBonus v{VERSION}</b>\n"
            f"By {CREDITS}\n\n"
            f"📊 <b>Статус:</b> {status}\n"
            f"🔁 <b>1 раз на юзера:</b> {once_status}\n"
            f"👥 <b>Получили бонус:</b> {len(self.rewarded_users)} чел."
        )

        cardinal.telegram.bot.send_message(chat_id, text, reply_markup=keyboard, parse_mode="HTML")

    def setup_callbacks(self, cardinal: "Cardinal") -> None:
        """Регистрирует обработчики кнопок."""
        
        @cardinal.telegram.bot.callback_query_handler(func=lambda c: c.data == "rb_show_status")
        def show_status_btn(call):
            status = "🟢 Включён" if self.running else "🔴 Выключен"
            once_status = "🟢 Да" if self.send_once_per_user else "🔴 Нет"

            info = (
                "<b>📊 Информация о плагине</b>\n\n"
                f"• Статус: {status}\n"
                f"• Отправлять 1 раз: {once_status}\n"
                f"• Получили бонус: {len(self.rewarded_users)} чел.\n\n"
                f"<b>Как работает:</b>\n"
                f"После подтверждения заказа с отзывом,\n"
                f"автоматически отправляет бонусное\n"
                f"сообщение в чат с покупателем.\n\n"
                f"<b>Автор:</b> {CREDITS}"
            )
            cardinal.telegram.bot.send_message(call.message.chat.id, info, parse_mode="HTML")

        @cardinal.telegram.bot.callback_query_handler(func=lambda c: c.data == "rb_toggle")
        def toggle_btn(call):
            self.running = not self.running

            status = "✅ Включен" if self.running else "❌ Выключен"
            cardinal.telegram.bot.answer_callback_query(
                call.id, f"Плагин {status}", show_alert=True
            )
            cardinal.telegram.bot.delete_message(call.message.chat.id, call.message.message_id)
            self.show_panel(cardinal, call.message.chat.id)

        @cardinal.telegram.bot.callback_query_handler(func=lambda c: c.data == "rb_toggle_once")
        def toggle_once_btn(call):
            self.send_once_per_user = not self.send_once_per_user

            status = "✅ Включено" if self.send_once_per_user else "❌ Выключено"
            cardinal.telegram.bot.answer_callback_query(
                call.id, f"Отправка 1 раз на юзера: {status}", show_alert=True
            )
            cardinal.telegram.bot.delete_message(call.message.chat.id, call.message.message_id)
            self.show_panel(cardinal, call.message.chat.id)

        @cardinal.telegram.bot.callback_query_handler(func=lambda c: c.data == "rb_edit_message")
        def edit_message_btn(call):
            msg = cardinal.telegram.bot.send_message(
                call.message.chat.id,
                "✏️ <b>Отправьте новое бонусное сообщение:</b>\n\n"
                "Можете использовать:\n"
                "• Переносы строк\n"
                "• Эмодзи 🎁✨💎\n"
                "• Ссылки https://...\n\n"
                "<b>Пример:</b>\n"
                "🎁 Спасибо за отзыв!\n"
                "Ваш бонус: https://funpay.com/lot/123",
                parse_mode="HTML"
            )
            cardinal.telegram.bot.register_next_step_handler(msg, self.process_new_message, cardinal)

        @cardinal.telegram.bot.callback_query_handler(func=lambda c: c.data == "rb_show_message")
        def show_message_btn(call):
            cardinal.telegram.bot.send_message(
                call.message.chat.id,
                f"📋 <b>Текущее бонусное сообщение:</b>\n\n"
                f"<code>{self.bonus_message}</code>\n\n"
                f"Для изменения нажмите ✏️ Изменить сообщение",
                parse_mode="HTML"
            )

        @cardinal.telegram.bot.callback_query_handler(func=lambda c: c.data == "rb_clear_rewarded")
        def clear_rewarded_btn(call):
            count = len(self.rewarded_users)
            self.rewarded_users.clear()
            cardinal.telegram.bot.answer_callback_query(
                call.id,
                f"🗑️ Список очищен! ({count} пользователей)\nТеперь все могут получить бонус снова.",
                show_alert=True
            )
            cardinal.telegram.bot.delete_message(call.message.chat.id, call.message.message_id)
            self.show_panel(cardinal, call.message.chat.id)

    def process_new_message(self, message, cardinal: "Cardinal") -> None:
        """Обрабатывает новое бонусное сообщение от пользователя."""
        new_message = message.text.strip()
        
        if not new_message:
            cardinal.telegram.bot.send_message(
                message.chat.id,
                "❌ Сообщение не может быть пустым!"
            )
            return

        # Ограничение длины сообщения
        if len(new_message) > 1000:
            cardinal.telegram.bot.send_message(
                message.chat.id,
                f"❌ Сообщение слишком длинное!\n"
                f"Максимум: 1000 символов\n"
                f"У вас: {len(new_message)} символов"
            )
            return

        self.bonus_message = new_message

        cardinal.telegram.bot.send_message(
            message.chat.id,
            f"✅ <b>Бонусное сообщение обновлено!</b>\n\n"
            f"Новое сообщение:\n\n"
            f"<code>{self.bonus_message}</code>",
            parse_mode="HTML"
        )

    # ───────────────────── Инициализация ─────────────────────

    def init_plugin(self, cardinal: "Cardinal") -> None:
        """Инициализация плагина."""
        logger.info(f"{LOGGER_PREFIX} {NAME} v{VERSION} by {CREDITS}")

        @cardinal.telegram.bot.message_handler(commands=["reviewbonus"])
        def panel(m):
            self.show_panel(cardinal, m.chat.id)

        self.setup_callbacks(cardinal)
        
        logger.info(f"{LOGGER_PREFIX} ✅ Плагин загружен")
        logger.info(f"{LOGGER_PREFIX} Статус: {'🟢 Включён' if self.running else '🔴 Выключен'}")
        logger.info(f"{LOGGER_PREFIX} Команда: /reviewbonus")


# ═══════════════════════════════════════════════════════════════════════════
# МОДУЛЬНЫЕ ПРИВЯЗКИ
# ═══════════════════════════════════════════════════════════════════════════

PLUGIN = ReviewBonusPlugin()


def init_plugin(cardinal: "Cardinal") -> None:
    PLUGIN.init_plugin(cardinal)


def handle_order_status_changed(cardinal: "Cardinal", event: OrderStatusChangedEvent, *args) -> None:
    PLUGIN.handle_order_status_changed(cardinal, event, *args)


BIND_TO_PRE_INIT = [init_plugin]
BIND_TO_ORDER_STATUS_CHANGED = [handle_order_status_changed]
BIND_TO_DELETE = []