# app.py
from flask import Flask, render_template, jsonify, request, send_from_directory
import subprocess
import threading
import os
import json
import time
import signal
import logging

import config
import state
from camera_handler import record_video, take_snapshot
from youtube_uploader import retry_failed_uploads

app = Flask(__name__, static_folder="static", template_folder="templates")

def is_phone_connected():
    try:
        output = subprocess.check_output("hcitool con", shell=True).decode()
        return "ACL" in output
    except Exception:
        return False

def update_active_color():
    """
    Checks if the current date is different from the stored date in colors.json.
    If it's different, the active color is moved to the next in the list
    and the date is updated.
    """
    today_str = time.strftime("%Y-%m-%d")

    if not os.path.exists(config.COLORS_PATH):
        default_colors = {
            "last_updated": "2000-01-01",
            "active_index": 0,
            "colors": ["#A7C7E7", "#C1E1C1", "#FDFD96", "#FFB347", "#FF6961"]
        }
        with open(config.COLORS_PATH, 'w') as f:
            json.dump(default_colors, f, indent=2)

    with open(config.COLORS_PATH, 'r+') as f:
        data = json.load(f)
        stored_date_str = data.get("last_updated")

        if stored_date_str != today_str:
            print(f"Date changed. Rotating active color.")
            current_index = data.get("active_index", 0)
            num_colors = len(data.get("colors", []))
            # Rotate to the next color, loop to the start if at the end
            data["active_index"] = (current_index + 1) % num_colors
            data["last_updated"] = today_str
            # Go back to the start of the file and overwrite
            f.seek(0)
            json.dump(data, f, indent=2)
            f.truncate()

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/songs")
def songs():
    with open(config.SONGS_PATH) as f:
        songs = json.load(f)
    songs.sort(key=lambda s: s["number"])

    # Standardize the data before sending it to the client
    # This makes the frontend code simpler and more robust.
    processed_songs = []
    for song in songs:
        processed_songs.append({
            "number": song["number"],
            "title": song["name"], # Use "name" as the source for "title"
            "filename": f"{song['number']:02d}-{song['name'].replace(' ', '_')}.txt", # Create a filename
            "active": song.get("active", False) # Send the active status
        })
    return jsonify(processed_songs)

@app.route("/start", methods=["POST"])
def start():
    if state.RECORDING:
        return jsonify({"status": "already recording"})
    data = request.get_json()
    filename = data.get("filename")
    title = data.get("title", filename) # Use filename as a fallback
    state.CURRENT_SONG = title
    t = threading.Thread(target=record_video, args=(title,))
    t.start()
    return jsonify({"status": "started"})

@app.route("/stop", methods=["POST"])
def stop():
    if state.RECORD_PROC and state.RECORDING:
        # Use os.killpg to send the signal to the entire process group.
        # This is a more robust way to terminate the process.
        os.killpg(os.getpgid(state.RECORD_PROC.pid), signal.SIGINT)
        return jsonify({"status": "stopped"})
    return jsonify({"status": "not recording"})

@app.route("/status")
def status():
    return jsonify({
        "recording": state.RECORDING,
        "bluetooth": is_phone_connected()
    })

@app.route("/upload_errors")
def upload_errors():
    """Returns a list of upload errors."""
    return jsonify(state.UPLOAD_ERRORS)

@app.route("/clear_error", methods=["POST"])
def clear_error():
    """Removes a specific error message from the list."""
    data = request.get_json()
    error_index = data.get("index")
    if error_index is not None and 0 <= error_index < len(state.UPLOAD_ERRORS):
        state.UPLOAD_ERRORS.pop(error_index)
    return jsonify({"status": "ok"})

@app.route("/upload_status")
def get_upload_status():
    """Returns a list of ongoing uploads and the active recording."""
    statuses = []
    # Add the active recording to the top of the list if it exists
    if state.RECORDING and state.CURRENT_SONG:
        statuses.append({'title': state.CURRENT_SONG, 'status': 'Recording...'})
    statuses.extend(list(state.UPLOAD_STATUS.values()))
    return jsonify(statuses)

@app.route("/static/<path:path>")
def static_files(path):
    return send_from_directory("static", path)

@app.route("/reboot", methods=["POST"])
def reboot():
    """Reboots the Raspberry Pi."""
    print("Received reboot request...")
    # Run the command in a separate thread to allow the server to respond before it restarts.
    def do_reboot():
        time.sleep(1)
        subprocess.run(["sudo", "reboot"])
    threading.Thread(target=do_reboot).start()
    return jsonify({"status": "rebooting"})

@app.route("/shutdown", methods=["POST"])
def shutdown():
    """Shuts down the Raspberry Pi."""
    print("Received shutdown request...")
    # Run the command in a separate thread to allow the server to respond before it shuts down.
    def do_shutdown():
        time.sleep(1)
        subprocess.run(["sudo", "shutdown", "-h", "now"])
    threading.Thread(target=do_shutdown).start()
    return jsonify({"status": "shutting down"})

@app.route("/snapshot.jpg")
def snapshot():
    try:
        snapshot_path = take_snapshot()
        return send_from_directory(os.path.dirname(snapshot_path), os.path.basename(snapshot_path))
    except Exception:
        return ("", 404)

# --- Application Startup ---
# This code runs once when Gunicorn starts the worker process.
print("Application starting: Running one-time setup...")
update_active_color()
retry_failed_uploads() # Starts the periodic check for failed uploads

if __name__ == "__main__":
    os.makedirs("static", exist_ok=True)
    # Make the server log less "noisy" by only showing errors
    # Disable the default logger to avoid a stream of GET requests in the terminal.
    logging.getLogger('werkzeug').disabled = True
    app.run(host="0.0.0.0", port=5000, debug=True)