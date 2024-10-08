import base64
import json
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, Body
from fastapi.middleware.cors import CORSMiddleware
import logging
from typing import Dict
from sqlalchemy import create_engine, Column, Integer, String
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
import logging
from pydantic import BaseModel, EmailStr
import os
import requests
from requests_ntlm import HttpNtlmAuth
import cv2
import numpy as np
import asyncio
import time
from concurrent.futures import ThreadPoolExecutor
from collections import deque
import websockets
from pydantic import BaseModel

app = FastAPI()

# Enable CORS for all origins
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Allows all origins
    allow_credentials=True,
    allow_methods=["*"],  # Allows all methods
    allow_headers=["*"],  # Allows all headers
)

asyncio.set_event_loop_policy(asyncio.DefaultEventLoopPolicy())

# Logger setup
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("fastapi-app")

# Store active connections for frame sources
active_connections = []

# Store active connections for frontend clients
frontend_connections: Dict[str, list] = {}


# Database setup
DATABASE_URL = "sqlite:///./notifications.db"
engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    email = Column(String, unique=True, index=True)
    username = Column(String, unique=True, index=True)
    password = Column(String)
    is_connected_milestone = Column(Integer, default=0)
    milestone_username = Column(String)
    milestone_password = Column(String)


class UserRequest(BaseModel):
    email: EmailStr
    username: str
    password: str


class UserResponse(BaseModel):
    id: int
    email: EmailStr
    username: str


class UserLoginRequest(BaseModel):
    username: str
    password: str


class MilestoneConnectRequest(BaseModel):
    username: str
    milestoneusername: str
    milestonepassword: str


class CameraIdsRequest(BaseModel):
    camera_id_1: str
    camera_id_2: str


def init_db():
    if os.path.exists("./notifications.db"):
        os.remove("./notifications.db")
    Base.metadata.create_all(bind=engine)


@app.on_event("startup")
async def startup_event():
    init_db()
    asyncio.create_task(continuous_frame_processing())


@app.post("/signup", response_model=UserResponse)
async def signup(user: UserRequest = Body(...)):
    db = SessionLocal()
    try:
        existing_user = db.query(User).filter(User.email == user.email).first()
        if existing_user:
            raise HTTPException(status_code=400, detail="Email already registered")

        db_user = User(email=user.email, username=user.username, password=user.password)
        db.add(db_user)
        db.commit()
        db.refresh(db_user)
        return UserResponse(
            id=db_user.id, email=db_user.email, username=db_user.username
        )
    finally:
        db.close()


@app.post("/login")
async def login(user: UserLoginRequest = Body(...)):
    db = SessionLocal()
    try:
        user_record = db.query(User).filter(User.username == user.username).first()
        if user_record is None or user_record.password != user.password:
            raise HTTPException(status_code=400, detail="Invalid credentials")
        return {
            "message": "Login successful",
            "user_id": user_record.id,
            "user_record": user_record,
        }
    finally:
        db.close()


@app.post("/connect_milestone")
async def connect_milestone(milestone_request: MilestoneConnectRequest = Body(...)):
    db = SessionLocal()
    try:
        user_record = (
            db.query(User).filter(User.username == milestone_request.username).first()
        )
        if not user_record:
            raise HTTPException(status_code=404, detail="User not found")

        # Check Milestone connection using the provided credentials
        milestone_connected = check_milestone_connection(
            milestone_request.milestoneusername, milestone_request.milestonepassword
        )

        if milestone_connected:
            user_record.is_connected_milestone = 1
            user_record.milestone_username = milestone_request.milestoneusername
            user_record.milestone_password = milestone_request.milestonepassword
            db.commit()
            return {"message": "Milestone connected successfully"}
        else:
            raise HTTPException(
                status_code=400, detail="Unable to connect to Milestone"
            )
    finally:
        db.close()


def check_milestone_connection(username: str, password: str) -> bool:
    SERVER_URL = "http://52.86.92.233"
    VERIFY_CERTIFICATES = True

    session = requests.Session()
    try:
        response = session.request(
            "POST",
            f"{SERVER_URL}/IDP/connect/token",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            data="grant_type=windows_credentials&client_id=GrantValidatorClient",
            verify=VERIFY_CERTIFICATES,
            auth=HttpNtlmAuth(username, password),
        )
        return response.status_code == 200
    except Exception as e:
        print(f"Error connecting to Milestone: {e}")
        return False


