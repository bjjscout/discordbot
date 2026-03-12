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
from concurrent.futures import ThreadPoolExecutor
from typing import Optional, Dict, Any

# Import configuration
try:
    from utils.config import get_settings
except ImportError as e:
    print(f"Error importing utils in SummarizationCog: {e}", file=sys.stderr)

# Module-level executor
_executor = ThreadPoolExecutor(max_workers=4)

# Get settings
settings = get_settings()

# WhisperX API URL
WHISPERX_API_URL = os.getenv("WHISPERX_API_URL", "https://whisperx.jeffrey-epstein.com")


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


def _fetch_transcript_youtube_api(youtube_url: str) -> tuple:
    """
    Fetch transcript using YouTube Transcript API.
    
    Returns: (transcript_text, source)
    """
    video_id = get_video_id(youtube_url)
    if not video_id:
        return None, "YouTube API failed"
    
    # Get proxy from environment - set YOUTUBE_PROXY in Coolify
    # Format: socks5://username:password@host:port
    proxy_url = os.getenv("YOUTUBE_PROXY", "")
    
    try:
        if proxy_url:
            from youtube_transcript_api.proxies import GenericProxyConfig
            ytt_api = YouTubeTranscriptApi(
                proxy_config=GenericProxyConfig(
                    http_url=proxy_url,
                    https_url=proxy_url,
                )
            )
        else:
            ytt_api = YouTubeTranscriptApi()
            
        fetched_transcript = ytt_api.fetch(video_id)
        
        # Convert to plain text
        transcript_text = ' '.join([snippet.text for snippet in fetched_transcript])
        
        return transcript_text, "YouTube API"
        
    except Exception as e:
        error_msg = str(e)
        # Check if it's an IP block - if so, skip other YouTube methods too
        if "cloud provider" in error_msg.lower() or "ip" in error_msg.lower() or "blocked" in error_msg.lower():
            print(f"YouTube API blocked (IP issue): {e}", file=sys.stderr)
            return None, "YouTube API blocked"
        print(f"YouTube Transcript API error: {e}", file=sys.stderr)
        return None, "YouTube API failed"


def _fetch_transcript_ytdlp(youtube_url: str) -> tuple:
    """
    Fetch transcript using yt-dlp - gets subtitles directly from YouTube.
    
    Returns: (transcript_text, source)
    """
    video_id = get_video_id(youtube_url)
    if not video_id:
        return None, "yt-dlp failed"
    
    # Get proxy from environment - set YOUTUBE_PROXY in Coolify
    # Format: socks5://username:password@host:port
    proxy_url = os.getenv("YOUTUBE_PROXY", "")
    
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
    
    # Only add proxy if configured
    if proxy_url:
        ydl_opts['proxy'] = proxy_url
    
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            # Extract info first to see available subtitles
            info = ydl.extract_info(youtube_url, download=False)
            
            subtitles = info.get('subtitles') or info.get('automatic_captions')
            
            if subtitles:
                # Download subtitles
                ydl.download([youtube_url])
                
                # Try to find English subtitles
                lang_order = ['en', 'en-US', 'en-GB', 'a.en']
                subtitle_lang = None
                
                for lang in lang_order:
                    if lang in subtitles:
                        subtitle_lang = lang
                        break
                
                if not subtitle_lang:
                    # Just pick the first available
                    subtitle_lang = list(subtitles.keys())[0]
                
                # Read the downloaded subtitle file
                vtt_path = f'/tmp/{video_id}.{subtitle_lang}.vtt'
                if os.path.exists(vtt_path):
                    with open(vtt_path, 'r', encoding='utf-8') as f:
                        vtt_content = f.read()
                    
                    # Convert VTT to plain text (strip tags)
                    # Remove VTT formatting tags
                    text = re.sub(r'<[^>]+>', '', vtt_content)
                    # Remove timestamp lines
                    text = re.sub(r'\d{2}:\d{2}:\d{2}\.\d{3} --> \d{2}:\d{2}:\d{2}\.\d{3}', '', text)
                    # Clean up extra whitespace
                    text = re.sub(r'\n\n+', '\n', text).strip()
                    
                    # Clean up temp files
                    try:
                        os.remove(vtt_path)
                    except:
                        pass
                    
                    if text:
                        return text, "yt-dlp"
        
        return None, "yt-dlp no subtitles"
        
    except Exception as e:
        print(f"yt-dlp transcript fetch error: {e}", file=sys.stderr)
        return None, "yt-dlp failed"


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
        print(f"Error submitting transcription job: {e}", file=sys.stderr)
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
                print(f"Transcription job failed: {error}", file=sys.stderr)
                return None
            
            time.sleep(poll_interval)
        
        except Exception as e:
            print(f"Error polling transcription job: {e}", file=sys.stderr)
            time.sleep(poll_interval)
    
    print(f"Transcription job timed out: {job_id}", file=sys.stderr)
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
    
    Returns: (transcript_text, source)
    """
    # 1. Try YouTube Transcript API first
    if progress_callback:
        progress_callback("Trying to get transcript with YouTube Transcript API...")
    
    transcript, source = _fetch_transcript_youtube_api(youtube_url)
    if transcript:
        if progress_callback:
            progress_callback("Got transcript from YouTube Transcript API!")
        return transcript, source
    
    # 2. Fall back to yt-dlp if YouTube API failed
    if progress_callback:
        progress_callback("YouTube Transcript API failed. Trying yt-dlp...")
    
    transcript, source = _fetch_transcript_ytdlp(youtube_url)
    if transcript:
        if progress_callback:
            progress_callback("Got transcript from yt-dlp!")
        return transcript, source
    
    # 3. Fall back to WhisperX API if both failed
    if progress_callback:
        progress_callback("yt-dlp failed. Trying WhisperX...")
    
    whisper_result = _transcribe_with_whisperx(youtube_url)
    if whisper_result:
        transcript = whisper_result.get("preview", "")
        if progress_callback:
            progress_callback("Got transcript from WhisperX!")
        return transcript, "WhisperX"
    
    return None, "Failed"


def _summarize_with_openai(transcript: str, video_title: str = "") -> Optional[str]:
    """Summarize transcript using OpenAI GPT"""
    api_key = os.getenv("OPENAI_API_KEY", "")
    if not api_key:
        return None
    
    system_prompt = """You are a helpful AI assistant that summarizes YouTube video transcripts.
