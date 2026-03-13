"""
Writer Cog - AI-powered article/script generation from Google Sheets

Commands:
- !aiwriter - Reads AIWRITER sheet, scrapes URLs, optionally transcribes videos, generates articles with OpenAI or Claude
- !ytwriter - Reads YT Script Rewriter sheet, gets YouTube transcripts, generates scripts with OpenAI or Claude

Uses:
- yt-dlp for video/audio downloading
- OpenAI GPT or Claude for article generation
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
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Optional, Dict, Any, List, Tuple

# Import configuration and logging
try:
    from utils.config import get_settings
    from utils.logging_config import get_logger
except ImportError as e:
    print(f"Error importing utils in WriterCog: {e}", file=sys.stderr)
    get_settings = None
    get_logger = None

logger = get_logger(__name__) if get_logger else None
settings = get_settings() if get_settings else None

# Create a safe logger wrapper that handles None
class SafeLogger:
    def __getattr__(self, name):
        return lambda msg, **kwargs: print(f"{name.upper()}: {msg}")

safe_logger = SafeLogger()

# Use safe_logger as logger if logger is None
if logger is None:
    logger = safe_logger

# Module-level executor
_executor = ThreadPoolExecutor(max_workers=4)

# Sheet names
AIWRITER_SHEET = "AIWRITER"
YTWRITER_SHEET = "YT Script Rewriter"

# Webhook URL (from settings or default)
MAKE_WEBHOOK_URL = os.getenv("MAKE_WEBHOOK_URL", "https://hook.us1.make.com/fcoam5l9s9581xq94594f7u33k43cbpn")

# Claude wrapper URL
CLAUDE_WRAPPER_URL = os.getenv("CLAUDE_WRAPPER_URL", "https://claudeapi.jeffrey-epstein.com/generate")
CLAUDE_WRAPPER_PASSWORD = os.getenv("CLAUDE_WRAPPER_PASSWORD", "")

# Valid sites
VALID_SITES = ['calf', 'doc', 'bred', 'vult', 'kz']


# ============================================================================
# R2 UPLOAD FUNCTION (for BytesIO content)
# ============================================================================

def upload_to_r2_from_bytesio(content: bytes, destination: str) -> str:
    """Upload bytes content to R2 using boto3 directly"""
    import boto3
    from botocore.client import Config
    from dotenv import load_dotenv
    import os
    
    # Load environment variables from .env
    load_dotenv()
    
    r2_access_key = os.getenv('R2_ACCESS_KEY_ID')
    r2_secret_key = os.getenv('R2_SECRET_ACCESS_KEY')
    r2_endpoint = os.getenv('R2_ENDPOINT_URL')
    r2_bucket = os.getenv('R2_BUCKET_NAME')
    
    if not all([r2_access_key, r2_secret_key, r2_endpoint, r2_bucket]):
        raise Exception("Missing R2 credentials")
    
    s3 = boto3.client(
        's3',
        endpoint_url=r2_endpoint,
        aws_access_key_id=r2_access_key,
        aws_secret_access_key=r2_secret_key,
        region_name='auto',
        config=Config(s3={'addressing_style': 'virtual'})
    )
    
    s3.put_object(Bucket=r2_bucket, Key=destination, Body=content)
    
    # Return public URL
    public_url = f"{r2_endpoint}/{r2_bucket}/{destination}"
    return public_url


# ============================================================================
# GOOGLE SHEETS FUNCTIONS
# ============================================================================

def get_google_sheets_service():
    """Get Google Sheets service"""
    from google.oauth2 import service_account
    from googleapiclient.discovery import build
    
    # First check settings, then check env var directly, then use default path
    creds_path = settings.google.credentials_path if settings else None
    if not creds_path:
        creds_path = os.getenv("GOOGLE_CREDENTIALS_PATH", "")
    if not creds_path:
        creds_path = "/app/credentials/google-service-account.json"
    
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
    
    # First check settings, then check env var directly
    spreadsheet_id = settings.google.spreadsheet_id if settings else None
    if not spreadsheet_id:
        spreadsheet_id = os.getenv("SPREADSHEET_ID", "")
    
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
    
    # First check settings, then check env var directly
    spreadsheet_id = settings.google.spreadsheet_id if settings else None
    if not spreadsheet_id:
        spreadsheet_id = os.getenv("SPREADSHEET_ID", "")
    
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
# VIDEO TRANSCRIPTION FUNCTIONS (WhisperX API based)
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


def download_text_file(url: str) -> Optional[str]:
    """Download and return text file content"""
    try:
        response = requests.get(url, timeout=30)
        if response.ok:
            return response.text
        return None
    except Exception as e:
        logger.error(f"Error downloading text file: {e}")
        return None


def transcribe_with_whisperx_url(video_url: str) -> Tuple[Optional[str], Optional[str]]:
    """Transcribe video directly from URL using WhisperX API
    Works with YouTube, Instagram, and any direct video URL
    Returns: (transcript_text or None, r2_url_to_transcript or None)"""
    import time
    
    WHISPERX_API_URL = os.getenv("WHISPERX_API_URL", "https://whisperx.jeffrey-epstein.com")
    r2_transcript_url = None
    
    try:
        # Submit transcription job with URL directly
        data = {
            'url': video_url,
            'model': 'large-v3',
            'task': 'transcribe'
        }
        response = requests.post(
            f"{WHISPERX_API_URL}/transcribe/url",
            data=data,
            timeout=60
        )
        
        if response.ok:
            result = response.json()
            job_id = result.get('job_id')
            if job_id:
                # Poll for completion
                max_retries = 60  # 5 minutes max
                retry_count = 0
                while retry_count < max_retries:
                    status_response = requests.get(f"{WHISPERX_API_URL}/jobs/{job_id}", timeout=30)
                    status_data = status_response.json()
                    status = status_data.get('status')
                    
                    if status == 'completed':
                        # Get the result - can be direct text or URLs
                        job_result = status_data.get('result', {})
                        if isinstance(job_result, dict):
                            # Check for R2 URL to transcript
                            urls = job_result.get('urls', {})
                            if urls.get('txt'):
                                r2_transcript_url = urls['txt']
                                # Download the txt from R2
                                txt_response = requests.get(r2_transcript_url, timeout=30)
                                if txt_response.ok:
                                    return (txt_response.text, r2_transcript_url)
                            # Fallback to preview
                            preview = job_result.get('preview', '')
                            if preview:
                                return (preview, None)
                        return (str(job_result), r2_transcript_url)
                    elif status == 'failed':
                        error = status_data.get('error', 'Unknown error')
                        logger.error(f"WhisperX job failed: {error}")
                        return (None, None)
                    elif status in ['queued', 'downloading', 'processing']:
                        logger.info(f"WhisperX job status: {status}")
                        time.sleep(5)
                        retry_count += 1
                    else:
                        logger.warning(f"Unknown WhisperX status: {status}")
                        return (None, None)
                
                logger.error(f"WhisperX job timed out after {max_retries} retries")
                return (None, None)
        else:
            logger.error(f"WhisperX API error: {response.status_code} - {response.text}")
            return (None, None)
    except Exception as e:
        logger.error(f"WhisperX URL transcription error: {e}")
        return (None, None)


def get_transcript_for_video(video_url: str) -> Tuple[Optional[str], Optional[str]]:
    """Get transcript - handles video URLs, text URLs, and plain text
    Returns: (transcript_text or None, r2_url or None)"""
    
    # Clean URL - remove common trailing stuff that might affect detection
    clean_url = video_url.strip()
    
    # Check if it's a text file URL (more robust check)
    # Check for .txt in URL, or common text file hosting patterns
    if ('.txt' in clean_url.lower() or 
        'textfile' in clean_url.lower() or 
        '/text/' in clean_url or
        '/raw/' in clean_url and '.txt' in clean_url.lower()):
        logger.info(f"Detected text file URL, downloading: {clean_url}")
        text = download_text_file(clean_url)
        return (text, clean_url)  # Return the URL as the R2 URL
    
    # Check if it's a YouTube URL
    video_id = get_video_id(clean_url)
    
    if video_id:
        # Try YouTube Transcript API first
        transcript = get_youtube_transcript(video_id)
        if transcript:
            logger.info(f"Got transcript from YouTube API for {video_id}")
            return (transcript, None)  # No R2 URL for YouTube Transcript API
    
    # Check if it looks like a URL (has http/https)
    if clean_url.startswith('http://') or clean_url.startswith('https://'):
        # Use WhisperX API for video URLs
        logger.info(f"Using WhisperX API for: {clean_url}")
        return transcribe_with_whisperx_url(clean_url)
    
    # Otherwise, treat as plain text transcript
    logger.info(f"Using as plain text transcript")
    return (video_url, None)  # Return input as transcript, no R2 URL


# ============================================================================
# AI FUNCTIONS - OpenAI and Claude
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


def call_claude(prompt: str, system_prompt: str = "", model: str = "sonnet", force_direct: bool = False) -> tuple:
    """Call Claude API - wrapper or direct (no fallback)
    Returns: (response or None, used_direct boolean)"""
    used_direct = False
    
    # If forcing direct, skip wrapper entirely
    if force_direct:
        logger.info("Forcing direct Claude API (skipping wrapper)")
        used_direct = True
    elif CLAUDE_WRAPPER_PASSWORD:
        # Try wrapper API first
        try:
            response = requests.post(
                CLAUDE_WRAPPER_URL,
                headers={
                    "Content-Type": "application/json",
                    "x-api-key": CLAUDE_WRAPPER_PASSWORD
                },
                json={
                    "prompt": prompt,
                    "system_prompt": system_prompt
                },
                timeout=600
            )
            response.raise_for_status()
            result = response.json().get('result')
            logger.info("[call_claude] Claude wrapper succeeded")
            return (result, used_direct)
        except Exception as e:
            logger.error(f"Claude wrapper failed: {e}. Not falling back to direct API.")
            return (None, used_direct)
    else:
        # No wrapper password, use direct
        logger.info("No CLAUDE_WRAPPER_PASSWORD, using direct API")
        used_direct = True
    
    # Use direct Anthropic API
    api_key = os.getenv("ANTHROPIC_API_KEY", "")
    if not api_key:
        logger.error("No ANTHROPIC_API_KEY available")
        return (None, used_direct)
    
    try:
        response = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
                "Content-Type": "application/json"
            },
            json={
                "model": f"claude-{model}-4-6",
                "max_tokens": 4000,
                "system": system_prompt,
                "messages": [{"role": "user", "content": prompt}]
            },
            timeout=120
        )
        response.raise_for_status()
        data = response.json()
        result = data["content"][0]["text"]
        logger.info("[call_claude] Claude direct API succeeded")
        return (result, used_direct)
    except Exception as e:
        logger.error(f"Claude direct API error: {e}")
        return (None, used_direct)


def generate_aiwriter_article(scraped_data: str, title: str, additional_context: str, ai_provider: str = "claude") -> tuple:
    """Generate article using OpenAI or Claude
    Returns: (article or None, provider_used string)"""
    
    system_prompt = """You are an engaging news writer who knows how to write interesting and original stories.
