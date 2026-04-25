"""
Summarization Cog - YouTube Video Summarization Commands

Provides commands for summarizing YouTube videos using:
- yt-dlp for fetching subtitles (primary method)
- WhisperX API for transcription (fallback)
- OpenAI GPT for summarization
- Anthropic Claude (wrapper API + direct API fallback)

Commands:
- !sumw - Uses WhisperX, then Claude
- !sum  - Uses yt-dlp/WhisperX, then OpenAI
- !sum2 - Uses yt-dlp/WhisperX, then Claude

Flow:
1. Try yt-dlp to get subtitles directly from YouTube
2. Fall back to WhisperX API if yt-dlp fails
3. Upload transcripts to R2 for all methods
"""

import discord
from discord.ext import commands
import asyncio
import os
import sys
import requests
import uuid
import traceback
import re
import time
import yt_dlp
from youtube_transcript_api import YouTubeTranscriptApi
from youtube_transcript_api.formatters import SRTFormatter
from concurrent.futures import ThreadPoolExecutor
from typing import Optional, Dict, Any

# Import configuration and logging
try:
    from utils.config import get_settings
    from utils.logging_config import get_logger
except ImportError as e:
    # Fallback to basic print if logger not available
    import sys
    print(f"Error importing utils in SummarizationCog: {e}", file=sys.stderr)

# Get logger
logger = get_logger(__name__)

# Module-level executor
_executor = ThreadPoolExecutor(max_workers=4)

# Get settings
settings = get_settings()

# WhisperX API URL
WHISPERX_API_URL = os.getenv("WHISPERX_API_URL", "https://whisperx.jeffrey-epstein.com")


# ============================================================================
# R2 UPLOAD FUNCTIONS
# ============================================================================

def get_r2_client():
    """Get R2 s3 client using environment variables"""
    import boto3
    from botocore.config import Config
    
    r2_access_key = os.getenv("R2_ACCESS_KEY_ID")
    r2_secret_key = os.getenv("R2_SECRET_ACCESS_KEY")
    r2_endpoint = os.getenv("R2_ENDPOINT_URL")
    r2_region = os.getenv("R2_REGION", "auto")
    
    if not all([r2_access_key, r2_secret_key, r2_endpoint]):
        return None
    
    return boto3.client(
        's3',
        endpoint_url=r2_endpoint,
        aws_access_key_id=r2_access_key,
        aws_secret_access_key=r2_secret_key,
        region_name=r2_region,
        config=Config(s3={'addressing_style': 'virtual'})
    )


def upload_transcript_to_r2(transcript: str, video_id: str, file_format: str = "txt") -> Optional[str]:
    """Upload transcript to R2 and return the URL
    
    Args:
        transcript: The transcript text content
        video_id: Video ID for naming the file
        file_format: 'txt' or 'srt'
    
    Returns:
        R2 presigned URL or None if failed
    """
    import io
    from botocore.config import Config
    
    r2_bucket = os.getenv("R2_BUCKET_NAME")
    if not r2_bucket:
        logger.warning("[upload_transcript_to_r2] R2_BUCKET_NAME not set")
        return None
    
    s3 = get_r2_client()
    if not s3:
        logger.warning("[upload_transcript_to_r2] Could not get R2 client")
        return None
    
    try:
        # Create file-like object from transcript string
        file_obj = io.BytesIO(transcript.encode('utf-8'))
        
        # Create object name with timestamp to avoid duplicates
        timestamp = int(time.time())
        object_name = f"transcripts/{video_id}_{timestamp}.{file_format}"
        
        # Upload
        s3.upload_fileobj(file_obj, r2_bucket, object_name)
        
        # Generate presigned URL
        url = s3.generate_presigned_url(
            'get_object',
            Params={'Bucket': r2_bucket, 'Key': object_name},
            ExpiresIn=86400 * 7  # 7 days
        )
        
        logger.info(f"[upload_transcript_to_r2] Uploaded {file_format} transcript to R2: {object_name}")
        return url
        
    except Exception as e:
        logger.error(f"[upload_transcript_to_r2] Error uploading to R2: {e}")
        return None


def get_video_id(url: str) -> Optional[str]:
    """Extract video ID from YouTube URL"""
    patterns = [
        r'(?:youtube\.com/watch\?v=|youtu\.be/|youtube\.com/embed/)([a-zA-Z0-9_-]{11})',
        r'youtube\.com/shorts/([a-zA-Z0-9_-]{11})',
    ]
    for pattern in patterns:
        match = re.search(pattern, url)
        if match:
            return match.group(1)
    return None


async def get_video_title(url: str) -> str:
    """Get video title using YouTube oEmbed API"""
    video_id = get_video_id(url)
    if not video_id:
        return "Unknown Video"
    try:
        oembed_url = f"https://www.youtube.com/oembed?url=https://youtube.com/watch?v={video_id}&format=json"
        loop = asyncio.get_event_loop()
        response = await loop.run_in_executor(
            _executor,
            lambda: requests.get(oembed_url, timeout=10)
        )
        if response.ok:
            data = response.json()
            return data.get('title', 'Unknown Video')
    except:
        pass
    return f"Video {video_id}"


def _get_proxy_with_modified_port(proxy_url: str = "") -> str:
    """Generate a proxy URL with modified last 2 digits of port for retry"""
    import random
    import re
    
    if not proxy_url:
        return proxy_url
    
    try:
        match = re.match(r'(socks5?://[^@]+@[^:]+:)(\d+)', proxy_url)
        if match:
            base = match.group(1)
            old_port = match.group(2)
            new_last_digits = str(random.randint(10, 99))
            new_port = old_port[:-2] + new_last_digits
            new_proxy = f"{base}{new_port}"
            logger.info(f"[_get_proxy_with_modified_port] Original port: {old_port}, new port: {new_port}")
            return new_proxy
    except Exception as e:
        logger.warning(f"[_get_proxy_with_modified_port] Error modifying proxy port: {e}")
    
    return proxy_url


def get_video_duration(url: str) -> Optional[int]:
    """Get video duration in seconds using yt-dlp (no download required)"""
    try:
        video_id = get_video_id(url)
        if not video_id:
            return None  # Not a YouTube URL

        proxy_url = os.getenv("YOUTUBE_PROXY", "")

        def _try_duration(proxy: str) -> Optional[int]:
            ydl_opts = {'quiet': True, 'no_warnings': True}
            if proxy:
                ydl_opts['proxy'] = proxy
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(f"https://www.youtube.com/watch?v={video_id}", download=False)
                return info.get('duration')

        duration = _try_duration(proxy_url)
        if duration is not None:
            return duration

        if proxy_url:
            new_proxy = _get_proxy_with_modified_port(proxy_url)
            logger.info(f"Duration fetch failed with original proxy, trying modified port", video_url=url)
            duration = _try_duration(new_proxy)
            if duration is not None:
                return duration

    except Exception as e:
        logger.error(f"Error fetching video duration: {e}", video_url=url)
        return None


