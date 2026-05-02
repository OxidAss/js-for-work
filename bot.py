import os
import logging
import requests
from io import BytesIO
from http.server import HTTPServer, BaseHTTPRequestHandler
import telebot
from telebot.types import ReplyKeyboardMarkup, KeyboardButton

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

BOT_TOKEN = os.getenv("BOT_TOKEN")
ALLOWED_USERS = [int(uid) for uid in os.getenv("ALLOWED_USER_IDS", "").split(",") if uid.strip()]
MC_API_URL = os.getenv("MC_API_URL")
MC_API_KEY = os.getenv("MC_API_KEY")
WEBHOOK_HOST = os.getenv("WEBHOOK_HOST")
WEBHOOK_PATH = "/" + BOT_TOKEN
PORT = int(os.getenv("PORT", 8000))

bot = telebot.TeleBot(BOT_TOKEN)

# Состояние пользователей: страница и ожидание ввода
user_pages = {}
user_state = {}  # {user_id: "whitelist_add" | "whitelist_remove" | None}

# Страницы кнопок (по 4 функции + навигация)
PAGES = [
    ["Список игроков", "Скачать лог", "Запустить сервер", "Остановить сервер", "Перезапустить сервер"],
    ["Статус", "Whitelist: добавить", "Whitelist: удалить"],
]


def get_keyboard(page=0):
    kb = ReplyKeyboardMarkup(resize_keyboard=True)
    buttons = PAGES[page]

    if page == 0:
        # Первая страница: по 2 в ряд, последняя кнопка -> отдельно
        for i in range(0, len(buttons) - 1, 2):
            row = [KeyboardButton(buttons[i])]
            if i + 1 < len(buttons) - 1:
                row.append(KeyboardButton(buttons[i + 1]))
            kb.row(*row)
        kb.row(KeyboardButton(buttons[-1]), KeyboardButton("→"))
    else:
        # Остальные страницы: по 2 в ряд
        for i in range(0, len(buttons), 2):
            row = [KeyboardButton(buttons[i])]
            if i + 1 < len(buttons):
                row.append(KeyboardButton(buttons[i + 1]))
            kb.row(*row)
        # Навигация
        nav = []
        nav.append(KeyboardButton("←"))
        if page < len(PAGES) - 1:
            nav.append(KeyboardButton("→"))
        kb.row(*nav)

    return kb


def is_allowed(user_id):
    return user_id in ALLOWED_USERS


def mc_get(path, retries=3):
    for attempt in range(retries):
        try:
            r = requests.get(
                MC_API_URL + path,
                headers={"X-Api-Key": MC_API_KEY, "ngrok-skip-browser-warning": "true"},
                timeout=10
            )
            if r.status_code == 200:
                return r
            logger.warning("Attempt %d: status %d", attempt + 1, r.status_code)
        except requests.exceptions.ConnectionError:
            logger.warning("Attempt %d: connection error", attempt + 1)
        except Exception as e:
            logger.error("Attempt %d: %s", attempt + 1, e)
    return None


@bot.message_handler(commands=["start"])
def start(message):
    if not is_allowed(message.from_user.id):
        return
    user_pages[message.from_user.id] = 0
    bot.send_message(message.chat.id, "MC Server Control", reply_markup=get_keyboard(0))


@bot.message_handler(func=lambda m: m.text in ["→", "←"])
def navigate(message):
    if not is_allowed(message.from_user.id):
        return
    page = user_pages.get(message.from_user.id, 0)
    if message.text == "→" and page < len(PAGES) - 1:
        page += 1
    elif message.text == "←" and page > 0:
        page -= 1
    user_pages[message.from_user.id] = page
    bot.send_message(message.chat.id, "Страница " + str(page + 1), reply_markup=get_keyboard(page))


@bot.message_handler(func=lambda m: m.text == "Список игроков")
def players(message):
    if not is_allowed(message.from_user.id):
        return
    r = mc_get("/api/players")
    if r is None:
        bot.send_message(message.chat.id, "Сервер недоступен.")
        return
    data = r.json()
    players_list = data.get("players", [])
    online = data.get("online", 0)
    max_p = data.get("max", 0)
    if players_list:
        names = "\n".join("- " + p for p in players_list)
        msg = "Онлайн: %d/%d\n\n%s" % (online, max_p, names)
    else:
        msg = "Онлайн: %d/%d\n\nИгроков нет." % (online, max_p)
    bot.send_message(message.chat.id, msg)


@bot.message_handler(func=lambda m: m.text == "Скачать лог")
def logs(message):
    if not is_allowed(message.from_user.id):
        return
    try:
        r = requests.get(
            MC_API_URL + "/api/logs",
            headers={"X-Api-Key": MC_API_KEY, "ngrok-skip-browser-warning": "true"},
            timeout=30
        )
        if r.status_code == 200:
            try:
                bot.send_document(message.chat.id, ("latest.log", BytesIO(r.content)))
            except Exception as e:
                bot.send_message(message.chat.id, "Ошибка отправки: " + str(e))
        elif r.status_code == 404:
            bot.send_message(message.chat.id, "Лог-файл не найден.")
        else:
            bot.send_message(message.chat.id, "Ошибка: " + str(r.status_code))
    except requests.exceptions.ConnectionError:
        bot.send_message(message.chat.id, "Сервер недоступен.")


@bot.message_handler(func=lambda m: m.text == "Запустить сервер")
def start_server(message):
    if not is_allowed(message.from_user.id):
        return
    r = mc_get("/api/start")
    if r is None:
        bot.send_message(message.chat.id, "Скрипт управления недоступен.")
    elif r.status_code == 200:
        status = r.json().get("status")
        if status == "already_running":
            bot.send_message(message.chat.id, "Сервер уже запущен.")
        else:
            bot.send_message(message.chat.id, "Сервер запускается.")
    else:
        bot.send_message(message.chat.id, "Ошибка при запуске.")


