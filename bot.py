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
SC_URL = os.getenv("SC_URL")
WEBHOOK_HOST = os.getenv("WEBHOOK_HOST")
WEBHOOK_PATH = "/" + BOT_TOKEN
PORT = int(os.getenv("PORT", 8000))

bot = telebot.TeleBot(BOT_TOKEN)


def get_keyboard():
    kb = ReplyKeyboardMarkup(resize_keyboard=True)
    kb.row(KeyboardButton("Список игроков"), KeyboardButton("Скачать лог"))
    kb.row(KeyboardButton("Запустить сервер"), KeyboardButton("Остановить сервер"))
    kb.row(KeyboardButton("Перезапустить сервер"), KeyboardButton("Статус"))
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


def sc_get(path):
    if not SC_URL:
        return None
    try:
        return requests.get(
            SC_URL + path,
            headers={"X-Api-Key": MC_API_KEY, "ngrok-skip-browser-warning": "true"},
            timeout=10
        )
    except Exception as e:
        logger.error("SC error: %s", e)
        return None


@bot.message_handler(commands=["start"])
def start(message):
    if not is_allowed(message.from_user.id):
        return
    bot.send_message(message.chat.id, "MC Server Control", reply_markup=get_keyboard())


@bot.message_handler(func=lambda m: m.text == "Список игроков")
def players(message):
    if not is_allowed(message.from_user.id):
        return
    logger.info("Players request from %d", message.from_user.id)
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
        logger.info("Log response: %d, size: %d", r.status_code, len(r.content))
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
    r = sc_get("/api/start")
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
    r = sc_get("/api/restart")
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
    r = sc_get("/api/status")
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


@bot.message_handler(func=lambda m: m.text and m.text.startswith("/") and not m.text.startswith("/start"))
def console_command(message):
    if not is_allowed(message.from_user.id):
        return
    command = message.text[1:]
    logger.info("Console command: %s", command)
    r = mc_get("/api/command?cmd=" + requests.utils.quote(command))
    if r is None:
        bot.send_message(message.chat.id, "Сервер недоступен.")
    elif r.status_code == 200:
        bot.send_message(message.chat.id, "Команда выполнена: /" + command)
    else:
        bot.send_message(message.chat.id, "Ошибка: " + str(r.status_code))


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

    server = HTTPServer(("0.0.0.0", PORT), Handler=WebhookHandler)
    server.serve_forever()


if __name__ == "__main__":
    main()
