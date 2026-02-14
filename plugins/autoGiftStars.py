# -*- coding: utf-8 -*-
from __future__ import annotations

from typing import TYPE_CHECKING, Dict, Optional

import asyncio
import json
import logging
import os
import random
import re
import time
from threading import Thread

from FunPayAPI.updater.events import NewOrderEvent, OrderStatusChangedEvent
from FunPayAPI.common.enums import OrderStatuses
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton

# Импорт Pyrogram (работает и с pyrofork и с обычным pyrogram)
try:
    from pyrogram import Client as PyroClient
    PYROGRAM_AVAILABLE = True
except ImportError:
    PyroClient = None
    PYROGRAM_AVAILABLE = False

if TYPE_CHECKING:
    from fpsupport import funpayautobot as Cardinal

# ═══════════════════════════════════════════════════════════════════════════
# МЕТАДАННЫЕ
# ═══════════════════════════════════════════════════════════════════════════

NAME = "StarsGifter"
VERSION = "4.3"
DESCRIPTION = "Автоматическая отправка звёзд через подарки Telegram"
CREDITS = "@Scwee_xz"
UUID = "298845c5-9c90-4912-b599-7ca26f94a7b1"
SETTINGS_PAGE = False

CONFIG_FILE = "plugins/starsgifter_config.json"
DEFAULT_CONFIG = {
    "lot_stars_mapping": {},
    "description_to_stars": {
        "13": 15,
        "21": 25,
        "43": 50,
        "85": 100,
    },
    "random_gifts": {
        "100": [5168043875654172773, 5170690322832818290, 5170521118301225164],
        "50": [5170144170496491616, 5170314324215857265, 5170564780938756245, 6028601630662853006],
        "25": [5170250947678437525, 5168103777563050263],
        "15": [5170145012310081615, 5170233102089322756],
    },
    "plugin_enabled": True,
    "review_gift_enabled": True,
    "review_gift_stars": 15,
    "anonymous_sending": True,
    "auto_clear_chat": True,
    "clear_chat_delay": 60,
    "pyrogram": {
        "api_id": 0,
        "api_hash": "",
        "phone_number": "",
        "session_name": "starsgifter_session",
    },
}

logger = logging.getLogger("FunPayAutobot")
LOGGER_PREFIX = "[StarsGifter]"


