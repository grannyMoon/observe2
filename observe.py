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
UPLOAD_ERRORS = [] # Global liste for å lagre opplastingsfeil
UPLOAD_STATUS = {} # Global ordbok for å spore status på opplastinger

retry_lock = threading.Lock() # Lås for å unngå samtidige gjenopplastingsforsøk
retry_timer = None # Holder på timer-objektet

snapshot_lock = threading.Lock()

def is_phone_connected():
    try:
        output = subprocess.check_output("hcitool con", shell=True).decode()
        return "ACL" in output
    except Exception:
        return False

def update_active_color():
    """
    Sjekker om dagens dato er annerledes enn den lagrede datoen i colors.json.
    Hvis den er annerledes, flyttes den aktive fargen til neste i listen
    og datoen oppdateres.
    """
    today_str = time.strftime("%Y-%m-%d")

    # Sjekk om filen eksisterer, hvis ikke, opprett den
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
            print(f"Dato endret. Roterer aktiv farge.")
            current_index = data.get("active_index", 0)
            num_colors = len(data.get("colors", []))
            # Roter til neste farge, gå til start hvis på slutten
            data["active_index"] = (current_index + 1) % num_colors
            data["last_updated"] = today_str
            # Gå tilbake til starten av filen og skriv over
            f.seek(0)
            json.dump(data, f, indent=2)
            f.truncate()

