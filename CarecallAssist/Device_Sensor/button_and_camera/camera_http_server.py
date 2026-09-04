#!/usr/bin/env python3

import json
import logging
import signal
import subprocess
import threading
import time

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


DEVICE_ID = "raspi5_01"

HTTP_HOST = "0.0.0.0"
HTTP_PORT = 8000

CAMERA_WIDTH = 1280
CAMERA_HEIGHT = 720
CAMERA_FPS = 15


HTML_PAGE = """<!DOCTYPE html>
<html lang="ko">
<head>
    <meta charset="UTF-8">
    <title>CareCall Camera</title>

    <style>
        body {
            margin: 0;
            padding: 20px;
            background: #111;
            color: white;
            font-family: sans-serif;
            text-align: center;
        }

        img {
            width: 95%;
            max-width: 1280px;
            border: 1px solid #555;
        }
    </style>
</head>

<body>
    <h1>CareCall Raspberry Pi Camera</h1>
    <p>Device: raspi5_01</p>
    <img src="/stream.mjpg" alt="Camera stream">
</body>
</html>
"""


class CameraStream:
    def __init__(self):
        self.frame = None
        self.frame_number = 0
        self.condition = threading.Condition()

        self.process = None
        self.reader_thread = None
        self.running = False

    def start(self):
        if self.running:
            return

        command = [
            "/usr/local/bin/rpicam-vid",
            "--camera",
            "0",
            "--nopreview",
            "--timeout",
            "0",
            "--width",
            str(CAMERA_WIDTH),
            "--height",
            str(CAMERA_HEIGHT),
            "--framerate",
            str(CAMERA_FPS),
            "--codec",
            "mjpeg",
            "--output",
            "-",
        ]

        logging.info(
            "Starting camera command: %s",
            " ".join(command),
        )

        self.process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=None,
            bufsize=0,
        )

        self.running = True

        self.reader_thread = threading.Thread(
            target=self._read_frames,
            daemon=True,
        )
        self.reader_thread.start()

    def _read_frames(self):
        buffer = bytearray()

        while self.running:
            if self.process is None or self.process.stdout is None:
                break

            chunk = self.process.stdout.read(8192)

            if not chunk:
                if self.process.poll() is not None:
                    logging.error(
                        "rpicam-vid stopped with code %s",
                        self.process.returncode,
                    )
                    break

                time.sleep(0.01)
                continue

            buffer.extend(chunk)

            while True:
                start_index = buffer.find(b"\xff\xd8")

                if start_index < 0:
                    # JPEG 시작 표시를 찾지 못한 경우
                    if len(buffer) > 1:
                        del buffer[:-1]
                    break

                end_index = buffer.find(
                    b"\xff\xd9",
                    start_index + 2,
                )

                if end_index < 0:
                    # 불완전한 JPEG 앞의 불필요한 데이터 제거
                    if start_index > 0:
                        del buffer[:start_index]
                    break

                frame_end = end_index + 2
                frame = bytes(buffer[start_index:frame_end])

                del buffer[:frame_end]

                with self.condition:
                    self.frame = frame
                    self.frame_number += 1
                    self.condition.notify_all()

        self.running = False

        with self.condition:
            self.condition.notify_all()

    def wait_for_frame(
        self,
        previous_frame_number=-1,
        timeout=5.0,
    ):
        with self.condition:
            if (
                self.frame is None
                or self.frame_number == previous_frame_number
            ):
                self.condition.wait(timeout=timeout)

            return self.frame, self.frame_number

    def stop(self):
        self.running = False

        if self.process is not None:
            if self.process.poll() is None:
                self.process.terminate()

                try:
                    self.process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    self.process.kill()
                    self.process.wait(timeout=2)

            self.process = None

        with self.condition:
            self.condition.notify_all()


