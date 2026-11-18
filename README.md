# Video Downloader

A web-based media downloader built with **yt-dlp**, **FFmpeg** and a FastAPI backend.

**Repository:** https://github.com/Tezzaaaaaa/Video-Downloader  
**GitHub Pages frontend/demo:** https://tezzaaaaaa.github.io/Video-Downloader/

> **Important:** GitHub Pages serves only static files. It cannot run the Python, yt-dlp or FFmpeg backend. The Cloudflare Containers deployment defined in this repository is intended to serve the frontend and API together from one origin.

## How it works

1. Paste a public media URL.
2. The backend attempts to extract the media title, thumbnail and available formats.
3. Choose a supported video or MP3 output.
4. The backend downloads and post-processes the media, then returns the result.

Supported websites depend on the installed yt-dlp extractors and can change as websites change. A successful extraction attempt is the practical compatibility test.

## Features

- Web interface served by the application backend
- Metadata extraction and available-format selection
- Video downloads and MP3 audio extraction
- yt-dlp with FFmpeg/FFprobe post-processing
- Browser-compatible request handling, including optional `curl_cffi` support
- X profile video downloads bundled into a ZIP archive (up to 200 videos per request)\n- Site-specific retry paths for individual X/Twitter posts and XHamster when supported by the installed dependencies
- Health-check and API endpoints for deployment monitoring

## Requirements

- Python 3.10 or newer
- `yt-dlp[default,curl-cffi]`\n- `gallery-dl` (X profile media discovery)
- FFmpeg and FFprobe
- A supported JavaScript runtime for full YouTube support; the included Dockerfile installs Deno

## Run locally

1. Create a virtual environment:

   ```bash
   python -m venv .venv
   ```

2. Activate it:

   ```bash
   source .venv/bin/activate
   ```

3. Install dependencies:

   ```bash
   pip install -r requirements.txt
   ```

4. Start the server:

   ```bash
   python server.py
   ```

5. Open http://localhost:8000

## Docker

Build the image:

```bash
docker build -t video-downloader .
```

Run the container:

```bash
docker run --rm -p 8000:8000 video-downloader
```

## Deploy to Cloudflare

This repository includes a Cloudflare Workers + Containers deployment:

- `wrangler.jsonc` configures the Worker and its container-backed Durable Object.
- `src/index.ts` routes requests through Cloudflare to the FastAPI container.
- The existing `Dockerfile` installs Python dependencies, FFmpeg and Deno.
- The container needs outbound internet access so yt-dlp can retrieve public media.
- The frontend and API share the same origin, so the browser does not need a separately configured API hostname.

### Deploy with Wrangler

Prerequisites: a Cloudflare account with Workers/Containers access, Node.js, npm, and a running Docker-compatible engine.

```bash
npm install
npx wrangler login
npm run deploy
```

Follow Wrangler's prompts to authenticate and deploy. The first container deployment can take several minutes to provision. Check the Worker deployment and container status/logs in the Cloudflare dashboard, then test the deployed `/health` endpoint.

Cloudflare Containers usage is billed under the Workers Paid plan; review current [Cloudflare Containers pricing](https://developers.cloudflare.com/containers/platform/pricing/) before deploying. Usage above included allowances can incur additional charges.

### Deploy from GitHub

Alternatively, connect this repository to Cloudflare Workers Builds in the Cloudflare dashboard. Set the production branch to `main` and the deploy command to:

```bash
npx wrangler deploy
```

Keep `wrangler.jsonc` and `Dockerfile` in the configured build root. Verify the deployment and container startup in Cloudflare after the build succeeds.

### API endpoints

- Health check: `GET /health`
- Metadata: `POST /api/info`
- Video: `POST /api/download`
- MP3 audio: `POST /api/audio`
- X profile ZIP: `POST /api/x-profile/download` (JSON: `url`, optional `limit` up to 200, optional `download_id`)

A successful GitHub Pages deployment alone does **not** mean the download backend is running. The Cloudflare Worker URL is the full-stack deployment endpoint once deployed; the GitHub Pages URL remains a static frontend and cannot execute the backend itself.

## Alternative deployment

The repository also retains `render.yaml` for a Docker-capable Render deployment. Do not assume that a Render service exists or is running unless its URL and health endpoint have been verified.

## X / Twitter and XHamster

The backend includes site-specific extraction fallback paths:

- X/Twitter URLs are normalized across `x.com`, `twitter.com` and mobile variants, then attempted through yt-dlp-supported extraction paths, with network retries where configured.
- XHamster extraction can retry ordinary requests, IPv4 and browser-impersonation paths when the installed yt-dlp networking dependencies support them.

These retries are best-effort compatibility measures. They do not guarantee extraction; platforms may change their APIs or impose authentication, anti-bot, geographic or age-verification restrictions.

## Privacy, permissions and restrictions

Use the downloader only for media you own or are permitted to download. It does not bypass DRM, private accounts, paywalls, login-only content or other access restrictions. When deploying a public instance, review the host's resource limits, access controls and applicable laws.

## Branding

The `favicon.svg` file is used for the browser-tab icon and the header logo.

## Project components

- `server.py` — FastAPI web server and API
- `Index.html` — frontend page
- `Dockerfile` — container image configuration
- `wrangler.jsonc` — Cloudflare Worker and Container configuration
- `src/index.ts` — Cloudflare request routing and container startup
- `package.json` — Wrangler deployment commands
- `render.yaml` — alternative Render deployment configuration
