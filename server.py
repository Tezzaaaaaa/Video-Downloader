import hashlib
import html
import ipaddress
import os
import re
import socket
import subprocess
import tempfile
import time
import zipfile
from pathlib import Path
from urllib.parse import urlparse

import yt_dlp
from curl_cffi import requests as curl_requests
from fastapi import BackgroundTasks, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel, field_validator

BASE_DIR = Path(__file__).resolve().parent
INDEX_FILE = BASE_DIR / "Index.html"
MAX_FILE_SIZE = 2 * 1024 * 1024 * 1024
DOWNLOAD_PROGRESS: dict[str, dict] = {}


def update_download_progress(download_id: str | None, **values) -> None:
    if not download_id:
        return
    current = DOWNLOAD_PROGRESS.get(download_id, {})
    current.update(values)
    DOWNLOAD_PROGRESS[download_id] = current


def yt_dlp_progress_hook(download_id: str):
    def update(data: dict) -> None:
        status = data.get("status")
        if status == "downloading":
            downloaded = data.get("downloaded_bytes") or 0
            total = data.get("total_bytes") or data.get("total_bytes_estimate")
            percent = min(99, downloaded * 100 / total) if total else None
            update_download_progress(
                download_id,
                stage="downloading",
                percent=round(percent, 1) if percent is not None else None,
                downloaded_bytes=downloaded,
                total_bytes=total,
                speed=data.get("speed"),
                eta=data.get("eta"),
                message="Downloading video…" if percent is not None else "Downloading video… (size estimate unavailable)",
            )
        elif status == "finished":
            update_download_progress(
                download_id,
                stage="processing",
                percent=99,
                speed=None,
                eta=None,
                message="Download received; processing and merging video…",
            )
    return update

app = FastAPI(title="Video Downloader", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://tezzaaaaaa.github.io"],
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Content-Type"],
)


class XProfileRequest(BaseModel):
    url: str
    limit: int = 100
    download_id: str | None = None

    @field_validator("download_id")
    @classmethod
    def validate_profile_download_id(cls, value: str | None) -> str | None:
        if value is not None and not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", value):
            raise ValueError("Invalid download progress ID.")
        return value

    @field_validator("limit")
    @classmethod
    def validate_profile_limit(cls, value: int) -> int:
        if not 1 <= value <= 200:
            raise ValueError("Choose between 1 and 200 videos.")
        return value

    @field_validator("url")
    @classmethod
    def validate_profile_url(cls, value: str) -> str:
        value = value.strip()
        parsed = urlparse(value)
        hostname = (parsed.hostname or "").lower().rstrip(".")
        if parsed.scheme not in {"http", "https"} or hostname not in {
            "x.com", "www.x.com", "mobile.x.com",
            "twitter.com", "www.twitter.com", "mobile.twitter.com",
        }:
            raise ValueError("Enter an X profile URL.")
        parts = [part for part in parsed.path.split("/") if part]
        if parts and parts[-1].lower() in {"media", "videos"}:
            parts.pop()
        if len(parts) != 1 or not re.fullmatch(r"@?[A-Za-z0-9_]{1,15}", parts[0]):
            raise ValueError("Enter a profile URL, not an individual post.")
        username = parts[0].lstrip("@")
        return f"https://x.com/{username}/media"


class URLRequest(BaseModel):
    url: str
    format_id: str | None = None
    download_id: str | None = None

    @field_validator("download_id")
    @classmethod
    def validate_download_id(cls, value: str | None) -> str | None:
        if value is not None and not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", value):
            raise ValueError("Invalid download progress ID.")
        return value

    @field_validator("url")
    @classmethod
    def validate_url(cls, value: str) -> str:
        value = value.strip()
        parsed = urlparse(value)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("Enter a valid http or https URL.")
        if len(value) > 4096:
            raise ValueError("URL is too long.")
        return value


