"""
Writer Cog - AI-powered article/script generation from Google Sheets

Commands:
- !aiwriter - Reads AIWRITER sheet, scrapes URLs, optionally transcribes videos, generates articles with OpenAI
- !ytwriter - Reads YT Script Rewriter sheet, gets YouTube transcripts, generates scripts with OpenAI

Uses:
- yt-dlp for video/audio downloading
- OpenAI GPT for article generation
- Google Sheets API for data
- R2 for transcript storage
- Make.com webhooks for publishing
"""

import discord
from discord.ext import commands
import asyncio
import os
import sys
import requests
import io
import uuid
import re
import traceback
from concurrent.futures import ThreadPoolExecutor
from typing import Optional, Dict, Any, List

# Import configuration and logging
try:
    from utils.config import get_settings
    from utils.logging_config import get_logger
    from utils.utility import upload_to_r2
except ImportError as e:
    print(f"Error importing utils in WriterCog: {e}", file=sys.stderr)

logger = get_logger(__name__)
settings = get_settings()

# Module-level executor
_executor = ThreadPoolExecutor(max_workers=4)

# Sheet names
AIWRITER_SHEET = "AIWRITER"
YTWRITER_SHEET = "YT Script Rewriter"

# Webhook URL (from settings or default)
MAKE_WEBHOOK_URL = os.getenv("MAKE_WEBHOOK_URL", "https://hook.us1.make.com/fcoam5l9s9581xq94594f7u33k43cbpn")

# Valid sites
VALID_SITES = ['calf', 'doc', 'bred', 'vult', 'kz']


# ============================================================================
# GOOGLE SHEETS FUNCTIONS
# ============================================================================

def get_google_sheets_service():
    """Get Google Sheets service"""
    from google.oauth2 import service_account
    from googleapiclient.discovery import build
    
    creds_path = settings.google.credentials_path
    if not creds_path or not os.path.exists(creds_path):
        raise Exception(f"Google credentials file not found: {creds_path}")
    
    creds = service_account.Credentials.from_service_account_file(
        creds_path,
        scopes=['https://www.googleapis.com/auth/spreadsheets']
    )
    return build('sheets', 'v4', credentials=creds)


def read_sheet(sheet_name: str, range_name: str = "A2:H") -> List[List[str]]:
    """Read data from Google Sheet"""
    service = get_google_sheets_service()
    spreadsheet_id = settings.google.spreadsheet_id
    
    if not spreadsheet_id:
        raise Exception("SPREADSHEET_ID not configured")
    
    full_range = f"'{sheet_name}'!{range_name}"
    result = service.spreadsheets().values().get(
        spreadsheetId=spreadsheet_id,
        range=full_range
    ).execute()
    
    return result.get('values', [])


def update_sheet_cell(sheet_name: str, cell: str, value: str):
    """Update a cell in Google Sheet"""
    service = get_google_sheets_service()
    spreadsheet_id = settings.google.spreadsheet_id
    
    range_name = f"'{sheet_name}'!{cell}"
    body = {'values': [[value]]}
    
    service.spreadsheets().values().update(
        spreadsheetId=spreadsheet_id,
        range=range_name,
        valueInputOption='RAW',
        body=body
    ).execute()


# ============================================================================
# SCRAPING FUNCTIONS
# ============================================================================

def scrape_with_beautifulsoup(url: str) -> Optional[Dict[str, Any]]:
    """Scrape URL using BeautifulSoup"""
    from bs4 import BeautifulSoup
    
    try:
        response = requests.get(url, timeout=30)
        soup = BeautifulSoup(response.content, 'html.parser')
        
        # Extract title
        title = soup.find('title').text if soup.find('title') else ''
        
        # Find main content
        main_content = soup.find('article') or soup.find('main') or soup.find('div', class_='content')
        if not main_content:
            main_content = soup.body
        
        # Extract body
        body = ' '.join([p.text for p in main_content.find_all('p')])
        
        # Extract links
        social_links = []
        other_links = []
        for a in main_content.find_all('a', href=True):
            href = a['href']
            if href.startswith('http'):
                if any(social in href for social in ['facebook.com', 'twitter.com', 'instagram.com']):
                    social_links.append(href)
                else:
                    other_links.append(href)
        
        links = (social_links + other_links)[:5]
        
        return {
            "postTitle": title,
            "postBody": body,
            "postLinks": links
        }
    except Exception as e:
        logger.error(f"BeautifulSoup scrape error: {e}", url=url)
        return None


