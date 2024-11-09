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

ALL_TIME_DANGEROUS_SIGNS = []
nest_asyncio.apply()
ws_IP = "api.neuwebtech.com"
API_BASE_URL = f"https://{ws_IP}"
WEBSOCKET_BASE_URL = f"wss://{ws_IP}/ws/process-stream-image"
WEBSOCKET_BASE_URL_ASYNC = f"wss://{ws_IP}/ws/process-stream-image-async"


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


def stream_video_queue(token, camera_id, video_path):
    frame_queue = queue.Queue(maxsize=10)
    frame_lock = threading.Lock()
    all_time_dangerous_signs = []
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
            while cap.isOpened():
                ret, frame = cap.read()
                if not ret:
                    break
                # Resize to HD
                frame = cv2.resize(frame, (1280, 720))
                with frame_lock:
                    try:
                        # Try to put the frame in the queue
                        frame_queue.put(frame, block=False)
                    except queue.Full:
                        # If the queue is full, remove the oldest frame and put the new one
                        try:
                            frame_queue.get_nowait()
                        except queue.Empty:
                            pass
                        frame_queue.put(frame, block=False)
                time.sleep(0.01)  # Optional sleep to control frame rate

        # Start the frame capture thread
        frame_thread = threading.Thread(target=capture_frames)
        frame_thread.daemon = True
        frame_thread.start()

        frame_number = 0
        latest_noti = None

        # Wait until we have at least one frame
        while frame_queue.empty():
            time.sleep(0.01)

        while True:
            with frame_lock:
                try:
                    frame = frame_queue.get(block=False)
                except queue.Empty:
                    # No new frame, sleep and continue
                    time.sleep(0.01)
                    continue

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
                    "threat_recognition_threshold": 0.15,
                    "fps": 10,
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
            vehicle_count = frame_results.get(
                "vehicle_count", []
            )  # [[class, count], ...]
            dangerous_signs = frame_results.get(
                "dangerous_signs", []
            )  # [[datetime, [signs]], ...]
            if len(vehicle_count) > 0:
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
                # plot the dangerous signs count
                for idx, (datetime_str, signs) in enumerate(dangerous_signs):
                    all_time_dangerous_signs.append((datetime_str, signs))
                for idx, (datetime_str, signs) in enumerate(all_time_dangerous_signs):
                    cv2.putText(
                        frame,
                        f"{datetime_str}: {'+'.join(signs)}",
                        (20, cur_y + 40 + 40 * idx),
                        cv2.FONT_HERSHEY_COMPLEX,
                        1,
                        (0, 0, 255),
                        3,
                    )

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
                f"People: {num_human_tracks}" if num_human_tracks > 0 else "",
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


def stream_video_async(token, camera_id, video_path):
    websocket_url = f"{WEBSOCKET_BASE_URL_ASYNC}?token={token}"

    try:
        ws = websocket.WebSocket()
        ws.connect(websocket_url)

        cap = cv2.VideoCapture(video_path)
        if not cap.isOpened():
            print("Error: Could not open video file.")
            return

        stop_event = threading.Event()
        frame_queue = queue.Queue(maxsize=10)  # Adjust maxsize as needed
        desired_fps = 50
        model_fps = 10
        frame_delay = 1.0 / desired_fps

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
                    break  # No frames available, exit loop

                _, img_encoded = cv2.imencode(".jpg", frame)
                img_bytes = img_encoded.tobytes()
                img_base64 = base64.b64encode(img_bytes).decode("utf-8")
                # Add a timestamp to the message

                message = json.dumps(
                    {
                        "token": token,
                        "camera_id": camera_id,
                        "image": img_base64,
                        "return_processed_image": False,
                        "subscriptionplan_id": SUBSCRIPTIONPLAN_ID,
                        "threat_recognition_threshold": 0.15,
                        "fps": model_fps,
                        "frame_number": frame_number,
                        "timestamp": frame_timestamp,  # Add timestamp here
                    }
                )
                try:
                    ws.send(message)
                except Exception as e:
                    print(f"Error sending frame: {e}")
                    stop_event.set()
                    break

                # Store the frame with frame_number and timestamp
                with sent_frames_lock:
                    sent_frames[frame_number] = (frame.copy(), frame_timestamp)

                frame_number += 1
                frame_queue.task_done()
                time.sleep(frame_delay)  # Control frame rate

        # Thread to receive and process responses from the server
        def receive_responses():
            while not stop_event.is_set():
                try:
                    response = ws.recv()
                    if response:
                        response_json = json.loads(response)
                        frame_number = response_json.get("frame_number")

                        with sent_frames_lock:
                            # Retrieve the corresponding frame
                            frame_data = sent_frames.pop(frame_number, None)
                            if frame_data is not None:
                                frame, frame_timestamp = frame_data
                                # Delete frames with frame numbers less than the received frame_number
                                keys_to_delete = [
                                    key
                                    for key in sent_frames.keys()
                                    if key < frame_number
                                ]
                                for key in keys_to_delete:
                                    del sent_frames[key]
                            else:
                                frame = None

                        if frame is not None:
                            visualized_frame = visualize_frame(
                                frame, response_json.get("frame_results")
                            )
                            cv2.imshow("Visualized Frame", visualized_frame)
                            if cv2.waitKey(1) & 0xFF == ord("q"):
                                stop_event.set()
                                break
                        else:
                            print(f"No frame found for frame_number {frame_number}")
                        print(f"Received response for frame {frame_number}")
                except Exception as e:
                    print(f"Error receiving response: {e}")
                    stop_event.set()
                    break

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

        cap.release()
        ws.close()
        cv2.destroyAllWindows()

    except Exception as e:
        print(f"Error connecting to WebSocket: {e}")


