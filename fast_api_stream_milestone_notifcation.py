from fastapi import FastAPI, WebSocket, HTTPException, Depends, Body, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
import asyncio
import json
import random
import cv2
import base64
import time
from sqlalchemy import (
    create_engine,
    Column,
    Integer,
    String,
    DateTime,
    UniqueConstraint,
    LargeBinary,
)
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from datetime import datetime
import os
import logging
from pydantic import BaseModel, EmailStr  # Import EmailStr for email validation
from typing import List
import websocket
import numpy as np
import requests

app = FastAPI()
logger = logging.getLogger(__name__)

# Update CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Allows all origins
    allow_credentials=True,
    allow_methods=["*"],  # Allows all methods
    allow_headers=["*"],  # Allows all headers
)

ws_url = "ws://52.86.92.233:8004/ws/live-stream"

# Model WebSocket URL for sending frames
neuweb_IP = "52.63.219.76"
port = 8000
camera_id = 1

# Variables to track frames and time
frame_count = 0
start_time = 0
last_sent_time = 0
SEND_INTERVAL = 0.2  # Adjust this value to control sending rate

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
                "subscriptionplan_id": 22,
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
        notification_data = frame_results.get("notification", [])
        print(notification_data)

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
                print(frame_count)
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


# Store active connections
active_connections: list[WebSocket] = []
latest_frame = None  # Global variable to store the latest frame

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
    password = Column(String)  # In a real application, use hashed passwords
    is_connected_milestone = Column(
        Integer, default=0
    )  # New field for milestone connection


class Notification(Base):
    __tablename__ = "notifications"

    id = Column(Integer, primary_key=True, index=True)
    title = Column(String)
    description = Column(String)
    avatar = Column(String)
    camera_id = Column(String)
    camera_name = Column(String)
    camera_location = Column(String)
    camera_model = Column(String)
    camera_status = Column(String)
    camera_status_color = Column(String)
    camera_last_maintenance = Column(String)
    frame_id = Column(Integer)
    frame_data = Column(LargeBinary)
    timestamp = Column(DateTime, default=datetime.utcnow)

    __table_args__ = (UniqueConstraint("camera_id", "frame_id", name="uix_1"),)


class NotificationResponse(BaseModel):
    id: int
    title: str
    description: str
    avatar: str
    camera_id: str
    camera_name: str
    camera_location: str
    camera_model: str
    camera_status: str
    camera_status_color: str
    camera_last_maintenance: str
    frame_id: int
    frame: str
    timestamp: datetime


class UserRequest(BaseModel):
    email: EmailStr  # Use EmailStr for email validation
    username: str
    password: str


class UserResponse(BaseModel):
    id: int
    email: EmailStr  # Use EmailStr for email validation
    username: str


class UserLoginRequest(BaseModel):
    username: str
    password: str


class MilestoneConnectRequest(BaseModel):
    username: str
    milestoneusername: str  # Added milestoneusername
    milestonepassword: str  # Added milestonepassword


def init_db():
    # Drop the database file if it exists
    if os.path.exists("./notifications.db"):
        os.remove("./notifications.db")

    # Create all tables
    Base.metadata.create_all(bind=engine)


@app.on_event("startup")
async def startup_event():
    init_db()
    # asyncio.create_task(send_frames())  # Run the function independently
    asyncio.create_task(stream_processor())


@app.options("/ws")
async def options_ws(request):
    return JSONResponse(status_code=200)


# @app.websocket("/ws")
# async def websocket_stream_endpoint(websocket: WebSocket):
#     await websocket.accept()
#     try:
#         while True:
#             # Send the latest frame to the connected client
#             if latest_frame:
#                 await websocket.send_text(json.dumps(latest_frame))
#             await asyncio.sleep(1 / 30)  # Adjust frame rate if needed (e.g., 30 FPS)
#     except:
#         pass  # Catch all exceptions to prevent crashing


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    logger.info("New WebSocket connection accepted")
    try:
        while True:
            if processed_frame:
                frame_data = {
                    "type": "frame",
                    "frame": processed_frame,
                }
                await websocket.send_text(json.dumps(frame_data))
            await asyncio.sleep(0.03)  # Adjust this value to control the frame rate
    except Exception as e:
        logger.error(f"Error in WebSocket endpoint: {e}")


@app.post("/signup", response_model=UserResponse)
async def signup(user: UserRequest = Body(...)):
    # Create a new database session
    db = SessionLocal()
    try:
        # Check if the user already exists
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
        }  # Return user_record
    finally:
        db.close()


@app.post("/connect_milestone")
async def connect_milestone(milestone_request: MilestoneConnectRequest = Body(...)):
    db = SessionLocal()
    try:
        user_record = (
            db.query(User).filter(User.username == milestone_request.username).first()
        )
        if (
            user_record
            and milestone_request.milestoneusername == "milestone"
            and milestone_request.milestonepassword == "milestone"
        ):
            user_record.is_connected_milestone = 1  # Set milestone connection
            db.commit()
            return {"message": "Milestone connected successfully"}
        elif not user_record:
            raise HTTPException(status_code=404, detail="User not found")
        else:
            raise HTTPException(status_code=400, detail="Invalid milestone credentials")
    finally:
        db.close()


