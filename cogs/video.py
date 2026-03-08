import discord
from discord.ext import commands
import asyncio
import os
import sys
import re
import requests
import json
import uuid
import io
import traceback
from bs4 import BeautifulSoup # For get_page_title
from concurrent.futures import ThreadPoolExecutor # For type hint
import time # Needed for time tracking in progress updates

# Import service clients (lightweight - just HTTP calls)
# No heavy dependencies like torch, opencv, moviepy!
import re  # For get_video_id

# Import the new WhisperX API client
from utils.whisperx_client import WhisperXClient, get_whisperx_client

# Import subtitle config for logo URLs
from subtitle_config import SUBTITLE_CONFIGS


def get_video_id(url: str) -> str:
    """Extract video ID from YouTube URL (lightweight, no dependencies)"""
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
    """Get video title (lightweight, uses YouTube oEmbed API)"""
    video_id = get_video_id(url)
    if not video_id:
        return "Unknown Video"
    
    try:
        import requests
        oembed_url = f"https://www.youtube.com/oembed?url=https://youtube.com/watch?v={video_id}&format=json"
        response = requests.get(oembed_url, timeout=10)
        if response.ok:
            data = response.json()
            return data.get('title', 'Unknown Video')
    except:
        pass
    return f"Video {video_id}"

