import os
import re
import subprocess
import threading
import time
import psutil
import requests
import shutil
import json
import logging
import hashlib
import uuid as _uuid
from http.server import HTTPServer, BaseHTTPRequestHandler
from socketserver import ThreadingMixIn
from urllib.parse import urlparse, parse_qs

# --- Настройка логгера ---
logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
logger = logging.getLogger(__name__)

# --- Конфигурация ---
API_KEY          = os.getenv("MC_API_KEY", "").strip()
BOT_TOKEN        = os.getenv("BOT_TOKEN", "").strip()
ALLOWED_USER_IDS = [int(uid) for uid in os.getenv("ALLOWED_USER_IDS", "").split(",") if uid.strip()]
PORT             = int(os.getenv("SC_PORT", "25583"))
PLUGIN_URL       = f"http://localhost:{os.getenv('MC_API_PORT', '25582')}"
SERVER_BAT       = r"C:\Users\Myasnoy\Desktop\server\start.bat"
SERVER_DIR       = r"C:\Users\Myasnoy\Desktop\server"
LOG_FILE         = os.path.join(SERVER_DIR, "logs", "latest.log")
WHITELIST_FILE   = os.path.join(SERVER_DIR, "whitelist.json")

# --- Паттерн для парсинга логов ---
BLOCK_PATTERN = re.compile(r"[Security] Blocked (\w+) from unknown location (.+?) \(IP: ([\w.:]+)\)")

# --- Глобальное состояние ---
server_process = None
process_lock   = threading.Lock()
_cpu_cache = {"value": 0.0, "time": 0}

# === 🔐 БЕЗОПАСНЫЙ ФАЙЛОВЫЙ МЕНЕДЖЕР ===
ALLOWED_ROOT = os.path.realpath(SERVER_DIR)
user_sessions = {}
session_lock = threading.Lock()

def get_session(chat_id):
    with session_lock:
        if chat_id not in user_sessions:
            user_sessions[chat_id] = {"cwd": ALLOWED_ROOT, "mode": None, "temp_path": None, "pending_name": None}
        return user_sessions[chat_id]

def safe_resolve(base, user_path):
    full = os.path.realpath(os.path.join(base, user_path))
    if not (full == ALLOWED_ROOT or full.startswith(ALLOWED_ROOT + os.sep)):
        return None
    return full

def handle_file_command_internal(chat_id, cmd):
    state = get_session(chat_id)
    parts = cmd.strip().split(maxsplit=1)
    command = parts[0].lower()
    arg = parts[1] if len(parts) > 1 else ""

    if command == "cd":
        if not arg:
            rel = os.path.relpath(state["cwd"], ALLOWED_ROOT)
            return f"📂 Текущая папка: {rel if rel != '.' else '/'}"
        target = safe_resolve(state["cwd"], arg)
        if not target:
            return "⛔ Доступ запрещён: путь выходит за пределы сервера."
        if not os.path.isdir(target):
            return "⛔ Директория не существует."
        state["cwd"] = target
        return f"✅ Перешли в: {os.path.relpath(target, ALLOWED_ROOT)}"

    elif command == "ls":
        target = safe_resolve(state["cwd"], arg if arg else ".")
        if not target or not os.path.isdir(target):
            return "⛔ Директория не найдена или недоступна."
        try:
            items = sorted(os.listdir(target))
            if not items:
                return "📂 Директория пуста."
            lines = []
            for item in items:
                full = os.path.join(target, item)
                prefix = "📁" if os.path.isdir(full) else "📄"
                lines.append(f"{prefix} {item}")
            rel = os.path.relpath(target, ALLOWED_ROOT)
            return f"📋 Содержимое `{rel}`:\n" + "\n".join(lines)
        except Exception as e:
            return f"⛔ Ошибка чтения: {e}"

    elif command == "rm":
        if not arg:
            return "⛔ Использование: /rm <имя_файла>"
        target = safe_resolve(state["cwd"], arg)
        if not target:
            return "⛔ Доступ запрещён."
        if not os.path.exists(target):
            return "⛔ Файл не найден."
        if os.path.isdir(target):
            return "⛔ Удаление папок запрещено. Используйте /rm только для файлов."
        try:
            os.remove(target)
            return f"✅ Файл `{arg}` удалён."
        except Exception as e:
            return f"⛔ Ошибка удаления: {e}"
    else:
        return "❓ Доступные команды: `/cd [путь]`, `/ls [путь]`, `/rm <файл>`"

