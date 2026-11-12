#!/usr/bin/env python3
"""Radxa 双 USB 摄像头网页预览：倒车自动切换后摄。"""

import threading
import time
from pathlib import Path

import cv2
from flask import Flask, Response, jsonify


# 使用 USB 物理端口的稳定路径，避免重启后 video0/video2 编号交换。
FRONT_DEVICE = (
    "/dev/v4l/by-path/"
    "platform-xhci-hcd.4.auto-usb-0:1.2:1.0-video-index0"
)
REAR_DEVICE = (
    "/dev/v4l/by-path/"
    "platform-xhci-hcd.4.auto-usb-0:1.3:1.0-video-index0"
)

# 车辆控制程序写入：1=前进、0=空挡/停止、-1=倒车。
DIRECTION_FILE = Path("/tmp/radxa_vehicle_direction")

WIDTH = 1280
HEIGHT = 720
FPS = 60
JPEG_QUALITY = 80
PORT = 5000
SWITCH_CHECK_SECONDS = 0.10

app = Flask(__name__)
latest_jpeg = None
active_name = "正在启动"
active_device = ""
frame_lock = threading.Lock()
running = True


def read_direction() -> int:
    """读取车辆挡位；文件不存在或内容无效时安全地使用前摄。"""

    try:
        direction = int(DIRECTION_FILE.read_text(encoding="ascii").strip())
        return direction if direction in (-1, 0, 1) else 0
    except (OSError, ValueError):
        return 0


def selected_camera() -> tuple[str, str]:
    if read_direction() == -1:
        return REAR_DEVICE, "后摄（倒车）"
    return FRONT_DEVICE, "前摄"


def open_camera(device: str):
    camera = cv2.VideoCapture(device, cv2.CAP_V4L2)
    if not camera.isOpened():
        camera.release()
        return None

    camera.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
    camera.set(cv2.CAP_PROP_FRAME_WIDTH, WIDTH)
    camera.set(cv2.CAP_PROP_FRAME_HEIGHT, HEIGHT)
    camera.set(cv2.CAP_PROP_FPS, FPS)
    camera.set(cv2.CAP_PROP_BUFFERSIZE, 1)
    return camera


def capture_loop() -> None:
    """只打开当前需要的一个摄像头，倒车时释放前摄并打开后摄。"""

    global latest_jpeg, active_name, active_device

    camera = None
    opened_device = ""
    last_open_attempt = 0.0

    try:
        while running:
            wanted_device, wanted_name = selected_camera()

            if wanted_device != opened_device:
                if camera is not None:
                    camera.release()
                    camera = None

                opened_device = wanted_device
                active_device = wanted_device
                active_name = f"{wanted_name}：正在切换"
                with frame_lock:
                    latest_jpeg = None

                # 给 UVC 驱动一点释放上一摄像头的时间。
                time.sleep(0.15)
                last_open_attempt = 0.0

            if camera is None:
                now = time.monotonic()
                if now - last_open_attempt < 1.0:
                    time.sleep(SWITCH_CHECK_SECONDS)
                    continue

                last_open_attempt = now
                camera = open_camera(wanted_device)
                if camera is None:
                    active_name = f"{wanted_name}：打开失败，正在重试"
                    continue

                actual_width = int(camera.get(cv2.CAP_PROP_FRAME_WIDTH))
                actual_height = int(camera.get(cv2.CAP_PROP_FRAME_HEIGHT))
                actual_fps = camera.get(cv2.CAP_PROP_FPS)
                active_name = wanted_name
                print(
                    f"已切换到 {wanted_name}: {actual_width}x{actual_height}, "
                    f"{actual_fps:.1f} FPS"
                )

            ok, frame = camera.read()
            if not ok:
                print(f"{wanted_name} 读取失败，准备重新打开")
                camera.release()
                camera = None
                time.sleep(0.1)
                continue

            # 左上角标记当前画面，便于确认自动切换成功。
            cv2.putText(
                frame,
                "REAR" if read_direction() == -1 else "FRONT",
                (20, 45),
                cv2.FONT_HERSHEY_SIMPLEX,
                1.2,
                (0, 0, 255) if read_direction() == -1 else (0, 255, 0),
                3,
                cv2.LINE_AA,
            )

            ok, encoded = cv2.imencode(
                ".jpg",
                frame,
                [cv2.IMWRITE_JPEG_QUALITY, JPEG_QUALITY],
            )
            if ok:
                with frame_lock:
                    latest_jpeg = encoded.tobytes()
    finally:
        if camera is not None:
            camera.release()


@app.get("/")
def index():
    return """<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>Radxa 前后摄像头</title>
  <style>
    body { margin:0; background:#111; color:#eee; font-family:sans-serif; text-align:center; }
    h2 { margin:12px 0 5px; }
    #status { margin-bottom:10px; color:#8fdaff; }
    img { width:min(100vw,1280px); height:auto; background:#222; }
  </style>
</head>
<body>
  <h2>Radxa 前后摄像头</h2>
  <div id="status">正在启动……</div>
  <img src="/video.mjpg" alt="摄像头画面">
  <script>
    async function updateStatus() {
      try {
        const data = await (await fetch('/status')).json();
        document.getElementById('status').textContent =
          '挡位：' + data.gear + '　画面：' + data.camera;
      } catch (_) {}
    }
    updateStatus();
    setInterval(updateStatus, 500);
  </script>
</body>
</html>"""


@app.get("/status")
def status():
    direction = read_direction()
    gear = {1: "前进", 0: "空挡/停止", -1: "倒车"}[direction]
    return jsonify(gear=gear, camera=active_name, device=active_device)


@app.get("/video.mjpg")
def video_stream():
    def generate():
        last_frame = None
        while running:
            with frame_lock:
                image = latest_jpeg

            if image is None or image == last_frame:
                time.sleep(0.005)
                continue

            last_frame = image
            yield (
                b"--frame\r\n"
                b"Content-Type: image/jpeg\r\n"
                b"Content-Length: " + str(len(image)).encode() + b"\r\n\r\n"
                + image
                + b"\r\n"
            )

    return Response(
        generate(),
        mimetype="multipart/x-mixed-replace; boundary=frame",
    )


def main() -> None:
    thread = threading.Thread(target=capture_loop, daemon=True)
    thread.start()

    print(f"前摄：{FRONT_DEVICE}")
    print(f"后摄：{REAR_DEVICE}")
    print(f"电脑浏览器打开：http://192.168.0.155:{PORT}")
    print("进入倒车挡后自动切换后摄；空挡或前进自动切回前摄。")
    print("按 Ctrl+C 停止。")
    app.run(host="0.0.0.0", port=PORT, threaded=True)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        running = False
        print("\n摄像头网页程序已停止")
