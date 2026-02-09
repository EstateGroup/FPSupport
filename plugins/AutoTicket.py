from __future__ import annotations
import requests
from bs4 import BeautifulSoup
from datetime import datetime, timedelta
import time
import json
import os
import logging
from logging.handlers import RotatingFileHandler
from typing import TYPE_CHECKING, Dict, List, Tuple
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
from telebot import types
import configparser
import random

if TYPE_CHECKING:
    from fpsupport import funpayautobot as Cardinal

from FunPayAPI.common.enums import OrderStatuses

NAME = "AutoTicket Plugin"
VERSION = "1.3.0"
DESCRIPTION = "Плагин для автоматической и ручной отправки тикетов в поддержку FunPay с интерактивным меню настроек."
CREDITS = "@RATER777X // @SXQSTAR // @gderobi //https://t.me/FunPay_plugin"
UUID = "a56ef9ac-ebce-4c91-8323-948655c179a3"
SETTINGS_PAGE = True
PLUGIN_FOLDER = f"storage/plugins/{UUID}/"
CONFIG_PATH = os.path.join(PLUGIN_FOLDER, "config.json")
LOG_DIR = "logs"
LOG_FILE = os.path.join(LOG_DIR, "autoticket.log")
LOGGER_PREFIX = "[AUTOTICKET]"

os.makedirs(LOG_DIR, exist_ok=True)
logger = logging.getLogger("FPC.autoticket")
logger.setLevel(logging.INFO)
file_handler = RotatingFileHandler(LOG_FILE, maxBytes=5*1024*1024, backupCount=5, encoding="utf-8")
file_handler.setFormatter(logging.Formatter("%(asctime)s%(message)s", datefmt="[%d.%m.%y %H:%M:%S]"))
logger.addHandler(file_handler)

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:128.0) Gecko/20100101 Firefox/128.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Safari/605.1.15",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Edge/126.0.2592.102",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
]

DEFAULT_CONFIG = {
    "phpsessid": "",
    "check_interval_seconds": 300,
    "auto_ticket_message": "Пожалуйста, подтвердите заказы: {order_ids}",
    "manual_ticket_message": "Подтвердите заказ: {order_id}",
    "auto_send_enabled": True,
    "auto_send_order_limit": 5,
    "auto_send_interval_seconds": 3600,
    "telegram_chat_id": None
}

def load_main_config() -> Dict:
    try:
        config = configparser.ConfigParser()
        config.read("configs/_main.cfg", encoding="utf-8")
        funpay_section = config["FunPay"]
        return {
            "golden_key": funpay_section.get("golden_key", ""),
            "user_agent": random.choice(USER_AGENTS),
            "locale": funpay_section.get("locale", "ru")
        }
    except Exception as e:
        logger.error(f"{LOGGER_PREFIX} Ошибка загрузки configs/_main.cfg: {e}")
        return {
            "golden_key": "",
            "user_agent": random.choice(USER_AGENTS),
            "locale": "ru"
        }

def load_config() -> Dict:
    os.makedirs(os.path.dirname(CONFIG_PATH), exist_ok=True)
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                config = json.load(f)
                for key in DEFAULT_CONFIG:
                    if key not in config:
                        config[key] = DEFAULT_CONFIG[key]
                return config
        except Exception as e:
            logger.error(f"{LOGGER_PREFIX} Ошибка загрузки конфига: {e}")
            save_config(DEFAULT_CONFIG)
            return DEFAULT_CONFIG
    else:
        save_config(DEFAULT_CONFIG)
        return DEFAULT_CONFIG

def save_config(config: Dict):
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=4, ensure_ascii=False)
    logger.info(f"{LOGGER_PREFIX} Настройки сохранены")

