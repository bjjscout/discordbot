"""
Transcription Service - Whisper API

FastAPI service that provides transcription capabilities using faster-whisper.
This runs as a separate container to keep the main bot lightweight.

Usage:
    docker build -f Dockerfile.transcription -t vidmaker3-transcription .
    docker run -p 8001:8001 vidmaker3-transcription

API Endpoints:
    POST /transcribe - Transcribe a video (sync)
    POST /transcribe/async - Start async transcription
    GET /job/{job_id} - Get job status
    GET /health - Health check
"""

import os
import tempfile
import asyncio
import logging
from pathlib import Path
from typing import Optional, List, Dict, Any
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, BackgroundTasks
from fastapi.responses import JSONResponse
from pydantic import BaseModel
import uvicorn
import requests

# For transcription
from faster_whisper import WhisperModel

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Global model instance
_model: Optional[WhisperModel] = None

# Job storage (in production, use Redis or database)
_jobs: Dict[str, Dict[str, Any]] = {}


def get_model() -> WhisperModel:
    """Load and return Whisper model"""
    global _model
    
    if _model is None:
        model_size = os.getenv("MODEL_SIZE", "base")
        compute_type = os.getenv("COMPUTE_TYPE", "float16")
        
        logger.info(f"Loading Whisper model: {model_size}, compute_type: {compute_type}")
        
        # Use GPU if available, otherwise CPU
        device = "cuda" if os.getenv("USE_GPU", "true").lower() == "true" else "cpu"
        
        _model = WhisperModel(
            model_size,
            device=device,
            compute_type=compute_type
        )
        
        logger.info(f"Whisper model loaded on {device}")
    
    return _model


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup and shutdown events"""
    # Startup - preload model
    logger.info("Starting transcription service...")
    try:
        get_model()
        logger.info("Transcription service ready")
    except Exception as e:
        logger.error(f"Failed to load model: {e}")
    
    yield
    
    # Cleanup
    logger.info("Shutting down transcription service...")


app = FastAPI(
    title="Vidmaker3 Transcription Service",
    description="Whisper-based transcription API",
    version="1.0.0",
    lifespan=lifespan
)


# Request/Response models
class TranscribeRequest(BaseModel):
    video_url: str
    language: Optional[str] = None
    translate: bool = True
    output_format: str = "ass"


class TranscribeResponse(BaseModel):
    text: str
    segments: List[Dict[str, Any]]
    srt: Optional[str] = None
    ass: Optional[str] = None
    duration: Optional[float] = None


class JobResponse(BaseModel):
    job_id: str
    status: str
    result: Optional[Dict[str, Any]] = None
    error: Optional[str] = None


@app.get("/health")
async def health_check():
    """Health check endpoint"""
    return {"status": "healthy", "service": "transcription"}


@app.post("/transcribe", response_model=TranscribeResponse)
async def transcribe_video(request: TranscribeRequest):
    """
    Transcribe a video synchronously.
    
    Downloads the video, runs transcription, returns results.
    """
    try:
        logger.info(f"Transcribing: {request.video_url}")
        
        # Download video to temp file
        temp_dir = Path("/app/temp")
        temp_dir.mkdir(parents=True, exist_ok=True)
        
        video_path = await download_video(request.video_url, temp_dir)
        
        # Run transcription
        model = get_model()
        
        segments, info = model.transcribe(
            str(video_path),
            language=request.language,
            task="translate" if request.translate else "transcribe",
            beam_size=5,
            vad_filter=True
        )
        
        # Collect segments
        segments_list = []
        full_text = ""
        
        for seg in segments:
            segments_list.append({
                "start": seg.start,
                "end": seg.end,
                "text": seg.text
            })
            full_text += seg.text + " "
        
        # Generate SRT/ASS if requested
        srt = None
        ass = None
        
        if request.output_format in ("srt", "ass"):
            ass = generate_ass(segments_list, info.language)
        if request.output_format in ("srt", "ass"):
            srt = generate_srt(segments_list)
        
        # Cleanup
        try:
            video_path.unlink()
        except:
            pass
        
        logger.info(f"Transcription complete: {len(full_text)} chars")
        
        return TranscribeResponse(
            text=full_text.strip(),
            segments=segments_list,
            srt=srt,
            ass=ass,
            duration=info.duration
        )
        
    except Exception as e:
        logger.error(f"Transcription failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/transcribe/async")
async def transcribe_async(request: TranscribeRequest, background_tasks: BackgroundTasks):
    """
    Start asynchronous transcription.
    
    Returns job_id for checking status later.
    """
    import uuid
    
    job_id = str(uuid.uuid4())
    
    _jobs[job_id] = {
        "status": "pending",
        "request": request.dict(),
        "result": None,
        "error": None
    }
    
    # Schedule background task
    background_tasks.add_task(process_transcription_job, job_id)
    
    return {"job_id": job_id, "status": "pending"}


@app.get("/job/{job_id}", response_model=JobResponse)
async def get_job_status(job_id: str):
    """Get transcription job status"""
    if job_id not in _jobs:
        raise HTTPException(status_code=404, detail="Job not found")
    
    job = _jobs[job_id]
    
    return JobResponse(
        job_id=job_id,
        status=job["status"],
        result=job.get("result"),
        error=job.get("error")
    )


async def process_transcription_job(job_id: str):
    """Background task to process transcription"""
    try:
        _jobs[job_id]["status"] = "processing"
        
        job = _jobs[job_id]
        request = TranscribeRequest(**job["request"])
        
        # Download video
        temp_dir = Path("/app/temp")
        video_path = await download_video(request.video_url, temp_dir)
        
        # Transcribe
        model = get_model()
        segments, info = model.transcribe(
            str(video_path),
            language=request.language,
            task="translate" if request.translate else "transcribe"
        )
        
        # Collect results
        segments_list = []
        full_text = ""
        
        for seg in segments:
            segments_list.append({
                "start": seg.start,
                "end": seg.end,
                "text": seg.text
            })
            full_text += seg.text + " "
        
        # Save result
        _jobs[job_id]["result"] = {
            "text": full_text.strip(),
            "segments": segments_list,
            "language": info.language,
            "duration": info.duration
        }
        _jobs[job_id]["status"] = "completed"
        
        # Cleanup
        try:
            video_path.unlink()
        except:
            pass
        
    except Exception as e:
        logger.error(f"Async transcription failed: {e}")
        _jobs[job_id]["status"] = "failed"
        _jobs[job_id]["error"] = str(e)


async def download_video(url: str, temp_dir: Path) -> Path:
    """Download video to temp file"""
    import yt_dlp
    
    output_path = temp_dir / f"download_{os.urandom(8).hex()}.mp4"
    
    ydl_opts = {
        'format': 'bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best',
        'outtmpl': str(output_path),
        'quiet': True,
        'no_warnings': True,
    }
    
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        ydl.download([url])
    
    return output_path


def generate_srt(segments: List[Dict]) -> str:
    """Generate SRT format subtitles"""
    srt_output = []
    
    for i, seg in enumerate(segments, 1):
        start = format_srt_time(seg["start"])
        end = format_srt_time(seg["end"])
        
        srt_output.append(f"{i}")
        srt_output.append(f"{start} --> {end}")
        srt_output.append(seg["text"].strip())
        srt_output.append("")
    
    return "\n".join(srt_output)


def generate_ass(segments: List[Dict], language: str) -> str:
    """Generate ASS format subtitles"""
    ass_header = """[Script Info]
Title: Vidmaker3 Transcription
ScriptType: v4.00+
WrapStyle: 0
PlayResX: 1920
PlayResY: 1080
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,Arial,48,&H00FFFFFF,&H000000FF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,2,2,2,10,10,10,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    
    events = []
    
    for seg in segments:
        start = format_ass_time(seg["start"])
        end = format_ass_time(seg["end"])
        text = seg["text"].replace("\n", "\\N")
        
        events.append(f"Dialogue: 0,{start},{end},Default,,0,0,0,,{text}")
    
    return ass_header + "\n".join(events)


def format_srt_time(seconds: float) -> str:
    """Format time for SRT"""
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    millis = int((seconds % 1) * 1000)
    
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


def format_ass_time(seconds: float) -> str:
    """Format time for ASS"""
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    centisecs = int((seconds % 1) * 100)
    
    return f"{hours}:{minutes:02d}:{secs:02d}.{centisecs:02d}"


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8001)