Write me an article with the given title. Use the provided news sources and video transcript as your main reference for facts and quotes.
Use the quotes exactly as written, but do not plagiarize. Do not invent or change the meaning of quotes.
Do not include any mentions of drinking, street fights, fighting, or alcohol.
Do not include the word count. Only output the article body.
Avoid the use of M dashes. Clean up grammar in quotes, don't change wording.
Eliminate all ai cliche phrases and sentences. Make all weight in lbs but leave kilogram value in ()s.
ALL OUTPUT ONLY IN ENGLISH.
Avoid using these words outside of quotes: Addicting, Addiction, Bomb, Booze, Cheat, Crime, Die, Drunk, Execute, Explode, Extreme, Gun, Hate, Hell, Insane, Kill, Naked, Profanity, Shoot, Sober, Stupid, Substance, Suffer, Torture, Victim, Shocking, Brutal"""

    prompt = f"""Write me an article with a title "{title}". Use the news sources and video transcript below as your main reference:

{scraped_data}

Also consider this background context:

{additional_context}

Only output the article body. Do not output the title."""

    if ai_provider == "openai":
        article = call_openai(prompt)
        return (article, "OpenAI")
    elif ai_provider == "claude_direct":
        result, used_direct = call_claude(prompt, system_prompt, force_direct=True)
        return (result, "Claude (direct)")
    else:
        # Default to Claude wrapper (no fallback)
        result, used_direct = call_claude(prompt, system_prompt, force_direct=False)
        if result:
            return (result, "Claude (wrapper)")
        else:
            return (result, "Claude (wrapper failed)")


def generate_ytwriter_script(transcript: str, title: str, custom_prompt: str, ai_provider: str = "claude") -> tuple:
    """Generate script using OpenAI or Claude
    Returns: (script or None, provider_used string)"""
    
    system_prompt = """You are a skilled script writer. Write a 300-500 word article from the provided transcript.