def validate_public_url(url: str) -> None:
    parsed = urlparse(url)
    hostname = parsed.hostname
    if not hostname:
        raise HTTPException(status_code=400, detail="Invalid URL.")

    lowered = hostname.lower().rstrip(".")
    if lowered in {"localhost", "localhost.localdomain"} or lowered.endswith(".local"):
        raise HTTPException(status_code=400, detail="Local URLs are not allowed.")

    try:
        addresses = {info[4][0] for info in socket.getaddrinfo(lowered, None)}
    except socket.gaierror:
        raise HTTPException(status_code=400, detail="The host could not be resolved.")

    for address in addresses:
        ip = ipaddress.ip_address(address)
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
            raise HTTPException(status_code=400, detail="Private or local network URLs are not allowed.")


def validate_format_id(format_id: str | None) -> str | None:
    if format_id is None:
        return None
    if not re.fullmatch(r"[A-Za-z0-9._-]{1,80}", format_id):
        raise HTTPException(status_code=400, detail="Invalid format.")
    return format_id


def base_options() -> dict:
    options = {
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "socket_timeout": 20,
        "retries": 3,
        "fragment_retries": 3,
        "max_filesize": MAX_FILE_SIZE,
    }
    cookies_file = os.environ.get("YTDLP_COOKIES_FILE", "").strip()
    if cookies_file:
        cookie_path = Path(cookies_file)
        if not cookie_path.is_file():
            raise RuntimeError("The configured yt-dlp cookies file does not exist or is not readable.")
        options["cookiefile"] = str(cookie_path)
    return options


def is_x_url(url: str) -> bool:
    hostname = (urlparse(url).hostname or "").lower().rstrip(".")
    return hostname in {
        "x.com",
        "www.x.com",
        "mobile.x.com",
        "twitter.com",
        "www.twitter.com",
        "mobile.twitter.com",
    }


def is_xhamster_url(url: str) -> bool:
    hostname = (urlparse(url).hostname or "").lower().rstrip(".")
    return hostname == "xhamster.com" or hostname.endswith(".xhamster.com")


def xhamster_media_host(hostname: str) -> bool:
    host = hostname.lower().rstrip(".")
    return host.endswith(".xhcdn.com") or host == "xhcdn.com" or host.endswith(".xhamster.com")


def xhamster_source_id(url: str) -> str:
    path = urlparse(url).path
    marker = "/speed=0/"
    stable_path = path.split(marker, 1)[1] if marker in path else path.rsplit("/", 1)[-1]
    return "xh_" + hashlib.sha256(stable_path.encode("utf-8")).hexdigest()[:20]


def xhamster_fetch_page(url: str) -> str:
    response = curl_requests.get(
        url,
        impersonate="chrome",
        headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/140 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.8",
        },
        timeout=20,
        allow_redirects=True,
    )
    response.raise_for_status()
    return response.text


def xhamster_title_from_page(url: str) -> str:
    try:
        page = html.unescape(xhamster_fetch_page(url))
        match = re.search(r"<title[^>]*>(.*?)</title>", page, re.I | re.S)
        if match:
            title = re.sub(r"\s+", " ", match.group(1)).strip()
            if title:
                return re.sub(r"\s*[|–-]\s*xHamster.*$", "", title, flags=re.I).strip() or title
    except Exception:
        pass
    return "XHamster video"


def xhamster_public_sources(url: str) -> list[dict]:
    page = html.unescape(xhamster_fetch_page(url)).replace("\\/", "/")
    raw_urls = re.findall(r'''https?://[^"'<>\s]+''', page)

    sources = []
    seen = set()

    for raw in raw_urls:
        candidate = raw.rstrip("\\")
        parsed = urlparse(candidate)
        if not parsed.hostname or not xhamster_media_host(parsed.hostname):
            continue

        lowered = candidate.lower()
        if ".mp4" not in lowered and ".m3u8" not in lowered:
            continue

        if candidate in seen:
            continue
        seen.add(candidate)

        quality_match = re.search(r"(?<!\d)(\d{3,4})p(?:\.|$)", urlparse(candidate).path, re.I)
        height = int(quality_match.group(1)) if quality_match else None
        is_hls = ".m3u8" in lowered
        sources.append({
            "id": xhamster_source_id(candidate),
            "url": candidate,
            "type": "hls" if is_hls else "video",
            "ext": "mp4",
            "height": height,
            "fps": None,
            "hasAudio": True,
            "size": None,
        })

    sources.sort(key=lambda item: (-(item["height"] or 0), item["type"] != "video", item["url"]))
    return sources


