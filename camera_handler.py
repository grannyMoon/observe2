# camera_handler.py
import subprocess
import threading
import os
import time
import json
from PIL import Image, ImageDraw, ImageFont

import state
import config
from youtube_uploader import upload_to_youtube

def record_video(song):
    """Handles the entire recording process in a thread."""
    while state.snapshot_lock.locked():
        time.sleep(0.1)

    state.RECORDING = True

    safe_name = "".join(c for c in song if c.isalnum() or c in (' ', '_', '-')).rstrip()
    dest_video = f"static/{safe_name}.mp4"
    dest_thumbnail = f"static/{safe_name}.png"

    cmd = [
        "rpicam-vid", "-t", "0", "-o", dest_video,
        "--width", "1280", "--height", "720", "--framerate", "30",
        "--mode", "2304:1296", "--codec", "libav", "--libav-format", "mp4",
        "--nopreview", "--flush"
    ]

    state.RECORD_PROC = subprocess.Popen(cmd, stderr=subprocess.PIPE, preexec_fn=os.setsid)
    _, err = state.RECORD_PROC.communicate()

    return_code = state.RECORD_PROC.returncode
    video_exists = os.path.exists(dest_video)
    video_size = os.path.getsize(dest_video) if video_exists else 0

    if return_code != 0 and err:
        print("--- rpicam-vid ERROR ---")
        print(err.decode(errors='ignore'))
        print("------------------------")

    if return_code != 0:
        time.sleep(1)

    if not video_exists or video_size == 0:
        print(f"Recording failed or resulted in an empty file. Code: {return_code}")
        if video_exists: os.remove(dest_video)
        state.RECORDING = False
        state.RECORD_PROC = None
        return

    make_splash(song, dest_thumbnail)

    script_dir = os.path.dirname(os.path.abspath(__file__))
    with open(os.path.join(script_dir, config.COLORS_PATH), 'r') as f:
        color_data = json.load(f)
        playlist_date_str = color_data.get("last_updated", time.strftime("%Y-%m-%d"))

    state.UPLOAD_STATUS[dest_video] = {'title': song, 'status': 'Waiting...'}
    upload_thread = threading.Thread(target=upload_to_youtube, args=(dest_video, dest_thumbnail, song, playlist_date_str))
    upload_thread.start()

    state.RECORDING = False
    state.CURRENT_SONG = None
    state.RECORD_PROC = None

def take_snapshot():
    """Takes a snapshot, returns the file path or raises an exception."""
    if state.RECORDING:
        return "static/snapshot.jpg"

    tmpfile = "static/snapshot.jpg"
    got_lock = state.snapshot_lock.acquire(blocking=False)
    if not got_lock:
        return "static/snapshot.jpg"

    try:
        subprocess.run([
            "rpicam-still", "-o", tmpfile,
            "--width", "640", "--height", "360", "-t", "100",
            "--mode", "2304:1296", "--nopreview"
        ], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return tmpfile
    finally:
        state.snapshot_lock.release()

def make_splash(songname, splash_path, width=1280, height=720):
    """Creates a splash screen image for the video thumbnail."""
    try:
        with open(config.COLORS_PATH, 'r') as f:
            color_data = json.load(f)
            active_index = color_data.get("active_index", 0)
            colors = color_data.get("colors", ["#000000"])
            background_color_hex = colors[active_index]
    except (FileNotFoundError, IndexError):
        background_color_hex = "#000000"

    h = background_color_hex.lstrip('#')
    r, g, b = tuple(int(h[i:i+2], 16) for i in (0, 2, 4))
    luminance = (0.299 * r + 0.587 * g + 0.114 * b) / 255
    text_color = "#000000" if luminance > 0.5 else "#FFFFFF"

    img = Image.new('RGB', (width, height), color=background_color_hex)
    draw = ImageDraw.Draw(img)

    try:
        font_title = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 80)
        font_date = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 40)
    except Exception:
        font_title = ImageFont.load_default()
        font_date = ImageFont.load_default()

    title_text = songname
    try:
        title_bbox = draw.textbbox((0, 0), title_text, font=font_title)
        title_w, title_h = title_bbox[2] - title_bbox[0], title_bbox[3] - title_bbox[1]
    except AttributeError:
        title_w, title_h = draw.textsize(title_text, font=font_title)

    draw.text(
        ((width - title_w) / 2, (height / 2) - title_h),
        title_text,
        font=font_title,
        fill=text_color
    )

    date_text = time.strftime("%d %B at %H:%M", time.localtime())
    try:
        date_bbox = draw.textbbox((0, 0), date_text, font=font_date)
        date_w, _ = date_bbox[2] - date_bbox[0], date_bbox[3] - date_bbox[1]
    except AttributeError:
        date_w, _ = draw.textsize(date_text, font=font_date)

    draw.text(
        ((width - date_w) / 2, (height / 2) + 20),
        date_text,
        font=font_date,
        fill=text_color
    )

    img.save(splash_path, format="PNG")