def init_plugin(cardinal: Cardinal, *args):
    try:
        cardinal.account.get()
        logger.info(f"{LOGGER_PREFIX} Данные аккаунта получены")
    except Exception as e:
        logger.error(f"{LOGGER_PREFIX} Ошибка получения аккаунта: {e}")
        raise Exception("Не удалось инициализировать аккаунт")

    config = load_config()
    main_config = load_main_config()
    settings_message_ids = {}
    cached_orders = []
    last_cache_time = 0
    support_username = None

    class AutoTicket:
        def __init__(self):
            self.cardinal = cardinal
            self.support_url = "https://support.funpay.com/tickets/create/1"
            self.headers = {
                "accept": "application/json, text/javascript, */*; q=0.01",
                "content-type": "application/x-www-form-urlencoded; charset=UTF-8",
                "origin": "https://support.funpay.com",
                "referer": "https://support.funpay.com/tickets/new/1",
                "user-agent": main_config["user_agent"],
                "x-requested-with": "XMLHttpRequest",
                "accept-language": f"{main_config['locale']}-{main_config['locale'].upper()},{main_config['locale']};q=0.9,en-US;q=0.8,en;q=0.7"
            }
            self.session = requests.Session()
            if main_config["golden_key"]:
                self.session.cookies.set("golden_key", main_config["golden_key"], domain="funpay.com")
                logger.info(f"{LOGGER_PREFIX} Установлен golden_key")
            self.last_check = 0

        def update_headers_for_ticket(self):
            config = load_config()
            if config["phpsessid"]:
                self.headers["cookie"] = f"PHPSESSID={config['phpsessid']}"
                logger.info(f"{LOGGER_PREFIX} Установлен PHPSESSID")
            else:
                logger.warning(f"{LOGGER_PREFIX} PHPSESSID отсутствует")
            self.session.headers.update(self.headers)

        def _extract_phpsessid(self) -> str:
            logger.info(f"{LOGGER_PREFIX} Извлечение PHPSESSID")
            max_attempts = 5
            main_config = load_main_config()
            if not main_config["golden_key"] or len(main_config["golden_key"]) < 20:
                logger.error(f"{LOGGER_PREFIX} Недействительный golden_key")
                error_msg = (
                    "❌ **Ошибка PHPSESSID** 🚫\n"
                    "━━━━━━━━━━━━━━━━━━\n"
                    "ℹ️ Недействительный golden_key\n"
                    "🔧 Введите PHPSESSID вручную в настройках"
                )
                config = load_config()
                if config["telegram_chat_id"]:
                    self.cardinal.telegram.bot.send_message(config["telegram_chat_id"], error_msg, parse_mode="HTML")
                raise Exception("Недействительный golden_key")

            for attempt in range(max_attempts):
                try:
                    user_agent = random.choice(USER_AGENTS)
                    headers = {
                        "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
                        "accept-encoding": "gzip, deflate, br",
                        "accept-language": f"{main_config['locale']}-{main_config['locale'].upper()},{main_config['locale']};q=0.9,en-US;q=0.8,en;q=0.7",
                        "referer": "https://funpay.com/",
                        "user-agent": user_agent
                    }
                    self.session.cookies.clear()
                    self.session.headers.clear()
                    self.session.headers.update(headers)
                    self.session.cookies.set("golden_key", main_config["golden_key"], domain="funpay.com")
                    sso_url = "https://funpay.com/support/sso?return_to=%2Ftickets%2Fnew"
                    response = self.session.get(sso_url, allow_redirects=False, timeout=20)
                    if response.status_code == 403:
                        logger.error(f"{LOGGER_PREFIX} Ошибка 403: недействительный golden_key")
                        raise Exception("Доступ запрещен (403)")
                    if response.status_code != 302:
                        logger.error(f"{LOGGER_PREFIX} Ошибка редиректа: статус {response.status_code}")
                        raise Exception("Не удалось инициировать SSO")

                    redirect_url = response.headers.get("Location", "")
                    if "jwt=" not in redirect_url:
                        logger.error(f"{LOGGER_PREFIX} JWT-токен не найден")
                        raise Exception("JWT-токен не найден")
                    jwt_token = redirect_url.split("jwt=")[1].split("&")[0]
                    access_url = f"https://support.funpay.com/access/jwt?jwt={jwt_token}&return_to=%2Ftickets%2Fnew"
                    response = self.session.get(access_url, allow_redirects=False, timeout=20)
                    if response.status_code != 302:
                        logger.error(f"{LOGGER_PREFIX} Ошибка доступа: статус {response.status_code}")
                        raise Exception("Не удалось получить PHPSESSID")

                    support_phpsessid = None
                    for cookie in self.session.cookies:
                        if cookie.name == "PHPSESSID" and ("support.funpay.com" in cookie.domain or ".support.funpay.com" in cookie.domain):
                            support_phpsessid = cookie.value
                            logger.info(f"{LOGGER_PREFIX} PHPSESSID извлечён")
                            break

                    if not support_phpsessid:
                        logger.error(f"{LOGGER_PREFIX} PHPSESSID не найден")
                        raise Exception("PHPSESSID не найден")

                    config = load_config()
                    config["phpsessid"] = support_phpsessid
                    save_config(config)
                    self.headers["cookie"] = f"PHPSESSID={support_phpsessid}"
                    self.session.headers.update(self.headers)
                    return support_phpsessid

                except Exception as e:
                    logger.error(f"{LOGGER_PREFIX} Ошибка извлечения PHPSESSID: {e}")
                    if attempt < max_attempts - 1:
                        time.sleep(5)
                        continue
                    error_msg = (
                        "❌ **Ошибка PHPSESSID** 🚫\n"
                        "━━━━━━━━━━━━━━━━━━\n"
                        "ℹ️ Не удалось извлечь PHPSESSID\n"
                        "🔧 Введите вручную в настройках"
                    )
                    config = load_config()
                    if config["telegram_chat_id"]:
                        self.cardinal.telegram.bot.send_message(config["telegram_chat_id"], error_msg, parse_mode="HTML")
                    raise Exception("Не удалось извлечь PHPSESSID")

        def refresh_support_data(self) -> Tuple[str, str]:
            logger.info(f"{LOGGER_PREFIX} Обновление данных поддержки")
            try:
                config = load_config()
                if not config["phpsessid"]:
                    self._extract_phpsessid()

                self.update_headers_for_ticket()
                response = self.session.get("https://support.funpay.com/tickets/new/1", timeout=20)
                response.raise_for_status()
                if response.status_code in (401, 403):
                    logger.error(f"{LOGGER_PREFIX} Не авторизован, обновляю PHPSESSID")
                    self.session.cookies.clear()
                    self.session.headers.clear()
                    self._extract_phpsessid()
                    self.update_headers_for_ticket()
                    response = self.session.get("https://support.funpay.com/tickets/new/1", timeout=20)
                    response.raise_for_status()

                soup = BeautifulSoup(response.text, 'html.parser')
                dropdown = soup.find('div', {'class': 'dropdown'})
                username = dropdown.find('span').text.strip() if dropdown and dropdown.find('span') else "Unknown"
                logger.info(f"{LOGGER_PREFIX} Ник поддержки: {username}")
                return username, config["phpsessid"]
            except Exception as e:
                logger.error(f"{LOGGER_PREFIX} Ошибка обновления данных: {e}")
                return "Unknown", config["phpsessid"]

        def get_csrf_token(self) -> str:
            logger.info(f"{LOGGER_PREFIX} Получение CSRF-токена")
            max_attempts = 3
            for attempt in range(max_attempts):
                try:
                    self.update_headers_for_ticket()
                    config = load_config()
                    if not config["phpsessid"]:
                        self._extract_phpsessid()

                    response = self.session.get("https://support.funpay.com/tickets/new/1", timeout=20)
                    response.raise_for_status()
                    if response.status_code in (401, 403):
                        logger.error(f"{LOGGER_PREFIX} Не авторизован, обновляю PHPSESSID")
                        self.session.cookies.clear()
                        self.session.headers.clear()
                        self._extract_phpsessid()
                        continue

                    soup = BeautifulSoup(response.text, 'html.parser')
                    token_input = soup.find('input', {'id': 'ticket__token'})
                    if token_input and token_input.get('value'):
                        logger.info(f"{LOGGER_PREFIX} CSRF-токен найден")
                        return token_input['value']

                    token_attr = soup.find(attrs={'data-csrf-token': True})
                    if token_attr and token_attr.get('data-csrf-token'):
                        logger.info(f"{LOGGER_PREFIX} CSRF-токен найден")
                        return token_attr['data-csrf-token']

                    scripts = soup.find_all('script')
                    for script in scripts:
                        if 'csrfToken' in str(script):
                            token = str(script).split('csrfToken":"')[1].split('"')[0]
                            if token:
                                logger.info(f"{LOGGER_PREFIX} CSRF-токен найден")
                                return token

                    logger.error(f"{LOGGER_PREFIX} CSRF-токен не найден")
                    raise ValueError("CSRF-токен не найден")
                except Exception as e:
                    logger.error(f"{LOGGER_PREFIX} Ошибка получения CSRF-токена: {e}")
                    if attempt < max_attempts - 1:
                        time.sleep(3)
                        continue
                    return ""
            logger.error(f"{LOGGER_PREFIX} Не удалось получить CSRF-токен")
            return ""

        def send_support_ticket(self, order_ids: List[str], is_manual: bool = False) -> Tuple[bool, str]:
            logger.info(f"{LOGGER_PREFIX} Отправка тикета для заказов: {', '.join(order_ids)}")
            config = load_config()
            try:
                self.session.cookies.clear()
                self.session.headers.clear()
                self._extract_phpsessid()
                self.update_headers_for_ticket()
            except Exception as e:
                logger.error(f"{LOGGER_PREFIX} Ошибка извлечения PHPSESSID: {e}")
                error_msg = (
                    "❌ **Ошибка отправки тикета** 🚫\n"
                    "━━━━━━━━━━━━━━━━━━\n"
                    "ℹ️ Не удалось извлечь PHPSESSID\n"
                    "🔧 Введите вручную в настройках"
                )
                if config["telegram_chat_id"]:
                    self.cardinal.telegram.bot.send_message(config["telegram_chat_id"], error_msg, parse_mode="HTML")
                return False, ""

            if is_manual:
                message = config["manual_ticket_message"].format(order_id=order_ids[0])
            else:
                order_ids_str = ", ".join(f"#{oid}" for oid in order_ids)
                message = config["auto_ticket_message"].format(order_ids=order_ids_str)
            max_attempts = 3
            for attempt in range(max_attempts):
                csrf_token = self.get_csrf_token()
                if not csrf_token:
                    logger.error(f"{LOGGER_PREFIX} Не удалось получить CSRF-токен")
                    if attempt < max_attempts - 1:
                        time.sleep(3)
                        continue
                    return False, ""

                payload = {
                    "ticket[fields][1]": self.cardinal.account.username,
                    "ticket[fields][2]": order_ids[0] if order_ids else "",
                    "ticket[fields][3]": "2",
                    "ticket[fields][5]": "201",
                    "ticket[comment][body_html]": f'<p dir="auto">{message}</p>',
                    "ticket[comment][attachments]": "",
                    "ticket[_token]": csrf_token,
                    "ticket[submit]": "Отправить"
                }

                try:
                    response = self.session.post(self.support_url, data=payload, timeout=20)
                    response.raise_for_status()
                    if response.status_code in (401, 403):
                        logger.error(f"{LOGGER_PREFIX} Не авторизован, обновляю PHPSESSID")
                        self.session.cookies.clear()
                        self.session.headers.clear()
                        self._extract_phpsessid()
                        continue
                    logger.info(f"{LOGGER_PREFIX} Тикет отправлен")
                    ticket_id = response.url.split('/')[-1] if '/tickets/' in response.url else "Unknown"
                    return True, ticket_id
                except Exception as e:
                    logger.error(f"{LOGGER_PREFIX} Ошибка отправки тикета: {e}")
                    if attempt < max_attempts - 1:
                        time.sleep(3)
                        continue
                    return False, ""
            logger.error(f"{LOGGER_PREFIX} Не удалось отправить тикет")
            return False, ""

        def get_orders(self, start_from: str, subcs: dict, locale) -> Tuple[str | None, List, str, dict]:
            logger.info(f"{LOGGER_PREFIX} Загрузка заказов")
            attempts = 3
            while attempts:
                try:
                    result = self.cardinal.account.get_sales(
                        category="sales", start_from=start_from or None, state="paid", locale=locale, subcategories=subcs
                    )
                    logger.info(f"{LOGGER_PREFIX} Загружено {len(result[1])} заказов")
                    break
                except Exception as e:
                    logger.error(f"{LOGGER_PREFIX} Ошибка загрузки заказов: {e}")
                    attempts -= 1
                    time.sleep(1)
            else:
                logger.error(f"{LOGGER_PREFIX} Не удалось загрузить заказы")
                raise Exception("Не удалось загрузить заказы")
            orders = result[1]
            old_orders = [o for o in orders if (datetime.now() - (o.date if isinstance(o.date, datetime) else datetime.fromtimestamp(o.date))).total_seconds() >= 24 * 3600]
            logger.info(f"{LOGGER_PREFIX} Найдено {len(old_orders)} просроченных заказов")
            return result[0], old_orders, result[2], result[3]

        def get_all_old_orders(self) -> List:
            nonlocal cached_orders, last_cache_time
            current_time = time.time()
            if current_time - last_cache_time > 300 or not cached_orders:
                logger.info(f"{LOGGER_PREFIX} Обновление кэша заказов")
                start_from = ""
                old_orders = []
                locale = None
                subcs = None
                while start_from is not None:
                    start_from, orders, locale, subcs = self.get_orders(start_from, subcs, locale)
                    old_orders.extend(orders)
                    time.sleep(1)
                cached_orders = sorted(old_orders, key=lambda o: o.date if isinstance(o.date, datetime) else datetime.fromtimestamp(o.date))
                last_cache_time = current_time
                logger.info(f"{LOGGER_PREFIX} Кэш обновлён: {len(cached_orders)} заказов")
            return cached_orders

        def auto_send_tickets(self, chat_id: int = None):
            config = load_config()
            if not config["auto_send_enabled"]:
                logger.info(f"{LOGGER_PREFIX} Автоотправка отключена")
                return
            orders = self.get_all_old_orders()
            if not orders:
                logger.info(f"{LOGGER_PREFIX} Нет просроченных заказов")
                return
            order_limit = min(config["auto_send_order_limit"], len(orders))
            order_ids = [order.id for order in orders[:order_limit]]
            if order_ids:
                logger.info(f"{LOGGER_PREFIX} Автоотправка для {', '.join(order_ids)}")
                success, ticket_id = self.send_support_ticket(order_ids, is_manual=False)
                if success and chat_id:
                    order_ids_str = ", ".join(f"#{oid}" for oid in order_ids)
                    self.cardinal.telegram.bot.send_message(
                        chat_id,
                        f"✅ Тикет отправлен 📬\n━━━━━━━━━━━━━━━━━━\n📦 Заказы: {order_ids_str}",
                        parse_mode="HTML"
                    )
                    logger.info(f"{LOGGER_PREFIX} Уведомление отправлено в чат {chat_id}")
                elif not success and chat_id:
                    order_ids_str = ", ".join(f"#{oid}" for oid in order_ids)
                    self.cardinal.telegram.bot.send_message(
                        chat_id,
                        f"❌ **Ошибка тикета** 🚫\n━━━━━━━━━━━━━━━━━━\n📦 **Заказы**: {order_ids_str}\nℹ️ Проверьте настройки",
                        parse_mode="HTML"
                    )
                    logger.info(f"{LOGGER_PREFIX} Ошибка уведомлена в чат {chat_id}")

    auto_ticket = AutoTicket()

    try:
        support_username, _ = auto_ticket.refresh_support_data()
        config = load_config()
        if config["telegram_chat_id"]:
            auto_status = "🟢 Включена" if config["auto_send_enabled"] else "🔴 Отключена"
            cardinal.telegram.bot.send_message(
                config["telegram_chat_id"],
                f"🎉 **AutoTicket v{VERSION} запущен** 🚀\n━━━━━━━━━━━━━━━━━━\n🚀 **Автоотправка**: {auto_status} \n👨‍💻 <b>Разработчик:</b> {CREDITS}",
                parse_mode="HTML"
            )
            logger.info(f"{LOGGER_PREFIX} Уведомление о запуске отправлено")
        else:
            logger.warning(f"{LOGGER_PREFIX} telegram_chat_id не задан")
    except Exception as e:
        logger.error(f"{LOGGER_PREFIX} Ошибка инициализации: {e}")

    def format_time_delta(seconds: float) -> str:
        days = int(seconds // (24 * 3600))
        hours = int((seconds % (24 * 3600)) // 3600)
        return f"{days} д {hours} ч" if days > 0 else f"{hours} ч"

    def manual_check_orders(m):
        config = load_config()
        orders = auto_ticket.get_all_old_orders()
        current_time = datetime.now()
        order_ids = []
        for order in orders:
            order_time = order.date if isinstance(order.date, datetime) else datetime.fromtimestamp(order.date)
            if (current_time - order_time).total_seconds() / 3600 >= 24:
                order_ids.append(order.id)
        if order_ids:
            success, ticket_id = auto_ticket.send_support_ticket(order_ids, is_manual=False)
            order_ids_str = ", ".join(f"#{oid}" for oid in order_ids)
            if success:
                message = config["auto_ticket_message"].format(order_ids=order_ids_str)
                auto_ticket.cardinal.telegram.bot.edit_message_text(
                    f"✅ **Тикет отправлен** 📬\n━━━━━━━━━━━━━━━━━━\n📦 **Заказы**: {order_ids_str}\n💬 **Сообщение**: {message[:50]}...",
                    m.chat.id,
                    settings_message_ids.get(str(m.chat.id), m.message_id),
                    parse_mode="HTML"
                )
                settings_message_ids[str(m.chat.id) + "_text"] = f"✅ Тикет отправлен для {order_ids_str}"
            else:
                auto_ticket.cardinal.telegram.bot.edit_message_text(
                    f"❌ **Ошибка тикета** 🚫\n━━━━━━━━━━━━━━━━━━\n📦 **Заказы**: {order_ids_str}\nℹ️ Проверьте настройки",
                    m.chat.id,
                    settings_message_ids.get(str(m.chat.id), m.message_id),
                    parse_mode="HTML"
                )
                settings_message_ids[str(m.chat.id) + "_text"] = f"❌ Ошибка для {order_ids_str}"
        else:
            auto_ticket.cardinal.telegram.bot.edit_message_text(
                "ℹ️ **Нет просроченных заказов** 🕒\n━━━━━━━━━━━━━━━━━━\n🔧 Вернитесь в меню",
                m.chat.id,
                settings_message_ids.get(str(m.chat.id), m.message_id),
                parse_mode="HTML"
            )
            settings_message_ids[str(m.chat.id) + "_text"] = "ℹ️ Нет просроченных заказов"

    def settings_menu(message_or_call, is_call=False, message_id=None):
        chat_id = message_or_call.message.chat.id if is_call else message_or_call.chat.id
        message_id = message_or_call.message.message_id if is_call else message_id or message_or_call.message_id
        config = load_config()
        if not config["telegram_chat_id"]:
            config["telegram_chat_id"] = chat_id
            save_config(config)
            logger.info(f"{LOGGER_PREFIX} Установлен telegram_chat_id: {chat_id}")
        phpsessid_display = config["phpsessid"][:5] + "*" * (len(config["phpsessid"]) - 5) if config["phpsessid"] else "Не задан ⚠️"
        username_display = support_username if support_username else "Unknown"
        auto_status = "🟢 Включена" if config["auto_send_enabled"] else "🔴 Отключена"
        txt = (
            f"🛠️ <b>AUTO-TICKET v{VERSION}</b> 🛠️\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"👤 Аккаунт: {username_display}\n"
            f"🔑 PHPSESSID: {phpsessid_display} 🔒\n"
            f"⏰ Проверка: каждые {config['check_interval_seconds'] // 60} мин\n"
            f"🚀 Автоотправка: {auto_status}\n"
            f"📬 Сообщение (авто): {config['auto_ticket_message'][:30] + ('...' if len(config['auto_ticket_message']) > 30 else '')}\n"
            f"📩 Сообщение (ручное): {config['manual_ticket_message'][:30] + ('...' if len(config['manual_ticket_message']) > 30 else '')}\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"👨‍💻 <b>Разработчик: <a href='https://t.me/RATER777X'>{CREDITS}</a></b>\n"
        )
        previous_txt = settings_message_ids.get(str(chat_id) + "_text", "")
        kb = InlineKeyboardMarkup(row_width=3)
        kb.add(
            InlineKeyboardButton("🔑 PHPSESSID", callback_data="edit_phpsessid"),
            InlineKeyboardButton("📬 Сообщения", callback_data="edit_messages"),
            InlineKeyboardButton("⏰ Интервалы", callback_data="edit_intervals"),
        )
        kb.add(
            InlineKeyboardButton("🚀 Авто", callback_data="auto_send_settings"),
            InlineKeyboardButton("📦 Заказы", callback_data="view_orders"),
            InlineKeyboardButton("🔄 Обновить", callback_data="refresh_data"),
        )
        kb.add(
            InlineKeyboardButton("📜 Логи", callback_data="export_logs"),
            InlineKeyboardButton("👀 Предпросмотр", callback_data="preview_ticket_message"),
        )
        try:
            if str(chat_id) in settings_message_ids and previous_txt != txt:
                cardinal.telegram.bot.edit_message_text(txt, chat_id, settings_message_ids[str(chat_id)], parse_mode="HTML", reply_markup=kb)
            else:
                sent_message = cardinal.telegram.bot.send_message(chat_id, txt, parse_mode="HTML", reply_markup=kb)
                settings_message_ids[str(chat_id)] = sent_message.message_id
            settings_message_ids[str(chat_id) + "_text"] = txt
        except Exception as e:
            logger.error(f"{LOGGER_PREFIX} Ошибка меню: {e}")
            sent_message = cardinal.telegram.bot.send_message(chat_id, txt, parse_mode="HTML", reply_markup=kb)
            settings_message_ids[str(chat_id)] = sent_message.message_id
            settings_message_ids[str(chat_id) + "_text"] = txt

    @cardinal.telegram.bot.callback_query_handler(func=lambda call: call.data == "edit_phpsessid")
    def edit_phpsessid(call):
        logger.info(f"{LOGGER_PREFIX} Изменение PHPSESSID для {call.message.chat.id}")
        cardinal.telegram.bot.answer_callback_query(call.id, "")
        txt = (
            "🔑 Введите PHPSESSID 🔒\n"
            "━━━━━━━━━━━━━━━━━━\n"
            "ℹ️ Оставьте пустым для автозаполнения\n"
            "➖ Отправьте \"-\" для сброса"
        )
        previous_txt = settings_message_ids.get(str(call.message.chat.id) + "_text", "")
        if previous_txt != txt:
            cardinal.telegram.bot.edit_message_text(
                txt,
                call.message.chat.id,
                call.message.message_id,
                parse_mode="HTML"
            )
            settings_message_ids[str(call.message.chat.id) + "_text"] = txt
        cardinal.telegram.bot.register_next_step_handler_by_chat_id(call.message.chat.id, process_phpsessid_change)

    @cardinal.telegram.bot.callback_query_handler(func=lambda call: call.data == "edit_intervals")
    def edit_intervals(call):
        logger.info(f"{LOGGER_PREFIX} Изменение интервалов для {call.message.chat.id}")
        cardinal.telegram.bot.answer_callback_query(call.id, "")
        txt = (
            "⏰ Настройка интервалов ⏰\n"
            "━━━━━━━━━━━━━━━━━━\n"
            "🔄 Проверка заказов\n"
            "🔄 Автоотправка"
        )
        kb = InlineKeyboardMarkup(row_width=2)
        kb.add(
            InlineKeyboardButton("🔄 Проверка", callback_data="edit_check_interval"),
            InlineKeyboardButton("🔄 Авто", callback_data="edit_auto_send_interval"),
        )
        kb.add(InlineKeyboardButton("⬅️ Назад", callback_data="back_to_settings"))
        previous_txt = settings_message_ids.get(str(call.message.chat.id) + "_text", "")
        if previous_txt != txt:
            cardinal.telegram.bot.edit_message_text(
                txt,
                call.message.chat.id,
                call.message.message_id,
                parse_mode="HTML",
                reply_markup=kb
            )
            settings_message_ids[str(call.message.chat.id) + "_text"] = txt

    @cardinal.telegram.bot.callback_query_handler(func=lambda call: call.data == "edit_check_interval")
    def edit_check_interval(call):
        logger.info(f"{LOGGER_PREFIX} Изменение интервала проверки для {call.message.chat.id}")
        cardinal.telegram.bot.answer_callback_query(call.id, "")
        txt = (
            "⏰ Интервал проверки ⏰\n"
            "━━━━━━━━━━━━━━━━━━\n"
            "ℹ️ Введите минуты (число > 0)\n"
            "➖ Отправьте \"-\" для сброса"
        )
        previous_txt = settings_message_ids.get(str(call.message.chat.id) + "_text", "")
        if previous_txt != txt:
            cardinal.telegram.bot.edit_message_text(
                txt,
                call.message.chat.id,
                call.message.message_id,
                parse_mode="HTML"
            )
            settings_message_ids[str(call.message.chat.id) + "_text"] = txt
        cardinal.telegram.bot.register_next_step_handler_by_chat_id(call.message.chat.id, process_check_interval_change)

    @cardinal.telegram.bot.callback_query_handler(func=lambda call: call.data == "edit_auto_send_interval")
    def edit_auto_send_interval(call):
        logger.info(f"{LOGGER_PREFIX} Изменение интервала автоотправки для {call.message.chat.id}")
        cardinal.telegram.bot.answer_callback_query(call.id, "")
        txt = (
            "⏰ Интервал автоотправки ⏰\n"
            "━━━━━━━━━━━━━━━━━━\n"
            "ℹ️ Введите минуты (число > 0)\n"
            "➖ Отправьте \"-\" для сброса"
        )
        previous_txt = settings_message_ids.get(str(call.message.chat.id) + "_text", "")
        if previous_txt != txt:
            cardinal.telegram.bot.edit_message_text(
                txt,
                call.message.chat.id,
                call.message.message_id,
                parse_mode="HTML"
            )
            settings_message_ids[str(call.message.chat.id) + "_text"] = txt
        cardinal.telegram.bot.register_next_step_handler_by_chat_id(call.message.chat.id, process_auto_send_interval_change)

    @cardinal.telegram.bot.callback_query_handler(func=lambda call: call.data == "edit_messages")
    def edit_messages(call):
        logger.info(f"{LOGGER_PREFIX} Изменение сообщений для {call.message.chat.id}")
        cardinal.telegram.bot.answer_callback_query(call.id, "")
        txt = (
            "📬 Настройка сообщений 📬\n"
            "━━━━━━━━━━━━━━━━━━\n"
            "📬 Автоотправка\n"
            "📩 Ручная отправка"
        )
        kb = InlineKeyboardMarkup(row_width=2)
        kb.add(
            InlineKeyboardButton("📬 Авто", callback_data="edit_auto_ticket_message"),
            InlineKeyboardButton("📩 Ручное", callback_data="edit_manual_ticket_message"),
        )
        kb.add(InlineKeyboardButton("⬅️ Назад", callback_data="back_to_settings"))
        previous_txt = settings_message_ids.get(str(call.message.chat.id) + "_text", "")
        if previous_txt != txt:
            cardinal.telegram.bot.edit_message_text(
                txt,
                call.message.chat.id,
                call.message.message_id,
                parse_mode="HTML",
                reply_markup=kb
            )
            settings_message_ids[str(call.message.chat.id) + "_text"] = txt

    @cardinal.telegram.bot.callback_query_handler(func=lambda call: call.data == "edit_auto_ticket_message")
    def edit_auto_ticket_message(call):
        config = load_config()
        logger.info(f"{LOGGER_PREFIX} Изменение текста автоотправки для {call.message.chat.id}")
        cardinal.telegram.bot.answer_callback_query(call.id, "")
        preview = config["auto_ticket_message"].format(order_ids="#JTZ38MFP, #JTZ38MFG")
        txt = (
            "📬 Текст автоотправки 📬\n"
            "━━━━━━━━━━━━━━━━━━\n"
            f"📝 Текущий: {config['auto_ticket_message']}\n"
            f"👀 Пример: {preview}\n"
            "ℹ️ Включите {order_ids}\n"
            "➖ Отправьте \"-\" для сброса"
        )
        previous_txt = settings_message_ids.get(str(call.message.chat.id) + "_text", "")
        if previous_txt != txt:
            cardinal.telegram.bot.edit_message_text(
                txt,
                call.message.chat.id,
                call.message.message_id,
                parse_mode="HTML"
            )
            settings_message_ids[str(call.message.chat.id) + "_text"] = txt
        cardinal.telegram.bot.register_next_step_handler_by_chat_id(call.message.chat.id, process_auto_ticket_message_change)

    @cardinal.telegram.bot.callback_query_handler(func=lambda call: call.data == "edit_manual_ticket_message")
    def edit_manual_ticket_message(call):
        config = load_config()
        logger.info(f"{LOGGER_PREFIX} Изменение текста ручной отправки для {call.message.chat.id}")
        cardinal.telegram.bot.answer_callback_query(call.id, "")
        preview = config["manual_ticket_message"].format(order_id="#JTZ38MFP")
        txt = (
            "📩 Текст ручной отправки 📩\n"
            "━━━━━━━━━━━━━━━━━━\n"
            f"📝 Текущий: {config['manual_ticket_message']}\n"
            f"👀 Пример: {preview}\n"
            "ℹ️ Включите {order_id}\n"
            "➖ Отправьте \"-\" для сброса"
        )
        previous_txt = settings_message_ids.get(str(call.message.chat.id) + "_text", "")
        if previous_txt != txt:
            cardinal.telegram.bot.edit_message_text(
                txt,
                call.message.chat.id,
                call.message.message_id,
                parse_mode="HTML"
            )
            settings_message_ids[str(call.message.chat.id) + "_text"] = txt
        cardinal.telegram.bot.register_next_step_handler_by_chat_id(call.message.chat.id, process_manual_ticket_message_change)

    @cardinal.telegram.bot.callback_query_handler(func=lambda call: call.data == "preview_ticket_message")
    def preview_ticket_message(call):
        logger.info(f"{LOGGER_PREFIX} Предпросмотр текста тикета для {call.message.chat.id}")
        cardinal.telegram.bot.answer_callback_query(call.id, "")
        config = load_config()
        auto_preview = config["auto_ticket_message"].format(order_ids="#JTZ38MFP, #JTZ38MFG")
        manual_preview = config["manual_ticket_message"].format(order_id="#JTZ38MFP")
        txt = (
            f"👀 Предпросмотр тикетов 📬\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"🚀 Авто: {auto_preview}\n"
            f"📩 Ручное: {manual_preview}\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"⬅️ Назад"
        )
        previous_txt = settings_message_ids.get(str(call.message.chat.id) + "_text", "")
        kb = InlineKeyboardMarkup(row_width=1)
        kb.add(InlineKeyboardButton("⬅️ Назад", callback_data="back_to_settings"))
        if previous_txt != txt:
            cardinal.telegram.bot.edit_message_text(
                txt,
                call.message.chat.id,
                call.message.message_id,
                parse_mode="HTML",
                reply_markup=kb
            )
            settings_message_ids[str(call.message.chat.id) + "_text"] = txt

    @cardinal.telegram.bot.callback_query_handler(func=lambda call: call.data == "view_orders")
    def view_orders(call):
        logger.info(f"{LOGGER_PREFIX} Просмотр заказов для {call.message.chat.id}")
        cardinal.telegram.bot.answer_callback_query(call.id, "")
        orders = auto_ticket.get_all_old_orders()
        current_time = datetime.now()
        if not orders:
            txt = (
                "ℹ️ Нет просроченных заказов 🕒\n"
                "━━━━━━━━━━━━━━━━━━\n"
                "🔧 Вернитесь в меню"
            )
            kb = InlineKeyboardMarkup(row_width=1)
            kb.add(InlineKeyboardButton("⬅️ Назад", callback_data="back_to_settings"))
        else:
            txt = "📦 **Просроченные заказы** 📦\n━━━━━━━━━━━━━━━━━━\n"
            kb = InlineKeyboardMarkup(row_width=3)
            for order in orders[:10]:
                order_time = order.date if isinstance(order.date, datetime) else datetime.fromtimestamp(order.date)
                time_delta = (current_time - order_time).total_seconds()
                time_str = format_time_delta(time_delta)
                product_name = getattr(order, 'title', 'Неизвестный товар')[:30]
                price = f"{order.price} {order.currency}"
                txt += (
                    f"📦 #{order.id}\n"
                    f"👤 Покупатель: {order.buyer_username}\n"
                    f"💰 Цена: {price}\n"
                    f"🎮 Товар: {product_name}\n"
                    f"⏳ Ждёт: {time_str}\n"
                    f"━━━━━━━━━━━━━━━━━━\n"
                )
                kb.add(InlineKeyboardButton(f"📨 #{order.id}", callback_data=f"send_ticket_{order.id}"))
            if len(orders) > 10:
                kb.add(InlineKeyboardButton("➡️ Ещё", callback_data="view_more_orders"))
            kb.add(InlineKeyboardButton("⬅️ Назад", callback_data="back_to_settings"))
        previous_txt = settings_message_ids.get(str(call.message.chat.id) + "_text", "")
        if previous_txt != txt:
            cardinal.telegram.bot.edit_message_text(
                txt,
                call.message.chat.id,
                call.message.message_id,
                parse_mode="HTML",
                reply_markup=kb
            )
            settings_message_ids[str(call.message.chat.id) + "_text"] = txt

    @cardinal.telegram.bot.callback_query_handler(func=lambda call: call.data == "view_more_orders")
    def view_more_orders(call):
        logger.info(f"{LOGGER_PREFIX} Просмотр дополнительных заказов для {call.message.chat.id}")
        cardinal.telegram.bot.answer_callback_query(call.id, "")
        orders = auto_ticket.get_all_old_orders()
        current_time = datetime.now()
        txt = "📦 Просроченные заказы (продолжение) 📦\n━━━━━━━━━━━━━━━━━━\n"
        kb = InlineKeyboardMarkup(row_width=3)
        for order in orders[10:20]:
            order_time = order.date if isinstance(order.date, datetime) else datetime.fromtimestamp(order.date)
            time_delta = (current_time - order_time).total_seconds()
            time_str = format_time_delta(time_delta)
            product_name = getattr(order, 'title', 'Неизвестный товар')[:30]
            price = f"{order.price} {order.currency}"
            txt += (
                f"📦 #{order.id}\n"
                f"👤 Покупатель: {order.buyer_username}\n"
                f"💰 Цена: {price}\n"
                f"🎮 Товар: {product_name}\n"
                f"⏳ Ждёт: {time_str}\n"
                f"━━━━━━━━━━━━━━━━━━\n"
            )
            kb.add(InlineKeyboardButton(f"📨 #{order.id}", callback_data=f"send_ticket_{order.id}"))
        kb.add(InlineKeyboardButton("⬅️ Назад", callback_data="view_orders"))
        previous_txt = settings_message_ids.get(str(call.message.chat.id) + "_text", "")
        if previous_txt != txt:
            cardinal.telegram.bot.edit_message_text(
                txt,
                call.message.chat.id,
                call.message.message_id,
                parse_mode="HTML",
                reply_markup=kb
            )
            settings_message_ids[str(call.message.chat.id) + "_text"] = txt

    @cardinal.telegram.bot.callback_query_handler(func=lambda call: call.data.startswith("send_ticket_"))
    def send_ticket(call):
        order_id = call.data.replace("send_ticket_", "")
        logger.info(f"{LOGGER_PREFIX} Отправка тикета для #{order_id} от {call.message.chat.id}")
        cardinal.telegram.bot.answer_callback_query(call.id, "")
        success, ticket_id = auto_ticket.send_support_ticket([order_id], is_manual=True)
        if success:
            cardinal.telegram.bot.answer_callback_query(call.id, f"✅ Тикет для #{order_id} отправлен!", show_alert=True)
            txt = (
                f"✅ **Тикет отправлен** 📬\n"
                f"━━━━━━━━━━━━━━━━━━\n"
                f"📦 **Заказ**: #{order_id}\n"
                f"ℹ️ Вернитесь к заказам"
            )
            kb = InlineKeyboardMarkup(row_width=1)
            kb.add(InlineKeyboardButton("⬅️ Назад", callback_data="view_orders"))
            previous_txt = settings_message_ids.get(str(call.message.chat.id) + "_text", "")
            if previous_txt != txt:
                cardinal.telegram.bot.edit_message_text(
                    txt,
                    call.message.chat.id,
                    call.message.message_id,
                    parse_mode="HTML",
                    reply_markup=kb
                )
                settings_message_ids[str(call.message.chat.id) + "_text"] = txt
        else:
            cardinal.telegram.bot.answer_callback_query(call.id, f"❌ Ошибка тикета для #{order_id}", show_alert=True)

    @cardinal.telegram.bot.callback_query_handler(func=lambda call: call.data == "refresh_data")
    def refresh_data(call):
        logger.info(f"{LOGGER_PREFIX} Обновление данных для {call.message.chat.id}")
        cardinal.telegram.bot.answer_callback_query(call.id, "")
        global support_username
        try:
            support_username, _ = auto_ticket.refresh_support_data()
            txt = f"✅ Данные обновлены 🎉\n━━━━━━━━━━━━━━━━━━\n👤 Аккаунт: {support_username}"
        except Exception as e:
            txt = f"❌ Ошибка обновления 🚫\n━━━━━━━━━━━━━━━━━━\nℹ️ {str(e)}"
        previous_txt = settings_message_ids.get(str(call.message.chat.id) + "_text", "")
        if previous_txt != txt:
            cardinal.telegram.bot.edit_message_text(
                txt,
                call.message.chat.id,
                call.message.message_id,
                parse_mode="HTML"
            )
            settings_message_ids[str(call.message.chat.id) + "_text"] = txt
        settings_menu(call, is_call=True, message_id=call.message.message_id)

    @cardinal.telegram.bot.callback_query_handler(func=lambda call: call.data == "back_to_settings")
    def back_to_settings(call):
        logger.info(f"{LOGGER_PREFIX} Возврат в меню для {call.message.chat.id}")
        cardinal.telegram.bot.answer_callback_query(call.id, "")
        settings_menu(call, is_call=True, message_id=call.message.message_id)

    @cardinal.telegram.bot.callback_query_handler(func=lambda call: call.data == "auto_send_settings")
    def auto_send_settings(call):
        logger.info(f"{LOGGER_PREFIX} Настройки автоотправки для {call.message.chat.id}")
        cardinal.telegram.bot.answer_callback_query(call.id, "")
        config = load_config()
        auto_status = "🟢 Включена" if config["auto_send_enabled"] else "🔴 Отключена"
        txt = (
            f"🚀 **Настройки автоотправки** 🚀\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"🔢 **Лимит заказов**: {config['auto_send_order_limit']}\n"
            f"⏰ **Интервал**: {config['auto_send_interval_seconds'] // 60} мин\n"
            f"📬 **Сообщение**: {config['auto_ticket_message'][:30] + ('...' if len(config['auto_ticket_message']) > 30 else '')}\n"
            f"🟢 **Статус**: {auto_status}\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"🔧 **Выберите действие**:"
        )
        kb = InlineKeyboardMarkup(row_width=3)
        kb.add(
            InlineKeyboardButton(f"🟢/🔴 Вкл/Выкл", callback_data="toggle_auto_send"),
            InlineKeyboardButton("📬 Текст", callback_data="edit_auto_ticket_message"),
            InlineKeyboardButton("⏰ Интервал", callback_data="edit_auto_send_interval"),
        )
        kb.add(
            InlineKeyboardButton(f"🔢 Лимит", callback_data="edit_auto_send_limit"),
            InlineKeyboardButton("⬅️ Назад", callback_data="back_to_settings"),
        )
        previous_txt = settings_message_ids.get(str(call.message.chat.id) + "_text", "")
        if previous_txt != txt:
            cardinal.telegram.bot.edit_message_text(
                txt,
                call.message.chat.id,
                call.message.message_id,
                parse_mode="HTML",
                reply_markup=kb
            )
            settings_message_ids[str(call.message.chat.id) + "_text"] = txt

    @cardinal.telegram.bot.callback_query_handler(func=lambda call: call.data == "edit_auto_send_limit")
    def edit_auto_send_limit(call):
        logger.info(f"{LOGGER_PREFIX} Изменение лимита заказов для {call.message.chat.id}")
        cardinal.telegram.bot.answer_callback_query(call.id, "")
        txt = (
            "🔢 **Лимит заказов** 🔢\n"
            "━━━━━━━━━━━━━━━━━━\n"
            "ℹ️ Введите число (1-10)\n"
            "➖ Отправьте \"-\" для сброса"
        )
        previous_txt = settings_message_ids.get(str(call.message.chat.id) + "_text", "")
        if previous_txt != txt:
            cardinal.telegram.bot.edit_message_text(
                txt,
                call.message.chat.id,
                call.message.message_id,
                parse_mode="HTML"
            )
            settings_message_ids[str(call.message.chat.id) + "_text"] = txt
        cardinal.telegram.bot.register_next_step_handler_by_chat_id(call.message.chat.id, process_auto_send_limit_change)

    @cardinal.telegram.bot.callback_query_handler(func=lambda call: call.data == "toggle_auto_send")
    def toggle_auto_send(call):
        config = load_config()
        config["auto_send_enabled"] = not config["auto_send_enabled"]
        save_config(config)
        cardinal.telegram.bot.answer_callback_query(
            call.id,
            f"🚀 Автоотправка {'включена 🟢' if config['auto_send_enabled'] else 'отключена 🔴'}",
            show_alert=True
        )
        auto_send_settings(call)

    @cardinal.telegram.bot.callback_query_handler(func=lambda call: call.data == "export_logs")
    def export_logs_callback(call):
        logger.info(f"{LOGGER_PREFIX} Экспорт логов для {call.message.chat.id}")
        cardinal.telegram.bot.answer_callback_query(call.id, "")
        if os.path.exists(LOG_FILE):
            try:
                with open(LOG_FILE, "rb") as f:
                    cardinal.telegram.bot.send_document(
                        call.message.chat.id,
                        f,
                        caption="📜 **Логи AutoTicket**",
                        visible_file_name="autoticket.log"
                    )
                cardinal.telegram.bot.answer_callback_query(call.id, "✅ Логи отправлены! 🎉", show_alert=True)
            except Exception as e:
                logger.error(f"{LOGGER_PREFIX} Ошибка экспорта логов: {e}")
                cardinal.telegram.bot.answer_callback_query(call.id, "❌ Ошибка логов 🚫", show_alert=True)
        else:
            cardinal.telegram.bot.answer_callback_query(call.id, "❌ Логи не найдены 🚫", show_alert=True)
        settings_menu(call, is_call=True, message_id=call.message.message_id)

    def process_phpsessid_change(message):
        config = load_config()
        logger.info(f"{LOGGER_PREFIX} Обработка PHPSESSID для {message.chat.id}")
        try:
            cardinal.telegram.bot.delete_message(message.chat.id, message.message_id)
        except Exception as e:
            logger.warning(f"{LOGGER_PREFIX} Не удалось удалить сообщение: {e}")

        phpsessid = message.text.strip()
        kb = InlineKeyboardMarkup(row_width=1)
        kb.add(InlineKeyboardButton("⬅️ Назад", callback_data="back_to_settings"))
        try:
            if phpsessid == "-":
                logger.info(f"{LOGGER_PREFIX} PHPSESSID не изменён")
                txt = "ℹ️ **PHPSESSID не изменён** 🔒\n━━━━━━━━━━━━━━━━━━\n⬅️ Вернитесь в меню"
                cardinal.telegram.bot.edit_message_text(
                    txt,
                    message.chat.id,
                    settings_message_ids.get(str(message.chat.id), message.message_id),
                    parse_mode="HTML",
                    reply_markup=kb
                )
                settings_message_ids[str(message.chat.id) + "_text"] = txt
                return

            if not phpsessid:
                logger.info(f"{LOGGER_PREFIX} Автозаполнение PHPSESSID")
                auto_ticket._extract_phpsessid()
                config = load_config()
                phpsessid_display = config["phpsessid"][:5] + "*" * (len(config["phpsessid"]) - 5) if config["phpsessid"] else "Не задан ⚠️"
                txt = f"✅ **PHPSESSID обновлён** 🎉\n━━━━━━━━━━━━━━━━━━\n🔑 {phpsessid_display}"
            else:
                config["phpsessid"] = phpsessid
                save_config(config)
                phpsessid_display = phpsessid[:5] + "*" * (len(phpsessid) - 5)
                txt = f"✅ **PHPSESSID обновлён** 🎉\n━━━━━━━━━━━━━━━━━━\n🔑 {phpsessid_display}"
            cardinal.telegram.bot.edit_message_text(
                txt,
                message.chat.id,
                settings_message_ids.get(str(message.chat.id), message.message_id),
                parse_mode="HTML",
                reply_markup=kb
            )
            settings_message_ids[str(message.chat.id) + "_text"] = txt
        except Exception as e:
            txt = (
                f"❌ **Ошибка PHPSESSID** 🚫\n"
                f"━━━━━━━━━━━━━━━━━━\n"
                f"ℹ️ {str(e)}\n"
                "➖ Отправьте \"-\" для сброса"
            )
            cardinal.telegram.bot.edit_message_text(
                txt,
                message.chat.id,
                settings_message_ids.get(str(message.chat.id), message.message_id),
                parse_mode="HTML"
            )
            settings_message_ids[str(message.chat.id) + "_text"] = txt
            cardinal.telegram.bot.register_next_step_handler_by_chat_id(message.chat.id, process_phpsessid_change)

    def process_check_interval_change(message):
        config = load_config()
        logger.info(f"{LOGGER_PREFIX} Обработка интервала проверки для {message.chat.id}")
        try:
            cardinal.telegram.bot.delete_message(message.chat.id, message.message_id)
        except Exception as e:
            logger.warning(f"{LOGGER_PREFIX} Не удалось удалить сообщение: {e}")

        kb = InlineKeyboardMarkup(row_width=1)
        kb.add(InlineKeyboardButton("⬅️ Назад", callback_data="back_to_settings"))
        try:
            if message.text.strip() == "-":
                logger.info(f"{LOGGER_PREFIX} Интервал проверки не изменён")
                txt = "ℹ️ **Интервал не изменён** ⏰\n━━━━━━━━━━━━━━━━━━\n⬅️ Вернитесь в меню"
                cardinal.telegram.bot.edit_message_text(
                    txt,
                    message.chat.id,
                    settings_message_ids.get(str(message.chat.id), message.message_id),
                    parse_mode="HTML",
                    reply_markup=kb
                )
                settings_message_ids[str(message.chat.id) + "_text"] = txt
                return

            minutes = int(message.text.strip())
            if minutes < 1:
                raise ValueError("Число должно быть > 0")
            config["check_interval_seconds"] = minutes * 60
            save_config(config)
            txt = f"✅ **Интервал обновлён** 🎉\n━━━━━━━━━━━━━━━━━━\n⏰ {minutes} мин"
            cardinal.telegram.bot.edit_message_text(
                txt,
                message.chat.id,
                settings_message_ids.get(str(message.chat.id), message.message_id),
                parse_mode="HTML",
                reply_markup=kb
            )
            settings_message_ids[str(message.chat.id) + "_text"] = txt
        except ValueError:
            txt = (
                "❌ **Ошибка интервала** 🚫\n"
                "━━━━━━━━━━━━━━━━━━\n"
                "ℹ️ Введите число > 0\n"
                "➖ Отправьте \"-\" для сброса"
            )
            cardinal.telegram.bot.edit_message_text(
                txt,
                message.chat.id,
                settings_message_ids.get(str(message.chat.id), message.message_id),
                parse_mode="HTML"
            )
            settings_message_ids[str(message.chat.id) + "_text"] = txt
            cardinal.telegram.bot.register_next_step_handler_by_chat_id(message.chat.id, process_check_interval_change)

    def process_auto_send_interval_change(message):
        config = load_config()
        logger.info(f"{LOGGER_PREFIX} Обработка интервала автоотправки для {message.chat.id}")
        try:
            cardinal.telegram.bot.delete_message(message.chat.id, message.message_id)
        except Exception as e:
            logger.warning(f"{LOGGER_PREFIX} Не удалось удалить сообщение: {e}")

        kb = InlineKeyboardMarkup(row_width=1)
        kb.add(InlineKeyboardButton("⬅️ Назад", callback_data="auto_send_settings"))
        try:
            if message.text.strip() == "-":
                logger.info(f"{LOGGER_PREFIX} Интервал автоотправки не изменён")
                txt = "ℹ️ **Интервал не изменён** ⏰\n━━━━━━━━━━━━━━━━━━\n⬅️ Вернитесь в меню"
                cardinal.telegram.bot.edit_message_text(
                    txt,
                    message.chat.id,
                    settings_message_ids.get(str(message.chat.id), message.message_id),
                    parse_mode="HTML",
                    reply_markup=kb
                )
                settings_message_ids[str(message.chat.id) + "_text"] = txt
                return

            minutes = int(message.text.strip())
            if minutes < 1:
                raise ValueError("Число должно быть > 0")
            config["auto_send_interval_seconds"] = minutes * 60
            save_config(config)
            txt = f"✅ **Интервал обновлён** 🎉\n━━━━━━━━━━━━━━━━━━\n⏰ {minutes} мин"
            cardinal.telegram.bot.edit_message_text(
                txt,
                message.chat.id,
                settings_message_ids.get(str(message.chat.id), message.message_id),
                parse_mode="HTML",
                reply_markup=kb
            )
            settings_message_ids[str(message.chat.id) + "_text"] = txt
        except ValueError:
            txt = (
                "❌ **Ошибка интервала** 🚫\n"
                "━━━━━━━━━━━━━━━━━━\n"
                "ℹ️ Введите число > 0\n"
                "➖ Отправьте \"-\" для сброса"
            )
            cardinal.telegram.bot.edit_message_text(
                txt,
                message.chat.id,
                settings_message_ids.get(str(message.chat.id), message.message_id),
                parse_mode="HTML"
            )
            settings_message_ids[str(message.chat.id) + "_text"] = txt
            cardinal.telegram.bot.register_next_step_handler_by_chat_id(message.chat.id, process_auto_send_interval_change)

    def process_auto_ticket_message_change(message):
        config = load_config()
        logger.info(f"{LOGGER_PREFIX} Обработка текста автоотправки для {message.chat.id}")
        try:
            cardinal.telegram.bot.delete_message(message.chat.id, message.message_id)
        except Exception as e:
            logger.warning(f"{LOGGER_PREFIX} Не удалось удалить сообщение: {e}")

        kb = InlineKeyboardMarkup(row_width=1)
        kb.add(InlineKeyboardButton("⬅️ Назад", callback_data="auto_send_settings"))
        try:
            new_message = message.text.strip()
            if new_message == "-":
                logger.info(f"{LOGGER_PREFIX} Текст автоотправки не изменён")
                txt = "ℹ️ **Текст не изменён** 📬\n━━━━━━━━━━━━━━━━━━\n⬅️ Вернитесь в меню"
                cardinal.telegram.bot.edit_message_text(
                    txt,
                    message.chat.id,
                    settings_message_ids.get(str(message.chat.id), message.message_id),
                    parse_mode="HTML",
                    reply_markup=kb
                )
                settings_message_ids[str(message.chat.id) + "_text"] = txt
                return

            if not new_message or "{order_ids}" not in new_message:
                raise ValueError("Включите {order_ids}")
            config["auto_ticket_message"] = new_message
            save_config(config)
            preview = new_message.format(order_ids="#JTZ38MFP, #JTZ38MFG")
            txt = f"✅ **Текст обновлён** 🎉\n━━━━━━━━━━━━━━━━━━\n👀 **Пример**: {preview}"
            cardinal.telegram.bot.edit_message_text(
                txt,
                message.chat.id,
                settings_message_ids.get(str(message.chat.id), message.message_id),
                parse_mode="HTML",
                reply_markup=kb
            )
            settings_message_ids[str(message.chat.id) + "_text"] = txt
        except ValueError as ve:
            txt = (
                f"❌ **Ошибка текста** 🚫\n"
                f"━━━━━━━━━━━━━━━━━━\n"
                f"ℹ️ {str(ve)}\n"
                "➖ Отправьте \"-\" для сброса"
            )
            cardinal.telegram.bot.edit_message_text(
                txt,
                message.chat.id,
                settings_message_ids.get(str(message.chat.id), message.message_id),
                parse_mode="HTML"
            )
            settings_message_ids[str(message.chat.id) + "_text"] = txt
            cardinal.telegram.bot.register_next_step_handler_by_chat_id(message.chat.id, process_auto_ticket_message_change)

    def process_manual_ticket_message_change(message):
        config = load_config()
        logger.info(f"{LOGGER_PREFIX} Обработка текста ручной отправки для {message.chat.id}")
        try:
            cardinal.telegram.bot.delete_message(message.chat.id, message.message_id)
        except Exception as e:
            logger.warning(f"{LOGGER_PREFIX} Не удалось удалить сообщение: {e}")

        kb = InlineKeyboardMarkup(row_width=1)
        kb.add(InlineKeyboardButton("⬅️ Назад", callback_data="back_to_settings"))
        try:
            new_message = message.text.strip()
            if new_message == "-":
                logger.info(f"{LOGGER_PREFIX} Текст ручной отправки не изменён")
                txt = "ℹ️ **Текст не изменён** 📩\n━━━━━━━━━━━━━━━━━━\n⬅️ Вернитесь в меню"
                cardinal.telegram.bot.edit_message_text(
                    txt,
                    message.chat.id,
                    settings_message_ids.get(str(message.chat.id), message.message_id),
                    parse_mode="HTML",
                    reply_markup=kb
                )
                settings_message_ids[str(message.chat.id) + "_text"] = txt
                return

            if not new_message or "{order_id}" not in new_message:
                raise ValueError("Включите {order_id}")
            config["manual_ticket_message"] = new_message
            save_config(config)
            preview = new_message.format(order_id="#JTZ38MFP")
            txt = f"✅ **Текст обновлён** 🎉\n━━━━━━━━━━━━━━━━━━\n👀 **Пример**: {preview}"
            cardinal.telegram.bot.edit_message_text(
                txt,
                message.chat.id,
                settings_message_ids.get(str(message.chat.id), message.message_id),
                parse_mode="HTML",
                reply_markup=kb
            )
            settings_message_ids[str(message.chat.id) + "_text"] = txt
        except ValueError as ve:
            txt = (
                f"❌ **Ошибка текста** 🚫\n"
                f"━━━━━━━━━━━━━━━━━━\n"
                f"ℹ️ {str(ve)}\n"
                "➖ Отправьте \"-\" для сброса"
            )
            cardinal.telegram.bot.edit_message_text(
                txt,
                message.chat.id,
                settings_message_ids.get(str(message.chat.id), message.message_id),
                parse_mode="HTML"
            )
            settings_message_ids[str(message.chat.id) + "_text"] = txt
            cardinal.telegram.bot.register_next_step_handler_by_chat_id(message.chat.id, process_manual_ticket_message_change)

    def process_auto_send_limit_change(message):
        config = load_config()
        logger.info(f"{LOGGER_PREFIX} Обработка лимита заказов для {message.chat.id}")
        try:
            cardinal.telegram.bot.delete_message(message.chat.id, message.message_id)
        except Exception as e:
            logger.warning(f"{LOGGER_PREFIX} Не удалось удалить сообщение: {e}")

        kb = InlineKeyboardMarkup(row_width=1)
        kb.add(InlineKeyboardButton("⬅️ Назад", callback_data="auto_send_settings"))
        try:
            if message.text.strip() == "-":
                logger.info(f"{LOGGER_PREFIX} Лимит заказов не изменён")
                txt = "ℹ️ **Лимит не изменён** 🔢\n━━━━━━━━━━━━━━━━━━\n⬅️ Вернитесь в меню"
                cardinal.telegram.bot.edit_message_text(
                    txt,
                    message.chat.id,
                    settings_message_ids.get(str(message.chat.id), message.message_id),
                    parse_mode="HTML",
                    reply_markup=kb
                )
                settings_message_ids[str(message.chat.id) + "_text"] = txt
                return

            limit = int(message.text.strip())
            if limit < 1 or limit > 10:
                raise ValueError("Число должно быть 1-10")
            config["auto_send_order_limit"] = limit
            save_config(config)
            txt = f"✅ **Лимит обновлён** 🎉\n━━━━━━━━━━━━━━━━━━\n🔢 {limit} заказов"
            cardinal.telegram.bot.edit_message_text(
                txt,
                message.chat.id,
                settings_message_ids.get(str(message.chat.id), message.message_id),
                parse_mode="HTML",
                reply_markup=kb
            )
            settings_message_ids[str(message.chat.id) + "_text"] = txt
        except ValueError:
            txt = (
                "❌ **Ошибка лимита** 🚫\n"
                "━━━━━━━━━━━━━━━━━━\n"
                "ℹ️ Введите число 1-10\n"
                "➖ Отправьте \"-\" для сброса"
            )
            cardinal.telegram.bot.edit_message_text(
                txt,
                message.chat.id,
                settings_message_ids.get(str(message.chat.id), message.message_id),
                parse_mode="HTML"
            )
            settings_message_ids[str(message.chat.id) + "_text"] = txt
            cardinal.telegram.bot.register_next_step_handler_by_chat_id(message.chat.id, process_auto_send_limit_change)

    def start_auto_send():
        config = load_config()
        chat_id = config["telegram_chat_id"]
        while True:
            try:
                config = load_config()
                logger.info(f"{LOGGER_PREFIX} Автоотправка запущена")
                if chat_id:
                    auto_ticket.auto_send_tickets(chat_id)
                else:
                    logger.warning(f"{LOGGER_PREFIX} telegram_chat_id не задан")
                    auto_ticket.auto_send_tickets()
                time.sleep(config["auto_send_interval_seconds"])
            except Exception as e:
                logger.error(f"{LOGGER_PREFIX} Ошибка автоотправки: {e}")
                time.sleep(60)

    import threading
    auto_send_thread = threading.Thread(target=start_auto_send, daemon=True)
    auto_send_thread.start()

    cardinal.add_telegram_commands(UUID, [
        ("autotickets_settings", "Настройки AutoTicket", True),
        ("check_tickets", "Проверка и отправка тикетов", True)
    ])

    cardinal.telegram.msg_handler(manual_check_orders, commands=["check_tickets"])
    cardinal.telegram.msg_handler(settings_menu, commands=["autotickets_settings"])

BIND_TO_PRE_INIT = [init_plugin]
BIND_TO_DELETE = None