# === 📥 ЗАГРУЗКА ФАЙЛОВ ===
def save_uploaded_file(chat_id, filename, content):
    temp_dir = os.path.join(SERVER_DIR, ".bot_uploads")
    os.makedirs(temp_dir, exist_ok=True)
    safe_name = re.sub(r'[^\w\.\-]', '_', filename)
    temp_path = os.path.join(temp_dir, f"{chat_id}_{safe_name}")
    with open(temp_path, "wb") as f:
        f.write(content)
    state = get_session(chat_id)
    state["mode"] = "awaiting_dest"
    state["temp_path"] = temp_path
    state["pending_name"] = safe_name
    return temp_path, safe_name

def move_file_internal(chat_id, dest_path):
    state = get_session(chat_id)
    if state["mode"] != "awaiting_dest" or not state["temp_path"]:
        return False, "⛔ Нет активного файла для перемещения."
    target = safe_resolve(ALLOWED_ROOT, dest_path)
    if not target:
        return False, "⛔ Неверный путь или выход за пределы разрешённой директории."
    try:
        os.makedirs(os.path.dirname(target), exist_ok=True)
        shutil.move(state["temp_path"], target)
        state["mode"] = None
        state["temp_path"] = None
        state["pending_name"] = None
        return True, f"✅ Файл `{state['pending_name']}` перемещён в `{dest_path}`"
    except Exception as e:
        return False, f"⛔ Ошибка перемещения: {e}"

# === 📋 ВАЙТЛИСТ ===
def offline_uuid(name: str) -> str:
    """Генерирует оффлайн UUID точно как Minecraft при online-mode=false."""
    h = hashlib.md5(b"OfflinePlayer:" + name.encode()).digest()
    return str(_uuid.UUID(bytes=h[:16], version=3))

def whitelist_add(name: str) -> tuple:
    try:
        wl = []
        if os.path.exists(WHITELIST_FILE):
            with open(WHITELIST_FILE, "r", encoding="utf-8") as f:
                wl = json.load(f)
        if any(e["name"].lower() == name.lower() for e in wl):
            return True, f"Игрок {name} уже в вайтлисте"
        uid = offline_uuid(name)
        wl.append({"uuid": uid, "name": name})
        with open(WHITELIST_FILE, "w", encoding="utf-8") as f:
            json.dump(wl, f, indent=2, ensure_ascii=False)
        try:
            requests.get(PLUGIN_URL + "/whitelist/reload", headers={"X-Api-Key": API_KEY}, timeout=5)
        except Exception:
            pass
        logger.info("Whitelist add: %s (%s)", name, uid)
        return True, f"✅ {name} добавлен в вайтлист"
    except Exception as e:
        logger.error("Whitelist add error: %s", e)
        return False, f"⛔ Ошибка: {e}"

def whitelist_remove(name: str) -> tuple:
    try:
        if not os.path.exists(WHITELIST_FILE):
            return False, "whitelist.json не найден"
        with open(WHITELIST_FILE, "r", encoding="utf-8") as f:
            wl = json.load(f)
        new_wl = [e for e in wl if e["name"].lower() != name.lower()]
        if len(new_wl) == len(wl):
            return False, f"Игрок {name} не найден в вайтлисте"
        with open(WHITELIST_FILE, "w", encoding="utf-8") as f:
            json.dump(new_wl, f, indent=2, ensure_ascii=False)
        try:
            requests.get(PLUGIN_URL + "/whitelist/reload", headers={"X-Api-Key": API_KEY}, timeout=5)
        except Exception:
            pass
        logger.info("Whitelist remove: %s", name)
        return True, f"✅ {name} удалён из вайтлиста"
    except Exception as e:
        logger.error("Whitelist remove error: %s", e)
        return False, f"⛔ Ошибка: {e}"