def scrape_article(url: str) -> Optional[Dict[str, Any]]:
    """Scrape article with fallback"""
    logger.info(f"Scraping URL: {url}")
    
    # Try BeautifulSoup first
    result = scrape_with_beautifulsoup(url)
    
    if result is None or not result.get('postBody'):
        logger.warning("BeautifulSoup failed, skipping Apify fallback (not configured)")
    
    if result is None or not result.get('postBody'):
        logger.error("Scraping completely failed", url=url)
        return None
    
    # Ensure postLinks is a list
    if 'postLinks' in result and isinstance(result['postLinks'], str):
        result['postLinks'] = result['postLinks'].split(',')
    
    logger.info(f"Scraped: {result.get('postTitle', 'No title')[:50]}...")
    return result


# ============================================================================
# VIDEO TRANSCRIPTION FUNCTIONS (yt-dlp based)
# ============================================================================

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


def get_youtube_transcript(video_id: str) -> Optional[str]:
    """Get transcript using YouTube Transcript API"""
    from youtube_transcript_api import YouTubeTranscriptApi
    
    proxy_url = os.getenv("YOUTUBE_PROXY", "")
    
    try:
        if proxy_url:
            from youtube_transcript_api.proxies import GenericProxyConfig
            ytt_api = YouTubeTranscriptApi(
                proxy_config=GenericProxyConfig(http_url=proxy_url, https_url=proxy_url)
            )
        else:
            ytt_api = YouTubeTranscriptApi()
        
        fetched_transcript = ytt_api.fetch(video_id)
        transcript_text = ' '.join([snippet.text for snippet in fetched_transcript])
        return transcript_text
    except Exception as e:
        logger.error(f"YouTube transcript error: {e}", video_id=video_id)
        return None


def download_video_audio(url: str, temp_dir: str = "/tmp") -> Optional[str]:
    """Download video/audio using yt-dlp"""
    import yt_dlp
    
    video_id = get_video_id(url)
    if not video_id:
        # Try as direct URL
        video_id = "direct_" + str(uuid.uuid4())[:8]
    
    output_path = os.path.join(temp_dir, f"{video_id}.%(ext)s")
    
    proxy_url = os.getenv("YOUTUBE_PROXY", "")
    ydl_opts = {
        'format': 'bestaudio/best',
        'outtmpl': output_path,
        'quiet': True,
        'no_warnings': True,
        'socket_timeout': 30,
        'postprocessors': [{
            'key': 'FFmpegExtractAudio',
            'preferredcodec': 'wav',
        }],
    }
    
    if proxy_url:
        ydl_opts['proxy'] = proxy_url
    
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([url])
        
        # Find the downloaded file
        wav_path = os.path.join(temp_dir, f"{video_id}.wav")
        if os.path.exists(wav_path):
            return wav_path
        
        # Check for other audio formats
        for ext in ['mp3', 'm4a', 'ogg']:
            alt_path = os.path.join(temp_dir, f"{video_id}.{ext}")
            if os.path.exists(alt_path):
                return alt_path
        
        return None
    except Exception as e:
        logger.error(f"yt-dlp download error: {e}", url=url)
        return None


