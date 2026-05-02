import os
import logging
import requests
import telebot
from io import BytesIO
from http.server import HTTPServer, BaseHTTPRequestHandler
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
    kb = telebot.types.ReplyKeyboardMarkup(resize_keyboard=True)
    btns = PAGES[page]
    for i in range(0, len(btns), 2):
        row = [telebot.types.KeyboardButton(btns[i])]
        if i + 1 < len(btns): row.append(telebot.types.KeyboardButton(btns[i+1]))
        kb.row(*row)
    nav = []
    if page > 0: nav.append(telebot.types.KeyboardButton("←"))
    if page < len(PAGES) - 1: nav.append(telebot.types.KeyboardButton("→"))
    if nav: kb.row(*nav)
    return kb

def is_allowed(uid): return uid in ALLOWED_USERS

def mc_get(path):
    try:
        r = requests.get(MC_API_URL + path, headers={"X-Api-Key": MC_API_KEY}, timeout=15)
        return r
    except: return None

@bot.message_handler(commands=["start"])
def start(m):
    if is_allowed(m.from_user.id):
        user_pages[m.from_user.id] = 0
        bot.send_message(m.chat.id, "Контроль MC Сервера", reply_markup=get_keyboard(0))

@bot.message_handler(func=lambda m: m.text in ["→", "←"])
def nav(m):
    if not is_allowed(m.from_user.id): return
    p = user_pages.get(m.from_user.id, 0)
    p = p + 1 if m.text == "→" else p - 1
    user_pages[m.from_user.id] = p
    bot.send_message(m.chat.id, f"Страница {p+1}", reply_markup=get_keyboard(p))

@bot.message_handler(func=lambda m: m.text == "Whitelist: добавить")
def wl_add(m):
    if is_allowed(m.from_user.id):
        user_state[m.from_user.id] = "add"
        bot.send_message(m.chat.id, "Введите ник для добавления:")

@bot.message_handler(func=lambda m: m.text == "Whitelist: удалить")
def wl_rem(m):
    if is_allowed(m.from_user.id):
        user_state[m.from_user.id] = "remove"
        bot.send_message(m.chat.id, "Введите ник для удаления:")

@bot.message_handler(func=lambda m: True)
def handle_text(m):
    if not is_allowed(m.from_user.id): return
    state = user_state.get(m.from_user.id)
    
    if state in ["add", "remove"]:
        user_state[m.from_user.id] = None
        nick = m.text.strip()
        action = "add" if state == "add" else "remove"
        
        bot.send_message(m.chat.id, f"⏳ Запрос для `{nick}`...")
        r = mc_get(f"/api/whitelist/{action}?name={quote(nick)}")
        
        if r and r.status_code == 200:
            data = r.json()
            bot.send_message(m.chat.id, data.get("message", "✅ Готово"))
            # Команда релоада
            mc_get("/api/command?cmd=" + quote("whitelist reload"))
        else:
            bot.send_message(m.chat.id, "❌ Ошибка сервера.")
    
    elif m.text == "Статус":
        r = mc_get("/api/status")
        if r:
            d = r.json()
            s = d['stats']
            bot.send_message(m.chat.id, f"Сервер: {'✅' if d['running'] else '❌'}\nCPU: {s['cpu']}%\nRAM: {s['ram_used']}/{s['ram_total']} MB")

# WEBHOOK HANDLER
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
