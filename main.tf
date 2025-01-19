terraform {
  required_providers {
    yandex = {
      source = "yandex-cloud/yandex"
    }
    archive = {
      source  = "hashicorp/archive"
      version = "2.4.2"
    }
  }
  required_version = ">= 0.13"
}

provider "yandex" {
  cloud_id                 = var.cloud_id
  folder_id                = var.folder_id
  service_account_key_file = "/Users/d.gorlov/yc-keys/key.json"
}

resource "yandex_iam_service_account" "bot_account" {
  name      = "bot-account"
  folder_id = var.folder_id
}

resource "yandex_iam_service_account_static_access_key" "queue_static_key" {
  service_account_id = yandex_iam_service_account.bot_account.id
}

resource "yandex_resourcemanager_folder_iam_binding" "mount_iam" {
  folder_id = var.folder_id
  role      = "admin"

  members = [
    "serviceAccount:${yandex_iam_service_account.bot_account.id}",
  ]
}

variable "folder_id" {
  type = string
}

variable "cloud_id" {
  type = string
}

variable "bucket_photo_name" {
  default = "vvot12-photo"
}

variable "bucket_faces_name" {
  default = "vvot12-faces"
}

variable "queue_name" {
  default = "vvot12-tasks"
}

variable "api_gateway_name" {
  default = "vvot12-api-gw"
}

variable "amazon_auth_token" {
  type = string
}

resource "yandex_storage_bucket" "photo" {
  bucket    = var.bucket_photo_name
  folder_id = var.folder_id
}

resource "yandex_storage_bucket" "faces" {
  bucket    = var.bucket_faces_name
  folder_id = var.folder_id
}

resource "yandex_message_queue" "tasks" {
  name                       = var.queue_name
  visibility_timeout_seconds = 600
  receive_wait_time_seconds  = 20
  message_retention_seconds  = 1209600
  access_key                 = yandex_iam_service_account_static_access_key.queue_static_key.access_key
  secret_key                 = yandex_iam_service_account_static_access_key.queue_static_key.secret_key
}

resource "yandex_api_gateway" "gateway" {
  name = var.api_gateway_name
  labels = {
    label       = "label"
    empty-label = ""
  }
  spec = <<-EOT
    openapi: "3.0.0"
    info:
      version: 1.0.0
      title: Face API
    paths:
      /:
        get:
          summary: Serve static file from Yandex Cloud Object Storage
          parameters:
            - name: face
              in: query
              required: false
              schema:
                type: string
            - name: image
              in: query
              required: false
              schema:
                type: string
          responses:
            "200":
              description: File
              content:
                image/jpeg:
                  schema:
                    type: string
                    format: binary
          x-yc-apigateway-integration:
            type: cloud_functions
            payload_format_version: '0.1'
            function_id: ${yandex_function.api_gw.id}
            tag: $latest
            service_account_id: ${yandex_iam_service_account.bot_account.id}
  EOT
}


resource "yandex_function" "face_detection" {
  name               = "vvot12-face-detection"
  folder_id          = var.folder_id
  memory             = 128
  execution_timeout  = 60
  runtime            = "python312"
  entrypoint         = "face_detection.handler"
  user_hash          = archive_file.face_detection_zip.output_sha256
  service_account_id = yandex_iam_service_account.bot_account.id
  environment = {
    "QUEUE_URL"             = yandex_message_queue.tasks.id
    "AWS_ACCESS_KEY_ID"     = yandex_iam_service_account_static_access_key.queue_static_key.access_key
    "AWS_SECRET_ACCESS_KEY" = yandex_iam_service_account_static_access_key.queue_static_key.secret_key
    "API_GW_URL"            = yandex_api_gateway.gateway.domain
    "AUTH_TOKEN"            = var.amazon_auth_token
  }

  storage_mounts {
    mount_point_name = "images"
    bucket           = yandex_storage_bucket.photo.bucket
    prefix           = ""
  }

  content {
    zip_filename = archive_file.face_detection_zip.output_path
  }
}

resource "archive_file" "face_detection_zip" {
  type        = "zip"
  output_path = "face_detection.zip"
  source_dir  = "main/face_detection"
}

resource "yandex_function" "face_cut" {
  name               = "vvot12-face-cut"
  folder_id          = var.folder_id
  memory             = 128
  execution_timeout  = 60
  runtime            = "python312"
  entrypoint         = "face_cut.handler"
  user_hash          = archive_file.face_cut_zip.output_sha256
  service_account_id = yandex_iam_service_account.bot_account.id
  environment = {
    "YDB_URL"               = yandex_ydb_database_serverless.face_img_db.ydb_full_endpoint
  }

  storage_mounts {
    mount_point_name = "images"
    bucket           = yandex_storage_bucket.photo.bucket
    prefix           = ""
  }

  storage_mounts {
    mount_point_name = "faces"
    bucket           = yandex_storage_bucket.faces.bucket
    prefix           = ""
  }

  content {
    zip_filename = archive_file.face_cut_zip.output_path
  }
}