def transcribe_with_whisperx_api(audio_path: str) -> Optional[str]:
    """Transcribe audio using WhisperX API"""
    WHISPERX_API_URL = os.getenv("WHISPERX_API_URL", "https://whisperx.jeffrey-epstein.com")
    
    try:
        # Submit transcription job
        with open(audio_path, 'rb') as f:
            files = {'file': f}
            data = {'model': 'large-v3', 'task': 'transcribe'}
            response = requests.post(
                f"{WHISPERX_API_URL}/transcribe/file",
                files=files,
                data=data,
                timeout=300
            )
        
        if response.ok:
            result = response.json()
            job_id = result.get('job_id')
            if job_id:
                # Poll for completion
                while True:
                    status_response = requests.get(f"{WHISPERX_API_URL}/jobs/{job_id}", timeout=30)
                    status_data = status_response.json()
                    if status_data.get('status') == 'completed':
                        return status_data.get('result', {}).get('text', '')
                    elif status_data.get('status') == 'failed':
                        return None
                    asyncio.sleep(5)
        return None
    except Exception as e:
        logger.error(f"WhisperX API error: {e}")
        return None


def get_transcript_for_video(video_url: str) -> Optional[str]:
    """Get transcript - try YouTube API first, then WhisperX"""
    video_id = get_video_id(video_url)
    
    if video_id:
        # Try YouTube Transcript API
        transcript = get_youtube_transcript(video_id)
        if transcript:
            logger.info(f"Got transcript from YouTube API for {video_id}")
            return transcript
    
    # Fall back to WhisperX (requires downloading first)
    logger.info("Trying WhisperX transcription...")
    audio_path = download_video_audio(video_url)
    if audio_path:
        try:
            transcript = transcribe_with_whisperx_api(audio_path)
            # Cleanup
            try:
                os.remove(audio_path)
            except:
                pass
            return transcript
        except Exception as e:
            logger.error(f"WhisperX transcription failed: {e}")
    
    return None


# ============================================================================
# OPENAI FUNCTIONS
# ============================================================================

def call_openai(prompt: str, model: str = "gpt-4o-mini") -> Optional[str]:
    """Call OpenAI API"""
    import openai
    
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        logger.error("OPENAI_API_KEY not set")
        return None
    
    try:
        client = openai.OpenAI(api_key=api_key)
        response = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}]
        )
        return response.choices[0].message.content
    except Exception as e:
        logger.error(f"OpenAI API error: {e}")
        return None


def generate_aiwriter_article(scraped_data: str, title: str, additional_context: str) -> Optional[str]:
    """Generate article using OpenAI for aiwriter"""
    
    prompt = f"""You are an engaging news writer who knows how to write interesting and original stories. Write me an article with a title "{title}". Use the news sources and video transcript below as your main reference for facts and quotes:

{scraped_data}

Also, take into account the points and ideas from this background context:

{additional_context}

Use the quotes exactly as written, but do not plagiarize or repeat the wording from the additional context. Instead, interpret its ideas and present them in your own narrative voice. Do not invent or change the meaning of quotes. Do not include any mentions of drinking, street fights, fighting, or alcohol. Do not include the word count. Do not output the title or restate "{title}". Only output the article body. Avoid the use of M dashes. Clean up grammar in quotes, don't change wording. Eliminate all ai cliche phrases and sentences. Make all weight in lbs but leave kilogram value in ()s. ALL OUTPUT ONLY IN ENGLISH.

Avoid using any of the following words outside of quotes Addicting/Addiction Bomb Booze/Boozy Cheat/Cheating Click Crime Die/Died/Dead Download [Drug name] Drunk/Drunken Execute/Execution Explode/Explosion Extreme Gun Hangover/Hungover Hate/Hatred Hell Insane Jerk Kill/Killer Naked [Profanity/Expletive] Shoot Shot/Shots Sober Stream Stupid Substance Suffer Torture Victim Shocking Brutal"""
    
    return call_openai(prompt)


