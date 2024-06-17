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
    clients.append(websocket)
    try:
        while True:
            data = await websocket.receive_text()
            await save_to_drive(data)  # Google Driveに保存
            await notification.asend(data)
    except WebSocketDisconnect:
        clients.remove(websocket)

# startup (preparing notification generator)

# CSVデータをGoogle Driveに保存する関数


async def save_to_drive(data: str):
    async with aiofiles.tempfile.NamedTemporaryFile('w', delete=False) as temp_file:
        await temp_file.write(data)
        temp_file_path = temp_file.name
    tokyo_tz = pytz.timezone('Asia/Tokyo')
    current_time = datetime.now(tokyo_tz).strftime("%y_%m_%d_%H_%M")
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