def upload_to_youtube(video_path, thumbnail_path, title, playlist_date_str):
    """
    Laster opp video og miniatyrbilde til YouTube via Google API.
    Sletter lokale filer etter vellykket opplasting.
    """
    print(f"Starter YouTube-opplasting for '{title}'...")
    # Oppdater status for UI
    if video_path in UPLOAD_STATUS:
        UPLOAD_STATUS[video_path]['status'] = 'Laster opp...'

    # Bygg en absolutt sti til secrets-filen for å sikre at den alltid blir funnet.
    script_dir = os.path.dirname(os.path.abspath(__file__))
    TOKEN_FILE = os.path.join(script_dir, "token.json")

    SCOPES = ["https://www.googleapis.com/auth/youtube"] # Endret for å kunne håndtere spillelister
    API_SERVICE_NAME = "youtube"
    API_VERSION = "v3"

    try:
        if not os.path.exists(TOKEN_FILE):
            print(f"FEIL: Finner ikke '{TOKEN_FILE}'.")
            print("Kjør 'python authenticate.py' først for å logge inn.")
            return

        # Last inn lagrede credentials fra token.json
        credentials = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)

        youtube = build(API_SERVICE_NAME, API_VERSION, credentials=credentials)

        playlist_title = f"Rehearsal {playlist_date_str}"
        playlist_id = None

        # Søk etter eksisterende spilleliste
        playlists_response = youtube.playlists().list(
            part="snippet",
            mine=True,
            maxResults=50
        ).execute()

        for item in playlists_response.get("items", []):
            if item["snippet"]["title"] == playlist_title:
                playlist_id = item["id"]
                print(f"Fant eksisterende spilleliste: '{playlist_title}' (ID: {playlist_id})")
                break

        # Opprett spilleliste hvis den ikke finnes
        if not playlist_id:
            print(f"Spilleliste '{playlist_title}' ikke funnet. Oppretter ny...")
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
            print(f"Opprettet ny spilleliste: '{playlist_title}' (ID: {playlist_id})")

        # Bygg request body
        body = {
            "snippet": {
                "title": title,
                "description": f"Rehearsal @ {time.strftime('%Y-%m-%d %H:%M')}",
                "tags": ["music", "live", "rehearsal"],
                "categoryId": "10" # 10 = Music
            },
            "status": {
                "privacyStatus": "private" # Kan endres til "public" eller "unlisted"
            }
        }

        # Last opp video
        media_file = MediaFileUpload(video_path, chunksize=-1, resumable=True)
        insert_request = youtube.videos().insert(
            part=",".join(body.keys()),
            body=body,
            media_body=media_file
        )

        response = insert_request.execute()
        print(f"Video lastet opp. Video ID: {response['id']}")

        # Legg videoen til i spillelisten
        playlist_item_body = {
            "snippet": {
                "playlistId": playlist_id,
                "resourceId": { "kind": "youtube#video", "videoId": response['id'] }
            }
        }
        youtube.playlistItems().insert(part="snippet", body=playlist_item_body).execute()
        print(f"Video lagt til i spilleliste '{playlist_title}'.")

        try:
            # Last opp miniatyrbilde
            youtube.thumbnails().set(
                videoId=response['id'],
                media_body=MediaFileUpload(thumbnail_path)
            ).execute()
            print("Miniatyrbilde lastet opp.")
        except HttpError as e:
            # Fang opp den spesifikke feilen for thumbnail-tillatelser
            if "custom video thumbnails" in str(e):
                print("\n--- VIKTIG MELDING ---")
                print("FEIL: Miniatyrbildet kunne ikke lastes opp. YouTube-kontoen din må verifiseres.")
                print("Gå til https://www.youtube.com/verify for å aktivere funksjonen.")
                print("Videoen ble lastet opp, men du må legge til miniatyrbildet manuelt.")
                print("----------------------\n")
            else:
                # Kast andre HttpError-feil videre
                raise e

        # Oppdater status før sletting
        if video_path in UPLOAD_STATUS:
            UPLOAD_STATUS[video_path]['status'] = 'Ferdig! Sletter fil...'

        # Slett lokale filer
        print(f"Sletter lokale filer: {video_path}, {thumbnail_path}")
        os.remove(video_path)
        os.remove(thumbnail_path)

        # Fjern fra statuslisten etter en kort forsinkelse, slik at brukeren ser "slettet"-meldingen
        time.sleep(5)
        UPLOAD_STATUS.pop(video_path, None)
    except Exception as e:
        error_message = f"Opplasting av '{title}' feilet: {e}"
        print(error_message)
        # Legg til feilmeldingen i den globale listen for visning i UI
        UPLOAD_ERRORS.append({"title": title, "message": str(e)})
        # Fjern fra den aktive statuslisten ved feil
        if video_path in UPLOAD_STATUS:
            UPLOAD_STATUS[video_path]['status'] = 'Opplasting feilet. Prøver igjen senere.'

        # Lagre mislykket opplasting for senere forsøk
        with retry_lock:
            try:
                with open(FAILED_UPLOADS_PATH, 'r') as f:
                    failed = json.load(f)
            except (FileNotFoundError, json.JSONDecodeError):
                failed = []

            # Unngå duplikater
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
    """Går gjennom mislykkede opplastinger og prøver å laste dem opp på nytt."""
    global retry_timer
    print("Kjører periodisk sjekk for mislykkede opplastinger...")
    with retry_lock:
        try:
            with open(FAILED_UPLOADS_PATH, 'r') as f:
                failed = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            failed = [] # Ingen fil, ingenting å gjøre

        if not failed:
            print("Ingen mislykkede opplastinger å prøve på nytt.")
        else:
            # Lag en kopi for å iterere over, slik at vi kan endre originalen
            remaining_uploads = list(failed)
            for item in list(remaining_uploads):
                # Sjekk om filene fortsatt eksisterer
                if not (os.path.exists(item['video_path']) and os.path.exists(item['thumbnail_path'])):
                    print(f"Filer for '{item['title']}' mangler, fjerner fra gjenopprettingslisten.")
                    remaining_uploads.remove(item)
                    continue

                try:
                    print(f"Prøver å laste opp '{item['title']}' på nytt...")
                    # Kall upload_to_youtube. Den vil fjerne filer og status ved suksess.
                    upload_to_youtube(item['video_path'], item['thumbnail_path'], item['title'], item['playlist_date_str'])
                    # Hvis vi kommer hit uten feil, var opplastingen vellykket.
                    print(f"Vellykket gjenopplasting av '{item['title']}'.")
                    remaining_uploads.remove(item)
                except Exception as e:
                    print(f"Gjenopplasting av '{item['title']}' feilet igjen: {e}")

            # Skriv den oppdaterte listen (kun de som fortsatt feiler) tilbake til filen,
            # kun hvis det er nødvendig.
            if failed != remaining_uploads:
                with open(FAILED_UPLOADS_PATH, 'w') as f:
                    json.dump(remaining_uploads, f, indent=2)

    # Sett opp neste forsøk om en time
    retry_timer = threading.Timer(3600, retry_failed_uploads)
    retry_timer.start()

