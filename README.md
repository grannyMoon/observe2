# observe2 - Automated Rehearsal Recorder

observe2 is a Flask-based web application designed to run on a Raspberry Pi. It provides a simple, mobile-friendly web interface to record rehearsal takes using a Pi camera module. After a recording is stopped, it automatically generates a custom thumbnail and uploads the video to a designated, date-specific YouTube playlist.

![Web Interface Screenshot](https://i.imgur.com/your-screenshot-url.png) <!-- It's a good idea to add a screenshot of your UI here -->

## Features

*   **Simple Web Interface**: Control recording from any device on your local network.
*   **Live Camera Preview**: See what the camera sees directly in your browser.
*   **Automatic YouTube Upload**: Videos are automatically uploaded to a private YouTube playlist. A new playlist is created for each day (e.g., "Rehearsal 2025-10-27").
*   **Custom Thumbnails**: A unique splash screen is generated for each video, featuring the song title and a timestamp.
*   **Robust Error Handling**: Failed uploads are automatically retried every hour, ensuring no video is lost due to network issues.
*   **Headless Operation**: Designed to run as a `systemd` service, starting automatically on boot and running reliably in the background.
*   **System Controls**: Reboot or shut down the Raspberry Pi safely from the web UI.
*   **Dynamic Configuration**: Song lists and thumbnail colors are managed via simple JSON files.

---

## 1. Hardware Requirements

*   **Raspberry Pi**: A Model 3B+ or newer is recommended for smooth 720p video encoding.
*   **Raspberry Pi Camera Module**: Tested with Camera Module 3 Wide.
*   **(Optional) USB Microphone**: For capturing audio with your video.
*   **SD Card**: With a fresh installation of Raspberry Pi OS.
*   **Power Supply**: A reliable power supply for your Raspberry Pi.

---

## 2. Software Setup

Follow these steps on your Raspberry Pi to get the application running.

### Step 2.1: Install Dependencies

First, update your system and install `git`, `nginx`, and the Python libraries needed for the camera and web server.

```bash
sudo apt update && sudo apt upgrade -y
sudo apt install git nginx python3-pip libcamera-apps -y

# Install Python packages
pip install flask gunicorn pillow google-api-python-client google-auth-httplib2 google-auth-oauthlib --break-system-packages
```

### Step 2.2: Clone the Repository

Clone this project into your home directory.

```bash
cd ~
git clone <your-repository-url> observe2
cd observe2
```

### Step 2.3: YouTube API Credentials

This application uses the YouTube Data API to upload videos. You need to get API credentials from the Google Cloud Console.

1.  Go to the Google Cloud Console.
2.  Create a new project.
3.  Enable the **"YouTube Data API v3"**.
4.  Go to "Credentials", click "Create Credentials", and select "OAuth client ID".
5.  Choose "Desktop application" as the application type.
6.  Click "Download JSON" to download your credentials file.
7.  Rename the downloaded file to `client_secrets.json` and place it inside the `Observe` directory on your Raspberry Pi.

### Step 2.4: One-Time Authentication

Now, run the authentication script. This will open a URL, ask you to log in to the Google account that owns the YouTube channel, and grant permission.

```bash
python authenticate.py
```

Follow the on-screen instructions. Paste the authorization code you receive back into the terminal. This will create a `token.json` file, which allows the application to upload videos on your behalf without needing you to log in again.

---

## 3. Configuration

### `songs.json`

This file contains the list of songs that appear in the web interface. Edit this file to match your setlist.

*   `"number"`: The song number.
*   `"name"`: The title of the song.
*   `"active"`: Set to `true` for songs in your current setlist, `false` for others.

### `colors.json`

This file manages the background colors used for the video thumbnails. The application cycles through this list, using a new color each day.

---

## 4. Running the Application

### Development Mode (for testing)

You can run the app directly for testing purposes. The web interface will be available at `http://<your-pi-ip-address>:5000`.

```bash
python app.py
```

### Production Mode (Recommended)

For reliable, headless operation, it's best to run the application as a `systemd` service using Gunicorn as the application server and Nginx as a reverse proxy. This makes the app start on boot and restart automatically if it crashes.

Detailed instructions for setting this up can be found in the user interaction history or a separate `SETUP.md` file. The key steps involve:

1.  Creating a `systemd` service file (`/etc/systemd/system/observe.service`) to manage the Gunicorn process.
2.  Creating an Nginx configuration file (`/etc/nginx/sites-available/observe`) to proxy requests from port 80 to the Gunicorn socket.
3.  Setting the correct file permissions so that Nginx can communicate with Gunicorn.

Once configured, the application will be available at `http://<your-pi-ip-address>` or `http://observe/`.

---

## 5. Troubleshooting

*   **502 Bad Gateway**: This usually means Nginx can't communicate with Gunicorn. Check that the `observe.service` is running (`sudo systemctl status observe.service`) and that file permissions are correct, especially for your home directory (`chmod 711 /home/your-user`).
*   **Recording Fails with "cannot open audio device"**: Your USB microphone is not found at the address specified in `observe.py`. Run `arecord -l` to find the correct card number and update the `--audio-device` parameter in the `record_video` function. If no microphone is connected, comment out all audio-related parameters.
*   **Recording Fails with "Invalid mode"**: The camera mode specified in `observe.py` is incorrect for your camera model. Check the `libcamera-apps` documentation for your specific camera's available modes and update the `--mode` parameter.
*   **Uploads Fail with "permission denied" or "authentication" errors**: Your `token.json` may be expired or invalid. Delete it and run `python authenticate.py` again.
*   **Thumbnails Fail to Upload**: Your YouTube account may not be verified. To upload custom thumbnails, you must verify your account at youtube.com/verify.