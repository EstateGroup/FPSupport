from __future__ import annotations
import time
from typing import TYPE_CHECKING, Dict, List

if TYPE_CHECKING:
    from e import e

from logging import getLogger

logger = getLogger("FPC.lot_cleaner_Pro")

NAME = "Lot Cleaner Pro"
VERSION = "1.2"
DESCRIPTION = "Плагин для удаления лотов"
CREDITS = "@rcnnu | https://t.me/FunPay_plugin"
UUID = "73d1e89f-df05-4117-82d9-4085883ebf9d"
SETTINGS_PAGE = False

DELAY_BETWEEN_DELETIONS = 0.2
MAX_RETRIES = 3

LANGUAGES = {
    "ru": {
        "flag": "🇷🇺",
        "start": "🚀 Запускаю очистку лотов...",
        "no_lots": "🙅 Нет лотов для удаления",
        "error": "😿 Ошибка: Не удалось получить лоты\n{}",
        "found": "🕵️‍♀️ Найдено лотов: {}",
        "report": "🏁 Готово!\n\nВсего лотов: {}\nУдалено: {}\nОшибок: {}\nВремя: {:.1f} сек\n\n{}",
        "all_success": "💯 Все удалены",
        "some_errors": "⚠️ Были проблемы",
        "critical": "💥 КРИТИЧЕСКАЯ ОШИБКА:\n{}",
        "language_set": "Язык установлен: Русский 🇷🇺",
        "language_menu": "Выберите язык 🌐:\n/ru - Русский 🇷🇺\n/en - English 🇬🇧\n/by - Беларуская 🇧🇾\n/zh - 中文 🇨🇳",
        "clean_all_cmd": "удалить ВСЕ лоты ",
        "language_cmd": "показать меню выбора языка ",
        "clean_select_cmd": "выбрать и удалить лоты",
        "select_prompt": "Выберите лоты для удаления. Напишите номера через запятую, например: 1,3,5",
        "selection_received": "Удаляю выбранные лоты...",
        "invalid_selection": "Некорректный ввод. Попробуйте снова.",
        "lot_list_item": "{}. {} (ID: {})",
        "no_valid_lots": "Не удалось найти ни одного лота по указанным номерам.",
        "deleting_progress": "⏳ Удаление: {}/{} ({}%)",
    },
    "en": {
        "flag": "🇬🇧",
        "start": "🚀 Starting lot cleanup...",
        "no_lots": "🙅 No lots found",
        "error": "😿 Error: Failed to get lots\n{}",
        "found": "🔍 Found lots: {}",
        "report": "🏁 Done!\n\nTotal lots: {}\nDeleted: {}\nErrors: {}\nTime: {:.1f} sec\n\n{}",
        "all_success": "💯 All deleted",
        "some_errors": "⚠️ There were problems",
        "critical": "💥 CRITICAL ERROR:\n{}",
        "language_set": "Language set to: English 🇬🇧",
        "language_menu": "Choose language:\n/ru - Russian 🇷🇺\n/en - English 🇬🇧\n/by - Belarusian 🇧🇾\n/zh - Chinese 🇨🇳",
        "clean_all_cmd": "delete ALL lots",
        "language_cmd": "show language menu",
        "clean_select_cmd": "select and delete lots",
        "select_prompt": "Select lots to delete. Enter numbers like: 1,3,5",
        "selection_received": "Deleting selected lots...",
        "invalid_selection": "Invalid input. Please try again.",
        "lot_list_item": "{}. {} (ID: {})",
        "no_valid_lots": "No valid lots found for the given numbers.",
        "deleting_progress": "⏳ Deleting: {}/{} ({}%)",
    },
    "by": {
        "flag": "🇧🇾",
        "start": "🚀 Пачынаю чыстку латоў...",
        "no_lots": "🙅 Няма латоў для выдалення",
        "error": "😿 Памылка: Не атрымалася атрымаць латы\n{}",
        "found": "🕵️‍♀️ Знойдзена латоў: {}",
        "report": "🏁 Гатова!\n\nУсяго латоў: {}\nВыдалена: {}\nПамылак: {}\nЧас: {:.1f} сек\n\n{}",
        "all_success": "💯 Усе выдалены",
        "some_errors": "⚠️ Былi праблемы",
        "critical": "💥 КРЫТЫЧНАЯ ПАМЫЛКА:\n{}",
        "language_set": "Мова ўсталявана: Беларуская 🇧🇾",
        "language_menu": "Выберыце мову:\n/ru - Руская 🇷🇺\n/en - Англійская 🇬🇧\n/by - Беларуская 🇧🇾\n/zh - Кітайская 🇨🇳",
        "clean_all_cmd": "выдаліць УСЕ латы",
        "language_cmd": "паказаць меню выбару мовы",
        "clean_select_cmd": "выбраць і выдаліць латы",
        "select_prompt": "Выберыце латы для выдалення. Напішыце нумары праз коску, напрыклад: 1,3,5",
        "selection_received": "Выдаляю абраныя латы...",
        "invalid_selection": "Няправільны ўвод. Паспрабуйце яшчэ раз.",
        "lot_list_item": "{}. {} (ID: {})",
        "no_valid_lots": "Не атрымалася знайсці нi аднаго лата па пададзеных нумарах.",
        "deleting_progress": "⏳ Выдаленне: {}/{} ({}%)",
    },
    "zh": {
        "flag": "🇨🇳",
        "start": "🚀 开始清理商品...",
        "no_lots": "🙅 没有找到商品",
        "error": "😿 错误: 无法获取商品\n{}",
        "found": "🔍 找到商品: {}",
        "report": "🏁 完成!\n\n总商品数: {}\n已删除: {}\n错误: {}\n时间: {:.1f} 秒\n\n{}",
        "all_success": "💯 全部删除成功",
        "some_errors": "⚠️ 出现了一些问题",
        "critical": "💥 严重错误:\n{}",
        "language_set": "语言设置为: 中文 🇨🇳",
        "language_menu": "选择语言:\n/ru - 俄语 🇷🇺\n/en - 英语 🇬🇧\n/by - 白俄罗斯语 🇧🇾\n/zh - 中文 🇨🇳",
        "clean_all_cmd": "删除所有商品",
        "language_cmd": "显示语言菜单",
        "clean_select_cmd": "选择并删除商品",
        "select_prompt": "选择要删除的商品。输入数字，例如: 1,3,5",
        "selection_received": "正在删除选定的商品...",
        "invalid_selection": "输入无效。请重试。",
        "lot_list_item": "{}. {} (ID: {})",
        "no_valid_lots": "根据提供的编号找不到有效的商品。",
        "deleting_progress": "⏳ 删除中: {}/{} ({}%)",
    }
}

