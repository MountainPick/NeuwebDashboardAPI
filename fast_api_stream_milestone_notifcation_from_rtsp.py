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
import threading

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

# Model WebSocket URL for sending frames
# neuweb_IP = "52.63.219.76"
neuweb_IP = "api.neuwebtech.com"

port = 8000
camera_id = 1

# Variables to track frames and time
frame_count = 0
start_time = 0
last_sent_time = 0
SEND_INTERVAL = 0  # Adjust this value to control sending rate

# Global variables for WebSocket connections
ws_model = None
processed_frame = None

# Add these global variables
FRAME_SKIP = 2  # Process every 3rd frame
frame_counter = 0
last_processed_time = time.time()
PROCESS_INTERVAL = 0.1  # Minimum time between processing frames

# RTSP stream URL
# rtsp_url = "rtsp://tho:Ldtho1610@100.82.206.126/axis-media/media.amp"
rtsp_url = "rtsp://root:Admin1234@100.91.128.124/axis-media/media.amp"

# Global variables
latest_frame = None
frame_lock = threading.Lock()


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
# token = login_to_get_token(username, password, f"http://{neuweb_IP}:{port}")
token = login_to_get_token(username, password, f"https://{neuweb_IP}")

# Model WebSocket URL
# neuweb_ws_url = f"ws://{neuweb_IP}:{port}/ws/process-stream-image?token={token}"
neuweb_ws_url = f"wss://{neuweb_IP}/ws/process-stream-image?token={token}"


async def process_frame(frame):
    global ws_model, last_sent_time, processed_frame, frame_counter, last_processed_time
    current_time = time.time()

    frame_counter = (frame_counter + 1) % (FRAME_SKIP + 1)
    if frame_counter != 0:
        return  # Skip this frame

    if current_time - last_processed_time < PROCESS_INTERVAL:
        return  # Don't process frames too frequently

    last_processed_time = current_time

    try:
        # Resize the frame to reduce processing time
        frame = cv2.resize(frame, (640, 480))

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
                "subscriptionplan_id": 15,
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
    global latest_frame, ws_model, processed_frame
    logger.info("Starting stream processor")

    # websocket_url = f"ws://{neuweb_IP}:{port}/ws/process-stream-image?token={token}"
    websocket_url = f"wss://{neuweb_IP}/ws/process-stream-image?token={token}"

    try:
        ws_model = websocket.WebSocket()
        ws_model.connect(websocket_url)
        cap = cv2.VideoCapture(rtsp_url)
        if not cap.isOpened():
            logger.error("Error: Unable to open RTSP stream.")
            return

        # Function to capture frames continuously
        def capture_frames():
            nonlocal cap
            global latest_frame
            while cap.isOpened():
                ret, frame = cap.read()
                if not ret:
                    break
                # Resize to HD
                frame = cv2.resize(frame, (1280, 720))
                with frame_lock:
                    latest_frame = frame

        # Start the frame capture thread
        frame_thread = threading.Thread(target=capture_frames)
        frame_thread.daemon = True
        frame_thread.start()

        frame_number = 0
        latest_noti = None

        # Wait until we have at least one frame
        while True:
            with frame_lock:
                if latest_frame is not None:
                    break
            await asyncio.sleep(0.01)

        while True:
            with frame_lock:
                frame = latest_frame.copy()

            _, img_encoded = cv2.imencode(".jpg", frame)
            img_bytes = img_encoded.tobytes()
            img_base64 = base64.b64encode(img_bytes).decode("utf-8")
            message = json.dumps(
                {
                    "token": token,
                    "camera_id": camera_id,
                    "image": img_base64,
                    "return_processed_image": False,
                    "subscriptionplan_id": 15,
                    "threat_recognition_threshold": 0.05,
                }
            )
            # Capture the start time
            start_time = time.time()
            ws_model.send(message)
            response = ws_model.recv()
            # Capture the end time
            end_time = time.time()
            response_json = json.loads(response)
            logger.debug(response_json)
            logger.debug(f"Time taken: {end_time - start_time}")

            # Parse the response and add text overlay
            frame_results = response_json.get("frame_results", {})
            if len(frame_results.get("notification", [])) > 0:
                latest_noti = frame_results["notification"][0]["content"]
                noti_time = time.time()
                # Convert to datetime YY-MM-DD HH:MM:SS
                noti_time = datetime.fromtimestamp(noti_time).strftime(
                    "%H:%M:%S %d-%m-%Y"
                )
            if latest_noti is not None:
                # add text overlay
                cv2.putText(
                    frame,
                    f"Notification: {latest_noti}",
                    (10, 70),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    1,
                    (0, 0, 255),
                    2,
                )
                # add time overlay
                cv2.putText(
                    frame,
                    f"{noti_time}",
                    (10, 110),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    1,
                    (0, 0, 255),
                    2,
                )
            num_human_tracks = frame_results.get("num_human_tracks", 0)
            human_tracked_boxes = frame_results.get("human_tracked_boxes", [])
            object_tracked_boxes = frame_results.get("object_tracked_boxes", [])
            if object_tracked_boxes is not None:
                for obj in object_tracked_boxes:
                    track_box = obj.get("track_box")
                    track_id = obj.get("track_name", "N/A")
                    if track_box:
                        x1, y1, x2, y2 = map(int, track_box)
                        cv2.rectangle(frame, (x1, y1), (x2, y2), (0, 255, 0), 2)
                        label = f"{track_id}"
                        (label_width, label_height), _ = cv2.getTextSize(
                            label, cv2.FONT_HERSHEY_SIMPLEX, 0.5, 1
                        )
                        cv2.rectangle(
                            frame,
                            (x1, y1 - label_height - 5),
                            (x1 + label_width, y1),
                            (0, 255, 0),
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
            # Add bounding boxes and labels for tracked humans
            if human_tracked_boxes is not None:
                for human in human_tracked_boxes:
                    track_box = human.get("track_box")
                    track_id = human.get("track_name", "N/A")
                    if track_box:
                        x1, y1, x2, y2 = map(int, track_box)
                        cv2.rectangle(frame, (x1, y1), (x2, y2), (0, 0, 255), 2)
                        label = f"{track_id}"
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
            # Add total people count
            cv2.putText(
                frame,
                f"People: {num_human_tracks}",
                (10, 30),
                cv2.FONT_HERSHEY_SIMPLEX,
                1,
                (0, 255, 0),
                2,
            )
            print("frame number", frame_number)

            _, buffer = cv2.imencode(".jpg", frame)
            processed_frame = base64.b64encode(buffer).decode("utf-8")

            # Calculate the time difference in milliseconds
            duration_ms = (end_time - start_time) * 1000
            logger.debug(f"Time taken for frame {frame_number}: {duration_ms:.2f} ms")
            frame_number += 1

            await asyncio.sleep(0.01)  # Small delay to prevent blocking

    except Exception as e:
        logger.error(f"Error in stream processor: {e}", exc_info=True)
    finally:
        if cap.isOpened():
            cap.release()
        if ws_model:
            ws_model.close()


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
        "fast_api_stream_milestone_notifcation_from_rtsp:app",
        host="0.0.0.0",
        port=8000,
        ssl_keyfile="/home/ubuntu/certs/privkey.pem",  # Updated path
        ssl_certfile="/home/ubuntu/certs/fullchain.pem",  # Updated path
    )
