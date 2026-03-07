"""
WhisperX API Client

Provides a dedicated client for the WhisperX API at https://whisperx.jeffrey-epstein.com
Handles:
- Video downloading (via /download endpoint)
- Transcription (via /transcribe/url endpoint)
- FFmpeg operations (via /ffmpeg endpoint)
- Job polling with automatic status checking

This client enables the Discord bot to be a pure orchestrator - no local GPU/FFmpeg needed!
"""

import asyncio
import logging
from typing import Optional, Dict, Any, List
from dataclasses import dataclass
from enum import Enum
import os

logger = logging.getLogger(__name__)

# Default API URL - can be overridden via environment variable
WHISPERX_API_URL = os.getenv("WHISPERX_API_URL", "https://whisperx.jeffrey-epstein.com")


class JobStatus(Enum):
    """Job status values from the API"""
    QUEUED = "queued"
    DOWNLOADING = "downloading"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass
class TranscriptionResult:
    """Result from transcription job"""
    txt_url: str
    srt_url: str
    ass_url: str
    preview: str


@dataclass
class DownloadResult:
    """Result from download job"""
    video_url: str
    audio_url: Optional[str]
    duration: float
    width: int
    height: int
    format: str  # "landscape", "reel", "square"
    title: str


@dataclass
class FFmpegResult:
    """Result from FFmpeg job"""
    operation: str
    output_url: str
    expires_in: int = 86400


