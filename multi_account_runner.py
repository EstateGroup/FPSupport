"""
Точка входа для запуска дополнительного аккаунта FunPay Manager как подпроцесса.
Используется системой мультиаккаунтов.
"""
import argparse
import os
import sys
import logging
import threading
import time

logger = logging.getLogger("MultiAccountRunner")


def main():
    parser = argparse.ArgumentParser(description="FunPay Manager Multi-Account Runner")
    parser.add_argument("--account-id", required=True, help="ID аккаунта из multi_accounts.json")
    parser.add_argument("--config-dir", required=True, help="Путь к директории конфига аккаунта")
    args = parser.parse_args()

    account_id = args.account_id
    config_dir = args.config_dir

    if not os.path.exists(config_dir):
        logger.error(f"Директория конфига не найдена: {config_dir}")
        sys.exit(1)

    config_file = os.path.join(config_dir, "_main.cfg")
    if not os.path.exists(config_file):
        logger.error(f"Конфиг файл не найден: {config_file}")
        sys.exit(1)

    # Переключаем рабочий конфиг на аккаунт
    os.environ["FPM_ACCOUNT_ID"] = account_id
    os.environ["FPM_CONFIG_DIR"] = config_dir

    from fpsupport import funpayautobot
    from Utils import FPManager
    import configparser

    config = configparser.ConfigParser(delimiters=(":",))
    config.read(config_file, encoding="utf-8")

    def save_stats_periodically(cardinal, acc_id):
        """Периодически сохраняет статистику аккаунта."""
        while True:
            try:
                time.sleep(60)  # Обновляем каждую минуту
                if cardinal.account:
                    stats = {
                        "username": cardinal.account.username,
                        "id": cardinal.account.id,
                        "balance_rub": cardinal.account.balance.rub if cardinal.account.balance else 0,
                        "balance_usd": cardinal.account.balance.usd if cardinal.account.balance else 0,
                        "balance_eur": cardinal.account.balance.eur if cardinal.account.balance else 0,
                        "active_orders": len(cardinal.account.get_active_orders()) if hasattr(cardinal.account, 'get_active_orders') else 0,
                        "total_lots": len(cardinal.account.get_lots()) if hasattr(cardinal.account, 'get_lots') else 0,
                        "last_update": time.strftime("%d.%m.%Y %H:%M:%S")
                    }
                    FPManager.save_account_stats(acc_id, stats)
            except Exception as e:
                logger.error(f"[MultiAccount] Ошибка сохранения статистики: {e}")

    try:
        cardinal = funpayautobot(config, False)
        cardinal.init()

        # Запускаем фоновую задачу для сохранения статистики
        stats_thread = threading.Thread(target=save_stats_periodically, args=(cardinal, account_id), daemon=True)
        stats_thread.start()

        cardinal.run()
    except KeyboardInterrupt:
        logger.info(f"[MultiAccount] Аккаунт {account_id} остановлен.")
    except Exception as e:
        logger.error(f"[MultiAccount] Ошибка аккаунта {account_id}: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
