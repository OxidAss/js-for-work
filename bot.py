import os
import logging
import requests
import time
import base64
from io import BytesIO
from http.server import HTTPServer, BaseHTTPRequestHandler
import telebot
from telebot.types import ReplyKeyboardMarkup, KeyboardButton

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
ALLOWED_USERS = [int(uid.strip()) for uid in os.getenv("ALLOWED_USER_IDS", "").split(",") if uid.strip()]
MC_API_URL = os.getenv("MC_API_URL", "").rstrip("/")
MC_API_KEY = os.getenv("MC_API_KEY", "").strip()
WEBHOOK_HOST = os.getenv("WEBHOOK_HOST", "").rstrip("/")
WEBHOOK_PATH = "/" + BOT_TOKEN
PORT = int(os.getenv("PORT", "8000"))

if not all([BOT_TOKEN, ALLOWED_USERS, MC_API_URL, MC_API_KEY, WEBHOOK_HOST]):
    raise RuntimeError("Не все переменные окружения заданы!")

bot = telebot.TeleBot(BOT_TOKEN, parse_mode=None)

user_pages = {}
user_state = {}
user_temp = {}

PAGES = [
    ["Список игроков", "Скачать лог", "Запустить сервер", "Остановить сервер", "Перезапустить сервер"],
    ["Статус", "Whitelist: добавить", "Whitelist: удалить"],
]

def get_keyboard(page=0):
    kb = ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=False)
    buttons = PAGES[page]
    if page == 0:
        for i in range(0, len(buttons) - 1, 2):
            row = [KeyboardButton(buttons[i])]
            if i + 1 < len(buttons) - 1:
                row.append(KeyboardButton(buttons[i + 1]))
            kb.row(*row)
        kb.row(KeyboardButton(buttons[-1]), KeyboardButton("→"))
    else:
        for i in range(0, len(buttons), 2):
            row = [KeyboardButton(buttons[i])]
            if i + 1 < len(buttons):
                row.append(KeyboardButton(buttons[i + 1]))
            kb.row(*row)
        nav = [KeyboardButton("←")]
        if page < len(PAGES) - 1:
            nav.append(KeyboardButton("→"))
        kb.row(*nav)
    return kb

def is_allowed(user_id):
    return user_id in ALLOWED_USERS

def mc_request(method, path, **kwargs):
    url = MC_API_URL + path
    headers = {
        "X-Api-Key": MC_API_KEY,
        "ngrok-skip-browser-warning": "true"
    }
    for attempt in range(3):
        try:
            r = getattr(requests, method)(url, headers=headers, timeout=20, **kwargs)
            if r.status_code == 200:
                return r
            logger.warning("Attempt %d: status %d", attempt + 1, r.status_code)
        except requests.exceptions.ConnectionError:
            logger.warning("Attempt %d: connection error", attempt + 1)
        except Exception as e:
            logger.error("Attempt %d: %s", attempt + 1, e)
        time.sleep(1.5 ** attempt)
    return None

@bot.message_handler(commands=["start"])
def start(message):
    if not is_allowed(message.from_user.id):
        bot.send_message(message.chat.id, "Доступ запрещён.")
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
    bot.send_message(message.chat.id, f"Страница {page + 1}", reply_markup=get_keyboard(page))

@bot.message_handler(func=lambda m: m.text == "Список игроков")
def players(message):
    if not is_allowed(message.from_user.id):
        return
    r = mc_request("get", "/api/players")
    if r is None:
        bot.send_message(message.chat.id, "Сервер управления недоступен.")
        return
    data = r.json()
    online = data.get("online", 0)
    max_p = data.get("max", 0)
    players_list = data.get("players", [])
    if players_list:
        names = "\n".join(f"- {p}" for p in players_list)
        msg = f"Онлайн: {online}/{max_p}\n\n{names}"
    else:
        msg = f"Онлайн: {online}/{max_p}\n\nИгроков нет."
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
            bot.send_document(message.chat.id, ("latest.log", BytesIO(r.content)))
        elif r.status_code == 404:
            bot.send_message(message.chat.id, "Лог-файл не найден.")
        else:
            bot.send_message(message.chat.id, f"Ошибка: {r.status_code}")
    except Exception as e:
        bot.send_message(message.chat.id, f"Ошибка: {e}")