def generate_ytwriter_script(transcript: str, title: str, custom_prompt: str) -> Optional[str]:
    """Generate script using OpenAI for ytwriter"""
    
    prompt = f"""
You have been provided with a transcript where you are to write a 300 to 500 word article corresponding to this title: {title}.

{custom_prompt}

Transcript:
{transcript}

Do not give me word counts. Do not repeat my instructions back to me. Only give me your output. Do not invent, paraphase or change the wording of quotes. Do not deviate and cover other topics in subheadings. Avoid the use of M dashes. Clean up grammar in quotes, don't change wording. Eliminate all ai cliche phrases and sentences. Make all weight in lbs but leave kilogram value in ()s. ALL OUTPUT ONLY IN ENGLISH.

Avoid using any of the following words outside of quotes Addicting/Addiction Bomb Booze/Boozy Cheat/Cheating Click Crime Die/Died/Dead Download [Drug name] Drunk/Drunken Execute/Execution Explode/Explosion Extreme Gun Hangover/Hungover Hate/Hatred Hell Insane Jerk Kill/Killer Naked [Profanity/Expletive] Shoot Shot/Shots Sober Stream Stupid Substance Suffer Torture Victim Shocking Brutal
"""
    
    return call_openai(prompt)


# ============================================================================
# WEBHOOK FUNCTIONS
# ============================================================================

def send_webhook(blog: str, title: str, body: str, row: int, source: str = "aiwriter") -> bool:
    """Send webhook to Make.com"""
    
    data = {
        "blog": blog,
        "Title": title,
        "Body": body,
        "row": row,
        "source": source
    }
    
    try:
        response = requests.post(MAKE_WEBHOOK_URL, json=data, timeout=30)
        return response.status_code == 200
    except Exception as e:
        logger.error(f"Webhook error: {e}")
        return False


# ============================================================================
# COG COMMANDS
# ============================================================================