def xhamster_source_for_id(url: str, source_id: str) -> dict | None:
    for source in xhamster_public_sources(url):
        if source["id"] == source_id:
            return source
    return None


def xhamster_download_source(source: dict, page_url: str, output: Path, download_id: str | None = None) -> None:
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/140 Safari/537.36",
        "Referer": page_url,
    }

    if source["type"] == "hls":
        update_download_progress(
            download_id,
            stage="downloading",
            percent=None,
            downloaded_bytes=0,
            total_bytes=None,
            speed=None,
            eta=None,
            message="Downloading live stream; this source does not expose a reliable percentage…",
        )
        header_text = "".join(f"{key}: {value}\\r\\n" for key, value in headers.items())
        subprocess.run(
            [
                "ffmpeg", "-hide_banner", "-loglevel", "error",
                "-headers", header_text,
                "-i", source["url"],
                "-c", "copy",
                "-movflags", "+faststart",
                "-y", str(output),
            ],
            check=True,
            timeout=300,
        )
        update_download_progress(
            download_id,
            stage="processing",
            percent=99,
            speed=None,
            eta=None,
            message="Stream downloaded; checking the video…",
        )
        return

    response = curl_requests.get(
        source["url"],
        impersonate="chrome",
        headers=headers,
        stream=True,
        timeout=30,
        allow_redirects=True,
    )
    try:
        response.raise_for_status()
        total = 0
        content_length = int(response.headers.get("content-length") or 0)
        started = time.monotonic()
        with output.open("wb") as handle:
            for chunk in response.iter_content(chunk_size=1024 * 1024):
                if not chunk:
                    continue
                total += len(chunk)
                if total > MAX_FILE_SIZE:
                    raise HTTPException(status_code=413, detail="The resulting file is larger than the 2 GB limit.")
                handle.write(chunk)
                elapsed = max(time.monotonic() - started, 0.1)
                speed = total / elapsed
                percent = min(99, total * 100 / content_length) if content_length else None
                update_download_progress(
                    download_id,
                    stage="downloading",
                    percent=round(percent, 1) if percent is not None else None,
                    downloaded_bytes=total,
                    total_bytes=content_length or None,
                    speed=speed,
                    eta=(content_length - total) / speed if content_length and speed > 0 else None,
                    message="Downloading video stream…",
                )
    finally:
        response.close()


def x_url_candidates(url: str) -> list[str]:
    if not is_x_url(url):
        return [url]

    parsed = urlparse(url)
    path = parsed.path
    query = f"?{parsed.query}" if parsed.query else ""
    return [
        f"https://x.com{path}{query}",
        f"https://twitter.com{path}{query}",
    ]


def extraction_attempts(url: str) -> list[dict]:
    base = base_options()

    if is_x_url(url):
        return [
            {**base, "extractor_args": {"twitter": {"api": "graphql"}}},
            {**base, "extractor_args": {"twitter": {"api": "syndication"}}},
            {**base, "extractor_args": {"twitter": {"api": "legacy"}}},
            {**base, "source_address": "0.0.0.0", "extractor_args": {"twitter": {"api": "graphql"}}},
        ]

    if is_xhamster_url(url):
        return [
            base,
            {**base, "source_address": "0.0.0.0"},
            {**base, "impersonate": "chrome"},
            {**base, "source_address": "0.0.0.0", "impersonate": "chrome"},
        ]

    return [base]


def extract_info_with_fallbacks(url: str) -> dict:
    last_error: Exception | None = None

    for candidate in x_url_candidates(url):
        for options in extraction_attempts(candidate):
            try:
                options["skip_download"] = True
                with yt_dlp.YoutubeDL(options) as ydl:
                    return ydl.extract_info(candidate, download=False)
            except Exception as exc:
                last_error = exc

    if last_error is not None:
        raise last_error
    raise RuntimeError("Unable to extract media.")


