import base64
import datetime
import json
import time

import cv2
import nest_asyncio
import requests
import websocket
import threading

# Apply the nest_asyncio patch
nest_asyncio.apply()
# ws_IP = "54.252.71.145"
# # ws_IP = "172.22.22.7"
# # ws_IP = "0.0.0.0"
# # ws_IP = "3.24.124.52"

# API_BASE_URL = f"http://{ws_IP}:8000"
# WEBSOCKET_BASE_URL = f"ws://{ws_IP}:8000/ws/process-stream-image"

# ws_IP = "web-alb-1726954032.ap-southeast-2.elb.amazonaws.com"
# API_BASE_URL = f"http://{ws_IP}"
# WEBSOCKET_BASE_URL = f"ws://{ws_IP}/ws/process-stream-image"


ws_IP = "api.neuwebtech.com"
API_BASE_URL = f"https://{ws_IP}"
WEBSOCKET_BASE_URL = f"wss://{ws_IP}/ws/process-stream-image"


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
    # Global variables for multithreading
    global latest_frame
    latest_frame = None
    frame_lock = threading.Lock()

    websocket_url = f"{WEBSOCKET_BASE_URL}?token={token}"

    try:
        ws = websocket.WebSocket()
        ws.connect(websocket_url)
        cap = cv2.VideoCapture(video_path)
        if not cap.isOpened():
            print("Error: Could not open video file.")
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
            time.sleep(0.01)

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
                    "subscriptionplan_id": SUBSCRIPTIONPLAN_ID,
                    "threat_recognition_threshold": 0.05,
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
            print("Time taken: ", end_time - start_time)

            # Parse the response and add text overlay
            frame_results = response_json.get("frame_results", {})
            if len(frame_results.get("notification", [])) > 0:
                latest_noti = frame_results["notification"][0]["content"]
                noti_time = time.time()
                # Convert to datetime YY-MM-DD HH:MM:SS
                noti_time = datetime.datetime.fromtimestamp(noti_time).strftime(
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

            show_frame = frame
            cv2.imshow("Processed Image", show_frame)
            if cv2.waitKey(1) & 0xFF == ord("q"):
                break

            # Calculate the time difference in milliseconds
            duration_ms = (end_time - start_time) * 1000
            print(f"Time taken for frame {frame_number}: {duration_ms:.2f} ms")
            frame_number += 1

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
    SUBSCRIPTIONPLAN_ID = 18  # Replace with appropriate subscription plan ID
    #
    # Login
    print("Logging in...")
    login_response = login(USERNAME, PASSWORD)
    print("Login response:", login_response)
    if "access_token" in login_response:
        TOKEN = login_response["access_token"]
        CAMERA_ID = 1  # Replace with your camera ID
        # VIDEO_PATH = "fire.mp4"  # Replace with the path to your video file
        # VIDEO_PATH = "rtsp://tho:Ldtho1610@100.82.206.126/axis-media/media.amp"
        # VIDEO_PATH = 0  # Uncomment if you want to use the default camera
        VIDEO_PATH = "rtsp://root:Admin1234@100.91.128.124/axis-media/media.amp"
        # Stream video
        print("Streaming video...")
        use_async = False  # Set this to True if you want to use the asyncio method
        # Stream video synchronously
        print("Streaming video synchronously...")
        stream_video_sync(TOKEN, CAMERA_ID, VIDEO_PATH)
    else:
        print("Error: Could not obtain access token.")