class WriterCog(commands.Cog):
    """Cog for AI-powered article/script generation commands."""
    
    def __init__(self, bot: commands.Bot):
        self.bot = bot
    
    async def _send_message(self, ctx, message: str):
        """Send a message to Discord"""
        try:
            await ctx.send(message)
        except Exception as e:
            logger.error(f"Error sending message: {e}")
    
    async def _progress_update(self, ctx, message: str, last_update_time: list, buffer: list):
        """Send periodic progress updates"""
        current_time = asyncio.get_event_loop().time()
        buffer.append(message)
        
        if current_time - last_update_time[0] >= 3 or len(buffer) >= 3:
            await ctx.send("📝 " + "\n".join(buffer))
            last_update_time[0] = current_time
            buffer.clear()
    
    @commands.command(
        name='aiwriter',
        help='Process AIWRITER sheet - scrape URLs, transcribe videos, generate articles with OpenAI',
        description='Reads Google Sheet, scrapes articles, optionally transcribes videos, generates articles using OpenAI',
        usage='!aiwriter',
        brief='!aiwriter'
    )
    async def aiwriter_command(self, ctx):
        """!aiwriter - Generate articles from scraped content and video transcripts"""
        
        if not isinstance(ctx.channel, discord.DMChannel):
            await ctx.send("This command can only be used in DMs.")
            return
        
        # Check API key
        if not os.getenv("OPENAI_API_KEY"):
            await ctx.send("❌ OPENAI_API_KEY not set in environment")
            return
        
        await ctx.send("📝 Starting AIWRITER processing with OpenAI...")
        logger.info("[aiwriter] Command invoked")
        
        loop = asyncio.get_event_loop()
        last_update = [0]
        buffer = []
        
        try:
            # Read sheet
            await self._progress_update(ctx, "Reading AIWRITER Google Sheet...", last_update, buffer)
            rows = await loop.run_in_executor(_executor, lambda: read_sheet(AIWRITER_SHEET))
            
            if not rows:
                await ctx.send("❌ No data found in AIWRITER sheet")
                return
            
            await ctx.send(f"📊 Found {len(rows)} rows to process")
            
            processed = 0
            for i, row in enumerate(rows, start=2):
                # Skip if column G (index 6) already has content
                if len(row) > 6 and row[6].strip():
                    logger.info(f"[aiwriter] Skipping row {i} - column G already has content")
                    continue
                
                if len(row) < 2:
                    continue
                
                await self._progress_update(ctx, f"Processing row {i}...", last_update, buffer)
                
                # Get data from columns
                urls = [row[0], row[1]] if len(row) > 1 else []
                video_link = row[2] if len(row) > 2 else ""
                title = row[3] if len(row) > 3 else ""
                additional_context = row[4] if len(row) > 4 else ""
                site = row[5].lower() if len(row) > 5 else ""
                
                # Scrape URLs
                combined_output = ""
                for url_idx, url in enumerate(urls, 1):
                    if url:
                        await self._progress_update(ctx, f"Row {i}: Scraping URL #{url_idx}...", last_update, buffer)
                        scraped = await loop.run_in_executor(_executor, lambda u=url: scrape_article(u))
                        if scraped:
                            combined_output += f"Title #{url_idx}\n{scraped.get('postTitle', '')}\n\n"
                            combined_output += f"Body #{url_idx}\n{scraped.get('postBody', '')}\n\n"
                            links = scraped.get('postLinks', [])
                            combined_output += f"Links #{url_idx}\n" + ('\n'.join(links) if links else "No links") + "\n\n"
                
                # Process video if provided
                if video_link:
                    await self._progress_update(ctx, f"Row {i}: Getting video transcript...", last_update, buffer)
                    transcript = await loop.run_in_executor(
                        _executor,
                        lambda: get_transcript_for_video(video_link)
                    )
                    if transcript:
                        combined_output += f"Transcript:\n{transcript}\n\n"
                
                # Save scraped data to column H
                if combined_output:
                    await loop.run_in_executor(
                        _executor,
                        lambda: update_sheet_cell(AIWRITER_SHEET, f"H{i}", combined_output)
                    )
                
                # Generate article with OpenAI
                if title:
                    await self._progress_update(ctx, f"Row {i}: Generating article with OpenAI...", last_update, buffer)
                    article = await loop.run_in_executor(
                        _executor,
                        lambda: generate_aiwriter_article(combined_output, title, additional_context)
                    )
                    
                    if article:
                        # Save to column G
                        await loop.run_in_executor(
                            _executor,
                            lambda: update_sheet_cell(AIWRITER_SHEET, f"G{i}", article)
                        )
                        
                        # Send webhook if site specified
                        if site in VALID_SITES:
                            await self._progress_update(ctx, f"Row {i}: Sending webhook for {site}...", last_update, buffer)
                            success = await loop.run_in_executor(
                                _executor,
                                lambda: send_webhook(site, title, article, i, "aiwriter")
                            )
                            status = "Webhook sent" if success else "Webhook failed"
                            await loop.run_in_executor(
                                _executor,
                                lambda: update_sheet_cell(AIWRITER_SHEET, f"I{i}", status)
                            )
                        elif site:
                            await loop.run_in_executor(
                                _executor,
                                lambda: update_sheet_cell(AIWRITER_SHEET, f"I{i}", f"Invalid site: {site}")
                            )
                        
                        processed += 1
                    else:
                        await loop.run_in_executor(
                            _executor,
                            lambda: update_sheet_cell(AIWRITER_SHEET, f"I{i}", "OpenAI generation failed")
                        )
            
            await ctx.send(f"✅ AIWRITER processing complete! Processed {processed} rows.")
            logger.info(f"[aiwriter] Completed. Processed {processed} rows")
            
        except Exception as e:
            await ctx.send(f"❌ Error: {str(e)}")
            logger.exception(f"[aiwriter] Error: {e}")
    
    @commands.command(
        name='ytwriter',
        help='Process YT Script Rewriter sheet - get transcripts, generate scripts with OpenAI',
        description='Reads Google Sheet, fetches YouTube transcripts, generates scripts using OpenAI',
        usage='!ytwriter',
        brief='!ytwriter'
    )
    async def ytwriter_command(self, ctx):
        """!ytwriter - Generate scripts from YouTube transcripts"""
        
        if not isinstance(ctx.channel, discord.DMChannel):
            await ctx.send("This command can only be used in DMs.")
            return
        
        # Check API key
        if not os.getenv("OPENAI_API_KEY"):
            await ctx.send("❌ OPENAI_API_KEY not set in environment")
            return
        
        await ctx.send("📝 Starting YT Script Rewriter processing with OpenAI...")
        logger.info("[ytwriter] Command invoked")
        
        loop = asyncio.get_event_loop()
        last_update = [0]
        buffer = []
        
        try:
            # Read sheet
            await self._progress_update(ctx, "Reading YT Script Rewriter Google Sheet...", last_update, buffer)
            rows = await loop.run_in_executor(_executor, lambda: read_sheet(YTWRITER_SHEET))
            
            if not rows:
                await ctx.send("❌ No data found in YT Script Rewriter sheet")
                return
            
            await ctx.send(f"📊 Found {len(rows)} rows to process")
            
            processed = 0
            for i, row in enumerate(rows, start=2):
                # Skip if column E (index 4) already has content
                if len(row) > 4 and row[4].strip():
                    logger.info(f"[ytwriter] Skipping row {i} - column E already has content")
                    continue
                
                if len(row) < 4 or not row[0] or not row[3]:
                    continue
                
                await self._progress_update(ctx, f"Processing row {i}...", last_update, buffer)
                
                # Get data
                link = row[0]
                site = row[1].lower() if len(row) > 1 else ""
                title = row[2] if len(row) > 2 else ""
                prompt = row[3] if len(row) > 3 else ""
                
                # Get transcript
                await self._progress_update(ctx, f"Row {i}: Getting transcript...", last_update, buffer)
                transcript = await loop.run_in_executor(
                    _executor,
                    lambda: get_transcript_for_video(link)
                )
                
                if not transcript:
                    await loop.run_in_executor(
                        _executor,
                        lambda: update_sheet_cell(YTWRITER_SHEET, f"G{i}", "Failed to get transcript")
                    )
                    continue
                
                # Generate script with OpenAI
                await self._progress_update(ctx, f"Row {i}: Generating script with OpenAI...", last_update, buffer)
                script = await loop.run_in_executor(
                    _executor,
                    lambda: generate_ytwriter_script(transcript, title, prompt)
                )
                
                if script:
                    # Save to column E
                    await loop.run_in_executor(
                        _executor,
                        lambda: update_sheet_cell(YTWRITER_SHEET, f"E{i}", script)
                    )
                    
                    # Upload transcript to R2
                    try:
                        transcript_file = io.BytesIO(transcript.encode('utf-8'))
                        video_id = get_video_id(link) or "direct"
                        r2_url = await loop.run_in_executor(
                            _executor,
                            lambda: upload_to_r2(transcript_file, f"transcript_{video_id}.txt")
                        )
                        if r2_url:
                            await loop.run_in_executor(
                                _executor,
                                lambda: update_sheet_cell(YTWRITER_SHEET, f"F{i}", r2_url)
                            )
                    except Exception as e:
                        logger.error(f"R2 upload error: {e}")
                    
                    # Send webhook if site specified
                    if site in VALID_SITES:
                        await self._progress_update(ctx, f"Row {i}: Sending webhook for {site}...", last_update, buffer)
                        success = await loop.run_in_executor(
                            _executor,
                            lambda: send_webhook(site, title, script, i, "ytwriter")
                        )
                        status = "Webhook sent" if success else "Webhook failed"
                        await loop.run_in_executor(
                            _executor,
                            lambda: update_sheet_cell(YTWRITER_SHEET, f"G{i}", status)
                        )
                    elif site:
                        await loop.run_in_executor(
                            _executor,
                            lambda: update_sheet_cell(YTWRITER_SHEET, f"G{i}", f"Invalid site: {site}")
                        )
                    
                    processed += 1
                else:
                    await loop.run_in_executor(
                        _executor,
                        lambda: update_sheet_cell(YTWRITER_SHEET, f"G{i}", "OpenAI generation failed")
                    )
            
            await ctx.send(f"✅ YT Script Rewriter processing complete! Processed {processed} rows.")
            logger.info(f"[ytwriter] Completed. Processed {processed} rows")
            
        except Exception as e:
            await ctx.send(f"❌ Error: {str(e)}")
            logger.exception(f"[ytwriter] Error: {e}")


async def setup(bot: commands.Bot):
    """Setup function for the cog"""
    await bot.add_cog(WriterCog(bot))
    logger.info("WriterCog loaded.")
