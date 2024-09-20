from fastapi import FastAPI, WebSocket, HTTPException
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


def init_db():
    # Drop the database file if it exists
    if os.path.exists("./notifications.db"):
        os.remove("./notifications.db")

    # Create all tables
    Base.metadata.create_all(bind=engine)


@app.on_event("startup")
async def startup_event():
    init_db()
    asyncio.create_task(
        send_frames_and_notifications()
    )  # Run the function independently


@app.options("/ws")
async def options_ws(request):
    return JSONResponse(status_code=200)


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    active_connections.append(websocket)
    try:
        while True:
            # Send the latest frame to the connected client
            if latest_frame:
                await websocket.send_text(json.dumps(latest_frame))
            await asyncio.sleep(1 / 30)  # Adjust frame rate if needed (e.g., 30 FPS)
    except:
        pass  # Catch all exceptions to prevent crashing
    finally:
        if websocket in active_connections:  # Check before removing
            active_connections.remove(websocket)


# Load video
video = cv2.VideoCapture("fire.mp4")
fps = video.get(cv2.CAP_PROP_FPS)
frame_time = 1 / fps


async def send_frames_and_notifications():
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

        # Send notification after 10 seconds
        if (
            current_time - start_time > 10
            and (current_time - start_time) % 10 < frame_time
        ):
            notification = {
                "type": "notification",
                "id": random.randint(1, 1000),
                "title": f"New Notification {random.randint(1, 100)}",
                "description": "This is a notification sent every 10 seconds.",
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
            logging.info(f"Sending notification: {notification}")

            # Send the notification to all active connections
            for connection in active_connections:
                try:
                    await connection.send_text(json.dumps(notification))
                except:
                    active_connections.remove(connection)

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

        await asyncio.sleep(frame_time)


@app.get("/notifications/{camera_id}/{frame_id}")
async def get_notification(camera_id: str, frame_id: int):
    db = SessionLocal()
    notification = (
        db.query(Notification)
        .filter(Notification.camera_id == camera_id, Notification.frame_id == frame_id)
        .first()
    )
    db.close()

    if notification is None:
        raise HTTPException(status_code=404, detail="Notification not found")

    return {
        "id": notification.id,
        "title": notification.title,
        "description": notification.description,
        "avatar": notification.avatar,
        "camera_id": notification.camera_id,
        "camera_name": notification.camera_name,
        "camera_location": notification.camera_location,
        "camera_model": notification.camera_model,
        "camera_status": notification.camera_status,
        "camera_status_color": notification.camera_status_color,
        "camera_last_maintenance": notification.camera_last_maintenance,
        "frame_id": notification.frame_id,
        "frame": base64.b64encode(notification.frame_data).decode("utf-8"),
        "timestamp": notification.timestamp,
    }


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