def get_num_topics(url: str) -> str:
    """Determine number of topics based on video duration
    Returns: "3 to 6", "7 to 10", or "10 to 15" """
    duration = get_video_duration(url)
    if not duration:
        return "10 to 15"  # Default for long videos
    
    duration_mins = duration / 60
    if duration_mins < 60:
        return "3 to 6"
    elif duration_mins < 120:
        return "7 to 10"
    else:
        return "10 to 15"


def _fetch_transcript_youtube_api(youtube_url: str) -> tuple:
    """
    Fetch transcript using YouTube Transcript API.

    Returns: (transcript_text, source, srt_transcript)
    """
    video_id = get_video_id(youtube_url)
    if not video_id:
        return None, "YouTube API failed", None

    proxy_url = os.getenv("YOUTUBE_PROXY", "")

    def _try_fetch(proxy: str) -> tuple:
        if proxy:
            from youtube_transcript_api.proxies import GenericProxyConfig
            ytt_api = YouTubeTranscriptApi(
                proxy_config=GenericProxyConfig(
                    http_url=proxy,
                    https_url=proxy,
                )
            )
        else:
            ytt_api = YouTubeTranscriptApi()
        return ytt_api.fetch(video_id)

    def _is_proxy_error(e: Exception) -> bool:
        error_msg = str(e).lower()
        proxy_indicators = ['proxy', 'socks', 'timeout', 'unreachable', 'connection', 'host', 'socket']
        return any(indicator in error_msg for indicator in proxy_indicators)

    try:
        fetched_transcript = _try_fetch(proxy_url)
        transcript_text = ' '.join([snippet.text for snippet in fetched_transcript])
        formatter = SRTFormatter()
        srt_transcript = formatter.format_transcript(fetched_transcript)
        return transcript_text, "YouTube API", srt_transcript

    except Exception as e:
        error_msg = str(e)

        if "not be retrieved" in error_msg.lower() and ("language" in error_msg.lower() or "transcript" in error_msg.lower()):
            logger.warning(f"YouTube API: requested language unavailable, trying alt languages: {e}", youtube_url=youtube_url)
            try:
                fetched_transcript = _try_fetch(proxy_url)
                transcript_text = ' '.join([snippet.text for snippet in fetched_transcript])
                formatter = SRTFormatter()
                srt_transcript = formatter.format_transcript(fetched_transcript)
                logger.info("YouTube API: got transcript in alternative language", youtube_url=youtube_url)
                return transcript_text, "YouTube API (needs translation)", srt_transcript
            except Exception as e2:
                if proxy_url and _is_proxy_error(e2):
                    logger.warning(f"YouTube API alt fetch failed with proxy, retrying with modified port: {e2}", youtube_url=youtube_url)
                    new_proxy = _get_proxy_with_modified_port(proxy_url)
                    try:
                        fetched_transcript = _try_fetch(new_proxy)
                        transcript_text = ' '.join([snippet.text for snippet in fetched_transcript])
                        formatter = SRTFormatter()
                        srt_transcript = formatter.format_transcript(fetched_transcript)
                        logger.info("YouTube API: got transcript with modified proxy", youtube_url=youtube_url)
                        return transcript_text, "YouTube API (needs translation)", srt_transcript
                    except Exception as e3:
                        logger.error(f"YouTube API alt fetch with modified proxy failed: {e3}", youtube_url=youtube_url)
                else:
                    logger.error(f"YouTube API alternative language fetch error: {e2}", youtube_url=youtube_url)

        if _is_proxy_error(e) and proxy_url:
            logger.warning(f"YouTube API failed with proxy, retrying with modified port: {e}", youtube_url=youtube_url)
            new_proxy = _get_proxy_with_modified_port(proxy_url)
            try:
                fetched_transcript = _try_fetch(new_proxy)
                transcript_text = ' '.join([snippet.text for snippet in fetched_transcript])
                formatter = SRTFormatter()
                srt_transcript = formatter.format_transcript(fetched_transcript)
                return transcript_text, "YouTube API", srt_transcript
            except Exception as e2:
                logger.error(f"YouTube API retry with modified proxy failed: {e2}", youtube_url=youtube_url)

        if "cloud provider" in error_msg.lower() or "ip" in error_msg.lower() or "blocked" in error_msg.lower():
            logger.warning(f"YouTube API blocked (IP issue): {e}", youtube_url=youtube_url)
            return None, "YouTube API blocked", None
        logger.error(f"YouTube Transcript API error: {e}", youtube_url=youtube_url)
        return None, "YouTube API failed", None


def _fetch_transcript_ytdlp(youtube_url: str) -> tuple:
    """
    Fetch transcript using yt-dlp - gets subtitles directly from YouTube.
    
    Returns: (transcript_text, source, srt_transcript)
    """
    video_id = get_video_id(youtube_url)
    if not video_id:
        return None, "yt-dlp failed", None
    
    # Get proxy from environment - set YOUTUBE_PROXY in Coolify
    # Format: socks5://username:password@host:port
    proxy_url = os.getenv("YOUTUBE_PROXY", "")

    def _is_proxy_error(e: Exception) -> bool:
        error_msg = str(e).lower()
        proxy_indicators = ['proxy', 'socks', 'timeout', 'unreachable', 'connection', 'host', 'socket', '429', 'rate']
        return any(indicator in error_msg for indicator in proxy_indicators)

    def _try_ytdlp(proxy: str) -> tuple:
        ydl_opts = {
            'writesubtitles': True,
            'writeautomaticsub': True,
            'subtitlesformat': 'vtt',
            'skip_download': True,
            'outtmpl': f'/tmp/{video_id}.%(ext)s',
            'quiet': True,
            'no_warnings': True,
            'socket_timeout': 30,
        }
        if proxy:
            ydl_opts['proxy'] = proxy

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(youtube_url, download=False)
            subtitles = info.get('subtitles') or info.get('automatic_captions')

            if subtitles:
                ydl.download([youtube_url])
                lang_order = ['en', 'en-US', 'en-GB', 'a.en']
                subtitle_lang = None

                for lang in lang_order:
                    if lang in subtitles:
                        subtitle_lang = lang
                        break

                if not subtitle_lang:
                    subtitle_lang = list(subtitles.keys())[0]

                vtt_path = f'/tmp/{video_id}.{subtitle_lang}.vtt'
                if os.path.exists(vtt_path):
                    with open(vtt_path, 'r', encoding='utf-8') as f:
                        vtt_content = f.read()

                    text = re.sub(r'<[^>]+>', '', vtt_content)
                    text = re.sub(r'\d{2}:\d{2}:\d{2}\.\d{3} --> \d{2}:\d{2}:\d{2}\.\d{3}', '', text)
                    text = re.sub(r'\n\n+', '\n', text).strip()

                    srt_transcript = _convert_vtt_to_srt(vtt_content)

                    try:
                        os.remove(vtt_path)
                    except:
                        pass

                    if text:
                        return text, "yt-dlp", srt_transcript

        return None, "yt-dlp no subtitles", None

    try:
        result = _try_ytdlp(proxy_url)
        if result[0]:
            return result
    except Exception as e:
        if proxy_url and _is_proxy_error(e):
            logger.warning(f"yt-dlp failed with proxy, retrying with modified port: {e}", youtube_url=youtube_url)
            new_proxy = _get_proxy_with_modified_port(proxy_url)
            try:
                result = _try_ytdlp(new_proxy)
                if result[0]:
                    logger.info("yt-dlp succeeded with modified proxy", youtube_url=youtube_url)
                    return result
            except Exception as e2:
                logger.error(f"yt-dlp retry with modified proxy failed: {e2}", youtube_url=youtube_url)
        else:
            logger.error(f"yt-dlp transcript fetch error: {e}", youtube_url=youtube_url)
            return None, "yt-dlp failed", None

    return None, "yt-dlp no subtitles", None


