import os
from google_auth_oauthlib.flow import InstalledAppFlow
import google.auth.transport.requests

def main():
    """
    Runs a one-time authentication for YouTube and saves the result.
    """
    script_dir = os.path.dirname(os.path.abspath(__file__))
    CLIENT_SECRETS_FILE = os.path.join(script_dir, "client_secrets.json")
    SCOPES = ["https://www.googleapis.com/auth/youtube"] # Scope for handling playlists

    if not os.path.exists(CLIENT_SECRETS_FILE):
        print(f"ERROR: Cannot find '{CLIENT_SECRETS_FILE}'.")
        print("Download the file from the Google API Console and place it in the same directory.")
        return

    # Use the "Out-Of-Band" (OOB) flow, which is the most robust for manual authentication.
    flow = InstalledAppFlow.from_client_secrets_file(
        CLIENT_SECRETS_FILE,
        scopes=SCOPES,
        redirect_uri='urn:ietf:wg:oauth:2.0:oob'
    )

    print("\n--- YouTube Authentication (One-Time Process) ---\n")
    auth_url, _ = flow.authorization_url(prompt='consent')
    print(f"1. Go to this URL in your browser:\n{auth_url}\n")
    print("2. Log in, grant access, and copy the authorization code you receive.")
    code = input("3. Paste the code here and press Enter: ")

    flow.fetch_token(code=code)
    credentials = flow.credentials

    # Save credentials for future use
    with open('token.json', 'w') as token:
        token.write(credentials.to_json())

    print("\nAuthentication successful! A 'token.json' file has been saved.")
    print("You can now start the main application (app.py).")

if __name__ == "__main__":
    main()