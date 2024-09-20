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

app = FastAPI()

# Update CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Allows all origins
    allow_credentials=True,
    allow_methods=["*"],  # Allows all methods
    allow_headers=["*"],  # Allows all headers
)

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


def init_db():
    # Drop the database file if it exists
    if os.path.exists("./notifications.db"):
        os.remove("./notifications.db")

    # Create all tables
    Base.metadata.create_all(bind=engine)


@app.on_event("startup")
async def startup_event():
    init_db()
    asyncio.create_task(send_frames())  # Run the function independently


@app.options("/ws")
async def options_ws(request):
    return JSONResponse(status_code=200)


@app.websocket("/ws")
async def websocket_stream_endpoint(websocket: WebSocket):
    await websocket.accept()
    try:
        while True:
            # Send the latest frame to the connected client
            if latest_frame:
                await websocket.send_text(json.dumps(latest_frame))
            await asyncio.sleep(1 / 30)  # Adjust frame rate if needed (e.g., 30 FPS)
    except:
        pass  # Catch all exceptions to prevent crashing


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
        return {"message": "Login successful", "user_id": user_record.id}
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
        "notification_api:app",
        host="0.0.0.0",
        port=8000,
        ws_ping_interval=30,  # Ping interval to keep the connection alive
        ws_ping_timeout=120,  # Timeout before closing an inactive connection
        ws_max_size=16777216,  # Increase max message size if needed
    )