@app.get("/user_cameras/{username}")
async def get_user_cameras(username: str):
    db = SessionLocal()
    try:
        user_record = db.query(User).filter(User.username == username).first()
        if not user_record:
            raise HTTPException(status_code=404, detail="User not found")

        if user_record.is_connected_milestone != 1:
            raise HTTPException(
                status_code=400, detail="User not connected to Milestone"
            )

        # Fetch cameras from Milestone
        cameras = fetch_milestone_cameras()
        return {"cameras": cameras}
    finally:
        db.close()


def fetch_milestone_cameras():
    SERVER_URL = "http://52.86.92.233"
    VERIFY_CERTIFICATES = True
    USERNAME = "Administrator"
    PASSWORD = "NeuwebAdmin@1"

    session = requests.Session()
    try:
        # Get token
        token_response = session.request(
            "POST",
            f"{SERVER_URL}/IDP/connect/token",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            data="grant_type=windows_credentials&client_id=GrantValidatorClient",
            verify=VERIFY_CERTIFICATES,
            auth=HttpNtlmAuth(USERNAME, PASSWORD),
        )
        token = token_response.json()["access_token"]

        # Get cameras
        cameras_response = session.request(
            "GET",
            f"{SERVER_URL}/api/rest/v1/cameras",
            headers={"Authorization": f"Bearer {token}"},
            verify=VERIFY_CERTIFICATES,
        )
        return cameras_response.json().get("array", [])
    except Exception as e:
        print(f"Error fetching cameras from Milestone: {e}")
        return []


@app.websocket("/ws/live-stream/{camera_id}")
async def websocket_endpoint(websocket: WebSocket, camera_id: str):
    await websocket.accept()
    logger.info(f"WebSocket connection accepted for camera {camera_id}")
    active_connections.append(websocket)

    try:
        while True:
            # Receive JSON message
            message = await websocket.receive_text()
            data = json.loads(message)

            # Extract the base64 image and camera id
            image_base64 = data.get("image")
            if image_base64:
                logger.info(f"Received frame from camera {camera_id}")

                # Decode base64 image for further processing
                image_data = base64.b64decode(image_base64)

                # Log the received frame size
                logger.info(
                    f"Frame size from camera {camera_id}: {len(image_data)} bytes"
                )

                # Broadcast the frame to frontend clients
                await broadcast_to_frontend(camera_id, image_base64)

    except WebSocketDisconnect:
        logger.info(f"WebSocket connection closed for camera {camera_id}")
        active_connections.remove(websocket)
    except Exception as e:
        logger.error(f"Error in WebSocket connection for camera {camera_id}: {e}")
        await websocket.close()


# @app.websocket("/ws/frontend/{camera_id}")
# async def frontend_websocket(websocket: WebSocket, camera_id: str):
#     await websocket.accept()
#     logger.info(f"Frontend WebSocket connection accepted for camera {camera_id}")

#     if camera_id not in frontend_connections:
#         frontend_connections[camera_id] = []
#     frontend_connections[camera_id].append(websocket)

#     try:
#         while True:
#             # Keep the connection alive
#             await websocket.receive_text()
#     except WebSocketDisconnect:
#         logger.info(f"Frontend WebSocket connection closed for camera {camera_id}")
#         frontend_connections[camera_id].remove(websocket)
#         if not frontend_connections[camera_id]:
#             del frontend_connections[camera_id]
#     except Exception as e:
#         logger.error(
#             f"Error in frontend WebSocket connection for camera {camera_id}: {e}"
#         )
#         await websocket.close()


@app.websocket("/ws/frontend/{camera_id}")
async def frontend_websocket(websocket: WebSocket, camera_id: str):
    await websocket.accept()
    logger.info(f"Frontend WebSocket connection accepted for camera {camera_id}")

    if camera_id not in frontend_connections:
        frontend_connections[camera_id] = []
    frontend_connections[camera_id].append(websocket)

    try:
        while True:
            # Receive frame from the camera stream
            data = await websocket.receive_json()
            frame = data.get("image")

            if frame:
                # Send the frame to the frontend
                await websocket.send_json({"camera_id": camera_id, "image": frame})
    except WebSocketDisconnect:
        logger.info(f"Frontend WebSocket connection closed for camera {camera_id}")
        frontend_connections[camera_id].remove(websocket)
        if not frontend_connections[camera_id]:
            del frontend_connections[camera_id]
    except Exception as e:
        logger.error(
            f"Error in frontend WebSocket connection for camera {camera_id}: {e}"
        )
        await websocket.close()


