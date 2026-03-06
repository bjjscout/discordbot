"""
Service Client Wrappers

Provides client interfaces for calling external microservices
(transcription, face detection, video processing) with:
- Circuit breaker protection
- Automatic retries
- Proper error handling
- Health checks

These clients allow you to call microservices that can be
deployed separately from the main bot.
"""

import asyncio
import logging
import aiohttp
from typing import Optional, Dict, Any, List
from dataclasses import dataclass
from enum import Enum

from .circuit_breaker import circuit_breaker, CircuitOpenError
from .config import get_settings

logger = logging.getLogger(__name__)


class ProcessingStatus(Enum):
    """Processing job status"""
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass
class TranscriptionResult:
    """Transcription result"""
    text: str
    segments: List[Dict[str, Any]]
    srt: Optional[str] = None
    ass: Optional[str] = None
    duration: Optional[float] = None


@dataclass
class FaceDetectionResult:
    """Face detection result"""
    frame_bboxes: List[Optional[List[List[float]]]]
    total_frames: int
    faces_found: bool


@dataclass
class VideoProcessingResult:
    """Video processing result"""
    output_path: str
    duration: float
    dimensions: tuple


class BaseServiceClient:
    """Base class for service clients"""
    
    def __init__(
        self,
        base_url: str,
        timeout: int = 300,
        max_retries: int = 3
    ):
        self.base_url = base_url.rstrip("/")
        self.timeout = aiohttp.ClientTimeout(total=timeout)
        self.max_retries = max_retries
        self._session: Optional[aiohttp.ClientSession] = None
    
    async def _get_session(self) -> aiohttp.ClientSession:
        """Get or create aiohttp session"""
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(timeout=self.timeout)
        return self._session
    
    async def close(self) -> None:
        """Close the HTTP session"""
        if self._session and not self._session.closed:
            await self._session.close()
    
    async def _request(
        self,
        method: str,
        endpoint: str,
        **kwargs
    ) -> Dict[str, Any]:
        """Make HTTP request with retries"""
        url = f"{self.base_url}/{endpoint.lstrip('/')}"
        session = await self._get_session()
        
        for attempt in range(self.max_retries):
            try:
                async with session.request(method, url, **kwargs) as response:
                    response.raise_for_status()
                    return await response.json()
            except aiohttp.ClientError as e:
                if attempt == self.max_retries - 1:
                    logger.error(f"Request failed after {self.max_retries} attempts: {e}")
                    raise
                await asyncio.sleep(attempt + 1)  # Exponential backoff
        
        raise RuntimeError("Should not reach here")
    
    async def get_health(self) -> bool:
        """Check if service is healthy"""
        try:
            result = await self._request("GET", "/health")
            return result.get("status") == "healthy"
        except Exception:
            return False


class TranscriptionClient(BaseServiceClient):
    """
    Client for the Transcription Service.
    
    Handles:
    - Synchronous transcription for short videos
    - Async transcription for long videos
    - Multiple output formats (SRT, ASS, TXT)
    """
    
    def __init__(self, base_url: str = None):
        # Default to local service or environment variable
        if base_url is None:
            settings = get_settings()
            base_url = os.getenv(
                "TRANSCRIPTION_SERVICE_URL",
                "http://transcription:8001"
            )
        super().__init__(base_url, timeout=1800)  # 30 min timeout
    
    @circuit_breaker(name="transcription", failure_threshold=3, timeout=60)
    async def transcribe(
        self,
        video_url: str,
        language: Optional[str] = None,
        translate: bool = True,
        output_format: str = "ass"
    ) -> TranscriptionResult:
        """
        Transcribe a video synchronously.
        
        Args:
            video_url: URL of the video to transcribe
            language: Language code (auto-detect if None)
            translate: Translate to English
            output_format: Output format (srt, ass, txt)
            
        Returns:
            TranscriptionResult with text and formats
        """
        logger.info(f"Transcribing video: {video_url}")
        
        payload = {
            "video_url": video_url,
            "language": language,
            "translate": translate,
            "output_format": output_format
        }
        
        result = await self._request("POST", "/transcribe", json=payload)
        
        return TranscriptionResult(
            text=result.get("text", ""),
            segments=result.get("segments", []),
            srt=result.get("srt"),
            ass=result.get("ass"),
            duration=result.get("duration")
        )
    
    async def transcribe_async(
        self,
        video_url: str,
        language: Optional[str] = None,
        translate: bool = True,
        output_format: str = "ass"
    ) -> str:
        """
        Start async transcription and return job ID.
        
        Use get_transcription_status() to check progress.
        """
        payload = {
            "video_url": video_url,
            "language": language,
            "translate": translate,
            "output_format": output_format
        }
        
        result = await self._request("POST", "/transcribe/async", json=payload)
        return result.get("job_id")
    
    async def get_transcription_status(
        self,
        job_id: str
    ) -> ProcessingStatus:
        """Check transcription job status"""
        result = await self._request("GET", f"/job/{job_id}")
        return ProcessingStatus(result.get("status"))


