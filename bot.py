import os
import logging
import asyncio
import aiohttp
from aiohttp import web
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton
from aiogram.utils.keyboard import ReplyKeyboardBuilder
from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

BOT_TOKEN = os.getenv("BOT_TOKEN")
ALLOWED_USERS = [int(uid) for uid in os.getenv("ALLOWED_USER_IDS", "").split(",") if uid.strip()]
MC_API_URL = os.getenv("MC_API_URL")
MC_API_KEY = os.getenv("MC_API_KEY")
WEBHOOK_HOST = os.getenv("WEBHOOK_HOST")  # https://your-service.onrender.com
WEBHOOK_PATH = "/webhook"
WEBHOOK_URL = f"{WEBHOOK_HOST}{WEBHOOK_PATH}"
WEB_PORT = int(os.getenv("PORT", 8000))

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()


def get_keyboard() -> ReplyKeyboardMarkup:
    builder = ReplyKeyboardBuilder()
    builder.row(
        KeyboardButton(text="Список игроков"),
        KeyboardButton(text="Скачать лог"),
    )
    builder.row(KeyboardButton(text="Остановить сервер"))
    return builder.as_markup(resize_keyboard=True)


def is_allowed(user_id: int) -> bool:
    return user_id in ALLOWED_USERS


@dp.message(Command("start"))
async def start(message: types.Message):
    if not is_allowed(message.from_user.id):
        return
    await message.answer("MC Server Control", reply_markup=get_keyboard())


@dp.message(F.text == "Список игроков")
async def players(message: types.Message):
    if not is_allowed(message.from_user.id):
        return

    headers = {"X-Api-Key": MC_API_KEY}
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(f"{MC_API_URL}/api/players", headers=headers, timeout=aiohttp.ClientTimeout(total=10)) as r:
                data = await r.json()
    except Exception:
        await message.answer("Сервер недоступен.")
        return

    players_list = data.get("players", [])
    online = data.get("online", 0)
    max_p = data.get("max", 0)

    if players_list:
        names = "\n".join(f"- {p}" for p in players_list)
        msg = f"Онлайн: {online}/{max_p}\n\n{names}"
    else:
        msg = f"Онлайн: {online}/{max_p}\n\nИгроков нет."

    await message.answer(msg)


@dp.message(F.text == "Скачать лог")
async def logs(message: types.Message):
    if not is_allowed(message.from_user.id):
        return

    headers = {"X-Api-Key": MC_API_KEY}
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(f"{MC_API_URL}/api/logs", headers=headers, timeout=aiohttp.ClientTimeout(total=30)) as r:
                if r.status == 200:
                    data = await r.read()
                    await message.answer_document(
                        types.BufferedInputFile(data, filename="latest.log")
                    )
                elif r.status == 404:
                    await message.answer("Лог-файл не найден.")
                else:
                    await message.answer("Ошибка при получении лога.")
    except aiohttp.ClientConnectorError:
        await message.answer("Сервер недоступен.")


@dp.message(F.text == "Остановить сервер")
async def stop_server(message: types.Message):
    if not is_allowed(message.from_user.id):
        return

    headers = {"X-Api-Key": MC_API_KEY}
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(f"{MC_API_URL}/api/stop", headers=headers, timeout=aiohttp.ClientTimeout(total=10)) as r:
                if r.status == 200:
                    await message.answer("Сервер останавливается.")
                else:
                    await message.answer("Ошибка при остановке.")
    except aiohttp.ClientConnectorError:
        await message.answer("Сервер недоступен.")


async def on_startup(app: web.Application):
    await bot.set_webhook(WEBHOOK_URL)
    logger.info(f"Webhook set to {WEBHOOK_URL}")


async def on_shutdown(app: web.Application):
    await bot.delete_webhook()
    logger.info("Webhook deleted")


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

    app = web.Application()
    app.on_startup.append(on_startup)
    app.on_shutdown.append(on_shutdown)

    SimpleRequestHandler(dispatcher=dp, bot=bot).register(app, path=WEBHOOK_PATH)
    setup_application(app, dp, bot=bot)

    web.run_app(app, host="0.0.0.0", port=WEB_PORT)


if __name__ == "__main__":
    main()