@app.get("/users/{username}", response_model=UserResponse)
async def get_user_info(username: str):
    db = SessionLocal()
    try:
        user_record = db.query(User).filter(User.username == username).first()
        if user_record is None:
            raise HTTPException(status_code=404, detail="User not found")
        return UserResponse(
            id=user_record.id,
            email=user_record.email,
            username=user_record.username,
        )
    finally:
        db.close()


@app.get("/notifications", response_model=List[NotificationResponse])
async def list_notifications():
    db = SessionLocal()
    try:
        notifications = db.query(Notification).all()
        return [
            NotificationResponse(
                id=notification.id,
                title=notification.title,
                description=notification.description,
                avatar=notification.avatar,
                camera_id=notification.camera_id,
                camera_name=notification.camera_name,
                camera_location=notification.camera_location,
                camera_model=notification.camera_model,
                camera_status=notification.camera_status,
                camera_status_color=notification.camera_status_color,
                camera_last_maintenance=notification.camera_last_maintenance,
                frame_id=notification.frame_id,
                frame=base64.b64encode(notification.frame_data).decode("utf-8"),
                timestamp=notification.timestamp,
            )
            for notification in notifications
        ]
    except Exception as e:
        logging.error(f"Error retrieving notifications: {e}")
        raise HTTPException(status_code=500, detail="Internal Server Error")
    finally:
        db.close()


@app.get("/notifications/{camera_id}/{frame_id}", response_model=NotificationResponse)
async def get_notification(camera_id: str, frame_id: int):
    db = SessionLocal()
    try:
        notification = (
            db.query(Notification)
            .filter(
                Notification.camera_id == camera_id, Notification.frame_id == frame_id
            )
            .first()
        )

        if notification is None:
            raise HTTPException(status_code=404, detail="Notification not found")

        return NotificationResponse(
            id=notification.id,
            title=notification.title,
            description=notification.description,
            avatar=notification.avatar,
            camera_id=notification.camera_id,
            camera_name=notification.camera_name,
            camera_location=notification.camera_location,
            camera_model=notification.camera_model,
            camera_status=notification.camera_status,
            camera_status_color=notification.camera_status_color,
            camera_last_maintenance=notification.camera_last_maintenance,
            frame_id=notification.frame_id,
            frame=base64.b64encode(notification.frame_data).decode("utf-8"),
            timestamp=notification.timestamp,
        )
    except Exception as e:
        logging.error(f"Error retrieving notification: {e}")
        raise HTTPException(status_code=500, detail="Internal Server Error")
    finally:
        db.close()


# Load video
video = cv2.VideoCapture("fire.mp4")
fps = video.get(cv2.CAP_PROP_FPS)
frame_time = 1 / fps


async def send_frames():
    global latest_frame  # Use global variable to store the latest frame
    start_time = time.time()
    frame_count = 0
    last_frame_time = time.time()

    while True:
        current_time = time.time()
        elapsed_time = current_time - last_frame_time

        # Skip frames if processing is taking longer than real-time
        frames_to_skip = max(0, int(elapsed_time / frame_time) - 1)
        for _ in range(frames_to_skip):
            video.read()
            frame_count += 1

        success, frame = video.read()
        if not success:
            video.set(cv2.CAP_PROP_POS_FRAMES, 0)  # Loop back to the start
            frame_count = 0
            continue

        _, buffer = cv2.imencode(".jpg", frame)
        frame_data = base64.b64encode(buffer).decode("utf-8")

        # Update the global variable with the latest frame
        latest_frame = {
            "type": "frame",
            "frame": frame_data,
            "frame_id": frame_count,
        }

        frame_count += 1
        last_frame_time = current_time

        # Save notification to database after 15 seconds
        if current_time - start_time > 15:
            notification = {
                "type": "notification",
                "id": random.randint(1, 1000),
                "title": f"New Notification {random.randint(1, 100)}",
                "description": "This is a notification sent every 15 seconds.",
                "avatar": f"https://i.pravatar.cc/150?img={random.randint(1, 70)}",
                "frame": frame_data,
                "frame_id": frame_count,
                "camera": {
                    "id": "1",
                    "name": "Front Door Camera",
                    "location": "Entrance",
                    "model": "SecureCam Pro",
                    "status": "Online",
                    "statusColor": "success.main",
                    "lastMaintenance": "2023-05-15",
                },
            }
            # Print the notification to the output
            logging.info(f"Saving notification: {notification}")

            # Save notification to database
            db = SessionLocal()
            db_notification = Notification(
                title=notification["title"],
                description=notification["description"],
                avatar=notification["avatar"],
                camera_id=notification["camera"]["id"],
                camera_name=notification["camera"]["name"],
                camera_location=notification["camera"]["location"],
                camera_model=notification["camera"]["model"],
                camera_status=notification["camera"]["status"],
                camera_status_color=notification["camera"]["statusColor"],
                camera_last_maintenance=notification["camera"]["lastMaintenance"],
                frame_id=notification["frame_id"],
                frame_data=buffer.tobytes(),
            )
            db.add(db_notification)
            db.commit()
            db.close()
            start_time = current_time  # Reset start time after saving

        await asyncio.sleep(frame_time)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        app,
        host="0.0.0.0",
        port=8000,
        ws_ping_interval=30,  # Ping interval to keep the connection alive
        ws_ping_timeout=120,  # Timeout before closing an inactive connection
        ws_max_size=16777216,  # Increase max message size if needed
        log_level="info",
    )
