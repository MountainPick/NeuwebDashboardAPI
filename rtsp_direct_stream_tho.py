import asyncio
import numpy as np
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
import websockets

ALL_TIME_DANGEROUS_SIGNS = []
nest_asyncio.apply()
ws_IP = "54.252.71.145"
ws_IP = "3.25.94.113"
ws_IP = "13.211.1.88"
ws_IP = "172.22.22.7"
ws_IP = "100.88.214.115"
ws_IP = "0.0.0.0"
ws_IP = "3.107.175.200"
ws_IP = "3.106.230.96"
ws_IP = "13.236.116.43"
# ws_IP = "3.24.124.52"
API_BASE_URL = f"http://{ws_IP}:8000"
WEBSOCKET_BASE_URL = f"ws://{ws_IP}:8000/ws/process-stream-image"
WEBSOCKET_BASE_URL_V2 = f"ws://{ws_IP}:8000/ws/process-stream-image-v2"
WEBSOCKET_BASE_URL_ASYNC = f"ws://{ws_IP}:8000/ws/process-stream-image-async"


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


async def stream_video_v2(token, camera_id, video_path):
    """
    Connects to the WebSocket server, sends initial parameters, and receives processed images.
    Parameters:
        token (str): Authentication token.
        camera_id (str): Identifier for the camera.
        video_path (str): RTSP path to the video stream.
        websocket_url (str): URL of the WebSocket server endpoint.
    """
    # Construct the WebSocket URL with the token as a query parameter
    websocket_url_with_token = f"{WEBSOCKET_BASE_URL_V2}?token={token}"
    async with websockets.connect(websocket_url_with_token, max_size=None) as websocket:
        # Prepare the initial message with required parameters
        message = {
            "rtsp_path": video_path,
            "camera_id": camera_id,
            "return_processed_image": True,  # Set to True to receive processed images
            "subscriptionplan_id": 3,  # Optional: Set your subscription plan ID if applicable
            "threat_recognition_threshold": 0.15,
        }
        # Send the initial message as JSON
        await websocket.send(json.dumps(message))
        frame_count = 0
        try:
            while True:
                # Receive the response from the server
                response = await websocket.recv()
                data = json.loads(response)
                # Extract data from the response
                status = data.get("status")
                frame_number = data.get("frame_number")
                frame_results = data.get("frame_results")
                processed_frame_base64 = data.get("processed_frame")
                if processed_frame_base64:
                    frame_count += 1
                    print(
                        f"Received processed frame {frame_count} for frame {frame_number}"
                    )
                    # Decode the base64-encoded processed image
                    img_data = base64.b64decode(processed_frame_base64)
                    np_arr = np.frombuffer(img_data, np.uint8)
                    img = cv2.imdecode(np_arr, cv2.IMREAD_COLOR)
                    # Display the processed image
                    cv2.imshow("Processed Frame", img)
                    if cv2.waitKey(1) & 0xFF == ord("q"):
                        print("Exiting...")
                        break
                else:
                    # If no image is returned, you can process the results as needed
                    print(f"Received data for frame {frame_number}: {frame_results}")
        except websockets.exceptions.ConnectionClosed as e:
            print("WebSocket connection closed:", e)
        finally:
            cv2.destroyAllWindows()


if __name__ == "__main__":
    # User details for sign up and login
    USERNAME = "test"
    PASSWORD = "test"
    FIRSTNAME = "your_firstname"
    LASTNAME = "your_lastname"
    EMAIL = "your_email@example.com"
    PHONENUMBER = "your_phonenumber"
    EMERGENCYCONTACT = "your_emergencycontact"
    SUBSCRIPTIONPLAN_ID = 3  # Replace with appropriate subscription plan ID
    #
    # Login
    print("Logging in...")
    login_response = login(USERNAME, PASSWORD)
    print("Login response:", login_response)
    if "access_token" in login_response:
        TOKEN = login_response["access_token"]
        CAMERA_ID = 1  # Replace with your camera ID
        VIDEO_PATH = "/mnt/SSD2TB/code/neuweb/data/demo/gasstationfight.mp4"  # Replace with the path to your video file
        VIDEO_PATH = "/mnt/SSD2TB/code/neuweb/data/tunnel/1.mp4"  # Replace with the path to your video file
        # VIDEO_PATH = 'rtsp://tho:Ldtho1610@100.82.206.126/axis-media/media.amp'
        VIDEO_PATH = "rtsp://root:Admin1234@100.91.128.124/axis-media/media.amp"
        # VIDEO_PATH = 0  # Uncomment if you want to use the default camera
        # Stream video
        print("Streaming video...")
        use_async = False  # Set this to True if you want to use the asyncio method
        # Stream video synchronously
        print("Streaming video synchronously...")
        # stream_video_sync(TOKEN, CAMERA_ID, VIDEO_PATH)
        # stream_video_async(TOKEN, CAMERA_ID, VIDEO_PATH)
        # stream_video_noskip(TOKEN, CAMERA_ID, VIDEO_PATH, desired_fps=20)
        asyncio.get_event_loop().run_until_complete(
            stream_video_v2(TOKEN, CAMERA_ID, VIDEO_PATH)
        )
        # stream_video_queue(TOKEN, CAMERA_ID, VIDEO_PATH)
    else:
        print("Error: Could not obtain access token.")