# Add these global variables at the top of your file
neuweb_IP = "52.63.219.76"
port = 8000
camera_id = 1
username = "test"
password = "test"
SEND_INTERVAL = 0.2  # Adjust this value as needed

# Initialize these variables
ws_model = None
last_sent_time = 0

# Create a ThreadPoolExecutor
executor = ThreadPoolExecutor(max_workers=4)  # Adjust the number of workers as needed

# Add these global variables
last_processed_frame = None
last_processed_time = 0
PROCESS_INTERVAL = 0.2  # Process every 0.5 seconds


# Add this function to get the NeuWeb token
def login_to_get_token(username, password, url):
    """Log in to the server and get the authentication token"""
    logger.info(f"Attempting to login and get token from {url}")
    response = requests.post(
        f"{url}/token", data={"username": username, "password": password}
    )
    response_data = response.json()
    logger.info("Successfully obtained token")
    return response_data["access_token"]


# Get the token
token = login_to_get_token(username, password, f"http://{neuweb_IP}:{port}")

# Set up the NeuWeb WebSocket URL
neuweb_ws_url = f"ws://{neuweb_IP}:{port}/ws/process-stream-image?token={token}"


# Update these global variables
latest_frame_1 = None
latest_frame_2 = None
latest_processed_frame_1 = None
latest_processed_frame_2 = None
processing_queue = asyncio.Queue(
    maxsize=2
)  # Limit queue size to 2 (one for each camera)
processing_lock = asyncio.Lock()


# Add these new global variables
neuweb_IP2 = "3.25.215.111"
camera_id_2 = "7b3d39e9-67c7-418f-8f1c-31ff16cdf50f"

# Global variables for WebSocket connections
ws_connection_1 = None
ws_connection_2 = None

# Set up the NeuWeb WebSocket URLs for both cameras
neuweb_ws_url_1 = f"ws://{neuweb_IP}:{port}/ws/process-stream-image?token={token}"
neuweb_ws_url_2 = f"ws://{neuweb_IP2}:{port}/ws/process-stream-image?token={token}"

# Add this new global variable
user_camera_ids = {
    "camera_id_1": "2eb15471-cf55-41aa-b879-c12bd9c6ea6f",
    "camera_id_2": "7b3d39e9-67c7-418f-8f1c-31ff16cdf50f",
}


@app.post("/set_camera_ids")
async def set_camera_ids(camera_ids: CameraIdsRequest):
    global user_camera_ids
    user_camera_ids["camera_id_1"] = camera_ids.camera_id_1
    user_camera_ids["camera_id_2"] = camera_ids.camera_id_2
    return {"message": "Camera IDs set successfully", "camera_ids": user_camera_ids}


async def ensure_websocket_connection(ws_url):
    max_retries = 5
    retry_delay = 5  # seconds

    for attempt in range(max_retries):
        try:
            return await websockets.connect(ws_url)
        except Exception as e:
            logger.error(f"Failed to connect to WebSocket (attempt {attempt + 1}): {e}")
            if attempt < max_retries - 1:
                await asyncio.sleep(retry_delay)
            else:
                raise


