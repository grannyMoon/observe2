from flask import Flask, render_template, jsonify, request, send_from_directory
import subprocess
import threading
import os
import json
import shutil
import time
import signal
from PIL import Image, ImageDraw, ImageFont
import logging
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from googleapiclient.errors import HttpError

app = Flask(__name__, static_folder="static", template_folder="templates")

SONGS_PATH = "songs.json"
COLORS_PATH = "colors.json"
FAILED_UPLOADS_PATH = "failed_uploads.json"
RECORDING = False
RECORD_PROC = None
CURRENT_SONG = None
UPLOAD_ERRORS = [] # Global list to store upload errors
UPLOAD_STATUS = {} # Global dictionary to track upload statuses

retry_lock = threading.Lock() # Lock to prevent concurrent retry attempts
retry_timer = None # Holds the timer object

snapshot_lock = threading.Lock()

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

    # Check if the file exists, if not, create it
    if not os.path.exists(COLORS_PATH):
        default_colors = {
            "last_updated": "2000-01-01",
            "active_index": 0,
            "colors": ["#A7C7E7", "#C1E1C1", "#FDFD96", "#FFB347", "#FF6961"]
        }
        with open(COLORS_PATH, 'w') as f:
            json.dump(default_colors, f, indent=2)

    with open(COLORS_PATH, 'r+') as f:
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

def upload_to_youtube(video_path, thumbnail_path, title, playlist_date_str):
    """
    Uploads video and thumbnail to YouTube via the Google API.
    Deletes local files after a successful upload.
    """
    print(f"Starting YouTube upload for '{title}'...")
    # Update status for the UI
    if video_path in UPLOAD_STATUS:
        UPLOAD_STATUS[video_path]['status'] = 'Uploading...'

    # Build an absolute path to the secrets file to ensure it's always found.
    script_dir = os.path.dirname(os.path.abspath(__file__))
    TOKEN_FILE = os.path.join(script_dir, "token.json")

    SCOPES = ["https://www.googleapis.com/auth/youtube"] # Changed to handle playlists
    API_SERVICE_NAME = "youtube"
    API_VERSION = "v3"

    try:
        if not os.path.exists(TOKEN_FILE):
            print(f"ERROR: Could not find '{TOKEN_FILE}'.")
            print("Run 'python authenticate.py' first to log in.")
            return

        # Load stored credentials from token.json
        credentials = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)

        youtube = build(API_SERVICE_NAME, API_VERSION, credentials=credentials)

        playlist_title = f"Rehearsal {playlist_date_str}"
        playlist_id = None

        # Search for existing playlist
        playlists_response = youtube.playlists().list(
            part="snippet",
            mine=True,
            maxResults=50
        ).execute()

        for item in playlists_response.get("items", []):
            if item["snippet"]["title"] == playlist_title:
                playlist_id = item["id"]
                print(f"Found existing playlist: '{playlist_title}' (ID: {playlist_id})")
                break

        # Create playlist if it doesn't exist
        if not playlist_id:
            print(f"Playlist '{playlist_title}' not found. Creating new one...")
            playlist_body = {
                "snippet": {
                    "title": playlist_title,
                    "description": f"All takes from the rehearsal on {playlist_date_str}"
                },
                "status": { "privacyStatus": "private" }
            }
            playlist_insert_request = youtube.playlists().insert(
                part="snippet,status",
                body=playlist_body
            )
            playlist_response = playlist_insert_request.execute()
            playlist_id = playlist_response["id"]
            print(f"Created new playlist: '{playlist_title}' (ID: {playlist_id})")

        # Build request body
        body = {
            "snippet": {
                "title": title,
                "description": f"Rehearsal @ {time.strftime('%Y-%m-%d %H:%M')}",
                "tags": ["music", "live", "rehearsal"],
                "categoryId": "10" # 10 = Music
            },
            "status": {
                "privacyStatus": "private" # Can be changed to "public" or "unlisted"
            }
        }

        # Upload video
        media_file = MediaFileUpload(video_path, chunksize=-1, resumable=True)
        insert_request = youtube.videos().insert(
            part=",".join(body.keys()),
            body=body,
            media_body=media_file
        )

        response = insert_request.execute()
        print(f"Video uploaded. Video ID: {response['id']}")

        # Add the video to the playlist
        playlist_item_body = {
            "snippet": {
                "playlistId": playlist_id,
                "resourceId": { "kind": "youtube#video", "videoId": response['id'] }
            }
        }
        youtube.playlistItems().insert(part="snippet", body=playlist_item_body).execute()
        print(f"Video added to playlist '{playlist_title}'.")

        try:
            # Upload thumbnail
            youtube.thumbnails().set(
                videoId=response['id'],
                media_body=MediaFileUpload(thumbnail_path)
            ).execute()
            print("Thumbnail uploaded.")
        except HttpError as e:
            # Catch the specific error for thumbnail permissions
            if "custom video thumbnails" in str(e):
                print("\n--- IMPORTANT NOTICE ---")
                print("ERROR: Could not upload thumbnail. Your YouTube account must be verified.")
                print("Go to https://www.youtube.com/verify to enable this feature.")
                print("The video was uploaded, but you will need to add the thumbnail manually.")
                print("------------------------\n")
            else:
                # Re-throw other HttpError exceptions
                raise e

        # Update status before deletion
        if video_path in UPLOAD_STATUS:
            UPLOAD_STATUS[video_path]['status'] = 'Done! Deleting file...'

        # Delete local files
        print(f"Deleting local files: {video_path}, {thumbnail_path}")
        os.remove(video_path)
        os.remove(thumbnail_path)
        # Remove from the status list after a short delay, so the user can see the "deleted" message
        time.sleep(5)
        UPLOAD_STATUS.pop(video_path, None)
    except Exception as e:
        error_message = f"Upload of '{title}' failed: {e}"
        print(error_message)
        # Add the error message to the global list for UI display
        UPLOAD_ERRORS.append({"title": title, "message": str(e)})
        # Update status in the active list on failure
        if video_path in UPLOAD_STATUS:
            UPLOAD_STATUS[video_path]['status'] = 'Upload failed. Retrying later.'

        # Save failed upload for a later retry
        with retry_lock:
            try:
                with open(FAILED_UPLOADS_PATH, 'r') as f:
                    failed = json.load(f)
            except (FileNotFoundError, json.JSONDecodeError):
                failed = []

            # Avoid duplicates
            if not any(item['video_path'] == video_path for item in failed):
                failed.append({
                    "video_path": video_path,
                    "thumbnail_path": thumbnail_path,
                    "title": title,
                    "playlist_date_str": playlist_date_str
                })
                with open(FAILED_UPLOADS_PATH, 'w') as f:
                    json.dump(failed, f, indent=2)

