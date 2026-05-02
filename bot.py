import os
import logging
import requests
from io import BytesIO
from http.server import HTTPServer, BaseHTTPRequestHandler
import telebot
from telebot.types import ReplyKeyboardMarkup, KeyboardButton
from urllib.parse import quote

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

user_pages = {}
user_state = {}

PAGES = [
    ["Список игроков", "Скачать лог", "Запустить сервер", "Остановить сервер", "Перезапустить сервер"],
    ["Статус", "Whitelist: добавить", "Whitelist: удалить"],
]

def get_keyboard(page=0):
    kb = ReplyKeyboardMarkup(resize_keyboard=True)
    buttons = PAGES[page]
    if page == 0:
        for i in range(0, len(buttons) - 1, 2):
            row = [KeyboardButton(buttons[i])]
            if i + 1 < len(buttons) - 1: row.append(KeyboardButton(buttons[i + 1]))
            kb.row(*row)
        kb.row(KeyboardButton(buttons[-1]), KeyboardButton("→"))
    else:
        for i in range(0, len(buttons), 2):
            row = [KeyboardButton(buttons[i])]
            if i + 1 < len(buttons): row.append(KeyboardButton(buttons[i + 1]))
            kb.row(*row)
        kb.row(KeyboardButton("←"))
    return kb

def is_allowed(uid): return uid in ALLOWED_USERS

def mc_get(path):
    try:
        return requests.get(MC_API_URL + path, headers={"X-Api-Key": MC_API_KEY, "ngrok-skip-browser-warning": "true"}, timeout=15)
    except: return None

@bot.message_handler(commands=["start"])
def start(m):
    if is_allowed(m.from_user.id):
        user_pages[m.from_user.id] = 0
        bot.send_message(m.chat.id, "MC Server Control", reply_markup=get_keyboard(0))

@bot.message_handler(func=lambda m: m.text in ["→", "←"])
def navigate(m):
    if not is_allowed(m.from_user.id): return
    page = user_pages.get(m.from_user.id, 0)
    page = page + 1 if m.text == "→" else page - 1
    user_pages[m.from_user.id] = page
    bot.send_message(m.chat.id, f"Страница {page + 1}", reply_markup=get_keyboard(page))

@bot.message_handler(func=lambda m: m.text == "Список игроков")
def players(m):
    if not is_allowed(m.from_user.id): return
    r = mc_get("/api/players")
    if not r: bot.send_message(m.chat.id, "Сервер недоступен."); return
    data = r.json()
    p_list = data.get("players", [])
    msg = f"Онлайн: {data.get('online', 0)}/{data.get('max', 0)}\n\n" + ("\n".join("- " + p for p in p_list) if p_list else "Игроков нет.")
    bot.send_message(m.chat.id, msg)

@bot.message_handler(func=lambda m: m.text == "Скачать лог")
def logs(m):
    if not is_allowed(m.from_user.id): return
    r = mc_get("/api/logs")
    if r and r.status_code == 200:
        bot.send_document(m.chat.id, ("latest.log", BytesIO(r.content)))
    else: bot.send_message(m.chat.id, "Лог не найден или ошибка.")

@bot.message_handler(func=lambda m: m.text in ["Запустить сервер", "Остановить сервер", "Перезапустить сервер"])
def server_ops(m):
    if not is_allowed(m.from_user.id): return
    op = "start" if "Запустить" in m.text else "stop" if "Остановить" in m.text else "restart"
    r = mc_get(f"/api/{op}")
    bot.send_message(m.chat.id, "Команда отправлена" if r else "Ошибка связи")

@bot.message_handler(func=lambda m: m.text == "Статус")
def status(m):
    if not is_allowed(m.from_user.id): return
    r = mc_get("/api/status")
    if not r: bot.send_message(m.chat.id, "Ошибка связи"); return
    data = r.json()
    stats = data.get("stats", {})
    msg = f"Сервер: {'запущен' if data.get('running') else 'остановлен'}\nCPU: {stats.get('cpu')}% | RAM: {stats.get('ram_percent')}%"
    bot.send_message(m.chat.id, msg)

@bot.message_handler(func=lambda m: m.text == "Whitelist: добавить")
def wl_add_p(m):
    if is_allowed(m.from_user.id):
        user_state[m.from_user.id] = "wl_add"
        bot.send_message(m.chat.id, "Введите ник:")

@bot.message_handler(func=lambda m: m.text == "Whitelist: удалить")
def wl_rem_p(m):
    if is_allowed(m.from_user.id):
        user_state[m.from_user.id] = "wl_rem"
        bot.send_message(m.chat.id, "Введите ник:")

@bot.message_handler(func=lambda m: m.text and m.text.startswith("/") and not m.text.startswith("/start"))
def console(m):
    if not is_allowed(m.from_user.id): return
    r = mc_get(f"/api/command?cmd={quote(m.text[1:])}")
    bot.send_message(m.chat.id, f"Выполнено: {m.text}" if r else "Ошибка")

@bot.message_handler(func=lambda m: True)
def handle_all(m):
    if not is_allowed(m.from_user.id): return
    state = user_state.get(m.from_user.id)
    if state:
        user_state[m.from_user.id] = None
        action = "add" if state == "wl_add" else "remove"
        r = mc_get(f"/api/whitelist/{action}?name={quote(m.text.strip())}")
        if r:
            res = r.json()
            bot.send_message(m.chat.id, res.get("message", "Готово"))
            # Важный релоад
            mc_get("/api/command?cmd=" + quote("whitelist reload"))
        else: bot.send_message(m.chat.id, "Ошибка сервера")

class WebhookHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        if self.path == WEBHOOK_PATH:
            length = int(self.headers["Content-Length"])
            body = self.rfile.read(length).decode("utf-8")
            bot.process_new_updates([telebot.types.Update.de_json(body)])
            self.send_response(200); self.end_headers()
    def do_GET(self): self.send_response(200); self.end_headers()

if __name__ == "__main__":
    bot.remove_webhook()
    bot.set_webhook(url=WEBHOOK_HOST + WEBHOOK_PATH)
    HTTPServer(("0.0.0.0", PORT), WebhookHandler).serve_forever()
