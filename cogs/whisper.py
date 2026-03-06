"""
WhisperX Cog - Direct WhisperX API integration with polling

Provides commands for transcribing videos using the WhisperX API:
- !whisper - Submit job and poll for results
- !whisperstatus - Check job status
"""

import discord
from discord.ext import commands
import asyncio
import os
import sys
import requests
import traceback
from concurrent.futures import ThreadPoolExecutor
from typing import Optional
import time
import re

# Get WhisperX API URL from environment
WHISPERX_API_URL = os.getenv("WHISPERX_API_URL", "https://whisperx.jeffrey-epstein.com")


def _is_valid_url(url: str) -> bool:
    """Check if the provided string is a valid URL."""
    if not url or not isinstance(url, str):
        return False
    url = url.strip()
    # Basic URL pattern
    pattern = re.compile(
        r"^(https?://)?"  # http:// or https:// (optional)
        r"([\w-]+\.)+[\w-]+"  # domain
        r"(\.[a-zA-Z]{2,})?"  # TLD
        r"(/[\w.,@?^=%&:/~+#-]*)?$",  # path (optional)
        re.IGNORECASE
    )
    return bool(pattern.match(url))


class WhisperCog(commands.Cog):
    """Cog for WhisperX transcription commands."""
    
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        # Track active jobs per user
        self.user_jobs = {}  # user_id -> list of job_ids
    
    @commands.command(
        name='whisper',
        help='Transcribe a YouTube video using WhisperX API',
        description='Submits a transcription job and polls for results',
        brief='!whisper <YouTube URL>'
    )
    async def whisper_command(self, ctx, *, video_url: str):
        """Submit a transcription job and poll for results."""
        if not isinstance(ctx.channel, discord.DMChannel):
            await ctx.send("This command can only be used in DMs.")
            return
        
        # Validate URL
        if not _is_valid_url(video_url):
            await ctx.send("❌ Error: Please provide a valid URL (e.g., https://www.youtube.com/watch?v=...)")
            return
        
        await ctx.send(f"📝 Submitting transcription job for: {video_url}")
        
        loop = asyncio.get_event_loop()
        
        try:
            # Submit job
            job_id = await loop.run_in_executor(
                self.bot.executor,
                self._submit_job,
                video_url
            )
            
            if not job_id:
                await ctx.send("❌ Failed to submit transcription job.")
                return
            
            await ctx.send(f"✅ Job submitted! ID: `{job_id}`\n⏳ Polling for results every 5 seconds (max 2 hours)...")
            
            # Track job for this user
            user_id = str(ctx.author.id)
            if user_id not in self.user_jobs:
                self.user_jobs[user_id] = []
            self.user_jobs[user_id].append(job_id)
            
            # Poll for results
            result = await loop.run_in_executor(
                self.bot.executor,
                self._poll_job,
                job_id
            )
            
            if result is None:
                await ctx.send("❌ Transcription failed: Unknown error - job completed but no result")
                return
            
            # Success - send results
            await ctx.send("✅ Transcription complete! Here are the results:")
            
            # Send preview text
            if result.get("preview"):
                preview = result["preview"]
                # Split into chunks if too long
                if len(preview) > 1900:
                    chunks = [preview[i:i+1900] for i in range(0, len(preview), 1900)]
                    for chunk in chunks:
                        await ctx.send(chunk)
                        await asyncio.sleep(0.5)
                else:
                    await ctx.send(preview)
            
            # Send URLs if available
            urls = result.get("urls", {})
            if urls.get("txt"):
                await ctx.send(f"📄 **TXT Transcript:** {urls['txt']}")
            if urls.get("srt"):
                await ctx.send(f"📋 **SRT Transcript:** {urls['srt']}")
            if urls.get("ass"):
                await ctx.send(f"🎬 **ASS Transcript:** {urls['ass']}")
                
        except Exception as e:
            await ctx.send(f"❌ Error: {str(e)}")
            print(f"Error in whisper_command: {e}", file=sys.stderr)
            traceback.print_exc(file=sys.stderr)
    
    @commands.command(
        name='whisperjobs',
        help='List your active WhisperX jobs',
        description='Shows all your active transcription jobs',
        brief='!whisperjobs'
    )
    async def whisperjobs_command(self, ctx):
        """List user's active jobs."""
        if not isinstance(ctx.channel, discord.DMChannel):
            await ctx.send("This command can only be used in DMs.")
            return
        
        user_id = str(ctx.author.id)
        jobs = self.user_jobs.get(user_id, [])
        
        if not jobs:
            await ctx.send("You have no active WhisperX jobs.")
            return
        
        await ctx.send(f"You have {len(jobs)} active job(s):")
        for job_id in jobs:
            await ctx.send(f"  • `{job_id}`")
    
    @commands.command(
        name='whisperstatus',
        help='Check status of a specific job',
        description='Check the status of a WhisperX transcription job',
        brief='!whisperstatus <job_id>'
    )
    async def whisperstatus_command(self, ctx, job_id: str):
        """Check job status."""
        if not isinstance(ctx.channel, discord.DMChannel):
            await ctx.send("This command can only be used in DMs.")
            return
        
        loop = asyncio.get_event_loop()
        
        try:
            status_data = await loop.run_in_executor(
                self.bot.executor,
                self._check_status,
                job_id
            )
            
            if not status_data:
                await ctx.send("❌ Could not fetch job status.")
                return
            
            status = status_data.get("status", "unknown")
            await ctx.send(f"📊 Job `{job_id}` status: **{status}**")
            
            if status == "completed":
                result = status_data.get("result")
                if result:
                    await ctx.send("✅ Job completed! Use !whisperdownload to get results.")
                    if result.get("preview"):
                        preview = result["preview"][:200] + "..." if len(result["preview"]) > 200 else result["preview"]
                        await ctx.send(f"📝 Preview: {preview}")
            elif status == "failed":
                error = status_data.get("error", "Unknown error")
                await ctx.send(f"❌ Job failed: {error}")
                
        except Exception as e:
            await ctx.send(f"❌ Error: {str(e)}")
    
    def _submit_job(self, video_url: str) -> Optional[str]:
        """Submit a transcription job to WhisperX API."""
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
            print(f"Error submitting job: {e}", file=sys.stderr)
            return None
    
    def _poll_job(self, job_id: str, max_wait: int = 7200, poll_interval: int = 5) -> Optional[dict]:
        """
        Poll for job completion.
        
        Args:
            job_id: The job ID to poll
            max_wait: Maximum wait time in seconds (default 2 hours)
            poll_interval: Seconds between polls (default 5)
            
        Returns:
            Result dict with preview and URLs, or None on failure
        """
        start_time = time.time()
        
        while time.time() - start_time < max_wait:
            status_data = self._check_status(job_id)
            
            if not status_data:
                time.sleep(poll_interval)
                continue
            
            status = status_data.get("status")
            
            if status == "completed":
                result = status_data.get("result")
                
                # Check if result exists and has valid data
                if result is None:
                    print(f"Job {job_id} completed but result is None", file=sys.stderr)
                    return None
                
                # Check for URLs in result
                if "urls" not in result:
                    print(f"Job {job_id} completed but no urls in result: {result}", file=sys.stderr)
                    return None
                
                return result
                
            elif status == "failed":
                error = status_data.get("error", "Unknown error")
                print(f"Job {job_id} failed: {error}", file=sys.stderr)
                return None
            
            # Still processing, wait and poll again
            time.sleep(poll_interval)
        
        # Timeout
        print(f"Job {job_id} timed out after {max_wait} seconds", file=sys.stderr)
        return None
    
    def _check_status(self, job_id: str) -> Optional[dict]:
        """Check job status from WhisperX API."""
        try:
            url = f"{WHISPERX_API_URL}/jobs/{job_id}"
            response = requests.get(url, timeout=30)
            response.raise_for_status()
            return response.json()
            
        except Exception as e:
            print(f"Error checking job status: {e}", file=sys.stderr)
            return None


async def setup(bot: commands.Bot):
    """Setup function for the cog."""
    await bot.add_cog(WhisperCog(bot))
    print("WhisperCog loaded.")