def retry_failed_uploads():
    """Goes through failed uploads and tries to upload them again."""
    global retry_timer
    print("Running periodic check for failed uploads...")
    with retry_lock:
        try:
            with open(FAILED_UPLOADS_PATH, 'r') as f:
                failed = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            failed = [] # No file, nothing to do

        if not failed:
            print("No failed uploads to retry.")
        else:
            # Create a copy to iterate over, allowing modification of the original
            remaining_uploads = list(failed)
            for item in list(remaining_uploads):
                # Check if the files still exist
                if not (os.path.exists(item['video_path']) and os.path.exists(item['thumbnail_path'])):
                    print(f"Files for '{item['title']}' are missing, removing from retry list.")
                    remaining_uploads.remove(item)
                    continue

                try:
                    print(f"Retrying upload for '{item['title']}'...")
                    # Call upload_to_youtube. It will handle file and status removal on success.
                    upload_to_youtube(item['video_path'], item['thumbnail_path'], item['title'], item['playlist_date_str'])
                    # If we get here without an error, the upload was successful
                    print(f"Successfully re-uploaded '{item['title']}'.")
                    remaining_uploads.remove(item)
                except Exception as e:
                    print(f"Retry for '{item['title']}' failed again: {e}")

            # Write the updated list (only those that are still failing) back to the file,
            # but only if necessary.
            if failed != remaining_uploads:
                with open(FAILED_UPLOADS_PATH, 'w') as f:
                    json.dump(remaining_uploads, f, indent=2)

    # Set up the next retry attempt in one hour
    retry_timer = threading.Timer(3600, retry_failed_uploads)
    retry_timer.start()