def _convert_vtt_to_srt(vtt_content: str) -> str:
    """
    Convert VTT subtitle content to SRT format.
    """
    lines = vtt_content.strip().split('\n')
    srt_lines = []
    cue_counter = 1
    i = 0
    
    # Skip header if present
    if lines and lines[0].strip() == 'WEBVTT':
        i = 1
        while i < len(lines) and lines[i].strip():
            i += 1
    
    # Parse cues
    current_cue_lines = []
    timestamp_pattern = r'(\d{2}:\d{2}:\d{2}\.\d{3})\s*-->\s*(\d{2}:\d{2}:\d{2}\.\d{3})'
    
    while i < len(lines):
        line = lines[i].strip()
        
        if re.match(timestamp_pattern, line):
            if current_cue_lines:
                srt_lines.append(str(cue_counter))
                timestamp_match = re.search(timestamp_pattern, current_cue_lines[0])
                if timestamp_match:
                    start_ts = timestamp_match.group(1).replace('.', ',')
                    end_ts = timestamp_match.group(2).replace('.', ',')
                    srt_lines.append(f"{start_ts} --> {end_ts}")
                    for text_line in current_cue_lines[1:]:
                        srt_lines.append(text_line)
                    srt_lines.append('')
                    cue_counter += 1
                current_cue_lines = []
            
            current_cue_lines.append(line)
        elif line:
            current_cue_lines.append(line)
        
        i += 1
    
    # Handle last cue
    if current_cue_lines:
        srt_lines.append(str(cue_counter))
        timestamp_match = re.search(timestamp_pattern, current_cue_lines[0])
        if timestamp_match:
            start_ts = timestamp_match.group(1).replace('.', ',')
            end_ts = timestamp_match.group(2).replace('.', ',')
            srt_lines.append(f"{start_ts} --> {end_ts}")
            for text_line in current_cue_lines[1:]:
                srt_lines.append(text_line)
        srt_lines.append('')
    
    return '\n'.join(srt_lines)


def _submit_transcription_job(video_url: str) -> Optional[str]:
    """Submit transcription job to WhisperX API"""
    try:
        response = requests.post(
            f"{WHISPERX_API_URL}/transcribe/url",
            data={
                "url": video_url,
                "model": "large-v3",
                "task": "translate"
            },
            timeout=60
        )
        response.raise_for_status()
        data = response.json()
        return data.get("job_id")
    except Exception as e:
        logger.error(f"Error submitting transcription job: {e}", video_url=video_url)
        return None


def _poll_transcription_job(job_id: str, max_wait: int = 3600, poll_interval: int = 5) -> Optional[Dict[str, Any]]:
    """Poll for transcription job completion"""
    start_time = time.time()
    
    while time.time() - start_time < max_wait:
        try:
            response = requests.get(
                f"{WHISPERX_API_URL}/jobs/{job_id}",
                timeout=30
            )
            if not response.ok:
                time.sleep(poll_interval)
                continue
            
            status_data = response.json()
            status = status_data.get("status")
            
            if status == "completed":
                result = status_data.get("result")
                if result and "urls" in result:
                    return result
            
            elif status == "failed":
                error = status_data.get("error", "Unknown error")
                logger.error(f"Transcription job failed: {error}", job_id=job_id)
                return None
            
            time.sleep(poll_interval)
        
        except Exception as e:
            logger.error(f"Error polling transcription job: {e}", job_id=job_id)
            time.sleep(poll_interval)
    
    logger.error(f"Transcription job timed out: {job_id}", job_id=job_id)
    return None


def _transcribe_with_whisperx(video_url: str) -> Optional[Dict[str, Any]]:
    """Transcribe video using WhisperX API"""
    job_id = _submit_transcription_job(video_url)
    if not job_id:
        return None
    
    return _poll_transcription_job(job_id)


