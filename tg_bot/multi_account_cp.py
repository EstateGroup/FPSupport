"""
Модуль управления мультиаккаунтами через Telegram бот.
"""
from __future__ import annotations

import os
import sys
import subprocess
import logging
import uuid
import configparser
import shutil
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from fpsupport import funpayautobot as FPSupport

from tg_bot import CBT
from tg_bot.static_keyboards import CLEAR_STATE_BTN
from locales.localizer import Localizer
from Utils import FPManager

from telebot.types import InlineKeyboardMarkup as K, InlineKeyboardButton as B, Message, CallbackQuery
from telebot.apihelper import ApiTelegramException

logger = logging.getLogger("TGBot")
localizer = Localizer()
_ = localizer.translate

# Хранение процессов запущенных аккаунтов
_processes: dict[str, subprocess.Popen] = {}


def _get_status_text(account: dict) -> str:
    status = account.get("status", "stopped")
    if status == "running":
        return _("ma_status_running")
    elif status == "proxy_error":
        return _("ma_status_proxy_error")
    elif status == "starting":
        return _("ma_status_starting")
    return _("ma_status_stopped")


def _accounts_list_kb(accounts: dict) -> K:
    kb = K()
    for acc_id, acc in accounts.items():
        status = acc.get("status", "stopped")
        proxy_status = "🌐" if acc.get("proxy") else "❌"
        if status == "running":
            icon = "🟢"
        elif status == "proxy_error":
            icon = "⚠️"
        else:
            icon = "🔴"
        name = acc.get("name", acc_id[:8])
        bot_username = acc.get("bot_username", "")
        label = f"{icon}{proxy_status} {name}"
        if bot_username:
            label += f" (@{bot_username})"
        kb.add(B(label, callback_data=f"{CBT.EDIT_MULTI_ACCOUNT}:{acc_id}"))
    kb.add(B(_("ma_add_account"), callback_data=CBT.ADD_MULTI_ACCOUNT))
    kb.add(B(_("gl_back"), callback_data=CBT.MAIN2))
    return kb


def _account_detail_kb(acc_id: str, account: dict) -> K:
    kb = K()
    status = account.get("status", "stopped")
    if status == "running":
        kb.add(B(_("ma_stop"), callback_data=f"{CBT.STOP_MULTI_ACCOUNT}:{acc_id}"))
    else:
        kb.add(B(_("ma_start"), callback_data=f"{CBT.START_MULTI_ACCOUNT}:{acc_id}"))
    kb.add(B(_("ma_edit_proxy"), callback_data=f"{CBT.EDIT_MULTI_ACCOUNT_PROXY}:{acc_id}"))
    kb.add(B(_("ma_delete"), callback_data=f"{CBT.DELETE_MULTI_ACCOUNT}:{acc_id}"))
    kb.add(B(_("ma_back"), callback_data=CBT.MULTI_ACCOUNT_LIST))
    return kb


def _confirm_delete_kb(acc_id: str) -> K:
    return K() \
        .row(B(_("ma_confirm_yes"), callback_data=f"ma_confirm_del:{acc_id}"),
             B(_("ma_confirm_no"), callback_data=f"{CBT.EDIT_MULTI_ACCOUNT}:{acc_id}"))


def _start_account_process(acc_id: str, accounts: dict) -> bool:
    """Запускает подпроцесс для аккаунта."""
    account = accounts.get(acc_id)
    if not account:
        return False

    config_dir = account.get("config_dir", os.path.join("storage", "multi_accounts", acc_id, "configs"))
    os.makedirs(config_dir, exist_ok=True)

    config_file = os.path.join(config_dir, "_main.cfg")
    if not os.path.exists(config_file):
        return False

    # Обновляем golden_key и прокси в конфиге аккаунта
    config = configparser.ConfigParser(delimiters=(":",), interpolation=None)
    config.optionxform = str
    config.read(config_file, encoding="utf-8")

    if "FunPay" not in config:
        config["FunPay"] = {}
    config["FunPay"]["golden_key"] = account["golden_key"]

    if "Proxy" not in config:
        config["Proxy"] = {}
    proxy = account.get("proxy", "")
    if proxy:
        login, password, ip, port = FPManager.validate_proxy(proxy)
        config["Proxy"]["enable"] = "1"
        config["Proxy"]["login"] = login
        config["Proxy"]["password"] = password
        config["Proxy"]["ip"] = ip
        config["Proxy"]["port"] = port

    with open(config_file, "w", encoding="utf-8") as f:
        config.write(f)

    try:
        proc = subprocess.Popen(
            [sys.executable, "multi_account_runner.py",
             "--account-id", acc_id,
             "--config-dir", config_dir],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE
        )
        _processes[acc_id] = proc
        account["status"] = "running"
        account["pid"] = proc.pid
        FPManager.save_multi_accounts(accounts)
        return True
    except Exception as e:
        logger.error(f"[MultiAccount] Ошибка запуска аккаунта {acc_id}: {e}")
        account["status"] = "stopped"
        FPManager.save_multi_accounts(accounts)
        return False