@bot.message_handler(func=lambda m: m.text == "Запустить сервер")
def start_server(message):
    if not is_allowed(message.from_user.id):
        return
    r = mc_request("get", "/api/start")
    if r is None:
        bot.send_message(message.chat.id, "Сервер управления недоступен.")
    elif r.status_code == 200:
        status = r.json().get("status")
        if status == "already_running":
            bot.send_message(message.chat.id, "Сервер уже запущен.")
        else:
            bot.send_message(message.chat.id, "Сервер запускается...")
    else:
        bot.send_message(message.chat.id, f"Ошибка: {r.status_code}")

@bot.message_handler(func=lambda m: m.text == "Остановить сервер")
def stop_server(message):
    if not is_allowed(message.from_user.id):
        return
    r = mc_request("get", "/api/stop")
    if r is None:
        bot.send_message(message.chat.id, "Сервер управления недоступен.")
    elif r.status_code == 200:
        bot.send_message(message.chat.id, "Сервер останавливается...")
    else:
        bot.send_message(message.chat.id, f"Ошибка: {r.status_code}")

@bot.message_handler(func=lambda m: m.text == "Перезапустить сервер")
def restart_server(message):
    if not is_allowed(message.from_user.id):
        return
    r = mc_request("get", "/api/restart")
    if r is None:
        bot.send_message(message.chat.id, "Сервер управления недоступен.")
    elif r.status_code == 200:
        bot.send_message(message.chat.id, "Сервер перезапускается...")
    else:
        bot.send_message(message.chat.id, f"Ошибка: {r.status_code}")

@bot.message_handler(func=lambda m: m.text == "Статус")
def status(message):
    if not is_allowed(message.from_user.id):
        return
    r = mc_request("get", "/api/status")
    if r is None:
        bot.send_message(message.chat.id, "Сервер управления недоступен.")
        return
    data = r.json()
    running = "запущен" if data.get("running") else "остановлен"
    stats = data.get("stats", {})
    lines = [
        f"Статус: {running}",
        f"CPU: {stats.get('cpu', 0)}%",
        f"RAM: {stats.get('ram_used', 0)} / {stats.get('ram_total', 0)} MB ({stats.get('ram_percent', 0)}%)"
    ]
    bot.send_message(message.chat.id, "\n".join(lines))

@bot.message_handler(func=lambda m: m.text == "Whitelist: добавить")
def whitelist_add_prompt(message):
    if not is_allowed(message.from_user.id):
        return
    user_state[message.from_user.id] = "whitelist_add"
    bot.send_message(message.chat.id, "Введите ник игрока для добавления в whitelist:")

@bot.message_handler(func=lambda m: m.text == "Whitelist: удалить")
def whitelist_remove_prompt(message):
    if not is_allowed(message.from_user.id):
        return
    user_state[message.from_user.id] = "whitelist_remove"
    bot.send_message(message.chat.id, "Введите ник игрока для удаления из whitelist:")

@bot.message_handler(func=lambda m: m.text and m.text.startswith("/cmd"))
def console_command(message):
    if not is_allowed(message.from_user.id):
        return
    command = message.text[5:].strip()
    if not command:
        bot.send_message(message.chat.id, "Использование: /cmd <команда>")
        return
    r = mc_request("get", f"/api/command?cmd={requests.utils.quote(command)}")
    if r is None:
        bot.send_message(message.chat.id, "Сервер управления недоступен.")
    elif r.status_code == 200:
        bot.send_message(message.chat.id, f"Команда выполнена: {command}")
    else:
        bot.send_message(message.chat.id, f"Ошибка: {r.status_code}")

@bot.message_handler(commands=["help_fs"])
def help_fs(message):
    if not is_allowed(message.from_user.id):
        return
    text = (
        "Файловый менеджер:\n"
        "/cd [путь] - перейти в директорию (без пути - показать текущую)\n"
        "/ls [путь] - показать содержимое папки\n"
        "/rm <файл> - удалить файл из текущей директории\n"
        "Отправив боту файл - он загрузится на сервер. После можно будет его перенести\n"
        "Изначальный путь - папка Python сервера."
    )
    bot.send_message(message.chat.id, text)

@bot.message_handler(func=lambda m: m.text and m.text.startswith("/") and m.text[1:].split()[0] in ["cd", "ls", "rm"])
def file_command(message):
    if not is_allowed(message.from_user.id):
        return
    cmd = message.text[1:].strip()
    r = mc_request("get", f"/api/file/cmd?cmd={requests.utils.quote(cmd)}&chat_id={message.from_user.id}")
    if r is None or r.status_code != 200:
        bot.send_message(message.chat.id, "Сервер управления недоступен.")
    else:
        output = r.json().get("output", "Выполнено.")
        output = output.replace("`", "\\`")
        bot.send_message(message.chat.id, output, parse_mode="MarkdownV2")