@bot.message_handler(func=lambda m: m.text == "Остановить сервер")
def stop_server(message):
    if not is_allowed(message.from_user.id):
        return
    r = mc_get("/api/stop")
    if r is None:
        bot.send_message(message.chat.id, "Сервер недоступен.")
    elif r.status_code == 200:
        bot.send_message(message.chat.id, "Сервер останавливается.")
    else:
        bot.send_message(message.chat.id, "Ошибка при остановке.")


@bot.message_handler(func=lambda m: m.text == "Перезапустить сервер")
def restart_server(message):
    if not is_allowed(message.from_user.id):
        return
    r = mc_get("/api/restart")
    if r is None:
        bot.send_message(message.chat.id, "Скрипт управления недоступен.")
    elif r.status_code == 200:
        bot.send_message(message.chat.id, "Сервер перезапускается.")
    else:
        bot.send_message(message.chat.id, "Ошибка при перезапуске.")


@bot.message_handler(func=lambda m: m.text == "Статус")
def status(message):
    if not is_allowed(message.from_user.id):
        return
    r = mc_get("/api/status")
    if r is None:
        bot.send_message(message.chat.id, "Скрипт управления недоступен.")
        return
    data = r.json()
    running = "запущен" if data.get("running") else "остановлен"
    stats = data.get("stats", {})
    lines = [
        "Сервер: " + running,
        "CPU: " + str(stats.get("cpu")) + "%",
        "RAM: " + str(stats.get("ram_used")) + " / " + str(stats.get("ram_total")) + " MB (" + str(stats.get("ram_percent")) + "%)"
    ]
    bot.send_message(message.chat.id, "\n".join(lines))


@bot.message_handler(func=lambda m: m.text == "Whitelist: добавить")
def whitelist_add_prompt(message):
    if not is_allowed(message.from_user.id):
        return
    user_state[message.from_user.id] = "whitelist_add"
    bot.send_message(message.chat.id, "Введите ник игрока:")


@bot.message_handler(func=lambda m: m.text == "Whitelist: удалить")
def whitelist_remove_prompt(message):
    if not is_allowed(message.from_user.id):
        return
    user_state[message.from_user.id] = "whitelist_remove"
    bot.send_message(message.chat.id, "Введите ник игрока:")


@bot.message_handler(func=lambda m: m.text and m.text.startswith("/") and not m.text.startswith("/start"))
def console_command(message):
    if not is_allowed(message.from_user.id):
        return
    command = message.text[1:]
    r = mc_get("/api/command?cmd=" + requests.utils.quote(command))
    if r is None:
        bot.send_message(message.chat.id, "Сервер недоступен.")
    elif r.status_code == 200:
        bot.send_message(message.chat.id, "Команда выполнена: /" + command)
    else:
        bot.send_message(message.chat.id, "Ошибка: " + str(r.status_code))


@bot.message_handler(func=lambda m: True)
def handle_text(message):
    if not is_allowed(message.from_user.id):
        return

    state = user_state.get(message.from_user.id)

    if state in ["whitelist_add", "whitelist_remove"]:
        user_state[message.from_user.id] = None
        nick = message.text.strip()
        
        # Выбираем эндпоинт в зависимости от действия
        action = "add" if state == "whitelist_add" else "remove"
        
        # Отправляем запрос. Только НИК, UUID сервер сделает сам.
        r = mc_get(f"/api/whitelist/{action}?name=" + requests.utils.quote(nick))
        
        if r is None:
            bot.send_message(message.chat.id, "⛔ Ошибка: Сервер управления недоступен.")
        else:
            res_data = r.json()
            if res_data.get("status") == "success":
                # ОБЯЗАТЕЛЬНО: после прямой правки файла НУЖЕН релоад в консоли
                bot.send_message(message.chat.id, f"✅ {res_data.get('message')}")
                
                # Просим сервер перечитать файл
                reload_r = mc_get("/api/command?cmd=" + requests.utils.quote("whitelist reload"))
                if reload_r:
                    bot.send_message(message.chat.id, "🔄 Вайтлист на сервере обновлен.")
            else:
                bot.send_message(message.chat.id, f"⚠️ Ошибка: {res_data.get('message')}")


class WebhookHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/health":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"status":"ok"}')
        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self):
        if self.path != WEBHOOK_PATH:
            self.send_response(404)
            self.end_headers()
            return
        length = int(self.headers["Content-Length"])
        body = self.rfile.read(length)
        update = telebot.types.Update.de_json(body.decode("utf-8"))
        bot.process_new_updates([update])
        self.send_response(200)
        self.end_headers()

    def log_message(self, format, *args):
        pass


def main():
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN not set")
    if not ALLOWED_USERS:
        raise RuntimeError("ALLOWED_USER_IDS not set")
    if not MC_API_URL:
        raise RuntimeError("MC_API_URL not set")
    if not MC_API_KEY:
        raise RuntimeError("MC_API_KEY not set")
    if not WEBHOOK_HOST:
        raise RuntimeError("WEBHOOK_HOST not set")

    bot.remove_webhook()
    bot.set_webhook(url=WEBHOOK_HOST + WEBHOOK_PATH)
    logger.info("Webhook set to %s%s", WEBHOOK_HOST, WEBHOOK_PATH)
    logger.info("Listening on port %d", PORT)

    server = HTTPServer(("0.0.0.0", PORT), WebhookHandler)
    server.serve_forever()


if __name__ == "__main__":
    main()