def download_with_fallbacks(url: str, options: dict, download_id: str | None = None) -> None:
    if download_id:
        update_download_progress(
            download_id,
            stage="starting",
            percent=0,
            downloaded_bytes=0,
            total_bytes=None,
            speed=None,
            eta=None,
            message="Preparing source download…",
        )
    last_error: Exception | None = None

    for candidate in x_url_candidates(url):
        for attempt in extraction_attempts(candidate):
            attempt.update(options)
            if download_id:
                attempt["progress_hooks"] = [yt_dlp_progress_hook(download_id)]
            try:
                with yt_dlp.YoutubeDL(attempt) as ydl:
                    ydl.download([candidate])
                    return
            except Exception as exc:
                last_error = exc

    if last_error is not None:
        raise last_error
    raise RuntimeError("Unable to download media.")


def extract(url: str) -> dict:
    validate_public_url(url)

    if is_xhamster_url(url) and not os.environ.get("YTDLP_COOKIES_FILE", "").strip():
        try:
            sources = xhamster_public_sources(url)
            if sources:
                return {
                    "title": xhamster_title_from_page(url),
                    "thumbnail": None,
                    "duration": None,
                    "uploader": None,
                    "extractor": "XHamster source extractor",
                    "formats": [
                        {key: source[key] for key in ("id", "type", "ext", "height", "fps", "hasAudio", "size")}
                        for source in sources[:100]
                    ],
                }

            page = html.unescape(xhamster_fetch_page(url))
            if re.search(r"verify your age.{0,100}logged in|logged in.{0,100}verify your age", page, re.I | re.S):
                raise HTTPException(
                    status_code=422,
                    detail="XHamster requires age verification and login for this video. Verify access on XHamster, then try again.",
                )
        except HTTPException:
            raise
        except Exception:
            pass

    try:
        info = extract_info_with_fallbacks(url)
    except Exception as exc:
        message = str(exc).strip().splitlines()[-1] if str(exc).strip() else "Unable to read this media URL."
        raise HTTPException(status_code=422, detail=message[:500])

    formats = []
    for item in info.get("formats") or []:
        format_id = str(item.get("format_id", ""))
        if not re.fullmatch(r"[A-Za-z0-9._-]{1,80}", format_id):
            continue

        has_video = item.get("vcodec") not in {None, "none"}
        has_audio = item.get("acodec") not in {None, "none"}
        if not (has_video or has_audio):
            continue

        height = item.get("height")
        fps = item.get("fps")
        ext = item.get("ext") or ""
        size = item.get("filesize") or item.get("filesize_approx")

        formats.append({
            "id": format_id,
            "type": "video" if has_video else "audio",
            "ext": ext,
            "height": height,
            "fps": fps,
            "hasAudio": has_audio,
            "size": size,
        })

    formats.sort(key=lambda item: (
        0 if item["type"] == "video" else 1,
        -(item["height"] or 0),
        item["ext"],
    ))

    return {
        "title": info.get("title") or "Downloaded media",
        "thumbnail": info.get("thumbnail"),
        "duration": info.get("duration"),
        "uploader": info.get("uploader") or info.get("channel"),
        "extractor": info.get("extractor_key") or info.get("extractor"),
        "formats": formats[:100],
    }


@app.get("/", response_class=HTMLResponse)
def home() -> HTMLResponse:
    return HTMLResponse(INDEX_FILE.read_text(encoding="utf-8"))


@app.get("/health")
def health() -> dict:
    return {"ok": True}


@app.get("/api/progress/{download_id}")
def download_progress(download_id: str) -> dict:
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", download_id):
        raise HTTPException(status_code=400, detail="Invalid download progress ID.")
    return DOWNLOAD_PROGRESS.get(download_id, {
        "stage": "starting",
        "percent": 0,
        "message": "Preparing download…",
    })