class VideoCog(commands.Cog):
    """Cog for handling generic video processing, sheet processing, and FloGrappling videos."""
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        # Access executor via self.bot.executor
        # Use WhisperX client for API-based processing
        self.whisperx = get_whisperx_client()

    # --- Helper Functions (Moved from discord_bot.py) ---

    async def _send_message_with_rate_limit(self, ctx, message):
        """Sends a message, handling potential rate limits."""
        try:
            await ctx.send(message)
        except discord.errors.HTTPException as e:
            if e.status == 429: # Rate limited
                retry_after = e.retry_after if hasattr(e, 'retry_after') else 5
                print(f"Rate limited sending message, waiting {retry_after} seconds.")
                await asyncio.sleep(retry_after)
                await ctx.send(message) # Retry
            else:
                print(f"Error sending message: {e}", file=sys.stderr)
                # Optionally re-raise or just log
        except Exception as e:
             print(f"Unexpected error in _send_message_with_rate_limit: {e}", file=sys.stderr)

    def _get_page_title(self, url):
        """Fetches and parses the title from a FloGrappling page."""
        try:
            # Run in executor as it's blocking I/O
            response = requests.get(url, timeout=15)
            response.raise_for_status()
            soup = BeautifulSoup(response.text, 'html.parser')
            title_element = soup.find('h2', class_='headline font-family-bold mt-2 ng-star-inserted')
            if title_element:
                title = title_element.text.strip()
                title = re.sub(r'\s*Presented by FloGrappling\s*$', '', title, flags=re.IGNORECASE)
                return title
            else:
                print("No specific title found, falling back to default title")
                default_title = soup.find('title')
                return default_title.text.strip() if default_title else None
        except requests.RequestException as e:
            print(f"Error fetching page title: {e}")
            return None
        except Exception as e:
            print(f"Error parsing page title: {e}")
            return None

    def _sanitize_filename(self, filename):
        """Removes invalid characters for filenames."""
        return re.sub(r'[^\w\-_\. ]', '', filename).replace(' ', '_')

    def _parse_ffmpeg_progress(self, line):
        """Parses ffmpeg progress lines for out_time_ms."""
        if 'out_time_ms=' in line:
            try:
                time_in_us_str = line.split('out_time_ms=')[1].strip()
                time_in_us = int(time_in_us_str)
                return time_in_us / 1000000  # Convert microseconds to seconds
            except (IndexError, ValueError, TypeError) as e:
                print(f"Error parsing ffmpeg progress line '{line}': {e}", file=sys.stderr)
                return None
        elif 'time=' in line:
             try:
                 time_str = line.split('time=')[1].split()[0]
                 h, m, s = map(float, time_str.split(':'))
                 return h * 3600 + m * 60 + s
             except (IndexError, ValueError, TypeError) as e:
                 return None
        return None

    async def _process_flo_video(self, url, ctx):
        """Processes a FloGrappling video (download, compress, extract audio, upload)."""
        def get_cdn_url(cdn_video_id):
            try:
                print(f"Fetching CDN URL for video ID: {cdn_video_id}")
                response = requests.get(
                    url=f"https://api.flograppling.com/api/right-rail/videos/{cdn_video_id}", timeout=15
                )
                response.raise_for_status()
                return response.json()['data']['source_video']['playlist']
            except requests.exceptions.RequestException as e:
                print(f'HTTP Request failed: {e}')
                return None
            except (KeyError, json.JSONDecodeError) as e:
                 print(f"Error parsing Flo API response: {e}")
                 return None

        match = re.search(r'(?:/video/(\d+)|/videos\?playing=(\d+))', url)
        if match:
            video_id = match.group(1) or match.group(2)
        else:
            await self._send_message_with_rate_limit(ctx, "Error: Could not extract video ID from the provided URL.")
            return None, None
        print(f"Extracted video ID: {video_id}")

        loop = asyncio.get_event_loop()
        page_title = await loop.run_in_executor(self.bot.executor, self._get_page_title, url)
        cdn_url = await loop.run_in_executor(self.bot.executor, get_cdn_url, video_id)

        if not cdn_url:
            await self._send_message_with_rate_limit(ctx, "Error: Could not retrieve video source URL from FloGrappling API.")
            return None, None

        if page_title:
            safe_title = self._sanitize_filename(page_title)
            location_to_save = f"{safe_title}_{video_id}"
        else:
            location_to_save = f"flo_video_{video_id}"
            page_title = f"Flo Video {video_id}"

        # Send initial message
        await self._send_message_with_rate_limit(ctx, f"Starting processing for '{page_title}'...")
        
        temp_dir = "temp"
        os.makedirs(temp_dir, exist_ok=True)
        abs_temp_dir = os.path.abspath(temp_dir)
        video_output_path = os.path.join(abs_temp_dir, f"{location_to_save}.mp4")
        audio_output_path = os.path.join(abs_temp_dir, f"{location_to_save}.ogg")
        video_r2_url, audio_r2_url = None, None

        try:
            # Send message before starting ffmpeg
            await self._send_message_with_rate_limit(ctx, f"Processing '{page_title}': Downloading/Compressing video...")
            video_command = f'ffmpeg -i "{cdn_url}" -c:v libx264 -preset slower -crf 28 -c:a aac -b:a 64k -ar 44100 -vf scale=-2:540 -progress pipe:1 -y "{video_output_path}"'
            print(f"Running ffmpeg video command: {video_command}")
            process_video_cmd = await asyncio.create_subprocess_shell(
                video_command, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
            )
            
            # --- Progress Reading with New Message Sending (stdout only) ---
            async def read_ffmpeg_progress(stream, stream_name):
                 last_update_time = time.time()
                 update_interval = 60 # Seconds (Update every 60 seconds)
                 while True:
                     line = await stream.readline()
                     if not line: break
                     decoded_line = line.decode(errors='ignore').strip()
                     if not decoded_line: continue

                     # Only print stderr to console for debugging errors - COMMENTED OUT
                     # if stream_name == 'stderr':
                     #     print(f"[ffmpeg stderr] {decoded_line}", file=sys.stderr)
                     #     continue # Don't process stderr for progress updates

                     # Process stdout for progress (only process stdout for updates)
                     if stream_name == 'stdout':
                         seconds = self._parse_ffmpeg_progress(decoded_line)
                         if seconds is not None:
                             current_time = time.time()
                             if current_time - last_update_time >= update_interval:
                                 try:
                                     minutes = int(seconds // 60)
                                     secs = int(seconds % 60)
                                     time_str = f"{minutes}m {secs}s"
                                     progress_update_msg = f"Processing '{page_title}': Downloading/Compressing video ({time_str})..."
                                     print(f"Sending progress update: {progress_update_msg}")
                                     # Send NEW message
                                     await self._send_message_with_rate_limit(ctx, progress_update_msg) 
                                     last_update_time = current_time
                                 except Exception as send_err:
                                     print(f"Error sending progress message: {send_err}")
                                     last_update_time = float('inf') # Prevent further updates

            # Run readers concurrently
            await asyncio.gather(
                read_ffmpeg_progress(process_video_cmd.stdout, 'stdout'), 
                read_ffmpeg_progress(process_video_cmd.stderr, 'stderr')
            )
            await process_video_cmd.wait()
            # --- End Progress Reading ---

            if process_video_cmd.returncode != 0 or not os.path.exists(video_output_path):
                await self._send_message_with_rate_limit(ctx, f"Error: Video processing failed for '{page_title}'. Check logs.")
                try:
                    stdout_data, stderr_data = await process_video_cmd.communicate() 
                    if stderr_data: print(f"FFMPEG Video Error Output:\n{stderr_data.decode(errors='ignore')}", file=sys.stderr)
                except Exception: pass 
                return None, None
            
            await self._send_message_with_rate_limit(ctx, f"Processing '{page_title}': Extracting audio...")
            audio_command = f'ffmpeg -i "{video_output_path}" -vn -map_metadata -1 -ac 1 -c:a libopus -b:a 12k -application voip -y "{audio_output_path}"'
            process_audio = await asyncio.create_subprocess_shell(
                audio_command, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
            )
            stdout_audio, stderr_audio = await process_audio.communicate() 
            if stderr_audio: print(f"FFMPEG Audio stderr:\n{stderr_audio.decode(errors='ignore')}", file=sys.stderr) 

            if process_audio.returncode != 0 or not os.path.exists(audio_output_path):
                await self._send_message_with_rate_limit(ctx, f"Warning: Audio extraction failed for '{page_title}'. Check logs.")
                # Continue without audio
            else:
                 await self._send_message_with_rate_limit(ctx, f"Processing '{page_title}': Uploading files...")

            # Upload files using executor
            try:
                with open(video_output_path, "rb") as video_file:
                    video_r2_url = await loop.run_in_executor(self.bot.executor, upload_to_r2, video_file, location_to_save, "mp4")
            except Exception as e: await self._send_message_with_rate_limit(ctx, f"Error uploading video file: {e}")

            if os.path.exists(audio_output_path): 
                try:
                    with open(audio_output_path, "rb") as audio_file:
                        audio_r2_url = await loop.run_in_executor(self.bot.executor, upload_to_r2, audio_file, f"{location_to_save}_audio", "ogg")
                except Exception as e: await self._send_message_with_rate_limit(ctx, f"Error uploading audio file: {e}")

            # Final completion message sent by flo_command
            return video_r2_url, audio_r2_url

        finally: # Cleanup
            if os.path.exists(video_output_path):
                try: os.remove(video_output_path)
                except OSError as e: print(f"Warning: Could not remove {video_output_path}: {e}")
            if os.path.exists(audio_output_path):
                try: os.remove(audio_output_path)
                except OSError as e: print(f"Warning: Could not remove {audio_output_path}: {e}")

    # --- Video/Sheet Commands ---

    @commands.command(
        name='process_video',
        help='Process single video with specified parameters.',
        description='Use with social or direct link with specified parameters.',
        usage='!process_video <url> [format] [transcribe] [start] [end] [logo_type]',
        brief='!process_video https://example.com/video.mp4 landscape y 0:10 1:00 calf'
    )
    async def process_video_command(self, ctx, url: str, format: str = 'landscape', transcribe: str = 'n', start: str = None, end: str = None, logo_type: str = ''):
        """Process a video using the WhisperX API (orchestrator mode)."""
        await self._send_message_with_rate_limit(ctx, "Processing your video via API... This may take a while.")
        
        # Progress callback - only send updates every 60 seconds to avoid spam
        last_progress_update = 0
        async def progress_callback(status):
            nonlocal last_progress_update
            import time
            current_time = time.time()
            # Only send update if it's been more than 60 seconds since last update
            if current_time - last_progress_update > 60:
                await self._send_message_with_rate_limit(ctx, f"Status: {status}...")
                last_progress_update = current_time
        
        try:
            current_video_url = url
            
            # Step 1: Download video (if it's a social URL that needs downloading)
            if any(site in url for site in ['youtube.com', 'youtu.be', 'rumble.com', 'twitter.com', 'x.com', 'facebook.com', 'instagram.com']):
                await self._send_message_with_rate_limit(ctx, "Downloading video...")
                download_result = await self.whisperx.download_video(url, quality="best", progress_callback=progress_callback)
                current_video_url = download_result.video_url
                await self._send_message_with_rate_limit(ctx, f"Downloaded: {download_result.title}")
            
            # Step 2: Trim if start/end provided
            if start or end:
                await self._send_message_with_rate_limit(ctx, f"Trimming video...")
                start_sec = self._time_to_seconds(start) if start else 0
                end_sec = self._time_to_seconds(end) if end else None
                trim_result = await self.whisperx.trim_video(current_video_url, start=start_sec, end=end_sec, progress_callback=progress_callback)
                current_video_url = trim_result.output_url
            
            # Step 3: Reformat if needed
            if format and format != 'landscape':
                await self._send_message_with_rate_limit(ctx, f"Reformatting to {format}...")
                reformat_result = await self.whisperx.reformat_video(current_video_url, format=format, progress_callback=progress_callback)
                current_video_url = reformat_result.output_url
            
            # Step 4: Add logo if provided
            if logo_type:
                await self._send_message_with_rate_limit(ctx, "Adding logo overlay...")
                # Get logo URL from subtitle_config
                config = SUBTITLE_CONFIGS.get(format, SUBTITLE_CONFIGS['landscape'])
                logo_config = config.get('logo', {})
                logo_url = logo_config.get('url', {}).get(logo_type.lower())
                
                if logo_url:
                    position = "top-center"
                    opacity = logo_config.get('opacity', 0.8)
                    scale = logo_config.get('size_factor', 0.2)
                    
                    overlay_result = await self.whisperx.add_overlay(
                        current_video_url, 
                        logo_url, 
                        position=position,
                        opacity=opacity,
                        scale=scale,
                        progress_callback=progress_callback
                    )
                    current_video_url = overlay_result.output_url
                else:
                    await self._send_message_with_rate_limit(ctx, f"Warning: No logo URL found for type: {logo_type}")
            
            # Step 5: Transcribe if requested
            txt_url = None
            srt_url = None
            if transcribe.lower() == 'y':
                await self._send_message_with_rate_limit(ctx, "Transcribing video...")
                
                # Get font config from subtitle_config for the format
                config = SUBTITLE_CONFIGS.get(format, SUBTITLE_CONFIGS['landscape'])
                
                transcription = await self.whisperx.transcribe(
                    current_video_url, 
                    task="translate", 
                    progress_callback=progress_callback,
                    font_name=config.get('font', 'Arial'),
                    font_size=config.get('fontsize'),
                    font_color=config.get('color', '&HFFFFFF').replace('#', '&H'),
                    font_bold=config.get('bold', 0)
                )
                
                # Burn subtitles - use ASS for better styling support
                await self._send_message_with_rate_limit(ctx, "Burning subtitles...")
                burn_result = await self.whisperx.burn_subtitles(current_video_url, transcription.ass_url, progress_callback=progress_callback)
                current_video_url = burn_result.output_url
                
                txt_url = transcription.txt_url
                srt_url = transcription.srt_url
                ass_url = transcription.ass_url
            
            # Step 6: Loop if needed (check duration - this is tricky with URLs, skip for now)
            # In production, you'd get video duration first
            
            # Final result
            await self._send_message_with_rate_limit(ctx, f"Video processed successfully!")
            await self._send_message_with_rate_limit(ctx, f"Video URL: {current_video_url}")
            if txt_url and srt_url:
                await self._send_message_with_rate_limit(ctx, f"Transcripts: TXT: {txt_url}, SRT: {srt_url}")
                
        except Exception as e:
            await self._send_message_with_rate_limit(ctx, f"Error processing video: {str(e)}")
            print(f"Error details (!process_video): {e}", file=sys.stderr)
            traceback.print_exc(file=sys.stderr)

    def _time_to_seconds(self, time_str: str) -> float:
        """Convert time string (HH:MM:SS or MM:SS or SS) to seconds."""
        if not time_str:
            return 0
        parts = time_str.split(':')
        if len(parts) == 3:
            return int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])
        elif len(parts) == 2:
            return int(parts[0]) * 60 + int(parts[1])
        else:
            return float(time_str)

    @commands.command(name='process_sheet', help='Download/trim/transcribe videos from social links (takes direct video DL links also) in Google Sheet')
    async def process_sheet_command(self, ctx):
        """Process videos from Google Sheet using the WhisperX API (orchestrator mode)."""
        await self._send_message_with_rate_limit(ctx, "Starting to process the Google Sheet via API... This may take a while.")
        
        # Progress callback - only send updates every 60 seconds to avoid spam
        last_progress_update = 0
        async def progress_callback(status):
            nonlocal last_progress_update
            import time
            current_time = time.time()
            # Only send update if it's been more than 60 seconds since last update
            if current_time - last_progress_update > 60:
                await self._send_message_with_rate_limit(ctx, f"Status: {status}...")
                last_progress_update = current_time
        
        try:
            # Import Google Sheets dependencies
            from google.oauth2 import service_account
            from googleapiclient.discovery import build
            import os
            
            # Set up Google Sheets API - use environment variable for credentials path
            creds_path = os.getenv('GOOGLE_APPLICATION_CREDENTIALS')
            if not creds_path:
                raise Exception("GOOGLE_APPLICATION_CREDENTIALS environment variable not set!")
            
            creds = service_account.Credentials.from_service_account_file(
                creds_path,
                scopes=['https://www.googleapis.com/auth/spreadsheets']
            )
            service = build('sheets', 'v4', credentials=creds)
            
            SPREADSHEET_ID = os.getenv('SPREADSHEET_ID')
            RANGE_NAME = 'vidclipper!A2:I'
            
            # Read the sheet
            sheet = service.spreadsheets()
            result = sheet.values().get(spreadsheetId=SPREADSHEET_ID, range=RANGE_NAME).execute()
            values = result.get('values', [])
            
            if not values:
                await self._send_message_with_rate_limit(ctx, "No data found in the Google Sheet.")
                return
            
            await self._send_message_with_rate_limit(ctx, f"Found {len(values)} rows to process")
            
            # Process each row
            for row_index, row in enumerate(values, start=2):
                if not row or not row[0].strip():
                    continue
                    
                # Check if already processed
                if len(row) >= 8 and row[7].lower() == 'y':
                    await self._send_message_with_rate_limit(ctx, f"Skipping row {row_index}: Already processed")
                    continue
                
                url = row[0]
                format_type = row[1] if len(row) > 1 else 'landscape'
                transcribe = row[2] if len(row) > 2 else 'n'
                start = row[3] if len(row) > 3 and row[3] else None
                end = row[4] if len(row) > 4 and row[4] else None
                logo_type = row[5] if len(row) > 5 else ''
                no_loop = len(row) > 8 and row[8].lower() == 'y'
                
                await self._send_message_with_rate_limit(ctx, f"Processing row {row_index}: {url[:50]}...")
                
                try:
                    current_video_url = url
                    
                    # Debug: Print what we're about to process
                    print(f"[DEBUG] Row {row_index}: URL={url}, format={format_type}, transcribe={transcribe}, start={start}, end={end}, logo={logo_type}")
                    
                    # Step 1: Download video
                    if any(site in url for site in ['youtube.com', 'youtu.be', 'rumble.com', 'twitter.com', 'x.com', 'facebook.com', 'instagram.com']):
                        await self._send_message_with_rate_limit(ctx, f"Row {row_index}: Downloading...")
                        try:
                            download_result = await self.whisperx.download_video(url, quality="best", progress_callback=progress_callback)
                            current_video_url = download_result.video_url
                            print(f"[DEBUG] Downloaded video URL: {current_video_url}")
                        except Exception as download_error:
                            await self._send_message_with_rate_limit(ctx, f"Row {row_index}: Download failed - {str(download_error)}")
                            continue
                    
                    # Step 2: Trim if needed
                    if start or end:
                        await self._send_message_with_rate_limit(ctx, f"Row {row_index}: Trimming...")
                        start_sec = self._time_to_seconds(start) if start else 0
                        end_sec = self._time_to_seconds(end) if end else None
                        trim_result = await self.whisperx.trim_video(current_video_url, start=start_sec, end=end_sec, progress_callback=progress_callback)
                        current_video_url = trim_result.output_url
                    
                    # Step 3: Reformat if needed
                    if format_type and format_type != 'landscape':
                        await self._send_message_with_rate_limit(ctx, f"Row {row_index}: Reformatting to {format_type}...")
                        reformat_result = await self.whisperx.reformat_video(current_video_url, format=format_type, progress_callback=progress_callback)
                        current_video_url = reformat_result.output_url
                    
                    # Step 4: Add logo if needed
                    if logo_type:
                        await self._send_message_with_rate_limit(ctx, f"Row {row_index}: Adding logo...")
                        # Get logo URL from subtitle_config
                        config = SUBTITLE_CONFIGS.get(format_type, SUBTITLE_CONFIGS['landscape'])
                        logo_config = config.get('logo', {})
                        logo_url = logo_config.get('url', {}).get(logo_type.lower())
                        
                        if logo_url:
                            position = "top-center"
                            opacity = logo_config.get('opacity', 0.8)
                            scale = logo_config.get('size_factor', 0.2)
                            
                            overlay_result = await self.whisperx.add_overlay(
                                current_video_url, 
                                logo_url, 
                                position=position,
                                opacity=opacity,
                                scale=scale,
                                progress_callback=progress_callback
                            )
                            current_video_url = overlay_result.output_url
                        else:
                            await self._send_message_with_rate_limit(ctx, f"Row {row_index}: Warning - No logo URL for type {logo_type}")
                    
                    # Step 5: Transcribe if needed
                    combined_urls = None
                    if transcribe.lower() == 'y':
                        await self._send_message_with_rate_limit(ctx, f"Row {row_index}: Transcribing...")
                        
                        # Get font config from subtitle_config for the format
                        config = SUBTITLE_CONFIGS.get(format_type, SUBTITLE_CONFIGS['landscape'])
                        
                        transcription = await self.whisperx.transcribe(
                            current_video_url, 
                            task="translate", 
                            progress_callback=progress_callback,
                            font_name=config.get('font', 'Arial'),
                            font_size=config.get('fontsize'),
                            font_color=config.get('color', '&HFFFFFF').replace('#', '&H'),
                            font_bold=config.get('bold', 0)
                        )
                        
                        await self._send_message_with_rate_limit(ctx, f"Row {row_index}: Burning subtitles...")
                        # Use ASS subtitles for better burning results with styling
                        burn_result = await self.whisperx.burn_subtitles(current_video_url, transcription.ass_url, progress_callback=progress_callback)
                        current_video_url = burn_result.output_url
                        
                        combined_urls = f"TXT: {transcription.txt_url}\nSRT: {transcription.srt_url}\nASS: {transcription.ass_url}"
                    
                    # Step 6: Loop if needed (skip for now - need duration info)
                    
                    # Update sheet with result
                    update_range = f"vidclipper!G{row_index}:H{row_index}"
                    update_body = {
                        'values': [[current_video_url, 'y']]
                    }
                    
                    if combined_urls:
                        update_range_v = f"vidclipper!X{row_index}:X{row_index}"
                        update_body_v = {
                            'values': [[combined_urls]]
                        }
                        sheet.values().update(spreadsheetId=SPREADSHEET_ID, range=update_range_v, valueInputOption='RAW', body=update_body_v).execute()
                    
                    sheet.values().update(spreadsheetId=SPREADSHEET_ID, range=update_range, valueInputOption='RAW', body=update_body).execute()
                    
                    await self._send_message_with_rate_limit(ctx, f"Row {row_index}: Completed!")
                    
                except Exception as row_error:
                    await self._send_message_with_rate_limit(ctx, f"Row {row_index}: Error - {str(row_error)}")
                    # Update sheet with error
                    update_range = f"vidclipper!G{row_index}:H{row_index}"
                    update_body = {
                        'values': [['', str(row_error)]]
                    }
                    sheet.values().update(spreadsheetId=SPREADSHEET_ID, range=update_range, valueInputOption='RAW', body=update_body).execute()
            
            await self._send_message_with_rate_limit(ctx, "Google Sheet processing completed successfully!")
            
        except Exception as e:
            await self._send_message_with_rate_limit(ctx, f"An error occurred while processing the Google Sheet: {str(e)}")
            print(f"Error details (!process_sheet): {e}", file=sys.stderr)
            traceback.print_exc(file=sys.stderr)

    @commands.command(
        name='flo',
        help='Process a FloGrappling video',
        description='Download, compress, and extract audio from a FloGrappling video',
        usage='!flo <flograppling_url>',
        brief='!flo <flograppling_url>'
    )
    async def flo_command(self, ctx, url: str):
        if not isinstance(ctx.channel, discord.DMChannel):
            await self._send_message_with_rate_limit(ctx, "This command can only be used in DMs.")
            return
        if not url.startswith('https://www.flograppling.com/'):
            await self._send_message_with_rate_limit(ctx, "Please provide a valid FloGrappling URL.")
            return
        try:
            # Call the internal helper method
            video_r2_url, audio_r2_url = await self._process_flo_video(url, ctx) 
            if video_r2_url: # Check if at least video was uploaded
                msg = f"Video processed successfully!\nCompressed video: {video_r2_url}"
                if audio_r2_url: msg += f"\nExtracted audio: {audio_r2_url}"
                else: msg += "\n(Audio extraction or upload failed)"
                await self._send_message_with_rate_limit(ctx, msg)
            else:
                await self._send_message_with_rate_limit(ctx, "Failed to process the FloGrappling video. Please check logs.")
        except Exception as e:
            await self._send_message_with_rate_limit(ctx, f"An error occurred while processing the FloGrappling video: {str(e)}")
            print(f"Error details (!flo): {e}", file=sys.stderr)
            traceback.print_exc(file=sys.stderr)

    @commands.command(
        name='pull',
        help='Extract video from social/direct link, transcribe, and upload to R2',
        description='Extracts video from any social URL or direct link, transcribes it, and uploads the transcripts to R2',
        usage='!pull <video_url>',
        brief='!pull <video_url>, will upload transcript to R2'
    )
    async def pull_command(self, ctx, video_url: str):
        if not isinstance(ctx.channel, discord.DMChannel):
            await self._send_message_with_rate_limit(ctx, "This command can only be used in DMs.")
            return
        await self._send_message_with_rate_limit(ctx, "Processing your video. This may take a few minutes...")
        loop = asyncio.get_event_loop()
        video_title = f"pulled_video_{uuid.uuid4()}"
        video_path = None
        temp_dir = "temp"
        os.makedirs(temp_dir, exist_ok=True)

        try:
            srt_transcript, plain_transcript, source = None, None, "Unknown"

            if "youtube.com" in video_url or "youtu.be" in video_url:
                try:
                    video_id = await loop.run_in_executor(self.bot.executor, get_video_id, video_url)
                    video_title = await loop.run_in_executor(self.bot.executor, get_video_title, video_url) or video_title
                    srt_transcript, plain_transcript, source = await loop.run_in_executor(self.bot.executor, fetch_transcript, video_id)
                    print(f"Transcript source: {source}")
                except Exception as e:
                    print(f"Error fetching YouTube transcript: {e}. Falling back to Whisper.")
                    source = "Whisper (YT fallback)"
                    video_path = os.path.join(temp_dir, f"{video_title}.mp4")
                    await self._send_message_with_rate_limit(ctx, "Downloading YouTube video for Whisper transcription...")
                    await loop.run_in_executor(self.bot.executor, download_video, video_url, video_path)
                    if not os.path.exists(video_path): raise Exception("Download failed")
                    await self._send_message_with_rate_limit(ctx, "Transcribing with Whisper...")
                    plain_transcript, srt_transcript = await loop.run_in_executor(self.bot.executor, transcribe_with_whisper, video_path)
            else:
                source = "Whisper"
                video_path = os.path.join(temp_dir, f"{video_title}.mp4")
                await self._send_message_with_rate_limit(ctx, "Downloading video for Whisper transcription...")
                await loop.run_in_executor(self.bot.executor, download_video, video_url, video_path)
                if not os.path.exists(video_path): raise Exception("Download failed")
                await self._send_message_with_rate_limit(ctx, "Transcribing with Whisper...")
                plain_transcript, srt_transcript = await loop.run_in_executor(self.bot.executor, transcribe_with_whisper, video_path)

            if plain_transcript and srt_transcript:
                await self._send_message_with_rate_limit(ctx, "Uploading transcripts to R2...")
                txt_file = io.BytesIO(plain_transcript.encode('utf-8'))
                srt_file = io.BytesIO(srt_transcript.encode('utf-8'))
                txt_r2_url = await loop.run_in_executor(self.bot.executor, upload_to_r2, txt_file, f"{video_title}_transcript", 'txt')
                srt_r2_url = await loop.run_in_executor(self.bot.executor, upload_to_r2, srt_file, f"{video_title}_transcript", 'srt')
                
                if txt_r2_url and srt_r2_url:
                     await self._send_message_with_rate_limit(ctx, f"Transcript URLs:\nPlain text: {txt_r2_url}\nSRT: {srt_r2_url}")
                else:
                     await self._send_message_with_rate_limit(ctx, "Failed to upload one or both transcripts to R2.")
                await self._send_message_with_rate_limit(ctx, f"Transcript source: {source}")
            else:
                 await self._send_message_with_rate_limit(ctx, "Transcription failed or produced no content. Cannot upload transcripts.")

        except Exception as e:
            await self._send_message_with_rate_limit(ctx, f"An error occurred while processing the video: {str(e)}")
            print(f"Error details (!pull): {e}", file=sys.stderr)
            traceback.print_exc(file=sys.stderr)
        finally:
            if video_path and os.path.exists(video_path):
                try: os.remove(video_path); print(f"Cleaned up temp file: {video_path}")
                except OSError as e: print(f"Warning: Could not remove temp file {video_path}: {e}")

# The mandatory setup function called by discord.py when loading the Cog
async def setup(bot: commands.Bot):
    await bot.add_cog(VideoCog(bot))
    print("VideoCog loaded.")