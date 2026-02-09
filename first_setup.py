"""
В данном модуле написана подпрограмма первичной настройки FPManager'а.
"""

import os
from configparser import ConfigParser
import time
import telebot
from colorama import Fore, Style
from Utils.FPManager import validate_proxy, hash_password

# locale#locale#locale
default_config = {
    "FunPay": {
        "golden_key": "",
        "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/109.0.0.0 Safari/537.36",
        "autoRaise": "0",
        "autoResponse": "0",
        "autoDelivery": "0",
        "multiDelivery": "0",
        "autoRestore": "0",
        "autoDisable": "0",
        "oldMsgGetMode": "0",
        "locale": "ru"
    },
    "Telegram": {
        "enabled": "0",
        "token": "",
        "secretKeyHash": "ХешСекретногоПароля",
        "blockLogin": "0"
    },

    "BlockList": {
        "blockDelivery": "0",
        "blockResponse": "0",
        "blockNewMessageNotification": "0",
        "blockNewOrderNotification": "0",
        "blockCommandNotification": "0"
    },

    "NewMessageView": {
        "includeMyMessages": "1",
        "includeFPMessages": "1",
        "includeBotMessages": "0",
        "notifyOnlyMyMessages": "0",
        "notifyOnlyFPMessages": "0",
        "notifyOnlyBotMessages": "0",
        "showImageName": "1"
    },

    "Greetings": {
        "ignoreSystemMessages": "0",
        "onlyNewChats": "0",
        "sendGreetings": "0",
        "greetingsText": "Привет, можешь оплачивать",
        "greetingsCooldown": "2"
    },

    "OrderConfirm": {
        "watermark": "1",
        "sendReply": "0",
        "replyText": "Здравствуйте, $username!\nПожалуйста, подтвердите заказ:\n$order_link"
    },

    "ReviewReply": {
        "star1Reply": "0",
        "star2Reply": "0",
        "star3Reply": "0",
        "star4Reply": "0",
        "star5Reply": "0",
        "star1ReplyText": "Спасибо за покупку $order_title",
        "star2ReplyText": "Спасибо за покупку $order_title",
        "star3ReplyText": "Спасибо за покупку $order_title",
        "star4ReplyText": "Спасибо за покупку $order_title",
        "star5ReplyText": "Спасибо за покупку $order_title",
    },

    "Proxy": {
        "enable": "0",
        "ip": "",
        "port": "",
        "login": "",
        "password": "",
        "check": "0"
    },

    "Other": {
        "watermark": "👨‍💻💎 FPSupport",
        "requestsDelay": "4",
        "language": "ru"
    }
}


def create_configs():
    if not os.path.exists("configs/auto_response.cfg"):
        with open("configs/auto_response.cfg", "w", encoding="utf-8"):
            ...

    if not os.path.exists("configs/auto_response.cfg"):
        with open("configs/auto_delivery.cfg", "w", encoding="utf-8"):
            ...


def create_config_obj(settings) -> ConfigParser:
    """
    Создает объект конфига с нужными настройками.

    :param settings: dict настроек.

    :return: объект конфига.
    """
    config = ConfigParser(delimiters=(":",), interpolation=None)
    config.optionxform = str
    config.read_dict(settings)
    return config


def contains_russian(text: str) -> bool:
    for char in text:
        if 'А' <= char <= 'я' or char in 'Ёё':
            return True
    return False