def record_video(song):
    global RECORDING, RECORD_PROC, CURRENT_SONG
    # Vent til snapshot er ferdig
    while snapshot_lock.locked():
        time.sleep(0.1)

    RECORDING = True

    # Definer filstier
    safe_name = "".join(c for c in song if c.isalnum() or c in (' ', '_', '-')).rstrip()
    dest_video = f"static/{safe_name}.mp4"
    dest_thumbnail = f"static/{safe_name}.png"

    # Ta opp video direkte til endelig fil
    cmd = [
        "rpicam-vid",
        "-t", "0", # Kjør til stoppet manuelt
        "-o", dest_video,
        "--width", "1280",
        "--height", "720",
        "--framerate", "30",
        "--mode", "2304:1296", # Velger 2x2 binned mode for full vidvinkel
        "--codec", "libav",
        "--libav-format", "mp4",
        "--nopreview",
        "--flush", # Tvinger skriving til disk for hver ramme
        # --- Parametere for lydopptak ---
        # IKKE bruk --audio-flagget. Det aktiveres automatisk av parameterne under.
        "--libav-audio",
        "--audio-device", "plughw:2",
        "--audio-codec", "aac",
        "--audio-source", "alsa", # Forteller libav å fange lyd fra ALSA
        "--audio-channels", "1" # Legg til eksplisitt antall kanaler (juster til 1 for mono mic)
        # "--libav-audio",
        # "--audio-device", "plughw:2",
        # "--audio-device", "plughw:1", # OPPDATERT: Endre '1' til kortnummeret fra 'arecord -l'
        # "--audio-codec", "aac",
        # "--audio-source", "alsa", # Forteller libav å fange lyd fra ALSA
        # "--audio-channels", "1" # Legg til eksplisitt antall kanaler (juster til 1 for mono mic)
    ]

    RECORD_PROC = subprocess.Popen(cmd, stderr=subprocess.PIPE, preexec_fn=os.setsid)
    # Vent på at prosessen skal avsluttes og hent stderr for feilsøking
    _, err = RECORD_PROC.communicate()

    # Sjekk om opptaket faktisk ble laget
    return_code = RECORD_PROC.returncode
    video_exists = os.path.exists(dest_video)
    video_size = os.path.getsize(dest_video) if video_exists else 0

    # Hvis det feilet, skriv ut den faktiske feilmeldingen fra rpicam-vid til loggen
    if return_code != 0 and err:
        print("--- rpicam-vid FEILMELDING ---")
        print(err.decode(errors='ignore'))
        print("-----------------------------")

    # Gi filsystemet et øyeblikk til å fullføre skriving etter at prosessen er stoppet.
    if return_code != 0:
        time.sleep(1)

    # Feilhåndtering: Sjekk om videoen ble laget korrekt
    # Når vi stopper med SIGINT, er returkoden ofte ikke 0.
    # Vi anser det som en feil KUN hvis filen ikke finnes eller er tom.
    # En positiv returkode alene er ikke lenger en feil, siden det er forventet ved stopp.
    if not video_exists or video_size == 0:
        print(f"Opptak feilet eller resulterte i en tom fil. Kode: {return_code}")
        if video_exists: os.remove(dest_video) # Slett tom/korrupt fil
        RECORDING = False # Sett til false før vi avslutter tråden
        RECORD_PROC = None
        return # Avslutt tråden

    # Lag miniatyrbilde
    make_splash(song, dest_thumbnail)

    # Hent dato for spilleliste FØR opplastingstråden starter
    script_dir = os.path.dirname(os.path.abspath(__file__))
    with open(os.path.join(script_dir, COLORS_PATH), 'r') as f:
        color_data = json.load(f)
        playlist_date_str = color_data.get("last_updated", time.strftime("%Y-%m-%d"))

    # Start opplasting i en egen tråd for å ikke blokkere
    # Legg til i status-ordboken før tråden starter
    UPLOAD_STATUS[dest_video] = {'title': song, 'status': 'Venter...'}
    upload_thread = threading.Thread(target=upload_to_youtube, args=(dest_video, dest_thumbnail, song, playlist_date_str))
    upload_thread.start()

    # Først NÅ er vi helt ferdige.
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

    # Standardiser dataen før den sendes til klienten
    # Dette gjør frontend-koden enklere og mer robust.
    processed_songs = []
    for song in songs:
        processed_songs.append({
            "number": song["number"],
            "title": song["name"], # Bruk "name" som kilde for "title"
            "filename": f"{song['number']:02d}-{song['name'].replace(' ', '_')}.txt", # Lag et filnavn
            "active": song.get("active", False) # Send med active-status
        })
    return jsonify(processed_songs)

