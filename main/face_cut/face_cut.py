import os
import json
import ydb
from PIL import Image
from uuid import uuid4
from ydb.iam import MetadataUrlCredentials


def init_ydb_driver(url):
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


def handle_message(event):
    messages = event.get("messages", [])
    if not messages:
        raise ValueError("Поле 'messages' отсутствует или пустое в event")

    ydb_url = os.getenv("YDB_URL")
    driver, session_pool = init_ydb_driver(ydb_url)

    for message in messages:
        try:
            message_body = message["details"]["message"]["body"]
            print(f"Тело сообщения (body): {message_body}")

            task_data = json.loads(message_body)
            object_id = task_data["objectID"]
            bounds = task_data["bounds"]
            print(f"Обнаружен объект: objectID={object_id}, bounds={bounds}")

            with session_pool.checkout() as session:
                print(f"Сессия YDB успешно получена.")
                process_image_and_save_to_ydb(session, object_id, bounds)

        except KeyError as e:
            print(f"Ошибка: отсутствует ключ {e} в сообщении")
            raise ValueError(f"Некорректное сообщение: отсутствует ключ {e}")
        except json.JSONDecodeError as e:
            print(f"Ошибка парсинга JSON в поле 'body': {e}")
            raise ValueError(f"Некорректный JSON в поле 'body': {e}")
        except Exception as e:
            print(f"Ошибка при обработке сообщения: {e}")
            raise RuntimeError(f"Ошибка при обработке сообщения: {e}")

    print("Обработка сообщений завершена.")
    return {
        "statusCode": 200,
        "body": "Сообщение успешно обработано",
    }


def process_image_and_save_to_ydb(session, object_id, bounds):
    input_dir = "/function/storage/images"
    output_dir = "/function/storage/faces"
    input_image_path = os.path.join(input_dir, object_id)

    try:
        img = Image.open(input_image_path)
        print(f"Изображение {object_id} открыто успешно.")
    except Exception as e:
        print(f"Ошибка при открытии изображения {object_id}: {e}")
        raise RuntimeError(f"Ошибка при открытии изображения: {e}")

    cropped_img = img.crop((bounds["x"], bounds["y"],
                            bounds["x"] + bounds["width"],
                            bounds["y"] + bounds["height"]))

    face_name = f"{uuid4()}.jpg"
    output_image_path = os.path.join(output_dir, face_name)
    cropped_img.save(output_image_path, format="JPEG")
    print(f"Сохранено обрезанное изображение: {output_image_path}")

    try:
        query_names = f"""
            UPSERT INTO `names` (FaceID)
            VALUES ("{face_name}");
        """
        execute_ydb_query(session, query_names)

        query_relations = f"""
            UPSERT INTO `relations` (ImageID, FaceID)
            VALUES ("{object_id}", "{face_name}");
        """
        execute_ydb_query(session, query_relations)
        print(f"Записи для FaceID={face_name} и ImageID={object_id} успешно добавлены в YDB.")
    except Exception as e:
        print(f"Ошибка при записи в YDB: {e}")
        raise RuntimeError(f"Ошибка при записи в YDB: {e}")


def handler(event, context):
    try:
        response = handle_message(event)
    except Exception as e:
        print(f"Ошибка при обработке сообщения: {e}")
        response = {
            "statusCode": 500,
            "body": str(e),
        }
    return response