async def process_frame_through_neuweb(frame, camera_id, camera_id_int):
    global ws_connection_1, ws_connection_2

    try:
        if camera_id == user_camera_ids["camera_id_1"]:
            if ws_connection_1 is None or ws_connection_1.closed:
                ws_connection_1 = await ensure_websocket_connection(neuweb_ws_url_1)
            ws_connection = ws_connection_1
        elif camera_id == user_camera_ids["camera_id_2"]:
            if ws_connection_2 is None or ws_connection_2.closed:
                ws_connection_2 = await ensure_websocket_connection(neuweb_ws_url_2)
            ws_connection = ws_connection_2
        else:
            raise ValueError(f"Invalid camera_id: {camera_id}")

        if isinstance(frame, str):
            img_bytes = base64.b64decode(frame)
            nparr = np.frombuffer(img_bytes, np.uint8)
            frame = cv2.imdecode(nparr, cv2.IMREAD_COLOR)

        encode_param = [int(cv2.IMWRITE_JPEG_QUALITY), 50]
        _, img_encoded = cv2.imencode(".jpg", frame, encode_param)
        img_bytes = img_encoded.tobytes()
        frame_base64 = base64.b64encode(img_bytes).decode("utf-8")

        message = json.dumps(
            {
                "camera_id": camera_id_int,
                "image": frame_base64,
                "token": token,
                "return_processed_image": False,
                "subscriptionplan_id": 22,
            }
        )

        await ws_connection.send(message)
        response = await ws_connection.recv()

        if not response:
            logger.warning(
                f"Received empty response from NeuWeb for camera {camera_id}"
            )
            return frame_base64

        response_data = json.loads(response)
        frame_results = response_data.get("frame_results", {})
        num_human_tracks = frame_results.get("num_human_tracks", 0)
        human_tracked_boxes = frame_results.get("human_tracked_boxes", [])
        notification_data = frame_results.get("notification", [])
        print(f"Notification data for camera {camera_id}:", notification_data)

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
        return processed_frame

    except websockets.exceptions.ConnectionClosed:
        logger.error(
            f"WebSocket connection closed for camera {camera_id}. Reconnecting..."
        )
        if camera_id == user_camera_ids["camera_id_1"]:
            ws_connection_1 = None
        else:
            ws_connection_2 = None
        return frame_base64
    except Exception as e:
        logger.error(
            f"Error processing frame through NeuWeb for camera {camera_id}: {e}"
        )
        return frame_base64


# Update the continuous_frame_processing function
async def continuous_frame_processing():
    global latest_processed_frame_1, latest_processed_frame_2
    while True:
        try:
            frame, camera_id = await asyncio.wait_for(
                processing_queue.get(), timeout=0.1
            )
            if camera_id == user_camera_ids["camera_id_1"]:
                processed_frame = await process_frame_through_neuweb(
                    frame, camera_id, 1
                )
                async with processing_lock:
                    latest_processed_frame_1 = processed_frame
            elif camera_id == user_camera_ids["camera_id_2"]:
                processed_frame = await process_frame_through_neuweb(
                    frame, camera_id, 2
                )
                async with processing_lock:
                    latest_processed_frame_2 = processed_frame
            processing_queue.task_done()
        except asyncio.TimeoutError:
            await asyncio.sleep(0.01)  # Short sleep to prevent busy waiting
        except Exception as e:
            logger.error(f"Error in continuous_frame_processing: {e}")
            await asyncio.sleep(0.1)  # Add a short delay before retrying


# Update the broadcast_to_frontend function
async def broadcast_to_frontend(camera_id: str, frame: str):
    global \
        latest_frame_1, \
        latest_frame_2, \
        latest_processed_frame_1, \
        latest_processed_frame_2

    if camera_id == user_camera_ids["camera_id_1"]:
        latest_frame_1 = frame
        if processing_queue.full():
            try:
                processing_queue.get_nowait()
            except asyncio.QueueEmpty:
                pass
        await processing_queue.put((frame, camera_id))
        async with processing_lock:
            frame_to_send = latest_processed_frame_1 or frame
    elif camera_id == user_camera_ids["camera_id_2"]:
        latest_frame_2 = frame
        if processing_queue.full():
            try:
                processing_queue.get_nowait()
            except asyncio.QueueEmpty:
                pass
        await processing_queue.put((frame, camera_id))
        async with processing_lock:
            frame_to_send = latest_processed_frame_2 or frame
    else:
        frame_to_send = frame

    if camera_id in frontend_connections:
        active_connections = []
        for connection in frontend_connections[camera_id]:
            try:
                await connection.send_text(
                    json.dumps({"image": frame_to_send, "camera_id": camera_id})
                )
                active_connections.append(connection)
            except WebSocketDisconnect:
                logger.info(f"Frontend WebSocket disconnected for camera {camera_id}")

        frontend_connections[camera_id] = active_connections

        if not frontend_connections[camera_id]:
            del frontend_connections[camera_id]


@app.on_event("shutdown")
async def shutdown_event():
    global ws_connection_1, ws_connection_2
    if ws_connection_1:
        await ws_connection_1.close()
    if ws_connection_2:
        await ws_connection_2.close()


# Add this new endpoint to get the current user_camera_ids
@app.get("/get_camera_ids")
async def get_camera_ids():
    return {"camera_ids": user_camera_ids}


# Run the app
if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000, log_level="info")