def first_setup():
    config = create_config_obj(default_config)
    sleep_time = 1

    print(f"\n{Fore.CYAN}{Style.BRIGHT}Конфиг не найден. Начинаем первичную настройку.{Style.RESET_ALL}")
    time.sleep(sleep_time)

    while True:
        print(f"\n{Fore.MAGENTA}{Style.BRIGHT}┌── {Fore.CYAN}"
              f"Введи golden_key от FunPay (найти можно через EditThisCookie){Style.RESET_ALL}")
        golden_key = input(f"{Fore.MAGENTA}{Style.BRIGHT}└───> {Style.RESET_ALL}").strip()
        if len(golden_key) != 32:
            print(
                f"\n{Fore.CYAN}{Style.BRIGHT}Неверный формат токена. Попробуй еще раз! {Fore.RED}\\(!!˚0˚)/{Style.RESET_ALL}")
            continue
        config.set("FunPay", "golden_key", golden_key)
        break

    while True:
        print(f"\n{Fore.MAGENTA}{Style.BRIGHT}┌── {Fore.CYAN}"
              f"User-Agent (необязательно, нажми Enter чтобы пропустить){Style.RESET_ALL}")
        user_agent = input(f"{Fore.MAGENTA}{Style.BRIGHT}└───> {Style.RESET_ALL}").strip()
        if contains_russian(user_agent):
            print(
                f"\n{Fore.CYAN}{Style.BRIGHT}Неверный User-Agent. Попробуй еще раз! {Fore.RED}\\(!!˚0˚)/{Style.RESET_ALL}")
            continue
        if user_agent:
            config.set("FunPay", "user_agent", user_agent)
        break

    while True:
        print(
            f"\n{Fore.MAGENTA}{Style.BRIGHT}┌── {Fore.CYAN}API-токен Telegram-бота (получить у @BotFather){Style.RESET_ALL}")
        token = input(f"{Fore.MAGENTA}{Style.BRIGHT}└───> {Style.RESET_ALL}").strip()
        try:
            if not token or not token.split(":")[0].isdigit():
                raise Exception("Неправильный формат токена")
            username = telebot.TeleBot(token).get_me().username
        except Exception as ex:
            s = ""
            if str(ex):
                s = f" ({str(ex)})"
            print(f"\n{Fore.CYAN}{Style.BRIGHT}Попробуй еще раз!{s} {Fore.RED}\\(!!˚0˚)/{Style.RESET_ALL}")
            continue
        break

    while True:
        print(
            f"\n{Fore.MAGENTA}{Style.BRIGHT}┌── {Fore.CYAN}Придумай пароль для Telegram-бота (мин. 8 символов, заглавные + строчные буквы + цифра){Style.RESET_ALL}")
        password = input(f"{Fore.MAGENTA}{Style.BRIGHT}└───> {Style.RESET_ALL}").strip()
        if len(password) < 8 or password.lower() == password or password.upper() == password or not any(
                [i.isdigit() for i in password]):
            print(
                f"\n{Fore.CYAN}{Style.BRIGHT}Слабый пароль. Попробуй еще раз! {Fore.RED}\\(!!˚0˚)/{Style.RESET_ALL}")
            continue
        break

    config.set("Telegram", "enabled", "1")
    config.set("Telegram", "token", token)
    config.set("Telegram", "secretKeyHash", hash_password(password))

    while True:
        print(f"\n{Fore.MAGENTA}{Style.BRIGHT}┌── {Fore.CYAN}"
              f"Прокси (формат: login:password@ip:port или ip:port, Enter чтобы пропустить){Style.RESET_ALL}")
        proxy = input(f"{Fore.MAGENTA}{Style.BRIGHT}└───> {Style.RESET_ALL}").strip()
        if proxy:
            try:
                login, password, ip, port = validate_proxy(proxy)
                config.set("Proxy", "enable", "1")
                config.set("Proxy", "check", "1")
                config.set("Proxy", "login", login)
                config.set("Proxy", "password", password)
                config.set("Proxy", "ip", ip)
                config.set("Proxy", "port", port)
                break
            except:
                print(
                    f"\n{Fore.CYAN}{Style.BRIGHT}Неверный формат прокси. Попробуй еще раз!{Style.RESET_ALL}")
                continue
        else:
            break

    print(f"\n{Fore.CYAN}{Style.BRIGHT}Готово! Конфиг сохранен.{Style.RESET_ALL}")
    print(f"{Fore.CYAN}{Style.BRIGHT}Перезапусти бота и напиши своему Telegram-боту для дальнейшей настройки.{Style.RESET_ALL}")
    with open("configs/_main.cfg", "w", encoding="utf-8") as f:
        config.write(f)
    time.sleep(10)
