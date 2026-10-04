import ipaddress
import os
import re
import socket
import tempfile
from pathlib import Path
from urllib.parse import urlparse

import yt_dlp
from fastapi import BackgroundTasks, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel, field_validator

BASE_DIR = Path(__file__).resolve().parent
INDEX_FILE = BASE_DIR / "Index.html"
MAX_FILE_SIZE = 500 * 1024 * 1024

app = FastAPI(title="Video Downloader", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://tezzaaaaaa.github.io"],
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Content-Type"],
)


class URLRequest(BaseModel):
    url: str
    format_id: str | None = None

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
    return {
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "socket_timeout": 20,
        "retries": 3,
        "fragment_retries": 3,
        "max_filesize": MAX_FILE_SIZE,
    }


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


def download_with_fallbacks(url: str, options: dict) -> None:
    last_error: Exception | None = None

    for candidate in x_url_candidates(url):
        for attempt in extraction_attempts(candidate):
            attempt.update(options)
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
    options = extraction_options(url)
    options["skip_download"] = True

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


@app.post("/api/info")
def media_info(request: URLRequest) -> dict:
    return extract(request.url)


@app.post("/api/download")
def download_media(request: URLRequest, background_tasks: BackgroundTasks) -> FileResponse:
    validate_public_url(request.url)

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
        download_with_fallbacks(request.url, options)
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
        raise HTTPException(status_code=413, detail="The resulting file is larger than the 500 MB limit.")

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
        raise HTTPException(status_code=413, detail="The resulting file is larger than the 500 MB limit.")

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
