import base64
import os
from PIL import Image

FACES_DIR = "/function/storage/faces"
IMAGES_DIR = "/function/storage/images"


def handler(request, context):
    face_name = request.get("queryStringParameters", {}).get("face")
    image_name = request.get("queryStringParameters", {}).get("image")
    file_name = face_name or image_name
    directory = FACES_DIR if face_name else IMAGES_DIR

    if not file_name:
        return {
            "statusCode": 404,
            "body": '{"error": "File not found"}',
            "headers": {
                "Content-Type": "application/json"
            }
        }

    file_path = os.path.join(directory, file_name)

    try:
        with Image.open(file_path) as img:
            img.verify()
            print(f"Формат изображения: {img.format}, размер: {img.size}")
    except Exception as e:
        print(f"Ошибка обработки изображения: {e}")

    try:
        with open(file_path, "rb") as file:
            file_content = file.read()

        return {
            "statusCode": 200,
            "body": base64.b64encode(file_content).decode("utf-8"),
            # "body": file_content,
            "headers": {
                "Content-Type": "image/jpeg",
                "Content-Disposition": f"attachment; filename={file_name}"
            },
            "isBase64Encoded": True
        }

    except FileNotFoundError:
        return {
            "statusCode": 404,
            "body": '{"error": "File not found"}',
            "headers": {
                "Content-Type": "application/json"
            }
        }
