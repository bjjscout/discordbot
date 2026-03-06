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
from concurrent.futures import ThreadPoolExecutor # For type hint

# Import necessary functions from other modules
# NOTE: No heavy dependencies - using service clients instead of app4!
try:
    # Import lightweight modules
    import ig_processing  # Lightweight - no torch/opencv
    
    # Import service clients for heavy operations (call external APIs)
    from utils.service_clients import (
        get_video_processing_client,
        get_transcription_client
    )
    
    # For article generation - use direct API call instead of ytsum
    import subprocess
except ImportError as e:
    print(f"Error importing helper modules in InstagramCog: {e}", file=sys.stderr)
    # Dummy implementations
    def get_video_processing_client():
        class Dummy:
            async def reformat(self, *args, **kwargs):
                raise Exception("Video processing service not available")
        return Dummy()
    def get_transcription_client():
        class Dummy:
            async def transcribe(self, *args, **kwargs):
                raise Exception("Transcription service not available")
        return Dummy()

# Define constants (consider moving to config or .env)
# MAKE_WEBHOOK_URL is needed for !igmake
MAKE_WEBHOOK_URL = os.getenv('MAKE_WEBHOOK_URL')
# TWEET_WEBHOOK_URL is needed for !ig's article posting
TWEET_WEBHOOK_URL = "https://n8n.jeffrey-epstein.com/webhook/fde498fe-a99c-4e73-8440-4b42baae09b1"

