import os
from google_auth_oauthlib.flow import InstalledAppFlow
import google.auth.transport.requests

def main():
    """
    Kjører en engangs-autentisering for YouTube og lagrer resultatet.
    """
    script_dir = os.path.dirname(os.path.abspath(__file__))
    CLIENT_SECRETS_FILE = os.path.join(script_dir, "client_secrets.json")
    SCOPES = ["https://www.googleapis.com/auth/youtube"] # Endret for å kunne håndtere spillelister

    if not os.path.exists(CLIENT_SECRETS_FILE):
        print(f"FEIL: Finner ikke '{CLIENT_SECRETS_FILE}'.")
        print("Last ned filen fra Google API Console og plasser den i samme mappe.")
        return

    # Bruk "Out-Of-Band" (OOB) flyten, som er den mest robuste for manuell autentisering.
    flow = InstalledAppFlow.from_client_secrets_file(
        CLIENT_SECRETS_FILE,
        scopes=SCOPES,
        redirect_uri='urn:ietf:wg:oauth:2.0:oob'
    )

    print("\n--- YouTube Autentisering (Engangsprosess) ---\n")
    auth_url, _ = flow.authorization_url(prompt='consent')
    print(f"1. Gå til denne URL-en i nettleseren din:\n{auth_url}\n")
    print("2. Logg inn, godkjenn tilgangen, og kopier autorisasjonskoden du får.")
    code = input("3. Lim inn koden her og trykk Enter: ")

    flow.fetch_token(code=code)
    credentials = flow.credentials

    # Lagre credentials for fremtidig bruk
    with open('token.json', 'w') as token:
        token.write(credentials.to_json())

    print("\nAutentisering vellykket! En 'token.json'-fil er nå lagret.")
    print("Du kan nå starte hovedapplikasjonen (observe.py).")

if __name__ == "__main__":
    main()