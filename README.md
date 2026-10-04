# Video Downloader

A NoAdsDL-style universal downloader powered by **yt-dlp** and **FFmpeg**.

## How it works

1. Paste a public media URL.
2. The server extracts the title, thumbnail and available formats.
3. Choose video or MP3.
4. The server downloads and post-processes the media, then returns the file.

Supported platforms depend on the current yt-dlp extractors. Site support can change as platforms change; the reliable compatibility test is attempting extraction.

The server includes yt-dlp's **curl_cffi** support and browser-compatible request handling for sources that reject ordinary HTTP/TLS fingerprints with errors such as HTTP 403.

## Requirements

- Python 3.10+
- yt-dlp[default,curl-cffi]
- FFmpeg + FFprobe
- A supported JavaScript runtime for full YouTube support; the included Dockerfile installs Deno.

## Run locally

1. Create a virtual environment: `python -m venv .venv`
2. Activate it: `source .venv/bin/activate`
3. Install dependencies: `pip install -r requirements.txt`
4. Start the server: `python server.py`
5. Open `http://localhost:8000`

## Docker

Build: `docker build -t video-downloader .`

Run: `docker run --rm -p 8000:8000 video-downloader`

## Important deployment note

GitHub Pages can host the frontend but cannot execute the Python/yt-dlp/FFmpeg backend. Deploy the repository as a server/container on a host that can run Docker or Python, then serve Index.html through server.py.

The downloader does not bypass DRM, private accounts, paywalls, login-only content or access restrictions. Use it only for media you are permitted to download.
## Live architecture

The GitHub Pages URL is the public frontend. GitHub Pages cannot execute Python, yt-dlp or FFmpeg, so the frontend is configured to call the deployed FastAPI backend instead.

- Frontend: https://tezzaaaaaa.github.io/Video-Downloader/
- Backend: https://yellowish-pointless-snake--tereroaafamasag.replit.app
- Backend health check: `/health`
- Metadata: `POST /api/info`
- Video: `POST /api/download`
- MP3: `POST /api/audio`

The backend is responsible for yt-dlp extraction and FFmpeg processing. The frontend must not be expected to run those components from GitHub Pages.
