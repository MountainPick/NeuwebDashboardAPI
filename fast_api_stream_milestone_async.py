import base64
import datetime
import json
import time
from collections import OrderedDict
import cv2
import nest_asyncio
import requests
import websocket
import threading
import queue
from fastapi import FastAPI, WebSocket
import uvicorn
from typing import Dict
import asyncio
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI()

ALL_TIME_DANGEROUS_SIGNS = []
nest_asyncio.apply()
ws_IP = "54.252.71.145"
# ws_IP = "172.22.22.7"
# ws_IP = "0.0.0.0"
# ws_IP = "3.24.124.52"
API_BASE_URL = f"http://{ws_IP}:8000"
WEBSOCKET_BASE_URL = f"ws://{ws_IP}:8000/ws/process-stream-image"
WEBSOCKET_BASE_URL_ASYNC = f"ws://{ws_IP}:8000/ws/process-stream-image-async"

# Global video stream state
stream_states: Dict[int, Dict] = {}

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # In production, replace with your frontend domain
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Store active WebSocket connections
active_connections = set()


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    print("New WebSocket connection request received")
    await websocket.accept()
    print("WebSocket connection accepted")
    active_connections.add(websocket)
    print(f"Active connections: {len(active_connections)}")

    try:
        while True:
            try:
                # Keep the connection alive and log received messages
                message = await websocket.receive_text()
                print(f"Received message: {message}")

                # Optional: You can also log the raw data
                raw_data = await websocket.receive_json()
                print(f"Received JSON data: {raw_data}")

            except Exception as e:
                print(f"WebSocket error: {e}")
                break
    finally:
        active_connections.remove(websocket)
        print(
            f"Connection closed. Remaining active connections: {len(active_connections)}"
        )


async def broadcast_frame(frame_data: str):
    """Broadcast frame to all connected clients"""
    for connection in active_connections:
        try:
            await connection.send_json({"type": "frame", "frame": frame_data})
        except Exception as e:
            print(f"Error sending frame: {e}")
            active_connections.remove(connection)


def sign_up(
    username,
    password,
    firstname,
    lastname,
    email,
    phonenumber,
    emergencycontact,
    subscriptionplan_id,
):
    sign_up_url = f"{API_BASE_URL}/signup"
    user_data = {
        "username": username,
        "password": password,
        "firstname": firstname,
        "middlename": "",
        "lastname": lastname,
        "email": email,
        "phonenumber": phonenumber,
        "emergencycontact": emergencycontact,
        "subscriptionplan_id": subscriptionplan_id,
    }
    response = requests.post(sign_up_url, json=user_data)
    return response.json()


def login(username, password):
    login_url = f"{API_BASE_URL}/token"
    form_data = {"grant_type": "password", "username": username, "password": password}
    response = requests.post(login_url, data=form_data)
    return response.json()