Do not give word counts. Do not repeat instructions back. Only give your output.
Do not invent, paraphrase or change quotes. Avoid M dashes. Clean up grammar.
Eliminate all ai cliche phrases. Make weight in lbs with kilogram in (). ENGLISH ONLY.
Avoid: Addiction, Bomb, Booze, Crime, Die, Drunk, Execute, Explode, Gun, Hate, Kill, Shoot, Sober, Stupid, Brutal"""

    prompt = f"""Write a 300-500 word article corresponding to this title: {title}

{custom_prompt}

Transcript:
{transcript}"""

    if ai_provider == "openai":
        script = call_openai(prompt)
        return (script, "OpenAI")
    elif ai_provider == "claude_direct":
        result, used_direct = call_claude(prompt, system_prompt, force_direct=True)
        return (result, "Claude (direct)")
    else:
        # Default to Claude wrapper (no fallback)
        result, used_direct = call_claude(prompt, system_prompt, force_direct=False)
        if result:
            return (result, "Claude (wrapper)")
        else:
            return (result, "Claude (wrapper failed)")


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
        help='Process AIWRITER sheet - use -openai for OpenAI, -direct for Claude direct API',
        description='Reads Google Sheet, scrapes articles, generates articles with OpenAI or Claude',
        usage='!aiwriter [-openai | -direct]',
        brief='!aiwriter [-openai | -direct]'
    )
    async def aiwriter_command(self, ctx, option: str = None):
        """!aiwriter - Generate articles from scraped content and video transcripts
        Use -openai for OpenAI, -direct for Claude direct API"""
        
        if not isinstance(ctx.channel, discord.DMChannel):
            await ctx.send("This command can only be used in DMs.")
            return
        
        # Determine AI provider
        if option and option.lower() == "-openai":
            ai_provider = "openai"
        elif option and option.lower() == "-direct":
            ai_provider = "claude_direct"
        else:
            ai_provider = "claude"
        
        # Check required API keys for aiwriter
        if ai_provider == "openai":
            if not os.getenv("OPENAI_API_KEY"):
                await ctx.send("❌ OPENAI_API_KEY not set in environment")
                return
            provider_msg = "OpenAI (gpt-4o-mini)"
        elif ai_provider == "claude_direct":
            if not os.getenv("ANTHROPIC_API_KEY"):
                await ctx.send("❌ ANTHROPIC_API_KEY not set in environment")
                return
            provider_msg = "Claude (direct API)"
        else:
            # Default: use wrapper, no fallback
            if not CLAUDE_WRAPPER_PASSWORD:
                await ctx.send("❌ CLAUDE_WRAPPER_PASSWORD not set in environment")
                return
            provider_msg = "Claude (wrapper)"
        
        await ctx.send(f"📝 Starting AIWRITER processing with {provider_msg}...")
        logger.info(f"[aiwriter] Command invoked with provider: {ai_provider}")
        
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
                    transcript, _ = await loop.run_in_executor(
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
                
                # Generate article with AI
                if title:
                    await self._progress_update(ctx, f"Row {i}: Generating article with {provider_msg}...", last_update, buffer)
                    article, provider_used = await loop.run_in_executor(
                        _executor,
                        lambda: generate_aiwriter_article(combined_output, title, additional_context, ai_provider)
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
                            lambda: update_sheet_cell(AIWRITER_SHEET, f"I{i}", f"{provider_used} failed")
                        )
            
            await ctx.send(f"✅ AIWRITER processing complete! Processed {processed} rows.")
            logger.info(f"[aiwriter] Completed. Processed {processed} rows")
            
        except Exception as e:
            await ctx.send(f"❌ Error: {str(e)}")
            logger.exception(f"[aiwriter] Error: {e}")
    
    @commands.command(
        name='ytwriter',
        help='Process YT Script Rewriter sheet - use -openai for OpenAI, -direct for Claude direct API',
        description='Reads Google Sheet, fetches transcripts, generates scripts with OpenAI or Claude',
        usage='!ytwriter [-openai | -direct]',
        brief='!ytwriter [-openai | -direct]'
    )
    async def ytwriter_command(self, ctx, option: str = None):
        """!ytwriter - Generate scripts from YouTube transcripts
        Use -openai for OpenAI, -direct for Claude direct API"""
        
        if not isinstance(ctx.channel, discord.DMChannel):
            await ctx.send("This command can only be used in DMs.")
            return
        
        # Determine AI provider
        if option and option.lower() == "-openai":
            ai_provider = "openai"
        elif option and option.lower() == "-direct":
            ai_provider = "claude_direct"
        else:
            ai_provider = "claude"
        
        # Check required API keys for ytwriter
        if ai_provider == "openai":
            if not os.getenv("OPENAI_API_KEY"):
                await ctx.send("❌ OPENAI_API_KEY not set in environment")
                return
            provider_msg = "OpenAI (gpt-4o-mini)"
        elif ai_provider == "claude_direct":
            if not os.getenv("ANTHROPIC_API_KEY"):
                await ctx.send("❌ ANTHROPIC_API_KEY not set in environment")
                return
            provider_msg = "Claude (direct API)"
        else:
            # Default: use wrapper, no fallback
            if not CLAUDE_WRAPPER_PASSWORD:
                await ctx.send("❌ CLAUDE_WRAPPER_PASSWORD not set in environment")
                return
            provider_msg = "Claude (wrapper)"
        
        await ctx.send(f"📝 Starting YT Script Rewriter processing with {provider_msg}...")
        logger.info(f"[ytwriter] Command invoked with provider: {ai_provider}")
        
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
                transcript, r2_url = await loop.run_in_executor(
                    _executor,
                    lambda: get_transcript_for_video(link)
                )
                
                if not transcript:
                    await loop.run_in_executor(
                        _executor,
                        lambda: update_sheet_cell(YTWRITER_SHEET, f"G{i}", "Failed to get transcript")
                    )
                    continue
                
                # Generate script with AI
                await self._progress_update(ctx, f"Row {i}: Generating script with {provider_msg}...", last_update, buffer)
                script, provider_used = await loop.run_in_executor(
                    _executor,
                    lambda: generate_ytwriter_script(transcript, title, prompt, ai_provider)
                )
                
                if script:
                    # Save to column E
                    await loop.run_in_executor(
                        _executor,
                        lambda: update_sheet_cell(YTWRITER_SHEET, f"E{i}", script)
                    )
                    
                    # Save transcript URL to column F
                    # r2_url is returned from get_transcript_for_video
                    if r2_url:
                        await loop.run_in_executor(
                            _executor,
                            lambda: update_sheet_cell(YTWRITER_SHEET, f"F{i}", r2_url)
                        )
                        logger.info(f"Saved transcript R2 URL to column F: {r2_url[:50]}...")
                    else:
                        logger.info("No R2 URL available for transcript")
                    
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
                        lambda: update_sheet_cell(YTWRITER_SHEET, f"G{i}", f"{provider_used} failed")
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