class WhisperXClient:
    """
    Client for the WhisperX API.
    
    Handles async job submission and polling for:
    - Video downloading
    - Transcription
    - FFmpeg operations (trim, reformat, burn_subtitles, overlay, loop, etc.)
    """
    
    def __init__(self, base_url: str = None, poll_interval: int = 5):
        """
        Initialize the WhisperX client.
        
        Args:
            base_url: Base URL for the API (defaults to WHISPERX_API_URL env var)
            poll_interval: Seconds between status polls (default: 5)
        """
        self.base_url = (base_url or WHISPERX_API_URL).rstrip("/")
        self.poll_interval = poll_interval
        self._session: Optional[Any] = None
    
    async def _get_session(self) -> Any:
        """Get or create aiohttp session"""
        if self._session is None or self._session.closed:
            import aiohttp
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=3600)  # 1 hour timeout for long operations
            )
        return self._session
    
    async def close(self) -> None:
        """Close the HTTP session"""
        if self._session and not self._session.closed:
            await self._session.close()
    
    async def _submit_job(self, endpoint: str, data: Dict[str, Any]) -> str:
        """
        Submit a job to the API and return the job_id.
        
        Args:
            endpoint: API endpoint (e.g., "/transcribe/url", "/download", "/ffmpeg")
            data: Form data to submit
            
        Returns:
            job_id string
        """
        session = await self._get_session()
        
        async with session.post(f"{self.base_url}{endpoint}", data=data) as response:
            response.raise_for_status()
            result = await response.json()
            
            if "job_id" not in result:
                raise ValueError(f"No job_id in response: {result}")
            
            logger.info(f"Submitted job to {endpoint}: {result['job_id']}")
            return result["job_id"]
    
    async def _poll_job(self, job_id: str) -> Dict[str, Any]:
        """
        Poll for job status.
        
        Args:
            job_id: The job ID to poll
            
        Returns:
            Full job status response
        """
        session = await self._get_session()
        
        async with session.get(f"{self.base_url}/jobs/{job_id}") as response:
            response.raise_for_status()
            return await response.json()
    
    async def _wait_for_job(self, job_id: str, progress_callback=None) -> Dict[str, Any]:
        """
        Wait for a job to complete, polling at regular intervals.
        
        Args:
            job_id: The job ID to wait for
            progress_callback: Optional async callback(status) for progress updates
            
        Returns:
            Completed job result
            
        Raises:
            Exception: If job fails
        """
        import time
        last_progress_update = 0
        
        while True:
            status = await self._poll_job(job_id)
            job_status = status.get("status")
            
            # Only send progress updates every 60 seconds to avoid spam
            if progress_callback:
                current_time = time.time()
                if current_time - last_progress_update > 60:
                    await progress_callback(job_status)
                    last_progress_update = current_time
            
            if job_status == JobStatus.COMPLETED.value:
                logger.info(f"Job {job_id} completed")
                return status
            
            elif job_status == JobStatus.FAILED.value:
                error = status.get("error", "Unknown error")
                logger.error(f"Job {job_id} failed: {error}")
                raise Exception(f"Job failed: {error}")
            
            elif job_status in [
                JobStatus.QUEUED.value,
                JobStatus.DOWNLOADING.value,
                JobStatus.PROCESSING.value
            ]:
                logger.debug(f"Job {job_id} status: {job_status}, polling again in {self.poll_interval}s")
                await asyncio.sleep(self.poll_interval)
            
            else:
                raise ValueError(f"Unknown job status: {job_status}")
    
    # ==================== DOWNLOAD ====================
    
    async def download_video(
        self,
        url: str,
        quality: str = "best",
        extract_audio: bool = False,
        progress_callback=None
    ) -> DownloadResult:
        """
        Download a video from any supported URL.
        
        Args:
            url: Video URL (YouTube, Rumble, Twitter, etc.)
            quality: Quality preference ("best", "720p", "480p", "worst")
            extract_audio: Also extract audio as MP3
            progress_callback: Optional async callback for progress updates
            
        Returns:
            DownloadResult with video details and URLs
        """
        logger.info(f"Downloading video: {url}")
        
        data = {
            "url": url,
            "quality": quality,
            "extract_audio": str(extract_audio).lower()
        }
        
        try:
            job_id = await self._submit_job("/download", data)
            logger.info(f"Download job submitted: {job_id}")
            result = await self._wait_for_job(job_id, progress_callback)
            
            logger.info(f"Download job completed: {job_id}")
            
            # Debug: print the result
            logger.debug(f"Download result keys: {result.keys() if isinstance(result, dict) else 'not a dict'}")
            
            download_result = result.get("result")
            if not download_result:
                # Check if there's an error
                error = result.get("error")
                raise Exception(f"Download failed: {error or 'Unknown error - no result returned'}")
            
            # Debug: print download_result
            logger.debug(f"Download result: {download_result}")
            
            return DownloadResult(
                video_url=download_result["video_url"],
                audio_url=download_result.get("audio_url"),
                duration=download_result["duration"],
                width=download_result["width"],
                height=download_result["height"],
                format=download_result["format"],
                title=download_result["title"]
            )
        except Exception as e:
            logger.error(f"Download error: {str(e)}")
            raise
    
    # ==================== TRANSCRIPTION ====================
    
    async def transcribe(
        self,
        video_url: str,
        model: str = "large-v3",
        language: str = None,
        task: str = "translate",
        progress_callback=None,
        # Custom font parameters for ASS subtitles
        font_name: str = None,
        font_size: int = None,
        font_color: str = None,
        font_bold: int = None,
    ) -> TranscriptionResult:
        """
        Transcribe a video/audio file.
        
        Args:
            video_url: URL to the video or audio file
            model: Whisper model ("tiny", "base", "small", "medium", "large-v2", "large-v3")
            language: Language code (auto-detect if None)
            task: "transcribe" or "translate"
            progress_callback: Optional async callback for progress updates
            font_name: Custom font name for ASS subtitles (e.g., "The Bold Font")
            font_size: Custom font size for ASS subtitles
            font_color: Custom font color (e.g., "&H00FFFFFF")
            font_bold: Font bold (0 or 1)
            
        Returns:
            TranscriptionResult with subtitle URLs
        """
        logger.info(f"Transcribing video: {video_url}")
        
        data = {
            "url": video_url,
            "model": model,
            "task": task
        }
        
        if language:
            data["language"] = language
        
        # Add font parameters if provided
        if font_name:
            data["font_name"] = font_name
        if font_size:
            data["font_size"] = font_size
        if font_color:
            data["font_color"] = font_color
        if font_bold is not None:
            data["font_bold"] = font_bold
        
        job_id = await self._submit_job("/transcribe/url", data)
        result = await self._wait_for_job(job_id, progress_callback)
        
        transcription = result["result"]
        
        return TranscriptionResult(
            txt_url=transcription["urls"]["txt"],
            srt_url=transcription["urls"]["srt"],
            ass_url=transcription["urls"]["ass"],
            preview=transcription.get("preview", "")
        )
    
    # ==================== FFMPEG OPERATIONS ====================
    
    async def trim_video(
        self,
        video_url: str,
        start: float = None,
        end: float = None,
        duration: float = None,
        progress_callback=None
    ) -> FFmpegResult:
        """
        Trim a video.
        
        Args:
            video_url: URL to the video
            start: Start time in seconds
            end: End time in seconds (alternative to duration)
            duration: Duration in seconds (alternative to end)
            progress_callback: Optional async callback for progress updates
            
        Returns:
            FFmpegResult with output URL
        """
        logger.info(f"Trimming video: {video_url} ({start}s - {end}s)")
        
        data = {
            "url": video_url,
            "operation": "trim"
        }
        
        if start is not None:
            data["start"] = start
        if end is not None:
            data["end"] = end
        elif duration is not None:
            data["duration"] = duration
        
        job_id = await self._submit_job("/ffmpeg", data)
        result = await self._wait_for_job(job_id, progress_callback)
        
        ffmpeg_result = result["result"]
        
        return FFmpegResult(
            operation=ffmpeg_result["operation"],
            output_url=ffmpeg_result["output_url"],
            expires_in=ffmpeg_result.get("expires_in", 86400)
        )
    
    async def reformat_video(
        self,
        video_url: str,
        format: str,
        fill_mode: str = "crop",
        progress_callback=None
    ) -> FFmpegResult:
        """
        Reformat video (landscape, reel, square).
        
        Uses the resize operation since the WhisperX API doesn't have a reformat operation.
        Skips resize if already in target format.
        
        Args:
            video_url: URL to the video
            format: Target format ("landscape", "reel", "square")
            fill_mode: How to fill ("crop", "pad", "blur_background") - currently not implemented
            progress_callback: Optional async callback for progress updates
            
        Returns:
            FFmpegResult with output URL
        """
        logger.info(f"Reformatting video to {format}: {video_url}")
        
        # Define target dimensions for each format
        format_dimensions = {
            "landscape": (1920, 1080),
            "reel": (1080, 1920),
            "square": (1080, 1080)
        }
        
        if format not in format_dimensions:
            raise ValueError(f"Unknown format: {format}. Supported: {list(format_dimensions.keys())}")
        
        width, height = format_dimensions[format]
        
        # For now, always apply resize to ensure consistent output
        # TODO: Could add dimension detection to skip if already correct
        data = {
            "url": video_url,
            "operation": "resize",
            "resize_width": width,
            "resize_height": height
        }
        
        job_id = await self._submit_job("/ffmpeg", data)
        result = await self._wait_for_job(job_id, progress_callback)
        
        ffmpeg_result = result["result"]
        
        return FFmpegResult(
            operation=ffmpeg_result["operation"],
            output_url=ffmpeg_result["output_url"],
            expires_in=ffmpeg_result.get("expires_in", 86400)
        )
    
    async def burn_subtitles(
        self,
        video_url: str,
        subtitle_url: str,
        progress_callback=None
    ) -> FFmpegResult:
        """
        Burn subtitles into video.
        
        Args:
            video_url: URL to the video
            subtitle_url: URL to SRT or ASS subtitle file
            progress_callback: Optional async callback for progress updates
            
        Returns:
            FFmpegResult with output URL
        """
        logger.info(f"Burning subtitles: {video_url}")
        
        data = {
            "url": video_url,
            "operation": "burn_subtitles",
            "subtitle_url": subtitle_url
        }
        
        job_id = await self._submit_job("/ffmpeg", data)
        result = await self._wait_for_job(job_id, progress_callback)
        
        ffmpeg_result = result["result"]
        
        return FFmpegResult(
            operation=ffmpeg_result["operation"],
            output_url=ffmpeg_result["output_url"],
            expires_in=ffmpeg_result.get("expires_in", 86400)
        )
    
    async def add_overlay(
        self,
        video_url: str,
        overlay_url: str,
        position: str = "top-center",
        opacity: float = 0.8,
        scale: float = 0.2,
        margin: int = 20,
        progress_callback=None
    ) -> FFmpegResult:
        """
        Add logo/image overlay to video.
        
        Args:
            video_url: URL to the video
            overlay_url: URL to overlay image (PNG recommended)
            position: Position ("top-left", "top-center", "top-right", "bottom-left", "bottom-center", "bottom-right", "center")
            opacity: Opacity 0.0-1.0
            scale: Scale relative to video width
            margin: Pixels from edge
            progress_callback: Optional async callback for progress updates
            
        Returns:
            FFmpegResult with output URL
        """
        logger.info(f"Adding overlay to video: {video_url}")
        
        data = {
            "url": video_url,
            "operation": "overlay",
            "overlay_url": overlay_url,
            "position": position,
            "opacity": str(opacity),
            "scale": str(scale),
            "margin": str(margin)
        }
        
        job_id = await self._submit_job("/ffmpeg", data)
        result = await self._wait_for_job(job_id, progress_callback)
        
        ffmpeg_result = result["result"]
        
        return FFmpegResult(
            operation=ffmpeg_result["operation"],
            output_url=ffmpeg_result["output_url"],
            expires_in=ffmpeg_result.get("expires_in", 86400)
        )
    
    async def loop_video(
        self,
        video_url: str,
        target_duration: float = None,
        loop_count: int = None,
        progress_callback=None
    ) -> FFmpegResult:
        """
        Loop video to target duration or count.
        
        Args:
            video_url: URL to the video
            target_duration: Target duration in seconds
            loop_count: Number of times to loop
            progress_callback: Optional async callback for progress updates
            
        Returns:
            FFmpegResult with output URL
        """
        logger.info(f"Looping video: {video_url}")
        
        data = {
            "url": video_url,
            "operation": "loop"
        }
        
        if target_duration is not None:
            data["target_duration"] = str(target_duration)
        elif loop_count is not None:
            data["loop_count"] = str(loop_count)
        
        job_id = await self._submit_job("/ffmpeg", data)
        result = await self._wait_for_job(job_id, progress_callback)
        
        ffmpeg_result = result["result"]
        
        return FFmpegResult(
            operation=ffmpeg_result["operation"],
            output_url=ffmpeg_result["output_url"],
            expires_in=ffmpeg_result.get("expires_in", 86400)
        )
    
    async def resize_video(
        self,
        video_url: str,
        width: int,
        height: int,
        progress_callback=None
    ) -> FFmpegResult:
        """
        Resize video to specific dimensions.
        
        Args:
            video_url: URL to the video
            width: Target width
            height: Target height
            progress_callback: Optional async callback for progress updates
            
        Returns:
            FFmpegResult with output URL
        """
        logger.info(f"Resizing video to {width}x{height}: {video_url}")
        
        data = {
            "url": video_url,
            "operation": "resize",
            "resize_width": str(width),
            "resize_height": str(height)
        }
        
        job_id = await self._submit_job("/ffmpeg", data)
        result = await self._wait_for_job(job_id, progress_callback)
        
        ffmpeg_result = result["result"]
        
        return FFmpegResult(
            operation=ffmpeg_result["operation"],
            output_url=ffmpeg_result["output_url"],
            expires_in=ffmpeg_result.get("expires_in", 86400)
        )
    
    async def extract_audio(
        self,
        video_url: str,
        codec: str = "mp3",
        bitrate: str = "192k",
        progress_callback=None
    ) -> FFmpegResult:
        """
        Extract audio from video.
        
        Args:
            video_url: URL to the video
            codec: Audio codec ("mp3", "aac", "flac", "wav", "ogg")
            bitrate: Audio bitrate ("128k", "192k", "256k", "320k")
            progress_callback: Optional async callback for progress updates
            
        Returns:
            FFmpegResult with output URL
        """
        logger.info(f"Extracting audio from: {video_url}")
        
        data = {
            "url": video_url,
            "operation": "extract_audio",
            "audio_codec": codec,
            "audio_bitrate": bitrate
        }
        
        job_id = await self._submit_job("/ffmpeg", data)
        result = await self._wait_for_job(job_id, progress_callback)
        
        ffmpeg_result = result["result"]
        
        return FFmpegResult(
            operation=ffmpeg_result["operation"],
            output_url=ffmpeg_result["output_url"],
            expires_in=ffmpeg_result.get("expires_in", 86400)
        )
    
    # ==================== HEALTH CHECK ====================
    
    async def health_check(self) -> Dict[str, Any]:
        """
        Check API health status.
        
        Returns:
            Health status dict
        """
        session = await self._get_session()
        
        async with session.get(f"{self.base_url}/health") as response:
            response.raise_for_status()
            return await response.json()


# Global client instance
_client: Optional[WhisperXClient] = None


def get_whisperx_client() -> WhisperXClient:
    """Get or create the global WhisperX client"""
    global _client
    if _client is None:
        _client = WhisperXClient()
    return _client


async def close_whisperx_client() -> None:
    """Close the global client session"""
    global _client
    if _client:
        await _client.close()
        _client = None