def visualize_frame(frame, frame_results):
    latest_noti = None
    if len(frame_results.get("notification", [])) > 0:
        latest_noti = frame_results["notification"][0]["content"]
        noti_time = time.time()
        # Convert to datetime HH:MM:SS DD-MM-YYYY
        noti_time = datetime.datetime.fromtimestamp(noti_time).strftime(
            "%H:%M:%S %d-%m-%Y"
        )
    if latest_noti is not None:
        # Add text overlay
        cv2.putText(
            frame,
            f"Notification: {latest_noti}",
            (10, 70),
            cv2.FONT_HERSHEY_SIMPLEX,
            1,
            (0, 0, 255),
            2,
        )
        # Add time overlay
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
    vehicle_count = frame_results.get("vehicle_count", [])  # [[class, count], ...]
    dangerous_signs = frame_results.get(
        "dangerous_signs", []
    )  # [[datetime, [signs]], ...]
    if len(vehicle_count) > 0:
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
        # Plot the dangerous signs count
        for idx, (datetime_str, signs) in enumerate(dangerous_signs):
            # Convert to datetime HH:MM:SS DD-MM-YYYY
            datetime_str = time.strftime(
                "%H:%M:%S %d-%m-%Y", time.gmtime(datetime_str + 10.5 * 3600)
            )
            ALL_TIME_DANGEROUS_SIGNS.append((datetime_str, signs))
        for idx, (datetime_str, signs) in enumerate(ALL_TIME_DANGEROUS_SIGNS):
            # convert to GMT+10:30
            cv2.putText(
                frame,
                f"{datetime_str}: {'+'.join(signs)}",
                (20, cur_y + 40 + 40 * idx),
                cv2.FONT_HERSHEY_COMPLEX,
                1,
                (0, 0, 255),
                3,
            )

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
        f"People: {num_human_tracks}" if num_human_tracks > 0 else "",
        (10, 30),
        cv2.FONT_HERSHEY_SIMPLEX,
        1,
        (0, 255, 0),
        2,
    )
    return frame


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
        # VIDEO_PATH = "/mnt/SSD2TB/code/neuweb/data/demo/gasstationfight.mp4"  # Replace with the path to your video file
        # VIDEO_PATH = "/mnt/SSD2TB/code/neuweb/data/tunnel/2.mp4"  # Replace with the path to your video file
        # VIDEO_PATH = 'rtsp://tho:Ldtho1610@100.82.206.126/axis-media/media.amp'
        VIDEO_PATH = "rtsp://root:Admin1234@100.91.128.124/axis-media/media.amp"
        # VIDEO_PATH = 0  # Uncomment if you want to use the default camera
        # Stream video
        print("Streaming video...")
        use_async = False  # Set this to True if you want to use the asyncio method
        # Stream video synchronously
        print("Streaming video synchronously...")
        # stream_video_sync(TOKEN, CAMERA_ID, VIDEO_PATH)
        stream_video_async(TOKEN, CAMERA_ID, VIDEO_PATH)
        # stream_video_noskip(TOKEN, CAMERA_ID, VIDEO_PATH, desired_fps=20)
        # stream_video_queue(TOKEN, CAMERA_ID, VIDEO_PATH)
    else:
        print("Error: Could not obtain access token.")