def record_video(song):
    global RECORDING, RECORD_PROC, CURRENT_SONG
    # Wait for snapshot to finish
    while snapshot_lock.locked():
        time.sleep(0.1)

    RECORDING = True

    # Define file paths
    safe_name = "".join(c for c in song if c.isalnum() or c in (' ', '_', '-')).rstrip()
    dest_video = f"static/{safe_name}.mp4"
    dest_thumbnail = f"static/{safe_name}.png"
    # Record video directly to the final file
    cmd = [
        "rpicam-vid",
        "-t", "0", # Run until stopped manually
        "-o", dest_video,
        "--width", "1280",
        "--height", "720",
        "--framerate", "30",
        "--mode", "2304:1296", # Selects 2x2 binned mode for full wide angle
        "--codec", "libav",
        "--libav-format", "mp4",
        "--nopreview",
        "--flush", # Forces writing to disk for each frame
        # --- Parameters for audio recording ---
        # DO NOT use the --audio flag. It's enabled automatically by the parameters below.
        # "--libav-audio",
        # "--audio-device", "plughw:2",
        # "--audio-codec", "aac",
        # "--audio-source", "alsa", # Tells libav to capture audio from ALSA
        # "--audio-channels", "1" # Add explicit channel count (adjust to 1 for mono mic)
        # "--libav-audio",
        # "--audio-device", "plughw:2",
        # "--audio-device", "plughw:1", # UPDATED: Change '1' to the card number from 'arecord -l'
        # "--audio-codec", "aac",
        # "--audio-source", "alsa", # Tells libav to capture audio from ALSA
        # "--audio-channels", "1" # Add explicit channel count (adjust to 1 for mono mic)
    ]

    RECORD_PROC = subprocess.Popen(cmd, stderr=subprocess.PIPE, preexec_fn=os.setsid)
    # Wait for the process to finish and get stderr for debugging
    _, err = RECORD_PROC.communicate()

    # Check if the recording was actually created
    return_code = RECORD_PROC.returncode
    video_exists = os.path.exists(dest_video)
    video_size = os.path.getsize(dest_video) if video_exists else 0

    # If it failed, print the actual error message from rpicam-vid to the log
    if return_code != 0 and err:
        print("--- rpicam-vid FEILMELDING ---")
        print(err.decode(errors='ignore'))
        print("-----------------------------")

    # Give the filesystem a moment to finish writing after the process has been stopped.
    if return_code != 0:
        time.sleep(1)

    # Error handling: Check if the video was created correctly
    # When we stop with SIGINT, the return code is often not 0.
    # We consider it an error ONLY if the file doesn't exist or is empty.
    # A non-zero return code alone is no longer an error, as it's expected on stop.
    if not video_exists or video_size == 0:
        print(f"Recording failed or resulted in an empty file. Code: {return_code}")
        if video_exists: os.remove(dest_video) # Delete empty/corrupt file
        RECORDING = False # Set to false before exiting the thread
        RECORD_PROC = None
        return # Exit the thread

    # Create thumbnail
    make_splash(song, dest_thumbnail)

    # Get the date for the playlist BEFORE starting the upload thread
    script_dir = os.path.dirname(os.path.abspath(__file__))
    with open(os.path.join(script_dir, COLORS_PATH), 'r') as f:
        color_data = json.load(f)
        playlist_date_str = color_data.get("last_updated", time.strftime("%Y-%m-%d"))

    # Start the upload in a separate thread to avoid blocking
    # Add to the status dictionary before the thread starts
    UPLOAD_STATUS[dest_video] = {'title': song, 'status': 'Waiting...'}
    upload_thread = threading.Thread(target=upload_to_youtube, args=(dest_video, dest_thumbnail, song, playlist_date_str))
    upload_thread.start()

    # Only NOW are we completely finished.
    RECORDING = False
    CURRENT_SONG = None
    RECORD_PROC = None

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/songs")
def songs():
    with open(SONGS_PATH) as f:
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
    global RECORDING, CURRENT_SONG
    if RECORDING:
        return jsonify({"status": "already recording"})
    data = request.get_json()
    filename = data.get("filename")
    title = data.get("title", filename) # Use filename as a fallback
    CURRENT_SONG = title
    t = threading.Thread(target=record_video, args=(title,))
    t.start()
    return jsonify({"status": "started"})

@app.route("/stop", methods=["POST"])
def stop():
    global RECORD_PROC, RECORDING, CURRENT_SONG
    if RECORD_PROC and RECORDING:
        # Use os.killpg to send the signal to the entire process group.
        # This is a more robust way to terminate the process.
        os.killpg(os.getpgid(RECORD_PROC.pid), signal.SIGINT)
        return jsonify({"status": "stopped"})
    return jsonify({"status": "not recording"})