resource "archive_file" "face_cut_zip" {
  type        = "zip"
  output_path = "face_cut.zip"
  source_dir  = "main/face_cut"
}

resource "yandex_function" "bot" {
  name               = "vvot12-boot"
  folder_id          = var.folder_id
  memory             = 128
  execution_timeout  = 60
  runtime            = "python312"
  entrypoint         = "bot.handler"
  user_hash          = archive_file.bot_zip.output_sha256
  service_account_id = yandex_iam_service_account.bot_account.id

  environment = {
    "TG_API_KEY" = var.tg_bot_key
    "YDB_URL"    = yandex_ydb_database_serverless.face_img_db.ydb_full_endpoint
    "API_GW_URL" = yandex_api_gateway.gateway.domain
    "folder_id"  = var.folder_id
  }

  storage_mounts {
    mount_point_name = "faces"
    bucket           = yandex_storage_bucket.faces.bucket
    prefix           = ""
  }

  storage_mounts {
    mount_point_name = "images"
    bucket           = yandex_storage_bucket.photo.bucket
    prefix           = ""
  }

  content {
    zip_filename = archive_file.bot_zip.output_path
  }
}

resource "archive_file" "bot_zip" {
  type        = "zip"
  output_path = "bot.zip"
  source_dir  = "main/bot"
}

resource "yandex_function_trigger" "photo_trigger" {
  name = "photo-trigger"
  function {
    id                 = yandex_function.face_detection.id
    service_account_id = yandex_iam_service_account.bot_account.id
    retry_attempts     = 2
    retry_interval     = 10
  }
  object_storage {
    batch_cutoff = 2
    bucket_id    = yandex_storage_bucket.photo.id
    suffix       = ".jpg"
    create       = true
    update       = false
    delete       = false
  }
}

resource "yandex_function_trigger" "task_trigger" {
  name = "task-trigger"
  function {
    id                 = yandex_function.face_cut.id
    service_account_id = yandex_iam_service_account.bot_account.id
  }
  message_queue {
    queue_id           = yandex_message_queue.tasks.arn
    batch_cutoff       = "5"
    batch_size         = "5"
    service_account_id = yandex_iam_service_account.bot_account.id
  }
}

variable "tg_bot_key" {
  type = string
}

resource "yandex_ydb_database_serverless" "face_img_db" {
  name                = "vvot12-db-face"
  deletion_protection = false

  serverless_database {
    enable_throttling_rcu_limit = false
    provisioned_rcu_limit       = 10
    storage_size_limit          = 50
    throttling_rcu_limit        = 0
  }
}

resource "yandex_ydb_table" "relations_table" {
  path              = "relations"
  connection_string = yandex_ydb_database_serverless.face_img_db.ydb_full_endpoint

  column {
    name     = "ImageID"
    type     = "String"
    not_null = true
  }
  column {
    name     = "FaceID"
    type     = "String"
    not_null = true
  }

  primary_key = ["FaceID"]
}

resource "yandex_ydb_table" "names_table" {
  path              = "names"
  connection_string = yandex_ydb_database_serverless.face_img_db.ydb_full_endpoint

  column {
    name     = "FaceName"
    type     = "String"
    not_null = false
  }
  column {
    name     = "FaceID"
    type     = "String"
    not_null = true
  }

  primary_key = ["FaceID"]
}

resource "yandex_function_iam_binding" "function-iam" {
  function_id = yandex_function.bot.id
  role        = "serverless.functions.invoker"

  members = [
    "system:allUsers",
  ]
}

resource "yandex_function" "api_gw" {
  name               = "vvot12-api-gw"
  user_hash          = archive_file.gw_zip.output_sha256
  runtime            = "python312"
  entrypoint         = "api_gw.handler"
  memory             = 128
  execution_timeout  = 10
  service_account_id = yandex_iam_service_account.bot_account.id

  storage_mounts {
    mount_point_name = "faces"
    bucket           = yandex_storage_bucket.faces.bucket
    prefix           = ""
  }

  storage_mounts {
    mount_point_name = "images"
    bucket           = yandex_storage_bucket.photo.bucket
    prefix           = ""
  }

  content {
    zip_filename = archive_file.gw_zip.output_path
  }
}

resource "archive_file" "gw_zip" {
  type        = "zip"
  output_path = "gw.zip"
  source_dir  = "main/api_gw"
}

resource "null_resource" "curl" {
  provisioner "local-exec" {
    command = "curl --insecure -X POST https://api.telegram.org/bot${var.tg_bot_key}/setWebhook?url=https://functions.yandexcloud.net/${yandex_function.bot.id}"
  }

  triggers = {
    on_version_change = var.tg_bot_key
  }

  provisioner "local-exec" {
    when    = destroy
    command = "curl --insecure -X POST https://api.telegram.org/bot${self.triggers.on_version_change}/deleteWebhook"
  }
}
