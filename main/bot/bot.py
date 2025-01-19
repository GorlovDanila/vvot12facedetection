import json
import os
import ydb
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, CallbackContext
from ydb.iam import MetadataUrlCredentials

MAX_MESSAGE_LEN = 4096
LOCAL_PATH = "/function/storage/images"
OCR_URL = "https://ocr.api.cloud.yandex.net/ocr/v1/recognizeText"
YA_GPT_URL = "https://llm.api.cloud.yandex.net/foundationModels/v1/completion"
GW_PATTERN = "https://{}/?face={}"
GW_IMAGE_PATTERN = "https://{}/?image={}"
BOT_TOKEN = os.getenv("TG_API_KEY")
API_GW_URL = os.getenv("API_GW_URL")

driver = None
session_pool = None


def init_ydb_driver(url):
    global driver, session_pool

    if driver is not None and session_pool is not None:
        return driver, session_pool

    endpoint, params = url.split('?')
    database = params.split('=')[1]
    print(endpoint)
    print(database)

    try:
        credentials = MetadataUrlCredentials()
        driver_config = ydb.DriverConfig(
            endpoint=endpoint,
            database=database,
            credentials=credentials
        )
        driver = ydb.Driver(driver_config)
        driver.wait(fail_fast=True, timeout=10)
        print("Драйвер YDB успешно инициализирован.")

        session_pool = ydb.SessionPool(driver)
        return driver, session_pool

    except Exception as e:
        print(f"Ошибка при инициализации драйвера YDB: {e}")
        raise


def execute_ydb_query(session, query):
    try:
        result = session.transaction().execute(query, commit_tx=True)
        print(f"Запрос успешно выполнен: {result}")
        return result
    except Exception as e:
        print(f"Ошибка при выполнении запроса: {e}")
        raise RuntimeError(f"Ошибка при выполнении запроса: {e}")


def get_ydb_session():
    url = os.getenv("YDB_URL")
    return init_ydb_driver(url)


async def send_reply(context: CallbackContext, chat_id: int, text: str, reply_to: int = None):
    texts = [text[i:i + MAX_MESSAGE_LEN] for i in range(0, len(text), MAX_MESSAGE_LEN)]
    for chunk in texts:
        await context.bot.send_message(chat_id=chat_id, text=chunk, reply_to_message_id=reply_to)


async def send_photo(context: CallbackContext, chat_id: int, photo_url: str):
    await context.bot.send_photo(chat_id=chat_id, photo=photo_url)


def read_face_id():
    try:
        statement = "SELECT FaceID FROM names WHERE FaceName IS NULL LIMIT 1"
        driver, session_pool = get_ydb_session()
        with session_pool.checkout() as session:
            response = session.transaction().execute(
                statement,
                commit_tx=False,
            )
            rows = response[0].rows
            if rows:
                return rows[0].get("FaceID")
        return None
    except Exception as e:
        print(f"Ошибка при выполнении запроса read_face_id: {e}")
        return None


def find_by_name(name):
    try:
        statement = f"""SELECT r.ImageID AS image
            FROM (SELECT FaceID FROM names WHERE FaceName = "{name}") AS n
            INNER JOIN relations AS r ON n.FaceID = r.FaceID"""
        driver, session_pool = get_ydb_session()
        with session_pool.checkout() as session:
            response = session.transaction().execute(
                statement,
                commit_tx=False,
            )
            rows = response[0].rows
            return [row.get("image").decode("utf-8") if isinstance(row.get("image"), bytes) else row.get("image") for
                    row in rows]
    except Exception as e:
        print(f"Ошибка при выполнении запроса find_by_name: {e}")
        return []


async def start(update: Update, context: CallbackContext):
    await send_reply(context, update.effective_chat.id, "Привет! Я готов помочь.")


async def handle_getface(update: Update, context: CallbackContext):
    face_id = read_face_id()
    if face_id:
        face_id_str = face_id.decode("utf-8") if isinstance(face_id, bytes) else face_id

        url = GW_PATTERN.format(API_GW_URL, face_id_str)
        print(f"Generated URL: {url}")
        await send_photo(context, update.effective_chat.id, url)
    else:
        await send_reply(context, update.effective_chat.id, "Не удалось найти фото без имени.")


async def handle_find(update: Update, context: CallbackContext):
    if len(context.args) < 1:
        await send_reply(context, update.effective_chat.id, "Укажите имя для поиска.")
        return

    name = context.args[0]
    print(f"AAA {name}")
    images = find_by_name(name)
    print(f"AAA {images}")
    if images:
        for image_name in images:
            url = GW_IMAGE_PATTERN.format(API_GW_URL, image_name)
            await send_photo(context, update.effective_chat.id, url)
    else:
        await send_reply(context, update.effective_chat.id, f"Фотографии с {name} не найдены.")


async def message_handler(update: Update, context: CallbackContext):
    message = update.message
    if not message:
        return

    if message.reply_to_message:
        face_id = read_face_id()
        if not face_id:
            await send_reply(context, message.chat.id, "Ошибка: FaceID не найден.")
            return

        face_id_str = face_id.decode("utf-8") if isinstance(face_id, bytes) else face_id

        name = message.text
        statement = f"""UPSERT INTO names (FaceID, FaceName) VALUES ("{face_id_str}", "{name}")"""
        driver, session_pool = get_ydb_session()

        with session_pool.checkout() as session:
            session.transaction().execute(
                statement,
                commit_tx=True,
            )

        await send_reply(
            context,
            message.chat.id,
            f"Лицу с ID: `{face_id_str}` присвоено имя `{name}`",
            reply_to=message.message_id,
        )
    else:
        await send_reply(context, message.chat.id, "Ошибка: пустое сообщение.")


async def handler(event, context):
    application = Application.builder().token(BOT_TOKEN).build()

    if not application._initialized:
        await application.initialize()

    if not application.handlers:
        application.add_handler(CommandHandler("start", start))
        application.add_handler(CommandHandler("getface", handle_getface))
        application.add_handler(CommandHandler("find", handle_find))
        application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, message_handler))

    try:
        body = json.loads(event['body'])
        print(body)
        update = Update.de_json(body, application.bot)
        await application.process_update(update)

        print("return")
        return {
            "statusCode": 200,
            "body": json.dumps({"status": "ok"}),
        }
    except Exception as e:
        print(f"Ошибка при обработке запроса: {e}")
        return {
            "statusCode": 500,
            "body": json.dumps({"error": "Ошибка обработки запроса"}),
        }