def _get_transcript(youtube_url: str, progress_callback=None) -> tuple:
    """
    Get transcript - tries YouTube Transcript API first, then yt-dlp, then WhisperX.
    Uploads both TXT and SRT to R2 for all methods.
    
    Returns: (transcript_text, source, txt_url, srt_url)
             txt_url and srt_url will be R2 URLs if uploaded successfully
    """
    logger.info(f"[_get_transcript] Starting transcript fetch for: {youtube_url}")
    
    video_id = get_video_id(youtube_url)
    
    # 1. Try YouTube Transcript API first
    if progress_callback:
        progress_callback("Trying to get transcript with YouTube Transcript API...")
    
    logger.info("[_get_transcript] Attempting YouTube Transcript API...")
    transcript, source, srt_transcript = _fetch_transcript_youtube_api(youtube_url)
    if transcript:
        logger.info(f"[_get_transcript] Got transcript from YouTube API, length: {len(transcript)} chars")
        if progress_callback:
            progress_callback("Got transcript from YouTube Transcript API!")
        
        # Upload both TXT and SRT to R2
        txt_url = upload_transcript_to_r2(transcript, video_id, "txt")
        logger.info(f"[_get_transcript] YouTube API TXT R2 URL: {txt_url}")
        
        srt_url = None
        if srt_transcript:
            srt_url = upload_transcript_to_r2(srt_transcript, video_id, "srt")
            logger.info(f"[_get_transcript] YouTube API SRT R2 URL: {srt_url}")
        
        return transcript, source, txt_url, srt_url
    
    # 2. Fall back to yt-dlp if YouTube API failed
    logger.info("[_get_transcript] YouTube API failed, trying yt-dlp...")
    if progress_callback:
        progress_callback("YouTube Transcript API failed. Trying yt-dlp...")
    
    transcript, source, srt_transcript = _fetch_transcript_ytdlp(youtube_url)
    if transcript:
        logger.info(f"[_get_transcript] Got transcript from yt-dlp, length: {len(transcript)} chars")
        if progress_callback:
            progress_callback("Got transcript from yt-dlp!")
        
        # Upload both TXT and SRT to R2
        txt_url = upload_transcript_to_r2(transcript, video_id, "txt")
        logger.info(f"[_get_transcript] yt-dlp TXT R2 URL: {txt_url}")
        
        srt_url = None
        if srt_transcript:
            srt_url = upload_transcript_to_r2(srt_transcript, video_id, "srt")
            logger.info(f"[_get_transcript] yt-dlp SRT R2 URL: {srt_url}")
        
        return transcript, source, txt_url, srt_url
    
    # 3. Fall back to WhisperX API if both failed
    logger.info("[_get_transcript] yt-dlp failed, trying WhisperX API...")
    if progress_callback:
        progress_callback("yt-dlp failed. Trying WhisperX...")
    
    whisper_result = _transcribe_with_whisperx(youtube_url)
    if whisper_result:
        transcript = whisper_result.get("preview", "")
        # Get the URLs for TXT and SRT transcripts from WhisperX
        txt_url = None
        srt_url = None
        if whisper_result.get("urls"):
            txt_url = whisper_result["urls"].get("txt")
            srt_url = whisper_result["urls"].get("srt")
        logger.info(f"[_get_transcript] Got transcript from WhisperX, length: {len(transcript)} chars")
        logger.info(f"[_get_transcript] TXT URL: {txt_url}, SRT URL: {srt_url}")
        if progress_callback:
            progress_callback("Got transcript from WhisperX!")
        return transcript, "WhisperX", txt_url, srt_url
    
    logger.error("[_get_transcript] All transcript methods failed")
    return None, "Failed", None, None


def _summarize_with_openai(transcript: str, video_title: str = "") -> Optional[str]:
    """Summarize transcript using OpenAI GPT"""
    logger.info("[_summarize_with_openai] Starting OpenAI summarization...")
    api_key = os.getenv("OPENAI_API_KEY", "")
    if not api_key:
        return None
    
    system_prompt = """You are a helpful AI assistant that summarizes YouTube video transcripts.
Provide a concise but comprehensive summary of the key points covered in the video.
Focus on the main topics, important details, and conclusions.
Format the summary in a clear, readable way with bullet points or sections.
IMPORTANT: Provide your entire response in English, even if the transcript is in another language."""

    user_prompt = f"""Please summarize the following transcript from the video "{video_title}":

{transcript}

Summary:"""

    try:
        response = requests.post(
            "https://api.openai.com/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json"
            },
            json={
                "model": "gpt-4o-mini",
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}
                ],
                "max_tokens": 2000,
                "temperature": 0.7
            },
            timeout=60
        )
        response.raise_for_status()
        data = response.json()
        logger.info("[_summarize_with_openai] OpenAI summarization completed")
        return data["choices"][0]["message"]["content"]
    except Exception as e:
        logger.error(f"Error calling OpenAI API: {e}")
        return None


def _identify_topics_openai(transcript: str, video_title: str = "", video_url: str = "") -> Optional[list]:
    """Identify topics in transcript using OpenAI GPT"""
    import json
    import re
    
    logger.info("[_identify_topics_openai] Starting topic identification with OpenAI...")
    api_key = os.getenv("OPENAI_API_KEY", "")
    if not api_key:
        return None
    
    # Determine number of topics based on video duration
    num_topics = get_num_topics(video_url) if video_url else "10 to 15"
    
    prompt = f"""
    You have been provided with the transcript of a podcast titled "{video_title}". Identify at least {num_topics} major topics in the following podcast transcript. Be meticulous in identifying topics, especially provocative, controversial or viral ones. I am especially interested in topics where someone is accused, called out, insulted or defamed.
    Format the response as a JSON array with the structure:
    [
        {{ "topic": "topic_name" }},
        ...
    ]
    IMPORTANT: Provide all topic names and your response in English, even if the transcript is in another language.
    Transcript:
    {transcript}
    """
    
    system_message = "You are a helpful assistant that identifies topics in podcast transcripts. Provide all output in English."
    
    try:
        response = requests.post(
            "https://api.openai.com/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json"
            },
            json={
                "model": "gpt-4o-mini",
                "messages": [
                    {"role": "system", "content": system_message},
                    {"role": "user", "content": prompt}
                ],
                "max_tokens": 2000,
                "temperature": 0.7
            },
            timeout=60
        )
        response.raise_for_status()
        data = response.json()
        result = data["choices"][0]["message"]["content"]
        
        # Extract JSON array from the response
        json_match = re.search(r'\[[\s\S]*\]', result)
        if json_match:
            json_str = json_match.group(0)
            topics = json.loads(json_str)
            logger.info(f"[_identify_topics_openai] Found {len(topics)} topics")
            if not topics:
                return [{"topic": "Full Podcast"}]
            return topics
        else:
            logger.warning(f"Could not find JSON in OpenAI response: {result}")
            return [{"topic": "Full Podcast"}]
            
    except Exception as e:
        logger.error(f"Error identifying topics with OpenAI: {e}")
        return None


def _summarize_all_topics_openai(topics: list, transcript: str, video_title: str = "") -> Optional[str]:
    """Summarize all topics using OpenAI GPT"""
    logger.info(f"[_summarize_all_topics_openai] Starting to summarize {len(topics)} topics with OpenAI...")
    api_key = os.getenv("OPENAI_API_KEY", "")
    if not api_key:
        return None
    
    topics_list = ", ".join([f'"{item["topic"]}"' for item in topics])
    
    prompt = f"""
    You have been provided with the transcript of a podcast titled "{video_title}". Comprehensively summarize each of the following topics that have been identified in this podcast transcript: {topics_list}

    Make sure you complete the summaries for every topic. Do not output a partial answer or summary. Do skip any topics or ask me for permission to summarise the rest of the topics after only doing some.

    I want detailed summaries for each Topic and you must have a minimum of 4 summary bullets for each Topic. This is how you will write the summary for each topic section:
    Original Topic name
    - summary bullet 1
    - summary bullet 2
    - summary bullet 3
    - summary bullet 4

    When you are done with the topic summaries, provide an overall summary of the entire podcast (200-300 words).

    After the overall summary, make a memorable quotes section with exactly 5 memorable quotes, write it as such:
    Memorable Quotes:
    - "First quote" - Person citing
    - "Second quote" - Person citing
    - "Third quote" - Person citing
    - "Fourth quote" - Person citing
    - "Fifth quote" - Person citing

    Transcript:
    {transcript}
    """
    
    system_message = "You are a helpful assistant that summarizes podcast topics from transcripts. Provide all output in English, even if the transcript is in another language."
    
    try:
        response = requests.post(
            "https://api.openai.com/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json"
            },
            json={
                "model": "gpt-4o-mini",
                "messages": [
                    {"role": "system", "content": system_message},
                    {"role": "user", "content": prompt}
                ],
                "max_tokens": 4000,
                "temperature": 0.7
            },
            timeout=120
        )
        response.raise_for_status()
        data = response.json()
        logger.info("[_summarize_all_topics_openai] OpenAI topic summarization completed")
        return data["choices"][0]["message"]["content"]
    except Exception as e:
        logger.error(f"Error summarizing topics with OpenAI: {e}")
        return None


