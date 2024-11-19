# app.py
import asyncio
import numpy as np
import base64
import json
import cv2
from fastapi import FastAPI, Response, WebSocket
from fastapi.responses import HTMLResponse
import requests
import nest_asyncio
import websockets

# Apply nest_asyncio to allow nested event loops
nest_asyncio.apply()

app = FastAPI()

# WebSocket and API configuration
ws_IP = "3.107.175.200"  # Replace with your WebSocket IP
ws_IP = "3.106.230.96"
ws_IP = "13.236.116.43"
API_BASE_URL = f"http://{ws_IP}:8000"
WEBSOCKET_BASE_URL_V2 = f"ws://{ws_IP}:8000/ws/process-stream-image-v2"


# Function to log in and get the access token
def login(username, password):
    login_url = f"{API_BASE_URL}/token"
    form_data = {"grant_type": "password", "username": username, "password": password}
    response = requests.post(login_url, data=form_data)
    return response.json()


# Function to stream video frames
async def stream_video_v2(token, camera_id, video_path):
    websocket_url_with_token = f"{WEBSOCKET_BASE_URL_V2}?token={token}"
    async with websockets.connect(websocket_url_with_token, max_size=None) as websocket:
        message = {
            "rtsp_path": video_path,
            "camera_id": camera_id,
            "return_processed_image": True,
            "subscriptionplan_id": 3,
            "threat_recognition_threshold": 0.15,
        }
        await websocket.send(json.dumps(message))
        while True:
            response = await websocket.recv()
            data = json.loads(response)
            processed_frame_base64 = data.get("processed_frame")
            if processed_frame_base64:
                img_data = base64.b64decode(processed_frame_base64)
                np_arr = np.frombuffer(img_data, np.uint8)
                img = cv2.imdecode(np_arr, cv2.IMREAD_COLOR)
                _, buffer = cv2.imencode(".jpg", img)
                yield buffer.tobytes()


# HTML page to display the video stream
@app.get("/", response_class=HTMLResponse)
async def get_html():
    return """
    <html>
        <head>
            <title>Video Stream</title>
            <style>
                body, html {
                    margin: 0;
                    padding: 0;
                    width: 100%;
                    height: 100%;
                    overflow: hidden;
                }
                #video {
                    width: 90vw;
                    height: 90vh;
                    object-fit: contain;
                    display: none;
                }
                #loginForm {
                    position: absolute;
                    top: 50%;
                    left: 50%;
                    transform: translate(-50%, -50%);
                    padding: 20px;
                    background: white;
                    border-radius: 5px;
                    box-shadow: 0 0 10px rgba(0,0,0,0.1);
                }
                .error {
                    color: red;
                    display: none;
                    margin-top: 10px;
                }
            </style>
        </head>
        <body>
            <div id="loginForm">
                <h2>Login Required</h2>
                <input type="text" id="username" placeholder="Username" /><br><br>
                <input type="password" id="password" placeholder="Password" /><br><br>
                <button onclick="authenticate()">Login</button>
                <p id="errorMsg" class="error">Invalid credentials</p>
            </div>
            <img id="video" src="">
            <script>
                function authenticate() {
                    const username = document.getElementById('username').value;
                    const password = document.getElementById('password').value;
                    
                    if (username === 'neuweb' && password === 'TechnologyKing#1') {
                        document.getElementById('loginForm').style.display = 'none';
                        document.getElementById('video').style.display = 'block';
                        initializeWebSocket();
                    } else {
                        document.getElementById('errorMsg').style.display = 'block';
                    }
                }

                function initializeWebSocket() {
                    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
                    const ws = new WebSocket(`${protocol}//${window.location.host}/ws/video_stream`);
                    ws.onmessage = function(event) {
                        document.getElementById('video').src = URL.createObjectURL(new Blob([event.data]));
                    };
                }
            </script>
        </body>
    </html>
    """


# WebSocket endpoint for continuous video streaming
@app.websocket("/ws/video_stream")
async def video_stream(websocket: WebSocket):
    await websocket.accept()
    USERNAME = "test"  # Replace with your username
    PASSWORD = "test"  # Replace with your password
    login_response = login(USERNAME, PASSWORD)

    if "access_token" in login_response:
        TOKEN = login_response["access_token"]
        CAMERA_ID = 1  # Replace with your camera ID
        VIDEO_PATH = "rtsp://root:Admin1234@100.91.128.124/axis-media/media.amp"  # Replace with your video source

        # Stream video frames
        async for frame in stream_video_v2(TOKEN, CAMERA_ID, VIDEO_PATH):
            await websocket.send_bytes(frame)  # Send frame to the WebSocket
    await websocket.close()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
