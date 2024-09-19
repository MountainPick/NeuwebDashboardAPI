import asyncio
import base64
import cv2
import numpy as np
import json
import requests
import websocket
from fastapi import FastAPI, WebSocket
from fastapi.responses import HTMLResponse
import logging
import time

# Configure logging
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

app = FastAPI()

# WebSocket URL for receiving live stream
ws_url = "ws://52.86.92.233:8004/ws/live-stream"

# Model WebSocket URL for sending frames
neuweb_IP = "54.79.32.154"
port = 8000
camera_id = 1

# Variables to track frames and time
frame_count = 0
start_time = 0
last_sent_time = 0
SEND_INTERVAL = 0.1  # Adjust this value to control sending rate

# Global variables for WebSocket connections
ws_stream = None
ws_model = None
processed_frame = None


# Login to obtain the authentication token
def login_to_get_token(username, password, url):
    """Log in to the server and get the authentication token"""
    logger.info(f"Attempting to login and get token from {url}")
    response = requests.post(
        f"{url}/token", data={"username": username, "password": password}
    )
    response_data = response.json()
    logger.info("Successfully obtained token")
    return response_data["access_token"]


# Token retrieval (replace with your actual username, password, and server URL)
username = "test"  # Replace with your actual username
password = "test"  # Replace with your actual password
token = login_to_get_token(username, password, f"http://{neuweb_IP}:{port}")

# Model WebSocket URL
neuweb_ws_url = f"ws://{neuweb_IP}:{port}/ws/process-stream-image?token={token}"


async def process_frame(frame):
    global ws_model, last_sent_time, processed_frame
    current_time = time.time()

    if current_time - last_sent_time < SEND_INTERVAL:
        return

    last_sent_time = current_time

    try:
        encode_param = [int(cv2.IMWRITE_JPEG_QUALITY), 50]
        _, img_encoded = cv2.imencode(".jpg", frame, encode_param)
        img_bytes = img_encoded.tobytes()
        img_base64 = base64.b64encode(img_bytes).decode("utf-8")
        message = json.dumps(
            {
                "camera_id": camera_id,
                "image": img_base64,
                "token": token,
                "return_processed_image": False,
                "subcriptionplan_id": 1,
            }
        )

        ws_model.send(message)
        logger.debug("Sent frame to model WebSocket")
        response = ws_model.recv()
        logger.debug("Received response from model WebSocket")
        response_data = json.loads(response)
        frame_results = response_data.get("frame_results", {})
        num_human_tracks = frame_results.get("num_human_tracks", 0)
        human_tracked_boxes = frame_results.get("human_tracked_boxes", [])
        # notification_data = frame_results.get("notification", [])

        if human_tracked_boxes is not None:
            for human in human_tracked_boxes:
                track_box = human.get("track_box")
                track_id = human.get("track_id", "N/A")
                if track_box:
                    x1, y1, x2, y2 = map(int, track_box)
                    cv2.rectangle(frame, (x1, y1), (x2, y2), (0, 0, 255), 2)
                    label = f"ID: {track_id}"
                    (label_width, label_height), _ = cv2.getTextSize(
                        label, cv2.FONT_HERSHEY_SIMPLEX, 0.5, 1
                    )
                    cv2.rectangle(
                        frame,
                        (x1, y1 - label_height - 5),
                        (x1 + label_width, y1),
                        (0, 0, 255),
                        -1,
                    )
                    cv2.putText(
                        frame,
                        label,
                        (x1, y1 - 5),
                        cv2.FONT_HERSHEY_SIMPLEX,
                        0.5,
                        (255, 255, 255),
                        1,
                    )

        cv2.putText(
            frame,
            f"People: {num_human_tracks}",
            (10, 30),
            cv2.FONT_HERSHEY_SIMPLEX,
            1,
            (0, 255, 0),
            2,
        )

        _, buffer = cv2.imencode(".jpg", frame)
        processed_frame = base64.b64encode(buffer).decode("utf-8")
        logger.debug(f"Processed frame with {num_human_tracks} people detected")

    except Exception as e:
        logger.error(f"Error processing model response: {e}")
        await reconnect_model_ws()


async def reconnect_model_ws():
    global ws_model
    try:
        if ws_model:
            ws_model.close()
    except:
        pass
    await asyncio.sleep(5)
    logger.info("Reconnecting to model WebSocket...")
    try:
        ws_model = websocket.create_connection(neuweb_ws_url)
        logger.info("Reconnected to model WebSocket")
    except Exception as e:
        logger.error(f"Failed to reconnect to model WebSocket: {e}")
        ws_model = None


async def stream_processor():
    global ws_stream, ws_model, frame_count, start_time
    logger.info("Starting stream processor")

    while True:
        try:
            if ws_stream is None or not ws_stream.connected:
                ws_stream = websocket.WebSocket()
                ws_stream.connect(ws_url)
                logger.info("Connected to stream WebSocket")

            if ws_model is None or not ws_model.connected:
                ws_model = websocket.create_connection(neuweb_ws_url)
                logger.info("Connected to model WebSocket")

            message = ws_stream.recv()
            if not message:
                logger.warning("Received empty message from stream WebSocket")
                continue

            img_data = base64.b64decode(message)
            np_arr = np.frombuffer(img_data, np.uint8)
            frame = cv2.imdecode(np_arr, cv2.IMREAD_COLOR)

            if frame is not None:
                await process_frame(frame)
                frame_count += 1
            else:
                logger.warning("Error: Decoded frame is None")

        except websocket.WebSocketException as e:
            logger.error(f"WebSocket error in stream processor: {e}")
            await asyncio.sleep(5)
            ws_stream = None
        except Exception as e:
            logger.error(f"Unexpected error in stream processor: {e}", exc_info=True)
            await asyncio.sleep(5)
            ws_stream = None

        await asyncio.sleep(0.01)  # Small delay to prevent blocking


@app.on_event("startup")
async def startup_event():
    logger.info("Starting up the application")
    asyncio.create_task(stream_processor())


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    logger.info("New WebSocket connection accepted")
    try:
        while True:
            if processed_frame:
                await websocket.send_text(processed_frame)
            await asyncio.sleep(0.03)  # Adjust this value to control the frame rate
    except Exception as e:
        logger.error(f"Error in WebSocket endpoint: {e}")


@app.get("/", response_class=HTMLResponse)
async def get():
    logger.info("Serving HTML content")
    html_content = """
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Live Video Stream</title>
        <style>
            #videoStream {
                max-width: 100%;
                height: auto;
            }
        </style>
    </head>
    <body>
        <h1>Live Video Stream</h1>
        <img id="videoStream" src="" alt="Live Video Stream">

    </body>
    </html>
    """
    return HTMLResponse(content=html_content)


if __name__ == "__main__":
    import uvicorn

    logger.info("Starting the FastAPI application")
    uvicorn.run(app, host="0.0.0.0", port=8000, log_level="info")