def _summarize_with_anthropic(transcript: str, video_title: str = "") -> tuple:
    """Summarize transcript using Anthropic Claude - wrapper first, then direct API
    Returns: (summary_string or None, fallback_used boolean)"""
    logger.info("[_summarize_with_anthropic] Starting Anthropic summarization...")
    wrapper_fallback = False
    
    # Use wrapper API first - get password from env variable
    wrapper_url = os.getenv("CLAUDE_WRAPPER_URL", "https://claudeapi.jeffrey-epstein.com/generate")
    wrapper_key = os.getenv("CLAUDE_WRAPPER_PASSWORD", "")
    
    system_prompt = """You are a helpful AI assistant that summarizes YouTube video transcripts.
Provide a concise but comprehensive summary of the key points covered in the video.
Focus on the main topics, important details, and conclusions.
Format the summary in a clear, readable way with bullet points or sections.
IMPORTANT: Provide your entire response in English, even if the transcript is in another language."""

    user_prompt = f"""Please summarize the following transcript from the video "{video_title}":

{transcript}

Summary:"""

    # Try wrapper API first (if password is set)
    if wrapper_key:
        try:
            response = requests.post(
                wrapper_url,
                headers={
                    "Content-Type": "application/json",
                    "x-api-key": wrapper_key
                },
                json={
                    "prompt": user_prompt,
                    "system_prompt": system_prompt
                },
                timeout=600
            )
            response.raise_for_status()
            result = response.json().get('result')
            logger.info("[_summarize_with_anthropic] Anthropic summarization completed (via wrapper)")
            return (result, wrapper_fallback)
        except Exception as e:
            logger.warning(f"Wrapper API failed: {e}. Trying direct API...")
            wrapper_fallback = True
    else:
        logger.info("No CLAUDE_WRAPPER_PASSWORD set, using direct API")
        wrapper_fallback = True
    
    # Fall back to direct Anthropic API - uses ANTHROPIC_API_KEY from Coolify env
    api_key = os.getenv("ANTHROPIC_API_KEY", "")
    if not api_key:
        logger.warning("No ANTHROPIC_API_KEY available for fallback")
        return (None, wrapper_fallback)
    
    try:
        response = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
                "Content-Type": "application/json"
            },
            json={
                "model": "claude-sonnet-4-6",
                "max_tokens": 2000,
                "system": system_prompt,
                "messages": [
                    {"role": "user", "content": user_prompt}
                ]
            },
            timeout=60
        )
        response.raise_for_status()
        data = response.json()
        result = data["content"][0]["text"]
        logger.info("[_summarize_with_anthropic] Anthropic summarization completed (direct API)")
        return (result, wrapper_fallback)
    except Exception as e:
        logger.error(f"Error calling Anthropic API: {e}")
        return (None, wrapper_fallback)


def _identify_topics_anthropic(transcript: str, video_title: str = "", video_url: str = "") -> tuple:
    """Identify topics in transcript using Anthropic Claude - wrapper first, then direct API
    Returns: (topics_list or None, fallback_used boolean)"""
    import json
    import re
    
    logger.info("[_identify_topics_anthropic] Starting topic identification with Anthropic...")
    wrapper_fallback = False
    
    # Determine number of topics based on video duration
    num_topics = get_num_topics(video_url) if video_url else "10 to 15"
    
    # Use wrapper API first - get password from env variable
    wrapper_url = os.getenv("CLAUDE_WRAPPER_URL", "https://claudeapi.jeffrey-epstein.com/generate")
    wrapper_key = os.getenv("CLAUDE_WRAPPER_PASSWORD", "")
    
    prompt = f"""
    You have been provided with the transcript of a podcast titled "{video_title}". Identify at least {num_topics} major topics in the following podcast transcript. Be meticulous in identifying topics, especially provocative, controversial or viral ones. I am especially interested in topics where someone is accused, called out, insulted or defamed.
    Format the response as a JSON array with the structure:
    [
        {{ "topic": "topic_name" }},
        ...
    ]
    IMPORTANT: Provide all topic names and your response in English, even if the transcript is in another language.
    Transcript:
    {transcript}
    """
    
    system_message = "You are a helpful assistant that identifies topics in podcast transcripts. Provide all output in English."
    
    # Try wrapper API first (if password is set)
    if wrapper_key:
        try:
            response = requests.post(
                wrapper_url,
                headers={
                    "Content-Type": "application/json",
                    "x-api-key": wrapper_key
                },
                json={
                    "prompt": f"System: {system_message}\n\n{prompt}",
                    "system_prompt": system_message
                },
                timeout=600
            )
            response.raise_for_status()
            result = response.json().get('result', '')
            
            # Extract JSON
            json_match = re.search(r'\[[\s\S]*\]', result)
            if json_match:
                topics = json.loads(json_match.group(0))
                if topics:
                    logger.info(f"[_identify_topics_anthropic] Found {len(topics)} topics (via wrapper)")
                    return (topics, wrapper_fallback)
        except Exception as e:
            logger.warning(f"Wrapper API failed for topic ID: {e}. Trying direct API...")
            wrapper_fallback = True
    else:
        logger.info("No CLAUDE_WRAPPER_PASSWORD set, using direct API for topics")
        wrapper_fallback = True
    
    # Fall back to direct Anthropic API
    api_key = os.getenv("ANTHROPIC_API_KEY", "")
    if not api_key:
        return (None, wrapper_fallback)
    
    try:
        response = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
                "Content-Type": "application/json"
            },
            json={
                "model": "claude-sonnet-4-6",
                "max_tokens": 2000,
                "system": system_message,
                "messages": [
                    {"role": "user", "content": prompt}
                ]
            },
            timeout=60
        )
        response.raise_for_status()
        data = response.json()
        result = data["content"][0]["text"]
        
        json_match = re.search(r'\[[\s\S]*\]', result)
        if json_match:
            topics = json.loads(json_match.group(0))
            if topics:
                logger.info(f"[_identify_topics_anthropic] Found {len(topics)} topics (direct API)")
                return (topics, wrapper_fallback)
        logger.warning("[_identify_topics_anthropic] Could not parse topics, using default")
        return ([{"topic": "Full Podcast"}], wrapper_fallback)
    except Exception as e:
        logger.error(f"Error identifying topics with Anthropic: {e}")
        return (None, wrapper_fallback)


