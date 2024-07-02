from google.auth import default
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
import os
import aiofiles
import json
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from datetime import datetime
import pytz
import aiofiles
from aiocsv import AsyncDictWriter
# fast app
app = FastAPI()
# 認証情報の設定

SCOPES = ['https://www.googleapis.com/auth/drive.file']


def get_drive_service():
    """Google Drive API のサービスオブジェクトを取得する"""

    creds, _ = default(scopes=SCOPES)
    return build('drive', 'v3', credentials=creds)


drive_service = get_drive_service()


# client websockets list
clients = []

# notify to all clients


async def notify(msg: str):
    for websocket in clients:
        await websocket.send_text(msg)

# notification generator


async def notification_generator():
    while True:
        msg = yield
        await notify(msg)
notification = notification_generator()

# websocket endpoint


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    csv_data = {"timestamp": [], "data_type": [], "heart_rate": [], "x": [], "y": [], "z": [], "rpe": []}
    clients.append(websocket)
    try:
        while True:
            data = await websocket.receive_text()

            await notification.asend(data)
            data_dict = json.loads(data)

            csv_data["timestamp"].append(data_dict["timestamp"])
            csv_data["data_type"].append(data_dict["data_type"])
            if data_dict["data_type"] == "finish":
                await save_to_drive(csv_data)  # Google Driveに保存
                csv_data = {"timestamp": [], "data_type": [], "heart_rate": [], "x": [], "y": [], "z": [], "rpe": []}
                continue

            # 初期化
            fields = ["x", "y", "z", "heart_rate", "rpe"]
            data_defaults = {field: None for field in fields}

            # 各データタイプごとの値を設定
            if data_dict["data_type"] == "accelerometer":
                data_defaults.update({
                    "x": data_dict["data"]["x"],
                    "y": data_dict["data"]["y"],
                    "z": data_dict["data"]["z"]
                })
            elif data_dict["data_type"] == "heart_rate":
                data_defaults["heart_rate"] = data_dict["data"]["heartRate"]
            elif data_dict["data_type"] == "fatigue":
                data_defaults["rpe"] = data_dict["data"]["rpe"]

            for field in fields:
                csv_data[field].append(data_defaults[field])
    except WebSocketDisconnect:
        clients.remove(websocket)

# startup (preparing notification generator)

# CSVデータをGoogle Driveに保存する関数


async def save_to_drive(csv_data: dict):
    async with aiofiles.tempfile.NamedTemporaryFile('w', delete=False) as temp_file:
        # CSVライターを作成
        writer = AsyncDictWriter(temp_file, fieldnames=csv_data.keys())
        await writer.writeheader()
        rows = [dict(zip(csv_data, t)) for t in zip(*csv_data.values())]
        await writer.writerows(rows)

        temp_file_path = temp_file.name
    tokyo_tz = pytz.timezone('Asia/Tokyo')
    current_time = datetime.now(tokyo_tz).strftime("%y%m%d-%H%M")
    file_name = f"{current_time}.csv"

    folder_id = "1LsY_gS5nL9XBXMof6RXHyWU1O55TbMF1"
    file_metadata = {
        'name': file_name,
        'parents': [folder_id]
    }
    media = MediaFileUpload(temp_file_path, mimetype='text/csv')

    drive_service.files().create(
        body=file_metadata,
        media_body=media,
        fields='id'
    ).execute()

    os.remove(temp_file_path)


@app.on_event("startup")
async def startup():
    await notification.asend(None)