class FaceDetectionClient(BaseServiceClient):
    """
    Client for the Face Detection Service.
    
    Handles:
    - Face detection in videos
    - Batch frame processing
    - Configurable detection parameters
    """
    
    def __init__(self, base_url: str = None):
        if base_url is None:
            base_url = os.getenv(
                "FACE_DETECTION_SERVICE_URL",
                "http://face-detect:8002"
            )
        super().__init__(base_url, timeout=600)  # 10 min timeout
    
    @circuit_breaker(name="face-detection", failure_threshold=3, timeout=60)
    async def detect_faces(
        self,
        video_url: str,
        sample_rate: int = 4,
        confidence_threshold: float = 0.5
    ) -> FaceDetectionResult:
        """
        Detect faces in a video.
        
        Args:
            video_url: URL of the video
            sample_rate: Process every Nth frame
            confidence_threshold: Minimum confidence for detection
            
        Returns:
            FaceDetectionResult with bounding boxes per frame
        """
        logger.info(f"Detecting faces in video: {video_url}")
        
        payload = {
            "video_url": video_url,
            "sample_rate": sample_rate,
            "confidence_threshold": confidence_threshold
        }
        
        result = await self._request("POST", "/detect-faces", json=payload)
        
        return FaceDetectionResult(
            frame_bboxes=result.get("frame_bboxes", []),
            total_frames=result.get("total_frames", 0),
            faces_found=result.get("faces_found", False)
        )
    
    async def detect_faces_batch(
        self,
        video_urls: List[str],
        **kwargs
    ) -> List[FaceDetectionResult]:
        """Detect faces in multiple videos"""
        tasks = [
            self.detect_faces(url, **kwargs) 
            for url in video_urls
        ]
        return await asyncio.gather(*tasks)


class VideoProcessingClient(BaseServiceClient):
    """
    Client for the Video Processing Service.
    
    Handles:
    - Video downloading
    - Video reformatting (reel, landscape, square)
    - Subtitle burning
    - Video composition
    """
    
    def __init__(self, base_url: str = None):
        if base_url is None:
            base_url = os.getenv(
                "VIDEO_PROCESSING_SERVICE_URL",
                "http://video-proc:8003"
            )
        super().__init__(base_url, timeout=1800)  # 30 min timeout
    
    async def download(
        self,
        url: str,
        output_format: str = "mp4"
    ) -> VideoProcessingResult:
        """Download a video"""
        payload = {
            "url": url,
            "output_format": output_format
        }
        
        result = await self._request("POST", "/video/download", json=payload)
        
        return VideoProcessingResult(
            output_path=result.get("temp_path"),
            duration=result.get("duration", 0),
            dimensions=tuple(result.get("dimensions", (0, 0)))
        )
    
    async def reformat(
        self,
        video_path: str,
        format: str,
        output_path: Optional[str] = None
    ) -> VideoProcessingResult:
        """Reformat video (reel, landscape, square)"""
        payload = {
            "video_path": video_path,
            "format": format,
            "output_path": output_path
        }
        
        result = await self._request("POST", "/video/reformat", json=payload)
        
        return VideoProcessingResult(
            output_path=result.get("output_path"),
            duration=result.get("duration", 0),
            dimensions=tuple(result.get("dimensions", (0, 0)))
        )
    
    async def burn_subtitles(
        self,
        video_path: str,
        subtitle_path: str,
        output_path: Optional[str] = None
    ) -> VideoProcessingResult:
        """Burn subtitles into video"""
        payload = {
            "video_path": video_path,
            "subtitle_path": subtitle_path,
            "output_path": output_path
        }
        
        result = await self._request("POST", "/video/burn-subtitles", json=payload)
        
        return VideoProcessingResult(
            output_path=result.get("output_path"),
            duration=result.get("duration", 0),
            dimensions=tuple(result.get("dimensions", (0, 0)))
        )


# Global client instances
_transcription_client: Optional[TranscriptionClient] = None
_face_detection_client: Optional[FaceDetectionClient] = None
_video_processing_client: Optional[VideoProcessingClient] = None


def get_transcription_client() -> TranscriptionClient:
    """Get or create transcription client"""
    global _transcription_client
    if _transcription_client is None:
        _transcription_client = TranscriptionClient()
    return _transcription_client


def get_face_detection_client() -> FaceDetectionClient:
    """Get or create face detection client"""
    global _face_detection_client
    if _face_detection_client is None:
        _face_detection_client = FaceDetectionClient()
    return _face_detection_client


def get_video_processing_client() -> VideoProcessingClient:
    """Get or create video processing client"""
    global _video_processing_client
    if _video_processing_client is None:
        _video_processing_client = VideoProcessingClient()
    return _video_processing_client


# Import os at module level
import os