@app.route("/status")
def status():
    return jsonify({
        "recording": RECORDING,
        "bluetooth": is_phone_connected()
    })

@app.route("/upload_errors")
def upload_errors():
    """Returns a list of upload errors."""
    return jsonify(UPLOAD_ERRORS)

@app.route("/clear_error", methods=["POST"])
def clear_error():
    """Removes a specific error message from the list."""
    data = request.get_json()
    error_index = data.get("index")
    if error_index is not None and 0 <= error_index < len(UPLOAD_ERRORS):
        UPLOAD_ERRORS.pop(error_index)
    return jsonify({"status": "ok"})

@app.route("/upload_status")
def get_upload_status():
    """Returns a list of ongoing uploads and the active recording."""
    statuses = []
    # Add the active recording to the top of the list if it exists
    if RECORDING and CURRENT_SONG:
        statuses.append({'title': CURRENT_SONG, 'status': 'Recording...'})
    statuses.extend(list(UPLOAD_STATUS.values()))
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
    global RECORDING
    if RECORDING:
        return send_from_directory("static", "snapshot.jpg")
    tmpfile = "static/snapshot.jpg"
    got_lock = snapshot_lock.acquire(blocking=False)
    if not got_lock:
        return send_from_directory("static", "snapshot.jpg")
    try:
        subprocess.run([
            "rpicam-still",
            "-o", tmpfile,
            "--width", "640",
            "--height", "360",
            "-t", "100",
            "--mode", "2304:1296", # Selects 2x2 binned mode for full wide angle
            "--nopreview"
        ], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL) # Redirect output to null
        return send_from_directory("static", "snapshot.jpg")
    except Exception:
        return ("", 404)
    finally:
        snapshot_lock.release()

def make_splash(songname, splash_path, width=1280, height=720):
    # Get today's active color
    try:
        with open(COLORS_PATH, 'r') as f:
            color_data = json.load(f)
            active_index = color_data.get("active_index", 0)
            colors = color_data.get("colors", ["#000000"])
            background_color_hex = colors[active_index]
    except (FileNotFoundError, IndexError):
        background_color_hex = "#000000" # Fallback to black

    # Determine text color based on background brightness for best readability
    # Convert hex to RGB
    h = background_color_hex.lstrip('#')
    r, g, b = tuple(int(h[i:i+2], 16) for i in (0, 2, 4))
    # Calculate luminance
    luminance = (0.299 * r + 0.587 * g + 0.114 * b) / 255
    if luminance > 0.5:
        text_color = "#000000" # Black text on light background
    else:
        text_color = "#FFFFFF" # White text on dark background

    img = Image.new('RGB', (width, height), color=background_color_hex)
    draw = ImageDraw.Draw(img)

    # Get fonts
    try:
        font_title = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 80)
        font_date = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 40)
    except Exception:
        font_title = ImageFont.load_default()
        font_date = ImageFont.load_default()

    # Text for song title
    title_text = songname
    try:
        title_bbox = draw.textbbox((0, 0), title_text, font=font_title)
        title_w, title_h = title_bbox[2] - title_bbox[0], title_bbox[3] - title_bbox[1]
    except AttributeError:
        title_w, title_h = draw.textsize(title_text, font=font_title)

    # Place song title slightly above the center
    draw.text(
        ((width - title_w) / 2, (height / 2) - title_h),
        title_text,
        font=font_title,
        fill=text_color
    )

    # Format and place date and time centered below the title
    # Format: 27 October at 21:45
    now = time.localtime()
    # Use strftime for locale-aware formatting, but set locale first if needed.
    # For simplicity and consistency, we'll use a standard English format.
    date_text = time.strftime("%d %B at %H:%M", now)

    try:
        date_bbox = draw.textbbox((0, 0), date_text, font=font_date)
        date_w, _ = date_bbox[2] - date_bbox[0], date_bbox[3] - date_bbox[1]
    except AttributeError:
        date_w, _ = draw.textsize(date_text, font=font_date)

    draw.text(
        ((width - date_w) / 2, (height / 2) + 20), # Place it below the center
        date_text,
        font=font_date,
        fill=text_color
    )

    img.save(splash_path, format="PNG")

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