from google.auth import default
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
import os
import aiofiles
import json
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from datetime import datetime
import pytz
from aiocsv import AsyncDictWriter
from collections import deque
import joblib
from pathlib import Path
from lib.create_futures import create_features
import numpy as np

# ===============================================
# ランダムフォレストモデル読み込み
# ===============================================

MODEL_PATH = Path("model/rf_model_1115.pkl")
rf_model = joblib.load(MODEL_PATH)


# ===============================================
# FastAPI アプリ
# ===============================================
app = FastAPI()

# ===============================================
# Google Drive 認証（既存コード）
# ===============================================
SCOPES = ["https://www.googleapis.com/auth/drive.file"]


def get_drive_service():
    creds, _ = default(scopes=SCOPES)
    return build("drive", "v3", credentials=creds)


drive_service = get_drive_service()


# ===============================================
# バッファ（単一クライアント前提）
# ===============================================
accel_buffer = deque(maxlen=400)  # 20秒 × 20Hz
hr_buffer = deque(maxlen=20)  # 20秒 × 1Hz

android_control_ws = None  # Androidへの mode 通信用


# ===============================================
# Fitbit 既存 WS（変更禁止）
# ===============================================
@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()

    csv_data = {
        "timestamp": [],
        "data_type": [],
        "heart_rate": [],
        "x": [],
        "y": [],
        "z": [],
        "rpe": [],
    }

    global accel_buffer, hr_buffer

    try:
        while True:
            data = await websocket.receive_text()
            data_dict = json.loads(data)

            # ----------------------------------------------------
            # finish → CSV 保存
            # ----------------------------------------------------
            if data_dict["data_type"] == "finish":
                await save_to_drive(csv_data)
                csv_data = {
                    "timestamp": [],
                    "data_type": [],
                    "heart_rate": [],
                    "x": [],
                    "y": [],
                    "z": [],
                    "rpe": [],
                }
                continue

            # ----------------------------------------------------
            # 加速度（Fitbit → CSV & Intervention バッファ）
            # ----------------------------------------------------
            if data_dict["data_type"] == "accelerometer":
                for d in data_dict["data"]:
                    # CSV 用
                    csv_data["x"].append(d[0])
                    csv_data["y"].append(d[1])
                    csv_data["z"].append(d[2])
                    csv_data["heart_rate"].append(None)
                    csv_data["rpe"].append(None)
                    csv_data["timestamp"].append(d[3])
                    csv_data["data_type"].append("accelerometer")

                    # Intervention 用バッファ
                    accel_buffer.append([d[0], d[1], d[2]])

                continue

            # ----------------------------------------------------
            # その他心拍/RPE
            # ----------------------------------------------------
            fields = ["x", "y", "z", "heart_rate", "rpe"]
            data_defaults = {field: None for field in fields}

            if data_dict["data_type"] == "heart_rate":
                data_defaults["heart_rate"] = data_dict["data"]["heartRate"]

            elif data_dict["data_type"] == "fatigue":
                data_defaults["rpe"] = data_dict["data"]["rpe"]

            csv_data["timestamp"].append(data_dict["timestamp"])
            csv_data["data_type"].append(data_dict["data_type"])

            for field in fields:
                csv_data[field].append(data_defaults[field])

            # ======================================================
            # Intervention トリガー：fatigue 受信で推論 → Android に送信
            # ======================================================
            if data_dict["data_type"] == "fatigue":
                await run_intervention_logic()

                # 既存の CSV 保存（壊さない）
                # await save_to_drive(csv_data)
                csv_data = {
                    "timestamp": [],
                    "data_type": [],
                    "heart_rate": [],
                    "x": [],
                    "y": [],
                    "z": [],
                    "rpe": [],
                }

    except WebSocketDisconnect as e:
        print(f"Fitbit websocket切断: {e}")


# ===============================================
# Google Drive 保存（既存コード）
# ===============================================
async def save_to_drive(csv_data: dict):
    async with aiofiles.tempfile.NamedTemporaryFile("w", delete=False) as temp_file:
        writer = AsyncDictWriter(temp_file, fieldnames=csv_data.keys())
        await writer.writeheader()
        rows = [dict(zip(csv_data, t)) for t in zip(*csv_data.values())]
        await writer.writerows(rows)
        temp_file_path = temp_file.name

    tokyo_tz = pytz.timezone("Asia/Tokyo")
    current_time = datetime.now(tokyo_tz).strftime("%y%m%d-%H%M")
    file_name = f"{current_time}.csv"

    folder_id = "1LsY_gS5nL9XBXMof6RXHyWU1O55TbMF1"
    file_metadata = {"name": file_name, "parents": [folder_id]}
    media = MediaFileUpload(temp_file_path, mimetype="text/csv")

    drive_service.files().create(
        body=file_metadata, media_body=media, fields="id"
    ).execute()

    os.remove(temp_file_path)


# ===============================================
# Android → 心拍送信用 WebSocket（単一）
# ===============================================
@app.websocket("/ws/android_hr")
async def android_hr(websocket: WebSocket):
    await websocket.accept()
    global hr_buffer

    try:
        while True:
            data = await websocket.receive_text()
            hr = json.loads(data)["heart_rate"]
            hr_buffer.append(hr)

    except WebSocketDisconnect:
        print("Android HR WS disconnected")


# ===============================================
# Android ← mode 通信用 WebSocket（単一）
# ===============================================
@app.websocket("/ws/android_control")
async def android_control(websocket: WebSocket):
    await websocket.accept()
    global android_control_ws
    android_control_ws = websocket

    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        android_control_ws = None
        print("Android control WS disconnected")


# ===============================================
# Intervention 推論ロジック（fatigue 受信で実行）
# ===============================================
async def run_intervention_logic():
    global accel_buffer, hr_buffer, android_control_ws
    print("Intervention ロジック実行")
    print(f"加速度データ: {accel_buffer}")
    print(f"心拍データ数: {hr_buffer}")

    # === 特徴量生成 ===
    try:
        feats = create_features(hr_buffer, accel_buffer)
        feats = np.asarray(feats, dtype=float)
        print(f"生成特徴量: {feats}")
    except Exception as e:
        print(f"特徴量生成失敗: {e}")
        return "normal"

    # === RPE 推定 ===
    try:
        pred_rpe = float(rf_model.predict([feats])[0])
        print(f"推定RPE: {pred_rpe}")
    except Exception as e:
        print(f"RPE 推定失敗: {e}")
        return "normal"

    # === mode 判定 ===
    mode = "normal" if pred_rpe <= 6 else "minus10"
    print(f"判定mode: {mode}")

    # === Android へ送信 ===
    if android_control_ws:
        try:
            await android_control_ws.send_json({"mode": mode})
        except:
            print("Android WS へ送信失敗")

    # === バッファクリア（次セットへ） ===
    accel_buffer.clear()
    hr_buffer.clear()

    return mode
