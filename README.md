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

Render runs the complete application as one Docker web service. The same FastAPI server serves the frontend and the yt-dlp/FFmpeg backend, so the frontend uses same-origin API requests and has no Replit dependency.

- Render service: `video-downloader`
- Health check: `/health`
- Metadata: `POST /api/info`
- Video: `POST /api/download`
- MP3: `POST /api/audio`

The included `render.yaml` and `Dockerfile` are the deployment configuration. Connect this GitHub repository to Render and deploy it as a Docker Web Service.


## X / Twitter and XHamster

The backend has site-specific extraction fallbacks for X / Twitter and XHamster:

- X / Twitter URLs are normalized across `x.com`, `twitter.com` and mobile variants, then tried through yt-dlp's supported GraphQL, syndication and legacy APIs, with an IPv4 retry for network-level 403 failures.
- XHamster extraction retries the normal request, IPv4, browser impersonation and IPv4 + browser impersonation paths when supported by the installed yt-dlp networking dependencies.
- The repository uses a pre-release yt-dlp build so fresh deployments receive current extractor fixes.

These fallbacks do not bypass private/protected accounts, login requirements, DRM, paywalls or age/region restrictions. yt-dlp documents that X and XHamster can return authentication, anti-bot, geo or age-verification failures even on current builds. citeturn1search0turn0search2turn0search5
