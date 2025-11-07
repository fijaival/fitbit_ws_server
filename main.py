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

# websocket endpoint


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    csv_data = {"timestamp": [], "data_type": [], "heart_rate": [], "x": [], "y": [], "z": [], "rpe": []}
    try:
        while True:
            data = await websocket.receive_text()

            data_dict = json.loads(data)

            if data_dict["data_type"] == "finish":
                await save_to_drive(csv_data)  # Google Driveに保存
                csv_data = {"timestamp": [], "data_type": [], "heart_rate": [], "x": [], "y": [], "z": [], "rpe": []}
                continue

            # 初期化
            fields = ["x", "y", "z", "heart_rate", "rpe"]
            data_defaults = {field: None for field in fields}

            # 各データタイプごとの値を設定
            if data_dict["data_type"] == "accelerometer":
                for data in data_dict["data"]:
                    csv_data["x"].append(data[0])
                    csv_data["y"].append(data[1])
                    csv_data["z"].append(data[2])
                    csv_data["heart_rate"].append(None)
                    csv_data["rpe"].append(None)
                    csv_data["timestamp"].append(data[3])
                    csv_data["data_type"].append(data_dict["data_type"])
                continue
            elif data_dict["data_type"] == "heart_rate":
                data_defaults["heart_rate"] = data_dict["data"]["heartRate"]
            elif data_dict["data_type"] == "fatigue":
                data_defaults["rpe"] = data_dict["data"]["rpe"]
            csv_data["timestamp"].append(data_dict["timestamp"])
            csv_data["data_type"].append(data_dict["data_type"])
        

            for field in fields:
                csv_data[field].append(data_defaults[field])

            # rpeデータが来たら逐一保存
            if data_dict["data_type"] == "fatigue":
                await save_to_drive(csv_data)  # Google Driveに保存
                csv_data = {"timestamp": [], "data_type": [], "heart_rate": [], "x": [], "y": [], "z": [], "rpe": []}
    except WebSocketDisconnect as e:
        print(f"websocketの接続が切断されました: {e}")


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