# === 📡 ОСТАЛЬНЫЕ ФУНКЦИИ СЕРВЕРА ===
def get_cpu():
    now = time.time()
    if now - _cpu_cache["time"] > 2:
        _cpu_cache["value"] = psutil.cpu_percent()
        _cpu_cache["time"] = now
    return _cpu_cache["value"]

def is_server_running():
    with process_lock:
        if server_process is not None and server_process.poll() is None:
            return True
    for proc in psutil.process_iter(["name", "cmdline"]):
        try:
            if proc.info["name"] and "java" in proc.info["name"].lower():
                cmdline = " ".join(proc.info["cmdline"] or [])
                if "fabric" in cmdline.lower() or "minecraft" in cmdline.lower():
                    return True
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
    return False

def start_server():
    global server_process
    with process_lock:
        server_process = subprocess.Popen(
            ["cmd", "/c", "start", "cmd", "/k", SERVER_BAT],
            cwd=SERVER_DIR,
            creationflags=subprocess.CREATE_NEW_CONSOLE
        )

def stop_server():
    global server_process
    with process_lock:
        if server_process:
            try:
                server_process.terminate()
                server_process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                server_process.kill()
            finally:
                server_process = None

def get_system_stats():
    mem = psutil.virtual_memory()
    return {
        "cpu": get_cpu(),
        "ram_used": round(mem.used / 1024 / 1024),
        "ram_total": round(mem.total / 1024 / 1024),
        "ram_percent": mem.percent,
    }

def proxy_to_plugin(path):
    try:
        r = requests.get(PLUGIN_URL + path, headers={"X-Api-Key": API_KEY}, timeout=10)
        return r.status_code, r.content, r.headers.get("Content-Type", "application/json")
    except requests.exceptions.ConnectionError:
        return 503, json.dumps({"error": "Plugin unavailable"}).encode(), "application/json"
    except Exception as e:
        logger.error("Plugin proxy error: %s", e)
        return 500, json.dumps({"error": "Internal error"}).encode(), "application/json"

def send_telegram(text):
    if not BOT_TOKEN or not ALLOWED_USER_IDS:
        return
    for uid in ALLOWED_USER_IDS:
        try:
            r = requests.post(
                f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
                json={"chat_id": uid, "text": text, "parse_mode": "Markdown"},
                timeout=10
            )
            if not r.ok:
                logger.warning("Telegram API error for %d: %s", uid, r.text)
        except Exception as e:
            logger.error("Telegram send error for %d: %s", uid, e)

def send_telegram_async(text):
    threading.Thread(target=send_telegram, args=(text,), daemon=True).start()

def watch_log():
    logger.info("Log watcher started: %s", LOG_FILE)
    last_pos = 0
    while True:
        try:
            if not os.path.exists(LOG_FILE):
                time.sleep(2)
                last_pos = 0
                continue
            size = os.path.getsize(LOG_FILE)
            if size < last_pos:
                last_pos = 0
            if size > last_pos:
                with open(LOG_FILE, "rb") as f:
                    f.seek(last_pos)
                    new_bytes = f.read()
                last_pos = size
                for line in new_bytes.decode("utf-8", errors="ignore").splitlines():
                    match = BLOCK_PATTERN.search(line)
                    if match:
                        username = match.group(1)
                        location = match.group(2).strip()
                        ip = match.group(3)
                        msg = f"⚠️ Заблокирован подозрительный вход!\nНик: `{username}`\nЛокация: {location}\nIP: {ip}"
                        logger.info("Security alert: %s", msg)
                        send_telegram_async(msg)
        except Exception as e:
            logger.error("Log watcher error: %s", e)
        time.sleep(1)

# === 🌐 HTTP SERVER ===
class ThreadedHTTPServer(ThreadingMixIn, HTTPServer):
    daemon_threads = True

