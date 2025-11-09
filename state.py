# state.py
import threading

RECORDING = False
RECORD_PROC = None
CURRENT_SONG = None
UPLOAD_ERRORS = []
UPLOAD_STATUS = {}

snapshot_lock = threading.Lock()
retry_lock = threading.Lock()