def x_profile_video_urls(profile_url: str, limit: int) -> list[str]:
    command = [
        "gallery-dl", "--no-input", "--quiet", "--no-colors", "--get-urls",
        "--post-range", f"1-{limit}",\n        "--range", f"1-{limit}",
        "--filter", "extension in ('mp4', 'm3u8')",
        "-o", "extractor.twitter.include=media",
        "-o", "extractor.twitter.videos=true",
    ]
    cookies_file = os.environ.get("YTDLP_COOKIES_FILE", "").strip()
    if cookies_file:
        cookie_path = Path(cookies_file)
        if not cookie_path.is_file():
            raise RuntimeError("The configured X cookies file does not exist or is not readable.")
        command.extend(["--cookies", str(cookie_path)])
    command.append(profile_url)

    result = subprocess.run(
        command,
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )
    if result.returncode != 0 and not result.stdout.strip():
        message = result.stderr.strip().splitlines()[-1] if result.stderr.strip() else "X profile extraction failed."
        raise RuntimeError(message[:500])

    urls = []
    seen = set()
    for line in result.stdout.splitlines():
        candidate = line.strip()
        parsed = urlparse(candidate)
        host = (parsed.hostname or "").lower().rstrip(".")
        if parsed.scheme not in {"http", "https"} or host not in {"video.twimg.com", "video-cf.twimg.com"}:
            continue
        if not re.search(r"\.(?:mp4|m3u8)(?:$|\?)", parsed.path, re.I):
            continue
        if candidate not in seen:
            seen.add(candidate)
            urls.append(candidate)
        if len(urls) >= limit:
            break
    return urls


def download_x_profile_archive(request: XProfileRequest, background_tasks: BackgroundTasks) -> FileResponse:
    validate_public_url(request.url)
    update_download_progress(
        request.download_id,
        stage="scanning",
        percent=0,
        downloaded_bytes=0,
        total_bytes=None,
        speed=None,
        eta=None,
        message="Finding videos on the X profile…",
    )
    temp_dir = Path(tempfile.mkdtemp(prefix="video-downloader-x-profile-"))
    archive = temp_dir / "x-profile-videos.zip"
    try:
        urls = x_profile_video_urls(request.url, request.limit)
        if not urls:
            raise HTTPException(
                status_code=422,
                detail="No downloadable public videos were found. The profile may require login, or X may be blocking access.",
            )

        total_bytes = 0
        completed = 0
        with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=1) as bundle:
            for index, media_url in enumerate(urls, start=1):
                parsed = urlparse(media_url)
                is_hls = parsed.path.lower().endswith(".m3u8")
                suffix = ".mp4" if is_hls else Path(parsed.path).suffix.lower()
                if suffix != ".mp4":
                    suffix = ".mp4"
                output = temp_dir / f"video-{index:03d}.mp4"

                if is_hls:
                    subprocess.run(
                        [
                            "ffmpeg", "-hide_banner", "-loglevel", "error",
                            "-headers", "Referer: https://x.com/\r\n",
                            "-i", media_url, "-c", "copy", "-movflags", "+faststart",
                            "-y", str(output),
                        ],
                        check=True,
                        timeout=300,
                        capture_output=True,
                        text=True,
                    )
                else:
                    response = curl_requests.get(
                        media_url,
                        impersonate="chrome",
                        headers={"Referer": "https://x.com/", "User-Agent": "Mozilla/5.0"},
                        stream=True,
                        timeout=30,
                        allow_redirects=True,
                    )
                    try:
                        response.raise_for_status()
                        with output.open("wb") as handle:
                            for chunk in response.iter_content(chunk_size=1024 * 1024):
                                if not chunk:
                                    continue
                                total_bytes += len(chunk)
                                if total_bytes > MAX_FILE_SIZE:
                                    raise HTTPException(status_code=413, detail="The profile archive exceeds the 2 GB limit.")
                                handle.write(chunk)
                    finally:
                        response.close()

                if not output.is_file() or output.stat().st_size == 0:
                    output.unlink(missing_ok=True)
                    continue
                total_bytes += output.stat().st_size if suffix == ".m3u8" else 0
                if total_bytes > MAX_FILE_SIZE:
                    raise HTTPException(status_code=413, detail="The profile archive exceeds the 2 GB limit.")
                bundle.write(output, arcname=output.name)
                output.unlink(missing_ok=True)
                completed += 1
                update_download_progress(
                    request.download_id,
                    stage="downloading",
                    percent=round(completed * 100 / len(urls), 1),
                    downloaded_bytes=completed,
                    total_bytes=len(urls),
                    speed=None,
                    eta=None,
                    message=f"Downloaded {completed} of {len(urls)} videos…",
                )

        if completed == 0 or not archive.exists() or archive.stat().st_size == 0:
            raise HTTPException(status_code=422, detail="X returned no downloadable video files.")
        update_download_progress(
            request.download_id,
            stage="ready",
            percent=100,
            speed=None,
            eta=0,
            message=f"{completed} videos ready; sending ZIP archive…",
        )
    except HTTPException:
        cleanup(temp_dir)
        raise
    except Exception as exc:
        cleanup(temp_dir)
        message = str(exc).strip().splitlines()[-1] if str(exc).strip() else "X profile download failed."
        raise HTTPException(status_code=422, detail=message[:500])

    background_tasks.add_task(cleanup, temp_dir)
    return FileResponse(
        archive,
        media_type="application/zip",
        filename="x-profile-videos.zip",
        background=background_tasks,
    )