class StarsGifterPlugin:
    def __init__(self) -> None:
        self.config = self.load_config()
        self.lot_stars_mapping = {
            str(k): int(v) for k, v in self.config.get("lot_stars_mapping", {}).items()
        }
        self.random_gifts = {
            int(k): v
            for k, v in self.config.get("random_gifts", DEFAULT_CONFIG["random_gifts"]).items()
        }
        self.running = self.config.get("plugin_enabled", True)
        self.review_gift_enabled = self.config.get("review_gift_enabled", False)
        self.review_gift_stars = self.config.get("review_gift_stars", 15)
        self.anonymous_sending = self.config.get("anonymous_sending", True)
        self.auto_clear_chat = self.config.get("auto_clear_chat", True)
        self.clear_chat_delay = self.config.get("clear_chat_delay", 60)
        self.description_to_stars = {
            int(k): int(v)
            for k, v in self.config.get(
                "description_to_stars", DEFAULT_CONFIG["description_to_stars"]
            ).items()
        }
        self.pyrogram_client = None
        self._loop = None  # Выделенный event loop для Pyrogram
        self._loop_thread = None  # Поток для event loop
        self.sent_orders: Dict[str, str] = {}  # order_id -> tg_username
        self.review_gifted_users: set = set()  # юзеры, уже получившие бонус за отзыв

    # ───────────────────── Конфиг ─────────────────────

    @staticmethod
    def load_config() -> Dict:
        os.makedirs(os.path.dirname(CONFIG_FILE), exist_ok=True)
        if not os.path.exists(CONFIG_FILE):
            with open(CONFIG_FILE, "w", encoding="utf-8") as f:
                json.dump(DEFAULT_CONFIG, f, indent=4, ensure_ascii=False)
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            return json.load(f)

    @staticmethod
    def save_config(cfg: Dict) -> None:
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=4, ensure_ascii=False)

    def persist_config(self) -> None:
        self.save_config(self.config)

    # ───────────────────── Деактивация лотов ─────────────────────

    def deactivate_lots_in_categories(self, cardinal: "Cardinal", categories: list) -> None:
        """Деактивирует все лоты в указанных категориях."""
        try:
            profile = cardinal.account.get_user(cardinal.account.id)
            lots = profile.get_lots()
            
            deactivated_count = 0
            for lot in lots:
                if lot.subcategory.id in categories and lot.active:
                    try:
                        lot_fields = cardinal.account.get_lot_fields(lot.id)
                        lot_fields.active = False
                        cardinal.account.save_lot(lot_fields)
                        logger.info(f"{LOGGER_PREFIX} 🔴 Деактивирован лот #{lot.id} (категория {lot.subcategory.id})")
                        deactivated_count += 1
                    except Exception as e:
                        logger.error(f"{LOGGER_PREFIX} Ошибка деактивации лота #{lot.id}: {e}")
            
            if deactivated_count > 0:
                logger.info(f"{LOGGER_PREFIX} Всего деактивировано лотов: {deactivated_count}")
            else:
                logger.info(f"{LOGGER_PREFIX} Лотов для деактивации не найдено в категориях {categories}")
        except Exception as e:
            logger.error(f"{LOGGER_PREFIX} Ошибка получения лотов для деактивации: {e}")


    # ───────────────────── Pyrogram ─────────────────────

    def get_pyrogram_client(self):
        """Создаёт клиент Pyrogram."""
        if PyroClient is None:
            raise RuntimeError(
                "Pyrogram не установлен! Установите: pip install pyrogram"
            )

        pyrogram_config = self.config.get("pyrogram", DEFAULT_CONFIG["pyrogram"])
        
        logger.info(f"{LOGGER_PREFIX} Создаю Pyrogram клиент...")
        
        return PyroClient(
            pyrogram_config["session_name"],
            api_id=pyrogram_config["api_id"],
            api_hash=pyrogram_config["api_hash"],
            phone_number=pyrogram_config.get("phone_number", ""),
        )

    def _run_loop(self):
        """Запускает event loop в фоновом потоке (крутится бесконечно)."""
        asyncio.set_event_loop(self._loop)
        logger.info(f"{LOGGER_PREFIX} Event loop запущен в фоновом потоке")
        self._loop.run_forever()

    def init_pyrogram(self) -> bool:
        pyrogram_config = self.config.get("pyrogram", DEFAULT_CONFIG["pyrogram"])

        if not pyrogram_config.get("api_id") or not pyrogram_config.get("api_hash"):
            logger.warning(f"{LOGGER_PREFIX} API ID или API HASH не установлены")
            return False

        try:
            # Создаём выделенный event loop
            self._loop = asyncio.new_event_loop()
            
            # Запускаем loop в отдельном потоке
            self._loop_thread = Thread(target=self._run_loop, daemon=True)
            self._loop_thread.start()
            
            # Даём потоку время на запуск
            time.sleep(0.5)

            # Создаём клиент Pyrogram
            self.pyrogram_client = self.get_pyrogram_client()
            
            # Запускаем клиент в нашем loop
            async def start_client():
                await self.pyrogram_client.start()
                logger.info(f"{LOGGER_PREFIX} Pyrogram клиент успешно авторизован")
                
                # Проверяем наличие метода send_gift
                if not hasattr(self.pyrogram_client, 'send_gift'):
                    logger.error(
                        f"{LOGGER_PREFIX} ❌ КРИТИЧЕСКАЯ ОШИБКА: Метод send_gift не найден!\n"
                        f"{LOGGER_PREFIX} Установите Pyrofork: pip install pyrofork"
                    )
                    return False
                else:
                    logger.info(f"{LOGGER_PREFIX} ✅ Метод send_gift доступен")
                    
                return True
            
            future = asyncio.run_coroutine_threadsafe(start_client(), self._loop)
            result = future.result(timeout=30)
            
            if not result:
                logger.error(f"{LOGGER_PREFIX} Метод send_gift недоступен")
                return False

            logger.info(f"{LOGGER_PREFIX} Pyrogram запущен и готов к работе")
            return True
            
        except Exception as e:
            logger.error(f"{LOGGER_PREFIX} Ошибка инициализации Pyrogram: {e}")
            logger.debug(f"{LOGGER_PREFIX} TRACEBACK", exc_info=True)
            return False

    # ───────────────────── Парсинг описания ─────────────────────

    @staticmethod
    def _extract_username(description: str) -> Optional[str]:
        """Извлекает username из описания заказа.
        Форматы: 
        - '... По username, @ReaLNey67, ...' (с @)
        - '... По username, maximtt66, ...' (без @)
        - '... https://t.me/username ...' (t.me ссылка)
        """
        # Сначала пробуем найти t.me ссылку
        match = re.search(r'https?://t\.me/(\w{3,})', description, re.IGNORECASE)
        if match:
            return match.group(1)
        
        # Потом пробуем найти с @
        match = re.search(r'@(\w{3,})', description)
        if match:
            return match.group(1)
        
        # Если не нашли с @, ищем после "username," или "username "
        match = re.search(r'username[,\s]+(\w{3,})', description, re.IGNORECASE)
        if match:
            return match.group(1)
        
        return None

    @staticmethod
    def _extract_stars_count(description: str) -> Optional[int]:
        """Извлекает количество звёзд из описания.
        Форматы: '13 звёзд', '21 звезда', '43 звезды', '85 звёзд'
        Поддерживает все формы слова 'звезда' и варианты ё/е
        """
        # Ищем число + любую форму слова "звезда" (звезда/звезды/звёзд/звезд)
        match = re.search(r'(\d+)\s*зв[её]зд[аыу]?', description, re.IGNORECASE)
        if match:
            return int(match.group(1))
        return None

    def _determine_stars_to_send(self, desc_stars: int) -> Optional[int]:
        """Определяет количество звёзд для отправки.
        Сначала ищет в description_to_stars (13→15, 21→25, 43→50, 85→100),
        потом проверяет точное количество, иначе None.
        """
        # Точный маппинг из конфига: 13→15, 21→25, 43→50, 85→100
        if desc_stars in self.description_to_stars:
            return self.description_to_stars[desc_stars]
        # Если число само по себе валидное (кратно 15/25/50/100)
        if self.calc_gifts_quantity(desc_stars):
            return desc_stars
        return None

    # ───────────────────── Расчёт и отправка ─────────────────────

    @staticmethod
    def calc_gifts_quantity(quantity: int) -> Optional[Dict[int, int]]:
        """Расчёт комбинации подарков (15/25/50/100)."""
        for d in range(quantity // 100, -1, -1):
            remain_after_100 = quantity - d * 100
            for c in range(remain_after_100 // 50, -1, -1):
                remain_after_50 = remain_after_100 - c * 50
                for b in range(remain_after_50 // 25, -1, -1):
                    remain_after_25 = remain_after_50 - b * 25
                    if remain_after_25 % 15 == 0:
                        a = remain_after_25 // 15
                        return {100: d, 50: c, 25: b, 15: a}
        return None

    @staticmethod
    def format_gifts_result(gifts_dict: Dict[int, int]) -> str:
        """Форматирование результата отправки."""
        result = []
        for price, count in sorted(gifts_dict.items(), reverse=True):
            if count > 0:
                if count == 1:
                    result.append(f"{count} подарок по {price} звёзд")
                elif 2 <= count <= 4:
                    result.append(f"{count} подарка по {price} звёзд")
                else:
                    result.append(f"{count} подарков по {price} звёзд")
        return "\n".join(result)

    async def _send_gifts_async(
        self,
        username: str,
        stars_count: int,
    ) -> tuple:
        """Отправляет подарки асинхронно. Возвращает (distribution, success, failed)."""
        gifts_distribution = self.calc_gifts_quantity(stars_count)
        if not gifts_distribution:
            return None, 0, 0

        # Проверяем наличие метода send_gift
        if not hasattr(self.pyrogram_client, 'send_gift'):
            logger.error(
                f"{LOGGER_PREFIX} Метод send_gift не найден! "
                f"Установите Pyrofork: pip uninstall pyrogram && pip install pyrofork"
            )
            return gifts_distribution, 0, -3  # -3 = метод не найден

        try:
            user = await self.pyrogram_client.get_users(username)
            if not user:
                return gifts_distribution, 0, -1  # -1 = user not found
        except Exception as e:
            logger.error(f"{LOGGER_PREFIX} Ошибка поиска @{username}: {e}")
            return gifts_distribution, 0, -2  # -2 = search error

        success_count = 0
        failed_count = 0

        for price, count in gifts_distribution.items():
            for _ in range(count):
                try:
                    gift_id = random.choice(self.random_gifts[price])
                    
                    # ФИКС БАГ 3: Используем hide_my_name вместо is_private
                    await self.pyrogram_client.send_gift(
                        chat_id=username,
                        gift_id=gift_id,
                        hide_my_name=self.anonymous_sending
                    )
                    
                    success_count += 1
                    logger.info(f"{LOGGER_PREFIX} Отправлен подарок {price}⭐ → @{username}")
                    await asyncio.sleep(2)
                    
                except Exception as e:
                    logger.error(f"{LOGGER_PREFIX} Ошибка отправки подарка {price}⭐: {e}")
                    failed_count += 1

        return gifts_distribution, success_count, failed_count

    async def _clear_chat_history_async(self, username: str) -> bool:
        """Очищает историю чата с пользователем (для обеих сторон)."""
        try:
            # Удаляем всю историю чата
            await self.pyrogram_client.delete_chat_history(
                chat_id=username,
                revoke=True  # Удаляет для обеих сторон
            )
            logger.info(f"{LOGGER_PREFIX} ✅ История чата с @{username} очищена")
            return True
        except Exception as e:
            logger.error(f"{LOGGER_PREFIX} Ошибка очистки чата с @{username}: {e}")
            return False

    def _run_on_pyrogram_loop(self, coro, timeout=120):
        """Запускает корутину на выделенном event loop.
        ФИКС БАГ 1: Увеличен таймаут до 120 секунд и добавлена проверка loop
        """
        if self._loop is None:
            raise RuntimeError("Event loop не создан")
        
        if not self._loop.is_running():
            raise RuntimeError("Event loop не запущен")
        
        future = asyncio.run_coroutine_threadsafe(coro, self._loop)
        
        try:
            result = future.result(timeout=timeout)
            return result
        except asyncio.TimeoutError:
            logger.error(f"{LOGGER_PREFIX} Таймаут выполнения корутины ({timeout}s)")
            raise

    def _send_order_gifts_with_refund(
        self,
        cardinal: "Cardinal",
        username: str,
        stars_count: int,
        chat_id,
        order_id: str,
        amount: int,
        stars_per_lot: int,
    ) -> None:
        """Отправка подарков с проверкой баланса и автовозвратом."""
        try:
            if self.pyrogram_client is None:
                cardinal.account.send_message(chat_id, "❌ Pyrogram клиент не подключен")
                logger.error(f"{LOGGER_PREFIX} Pyrogram клиент = None")
                return
            
            if self._loop is None or not self._loop.is_running():
                cardinal.account.send_message(chat_id, "❌ Event loop не запущен")
                logger.error(f"{LOGGER_PREFIX} Event loop не работает")
                return

            logger.info(f"{LOGGER_PREFIX} Начинаю отправку {stars_count}⭐ → @{username}")
            
            # Пытаемся отправить подарки
            distribution, success, failed = self._run_on_pyrogram_loop(
                self._send_gifts_async(username, stars_count)
            )

            if distribution is None:
                cardinal.account.send_message(chat_id, "❌ Ошибка расчёта подарков")
                return
            if failed == -1:
                cardinal.account.send_message(chat_id, f"❌ Пользователь @{username} не найден")
                return
            if failed == -2:
                cardinal.account.send_message(chat_id, f"❌ Ошибка поиска @{username}")
                return
            if failed == -3:
                cardinal.account.send_message(
                    chat_id,
                    "❌ Метод отправки подарков не доступен!\n"
                    "Установите Pyrofork:\n"
                    "pip uninstall pyrogram\n"
                    "pip install pyrofork"
                )
                return

            # Проверяем: отправлено ли всё
            if failed > 0:
                # НЕ ВСЁ ОТПРАВЛЕНО — ВОЗВРАТ СРЕДСТВ + ДЕАКТИВАЦИЯ
                logger.error(
                    f"{LOGGER_PREFIX} ❌ Не удалось отправить все подарки "
                    f"({success} из {success + failed}), возврат средств"
                )
                
                # Делаем возврат
                try:
                    cardinal.account.refund_order(order_id)
                    logger.info(f"{LOGGER_PREFIX} 💰 Возврат средств по заказу #{order_id} выполнен")
                    
                    # Отправляем сообщение
                    refund_msg = (
                        f"❌ Извините, не хватает баланса звёзд!\n\n"
                        f"Запрошено: {stars_count} звёзд ({amount} × {stars_per_lot})\n\n"
                        f"💰 Средства возвращены на ваш счёт.\n"
                        f"Приносим извинения за неудобства!"
                    )
                    cardinal.account.send_message(chat_id, refund_msg)
                    
                    # Деактивируем лоты в категориях 2418 и 3064
                    self.deactivate_lots_in_categories(cardinal, [2418, 3064])
                    
                except Exception as refund_error:
                    logger.error(f"{LOGGER_PREFIX} Ошибка возврата средств: {refund_error}")
                    cardinal.account.send_message(
                        chat_id,
                        f"❌ Не удалось отправить подарки.\n"
                        f"Пожалуйста, свяжитесь с продавцом для возврата средств."
                    )
                
                return

            # УСПЕШНО ОТПРАВЛЕНО ВСЁ
            report = f"✅ Отправлено: {stars_count} звёзд\n\n" + self.format_gifts_result(distribution)
            cardinal.account.send_message(chat_id, report)

            review_msg = (
                "✅ Звезды отправлены на ваш аккаунт!\n\n"
                "❤️ Подтвердите заказ и напишите отзыв."
                f"\n✨ https://funpay.com/orders/{order_id}/"
            )
            cardinal.account.send_message(chat_id, review_msg)
            logger.info(f"{LOGGER_PREFIX} Заказ #{order_id} успешно выполнен!")
            
            # Автоочистка чата
            if self.auto_clear_chat:
                logger.info(
                    f"{LOGGER_PREFIX} Запланирована очистка чата с @{username} "
                    f"через {self.clear_chat_delay} секунд"
                )
                
                def delayed_clear():
                    time.sleep(self.clear_chat_delay)
                    try:
                        self._run_on_pyrogram_loop(
                            self._clear_chat_history_async(username),
                            timeout=30
                        )
                    except Exception as e:
                        logger.error(f"{LOGGER_PREFIX} Ошибка отложенной очистки чата: {e}")
                
                Thread(target=delayed_clear, daemon=True, name=f"ClearChat-{username}").start()

        except asyncio.TimeoutError:
            cardinal.account.send_message(chat_id, "❌ Таймаут отправки подарков (>2 мин)")
            logger.error(f"{LOGGER_PREFIX} Таймаут отправки звёзд @{username}")
        except Exception as e:
            cardinal.account.send_message(chat_id, f"❌ Ошибка отправки: {type(e).__name__}")
            err_msg = f"{type(e).__name__}: {e}" if str(e) else type(e).__name__
            logger.error(f"{LOGGER_PREFIX} Ошибка отправки звёзд: {err_msg}")

    def _send_order_gifts(
        self,
        cardinal: "Cardinal",
        username: str,
        stars_count: int,
        chat_id,
        order_id: str,
    ) -> None:
        """Отправка подарков за заказ (запускается в отдельном потоке)."""
        try:
            if self.pyrogram_client is None:
                cardinal.account.send_message(chat_id, "❌ Pyrogram клиент не подключен")
                logger.error(f"{LOGGER_PREFIX} Pyrogram клиент = None")
                return
            
            if self._loop is None or not self._loop.is_running():
                cardinal.account.send_message(chat_id, "❌ Event loop не запущен")
                logger.error(f"{LOGGER_PREFIX} Event loop не работает")
                return

            logger.info(f"{LOGGER_PREFIX} Начинаю отправку {stars_count}⭐ → @{username}")
            
            distribution, success, failed = self._run_on_pyrogram_loop(
                self._send_gifts_async(username, stars_count)
            )

            if distribution is None:
                cardinal.account.send_message(chat_id, "❌ Ошибка расчёта подарков")
                return
            if failed == -1:
                cardinal.account.send_message(chat_id, f"❌ Пользователь @{username} не найден")
                return
            if failed == -2:
                cardinal.account.send_message(chat_id, f"❌ Ошибка поиска @{username}")
                return
            if failed == -3:
                cardinal.account.send_message(
                    chat_id,
                    "❌ Метод отправки подарков не доступен!\n"
                    "Установите Pyrofork:\n"
                    "pip uninstall pyrogram\n"
                    "pip install pyrofork"
                )
                return

            report = f"✅ Отправлено: {stars_count} звёзд\n\n" + self.format_gifts_result(distribution)
            if failed > 0:
                report += f"\n\n❌ Не удалось: {failed}"
            cardinal.account.send_message(chat_id, report)

            if failed == 0:
                review_msg = (
                    "✅ Звезды отправлены на ваш аккаунт!\n\n"
                    "❤️ Подтвердите заказ и напишите отзыв."
                    f"\n✨ https://funpay.com/orders/{order_id}/"
                )
                cardinal.account.send_message(chat_id, review_msg)
                logger.info(f"{LOGGER_PREFIX} Заказ #{order_id} успешно выполнен!")
                
                # Автоочистка чата через заданное время
                if self.auto_clear_chat:
                    logger.info(
                        f"{LOGGER_PREFIX} Запланирована очистка чата с @{username} "
                        f"через {self.clear_chat_delay} секунд"
                    )
                    
                    def delayed_clear():
                        time.sleep(self.clear_chat_delay)
                        try:
                            self._run_on_pyrogram_loop(
                                self._clear_chat_history_async(username),
                                timeout=30
                            )
                        except Exception as e:
                            logger.error(f"{LOGGER_PREFIX} Ошибка отложенной очистки чата: {e}")
                    
                    Thread(target=delayed_clear, daemon=True, name=f"ClearChat-{username}").start()


        except asyncio.TimeoutError:
            err_msg = "⏱️ Таймаут отправки подарков (превышено время ожидания)"
            logger.error(f"{LOGGER_PREFIX} {err_msg}")
            try:
                cardinal.account.send_message(chat_id, f"❌ {err_msg}")
            except Exception:
                pass
                
        except Exception as e:
            err_msg = f"{type(e).__name__}: {e}" if str(e) else type(e).__name__
            logger.error(f"{LOGGER_PREFIX} Ошибка отправки подарков: {err_msg}")
            logger.debug(f"{LOGGER_PREFIX} TRACEBACK", exc_info=True)
            try:
                cardinal.account.send_message(chat_id, f"❌ Ошибка: {err_msg}")
            except Exception:
                pass

    def _send_review_gifts(
        self,
        cardinal: "Cardinal",
        username: str,
        chat_id,
        order_id: str,
    ) -> None:
        """Отправка бонуса за отзыв (запускается в отдельном потоке)."""
        try:
            # Проверяем: юзер уже получал бонус за отзыв — не отправляем повторно
            username_lower = username.lower()
            if username_lower in self.review_gifted_users:
                logger.info(
                    f"{LOGGER_PREFIX} @{username} уже получал бонус за отзыв, пропуск"
                )
                return

            # Ждём чтобы FunPay сохранил отзыв
            time.sleep(3)

            full_order = cardinal.account.get_order(order_id)
            if not full_order.review or full_order.review.stars is None:
                logger.info(f"{LOGGER_PREFIX} Нет отзыва для #{order_id}, бонус не отправляется")
                return

            if self.pyrogram_client is None or self._loop is None or not self._loop.is_running():
                logger.warning(f"{LOGGER_PREFIX} Pyrogram не подключен, бонус за отзыв не отправлен")
                return

            logger.info(
                f"{LOGGER_PREFIX} Отправка бонуса {self.review_gift_stars} звёзд за отзыв -> @{username}"
            )
            cardinal.account.send_message(
                chat_id,
                f"🎁 Спасибо за отзыв! Отправляю бонус {self.review_gift_stars} звёзд..."
            )

            distribution, success, failed = self._run_on_pyrogram_loop(
                self._send_gifts_async(username, self.review_gift_stars)
            )

            if distribution and success > 0:
                # Помечаем юзера — бонус отправлен, повторно не отправляем
                self.review_gifted_users.add(username_lower)
                cardinal.account.send_message(
                    chat_id,
                    f"🎁 Бонус за отзыв: {self.review_gift_stars} звёзд отправлено!"
                )
            elif failed > 0:
                cardinal.account.send_message(
                    chat_id,
                    f"❌ Не удалось отправить бонус за отзыв"
                )

        except Exception as e:
            err_msg = f"{type(e).__name__}: {e}" if str(e) else type(e).__name__
            logger.error(f"{LOGGER_PREFIX} Ошибка бонуса за отзыв #{order_id}: {err_msg}")
        finally:
            self.sent_orders.pop(order_id, None)

    # ───────────────────── Хэндлеры событий ─────────────────────

    def handle_new_order(self, cardinal: "Cardinal", event: NewOrderEvent, *args) -> None:
        """Обработка нового заказа — автоматическая отправка звёзд."""
        if not self.running:
            return

        try:
            order = event.order
            order_id = order.id
            chat_id = order.chat_id
            description = order.description

            logger.info(f"{LOGGER_PREFIX} Новый заказ #{order_id} | {description}")

            # Извлекаем @username из описания
            username = self._extract_username(description)
            if not username:
                logger.info(f"{LOGGER_PREFIX} Username не найден в описании #{order_id}, пропуск")
                return

            # Извлекаем количество звёзд (теперь работает со всеми формами)
            desc_stars = self._extract_stars_count(description)
            if not desc_stars:
                logger.info(f"{LOGGER_PREFIX} Звёзды не найдены в описании #{order_id}, пропуск")
                return

            # Получаем количество заказанных лотов
            amount = order.amount if hasattr(order, "amount") and order.amount else 1
            
            # Рассчитываем общее количество звёзд
            stars_per_lot = self._determine_stars_to_send(desc_stars)
            if not stars_per_lot:
                logger.warning(
                    f"{LOGGER_PREFIX} Невозможно определить кол-во звёзд для {desc_stars} (заказ #{order_id})"
                )
                return

            total_stars = stars_per_lot * amount
            
            logger.info(
                f"{LOGGER_PREFIX} Распознано: @{username}, {desc_stars} звёзд × {amount} шт = {total_stars} звёзд"
            )

            # Проверяем можно ли собрать такое количество подарков
            gifts_distribution = self.calc_gifts_quantity(total_stars)
            if not gifts_distribution:
                cardinal.account.send_message(
                    chat_id,
                    f"❌ Невозможно собрать комбинацию для {total_stars} звёзд.\n"
                    f"Пожалуйста, свяжитесь с продавцом."
                )
                logger.error(f"{LOGGER_PREFIX} Невозможно собрать {total_stars} звёзд из подарков")
                return

            # Сохраняем для бонуса за отзыв
            self.sent_orders[order_id] = username

            logger.info(
                f"{LOGGER_PREFIX} Автоотправка {total_stars}⭐ → @{username} (заказ #{order_id}, {amount} шт)"
            )
            cardinal.account.send_message(
                chat_id,
                f"✨ Спасибо за заказ!\n"
                f"🚀 Отправляю {total_stars} звёзд ({amount} × {stars_per_lot}) на @{username}..."
            )

            # Отправка в отдельном потоке чтобы не блокировать другие хэндлеры
            Thread(
                target=self._send_order_gifts_with_refund,
                args=(cardinal, username, total_stars, chat_id, order_id, amount, stars_per_lot),
                daemon=True,
            ).start()

        except Exception as e:
            logger.error(f"{LOGGER_PREFIX} Ошибка обработки заказа: {e}")
            logger.debug(f"{LOGGER_PREFIX} TRACEBACK", exc_info=True)
            if not stars_to_send:
                logger.warning(
                    f"{LOGGER_PREFIX} Невозможно определить кол-во звёзд для {desc_stars} (заказ #{order_id})"
                )
                return

            # Сохраняем для бонуса за отзыв
            self.sent_orders[order_id] = username

            logger.info(
                f"{LOGGER_PREFIX} Автоотправка {stars_to_send}⭐ → @{username} (заказ #{order_id})"
            )
            cardinal.account.send_message(
                chat_id,
                f"✨ Спасибо за заказ!\n🚀 Отправляю {stars_to_send} звёзд на @{username}..."
            )

            # Отправка в отдельном потоке чтобы не блокировать другие хэндлеры
            Thread(
                target=self._send_order_gifts,
                args=(cardinal, username, stars_to_send, chat_id, order_id),
                daemon=True,
            ).start()

        except Exception as e:
            logger.error(f"{LOGGER_PREFIX} Ошибка обработки заказа: {e}")
            logger.debug(f"{LOGGER_PREFIX} TRACEBACK", exc_info=True)

    def handle_order_status_changed(
        self, cardinal: "Cardinal", event: OrderStatusChangedEvent, *args
    ) -> None:
        """Обработка подтверждения заказа — бонус за отзыв."""
        if not self.review_gift_enabled or not self.running:
            return

        try:
            order = event.order
            if order.status != OrderStatuses.CLOSED:
                return

            if order.id not in self.sent_orders:
                return

            username = self.sent_orders[order.id]

            # Проверяем: юзер уже получал бонус — не отправляем повторно
            if username.lower() in self.review_gifted_users:
                logger.info(
                    f"{LOGGER_PREFIX} @{username} уже получал бонус за отзыв, пропуск"
                )
                self.sent_orders.pop(order.id, None)
                return

            logger.info(
                f"{LOGGER_PREFIX} Заказ #{order.id} подтверждён, проверяю отзыв для @{username}"
            )

            # Проверка отзыва и отправка бонуса в отдельном потоке
            Thread(
                target=self._send_review_gifts,
                args=(cardinal, username, order.chat_id, order.id),
                daemon=True,
            ).start()

        except Exception as e:
            logger.error(f"{LOGGER_PREFIX} Ошибка обработки статуса заказа: {e}")
            logger.debug(f"{LOGGER_PREFIX} TRACEBACK", exc_info=True)

    # ───────────────────── Telegram-панель ─────────────────────

    def show_simple_panel(self, cardinal: "Cardinal", chat_id: int) -> None:
        keyboard = InlineKeyboardMarkup(row_width=2)

        status = "🟢 ВКЛ" if self.running else "🔴 ВЫКЛ"
        review_status = "🟢" if self.review_gift_enabled else "🔴"
        anon_status = "🟢" if self.anonymous_sending else "🔴"
        clear_status = "🟢" if self.auto_clear_chat else "🔴"
        lots_count = len(self.lot_stars_mapping)
        
        # Проверка статуса Pyrogram
        pyrogram_status = "🟢" if (self.pyrogram_client and self._loop and self._loop.is_running()) else "🔴"

        keyboard.row(
            InlineKeyboardButton(f"Статус: {status}", callback_data="sg_show_status"),
            InlineKeyboardButton("🔄 Вкл/Выкл", callback_data="sg_toggle"),
        )
        keyboard.row(
            InlineKeyboardButton(
                f"🎁 Бонус за отзыв: {review_status}", callback_data="sg_toggle_review"
            ),
        )
        keyboard.row(
            InlineKeyboardButton(
                f"🔒 Анонимно: {anon_status}", callback_data="sg_toggle_anon"
            ),
        )
        keyboard.row(
            InlineKeyboardButton(
                f"🧹 Очистка чата: {clear_status}", callback_data="sg_toggle_clear"
            ),
        )
        keyboard.row(
            InlineKeyboardButton("⚙️ API", callback_data="sg_set_api"),
            InlineKeyboardButton(f"📌 Лоты ({lots_count})", callback_data="sg_manage_lots"),
        )

        text = (
            f"⚡ <b>StarsGifter v{VERSION}</b>\n\n"
            f"📊 <b>Статус:</b> {status}\n"
            f"🤖 <b>Pyrogram:</b> {pyrogram_status}\n"
            f"🎁 <b>Бонус за отзыв:</b> {review_status} ({self.review_gift_stars}⭐)\n"
            f"🔒 <b>Анонимная отправка:</b> {anon_status}\n"
            f"🧹 <b>Очистка чата:</b> {clear_status} ({self.clear_chat_delay}с)\n"
            f"⚙️ <b>API ID:</b> {'✅' if self.config.get('pyrogram', {}).get('api_id') else '❌'}\n"
            f"📌 <b>Лотов:</b> {lots_count}"
        )

        cardinal.telegram.bot.send_message(chat_id, text, reply_markup=keyboard, parse_mode="HTML")

    def setup_simple_callbacks(self, cardinal: "Cardinal") -> None:
        @cardinal.telegram.bot.callback_query_handler(func=lambda c: c.data == "sg_show_status")
        def show_status_btn(call):
            status = "🟢 ВКЛ" if self.running else "🔴 ВЫКЛ"
            review_status = "🟢 ВКЛ" if self.review_gift_enabled else "🔴 ВЫКЛ"
            api_id_ok = "✅" if self.config.get("pyrogram", {}).get("api_id") else "❌"
            api_hash_ok = "✅" if self.config.get("pyrogram", {}).get("api_hash") else "❌"
            lots = len(self.lot_stars_mapping)
            active = len(self.sent_orders)
            pyrogram_status = "🟢 Работает" if (self.pyrogram_client and self._loop and self._loop.is_running()) else "🔴 Не работает"

            anon_status = "🟢 ВКЛ" if self.anonymous_sending else "🔴 ВЫКЛ"
            clear_status = "🟢 ВКЛ" if self.auto_clear_chat else "🔴 ВЫКЛ"

            info = (
                "<b>📊 Информация</b>\n\n"
                f"• Статус: {status}\n"
                f"• Pyrogram: {pyrogram_status}\n"
                f"• Бонус за отзыв: {review_status} ({self.review_gift_stars}⭐)\n"
                f"• Анонимная отправка: {anon_status}\n"
                f"• Очистка чата: {clear_status} ({self.clear_chat_delay}с)\n"
                f"• API ID: {api_id_ok}\n"
                f"• API HASH: {api_hash_ok}\n"
                f"• Лотов: {lots}\n"
                f"• Активных заказов: {active}"
            )
            cardinal.telegram.bot.send_message(call.message.chat.id, info, parse_mode="HTML")

        @cardinal.telegram.bot.callback_query_handler(func=lambda c: c.data == "sg_toggle")
        def toggle_btn(call):
            self.running = not self.running
            self.config["plugin_enabled"] = self.running
            self.persist_config()

            status = "✅ Включен" if self.running else "❌ Выключен"
            cardinal.telegram.bot.answer_callback_query(
                call.id, f"Плагин {status}", show_alert=True
            )
            cardinal.telegram.bot.delete_message(call.message.chat.id, call.message.message_id)
            self.show_simple_panel(cardinal, call.message.chat.id)

        @cardinal.telegram.bot.callback_query_handler(func=lambda c: c.data == "sg_toggle_review")
        def toggle_review_btn(call):
            self.review_gift_enabled = not self.review_gift_enabled
            self.config["review_gift_enabled"] = self.review_gift_enabled
            self.persist_config()

            status = "✅ Включен" if self.review_gift_enabled else "❌ Выключен"
            cardinal.telegram.bot.answer_callback_query(
                call.id, f"Бонус за отзыв: {status}", show_alert=True
            )
            cardinal.telegram.bot.delete_message(call.message.chat.id, call.message.message_id)
            self.show_simple_panel(cardinal, call.message.chat.id)

        @cardinal.telegram.bot.callback_query_handler(func=lambda c: c.data == "sg_toggle_anon")
        def toggle_anon_btn(call):
            self.anonymous_sending = not self.anonymous_sending
            self.config["anonymous_sending"] = self.anonymous_sending
            self.persist_config()

            status = "✅ Включена" if self.anonymous_sending else "❌ Выключена"
            cardinal.telegram.bot.answer_callback_query(
                call.id, f"Анонимная отправка: {status}", show_alert=True
            )
            cardinal.telegram.bot.delete_message(call.message.chat.id, call.message.message_id)
            self.show_simple_panel(cardinal, call.message.chat.id)

        @cardinal.telegram.bot.callback_query_handler(func=lambda c: c.data == "sg_toggle_clear")
        def toggle_clear_btn(call):
            self.auto_clear_chat = not self.auto_clear_chat
            self.config["auto_clear_chat"] = self.auto_clear_chat
            self.persist_config()

            status = "✅ Включена" if self.auto_clear_chat else "❌ Выключена"
            cardinal.telegram.bot.answer_callback_query(
                call.id, f"Очистка чата: {status}", show_alert=True
            )
            cardinal.telegram.bot.delete_message(call.message.chat.id, call.message.message_id)
            self.show_simple_panel(cardinal, call.message.chat.id)

        @cardinal.telegram.bot.callback_query_handler(func=lambda c: c.data == "sg_set_api")
        def set_api_btn(call):
            keyboard = InlineKeyboardMarkup(row_width=1)
            keyboard.add(InlineKeyboardButton("📝 API ID", callback_data="sg_input_api_id"))
            keyboard.add(InlineKeyboardButton("📝 API HASH", callback_data="sg_input_api_hash"))
            keyboard.add(InlineKeyboardButton("🔙 Назад", callback_data="sg_back_to_main"))
            cardinal.telegram.bot.send_message(
                call.message.chat.id, "⚙️ <b>API</b>", reply_markup=keyboard, parse_mode="HTML"
            )

        @cardinal.telegram.bot.callback_query_handler(func=lambda c: c.data == "sg_input_api_id")
        def input_api_id_btn(call):
            msg = cardinal.telegram.bot.send_message(call.message.chat.id, "📝 API ID:")
            cardinal.telegram.bot.register_next_step_handler(msg, self.process_api_id, cardinal)

        @cardinal.telegram.bot.callback_query_handler(func=lambda c: c.data == "sg_input_api_hash")
        def input_api_hash_btn(call):
            msg = cardinal.telegram.bot.send_message(call.message.chat.id, "📝 API HASH:")
            cardinal.telegram.bot.register_next_step_handler(msg, self.process_api_hash, cardinal)

        @cardinal.telegram.bot.callback_query_handler(func=lambda c: c.data == "sg_manage_lots")
        def manage_lots_btn(call):
            keyboard = InlineKeyboardMarkup(row_width=1)
            keyboard.add(InlineKeyboardButton("➕ Добавить", callback_data="sg_add_lot"))
            keyboard.add(InlineKeyboardButton("➖ Удалить", callback_data="sg_remove_lot"))
            keyboard.add(InlineKeyboardButton("📋 Показать", callback_data="sg_show_lots"))
            keyboard.add(InlineKeyboardButton("🔙 Назад", callback_data="sg_back_to_main"))
            cardinal.telegram.bot.send_message(
                call.message.chat.id,
                f"📌 <b>Лоты ({len(self.lot_stars_mapping)})</b>",
                reply_markup=keyboard,
                parse_mode="HTML",
            )

        @cardinal.telegram.bot.callback_query_handler(func=lambda c: c.data == "sg_add_lot")
        def add_lot_btn(call):
            msg = cardinal.telegram.bot.send_message(
                call.message.chat.id,
                "Формат: <code>123456 100</code>",
                parse_mode="HTML",
            )
            cardinal.telegram.bot.register_next_step_handler(msg, self.process_add_lot, cardinal)

        @cardinal.telegram.bot.callback_query_handler(func=lambda c: c.data == "sg_remove_lot")
        def remove_lot_btn(call):
            msg = cardinal.telegram.bot.send_message(call.message.chat.id, "ID лота:")
            cardinal.telegram.bot.register_next_step_handler(
                msg, self.process_remove_lot, cardinal
            )

        @cardinal.telegram.bot.callback_query_handler(func=lambda c: c.data == "sg_show_lots")
        def show_lots_btn(call):
            if not self.lot_stars_mapping:
                text = "❌ Пусто"
            else:
                text = "<b>📌 Лоты:</b>\n\n"
                for lot_id, stars in self.lot_stars_mapping.items():
                    text += f"• <code>{lot_id}</code> → <b>{stars}⭐</b>\n"
            cardinal.telegram.bot.send_message(call.message.chat.id, text, parse_mode="HTML")

        @cardinal.telegram.bot.callback_query_handler(func=lambda c: c.data == "sg_back_to_main")
        def back_to_main_btn(call):
            cardinal.telegram.bot.delete_message(call.message.chat.id, call.message.message_id)
            self.show_simple_panel(cardinal, call.message.chat.id)

    # ───────────────────── Обработка ввода ─────────────────────

    def process_api_id(self, message, cardinal: "Cardinal") -> None:
        try:
            api_id = int(message.text.strip())
            self.config["pyrogram"]["api_id"] = api_id
            self.persist_config()
            cardinal.telegram.bot.send_message(
                message.chat.id, f"✅ API ID: <code>{api_id}</code>", parse_mode="HTML"
            )
        except (TypeError, ValueError):
            cardinal.telegram.bot.send_message(message.chat.id, "❌ Ошибка")

    def process_api_hash(self, message, cardinal: "Cardinal") -> None:
        api_hash = message.text.strip()
        self.config["pyrogram"]["api_hash"] = api_hash
        self.persist_config()
        cardinal.telegram.bot.send_message(
            message.chat.id,
            f"✅ API HASH: <code>{api_hash[:10]}...</code>",
            parse_mode="HTML",
        )

    def process_add_lot(self, message, cardinal: "Cardinal") -> None:
        try:
            parts = message.text.strip().split()
            lot_id = parts[0]
            stars = int(parts[1])
            self.lot_stars_mapping[lot_id] = stars
            self.config["lot_stars_mapping"][lot_id] = stars
            self.persist_config()
            cardinal.telegram.bot.send_message(
                message.chat.id,
                f"✅ Лот <code>{lot_id}</code> → <b>{stars}⭐</b>",
                parse_mode="HTML",
            )
        except (IndexError, ValueError):
            cardinal.telegram.bot.send_message(message.chat.id, "❌ Ошибка")

    def process_remove_lot(self, message, cardinal: "Cardinal") -> None:
        lot_id = message.text.strip()
        if lot_id in self.lot_stars_mapping:
            self.lot_stars_mapping.pop(lot_id)
            self.config["lot_stars_mapping"].pop(lot_id, None)
            self.persist_config()
            cardinal.telegram.bot.send_message(message.chat.id, "✅ Лот удалён", parse_mode="HTML")
        else:
            cardinal.telegram.bot.send_message(message.chat.id, "❌ Не найден", parse_mode="HTML")

    # ───────────────────── Инициализация ─────────────────────

    def init_plugin(self, cardinal: "Cardinal") -> None:
        logger.info(f"{LOGGER_PREFIX} {NAME} v{VERSION}")
        
        # Инициализация Pyrogram
        pyrogram_ok = self.init_pyrogram()
        if pyrogram_ok:
            logger.info(f"{LOGGER_PREFIX} ✅ Pyrogram готов к работе")
        else:
            logger.warning(f"{LOGGER_PREFIX} ⚠️ Pyrogram не инициализирован (проверьте API настройки)")

        @cardinal.telegram.bot.message_handler(commands=["stars_panel"])
        def panel(m):
            self.show_simple_panel(cardinal, m.chat.id)

        self.setup_simple_callbacks(cardinal)
        logger.info(f"{LOGGER_PREFIX} Загружен")


# ═══════════════════════════════════════════════════════════════════════════
# МОДУЛЬНЫЕ ПРИВЯЗКИ
# ═══════════════════════════════════════════════════════════════════════════

PLUGIN = StarsGifterPlugin()


def init_plugin(cardinal: "Cardinal") -> None:
    PLUGIN.init_plugin(cardinal)


def handle_new_order(cardinal: "Cardinal", event: NewOrderEvent, *args) -> None:
    PLUGIN.handle_new_order(cardinal, event, *args)


def handle_order_status_changed(cardinal: "Cardinal", event: OrderStatusChangedEvent, *args) -> None:
    PLUGIN.handle_order_status_changed(cardinal, event, *args)


BIND_TO_PRE_INIT = [init_plugin]
BIND_TO_NEW_ORDER = [handle_new_order]
BIND_TO_ORDER_STATUS_CHANGED = [handle_order_status_changed]
BIND_TO_DELETE = []