@app.route("/start", methods=["POST"])
def start():
    global RECORDING, CURRENT_SONG
    if RECORDING:
        return jsonify({"status": "already recording"})
    data = request.get_json()
    filename = data.get("filename")
    title = data.get("title", filename) # Bruk filename som fallback
    CURRENT_SONG = title
    t = threading.Thread(target=record_video, args=(title,))
    t.start()
    return jsonify({"status": "started"})

@app.route("/stop", methods=["POST"])
def stop():
    global RECORD_PROC, RECORDING, CURRENT_SONG
    if RECORD_PROC and RECORDING:
        # Bruk os.killpg for å sende signalet til hele prosessgruppen.
        # Dette er en mer robust måte å terminere prosessen på.
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
    """Returnerer en liste over opplastingsfeil."""
    return jsonify(UPLOAD_ERRORS)

@app.route("/clear_error", methods=["POST"])
def clear_error():
    """Fjerner en spesifikk feilmelding fra listen."""
    data = request.get_json()
    error_index = data.get("index")
    if error_index is not None and 0 <= error_index < len(UPLOAD_ERRORS):
        UPLOAD_ERRORS.pop(error_index)
    return jsonify({"status": "ok"})

@app.route("/upload_status")
def get_upload_status():
    """Returnerer en liste over pågående opplastinger og aktivt opptak."""
    statuses = []
    # Legg til aktivt opptak først i listen hvis det finnes
    if RECORDING and CURRENT_SONG:
        statuses.append({'title': CURRENT_SONG, 'status': 'Tar opp...'})
    statuses.extend(list(UPLOAD_STATUS.values()))
    return jsonify(statuses)

@app.route("/static/<path:path>")
def static_files(path):
    return send_from_directory("static", path)

@app.route("/reboot", methods=["POST"])
def reboot():
    """Starter Raspberry Pi på nytt."""
    print("Mottok forespørsel om omstart...")
    # Kjører kommandoen i en egen tråd for å la serveren svare før den restarter.
    def do_reboot():
        time.sleep(1)
        subprocess.run(["sudo", "reboot"])
    threading.Thread(target=do_reboot).start()
    return jsonify({"status": "rebooting"})

@app.route("/shutdown", methods=["POST"])
def shutdown():
    """Slår av Raspberry Pi."""
    print("Mottok forespørsel om avslutning...")
    # Kjører kommandoen i en egen tråd for å la serveren svare før den slår seg av.
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
            "--mode", "2304:1296", # Velger 2x2 binned mode for full vidvinkel
            "--nopreview"
        ], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL) # Omdirigerer output til null
        return send_from_directory("static", "snapshot.jpg")
    except Exception:
        return ("", 404)
    finally:
        snapshot_lock.release()

