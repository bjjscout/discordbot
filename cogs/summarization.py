"""
Summarization Cog - YouTube Video Summarization Commands

Provides commands for summarizing YouTube videos using:
- WhisperX API for transcription (Docker deployment)
- OpenAI GPT for summarization
- Anthropic Claude (wrapper API + direct API fallback)

Commands:
- !sumw - Uses Whisper first, then Claude
- !sum  - Try YouTube transcript first, fallback to Whisper, then OpenAI
- !sum2 - Try YouTube transcript first, fallback to Whisper, then Claude
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


def _fetch_transcript_youtube_api(video_id: str) -> Optional[tuple]:
    """Try to fetch transcript using YouTube Transcript API"""
    try:
        from youtube_transcript_api import YouTubeTranscriptApi
        from youtube_transcript_api.formatters import SRTFormatter
        
        ytt_api = YouTubeTranscriptApi()
        fetched_transcript = ytt_api.fetch(video_id)
        
        # Format as SRT
        formatter = SRTFormatter()
        srt_transcript = formatter.format_transcript(fetched_transcript)
        
        # Plain text
        plain_transcript = ' '.join([entry.text for entry in fetched_transcript])
        
        return srt_transcript, plain_transcript, "YouTube API"
    except Exception as e:
        print(f"YouTube Transcript API failed: {e}", file=sys.stderr)
        return None


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


def _get_transcript(video_id: str, youtube_url: str, progress_callback=None) -> tuple:
    """
    Try to get transcript in order:
    1. YouTube Transcript API
    2. WhisperX (fallback)
    
    Returns: (transcript_text, source)
    """
    # 1. Try YouTube Transcript API
    if progress_callback:
        progress_callback("Trying YouTube Transcript API...")
    
    result = _fetch_transcript_youtube_api(video_id)
    if result:
        srt, plain, source = result
        if progress_callback:
            progress_callback(f"Got transcript from {source}!")
        return plain, source
    
    # 2. Fall back to WhisperX
    if progress_callback:
        progress_callback("Falling back to WhisperX...")
    
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
    # Use wrapper API first
    wrapper_url = "https://claudeapi.jeffrey-epstein.com/generate"
    wrapper_key = "jiujitsu2020"
    
    system_prompt = """You are a helpful AI assistant that summarizes YouTube video transcripts.
Provide a concise but comprehensive summary of the key points covered in the video.
Focus on the main topics, important details, and conclusions.
Format the summary in a clear, readable way with bullet points or sections."""

    user_prompt = f"""Please summarize the following transcript from the video "{video_title}":

{transcript}

Summary:"""

    # Try wrapper API first
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
        help='Summarize using Whisper first, then Claude',
        description='Transcribe with WhisperX first, then summarize with Claude',
        usage='!sumw <youtube_url>',
        brief='!sumw <youtube_url>'
    )
    async def sumw_command(self, ctx, youtube_url: str):
        """!sumw - Uses Whisper first, then Claude"""
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
            await ctx.send("⏳ Transcribing with WhisperX...")
            
            # !sumw uses Whisper FIRST (skip YouTube API)
            transcript, source = None, "Failed"
            
            # Direct to WhisperX (like the old !sumw behavior)
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
                await ctx.send("✅ **Summary (Whisper + Claude):**\n")
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
        help='Try YouTube transcript first, fallback to Whisper, then OpenAI',
        description='Try YouTube API, then Whisper, then OpenAI',
        usage='!sum <youtube_url>',
        brief='!sum <youtube_url>'
    )
    async def sum_command(self, ctx, youtube_url: str):
        """!sum - Try YouTube transcript first, then OpenAI"""
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
            
            # Try transcript sources in order (YouTube API -> WhisperX)
            transcript, source = await loop.run_in_executor(
                _executor,
                lambda: _get_transcript(video_id, youtube_url)
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
                await ctx.send("✅ **Summary (YouTube/Whisper + OpenAI):**\n")
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
        help='Try YouTube transcript first, fallback to Whisper, then Claude',
        description='Try YouTube API, then Whisper, then Claude',
        usage='!sum2 <youtube_url>',
        brief='!sum2 <youtube_url>'
    )
    async def sum2_command(self, ctx, youtube_url: str):
        """!sum2 - Try YouTube transcript first, then Claude"""
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
            
            # Try transcript sources in order (YouTube API -> WhisperX)
            transcript, source = await loop.run_in_executor(
                _executor,
                lambda: _get_transcript(video_id, youtube_url)
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
                await ctx.send("✅ **Summary (YouTube/Whisper + Claude):**\n")
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