class LanguageState:
    current_lang = "ru"
    lot_selection_buffer: Dict[int, List] = {}

def get_text(key: str) -> str:
    return LANGUAGES[LanguageState.current_lang][key]

def update_telegram_commands(e: e):
    e.add_telegram_commands(UUID, [
        ("clean_all", get_text("clean_all_cmd"), True),
        ("clean_select", get_text("clean_select_cmd"), True),
        ("language", get_text("language_cmd"), False)
    ])

def delete_lot_with_retry(e: e, lot_id: str, max_retries: int = MAX_RETRIES) -> bool:
    for attempt in range(max_retries):
        try:
            e.account.delete_lot(lot_id)
            return True
        except Exception as ex:
            logger.warning(f"Attempt {attempt + 1} failed to delete lot {lot_id}: {ex}")
            if attempt < max_retries - 1:
                time.sleep(DELAY_BETWEEN_DELETIONS * 2)
    return False

def send_progress_update(bot, chat_id: int, current: int, total: int):
    if total == 0:
        return
    percent = int((current / total) * 100)
    if percent % 10 == 0 or current == total:
        bot.send_message(
            chat_id, 
            get_text("deleting_progress").format(current, total, percent),
            disable_notification=True
        )

def process_lot_deletion(e: e, bot, chat_id: int, lots: List, is_selective: bool = False):
    total = len(lots)
    if not total:
        bot.send_message(chat_id, get_text("no_lots"))
        return

    success = 0
    start_time = time.time()
    
    for index, lot in enumerate(lots, 1):
        try:
            if delete_lot_with_retry(e, lot.id):
                success += 1
            else:
                logger.warning(f"Failed to delete lot after retries: {lot.id}")
            
            send_progress_update(bot, chat_id, index, total)
            
            time.sleep(DELAY_BETWEEN_DELETIONS)
        except Exception as ex:
            logger.error(f"Unexpected error while deleting lot {lot.id}: {ex}")

    time_spent = time.time() - start_time
    status = get_text("all_success") if success == total else get_text("some_errors")
    report = get_text("report").format(
        total, success, total - success, time_spent, status
    )
    bot.send_message(chat_id, report)

    if is_selective and chat_id in LanguageState.lot_selection_buffer:
        del LanguageState.lot_selection_buffer[chat_id]

