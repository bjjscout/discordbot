"""
Face Detection Service - S3FD API

FastAPI service that provides face detection using S3FD.
This runs as a separate container to keep the main bot lightweight.

Usage:
    docker build -f Dockerfile.face-detection -t vidmaker3-face-detection .
    docker run -p 8002:8002 vidmaker3-face-detection

API Endpoints:
    POST /detect-faces - Detect faces in video
    GET /health - Health check
"""

import os
import logging
from pathlib import Path
from typing import List, Optional, Any
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import uvicorn
import torch
import cv2
import numpy as np
from PIL import Image

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Import S3FD detector
DETECTOR = None


def get_detector():
    """Get or create S3FD detector"""
    global DETECTOR
    
    if DETECTOR is None:
        try:
            from face_detector_s3fd import S3FDDetector
            device = 'cuda' if torch.cuda.is_available() else 'cpu'
            DETECTOR = S3FDDetector(device=device)
            logger.info(f"S3FD detector loaded on {device}")
        except ImportError:
            logger.warning("S3FD detector not available, using OpenCV fallback")
            DETECTOR = None
    
    return DETECTOR


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup and shutdown events"""
    logger.info("Starting face detection service...")
    get_detector()
    yield
    logger.info("Shutting down face detection service...")


app = FastAPI(
    title="Vidmaker3 Face Detection Service",
    description="S3FD-based face detection API",
    version="1.0.0",
    lifespan=lifespan
)


# Request/Response models
class DetectFacesRequest(BaseModel):
    video_url: str
    sample_rate: int = 4  # Process every Nth frame
    confidence_threshold: float = 0.5


class FaceDetectionResponse(BaseModel):
    frame_bboxes: List[Optional[List[List[float]]]]
    total_frames: int
    faces_found: bool


@app.get("/health")
async def health_check():
    """Health check endpoint"""
    return {"status": "healthy", "service": "face-detection"}


@app.post("/detect-faces", response_model=FaceDetectionResponse)
async def detect_faces(request: DetectFacesRequest):
    """
    Detect faces in a video.
    
    Downloads the video, processes frames at the specified sample rate,
    returns bounding boxes for each frame.
    """
    try:
        logger.info(f"Detecting faces in: {request.video_url}")
        
        temp_dir = Path("/app/temp")
        temp_dir.mkdir(parents=True, exist_ok=True)
        
        # Download video
        video_path = await download_video(request.video_url, temp_dir)
        
        # Open video
        cap = cv2.VideoCapture(str(video_path))
        
        if not cap.isOpened():
            raise HTTPException(status_code=400, detail="Could not open video")
        
        total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        fps = cap.get(cv2.CAP_PROP_FPS)
        
        # Process frames at sample rate
        frame_bboxes = []
        frame_idx = 0
        
        detector = get_detector()
        
        while True:
            ret, frame = cap.read()
            if not ret:
                break
            
            # Process every Nth frame
            if frame_idx % request.sample_rate == 0:
                if detector is not None:
                    # Use S3FD detector
                    bboxes = detector.detect_faces(frame, threshold=request.confidence_threshold)
                    if len(bboxes) > 0:
                        # Convert to [x1, y1, x2, y2] format
                        frame_bboxes.append(bboxes.tolist())
                    else:
                        frame_bboxes.append(None)
                else:
                    # Fallback to OpenCV Haar Cascade
                    bboxes = detect_faces_opencv(frame)
                    frame_bboxes.append(bboxes if bboxes else None)
            else:
                frame_bboxes.append(None)
            
            frame_idx += 1
        
        cap.release()
        
        # Cleanup
        video_path.unlink()
        
        faces_found = any(b is not None for b in frame_bboxes)
        
        logger.info(f"Face detection complete: {faces_found}")
        
        return FaceDetectionResponse(
            frame_bboxes=frame_bboxes,
            total_frames=total_frames,
            faces_found=faces_found
        )
        
    except Exception as e:
        logger.error(f"Face detection failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


async def download_video(url: str, temp_dir: Path) -> Path:
    """Download video to temp file"""
    import yt_dlp
    
    output_path = temp_dir / f"detect_{os.urandom(8).hex()}.mp4"
    
    ydl_opts = {
        'format': 'bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best',
        'outtmpl': str(output_path),
        'quiet': True,
    }
    
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        ydl.download([url])
    
    return output_path


def detect_faces_opencv(frame: np.ndarray) -> Optional[List[List[float]]]:
    """Fallback face detection using OpenCV Haar Cascade"""
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    
    # Load pre-trained face cascade
    face_cascade = cv2.CascadeClassifier(
        cv2.data.haarcascades + 'haarcascade_frontalface_default.xml'
    )
    
    faces = face_cascade.detectMultiScale(
        gray,
        scaleFactor=1.1,
        minNeighbors=5,
        minSize=(30, 30)
    )
    
    if len(faces) > 0:
        # Convert to [x1, y1, x2, y2] format
        bboxes = []
        for (x, y, w, h) in faces:
            bboxes.append([float(x), float(y), float(x + w), float(y + h)])
        return bboxes
    
    return None


@app.post("/dynamic-crop")
async def dynamic_crop(request: DetectFacesRequest):
    """
    Apply dynamic cropping based on face detection.
    
    Keeps the face centered in the frame.
    """
    try:
        logger.info(f"Dynamic cropping: {request.video_url}")
        
        temp_dir = Path("/app/temp")
        temp_dir.mkdir(parents=True, exist_ok=True)
        
        # Download video
        video_path = await download_video(request.video_url, temp_dir)
        
        # Get face detections
        response = await detect_faces(request)
        
        if not response.faces_found:
            raise HTTPException(status_code=400, detail="No faces found in video")
        
        # Apply dynamic cropping
        from dynamic_cropper import apply_dynamic_reel_crop
        
        output_path = str(temp_dir / f"cropped_{os.urandom(8).hex()}.mp4")
        
        await apply_dynamic_reel_crop(
            str(video_path),
            output_path,
            response.frame_bboxes,
            request.sample_rate
        )
        
        return {
            "output_path": output_path,
            "faces_found": response.faces_found
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Dynamic crop failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8002)
