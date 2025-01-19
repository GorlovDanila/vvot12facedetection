import os
import json
import math
import boto3
import requests
from PIL import Image

API_URL = "https://api.edenai.run/v2/image/face_detection"
GW_IMAGE_PATTERN = "https://{}/?image={}"
IMG_DIR = "/function/storage/images"

sqs = boto3.client(
    'sqs',
    endpoint_url="https://message-queue.api.cloud.yandex.net",
    region_name="ru-central1"
)

QUEUE_URL = os.getenv("QUEUE_URL")
API_GW_URL = os.getenv("API_GW_URL")
AUTH_TOKEN = os.getenv("AUTH_TOKEN")


def validate_image(image_path):
    try:
        with Image.open(image_path) as img:
            if img.format not in ["JPEG", "PNG"]:
                raise RuntimeError(f"Неподдерживаемый формат изображения: {img.format}")
            if img.size[0] < 50 or img.size[1] < 50:
                raise RuntimeError("Изображение слишком маленькое")
    except Exception as e:
        raise RuntimeError(f"Ошибка проверки изображения: {e}")


def get_image_dimensions(image_path):
    try:
        with Image.open(image_path) as img:
            return img.width, img.height
    except Exception as e:
        raise RuntimeError(f"Ошибка при открытии изображения: {e}")


def process_message(message):
    object_id = message['details']['object_id']
    file_url = GW_IMAGE_PATTERN.format(API_GW_URL, object_id)
    print(f"Сформированный URL: {file_url}")

    try:
        # Проверка доступности URL
        response = requests.get(file_url)
        if response.status_code != 200:
            raise RuntimeError(f"Ошибка доступа к файлу: {response.status_code}")
        if not response.headers.get('Content-Type', '').startswith("image/"):
            raise RuntimeError(f"URL не указывает на изображение: {response.headers.get('Content-Type')}")

        # Локальная валидация изображения
        local_image_path = os.path.join(IMG_DIR, object_id)
        validate_image(local_image_path)
    except Exception as e:
        raise RuntimeError(f"Ошибка при проверке изображения: {e}")

    if not file_url.startswith("http"):
        raise RuntimeError(f"Некорректный URL: {file_url}")

    api_req = {
        "providers": "amazon",
        "file_url": file_url
    }
    headers = {
        "Authorization": AUTH_TOKEN,
        "Content-Type": "application/json"
    }
    response = requests.post(API_URL, json=api_req, headers=headers)
    print(f"Ответ API: {response.status_code} {response.text}")

    if response.status_code != 200:
        raise RuntimeError(f"API error: {response.status_code} {response.text}")

    api_resp = response.json()

    if not api_resp.get('amazon') or api_resp['amazon'].get('status') != "success":
        raise RuntimeError(f"Ошибка в API: {api_resp.get('amazon', {}).get('error', 'Неизвестная ошибка')}")

    items = api_resp['amazon'].get('items', [])
    if not items:
        raise RuntimeError("API не вернуло элементы 'items'.")

    image_path = os.path.join(IMG_DIR, object_id)
    max_x, max_y = get_image_dimensions(image_path)

    tasks = []
    for item in api_resp['amazon']['items']:
        bbox = item['bounding_box']
        x_min = int(math.floor(max_x * bbox['x_min']))
        y_min = int(math.floor(max_y * bbox['y_min']))
        x_max = int(math.ceil(max_x * bbox['x_max']))
        y_max = int(math.ceil(max_y * bbox['y_max']))

        task = {
            "bounds": {
                "x": x_min,
                "y": y_min,
                "width": x_max - x_min,
                "height": y_max - y_min
            },
            "objectID": object_id
        }
        tasks.append(task)

    for task in tasks:
        response = sqs.send_message(
            QueueUrl=QUEUE_URL,
            MessageBody=json.dumps(task)
        )
        print(f"Задача отправлена: {response['MessageId']}")


def handler(event, context):
    if isinstance(event, str):
        event = json.loads(event)

    for message in event['messages']:
        try:
            process_message(message)
        except Exception as e:
            print(f"Ошибка обработки сообщения: {e}")

    return {
        "statusCode": 200,
        "body": "Успешно обработано"
    }
