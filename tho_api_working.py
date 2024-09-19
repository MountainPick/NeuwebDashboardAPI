import requests
import json
import base64
import cv2
import asyncio
import websockets
import websocket
import nest_asyncio
import numpy as np
import time

# Apply the nest_asyncio patch
nest_asyncio.apply()
API_BASE_URL = "http://3.25.106.181:8000"
WEBSOCKET_BASE_URL = "ws://3.25.106.181:8000/ws/process-stream-image"


# 1. Sign Up
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


# 2. Login
def login(username, password):
    login_url = f"{API_BASE_URL}/token"
    form_data = {"grant_type": "password", "username": username, "password": password}
    response = requests.post(login_url, data=form_data)
    return response.json()


# Synchronous function to stream video
def stream_video_sync(token, camera_id, video_path):
    websocket_url = f"{WEBSOCKET_BASE_URL}?token={token}"
    try:
        ws = websocket.WebSocket()
        ws.connect(websocket_url)
        cap = cv2.VideoCapture(video_path)
        if not cap.isOpened():
            print("Error: Could not open video file.")
            return
        frame_number = 0
        fps = cap.get(cv2.CAP_PROP_FPS)
        frame_time = 1 / fps
        last_frame_time = time.time()
        while cap.isOpened():
            current_time = time.time()
            elapsed_time = current_time - last_frame_time
            ret, frame = cap.read()
            if not ret:
                break
            _, img_encoded = cv2.imencode(".jpg", frame)
            img_bytes = img_encoded.tobytes()
            img_base64 = base64.b64encode(img_bytes).decode("utf-8")
            message = json.dumps(
                {
                    "token": token,
                    "camera_id": camera_id,
                    "image": img_base64,
                    "return_processed_image": False,  # Changed to True
                    "subscriptionplan_id": 22,
                }
            )
            # Capture the start time
            start_time = time.time()
            ws.send(message)
            response = ws.recv()
            # Capture the end time
            end_time = time.time()
            response_json = json.loads(response)
            print(response_json)
            # Parse the response and add text overlay
            frame_results = response_json.get("frame_results", {})
            if len(frame_results["notification"]) > 0:
                print(frame_results["notification"])
                exit()
            num_human_tracks = frame_results.get("num_human_tracks", 0)
            human_tracked_boxes = frame_results.get("human_tracked_boxes", [])
            # Add bounding boxes and labels for tracked humans
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
            # Display the processed image
            cv2.imshow("Processed Image", frame)
            if cv2.waitKey(1) & 0xFF == ord("q"):
                break
            # Calculate the time difference in milliseconds
            duration_ms = (end_time - start_time) * 1000
            print(f"Time taken for frame {frame_number}: {duration_ms:.2f} ms")
            frame_number += 1
            last_frame_time = current_time
        cap.release()
        ws.close()
        cv2.destroyAllWindows()
    except Exception as e:
        print(f"Error connecting to WebSocket: {e}")


if __name__ == "__main__":
    # User details for sign up and login
    USERNAME = "test"
    PASSWORD = "test"
    FIRSTNAME = "your_firstname"
    LASTNAME = "your_lastname"
    EMAIL = "your_email@example.com"
    PHONENUMBER = "your_phonenumber"
    EMERGENCYCONTACT = "your_emergencycontact"
    SUBSCRIPTIONPLAN_ID = 22  # Replace with appropriate subscription plan ID
    # Login
    print("Logging in...")
    login_response = login(USERNAME, PASSWORD)
    print("Login response:", login_response)
    if "access_token" in login_response:
        TOKEN = login_response["access_token"]
        CAMERA_ID = 1  # Replace with your camera ID
        VIDEO_PATH = "ausambulance.mp4"  # Replace with the path to your video file
        # Stream video
        print("Streaming video...")
        use_async = False  # Set this to True if you want to use the asyncio method
        # Stream video synchronously
        print("Streaming video synchronously...")
        stream_video_sync(TOKEN, CAMERA_ID, VIDEO_PATH)
    else:
        print("Error: Could not obtain access token.")