def stream_video_async(token, camera_id, video_path):
    websocket_url = f"{WEBSOCKET_BASE_URL_ASYNC}?token={token}"
    max_retries = 3
    retry_delay = 5  # seconds
    retry_count = 0

    def connect_websocket():
        try:
            ws = websocket.WebSocket()
            ws.connect(websocket_url)
            print("WebSocket connected successfully")
            return ws
        except Exception as e:
            print(f"Failed to connect WebSocket: {e}")
            return None

    while retry_count < max_retries:
        try:
            ws = connect_websocket()
            if not ws:
                raise Exception("Failed to establish WebSocket connection")

            cap = cv2.VideoCapture(video_path)
            if not cap.isOpened():
                print("Error: Could not open video file.")
                return

            stop_event = threading.Event()
            frame_queue = queue.Queue(maxsize=10)  # Adjust maxsize as needed
            desired_fps = 50
            model_fps = 10
            frame_delay = 1.0 / desired_fps
            reconnect_event = threading.Event()

            # Initialize the sent_frames OrderedDict and lock
            sent_frames = OrderedDict()
            sent_frames_lock = threading.Lock()

            # Thread to capture frames and put them into the queue
            def capture_frames():
                while not stop_event.is_set():
                    ret, frame = cap.read()
                    if not ret:
                        break
                    # Resize to HD
                    # frame = cv2.resize(frame, (1280, 720))
                    frame_timestamp = (
                        time.time()
                    )  # Record the timestamp when the frame is captured
                    try:
                        frame_queue.put(
                            (frame, frame_timestamp), timeout=1
                        )  # Put both frame and timestamp in the queue
                    except queue.Full:
                        continue  # Skip frame if queue is full
                    time.sleep(frame_delay)

            # Thread to send frames from the queue to the server
            def send_frames():
                frame_number = 0
                while not stop_event.is_set():
                    try:
                        frame, frame_timestamp = frame_queue.get(timeout=5)
                    except queue.Empty:
                        continue

                    while True:  # Keep trying to send the frame until successful
                        if reconnect_event.is_set():
                            print("Waiting for reconnection...")
                            time.sleep(1)
                            continue

                        try:
                            _, img_encoded = cv2.imencode(".jpg", frame)
                            img_bytes = img_encoded.tobytes()
                            img_base64 = base64.b64encode(img_bytes).decode("utf-8")

                            message = json.dumps(
                                {
                                    "token": token,
                                    "camera_id": camera_id,
                                    "image": img_base64,
                                    "return_processed_image": False,
                                    "subscriptionplan_id": 3,
                                    "threat_recognition_threshold": 0.15,
                                    "fps": model_fps,
                                    "frame_number": frame_number,
                                    "timestamp": frame_timestamp,
                                }
                            )
                            ws.send(message)
                            break  # Successfully sent the frame, move to next one
                        except Exception as e:
                            print(f"Error sending frame: {e}")
                            reconnect_event.set()
                            time.sleep(1)  # Wait before retry

                    # Store the frame with frame_number and timestamp
                    with sent_frames_lock:
                        sent_frames[frame_number] = (frame.copy(), frame_timestamp)

                    frame_number += 1
                    frame_queue.task_done()
                    time.sleep(frame_delay)  # Control frame rate

            def receive_responses():
                nonlocal ws
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)

                while not stop_event.is_set():
                    try:
                        if reconnect_event.is_set():
                            print("Connection lost, attempting to reconnect...")
                            ws.close()
                            new_ws = connect_websocket()
                            if new_ws:
                                ws = new_ws
                                reconnect_event.clear()
                                print("Successfully reconnected")
                            else:
                                time.sleep(retry_delay)
                                continue

                        response = ws.recv()
                        if response:
                            response_json = json.loads(response)
                            frame_number = response_json.get("frame_number")
                            print(f"Received response for frame {frame_number}")

                            with sent_frames_lock:
                                frame_data = sent_frames.pop(frame_number, None)
                                if frame_data is not None:
                                    frame, frame_timestamp = frame_data
                                    # Clean up older frames
                                    keys_to_delete = [
                                        key
                                        for key in sent_frames.keys()
                                        if key < frame_number
                                    ]
                                    for key in keys_to_delete:
                                        del sent_frames[key]

                                    if frame is not None:
                                        try:
                                            frame_base64 = base64.b64encode(
                                                cv2.imencode(".jpg", frame)[1]
                                            ).decode("utf-8")
                                            loop.run_until_complete(
                                                broadcast_frame(frame_base64)
                                            )
                                        except Exception as e:
                                            print(f"Error broadcasting frame: {e}")
                                            continue

                    except websocket.WebSocketConnectionClosedException:
                        print("WebSocket connection closed. Attempting to reconnect...")
                        reconnect_event.set()
                    except Exception as e:
                        print(f"Error receiving response: {e}")
                        reconnect_event.set()

                loop.close()

            # Start the threads
            capture_thread = threading.Thread(target=capture_frames)
            send_thread = threading.Thread(target=send_frames)
            receive_thread = threading.Thread(target=receive_responses)

            capture_thread.start()
            send_thread.start()
            receive_thread.start()

            # Wait for threads to finish
            capture_thread.join()
            send_thread.join()
            receive_thread.join()

            break

        except Exception as e:
            print(f"Error in main loop: {e}")
            retry_count += 1
            if retry_count < max_retries:
                print(
                    f"Retrying in {retry_delay} seconds... (Attempt {retry_count + 1}/{max_retries})"
                )
                time.sleep(retry_delay)
            else:
                print("Max retries reached. Stopping stream.")
                break

        finally:
            stop_event.set()
            try:
                if "cap" in locals():
                    cap.release()
                if "ws" in locals():
                    ws.close()
                cv2.destroyAllWindows()
            except Exception as e:
                print(f"Error during cleanup: {e}")


@app.post("/start_stream/{camera_id}")
async def start_stream(camera_id: int, token: str, video_path: str):
    if camera_id in stream_states and stream_states[camera_id].get("running", False):
        return {"message": f"Stream already running for camera {camera_id}"}

    stream_states[camera_id] = {"running": False}

    # Start streaming in a separate thread
    stream_thread = threading.Thread(
        target=stream_video_async,
        args=(token, camera_id, video_path),
    )
    stream_thread.start()

    return {"message": f"Started streaming for camera {camera_id}"}


@app.post("/stop_stream/{camera_id}")
async def stop_stream(camera_id: int):
    if camera_id not in stream_states:
        return {"message": f"No stream found for camera {camera_id}"}

    stream_states[camera_id]["running"] = False
    return {"message": f"Stopped streaming for camera {camera_id}"}


@app.get("/stream_status/{camera_id}")
async def get_stream_status(camera_id: int):
    if camera_id not in stream_states:
        return {"status": "No stream found"}

    return {"status": "running" if stream_states[camera_id]["running"] else "stopped"}


@app.on_event("startup")
async def startup_event():
    # Start streaming for camera 1 with default token and video path
    camera_id = 1
    token = login("test", "test")["access_token"]
    video_path = "rtsp://root:Admin1234@100.91.128.124/axis-media/media.amp"  # Replace with your default video path

    stream_states[camera_id] = {"running": False}
    stream_thread = threading.Thread(
        target=stream_video_async,
        args=(token, camera_id, video_path),
    )
    stream_thread.start()


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8001)