class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.headers.get("X-Api-Key") != API_KEY:
            self._send(403, {"error": "Forbidden"})
            return

        parsed = urlparse(self.path)
        path = parsed.path
        query = parse_qs(parsed.query)

        if path == "/health":
            self._send(200, {"status": "ok"})

        elif path == "/api/status":
            self._send(200, {"running": is_server_running(), "stats": get_system_stats()})

        elif path == "/api/start":
            if is_server_running():
                self._send(200, {"status": "already_running"})
                return
            try:
                start_server()
                logger.info("Server started")
                self._send(200, {"status": "starting"})
            except Exception as e:
                logger.error("Start error: %s", e)
                self._send(500, {"error": str(e)})

        elif path == "/api/stop":
            if not is_server_running():
                self._send(200, {"status": "not_running"})
                return
            try:
                stop_server()
                logger.info("Server stopped")
                self._send(200, {"status": "stopping"})
            except Exception as e:
                logger.error("Stop error: %s", e)
                self._send(500, {"error": str(e)})

        elif path == "/api/restart":
            try:
                if is_server_running(): stop_server()
                start_server()
                logger.info("Server restarted")
                self._send(200, {"status": "restarting"})
            except Exception as e:
                logger.error("Restart error: %s", e)
                self._send(500, {"error": str(e)})

        elif path == "/api/file/cmd" and "cmd" in query:
            chat_id = int(query.get("chat_id", [0])[0])
            cmd = query["cmd"][0]
            output = handle_file_command_internal(chat_id, cmd)
            self._send(200, {"output": output})

        elif path == "/api/whitelist/add":
            name = query.get("name", [None])[0]
            if not name:
                self._send(400, {"error": "missing name"})
                return
            ok, msg = whitelist_add(name)
            self._send(200 if ok else 500, {"ok": ok, "message": msg})

        elif path == "/api/whitelist/remove":
            name = query.get("name", [None])[0]
            if not name:
                self._send(400, {"error": "missing name"})
                return
            ok, msg = whitelist_remove(name)
            self._send(200 if ok else 500, {"ok": ok, "message": msg})

        elif path.startswith("/api/"):
            code, body, content_type = proxy_to_plugin(path + ("?" + parsed.query if parsed.query else ""))
            try:
                self.send_response(code)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Connection", "close")
                self.end_headers()
                self.wfile.write(body)
            except Exception as e:
                logger.warning("Response write error: %s", e)

        else:
            self._send(404, {"error": "Not found"})

    def do_POST(self):
        if self.headers.get("X-Api-Key") != API_KEY:
            self._send(403, {"error": "Forbidden"})
            return

        parsed = urlparse(self.path)
        path = parsed.path

        if path == "/api/file/upload":
            try:
                length = int(self.headers.get("Content-Length", 0))
                body = self.rfile.read(length)
                data = json.loads(body)
                chat_id = int(data["chat_id"])
                filename = data["filename"]
                content = data["content"].encode("latin1") if isinstance(data["content"], str) else data["content"]
                temp_path, safe_name = save_uploaded_file(chat_id, filename, content)
                self._send(200, {
                    "message": f"📥 Файл `{safe_name}` получен. Введите путь для перемещения:",
                    "awaiting_path": True,
                    "temp_path": temp_path
                })
            except Exception as e:
                logger.error("File upload error: %s", e)
                self._send(500, {"error": str(e)})

        elif path == "/api/file/move":
            try:
                length = int(self.headers.get("Content-Length", 0))
                body = self.rfile.read(length)
                data = json.loads(body)
                chat_id = int(data["chat_id"])
                dest_path = data["dest_path"]
                success, message = move_file_internal(chat_id, dest_path)
                self._send(200 if success else 400, {"message": message})
            except Exception as e:
                logger.error("File move error: %s", e)
                self._send(500, {"error": str(e)})

        else:
            self._send(404, {"error": "Not found"})

    def _send(self, code, data):
        try:
            body = json.dumps(data).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(body)
        except Exception as e:
            logger.warning("Response write error: %s", e)

    def log_message(self, format, *args):
        pass

# === 🚀 ТОЧКА ВХОДА ===
def main():
    if not API_KEY:
        raise RuntimeError("MC_API_KEY not set")
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN not set")
    if not ALLOWED_USER_IDS:
        raise RuntimeError("ALLOWED_USER_IDS not set")

    threading.Thread(target=watch_log, daemon=True).start()

    server = ThreadedHTTPServer(("0.0.0.0", PORT), Handler)
    logger.info("Server control listening on port %d", PORT)
    logger.info("Plugin URL: %s", PLUGIN_URL)
    server.serve_forever()

if __name__ == "__main__":
    main()