class CameraHttpHandler(BaseHTTPRequestHandler):
    camera_stream = None

    def send_common_headers(self, content_type):
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", "no-cache, no-store")
        self.send_header("Pragma", "no-cache")
        self.send_header("Expires", "0")
        self.send_header("Access-Control-Allow-Origin", "*")

    def do_GET(self):
        request_path = self.path.split("?", 1)[0]

        if request_path == "/":
            self.send_response(302)
            self.send_header("Location", "/index.html")
            self.end_headers()
            return

        if request_path == "/index.html":
            content = HTML_PAGE.encode("utf-8")

            self.send_response(200)
            self.send_common_headers(
                "text/html; charset=utf-8"
            )
            self.send_header(
                "Content-Length",
                str(len(content)),
            )
            self.end_headers()

            self.wfile.write(content)
            return

        if request_path == "/health":
            camera_running = (
                self.camera_stream is not None
                and self.camera_stream.running
            )

            response = {
                "device_id": DEVICE_ID,
                "status": "ok" if camera_running else "error",
                "camera": (
                    "running"
                    if camera_running
                    else "stopped"
                ),
                "stream_path": "/stream.mjpg",
                "snapshot_path": "/snapshot.jpg",
                "width": CAMERA_WIDTH,
                "height": CAMERA_HEIGHT,
                "fps": CAMERA_FPS,
            }

            content = json.dumps(
                response,
                ensure_ascii=False,
            ).encode("utf-8")

            self.send_response(
                200 if camera_running else 503
            )
            self.send_common_headers("application/json")
            self.send_header(
                "Content-Length",
                str(len(content)),
            )
            self.end_headers()

            self.wfile.write(content)
            return

        if request_path == "/snapshot.jpg":
            frame, _ = self.camera_stream.wait_for_frame(
                timeout=5.0
            )

            if frame is None:
                self.send_error(
                    503,
                    "Camera frame is not ready",
                )
                return

            self.send_response(200)
            self.send_common_headers("image/jpeg")
            self.send_header(
                "Content-Length",
                str(len(frame)),
            )
            self.end_headers()

            self.wfile.write(frame)
            return

        if request_path == "/stream.mjpg":
            self.send_response(200)
            self.send_common_headers(
                "multipart/x-mixed-replace; boundary=FRAME"
            )
            self.end_headers()

            previous_frame_number = -1

            try:
                while self.camera_stream.running:
                    frame, frame_number = (
                        self.camera_stream.wait_for_frame(
                            previous_frame_number,
                            timeout=5.0,
                        )
                    )

                    if frame is None:
                        continue

                    if frame_number == previous_frame_number:
                        continue

                    previous_frame_number = frame_number

                    self.wfile.write(b"--FRAME\r\n")
                    self.wfile.write(
                        b"Content-Type: image/jpeg\r\n"
                    )
                    self.wfile.write(
                        (
                            f"Content-Length: "
                            f"{len(frame)}\r\n"
                        ).encode("utf-8")
                    )
                    self.wfile.write(b"\r\n")
                    self.wfile.write(frame)
                    self.wfile.write(b"\r\n")
                    self.wfile.flush()

            except (
                BrokenPipeError,
                ConnectionResetError,
                ConnectionAbortedError,
            ):
                logging.info(
                    "Camera client disconnected: %s",
                    self.client_address[0],
                )

            except Exception as error:
                logging.warning(
                    "Camera client error %s: %s",
                    self.client_address[0],
                    error,
                )

            return

        self.send_error(404, "Not found")

    def log_message(self, message_format, *args):
        logging.info(
            "%s - %s",
            self.client_address[0],
            message_format % args,
        )


def main():
    logging.basicConfig(
        level=logging.INFO,
        format=(
            "[%(asctime)s] "
            "%(levelname)s: "
            "%(message)s"
        ),
    )

    camera_stream = CameraStream()
    camera_stream.start()

    CameraHttpHandler.camera_stream = camera_stream

    http_server = ThreadingHTTPServer(
        (HTTP_HOST, HTTP_PORT),
        CameraHttpHandler,
    )

    http_server.daemon_threads = True
    http_server.allow_reuse_address = True

    def handle_stop_signal(signum, frame):
        logging.info(
            "Stop signal received: %s",
            signum,
        )
        raise KeyboardInterrupt

    signal.signal(
        signal.SIGTERM,
        handle_stop_signal,
    )
    signal.signal(
        signal.SIGINT,
        handle_stop_signal,
    )

    try:
        logging.info(
            "Camera HTTP server started: "
            "http://0.0.0.0:%d",
            HTTP_PORT,
        )

        http_server.serve_forever()

    except KeyboardInterrupt:
        logging.info(
            "Stopping camera HTTP server"
        )

    finally:
        http_server.server_close()
        camera_stream.stop()


if __name__ == "__main__":
    main()