def make_splash(songname, splash_path, width=1280, height=720):
    # Hent dagens aktive farge
    try:
        with open(COLORS_PATH, 'r') as f:
            color_data = json.load(f)
            active_index = color_data.get("active_index", 0)
            colors = color_data.get("colors", ["#000000"])
            background_color_hex = colors[active_index]
    except (FileNotFoundError, IndexError):
        background_color_hex = "#000000" # Fallback til svart

    # Bestem tekstfarge basert på bakgrunnens lysstyrke for best lesbarhet
    # Konverter hex til RGB
    h = background_color_hex.lstrip('#')
    r, g, b = tuple(int(h[i:i+2], 16) for i in (0, 2, 4))
    # Beregn lysstyrke (luminance)
    luminance = (0.299 * r + 0.587 * g + 0.114 * b) / 255
    if luminance > 0.5:
        text_color = "#000000" # Svart tekst på lys bakgrunn
    else:
        text_color = "#FFFFFF" # Hvit tekst på mørk bakgrunn

    img = Image.new('RGB', (width, height), color=background_color_hex)
    draw = ImageDraw.Draw(img)

    # Hent fonter
    try:
        font_title = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 80)
        font_date = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 40)
    except Exception:
        font_title = ImageFont.load_default()
        font_date = ImageFont.load_default()

    # Tekst for sangtittel
    title_text = songname
    try:
        title_bbox = draw.textbbox((0, 0), title_text, font=font_title)
        title_w, title_h = title_bbox[2] - title_bbox[0], title_bbox[3] - title_bbox[1]
    except AttributeError:
        title_w, title_h = draw.textsize(title_text, font=font_title)

    # Plasser sangtittel litt over midten
    draw.text(
        ((width - title_w) / 2, (height / 2) - title_h),
        title_text,
        font=font_title,
        fill=text_color
    )

    # Formater og plasser dato og tid sentrert under tittelen
    # Format: 5. oktober kl 21:45
    now = time.localtime()
    norwegian_months = {
        1: "januar", 2: "februar", 3: "mars", 4: "april",
        5: "mai", 6: "juni", 7: "juli", 8: "august",
        9: "september", 10: "oktober", 11: "november", 12: "desember"
    }
    # Hent norsk månedsnavn fra vår egen liste
    month_name = norwegian_months.get(now.tm_mon, "")
    # Bygg datostrengen manuelt for å sikre norsk format
    date_text = f"{now.tm_mday}. {month_name} kl {now.tm_hour:02d}:{now.tm_min:02d}"

    try:
        date_bbox = draw.textbbox((0, 0), date_text, font=font_date)
        date_w, _ = date_bbox[2] - date_bbox[0], date_bbox[3] - date_bbox[1]
    except AttributeError:
        date_w, _ = draw.textsize(date_text, font=font_date)

    draw.text(
        ((width - date_w) / 2, (height / 2) + 20), # Plasserer under midten
        date_text,
        font=font_date,
        fill=text_color
    )

    img.save(splash_path, format="PNG")

# --- Applikasjonsoppstart ---
# Denne koden kjøres én gang når Gunicorn starter worker-prosessen.
print("Applikasjon starter: Kjører engangsoppsett...")
update_active_color()
retry_failed_uploads() # Starter den periodiske sjekken for mislykkede opplastinger

if __name__ == "__main__":
    os.makedirs("static", exist_ok=True)
    # Gjør server-loggen mindre "bråkete" ved å kun vise feilmeldinger
    # Deaktiver standard-loggeren for å unngå en strøm av GET-requests i terminalen.
    logging.getLogger('werkzeug').disabled = True
    app.run(host="0.0.0.0", port=5000, debug=True)