def _summarize_all_topics_anthropic(topics: list, transcript: str, video_title: str = "") -> tuple:
    """Summarize all topics using Anthropic Claude - wrapper first, then direct API
    Returns: (summary_string or None, fallback_used boolean)"""
    
    import sys
    logger.info(f"[_summarize_all_topics_anthropic] Starting to summarize {len(topics)} topics with Anthropic...")
    wrapper_fallback = False
    
    # Use wrapper API first - get password from env variable
    wrapper_url = os.getenv("CLAUDE_WRAPPER_URL", "https://claudeapi.jeffrey-epstein.com/generate")
    wrapper_key = os.getenv("CLAUDE_WRAPPER_PASSWORD", "")
    
    logger.debug(f"wrapper_key set = {bool(wrapper_key)}")

    topics_list = ", ".join([f'"{item["topic"]}"' for item in topics])
    
    prompt = f"""
    You have been provided with the transcript of a podcast titled "{video_title}". Comprehensively summarize each of the following topics that have been identified in this podcast transcript: {topics_list}

    Make sure you complete the summaries for every topic. Do not output a partial answer or summary. Do skip any topics or ask me for permission to summarise the rest of the topics after only doing some.

    I want detailed summaries for each Topic and you must have a minimum of 4 summary bullets for each Topic. This is how you will write the summary for each topic section:
    Original Topic name
    - summary bullet 1
    - summary bullet 2
    - summary bullet 3
    - summary bullet 4

    When you are done with the topic summaries, provide an overall summary of the entire podcast (200-300 words).

    After the overall summary, make a memorable quotes section with exactly 5 memorable quotes, write it as such:
    Memorable Quotes:
    - "First quote" - Person citing
    - "Second quote" - Person citing
    - "Third quote" - Person citing
    - "Fourth quote" - Person citing
    - "Fifth quote" - Person citing

    Transcript:
    {transcript}
    """
    
    system_message = "You are a helpful assistant that summarizes podcast topics from transcripts. Provide all output in English, even if the transcript is in another language."
    
    # Try wrapper API first (if password is set)
    if wrapper_key:
        try:
            response = requests.post(
                wrapper_url,
                headers={
                    "Content-Type": "application/json",
                    "x-api-key": wrapper_key
                },
                json={
                    "prompt": f"System: {system_message}\n\n{prompt}",
                    "system_prompt": system_message
                },
                timeout=600
            )
            response.raise_for_status()
            result = response.json().get('result')
            logger.debug("Wrapper succeeded, returning with fallback=False")
            logger.info("[_summarize_all_topics_anthropic] Topic summarization completed (via wrapper)")
            return (result, wrapper_fallback)
        except Exception as e:
            logger.warning(f"Wrapper API failed for summary: {e}. Trying direct API...")
            wrapper_fallback = True
            logger.debug(f"Set wrapper_fallback = {wrapper_fallback} after exception")
    else:
        logger.info("No CLAUDE_WRAPPER_PASSWORD set, using direct API for summary")
        wrapper_fallback = True
    
    # Fall back to direct Anthropic API
    api_key = os.getenv("ANTHROPIC_API_KEY", "")
    if not api_key:
        return (None, wrapper_fallback)
    
    try:
        response = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
                "Content-Type": "application/json"
            },
            json={
                "model": "claude-sonnet-4-6",
                "max_tokens": 4000,
                "system": system_message,
                "messages": [
                    {"role": "user", "content": prompt}
                ]
            },
            timeout=120
        )
        response.raise_for_status()
        data = response.json()
        result = data["content"][0]["text"]
        logger.debug(f"Direct API succeeded, returning fallback={wrapper_fallback}")
        logger.info("[_summarize_all_topics_anthropic] Topic summarization completed (direct API)")
        return (result, wrapper_fallback)
    except Exception as e:
        logger.error(f"Error summarizing topics with Anthropic: {e}")
        return (None, wrapper_fallback)


