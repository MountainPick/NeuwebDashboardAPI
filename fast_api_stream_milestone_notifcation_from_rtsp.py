import base64
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
from fastapi import Body, HTTPException
import logging
from sqlalchemy import (
    create_engine,
    Column,
    Integer,
    String,
    DateTime,
    LargeBinary,
    UniqueConstraint,
)
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from pydantic import BaseModel, EmailStr
from typing import List
import os
from datetime import datetime


logger = logging.getLogger(__name__)

app = FastAPI()

ALL_TIME_DANGEROUS_SIGNS = []
nest_asyncio.apply()

# neuweb_IP = "api.neuwebtech.com"
ws_IP = "api.neuwebtech.com"
API_BASE_URL = f"https://{ws_IP}"
WEBSOCKET_BASE_URL = f"wss://{ws_IP}/ws/process-stream-image"
WEBSOCKET_BASE_URL_ASYNC = f"wss://{ws_IP}/ws/process-stream-image-async"
# ws_IP = "54.252.71.145"
# # ws_IP = "172.22.22.7"
# # ws_IP = "0.0.0.0"
# # ws_IP = "3.24.124.52"
# API_BASE_URL = f"http://{ws_IP}:8000"
# WEBSOCKET_BASE_URL = f"ws://{ws_IP}:8000/ws/process-stream-image"
# WEBSOCKET_BASE_URL_ASYNC = f"ws://{ws_IP}:8000/ws/process-stream-image-async"

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
active_connections: set[WebSocket] = set()
latest_frame = None  # Global variable to store the latest frame


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
                # Keep the connection alive with a ping/pong mechanism
                await websocket.send_json({"type": "ping"})

                # If there's a latest frame available, send it
                if latest_frame is not None:
                    await websocket.send_json({"type": "frame", "frame": latest_frame})

                # Small delay to prevent overwhelming the connection
                await asyncio.sleep(0.01)  # Adjust this value based on your needs

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
    global latest_frame
    latest_frame = frame_data  # Store the latest frame

    disconnected = set()
    for connection in active_connections:
        try:
            await connection.send_json({"type": "frame", "frame": frame_data})
        except Exception as e:
            print(f"Error sending frame: {e}")
            disconnected.add(connection)

    # Remove disconnected clients
    for connection in disconnected:
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


def login_to_get_token(username, password):
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
                            time.sleep(0.1)  # Wait before retry

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
                                            # Visualize the frame with detection results
                                            processed_frame = visualize_frame(
                                                frame.copy(),
                                                response_json.get("frame_results"),
                                            )

                                            # Convert the processed frame to base64
                                            # frame_base64 = base64.b64encode(
                                            #     cv2.imencode(".jpg", processed_frame)[1]
                                            # ).decode("utf-8")

                                            loop.run_until_complete(
                                                broadcast_frame(processed_frame)
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


def visualize_frame(frame, frame_results):
    """Process the response and add visualizations to the frame"""
    if frame_results is None:
        return frame

    # Get detection results
    num_human_tracks = frame_results.get("num_human_tracks", 0)
    human_tracked_boxes = frame_results.get("human_tracked_boxes", [])
    object_tracked_boxes = frame_results.get("object_tracked_boxes", [])
    vehicle_count = frame_results.get("vehicle_count", [])
    dangerous_signs = frame_results.get("dangerous_signs", [])
    notification_data = frame_results.get("notification", [])

    # Add notification if available
    if len(notification_data) > 0:
        latest_noti = notification_data[0]["content"]
        noti_time = datetime.now().strftime("%H:%M:%S %d-%m-%Y")
        cv2.putText(
            frame,
            f"Notification: {latest_noti}",
            (10, 70),
            cv2.FONT_HERSHEY_SIMPLEX,
            1,
            (0, 0, 255),
            2,
        )
        cv2.putText(
            frame,
            f"{noti_time}",
            (10, 110),
            cv2.FONT_HERSHEY_SIMPLEX,
            1,
            (0, 0, 255),
            2,
        )

    # Draw vehicle counts
    if vehicle_count:
        cur_y = 0
        for idx, (cls, count) in enumerate(vehicle_count):
            cv2.putText(
                frame,
                f"{cls}: {count}",
                (20, 40 + 40 * idx),
                cv2.FONT_HERSHEY_COMPLEX,
                1,
                (0, 255, 0),
                3,
            )
            cur_y = 80 + 80 * idx

    # Draw object boxes
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

    # Draw human boxes
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
        f"People: {num_human_tracks}" if num_human_tracks > 0 else "",
        (10, 30),
        cv2.FONT_HERSHEY_SIMPLEX,
        1,
        (0, 255, 0),
        2,
    )

    # Encode the processed frame
    _, buffer = cv2.imencode(".jpg", frame)
    processed_frame = base64.b64encode(buffer).decode("utf-8")

    return processed_frame


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


@app.post("/update_detection_mode")
async def update_detection_mode(data: dict = Body(...)):
    global SUBSCRIPTION_ID
    try:
        camera_id = data.get("camera_id")
        subscription_id = data.get("subscription_id")

        if not camera_id or subscription_id is None:
            raise HTTPException(status_code=400, detail="Missing required fields")

        # Update the global subscription ID
        SUBSCRIPTION_ID = subscription_id

        logger.info(
            f"Updated detection mode for camera {camera_id} to subscription ID {subscription_id}"
        )
        return {
            "status": "success",
            "message": f"Detection mode updated for camera {camera_id}",
        }

    except Exception as e:
        logger.error(f"Error updating detection mode: {e}")
        raise HTTPException(status_code=500, detail=str(e))


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
    # if os.path.exists("./notifications.db"):
    #     os.remove("./notifications.db")

    # Create all tables
    Base.metadata.create_all(bind=engine)


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


@app.on_event("startup")
async def startup_event():
    # Start streaming for camera 1 with default token and video path
    camera_id = 1
    token = login_to_get_token("test", "test")["access_token"]
    video_path = "rtsp://root:Admin1234@100.91.128.124/axis-media/media.amp"  # Replace with your default video path

    stream_states[camera_id] = {"running": False}
    stream_thread = threading.Thread(
        target=stream_video_async,
        args=(token, camera_id, video_path),
    )
    stream_thread.start()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "fast_api_stream_milestone_notifcation_from_rtsp:app",
        host="0.0.0.0",
        port=8000,
        ssl_keyfile="/home/ubuntu/certs/privkey.pem",  # Updated path
        ssl_certfile="/home/ubuntu/certs/fullchain.pem",  # Updated path
    )