def _stop_account_process(acc_id: str, accounts: dict) -> bool:
    """Останавливает подпроцесс аккаунта."""
    account = accounts.get(acc_id)
    if not account:
        return False

    proc = _processes.get(acc_id)
    if proc and proc.poll() is None:
        try:
            proc.terminate()
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
        except Exception:
            pass

    _processes.pop(acc_id, None)
    account["status"] = "stopped"
    account["pid"] = None
    FPManager.save_multi_accounts(accounts)
    return True


def init_multi_account_cp(cardinal: FPSupport, *args):
    logger.info("[MultiAccount] Инициализация панели управления мультиаккаунтами...")
    tg = cardinal.telegram
    bot = tg.bot

    def _safe_edit_text(chat_id, message_id, text, reply_markup):
        """Редактирует сообщение, обрабатывая переход с фото на текст."""
        try:
            bot.edit_message_text(text, chat_id, message_id, reply_markup=reply_markup)
        except ApiTelegramException:
            try:
                bot.delete_message(chat_id, message_id)
            except:
                pass
            bot.send_message(chat_id, text, reply_markup=reply_markup)

    def open_accounts_list(c: CallbackQuery):
        accounts = FPManager.load_multi_accounts()
        text = f"{_('ma_title')}\n\n{_('ma_desc')}"
        if not accounts:
            text += f"\n\n{_('ma_no_accounts')}"
        _safe_edit_text(c.message.chat.id, c.message.id, text,
                        _accounts_list_kb(accounts))
        bot.answer_callback_query(c.id)

    def open_account_detail(c: CallbackQuery):
        acc_id = c.data.split(":")[1]
        accounts = FPManager.load_multi_accounts()
        account = accounts.get(acc_id)
        if not account:
            bot.answer_callback_query(c.id, _("ma_account_not_found"), show_alert=True)
            return

        # Загружаем статистику аккаунта
        stats = FPManager.load_account_stats(acc_id)

        gk = account["golden_key"]
        name = account.get("name", acc_id[:8])
        proxy = account.get("proxy", "—")
        status = _get_status_text(account)

        # Формируем текст с полной информацией
        if stats and account.get("status") == "running":
            text = _("ma_account_info_full",
                     name,
                     gk[:4], gk[-4:],
                     stats.get("username", "—"),
                     stats.get("id", "—"),
                     stats.get("balance_rub", 0),
                     stats.get("balance_usd", 0),
                     stats.get("balance_eur", 0),
                     stats.get("active_orders", 0),
                     stats.get("total_lots", 0),
                     proxy,
                     status,
                     stats.get("last_update", "—"))
        else:
            text = _("ma_account_info",
                     name,
                     gk[:4], gk[-4:],
                     proxy,
                     status)

        _safe_edit_text(c.message.chat.id, c.message.id, text,
                        _account_detail_kb(acc_id, account))
        bot.answer_callback_query(c.id)

    def start_account(c: CallbackQuery):
        acc_id = c.data.split(":")[1]
        accounts = FPManager.load_multi_accounts()
        account = accounts.get(acc_id)
        if not account:
            bot.answer_callback_query(c.id, _("ma_account_not_found"), show_alert=True)
            return

        # Проверяем прокси перед запуском
        proxy = account.get("proxy", "")
        if not proxy:
            bot.answer_callback_query(c.id, _("ma_proxy_required"), show_alert=True)
            return

        ok, msg = FPManager.check_proxy_health(proxy)
        if not ok:
            bot.answer_callback_query(c.id, _("ma_proxy_check_fail", msg), show_alert=True)
            account["status"] = "proxy_error"
            FPManager.save_multi_accounts(accounts)
            c.data = f"{CBT.EDIT_MULTI_ACCOUNT}:{acc_id}"
            open_account_detail(c)
            return

        if _start_account_process(acc_id, accounts):
            bot.answer_callback_query(c.id, _("ma_account_started", account.get("name", acc_id[:8])))
        else:
            bot.answer_callback_query(c.id, "❌ Failed to start", show_alert=True)
        c.data = f"{CBT.EDIT_MULTI_ACCOUNT}:{acc_id}"
        open_account_detail(c)

    def stop_account(c: CallbackQuery):
        acc_id = c.data.split(":")[1]
        accounts = FPManager.load_multi_accounts()
        account = accounts.get(acc_id)
        if not account:
            bot.answer_callback_query(c.id, _("ma_account_not_found"), show_alert=True)
            return

        _stop_account_process(acc_id, accounts)
        bot.answer_callback_query(c.id, _("ma_account_stopped", account.get("name", acc_id[:8])))
        c.data = f"{CBT.EDIT_MULTI_ACCOUNT}:{acc_id}"
        open_account_detail(c)

    def ask_delete_account(c: CallbackQuery):
        acc_id = c.data.split(":")[1]
        accounts = FPManager.load_multi_accounts()
        account = accounts.get(acc_id)
        if not account:
            bot.answer_callback_query(c.id, _("ma_account_not_found"), show_alert=True)
            return

        _safe_edit_text(c.message.chat.id, c.message.id,
                        _("ma_confirm_delete", account.get("name", acc_id[:8])),
                        _confirm_delete_kb(acc_id))
        bot.answer_callback_query(c.id)

    def confirm_delete_account(c: CallbackQuery):
        acc_id = c.data.split(":")[1]
        accounts = FPManager.load_multi_accounts()
        account = accounts.get(acc_id)
        if not account:
            bot.answer_callback_query(c.id, _("ma_account_not_found"), show_alert=True)
            return

        name = account.get("name", acc_id[:8])
        _stop_account_process(acc_id, accounts)
        accounts.pop(acc_id, None)
        FPManager.save_multi_accounts(accounts)

        # Удаляем директорию конфига
        config_dir = os.path.join("storage", "multi_accounts", acc_id)
        if os.path.exists(config_dir):
            shutil.rmtree(config_dir, ignore_errors=True)

        bot.answer_callback_query(c.id, _("ma_account_deleted", name))
        c.data = CBT.MULTI_ACCOUNT_LIST
        open_accounts_list(c)

    def act_add_account(c: CallbackQuery):
        result = bot.send_message(c.message.chat.id, _("ma_enter_golden_key"),
                                  reply_markup=CLEAR_STATE_BTN())
        tg.set_state(c.message.chat.id, result.id, c.from_user.id,
                     CBT.ADD_MULTI_ACCOUNT, {})
        bot.answer_callback_query(c.id)

    def process_golden_key(m: Message):
        tg.clear_state(m.chat.id, m.from_user.id, True)
        golden_key = m.text.strip()
        if len(golden_key) != 32 or golden_key != golden_key.lower() or len(golden_key.split()) != 1:
            bot.send_message(m.chat.id, _("ma_invalid_golden_key"))
            return

        result = bot.send_message(m.chat.id, _("ma_enter_bot_token"),
                                  reply_markup=CLEAR_STATE_BTN())
        tg.set_state(m.chat.id, result.id, m.from_user.id,
                     "ma_enter_bot_token", {"golden_key": golden_key})

    def process_bot_token(m: Message):
        state = tg.get_state(m.chat.id, m.from_user.id)
        tg.clear_state(m.chat.id, m.from_user.id, True)

        if not state or "golden_key" not in state.get("data", {}):
            bot.send_message(m.chat.id, "❌ State lost. Try again.")
            return

        bot_token = m.text.strip()
        # Проверяем формат токена
        try:
            import telebot
            test_bot = telebot.TeleBot(bot_token)
            bot_username = test_bot.get_me().username
        except Exception as e:
            bot.send_message(m.chat.id, _("ma_invalid_bot_token"))
            return

        golden_key = state["data"]["golden_key"]
        result = bot.send_message(m.chat.id, _("ma_enter_bot_password"),
                                  reply_markup=CLEAR_STATE_BTN())
        tg.set_state(m.chat.id, result.id, m.from_user.id,
                     "ma_enter_bot_password", {"golden_key": golden_key, "bot_token": bot_token, "bot_username": bot_username})

    def process_bot_password(m: Message):
        state = tg.get_state(m.chat.id, m.from_user.id)
        tg.clear_state(m.chat.id, m.from_user.id, True)

        if not state or "golden_key" not in state.get("data", {}):
            bot.send_message(m.chat.id, "❌ State lost. Try again.")
            return

        password = m.text.strip()
        # Проверяем пароль
        if len(password) < 8 or password.lower() == password or password.upper() == password or not any([i.isdigit() for i in password]):
            bot.send_message(m.chat.id, _("ma_invalid_password"))
            return

        golden_key = state["data"]["golden_key"]
        bot_token = state["data"]["bot_token"]
        bot_username = state["data"]["bot_username"]

        result = bot.send_message(m.chat.id, _("ma_enter_proxy"),
                                  reply_markup=CLEAR_STATE_BTN())
        tg.set_state(m.chat.id, result.id, m.from_user.id,
                     CBT.ADD_MULTI_ACCOUNT_PROXY, {
                         "golden_key": golden_key,
                         "bot_token": bot_token,
                         "bot_username": bot_username,
                         "password": password
                     })

    def process_proxy(m: Message):
        state = tg.get_state(m.chat.id, m.from_user.id)
        tg.clear_state(m.chat.id, m.from_user.id, True)

        if not state or "golden_key" not in state.get("data", {}):
            bot.send_message(m.chat.id, "❌ State lost. Try again.")
            return

        proxy = m.text.strip()
        if not FPManager.validate_proxy_format(proxy):
            bot.send_message(m.chat.id, _("ma_invalid_proxy"))
            return

        # Проверяем прокси
        ok, msg = FPManager.check_proxy_health(proxy)
        if not ok:
            bot.send_message(m.chat.id, _("ma_proxy_check_fail", msg))
            return

        data = state["data"]
        data["proxy"] = proxy

        # Сразу создаем аккаунт (имя берется из golden_key)
        tg.clear_state(m.chat.id, m.from_user.id, True)

        golden_key = data["golden_key"]
        bot_token = data.get("bot_token")
        bot_username = data.get("bot_username")
        password = data.get("password")

        # Имя аккаунта = golden_key
        name = golden_key

        acc_id = str(uuid.uuid4())

        # Создаем директорию для конфига нового аккаунта
        config_dir = os.path.join("storage", "multi_accounts", acc_id, "configs")
        os.makedirs(config_dir, exist_ok=True)

        # Создаем полный конфиг для нового бота
        from Utils.FPManager import hash_password
        config = configparser.ConfigParser(delimiters=(":",), interpolation=None)
        config.optionxform = str

        # Копируем структуру из default_config (first_setup.py)
        from first_setup import default_config
        config.read_dict(default_config)

        # Устанавливаем параметры аккаунта
        config.set("FunPay", "golden_key", golden_key)
        config.set("Telegram", "enabled", "1")
        config.set("Telegram", "token", bot_token)
        config.set("Telegram", "secretKeyHash", hash_password(password))

        # Устанавливаем прокси (обязательно!)
        login, pwd, ip, port = FPManager.validate_proxy(proxy)
        config.set("Proxy", "enable", "1")
        config.set("Proxy", "check", "1")
        config.set("Proxy", "login", login)
        config.set("Proxy", "password", pwd)
        config.set("Proxy", "ip", ip)
        config.set("Proxy", "port", port)

        # Сохраняем конфиг
        config_path = os.path.join(config_dir, "_main.cfg")
        with open(config_path, "w", encoding="utf-8") as f:
            config.write(f)

        # Копируем плагины
        plugins_source = "plugins"
        plugins_dest = os.path.join("storage", "multi_accounts", acc_id, "plugins")
        if os.path.exists(plugins_source):
            shutil.copytree(plugins_source, plugins_dest, dirs_exist_ok=True)

        # Сохраняем информацию об аккаунте
        accounts = FPManager.load_multi_accounts()
        accounts[acc_id] = {
            "golden_key": golden_key,
            "bot_token": bot_token,
            "bot_username": bot_username,
            "proxy": proxy,
            "name": name,
            "enabled": True,
            "status": "stopped",
            "pid": None,
            "config_dir": config_dir
        }
        FPManager.save_multi_accounts(accounts)

        kb = K().add(B(_("ma_back"), callback_data=CBT.MULTI_ACCOUNT_LIST))
        bot.send_message(m.chat.id, _("ma_account_added", name, bot_username), reply_markup=kb)

    def act_edit_proxy(c: CallbackQuery):
        acc_id = c.data.split(":")[1]
        result = bot.send_message(c.message.chat.id, _("ma_enter_proxy"),
                                  reply_markup=CLEAR_STATE_BTN())
        tg.set_state(c.message.chat.id, result.id, c.from_user.id,
                     CBT.EDIT_MULTI_ACCOUNT_PROXY, {"account_id": acc_id})
        bot.answer_callback_query(c.id)

    def process_edit_proxy(m: Message):
        state = tg.get_state(m.chat.id, m.from_user.id)
        tg.clear_state(m.chat.id, m.from_user.id, True)

        if not state or "account_id" not in state.get("data", {}):
            bot.send_message(m.chat.id, "❌ State lost. Try again.")
            return

        proxy = m.text.strip()
        if not FPManager.validate_proxy_format(proxy):
            bot.send_message(m.chat.id, _("ma_invalid_proxy"))
            return

        acc_id = state["data"]["account_id"]
        accounts = FPManager.load_multi_accounts()
        account = accounts.get(acc_id)
        if not account:
            bot.send_message(m.chat.id, _("ma_account_not_found"))
            return

        account["proxy"] = proxy
        if account.get("status") == "proxy_error":
            account["status"] = "stopped"
        FPManager.save_multi_accounts(accounts)

        kb = K().add(B(_("ma_back"), callback_data=CBT.MULTI_ACCOUNT_LIST))
        bot.send_message(m.chat.id, _("ma_proxy_updated", account.get("name", acc_id[:8])),
                         reply_markup=kb)

    # Регистрация callback хэндлеров
    tg.cbq_handler(open_accounts_list, lambda c: c.data == CBT.MULTI_ACCOUNT_LIST)
    tg.cbq_handler(open_account_detail, lambda c: c.data.startswith(f"{CBT.EDIT_MULTI_ACCOUNT}:"))
    tg.cbq_handler(start_account, lambda c: c.data.startswith(f"{CBT.START_MULTI_ACCOUNT}:"))
    tg.cbq_handler(stop_account, lambda c: c.data.startswith(f"{CBT.STOP_MULTI_ACCOUNT}:"))
    tg.cbq_handler(ask_delete_account, lambda c: c.data.startswith(f"{CBT.DELETE_MULTI_ACCOUNT}:"))
    tg.cbq_handler(confirm_delete_account, lambda c: c.data.startswith("ma_confirm_del:"))
    tg.cbq_handler(act_add_account, lambda c: c.data == CBT.ADD_MULTI_ACCOUNT)
    tg.cbq_handler(act_edit_proxy, lambda c: c.data.startswith(f"{CBT.EDIT_MULTI_ACCOUNT_PROXY}:"))

    # Регистрация message хэндлеров (ввод текста по состоянию)
    tg.msg_handler(process_golden_key,
                   func=lambda m: tg.check_state(m.chat.id, m.from_user.id, CBT.ADD_MULTI_ACCOUNT))
    tg.msg_handler(process_bot_token,
                   func=lambda m: tg.check_state(m.chat.id, m.from_user.id, "ma_enter_bot_token"))
    tg.msg_handler(process_bot_password,
                   func=lambda m: tg.check_state(m.chat.id, m.from_user.id, "ma_enter_bot_password"))
    tg.msg_handler(process_proxy,
                   func=lambda m: tg.check_state(m.chat.id, m.from_user.id, CBT.ADD_MULTI_ACCOUNT_PROXY))
    tg.msg_handler(process_edit_proxy,
                   func=lambda m: tg.check_state(m.chat.id, m.from_user.id, CBT.EDIT_MULTI_ACCOUNT_PROXY))

    logger.info("[MultiAccount] Обработчики мультиаккаунтов зарегистрированы ✓")


BIND_TO_PRE_INIT = [init_multi_account_cp]
