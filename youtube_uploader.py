# youtube_uploader.py
import os
import json
import time
import threading
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from googleapiclient.errors import HttpError

import config
import state

retry_timer = None

def upload_to_youtube(video_path, thumbnail_path, title, playlist_date_str):
    """
    Uploads video and thumbnail to YouTube via the Google API.
    Deletes local files after a successful upload.
    """
    print(f"Starting YouTube upload for '{title}'...")
    if video_path in state.UPLOAD_STATUS:
        state.UPLOAD_STATUS[video_path]['status'] = 'Uploading...'

    script_dir = os.path.dirname(os.path.abspath(__file__))
    token_path = os.path.join(script_dir, config.TOKEN_FILE)

    try:
        if not os.path.exists(token_path):
            print(f"ERROR: Could not find '{token_path}'.")
            print("Run 'python authenticate.py' first to log in.")
            return

        credentials = Credentials.from_authorized_user_file(token_path, config.YOUTUBE_SCOPES)
        youtube = build(config.YOUTUBE_API_SERVICE_NAME, config.YOUTUBE_API_VERSION, credentials=credentials)

        playlist_title = f"Rehearsal {playlist_date_str}"
        playlist_id = None

        playlists_response = youtube.playlists().list(part="snippet", mine=True, maxResults=50).execute()
        for item in playlists_response.get("items", []):
            if item["snippet"]["title"] == playlist_title:
                playlist_id = item["id"]
                print(f"Found existing playlist: '{playlist_title}' (ID: {playlist_id})")
                break

        if not playlist_id:
            print(f"Playlist '{playlist_title}' not found. Creating new one...")
            playlist_body = {
                "snippet": {"title": playlist_title, "description": f"All takes from the rehearsal on {playlist_date_str}"},
                "status": {"privacyStatus": "private"}
            }
            playlist_insert_request = youtube.playlists().insert(part="snippet,status", body=playlist_body)
            playlist_response = playlist_insert_request.execute()
            playlist_id = playlist_response["id"]
            print(f"Created new playlist: '{playlist_title}' (ID: {playlist_id})")

        body = {
            "snippet": {
                "title": title,
                "description": f"Rehearsal @ {time.strftime('%Y-%m-%d %H:%M')}",
                "tags": ["music", "live", "rehearsal"],
                "categoryId": "10"
            },
            "status": {"privacyStatus": "private"}
        }

        media_file = MediaFileUpload(video_path, chunksize=-1, resumable=True)
        insert_request = youtube.videos().insert(part=",".join(body.keys()), body=body, media_body=media_file)
        response = insert_request.execute()
        print(f"Video uploaded. Video ID: {response['id']}")

        playlist_item_body = {
            "snippet": {
                "playlistId": playlist_id,
                "resourceId": {"kind": "youtube#video", "videoId": response['id']}
            }
        }
        youtube.playlistItems().insert(part="snippet", body=playlist_item_body).execute()
        print(f"Video added to playlist '{playlist_title}'.")

        try:
            youtube.thumbnails().set(videoId=response['id'], media_body=MediaFileUpload(thumbnail_path)).execute()
            print("Thumbnail uploaded.")
        except HttpError as e:
            if "custom video thumbnails" in str(e):
                print("\n--- IMPORTANT NOTICE ---")
                print("ERROR: Could not upload thumbnail. Your YouTube account must be verified.")
                print("Go to https://www.youtube.com/verify to enable this feature.")
                print("The video was uploaded, but you will need to add the thumbnail manually.")
                print("------------------------\n")
            else:
                raise e

        if video_path in state.UPLOAD_STATUS:
            state.UPLOAD_STATUS[video_path]['status'] = 'Done! Deleting file...'

        print(f"Deleting local files: {video_path}, {thumbnail_path}")
        os.remove(video_path)
        os.remove(thumbnail_path)
        time.sleep(5)
        state.UPLOAD_STATUS.pop(video_path, None)

    except Exception as e:
        error_message = f"Upload of '{title}' failed: {e}"
        print(error_message)
        state.UPLOAD_ERRORS.append({"title": title, "message": str(e)})
        if video_path in state.UPLOAD_STATUS:
            state.UPLOAD_STATUS[video_path]['status'] = 'Upload failed. Retrying later.'

        with state.retry_lock:
            try:
                with open(config.FAILED_UPLOADS_PATH, 'r') as f:
                    failed = json.load(f)
            except (FileNotFoundError, json.JSONDecodeError):
                failed = []

            if not any(item['video_path'] == video_path for item in failed):
                failed.append({
                    "video_path": video_path, "thumbnail_path": thumbnail_path,
                    "title": title, "playlist_date_str": playlist_date_str
                })
                with open(config.FAILED_UPLOADS_PATH, 'w') as f:
                    json.dump(failed, f, indent=2)

def retry_failed_uploads():
    """Goes through failed uploads and tries to upload them again."""
    global retry_timer
    print("Running periodic check for failed uploads...")
    with state.retry_lock:
        try:
            with open(config.FAILED_UPLOADS_PATH, 'r') as f:
                failed = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            failed = []

        if not failed:
            print("No failed uploads to retry.")
        else:
            remaining_uploads = list(failed)
            for item in list(remaining_uploads):
                if not (os.path.exists(item['video_path']) and os.path.exists(item['thumbnail_path'])):
                    print(f"Files for '{item['title']}' are missing, removing from retry list.")
                    remaining_uploads.remove(item)
                    continue
                try:
                    print(f"Retrying upload for '{item['title']}'...")
                    upload_to_youtube(item['video_path'], item['thumbnail_path'], item['title'], item['playlist_date_str'])
                    print(f"Successfully re-uploaded '{item['title']}'.")
                    remaining_uploads.remove(item)
                except Exception as e:
                    print(f"Retry for '{item['title']}' failed again: {e}")

            if failed != remaining_uploads:
                with open(config.FAILED_UPLOADS_PATH, 'w') as f:
                    json.dump(remaining_uploads, f, indent=2)

    retry_timer = threading.Timer(3600, retry_failed_uploads)
    retry_timer.start()