def init_commands(e: e):
    if not hasattr(e, 'telegram') or not e.telegram:
        logger.error("Telegram bot not available")
        return

    bot = e.telegram.bot

    def execute_cleanup(message):
        try:
            bot.send_message(message.chat.id, get_text("start"))
            try:
                profile = e.account.get_user(e.account.id)
                all_lots = profile.get_lots()
                if not all_lots:
                    bot.send_message(message.chat.id, get_text("no_lots"))
                    return
            except Exception as ex:
                bot.send_message(message.chat.id, get_text("error").format(ex))
                logger.error(f"Lot fetch error: {ex}")
                return

            bot.send_message(message.chat.id, get_text("found").format(len(all_lots)))
            process_lot_deletion(e, bot, message.chat.id, all_lots)

        except Exception as ex:
            bot.send_message(message.chat.id, get_text("critical").format(ex))
            logger.critical(f"Plugin crash: {ex}", exc_info=True)

    def selective_cleanup(message):
        try:
            profile = e.account.get_user(e.account.id)
            lots = profile.get_lots()
            if not lots:
                bot.send_message(message.chat.id, get_text("no_lots"))
                return

            LanguageState.lot_selection_buffer[message.chat.id] = lots

            chunk_size = 10
            for i in range(0, len(lots), chunk_size):
                chunk = lots[i:i + chunk_size]
                lot_list = "\n".join(
                    get_text("lot_list_item").format(i + j + 1, lot.title, lot.id)
                    for j, lot in enumerate(chunk)
                )
                
                if i == 0:
                    msg = f"{lot_list}\n\n{get_text('select_prompt')}"
                else:
                    msg = lot_list
                
                bot.send_message(message.chat.id, msg)

        except Exception as ex:
            error_msg = f"😿 Ошибка: {str(ex)}"
            if "message is too long" in str(ex):
                error_msg = "😿 Ошибка: Слишком много лотов. Используйте команду /clean_all для полной очистки."
            bot.send_message(message.chat.id, error_msg)
            logger.error(f"Selective cleanup error: {ex}")

    def handle_selection_reply(message):
        chat_id = message.chat.id
        if chat_id not in LanguageState.lot_selection_buffer:
            return

        input_text = message.text.strip()
        try:
            selected = set()
            parts = input_text.split(',')
            for part in parts:
                part = part.strip()
                if '-' in part:
                    start, end = map(int, part.split('-'))
                    selected.update(range(start, end + 1))
                else:
                    selected.add(int(part))
            
            indexes = [i - 1 for i in selected]
            lots = LanguageState.lot_selection_buffer[chat_id]
            to_delete = [lots[i] for i in indexes if 0 <= i < len(lots)]

            if not to_delete:
                bot.send_message(chat_id, get_text("no_valid_lots"))
                return

            bot.send_message(chat_id, get_text("selection_received"))
            process_lot_deletion(e, bot, chat_id, to_delete, is_selective=True)

        except Exception as ex:
            bot.send_message(chat_id, get_text("invalid_selection"))
            logger.error(f"Invalid selection input: {ex}")

    def show_language_menu(message):
        bot.send_message(message.chat.id, get_text("language_menu"))

    def set_language(message, lang):
        if lang in LANGUAGES:
            LanguageState.current_lang = lang
            bot.send_message(message.chat.id, get_text("language_set"))
            update_telegram_commands(e)
        else:
            bot.send_message(message.chat.id, "Invalid language code")

    update_telegram_commands(e)

    e.telegram.msg_handler(execute_cleanup, commands=["clean_all"])
    e.telegram.msg_handler(selective_cleanup, commands=["clean_select"])
    e.telegram.msg_handler(show_language_menu, commands=["language"])
    e.telegram.msg_handler(
        handle_selection_reply,
        func=lambda m: m.chat.id in LanguageState.lot_selection_buffer
    )
    e.telegram.msg_handler(lambda m: set_language(m, "ru"), commands=["ru"])
    e.telegram.msg_handler(lambda m: set_language(m, "en"), commands=["en"])
    e.telegram.msg_handler(lambda m: set_language(m, "by"), commands=["by"])
    e.telegram.msg_handler(lambda m: set_language(m, "zh"), commands=["zh"])

BIND_TO_PRE_INIT = [init_commands]
BIND_TO_DELETE = None
