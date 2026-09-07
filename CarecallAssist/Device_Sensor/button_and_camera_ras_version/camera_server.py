#!/usr/bin/env python3
import subprocess
from http.server import BaseHTTPRequestHandler, HTTPServer

HOST = "0.0.0.0"
PORT = 8080

CAMERA_CMD = [
    "rpicam-vid",
    "-t", "0",
    "-n",
    "--width", "640",
    "--height", "480",
    "--framerate", "10",
    "--codec", "mjpeg",
    "-o", "-"
]


class CameraHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/":
            html = """
            <!doctype html>
            <html>
            <head>
                <meta charset="utf-8">
                <title>Raspberry Pi Camera Stream</title>
                <style>
                    body {
                        background: #111;
                        color: white;
                        font-family: Arial, sans-serif;
                        text-align: center;
                    }
                    img {
                        width: 90%;
                        max-width: 900px;
                        border: 2px solid #444;
                        border-radius: 8px;
                    }
                </style>
            </head>
            <body>
                <h2>Raspberry Pi Camera Live Stream</h2>
                <img src="/stream.mjpg">
            </body>
            </html>
            """
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(html.encode("utf-8"))

        elif self.path == "/stream.mjpg":
            self.send_response(200)
            self.send_header("Age", "0")
            self.send_header("Cache-Control", "no-cache, private")
            self.send_header("Pragma", "no-cache")
            self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=FRAME")
            self.end_headers()

            process = subprocess.Popen(
                CAMERA_CMD,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                bufsize=0
            )

            try:
                buffer = b""
                while True:
                    data = process.stdout.read(4096)
                    if not data:
                        break

                    buffer += data

                    while True:
                        start = buffer.find(b"\xff\xd8")
                        end = buffer.find(b"\xff\xd9")

                        if start != -1 and end != -1 and end > start:
                            frame = buffer[start:end + 2]
                            buffer = buffer[end + 2:]

                            self.wfile.write(b"--FRAME\r\n")
                            self.wfile.write(b"Content-Type: image/jpeg\r\n")
                            self.wfile.write(
                                f"Content-Length: {len(frame)}\r\n\r\n".encode()
                            )
                            self.wfile.write(frame)
                            self.wfile.write(b"\r\n")
                        else:
                            break

            except BrokenPipeError:
                pass
            finally:
                process.terminate()

        else:
            self.send_response(404)
            self.end_headers()


if __name__ == "__main__":
    server = HTTPServer((HOST, PORT), CameraHandler)
    print(f"Camera server running at http://0.0.0.0:{PORT}")
    server.serve_forever()