class InstagramCog(commands.Cog):
    """Cog for handling Instagram related commands."""
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        # Access executor via self.bot.executor

    # --- Helper for sending messages (Consider moving to a shared utility cog later) ---
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

     # --- Helper Function (Moved from discord_bot.py) ---
     # This helper seems specific to !igmake
    def _process_ig_video(self, url, title, progress_callback=None):
        """Helper to call the main video processing for !igmake."""
        format_type = 'reel' # Specific format for this command
        transcribe = 'n'
        start = None
        end = None
        logo_type = ''
        # Assuming process_video is imported correctly from app4
        result = process_video(url, format_type, transcribe, title, start, end, logo_type, progress_callback)
        print(f"DEBUG: process_video returned: {result}") # Add this line
        return result # Return the result

    # --- Helper for sending webhook (similar to twitter cog) ---
    # TODO: Consolidate webhook sending logic into a shared utility/cog
    async def _send_webhook_request(self, ctx, title, body, image_url, blog_type="calf", row_number=None):
        """Internal helper to send webhook requests for articles."""
        data = {
            "blog": blog_type,
            "Title": str(title),
            "Body": str(body),
            "image": str(image_url),
            "user_id": str(ctx.author.id)
        }
        if row_number is not None: data["row"] = row_number
        try:
            loop = asyncio.get_event_loop()
            response = await loop.run_in_executor(
                self.bot.executor,
                lambda: requests.post(TWEET_WEBHOOK_URL, json=data, timeout=60)
            )
            response.raise_for_status()
            print(f"Successfully sent webhook request ({blog_type})")
            return True
        except requests.exceptions.Timeout:
            print(f"Failed to send webhook request ({blog_type}): Timeout")
            return False
        except requests.exceptions.RequestException as e:
            print(f"Failed to send webhook request ({blog_type}): {str(e)}")
            if hasattr(e, 'response') and e.response is not None: print(f"Error response: {e.response.text}")
            return False
        except Exception as e:
             print(f"Unexpected error in _send_webhook_request ({blog_type}): {e}")
             traceback.print_exc(file=sys.stderr)
             return False

    # --- Instagram Commands ---

    @commands.command(
        name='igmake',
        help='Process and upload video to doc/calf/HT IG.',
        description='use on social links to re-up to calf/hot/doc IGs via MAKE.',
        usage='!igmake <video_url> <account_type ->doc/calf/hot> <caption>',
        brief='!igmake <video_url> <account_type doc/calf/hot> <caption> , no commas'
    )
    async def igmake_command(self, ctx, url, account_type, *, caption):
        if not isinstance(ctx.channel, discord.DMChannel):
            await self._send_message_with_rate_limit(ctx, "This command can only be used in DMs.") # Use helper
            return
        if account_type.lower() not in ['doc', 'calf', 'hot']:
            await self._send_message_with_rate_limit(ctx, "Invalid account type. Please use 'doc', 'calf' or 'hot'.") # Use helper
            return
            
        await self._send_message_with_rate_limit(ctx, "Processing your video for Instagram. This may take a while...") # Use helper
        title = f"ig_video_{ctx.author.id}_{ctx.message.id}"
        
        # Use a simple lambda for the callback within the command scope
        async def progress_callback(message):
             # Use the bot's method for sending, handles rate limits if defined there
             # Or use a shared helper if available
             await self._send_message_with_rate_limit(ctx, message) # Use helper

        loop = asyncio.get_event_loop()
        try:
            # Call the internal helper method using self.bot.executor
            result = await loop.run_in_executor(
                self.bot.executor, 
                lambda: self._process_ig_video(url, title, progress_callback=lambda m: asyncio.run_coroutine_threadsafe(progress_callback(m), loop).result())
            )
            r2_url, update_status, _ = result # Unpack the 3-element tuple
            if r2_url:
                await self._send_message_with_rate_limit(ctx, "Video processed successfully! Sending to webhook for further processing...") # Use helper
                if not MAKE_WEBHOOK_URL:
                    await self._send_message_with_rate_limit(ctx, "Error: MAKE_WEBHOOK_URL is not set.") # Use helper
                    return
                payload = {
                    "r2_url": r2_url, "account_type": account_type,
                    "caption": caption, "user_id": str(ctx.author.id)
                }
                try:
                    # Run blocking request in executor
                    response = await loop.run_in_executor(
                        self.bot.executor,
                        lambda: requests.post(MAKE_WEBHOOK_URL, json=payload, timeout=30)
                    )
                    response.raise_for_status()
                    await self._send_message_with_rate_limit(ctx, "Successfully sent to webhook for further processing.") # Use helper
                    await self._send_message_with_rate_limit(ctx, f"Webhook response: {response.text}") # Use helper
                except requests.exceptions.Timeout:
                     await self._send_message_with_rate_limit(ctx, "Error: Request to MAKE webhook timed out.") # Use helper
                except requests.RequestException as e:
                    await self._send_message_with_rate_limit(ctx, f"Failed to send to webhook. Error: {str(e)}") # Use helper
                    if hasattr(e, 'response'): await self._send_message_with_rate_limit(ctx, f"Response: {e.response.text}") # Use helper
                if update_status != 'y':
                    await self._send_message_with_rate_limit(ctx, f"Note: {update_status}") # Use helper
            else:
                await self._send_message_with_rate_limit(ctx, f"Failed to process video. Error: {update_status}") # Use helper
        except Exception as e:
            await self._send_message_with_rate_limit(ctx, f"An error occurred during video processing: {str(e)}") # Use helper
            print(f"Error details (!igmake): {e}", file=sys.stderr)
            traceback.print_exc(file=sys.stderr)

    # --- !ig command using user-provided logic ---
    @commands.command(
        name='ig',
        help='Download Instagram video, metadata, and optionally fetch comments.',
        description='Downloads video from Instagram URL, returns video link, and optionally fetches comments using Apify.',
        usage='!ig <instagram_url> -title: Your custom title -add: Additional context (optional) -r (optional)',
        brief='!ig <instagram_url> -title: Your custom title -add: Additional context (optional) -r (optional)'
    )
    async def ig_command(self, ctx): # Use self as it's in a Cog
        if not isinstance(ctx.channel, discord.DMChannel):
            await self._send_message_with_rate_limit(ctx, "This command can only be used in DMs.") # Use helper
            return

        # Get the full message content and remove the command prefix
        content = ctx.message.content
        if content.startswith('!ig '):
            content = content[4:].strip()
        else:
            await self._send_message_with_rate_limit(ctx, "Please provide an Instagram URL.") # Use helper
            return

        # Parse the content to extract URL, title, additional context, and -r flag
        url_match = re.match(r"^(https?://(?:www\.)?instagram\.com/[^\s]+)", content) # More specific regex for IG
        url = url_match.group(1).strip() if url_match else None

        custom_title = None
        additional_context = None
        fetch_comments_flag = False # Initialize fetch_comments flag

        # Extract parameters using regex
        title_match = re.search(r"-title:\s*(.*?)(?=\s*-add:|\s*-r|$)", content, re.IGNORECASE)
        add_match = re.search(r"-add:\s*(.*?)(?=\s*-title:|\s*-r|$)", content, re.IGNORECASE)
        r_match = re.search(r"\s-r\b", content, re.IGNORECASE) # Check for standalone -r flag

        if title_match:
            custom_title = title_match.group(1).strip()
            print(f"Extracted title: '{custom_title}'")
            sys.stdout.flush()

        if add_match:
            additional_context = add_match.group(1).strip()
            print(f"Extracted additional context: '{additional_context}'")
            sys.stdout.flush()

        if r_match:
            fetch_comments_flag = True
            print("Fetch comments flag (-r) detected.")
            sys.stdout.flush()

        if not url:
            await self._send_message_with_rate_limit(ctx, "Please provide a valid Instagram URL (e.g., https://www.instagram.com/p/...).") # Use helper
            return

        await self._send_message_with_rate_limit(ctx, "Processing Instagram post...") # Use helper
        print(f"Processing Instagram post with URL: {url}")
        if custom_title:
            print(f"Custom title: '{custom_title}'")
        sys.stdout.flush()

        loop = asyncio.get_event_loop()
        video_r2_url = None
        thumbnail_r2_url = None
        comments_r2_url = None
        transcript_r2_url = None # Added for transcript
        article_r2_url = None # Added for article
        embed_code = None # Added for Raptive embed code
        temp_video_path = None
        temp_thumbnail_path = None
        plain_transcript = None # To store transcript text
        article = None # To store generated article
        webhook_success = False # Initialize webhook success flag
        ig_description = None # Initialize description
        formatted_comments = None # Initialize comments
        comments_error = None # Initialize comments error

        # Define the forced simple filename for video
        forced_video_filename = "downloaded_ig_video.mp4"
        # Define the expected simple filename for the converted thumbnail
        forced_thumbnail_filename = "downloaded_ig_video.jpg" 

        # Ensure any previous attempts are cleaned up
        if os.path.exists(forced_video_filename):
            try: os.remove(forced_video_filename)
            except OSError: pass 
        if os.path.exists(forced_thumbnail_filename):
            try: os.remove(forced_thumbnail_filename)
            except OSError: pass 

        try:
            # --- Step 1: Download Media using simple yt-dlp call to CWD ---
            await self._send_message_with_rate_limit(ctx, "Attempting video and thumbnail download...") 
            
            cmd_video = [
                'yt-dlp',
                '--verbose', # Get maximum debug info
                '-S', "proto,ext:mp4:m4a,res,br", # Format selection
                '--write-thumbnail', # Re-enable thumbnail download
                '--convert-thumbnails', 'jpg', # Re-enable thumbnail conversion
                # '-f', 'bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best', # Alt format
                '-o', forced_video_filename, # Force simple output name in CWD
                # Remove --print options, we check for fixed filenames
                url
            ]
            print(f"Running simplified yt-dlp command: {' '.join(cmd_video)}")

            # Run the blocking subprocess call in the executor
            process = await loop.run_in_executor(
                self.bot.executor,
                lambda: subprocess.run(cmd_video, capture_output=True, text=True, check=False, encoding='utf-8') 
            )

            # Print verbose output regardless of exit code for debugging
            print(f"yt-dlp stdout:\n{process.stdout}") # Stdout might contain description if not using --print
            print(f"yt-dlp stderr:\n{process.stderr}")
            sys.stdout.flush()
            sys.stderr.flush()

            download_error = None
            if process.returncode != 0:
                download_error = f"yt-dlp failed with exit code {process.returncode}."
                await self._send_message_with_rate_limit(ctx, f"❌ Error during download: {download_error}")
                return 
            
            # Check if the forced video filename exists
            if os.path.exists(forced_video_filename):
                temp_video_path = forced_video_filename # Use the known filename
                print(f"Video file confirmed: {temp_video_path}")
                await self._send_message_with_rate_limit(ctx, "✅ Basic video download successful.")
            else:
                 error_msg = f"❌ yt-dlp finished successfully (exit code 0), but the forced output file '{forced_video_filename}' was not found in CWD ({os.getcwd()})."
                 await self._send_message_with_rate_limit(ctx, error_msg)
                 print(error_msg, file=sys.stderr)
                 # List files in CWD for debugging
                 try:
                     cwd_files = os.listdir('.')
                     print(f"Files in CWD: {cwd_files}")
                 except Exception as list_err:
                     print(f"Could not list CWD files: {list_err}")
                 return # Stop processing

            # Check if the forced thumbnail filename exists
            if os.path.exists(forced_thumbnail_filename):
                temp_thumbnail_path = forced_thumbnail_filename
                print(f"Thumbnail file confirmed: {temp_thumbnail_path}")
                await self._send_message_with_rate_limit(ctx, "✅ Thumbnail download successful.")
            else:
                 print(f"Warning: Could not find thumbnail file: {forced_thumbnail_filename}")
                 await self._send_message_with_rate_limit(ctx, "ℹ️ Thumbnail download/conversion failed or file not found.")

            # --- Step 1b: Get Description Separately ---
            ig_description = None
            try:
                cmd_desc = ['yt-dlp', '--get-description', url]
                print(f"Running yt-dlp command for description: {' '.join(cmd_desc)}")
                desc_process = await loop.run_in_executor(
                    self.bot.executor,
                    lambda: subprocess.run(cmd_desc, capture_output=True, text=True, check=False, encoding='utf-8')
                )
                if desc_process.returncode == 0:
                    ig_description = desc_process.stdout.strip()
                    if ig_description: print("Description captured separately.")
                    else: print("Warning: --get-description yielded empty output.")
                else:
                    print(f"Warning: yt-dlp --get-description failed (Code: {desc_process.returncode}): {desc_process.stderr}", file=sys.stderr)
            except Exception as desc_err:
                 print(f"Warning: Error getting description separately: {desc_err}", file=sys.stderr)


            # --- Step 2: Upload Video to R2 --- 
            await self._send_message_with_rate_limit(ctx, "Uploading video to R2...") # Use helper
            try:
                # Determine video extension from the returned path
                _, video_ext = os.path.splitext(temp_video_path)
                video_ext = video_ext.lstrip('.') # Remove leading dot
                # Use the base name from the path reported by yt-dlp for R2 filename
                video_filename_base_for_r2 = os.path.splitext(os.path.basename(temp_video_path))[0] 
                
                with open(temp_video_path, "rb") as video_file:
                    video_r2_url = await loop.run_in_executor(
                        self.bot.executor, # Use self.bot.executor
                        lambda: upload_to_r2(video_file, video_filename_base_for_r2, file_extension=video_ext) # Use actual extension
                    )
                if video_r2_url:
                    await self._send_message_with_rate_limit(ctx, "✅ Video uploaded to R2.") # Use helper
                else:
                    await self._send_message_with_rate_limit(ctx, "❌ Failed to upload video to R2.") # Use helper
            except Exception as upload_error:
                await self._send_message_with_rate_limit(ctx, f"❌ Error uploading video to R2: {str(upload_error)}") # Use helper
                print(f"Video R2 upload error details: {upload_error}", file=sys.stderr)
                traceback.print_exc(file=sys.stderr)

            # --- Step 3: Upload Thumbnail to R2 --- (Re-enabled)
            if temp_thumbnail_path and os.path.exists(temp_thumbnail_path):
                await self._send_message_with_rate_limit(ctx, "Uploading thumbnail to R2...") # Use helper
                try:
                    _, thumb_ext = os.path.splitext(temp_thumbnail_path); thumb_ext = thumb_ext.lstrip('.')
                    # Use base name from video path for consistency if possible
                    thumbnail_filename_base_for_r2 = os.path.splitext(os.path.basename(temp_video_path))[0] if temp_video_path else f"ig_thumb_{uuid.uuid4()}"
                    
                    with open(temp_thumbnail_path, "rb") as thumb_file:
                        thumbnail_r2_url = await loop.run_in_executor(
                            self.bot.executor, upload_to_r2, thumb_file, thumbnail_filename_base_for_r2, thumb_ext # Use self.bot.executor
                        )
                    if thumbnail_r2_url: await self._send_message_with_rate_limit(ctx, "✅ Thumbnail uploaded to R2.") # Use helper
                    else: await self._send_message_with_rate_limit(ctx, "❌ Failed to upload thumbnail to R2.") # Use helper
                except Exception as thumb_upload_error: await self._send_message_with_rate_limit(ctx, f"❌ Error uploading thumbnail: {thumb_upload_error}") # Use helper
            # else: await self._send_message_with_rate_limit(ctx, "ℹ️ No thumbnail found or downloaded to upload.") # Already reported above

            # --- Step 4: Transcribe Video --- 
            is_valid_transcript = False
            if temp_video_path and os.path.exists(temp_video_path):
                await self._send_message_with_rate_limit(ctx, "Transcribing video...") # Use helper
                # Use the helper from ig_processing
                plain_transcript, transcribe_error = await loop.run_in_executor(
                     self.bot.executor, ig_processing.transcribe_ig_video, temp_video_path # Use self.bot.executor
                )
                is_valid_transcript = not transcribe_error and plain_transcript and plain_transcript not in ["Transcription resulted in empty text.", "Import Error"] # Check for errors and empty string
                if transcribe_error: await self._send_message_with_rate_limit(ctx, f"❌ Transcription Error: {transcribe_error}") # Use helper
                elif not is_valid_transcript: await self._send_message_with_rate_limit(ctx, "⚠️ Warning: Transcription returned empty text.") # Use helper
                else:
                    await self._send_message_with_rate_limit(ctx, "✅ Transcription successful.") # Use helper
                    # Upload transcript
                    await self._send_message_with_rate_limit(ctx, "Uploading transcript to R2...") # Use helper
                    try:
                        transcript_filename = f"ig_transcript_{uuid.uuid4()}"
                        transcript_r2_url = await loop.run_in_executor(
                            self.bot.executor, upload_to_r2, io.BytesIO(plain_transcript.encode('utf-8')), transcript_filename, 'txt' # Use self.bot.executor
                        )
                        if transcript_r2_url: await self._send_message_with_rate_limit(ctx, "✅ Transcript uploaded to R2.") # Use helper
                        else: await self._send_message_with_rate_limit(ctx, "❌ Failed to upload transcript.") # Use helper
                    except Exception as trans_upload_error: await self._send_message_with_rate_limit(ctx, f"❌ Error uploading transcript: {trans_upload_error}") # Use helper
            else:
                 await self._send_message_with_rate_limit(ctx, "❌ Skipping transcription: Video file missing.") # Use helper
                 plain_transcript = "Transcription failed due to missing video file."

            # --- Step 5: Upload to Raptive --- 
            input_url_for_script = video_r2_url if video_r2_url else temp_video_path
            if not input_url_for_script: await self._send_message_with_rate_limit(ctx, "❌ Skipping Raptive upload: No valid video source.") # Use helper
            else:
                await self._send_message_with_rate_limit(ctx, "Uploading video to Calf Raptive...") # Use helper
                if not custom_title: await self._send_message_with_rate_limit(ctx, "⚠️ Skipping Raptive upload: Custom title (-title:) is required.") # Use helper
                else:
                    try:
                        script_path = os.path.join(os.path.dirname(os.path.abspath(__file__)).replace("cogs",""), "calfupload.py")
                        process = await asyncio.create_subprocess_exec(
                            sys.executable, script_path, input_url_for_script, "-title", custom_title,
                            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
                        )
                        all_output_lines = []
                        async def capture_raptive_output():
                             proc_stdout, proc_stderr = await process.communicate()
                             if proc_stdout: all_output_lines.extend(proc_stdout.decode().strip().splitlines())
                             if proc_stderr: print(f"[calfupload.py stderr]\n{proc_stderr.decode().strip()}", file=sys.stderr)
                        await capture_raptive_output()
                        exit_code = process.returncode
                        if exit_code == 0:
                            for line in reversed(all_output_lines):
                                if line.startswith('[adthrive-in-post-video-player'): embed_code = line; break
                            if embed_code: await self._send_message_with_rate_limit(ctx, "✅ Video successfully uploaded to Raptive.") # Use helper
                            else: await self._send_message_with_rate_limit(ctx, "⚠️ Raptive upload finished but couldn't extract embed code.") # Use helper
                        else: await self._send_message_with_rate_limit(ctx, f"❌ Error uploading to Raptive (exit code {exit_code}).") # Use helper
                    except Exception as e: await self._send_message_with_rate_limit(ctx, f"⚠️ Error during Raptive upload process: {str(e)}") # Use helper

            # --- Step 6: Cleanup Local Files --- 
            # Cleanup the forced filename if it exists
            if temp_video_path and os.path.exists(temp_video_path): # temp_video_path now holds forced_output_filename if successful
                try: 
                    os.remove(temp_video_path)
                    print(f"Cleaned up video: {temp_video_path}")
                except OSError as e: print(f"Warning: Could not remove {temp_video_path}: {e}")
            # Clean up thumbnail if it exists
            if temp_thumbnail_path and os.path.exists(temp_thumbnail_path):
                 try: 
                     os.remove(temp_thumbnail_path)
                     print(f"Cleaned up thumbnail: {temp_thumbnail_path}")
                 except OSError as e: print(f"Warning: Could not remove {temp_thumbnail_path}: {e}")

            # --- Step 7: Fetch Comments --- 
            if fetch_comments_flag:
                await self._send_message_with_rate_limit(ctx, "Fetching comments via Apify...") # Use helper
                apify_token = os.getenv("APIFY_API_TOKEN") # Consider making default None
                if not apify_token or apify_token == "YOUR_APIFY_API_TOKEN_HERE":
                    await self._send_message_with_rate_limit(ctx, "❌ Error: Apify API token not configured.") # Use helper
                    formatted_comments, comments_error = None, "Apify token not configured"
                else:
                    formatted_comments, comments_error = await ig_processing.fetch_and_format_ig_comments(url, apify_token)
                    if comments_error: await self._send_message_with_rate_limit(ctx, f"❌ Error fetching/formatting comments: {comments_error}")
                    elif formatted_comments:
                        await self._send_message_with_rate_limit(ctx, "✅ Successfully fetched and formatted comments.") # Use helper
                        # Upload Comments
                        await self._send_message_with_rate_limit(ctx, "Uploading comments to R2...") # Use helper
                        try:
                            comments_filename = f"ig_comments_{uuid.uuid4()}"
                            comments_r2_url = await loop.run_in_executor(
                                self.bot.executor, upload_to_r2, io.BytesIO(formatted_comments.encode('utf-8')), comments_filename, 'txt' # Use self.bot.executor
                            )
                            if comments_r2_url: await self._send_message_with_rate_limit(ctx, "✅ Comments uploaded to R2.") # Use helper
                            else: await self._send_message_with_rate_limit(ctx, "❌ Failed to upload comments.") # Use helper
                        except Exception as comments_upload_error: await self._send_message_with_rate_limit(ctx, f"❌ Error uploading comments: {comments_upload_error}") # Use helper
                    else: await self._send_message_with_rate_limit(ctx, "⚠️ No comments found by Apify.") # Use helper
            else: await self._send_message_with_rate_limit(ctx, "ℹ️ Skipping comment fetching (no -r flag).") # Use helper
            
            # --- Step 8: Generate Article --- 
            if custom_title and is_valid_transcript and embed_code:
                await self._send_message_with_rate_limit(ctx, f"Generating CALFKICKER article...") # Use helper
                try:
                    prompt, prompt_error = ig_processing.format_ig_claude_prompt(
                        custom_title, plain_transcript, ig_description, additional_context,
                        formatted_comments if fetch_comments_flag else None
                    )
                    if prompt_error: await self._send_message_with_rate_limit(ctx, f"❌ Error formatting prompt: {prompt_error}") # Use helper
                    else:
                        article_content = await loop.run_in_executor(self.bot.executor, call_claude_api, prompt, "claude-3-7-sonnet-latest") # Use self.bot.executor
                        if not article_content or len(article_content.strip()) == 0:
                            await self._send_message_with_rate_limit(ctx, "❌ Error: Claude API returned empty article.") # Use helper
                            article = None 
                        else:
                            await self._send_message_with_rate_limit(ctx, "✅ Article content generated.") # Use helper
                            article = f"{article_content}\n\n{embed_code}"
                            # Upload article
                            await self._send_message_with_rate_limit(ctx, "Uploading article to R2...") # Use helper
                            try:
                                article_filename = f"ig_article_{uuid.uuid4()}"
                                article_r2_url = await loop.run_in_executor(
                                    self.bot.executor, upload_to_r2, io.BytesIO(article.encode('utf-8')), article_filename, 'txt' # Use self.bot.executor
                                )
                                if article_r2_url: await self._send_message_with_rate_limit(ctx, "✅ Article uploaded to R2.") # Use helper
                                else: await self._send_message_with_rate_limit(ctx, "❌ Failed to upload article.") # Use helper
                            except Exception as article_upload_error: await self._send_message_with_rate_limit(ctx, f"❌ Error uploading article: {article_upload_error}") # Use helper
                except Exception as article_gen_error:
                    await self._send_message_with_rate_limit(ctx, f"❌ Error in article generation: {article_gen_error}") # Use helper
                    article = None 
            else:
                reason = []
                if not custom_title: reason.append("missing title")
                if not is_valid_transcript: reason.append("invalid transcript")
                if not embed_code: reason.append("missing embed code")
                await self._send_message_with_rate_limit(ctx, f"ℹ️ Skipping article generation ({', '.join(reason)}).") # Use helper
                 
            # --- Step 9: Send Webhook --- (Re-add thumbnail requirement)
            if article and embed_code and custom_title and thumbnail_r2_url: 
                await self._send_message_with_rate_limit(ctx, "Sending webhook request...") # Use helper
                webhook_success = await self._send_webhook_request(ctx, custom_title, article, thumbnail_r2_url, blog_type="calf") # Use internal helper
                if webhook_success: await self._send_message_with_rate_limit(ctx, "✅ Webhook request sent successfully!") # Use helper
                else: await self._send_message_with_rate_limit(ctx, "❌ Failed to send webhook request.") # Use helper
            elif article: await self._send_message_with_rate_limit(ctx, "ℹ️ Skipping webhook (missing embed/title/thumbnail).") # Use helper

            # --- Step 10: Send Final Response --- 
            response_parts = [f"**Instagram Post Processed:** {url}\n"]
            if custom_title: response_parts.append(f"**Custom Title:** {custom_title}\n")
            if ig_description: response_parts.append(f"**IG Description:**\n```\n{ig_description}\n```\n")
            response_parts.append(f"**Video:** {video_r2_url or 'Upload Failed'}\n")
            if thumbnail_r2_url: response_parts.append(f"**Thumbnail:** {thumbnail_r2_url}\n") # Re-enabled
            if transcript_r2_url: response_parts.append(f"**Transcription:** {transcript_r2_url}\n")
            elif plain_transcript and is_valid_transcript: response_parts.append(f"**Transcription Status:** Upload Failed\n") 
            elif plain_transcript: response_parts.append(f"**Transcription Status:** {plain_transcript[:100]}...\n") # Show error/status
            if comments_r2_url: response_parts.append(f"**Comments:** {comments_r2_url}\n")
            elif fetch_comments_flag: response_parts.append(f"**Comments:** Fetch/Upload Failed (-r specified: {comments_error or 'Unknown reason'})\n")
            if article_r2_url: response_parts.append(f"**Article:** {article_r2_url}\n")
            if embed_code: response_parts.append(f"**Embed Code:**\n```{embed_code}```\n")
            
            response_parts.append("\n**--- Status Summary ---**\n")
            if article and webhook_success: response_parts.append("✅ Complete.")
            elif article: response_parts.append("⚠️ Article generated but webhook failed/skipped.")
            elif embed_code: response_parts.append("⚠️ Embed generated but article failed/skipped.")
            elif video_r2_url: response_parts.append("⚠️ Video uploaded but embed/article failed/skipped.")
            else: response_parts.append("❌ Failed during download or initial uploads.")
            
            full_response = "".join(response_parts)
            chunks = [full_response[i:i+1900] for i in range(0, len(full_response), 1900)]
            for i, chunk in enumerate(chunks):
                 prefix = f"**(Part {i+1}/{len(chunks)})**\n" if len(chunks) > 1 else ""
                 await self._send_message_with_rate_limit(ctx, prefix + chunk) # Use helper
                 if i < len(chunks) - 1: await asyncio.sleep(1)

        except Exception as e:
            await self._send_message_with_rate_limit(ctx, f"An unexpected error occurred during !ig processing: {str(e)}") # Use helper
            print(f"IG command error: {e}", file=sys.stderr)
            traceback.print_exc(file=sys.stderr)
            # Cleanup on error
            if temp_video_path and os.path.exists(temp_video_path):
                try: os.remove(temp_video_path)
                except OSError as rm_err: print(f"Warning: Could not remove {temp_video_path} on error: {rm_err}")
            # Clean up thumbnail if it exists
            if temp_thumbnail_path and os.path.exists(temp_thumbnail_path):
                 try: 
                     os.remove(temp_thumbnail_path)
                     print(f"Cleaned up thumbnail: {temp_thumbnail_path}")
                 except OSError as rm_err: print(f"Warning: Could not remove {temp_thumbnail_path} on error: {rm_err}")

# The mandatory setup function called by discord.py when loading the Cog
async def setup(bot: commands.Bot):
    await bot.add_cog(InstagramCog(bot))
    print("InstagramCog loaded.") # Optional confirmation message