@bot.message_handler(content_types=["document", "photo"])
def handle_file_upload(message):
    if not is_allowed(message.from_user.id):
        return
    if message.document:
        file_id = message.document.file_id
        file_name = message.document.file_name or f"file_{int(time.time())}"
    elif message.photo:
        file_id = message.photo[-1].file_id
        file_name = f"photo_{int(time.time())}.jpg"
    else:
        return
    try:
        file_info = bot.get_file(file_id)
        file_content = bot.download_file(file_info.file_path)
        content_b64 = base64.b64encode(file_content).decode("ascii")
        r = requests.post(
            MC_API_URL + "/api/file/upload",
            headers={"X-Api-Key": MC_API_KEY, "ngrok-skip-browser-warning": "true"},
            json={
                "chat_id": message.from_user.id,
                "filename": file_name,
                "content": content_b64
            },
            timeout=60
        )
        if r.status_code == 200:
            resp = r.json()
            msg = resp.get("message", "Файл загружен.").replace("`", "\\`")
            bot.send_message(message.chat.id, msg, parse_mode="MarkdownV2")
            if resp.get("awaiting_path"):
                user_state[message.from_user.id] = "file_move_pending"
                user_temp[message.from_user.id] = resp["temp_path"]
                bot.send_message(message.chat.id, "Введите путь для перемещения (относительно корня сервера):")
        else:
            bot.send_message(message.chat.id, f"Ошибка: {r.status_code} {r.text}")
    except Exception as e:
        bot.send_message(message.chat.id, f"Ошибка загрузки: {e}")

@bot.message_handler(func=lambda m: user_state.get(m.from_user.id) == "file_move_pending")
def handle_move_path(message):
    if not is_allowed(message.from_user.id):
        return
    temp_path = user_temp.get(message.from_user.id)
    dest_path = message.text.strip()
    try:
        r = requests.post(
            MC_API_URL + "/api/file/move",
            headers={"X-Api-Key": MC_API_KEY, "ngrok-skip-browser-warning": "true"},
            json={
                "chat_id": message.from_user.id,
                "temp_path": temp_path,
                "dest_path": dest_path
            },
            timeout=30
        )
        if r.status_code == 200:
            msg = r.json().get("message", "Файл перемещён.").replace("`", "\\`")
            bot.send_message(message.chat.id, msg, parse_mode="MarkdownV2")
        else:
            bot.send_message(message.chat.id, f"Ошибка: {r.status_code} {r.text}")
    except Exception as e:
        bot.send_message(message.chat.id, f"Ошибка: {e}")
    finally:
        user_state[message.from_user.id] = None
        user_temp.pop(message.from_user.id, None)

@bot.message_handler(func=lambda m: user_state.get(m.from_user.id) in ["whitelist_add", "whitelist_remove"])
def handle_whitelist_input(message):
    if not is_allowed(message.from_user.id):
        return
    state = user_state[message.from_user.id]
    nick = message.text.strip()
    action = "add" if state == "whitelist_add" else "remove"
    r = mc_request("get", f"/api/command?cmd={requests.utils.quote(f'whitelist {action} {nick}')}")
    if r is None:
        bot.send_message(message.chat.id, "Сервер управления недоступен.")
    else:
        verb = "добавлен" if action == "add" else "удалён"
        bot.send_message(message.chat.id, f"{nick} {verb} в whitelist.")
    user_state[message.from_user.id] = None

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
        try:
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length)
            update = telebot.types.Update.de_json(body.decode("utf-8"))
            bot.process_new_updates([update])
            self.send_response(200)
            self.end_headers()
        except Exception as e:
            logger.error("Webhook error: %s", e)
            self.send_response(500)
            self.end_headers()

    def log_message(self, format, *args):
        pass

def main():
    bot.remove_webhook()
    webhook_url = WEBHOOK_HOST + WEBHOOK_PATH
    bot.set_webhook(url=webhook_url, allowed_updates=["message", "callback_query"])
    logger.info("Webhook установлен: %s", webhook_url)
    logger.info("Слушаю порт %d", PORT)
    server = HTTPServer(("0.0.0.0", PORT), WebhookHandler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logger.info("Завершение работы...")
        bot.remove_webhook()
        server.shutdown()

if __name__ == "__main__":
    main()