Provide a concise but comprehensive summary of the key points covered in the video.
Focus on the main topics, important details, and conclusions.
Format the summary in a clear, readable way with bullet points or sections."""

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
        return data["choices"][0]["message"]["content"]
    except Exception as e:
        print(f"Error calling OpenAI API: {e}", file=sys.stderr)
        return None


def _summarize_with_anthropic(transcript: str, video_title: str = "") -> Optional[str]:
    """Summarize transcript using Anthropic Claude - wrapper first, then direct API"""
    # Use wrapper API first - get password from env variable
    wrapper_url = os.getenv("CLAUDE_WRAPPER_URL", "https://clrey-epstein.comaudeapi.jeff/generate")
    wrapper_key = os.getenv("CLAUDE_WRAPPER_PASSWORD", "")
    
    system_prompt = """You are a helpful AI assistant that summarizes YouTube video transcripts.
Provide a concise but comprehensive summary of the key points covered in the video.
Focus on the main topics, important details, and conclusions.
Format the summary in a clear, readable way with bullet points or sections."""

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
                timeout=120
            )
            response.raise_for_status()
            return response.json().get('result')
        except Exception as e:
            print(f"Wrapper API failed: {e}. Trying direct API...", file=sys.stderr)
    else:
        print("No CLAUDE_WRAPPER_PASSWORD set, using direct API", file=sys.stderr)
    
    # Fall back to direct Anthropic API - uses ANTHROPIC_API_KEY from Coolify env
    api_key = os.getenv("ANTHROPIC_API_KEY", "")
    if not api_key:
        print("No ANTHROPIC_API_KEY available for fallback", file=sys.stderr)
        return None
    
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
        return data["content"][0]["text"]
    except Exception as e:
        print(f"Error calling Anthropic API: {e}", file=sys.stderr)
        return None


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
            print(f"Error sending message: {e}", file=sys.stderr)

    @commands.command(
        name='sumw',
        help='Summarize using WhisperX, then Claude',
        description='Transcribe with WhisperX, then summarize with Claude',
        usage='!sumw <youtube_url>',
        brief='!sumw <youtube_url>'
    )
    async def sumw_command(self, ctx, youtube_url: str):
        """!sumw - Uses WhisperX first, then Claude"""
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
            await ctx.send(f"📺 Video: {video_title}")
            await ctx.send("⏳ Getting transcript (yt-dlp first, then WhisperX)...")
            
            # !sumw tries yt-dlp first, then falls back to WhisperX
            transcript, source = None, "Failed"
            
            # Try yt-dlp first
            transcript, source = await loop.run_in_executor(
                _executor,
                lambda: _fetch_transcript_ytdlp(youtube_url)
            )
            
            # Fall back to WhisperX if yt-dlp failed
            if not transcript:
                await ctx.send("yt-dlp failed. Trying WhisperX...")
                whisper_result = await loop.run_in_executor(
                    _executor,
                    lambda: _transcribe_with_whisperx(youtube_url)
                )
                if whisper_result:
                    transcript = whisper_result.get("preview", "")
                    source = "WhisperX"
            
            if not transcript:
                await ctx.send("❌ Transcription failed. Please try again later.")
                return
            
            await ctx.send(f"📝 Transcript source: {source}\n🧠 Generating summary with Claude...")
            
            # Send to Claude
            summary = await loop.run_in_executor(
                _executor,
                lambda: _summarize_with_anthropic(transcript, video_title)
            )
            
            if summary:
                await ctx.send("✅ **Summary:**\n")
                chunks = [summary[i:i+1900] for i in range(0, len(summary), 1900)]
                for chunk in chunks:
                    await ctx.send(chunk)
                    await asyncio.sleep(0.5)
            else:
                await ctx.send("❌ Summary generation failed.")
            
        except Exception as e:
            await ctx.send(f"❌ An error occurred: {str(e)}")
            print(f"Error details (!sumw): {e}", file=sys.stderr)
            traceback.print_exc(file=sys.stderr)

    @commands.command(
        name='sum',
        help='Summarize using yt-dlp/WhisperX, then OpenAI',
        description='Get transcript with yt-dlp first, then WhisperX fallback, summarize with OpenAI',
        usage='!sum <youtube_url>',
        brief='!sum <youtube_url>'
    )
    async def sum_command(self, ctx, youtube_url: str):
        """!sum - Use yt-dlp/WhisperX, then OpenAI"""
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
            await ctx.send(f"📺 Video: {video_title}")
            await ctx.send("⏳ Getting transcript (yt-dlp first, then WhisperX)...")
            
            # Use yt-dlp first, then WhisperX fallback
            transcript, source = await loop.run_in_executor(
                _executor,
                lambda: _get_transcript(youtube_url)
            )
            
            if not transcript:
                await ctx.send("❌ Could not get transcript. Please try again later.")
                return
            
            await ctx.send(f"📝 Transcript source: {source}\n🤖 Summarizing with OpenAI...")
            
            # Send to OpenAI
            summary = await loop.run_in_executor(
                _executor,
                lambda: _summarize_with_openai(transcript, video_title)
            )
            
            if summary:
                await ctx.send("✅ **Summary (yt-dlp/Whisper + OpenAI):**\n")
                chunks = [summary[i:i+1900] for i in range(0, len(summary), 1900)]
                for chunk in chunks:
                    await ctx.send(chunk)
                    await asyncio.sleep(0.5)
            else:
                await ctx.send("❌ Summary generation failed.")
            
        except Exception as e:
            await ctx.send(f"❌ An error occurred: {str(e)}")
            print(f"Error details (!sum): {e}", file=sys.stderr)
            traceback.print_exc(file=sys.stderr)

    @commands.command(
        name='sum2',
        help='Summarize using yt-dlp/WhisperX, then Claude',
        description='Get transcript with yt-dlp first, then WhisperX fallback, summarize with Claude',
        usage='!sum2 <youtube_url>',
        brief='!sum2 <youtube_url>'
    )
    async def sum2_command(self, ctx, youtube_url: str):
        """!sum2 - Use yt-dlp/WhisperX, then Claude"""
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
            await ctx.send(f"📺 Video: {video_title}")
            await ctx.send("⏳ Getting transcript (yt-dlp first, then WhisperX)...")
            
            # Use yt-dlp first, then WhisperX fallback
            transcript, source = await loop.run_in_executor(
                _executor,
                lambda: _get_transcript(youtube_url)
            )
            
            if not transcript:
                await ctx.send("❌ Could not get transcript. Please try again later.")
                return
            
            await ctx.send(f"📝 Transcript source: {source}\n🧠 Summarizing with Claude...")
            
            # Send to Claude
            summary = await loop.run_in_executor(
                _executor,
                lambda: _summarize_with_anthropic(transcript, video_title)
            )
            
            if summary:
                await ctx.send("✅ **Summary (yt-dlp/Whisper + Claude):**\n")
                chunks = [summary[i:i+1900] for i in range(0, len(summary), 1900)]
                for chunk in chunks:
                    await ctx.send(chunk)
                    await asyncio.sleep(0.5)
            else:
                await ctx.send("❌ Summary generation failed.")
            
        except Exception as e:
            await ctx.send(f"❌ An error occurred: {str(e)}")
            print(f"Error details (!sum2): {e}", file=sys.stderr)
            traceback.print_exc(file=sys.stderr)


async def setup(bot: commands.Bot):
    """Setup function for the cog"""
    await bot.add_cog(SummarizationCog(bot))
    print("SummarizationCog loaded.")