class SummarizationCog(commands.Cog):
    """Cog for handling YouTube video summarization commands."""
    
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.user_jobs: Dict[str, list] = {}
    
    async def _send_message(self, ctx, message: str):
        """Send a message"""
        try:
            await ctx.send(message)
        except Exception as e:
            logger.error(f"Error sending message: {e}")

    @commands.command(
        name='sumw',
        help='Summarize using WhisperX, then Claude',
        description='Transcribe with WhisperX, then summarize with Claude',
        usage='!sumw <youtube_url>',
        brief='!sumw <youtube_url>'
    )
    async def sumw_command(self, ctx, youtube_url: str):
        """!sumw - Uses WhisperX first, then Claude"""
        logger.info(f"[sumw] Command invoked with URL: {youtube_url}")
        
        if not isinstance(ctx.channel, discord.DMChannel):
            await ctx.send("This command can only be used in DMs.")
            return
        
        await ctx.send(f"📝 Processing: {youtube_url}")
        
        loop = asyncio.get_event_loop()
        video_id = get_video_id(youtube_url)
        
        if not video_id:
            await ctx.send("❌ Invalid YouTube URL")
            return
        
        try:
            video_title = await get_video_title(youtube_url)
            logger.info(f"[sumw] Video title: {video_title}")
            await ctx.send(f"📺 Video: {video_title}")
            
            # Get video duration and show topic count
            logger.info("[sumw] Checking video duration...")
            await ctx.send("⏳ Checking video duration for topic count...")
            duration = get_video_duration(youtube_url)
            if duration:
                duration_mins = duration // 60
                duration_secs = duration % 60
                num_topics = get_num_topics(youtube_url)
                logger.info(f"[sumw] Video: {duration_mins}m {duration_secs}s, topics: {num_topics}")
                await ctx.send(f"⏱️ Video duration: {duration_mins}m {duration_secs}s → Will identify {num_topics} topics")
            else:
                logger.warning("[sumw] Could not detect video duration")
                await ctx.send("⚠️ Could not detect video duration, using default topic count (10 to 15)")
            
            logger.info("[sumw] Fetching transcript with WhisperX...")
            await ctx.send("📝 Getting transcript with WhisperX...")
            
            # Use WhisperX directly for !sumw command
            transcript, source, txt_url, srt_url = None, "Failed", None, None
            
            whisper_result = await loop.run_in_executor(
                _executor,
                lambda: _transcribe_with_whisperx(youtube_url)
            )
            
            if whisper_result:
                transcript = whisper_result.get("preview", "")
                if whisper_result.get("urls"):
                    txt_url = whisper_result["urls"].get("txt")
                    srt_url = whisper_result["urls"].get("srt")
                source = "WhisperX"
                logger.info(f"[sumw] Got transcript from WhisperX, length: {len(transcript)} chars")
            else:
                await ctx.send("❌ WhisperX transcription failed. Please try again later.")
                return
            
            # Send transcript URLs if available
            if txt_url or srt_url:
                urls_msg = "📄 **Transcript URLs:**\n"
                if txt_url:
                    urls_msg += f"• TXT: {txt_url}\n"
                if srt_url:
                    urls_msg += f"• SRT: {srt_url}\n"
                await ctx.send(urls_msg)
            
            # Check if using wrapper or direct API
            wrapper_key = os.getenv("CLAUDE_WRAPPER_PASSWORD", "")
            if wrapper_key:
                await ctx.send("🔐 Using Claude wrapper API...")
            else:
                await ctx.send("🔐 Using direct Claude API (no wrapper password set)...")
            
            await ctx.send(f"📝 Transcript source: {source}\n🧠 Identifying topics with Claude (step 1/2)...")
            
            # Stage 1: Identify topics (pass youtube_url for duration-based topic count)
            logger.info("[sumw] Identifying topics with Claude...")
            topics_result = await loop.run_in_executor(
                _executor,
                lambda: _identify_topics_anthropic(transcript, video_title, youtube_url)
            )
            topics, topics_fallback = topics_result if topics_result else (None, False)
            
            if topics_fallback:
                await ctx.send("🔄 Using Claude direct API for topic identification...")
            
            if not topics:
                await ctx.send("❌ Could not identify topics. Trying simple summary...")
                # Fall back to simple summary
                summary_result = await loop.run_in_executor(
                    _executor,
                    lambda: _summarize_with_anthropic(transcript, video_title)
                )
                summary, summary_fallback = summary_result if summary_result else (None, False)
                
                if summary_fallback:
                    await ctx.send("🔄 Using Claude direct API for summary...")
            else:
                logger.info(f"[sumw] Found {len(topics)} topics, summarizing each...")
                await ctx.send(f"📋 Found {len(topics)} topics! Summarizing each (step 2/2)...")
                
                # Rate limiting before summarization (10 seconds)
                logger.info("[sumw] Waiting 10 seconds for rate limiting...")
                await ctx.send("⏳ Waiting 10 seconds for rate limiting...")
                await asyncio.sleep(10)
                
                # Stage 2: Summarize all topics
                summary_result = await loop.run_in_executor(
                    _executor,
                    lambda: _summarize_all_topics_anthropic(topics, transcript, video_title)
                )
                summary, summary_fallback = summary_result if summary_result else (None, False)
                
                if summary_fallback:
                    await ctx.send("🔄 Using Claude direct API for summarization...")
            
            if summary:
                logger.info("[sumw] Summary generation complete, sending to Discord...")
                await ctx.send("✅ **Summary (YouTube API/yt-dlp/Whisper + Claude):**\n")
                chunks = [summary[i:i+1900] for i in range(0, len(summary), 1900)]
                for chunk in chunks:
                    await ctx.send(chunk)
                    await asyncio.sleep(0.5)
            else:
                await ctx.send("❌ Summary generation failed.")
            
        except Exception as e:
            await ctx.send(f"❌ An error occurred: {str(e)}")
            logger.exception(f"Error details (!sumw): {e}")

    @commands.command(
        name='sum',
        help='Summarize using yt-dlp/WhisperX, then OpenAI',
        description='Get transcript with yt-dlp first, then WhisperX fallback, summarize with OpenAI',
        usage='!sum <youtube_url>',
        brief='!sum <youtube_url>'
    )
    async def sum_command(self, ctx, youtube_url: str):
        """!sum - Use yt-dlp/WhisperX, then OpenAI"""
        logger.info(f"[sum] Command invoked with URL: {youtube_url}")
        
        if not isinstance(ctx.channel, discord.DMChannel):
            await ctx.send("This command can only be used in DMs.")
            return
        
        # Check API key
        if not os.getenv("OPENAI_API_KEY"):
            await ctx.send("❌ OPENAI_API_KEY not set in environment")
            return
        
        await ctx.send(f"📝 Processing: {youtube_url}")
        
        loop = asyncio.get_event_loop()
        video_id = get_video_id(youtube_url)
        
        if not video_id:
            await ctx.send("❌ Invalid YouTube URL")
            return
        
        try:
            video_title = await get_video_title(youtube_url)
            logger.info(f"[sum] Video title: {video_title}")
            await ctx.send(f"📺 Video: {video_title}")
            
            # Get video duration and show topic count
            logger.info("[sum] Checking video duration...")
            await ctx.send("⏳ Checking video duration for topic count...")
            duration = get_video_duration(youtube_url)
            if duration:
                duration_mins = duration // 60
                duration_secs = duration % 60
                num_topics = get_num_topics(youtube_url)
                logger.info(f"[sum] Video: {duration_mins}m {duration_secs}s, topics: {num_topics}")
                await ctx.send(f"⏱️ Video duration: {duration_mins}m {duration_secs}s → Will identify {num_topics} topics")
            else:
                logger.warning("[sum] Could not detect video duration")
                await ctx.send("⚠️ Could not detect video duration, using default topic count (10 to 15)")
            
            logger.info("[sum] Fetching transcript...")
            await ctx.send("📝 Getting transcript (YouTube API → yt-dlp → WhisperX)...")
            
            # Try all transcript methods in order
            transcript, source, txt_url, srt_url = await loop.run_in_executor(
                _executor,
                lambda: _get_transcript(youtube_url)
            )
            
            if not transcript:
                await ctx.send("❌ Could not get transcript. Please try again later.")
                return
            
            # Send transcript URLs if available
            if txt_url or srt_url:
                urls_msg = "📄 **Transcript URLs:**\n"
                if txt_url:
                    urls_msg += f"• TXT: {txt_url}\n"
                if srt_url:
                    urls_msg += f"• SRT: {srt_url}\n"
                await ctx.send(urls_msg)
            
            logger.info("[sum] Identifying topics with OpenAI...")
            await ctx.send(f"📝 Transcript source: {source}\n🤖 Identifying topics with OpenAI (step 1/2)...")
            
            # Stage 1: Identify topics (pass youtube_url for duration-based topic count)
            topics = await loop.run_in_executor(
                _executor,
                lambda: _identify_topics_openai(transcript, video_title, youtube_url)
            )
            
            if not topics:
                await ctx.send("❌ Could not identify topics. Trying simple summary...")
                # Fall back to simple summary
                summary = await loop.run_in_executor(
                    _executor,
                    lambda: _summarize_with_openai(transcript, video_title)
                )
            else:
                logger.info(f"[sum] Found {len(topics)} topics, summarizing each...")
                await ctx.send(f"📋 Found {len(topics)} topics! Summarizing each (step 2/2)...")
                
                # Rate limiting before summarization (10 seconds for OpenAI)
                logger.info("[sum] Waiting 10 seconds for rate limiting...")
                await ctx.send("⏳ Waiting 10 seconds for rate limiting...")
                await asyncio.sleep(10)
                
                # Stage 2: Summarize all topics
                summary = await loop.run_in_executor(
                    _executor,
                    lambda: _summarize_all_topics_openai(topics, transcript, video_title)
                )
            
            if summary:
                logger.info("[sum] Summary generation complete, sending to Discord...")
                await ctx.send("✅ **Summary (YouTube API/yt-dlp/Whisper + OpenAI):**\n")
                chunks = [summary[i:i+1900] for i in range(0, len(summary), 1900)]
                for chunk in chunks:
                    await ctx.send(chunk)
                    await asyncio.sleep(0.5)
            else:
                await ctx.send("❌ Summary generation failed.")
            
        except Exception as e:
            await ctx.send(f"❌ An error occurred: {str(e)}")
            logger.exception(f"Error details (!sum): {e}")

    @commands.command(
        name='sum2',
        help='Summarize using yt-dlp/WhisperX, then Claude',
        description='Get transcript with yt-dlp first, then WhisperX fallback, summarize with Claude',
        usage='!sum2 <youtube_url>',
        brief='!sum2 <youtube_url>'
    )
    async def sum2_command(self, ctx, youtube_url: str):
        """!sum2 - Use yt-dlp/WhisperX, then Claude"""
        logger.info(f"[sum2] Command invoked with URL: {youtube_url}")
        
        if not isinstance(ctx.channel, discord.DMChannel):
            await ctx.send("This command can only be used in DMs.")
            return
        
        await ctx.send(f"📝 Processing: {youtube_url}")
        
        loop = asyncio.get_event_loop()
        video_id = get_video_id(youtube_url)
        
        if not video_id:
            await ctx.send("❌ Invalid YouTube URL")
            return
        
        try:
            video_title = await get_video_title(youtube_url)
            logger.info(f"[sum2] Video title: {video_title}")
            await ctx.send(f"📺 Video: {video_title}")
            
            # Get video duration and show topic count
            logger.info("[sum2] Checking video duration...")
            await ctx.send("⏳ Checking video duration for topic count...")
            duration = get_video_duration(youtube_url)
            if duration:
                duration_mins = duration // 60
                duration_secs = duration % 60
                num_topics = get_num_topics(youtube_url)
                logger.info(f"[sum2] Video: {duration_mins}m {duration_secs}s, topics: {num_topics}")
                await ctx.send(f"⏱️ Video duration: {duration_mins}m {duration_secs}s → Will identify {num_topics} topics")
            else:
                logger.warning("[sum2] Could not detect video duration")
                await ctx.send("⚠️ Could not detect video duration, using default topic count (10 to 15)")
            
            logger.info("[sum2] Fetching transcript...")
            await ctx.send("📝 Getting transcript (YouTube API → yt-dlp → WhisperX)...")
            
            # Try all transcript methods in order
            transcript, source, txt_url, srt_url = await loop.run_in_executor(
                _executor,
                lambda: _get_transcript(youtube_url)
            )
            
            if not transcript:
                await ctx.send("❌ Could not get transcript. Please try again later.")
                return
            
            # Send transcript URLs if available
            if txt_url or srt_url:
                urls_msg = "📄 **Transcript URLs:**\n"
                if txt_url:
                    urls_msg += f"• TXT: {txt_url}\n"
                if srt_url:
                    urls_msg += f"• SRT: {srt_url}\n"
                await ctx.send(urls_msg)
            
            # Check if using wrapper or direct API
            wrapper_key = os.getenv("CLAUDE_WRAPPER_PASSWORD", "")
            if wrapper_key:
                await ctx.send("🔐 Using Claude wrapper API...")
            else:
                await ctx.send("🔐 Using direct Claude API (no wrapper password set)...")
            
            await ctx.send(f"📝 Transcript source: {source}\n🧠 Identifying topics with Claude (step 1/2)...")
            
            # Stage 1: Identify topics (pass youtube_url for duration-based topic count)
            logger.info("[sum2] Identifying topics with Claude...")
            topics_result = await loop.run_in_executor(
                _executor,
                lambda: _identify_topics_anthropic(transcript, video_title, youtube_url)
            )
            topics, topics_fallback = topics_result if topics_result else (None, False)
            
            if topics_fallback:
                await ctx.send("🔄 Using Claude direct API for topic identification...")
            
            if not topics:
                await ctx.send("❌ Could not identify topics. Trying simple summary...")
                # Fall back to simple summary
                summary_result = await loop.run_in_executor(
                    _executor,
                    lambda: _summarize_with_anthropic(transcript, video_title)
                )
                summary, summary_fallback = summary_result if summary_result else (None, False)
                
                if summary_fallback:
                    await ctx.send("🔄 Using Claude direct API for summary...")
            else:
                logger.info(f"[sum2] Found {len(topics)} topics, summarizing each...")
                await ctx.send(f"📋 Found {len(topics)} topics! Summarizing each (step 2/2)...")
                
                # Rate limiting before summarization (10 seconds)
                logger.info("[sum2] Waiting 10 seconds for rate limiting...")
                await ctx.send("⏳ Waiting 10 seconds for rate limiting...")
                await asyncio.sleep(10)
                
                # Stage 2: Summarize all topics
                summary_result = await loop.run_in_executor(
                    _executor,
                    lambda: _summarize_all_topics_anthropic(topics, transcript, video_title)
                )
                summary, summary_fallback = summary_result if summary_result else (None, False)
                
                if summary_fallback:
                    await ctx.send("🔄 Using Claude direct API for summarization...")
            
            if summary:
                logger.info("[sum2] Summary generation complete, sending to Discord...")
                await ctx.send("✅ **Summary (YouTube API/yt-dlp/Whisper + Claude):**\n")
                chunks = [summary[i:i+1900] for i in range(0, len(summary), 1900)]
                for chunk in chunks:
                    await ctx.send(chunk)
                    await asyncio.sleep(0.5)
            else:
                await ctx.send("❌ Summary generation failed.")
            
        except Exception as e:
            await ctx.send(f"❌ An error occurred: {str(e)}")
            logger.exception(f"Error details (!sum2): {e}")


async def setup(bot: commands.Bot):
    """Setup function for the cog"""
    await bot.add_cog(SummarizationCog(bot))
    logger.info("SummarizationCog loaded.")