@app.post("/api/x-profile/download")
def download_x_profile(request: XProfileRequest, background_tasks: BackgroundTasks) -> FileResponse:
    return download_x_profile_archive(request, background_tasks)


@app.post("/api/info")
def media_info(request: URLRequest) -> dict:
    return extract(request.url)


@app.post("/api/download")
def download_media(request: URLRequest, background_tasks: BackgroundTasks) -> FileResponse:
    validate_public_url(request.url)

    if is_xhamster_url(request.url):
        # Use the same public source list shown by /api/info. The default
        # Download video action must work even when the user does not select
        # an individual quality button.
        try:
            sources = xhamster_public_sources(request.url)
            if request.format_id and request.format_id.startswith("xh_"):
                source = next((item for item in sources if item["id"] == request.format_id), None)
            else:
                source = next((item for item in sources if item["type"] == "video"), None)
                if source is None and sources:
                    source = sources[0]
            if source is None:
                raise HTTPException(status_code=422, detail="No public XHamster video stream was found. The video may require access verification or may not expose a downloadable source.")
        except HTTPException:
            raise
        except Exception as exc:
            message = str(exc).strip().splitlines()[-1] if str(exc).strip() else "Unable to read the XHamster video source."
            raise HTTPException(status_code=422, detail=message[:500])

        update_download_progress(
            request.download_id,
            stage="starting",
            percent=0,
            downloaded_bytes=0,
            total_bytes=None,
            speed=None,
            eta=None,
            message="Preparing video stream…",
        )
        temp_dir = Path(tempfile.mkdtemp(prefix="video-downloader-xh-"))
        output = temp_dir / "download.mp4"
        try:
            xhamster_download_source(source, request.url, output, request.download_id)
            if not output.exists() or output.stat().st_size == 0:
                raise HTTPException(status_code=422, detail="XHamster returned an empty media file.")
            if output.stat().st_size > MAX_FILE_SIZE:
                raise HTTPException(status_code=413, detail="The resulting file is larger than the 2 GB limit.")
            update_download_progress(request.download_id, stage="ready", percent=100, speed=None, eta=0, message="Video ready; sending it to your browser…")
        except HTTPException:
            cleanup(temp_dir)
            raise
        except Exception as exc:
            cleanup(temp_dir)
            message = str(exc).strip().splitlines()[-1] if str(exc).strip() else "XHamster download failed."
            raise HTTPException(status_code=422, detail=message[:500])

        background_tasks.add_task(cleanup, temp_dir)
        return FileResponse(output, media_type="video/mp4", filename="download.mp4", background=background_tasks)

    temp_dir = Path(tempfile.mkdtemp(prefix="video-downloader-"))
    output_template = str(temp_dir / "%(title).120s-%(id)s.%(ext)s")

    options = base_options()
    format_id = validate_format_id(request.format_id)
    options.update({
        "outtmpl": output_template,
        "format": f"{format_id}+bestaudio/{format_id}" if format_id else "bestvideo*+bestaudio/best",
        "merge_output_format": "mp4",
        "restrictfilenames": True,
        "windowsfilenames": True,
        "overwrites": False,
    })

    try:
        download_with_fallbacks(request.url, options, request.download_id)
    except Exception as exc:
        for child in temp_dir.iterdir():
            child.unlink(missing_ok=True)
        temp_dir.rmdir()
        message = str(exc).strip().splitlines()[-1] if str(exc).strip() else "Download failed."
        raise HTTPException(status_code=422, detail=message[:500])

    files = [path for path in temp_dir.iterdir() if path.is_file()]
    if not files:
        temp_dir.rmdir()
        raise HTTPException(status_code=422, detail="No downloadable media was produced.")

    output = max(files, key=lambda path: path.stat().st_mtime)
    if output.stat().st_size > MAX_FILE_SIZE:
        output.unlink(missing_ok=True)
        temp_dir.rmdir()
        raise HTTPException(status_code=413, detail="The resulting file is larger than the 2 GB limit.")

    update_download_progress(request.download_id, stage="ready", percent=100, speed=None, eta=0, message="Video ready; sending it to your browser…")
    filename = re.sub(r'[^A-Za-z0-9._ -]+', "_", output.name).strip() or "download.mp4"
    background_tasks.add_task(cleanup, temp_dir)

    return FileResponse(
        output,
        media_type="application/octet-stream",
        filename=filename,
        background=background_tasks,
    )


