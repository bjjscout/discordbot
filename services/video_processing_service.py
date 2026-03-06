"""
Video Processing Service

FastAPI service that handles video downloading, reformatting, and subtitle burning.
This runs as a separate container to keep the main bot lightweight.

Usage:
    docker build -f Dockerfile.video-processing -t vidmaker3-video-processing .
    docker run -p 8003:8003 vidmaker3-video-processing

API Endpoints:
    POST /video/download - Download video from URL
    POST /video/reformat - Reformat video (reel, landscape, square)
    POST /video/burn-subtitles - Burn subtitles into video
    GET /health - Health check
"""

import os
import tempfile
import logging
from pathlib import Path
from typing import Optional, Tuple
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, UploadFile, File
from fastapi.responses import FileResponse
from pydantic import BaseModel
import uvicorn
import boto3
from botocore.client import Config

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Configure R2 client
def get_r2_client():
    """Get R2 S3 client"""
    return boto3.client(
        's3',
        endpoint_url=os.getenv('R2_ENDPOINT_URL'),
        aws_access_key_id=os.getenv('R2_ACCESS_KEY_ID'),
        aws_secret_access_key=os.getenv('R2_SECRET_ACCESS_KEY'),
        region_name=os.getenv('R2_REGION', 'auto'),
        config=Config(s3={'addressing_style': 'virtual'})
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup and shutdown events"""
    logger.info("Starting video processing service...")
    yield
    logger.info("Shutting down video processing service...")


app = FastAPI(
    title="Vidmaker3 Video Processing Service",
    description="Video processing API (download, reformat, burn subtitles)",
    version="1.0.0",
    lifespan=lifespan
)


# Request/Response models
class DownloadRequest(BaseModel):
    url: str
    output_format: str = "mp4"


class ReformatRequest(BaseModel):
    video_url: str
    format: str  # "reel", "landscape", "square"
    output_path: Optional[str] = None


class BurnSubtitlesRequest(BaseModel):
    video_url: str
    subtitle_url: str
    output_path: Optional[str] = None


@app.get("/health")
async def health_check():
    """Health check endpoint"""
    return {"status": "healthy", "service": "video-processing"}


@app.post("/video/download")
async def download_video(request: DownloadRequest):
    """
    Download video from URL.
    
    Supports YouTube, social media, direct URLs.
    """
    try:
        logger.info(f"Downloading: {request.url}")
        
        temp_dir = Path("/app/temp")
        temp_dir.mkdir(parents=True, exist_ok=True)
        
        # Use yt-dlp for downloading
        import yt_dlp
        
        output_path = temp_dir / f"video_{os.urandom(8).hex()}.{request.output_format}"
        
        ydl_opts = {
            'format': 'bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best',
            'outtmpl': str(output_path),
            'quiet': False,
        }
        
        # Check if it's a direct URL or needs yt-dlp
        if request.url.startswith(('http://', 'https://')):
            if 'youtube.com' in request.url or 'youtu.be' in request.url:
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    ydl.download([request.url])
            else:
                # Direct download
                import requests
                r = requests.get(request.url, stream=True)
                r.raise_for_status()
                
                with open(output_path, 'wb') as f:
                    for chunk in r.iter_content(chunk_size=8192):
                        f.write(chunk)
        
        # Get video info
        duration = get_video_duration(output_path)
        
        return {
            "temp_path": str(output_path),
            "duration": duration,
            "download_url": f"/video/download/{output_path.name}"
        }
        
    except Exception as e:
        logger.error(f"Download failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/video/reformat")
async def reformat_video(request: ReformatRequest):
    """
    Reformat video to different aspect ratios:
    - reel: 9:16 (vertical/Story/Reel)
    - landscape: 16:9 (YouTube)
    - square: 1:1 (Instagram feed)
    """
    try:
        logger.info(f"Reformatting: {request.video_url} -> {request.format}")
        
        temp_dir = Path("/app/temp")
        temp_dir.mkdir(parents=True, exist_ok=True)
        
        # Download video first
        import yt_dlp
        input_path = temp_dir / f"input_{os.urandom(8).hex()}.mp4"
        
        ydl_opts = {'outtmpl': str(input_path), 'quiet': True}
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([request.video_url])
        
        # Process with moviepy
        from moviepy.editor import VideoFileClip
        
        with VideoFileClip(str(input_path)) as clip:
            # Get dimensions
            w, h = clip.size
            duration = clip.duration
            
            # Calculate target dimensions
            if request.format == "reel":
                # 9:16 vertical
                target_w = 1080
                target_h = 1920
            elif request.format == "landscape":
                # 16:9
                target_w = 1920
                target_h = 1080
            elif request.format == "square":
                # 1:1
                target_w = 1080
                target_h = 1080
            else:
                raise HTTPException(status_code=400, detail="Invalid format")
            
            # Calculate crop coordinates (center crop)
            if w / h > target_w / target_h:
                # Video is wider, crop sides
                new_h = h
                new_w = int(h * target_w / target_h)
                x1 = (w - new_w) // 2
                y1 = 0
            else:
                # Video is taller, crop top/bottom
                new_w = w
                new_h = int(w / (target_w / target_h))
                x1 = 0
                y1 = (h - new_h) // 2
            
            # Crop and resize
            cropped = clip.crop(x1=x1, y1=y1, width=new_w, height=new_h)
            resized = cropped.resize(newsize=(target_w, target_h))
            
            output_path = request.output_path or str(temp_dir / f"output_{os.urandom(8).hex()}.mp4")
            resized.write_videofile(output_path, codec='libx264', audio_codec='aac', verbose=False, logger=None)
        
        # Cleanup input
        input_path.unlink()
        
        return {
            "output_path": output_path,
            "duration": duration,
            "dimensions": (target_w, target_h)
        }
        
    except Exception as e:
        logger.error(f"Reformat failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/video/burn-subtitles")
async def burn_subtitles(request: BurnSubtitlesRequest):
    """
    Burn subtitles into video.
    
    Supports SRT and ASS subtitle files.
    """
    try:
        logger.info(f"Burning subtitles: {request.video_url}")
        
        temp_dir = Path("/app/temp")
        temp_dir.mkdir(parents=True, exist_ok=True)
        
        # Download video and subtitles
        import yt_dlp
        import requests
        
        input_path = temp_dir / f"input_{os.urandom(8).hex()}.mp4"
        subtitle_path = temp_dir / f"subs_{os.urandom(8).hex()}.srt"
        
        # Download video
        ydl_opts = {'outtmpl': str(input_path), 'quiet': True}
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([request.video_url])
        
        # Download subtitles
        r = requests.get(request.subtitle_url)
        r.raise_for_status()
        subtitle_path.write_bytes(r.content)
        
        # Burn subtitles using ffmpeg
        import subprocess
        
        output_path = request.output_path or str(temp_dir / f"burned_{os.urandom(8).hex()}.mp4")
        
        cmd = [
            'ffmpeg', '-i', str(input_path),
            '-vf', f"subtitles='{subtitle_path}'",
            '-c:a', 'copy',
            '-y', output_path
        ]
        
        subprocess.run(cmd, check=True, capture_output=True)
        
        # Cleanup
        input_path.unlink()
        subtitle_path.unlink()
        
        return {
            "output_path": output_path
        }
        
    except Exception as e:
        logger.error(f"Burn subtitles failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


def get_video_duration(path: Path) -> float:
    """Get video duration using moviepy"""
    from moviepy.editor import VideoFileClip
    with VideoFileClip(str(path)) as clip:
        return clip.duration


@app.get("/video/download/{filename}")
async def get_video(filename: str):
    """Serve downloaded video file"""
    path = Path(f"/app/temp/{filename}")
    if not path.exists():
        raise HTTPException(status_code=404, detail="File not found")
    return FileResponse(path)


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8003)