@app.post("/api/audio")
def download_audio(request: URLRequest, background_tasks: BackgroundTasks) -> FileResponse:
    validate_public_url(request.url)

    temp_dir = Path(tempfile.mkdtemp(prefix="video-downloader-audio-"))
    output_template = str(temp_dir / "%(title).120s-%(id)s.%(ext)s")

    options = base_options()
    format_id = validate_format_id(request.format_id)
    options.update({
        "outtmpl": output_template,
        "format": format_id or "bestaudio/best",
        "restrictfilenames": True,
        "windowsfilenames": True,
        "postprocessors": [{
            "key": "FFmpegExtractAudio",
            "preferredcodec": "mp3",
            "preferredquality": "192",
        }],
    })

    try:
        download_with_fallbacks(request.url, options)
    except Exception as exc:
        for child in temp_dir.iterdir():
            child.unlink(missing_ok=True)
        temp_dir.rmdir()
        message = str(exc).strip().splitlines()[-1] if str(exc).strip() else "Audio download failed."
        raise HTTPException(status_code=422, detail=message[:500])

    files = [path for path in temp_dir.iterdir() if path.is_file()]
    if not files:
        temp_dir.rmdir()
        raise HTTPException(status_code=422, detail="No audio file was produced.")

    output = max(files, key=lambda path: path.stat().st_mtime)
    if output.stat().st_size > MAX_FILE_SIZE:
        output.unlink(missing_ok=True)
        temp_dir.rmdir()
        raise HTTPException(status_code=413, detail="The resulting file is larger than the 2 GB limit.")

    update_download_progress(request.download_id, stage="ready", percent=100, speed=None, eta=0, message="Audio ready; sending it to your browser…")
    filename = re.sub(r'[^A-Za-z0-9._ -]+', "_", output.name).strip() or "audio.mp3"
    background_tasks.add_task(cleanup, temp_dir)

    return FileResponse(
        output,
        media_type="audio/mpeg",
        filename=filename,
        background=background_tasks,
    )


def cleanup(directory: Path) -> None:
    for child in directory.glob("*"):
        child.unlink(missing_ok=True)
    directory.rmdir()


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("server:app", host="0.0.0.0", port=int(os.environ.get("PORT", "8000")))
