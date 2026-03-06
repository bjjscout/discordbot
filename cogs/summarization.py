import discord
from discord.ext import commands
import asyncio
import io
import os
import sys
import requests # Needed for !groq webhook and !audio download
import uuid # Needed for !audio filenames
import traceback # Needed for !audio error logging
from concurrent.futures import ThreadPoolExecutor # Needed for type hint if used
import uuid # Needed for !audio filenames
import traceback # Needed for !audio error logging
# requests and io are already imported

# Import necessary functions
# NOTE: Using service clients for transcription - no torch/whisper in bot!
import re  # For get_video_id

try:
    # Use service clients for transcription
    from utils.service_clients import get_transcription_client
except ImportError as e:
    print(f"Error importing service clients in SummarizationCog: {e}", file=sys.stderr)
    def get_transcription_client():
        class Dummy:
            async def transcribe(self, *args, **kwargs):
                raise Exception("Transcription service not available")
        return Dummy()


def get_video_id(url: str) -> str:
    """Extract video ID from YouTube URL (lightweight)"""
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
        oembed_url = f"https://www.youtube.com/oembed?url=https://youtube.com/watch?v={video_id}&format=json"
        response = requests.get(oembed_url, timeout=10)
        if response.ok:
            data = response.json()
            return data.get('title', 'Unknown Video')
    except:
        pass
    return f"Video {video_id}"

# REMOVED: send_messages_in_batches helper function. Chunking handled in commands.

class SummarizationCog(commands.Cog):
    """Cog for handling YouTube video summarization commands."""
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        # Access the executor via self.bot.executor if needed by commands

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

    @commands.command(
        name='sumw',
        help='Summarize a YouTube video using Whisper transcription (no api)',
        description='Summarize a YouTube video given its URL, using Whisper for transcription',
        usage='!sumw <youtube_url>', # Simplified usage
        brief='!sumw <youtube_url>'
    )
    async def summarize_video_whisper_command(self, ctx, youtube_url: str):
        if not isinstance(ctx.channel, discord.DMChannel):
            await ctx.send("This command can only be used in DMs.")
            return

        await ctx.send("Processing your YouTube video using Whisper. This may take a few minutes...")

        def progress_callback(message):
            # Use self.bot.loop for threadsafe execution from executor
            asyncio.run_coroutine_threadsafe(ctx.send(message), self.bot.loop)

        loop = asyncio.get_event_loop()
        try:
            # Use self.bot.executor
            summary, srt_transcript, plain_transcript, transcript_source, video_id = await loop.run_in_executor(
                self.bot.executor,
                lambda: summarize_youtube_video_whisper(youtube_url, progress_callback=progress_callback)
            )

            # Send the summary using simple chunking
            if summary:
                chunks = [summary[i:i+1900] for i in range(0, len(summary), 1900)]
                for chunk in chunks:
                    await ctx.send(chunk)
                    await asyncio.sleep(1) # Add small delay between chunks
            else:
                await ctx.send("Sorry, the summary generation failed. Please try again.")

            # Upload transcripts to R2
            # Ensure video_id was obtained
            if not video_id:
                 video_id = get_video_id(youtube_url) or f"whisper_{uuid.uuid4()}" # Fallback name

            if srt_transcript:
                # Run R2 upload in executor as it might involve I/O
                srt_r2_url = await loop.run_in_executor(
                    self.bot.executor,
                    lambda: upload_to_r2(io.BytesIO(srt_transcript.encode()), f"{video_id}_transcript", "srt")
                )
                if srt_r2_url: await ctx.send(f"SRT Transcript: {srt_r2_url}")
                else: await ctx.send("Failed to upload SRT transcript.")

            if plain_transcript:
                # Run R2 upload in executor
                plain_r2_url = await loop.run_in_executor(
                    self.bot.executor,
                    lambda: upload_to_r2(io.BytesIO(plain_transcript.encode()), f"{video_id}_transcript", "txt")
                )
                if plain_r2_url: await ctx.send(f"Plain Transcript: {plain_r2_url}")
                else: await ctx.send("Failed to upload plain transcript.")

        except Exception as e:
            await ctx.send(f"An error occurred while summarizing the video: {str(e)}")
            print(f"Error details (!sumw): {e}", file=sys.stderr)
            traceback.print_exc(file=sys.stderr) # Add traceback

    @commands.command(
        name='sum',
        help='Summarize a YouTube video using OpenAI GPT-4o-mini',
        description='Summarize a YouTube video given its URL using Claude 3 Haiku',
        usage='!sum <youtube_url>', # Simplified usage
        brief='!sum <youtube_url>'
    )
    async def summarize_video_command(self, ctx, youtube_url: str):
        if not isinstance(ctx.channel, discord.DMChannel):
            await ctx.send("This command can only be used in DMs.")
            return

        await ctx.send("Processing your YouTube video using OpenAI GPT-4o-mini. This may take a few minutes...")

        def progress_callback(message):
            asyncio.run_coroutine_threadsafe(ctx.send(message), self.bot.loop)

        loop = asyncio.get_event_loop()
        try:
            # Use self.bot.executor with OpenAI function
            summary, srt_transcript, plain_transcript, transcript_source, video_id = await loop.run_in_executor(
                self.bot.executor,
                lambda: summarize_youtube_video_openai(youtube_url, model="gpt-4o-mini", progress_callback=progress_callback)
            )
            # Ensure video_id was obtained
            if not video_id:
                 video_id = get_video_id(youtube_url) or f"haiku_{uuid.uuid4()}" # Fallback name

            # Upload transcripts to R2
            txt_r2_url = None
            srt_r2_url = None

            if video_id:
                await ctx.send("Uploading transcripts to R2...")
                try:
                    # Run R2 uploads in executor
                    txt_file = io.BytesIO(plain_transcript.encode('utf-8'))
                    srt_file = io.BytesIO(srt_transcript.encode('utf-8'))
                    
                    # Run uploads concurrently? Or sequentially is fine? Sequentially is simpler.
                    txt_r2_url = await loop.run_in_executor(
                        self.bot.executor,
                        lambda: upload_to_r2(txt_file, f"{video_id}_transcript", file_extension='txt')
                    )
                    srt_r2_url = await loop.run_in_executor(
                        self.bot.executor,
                        lambda: upload_to_r2(srt_file, f"{video_id}_transcript", file_extension='srt')
                    )
                except Exception as e:
                    await ctx.send(f"Error uploading to R2: {str(e)}")

            # Send summary using simple chunking
            if summary:
                chunks = [summary[i:i+1900] for i in range(0, len(summary), 1900)]
                for chunk in chunks:
                    await ctx.send(chunk)
                    await asyncio.sleep(1) # Add small delay between chunks
            else:
                 await ctx.send("Sorry, the summary generation failed.")

            if txt_r2_url and srt_r2_url:
                await ctx.send(f"Transcript URLs:\nPlain text: {txt_r2_url}\nSRT: {srt_r2_url}")
            elif video_id: # Only mention failure if upload was attempted
                await ctx.send("Failed to upload transcripts to R2.")

        except Exception as e:
            await ctx.send(f"An error occurred while summarizing the video: {str(e)}")
            print(f"Error details (!sum): {e}", file=sys.stderr)
            traceback.print_exc(file=sys.stderr) # Add traceback

    @commands.command(
        name='sum2',
        help='Summarize a YouTube video using Claude 4 Sonnet',
        description='Summarize a YouTube video given its URL using Claude 4 Sonnet',
        usage='!sum2 <youtube_url>', # Simplified usage
        brief='!sum2 <youtube_url>'
    )
    async def summarize_video_command_sonnet(self, ctx, youtube_url: str):
        if not isinstance(ctx.channel, discord.DMChannel):
            await ctx.send("This command can only be used in DMs.")
            return

        await ctx.send("Processing your YouTube video using Claude 4 Sonnet. This may take a few minutes...")

        def progress_callback(message):
            asyncio.run_coroutine_threadsafe(ctx.send(message), self.bot.loop)

        loop = asyncio.get_event_loop()
        try:
            # Use self.bot.executor
            summary, srt_transcript, plain_transcript, transcript_source, video_id = await loop.run_in_executor(
                self.bot.executor,
                lambda: summarize_youtube_video(youtube_url, model="claude-sonnet-4-5", progress_callback=progress_callback) # Corrected model name
            )
             # Ensure video_id was obtained
            if not video_id:
                 video_id = get_video_id(youtube_url) or f"sonnet_{uuid.uuid4()}" # Fallback name

            # Upload transcripts to R2
            txt_r2_url = None
            srt_r2_url = None

            if video_id:
                await ctx.send("Uploading transcripts to R2...")
                try:
                    # Run R2 uploads in executor
                    txt_file = io.BytesIO(plain_transcript.encode('utf-8'))
                    srt_file = io.BytesIO(srt_transcript.encode('utf-8'))

                    txt_r2_url = await loop.run_in_executor(
                        self.bot.executor,
                        lambda: upload_to_r2(txt_file, f"{video_id}_transcript", file_extension='txt')
                    )
                    srt_r2_url = await loop.run_in_executor(
                        self.bot.executor,
                        lambda: upload_to_r2(srt_file, f"{video_id}_transcript", file_extension='srt')
                    )
                except Exception as e:
                    await ctx.send(f"Error uploading to R2: {str(e)}")

            # Send summary using simple chunking
            if summary:
                chunks = [summary[i:i+1900] for i in range(0, len(summary), 1900)]
                for chunk in chunks:
                    await ctx.send(chunk)
                    await asyncio.sleep(1) # Add small delay between chunks
            else:
                 await ctx.send("Sorry, the summary generation failed.")


            if txt_r2_url and srt_r2_url:
                await ctx.send(f"Transcript URLs:\nPlain text: {txt_r2_url}\nSRT: {srt_r2_url}")
            elif video_id:
                await ctx.send("Failed to upload transcripts to R2.")

        except Exception as e:
            await ctx.send(f"An error occurred while summarizing the video: {str(e)}")
            print(f"Error details (!sum2): {e}", file=sys.stderr)
            traceback.print_exc(file=sys.stderr) # Add traceback

    @commands.command(
        name='groq',
        help='Summarize a YT video using GROQ',
        description='via a N8N webhook, pulls a API YT transcript and summarizes via llama-3.1-70b-versatile',
        brief='!groq <YouTube Link>'
    )
    async def groq_command(self, ctx, *, youtube_link: str): # Renamed from send_command
        # Check if command is used in DMs
        if not isinstance(ctx.channel, discord.DMChannel):
            await ctx.send("This command can only be used in DMs.")
            return

        # TODO: Consider making webhook URL configurable via .env
        webhook_url = 'https://n8n.jeffrey-epstein.com/webhook/71e77374-1eb0-4ff1-b623-3446838fbd1e'
        payload = {
            "user_id": str(ctx.author.id),
            "YT URL": youtube_link  # Add the YouTube link to the payload
        }

        await ctx.send("Sending request to GROQ summarization webhook. Wait...") # Give feedback first

        try:
            # Run the blocking request in the executor
            loop = asyncio.get_event_loop()
            response = await loop.run_in_executor(
                self.bot.executor,
                lambda: requests.post(webhook_url, json=payload, timeout=120) # Add timeout
            )
            response.raise_for_status() # Raise an exception for bad status codes (4xx or 5xx)
            # N8N likely sends back an acceptance message, not the summary itself.
            # The summary might come back via a separate webhook call TO the bot,
            # or the user just has to wait for N8N to finish.
            # For now, just confirm the request was sent.
            await ctx.send(f"Webhook request sent successfully. Status: {response.status_code}. Response: `{response.text}`")

        except requests.exceptions.Timeout:
             await ctx.send("Error: Request to GROQ webhook timed out.")
        except requests.exceptions.RequestException as e:
            error_message = f"Failed to send to webhook. Error: {str(e)}"
            if hasattr(e, 'response') and e.response is not None:
                error_message += f"\nResponse status code: {e.response.status_code}\nResponse content: {e.response.text}"
            await ctx.send(error_message)
            print(f"Error details (!groq): {e}", file=sys.stderr) # Log error
        except Exception as e:
             await ctx.send(f"An unexpected error occurred: {str(e)}")
             print(f"Error details (!groq): {e}", file=sys.stderr)
             traceback.print_exc(file=sys.stderr) # Add traceback


    @commands.command(
        name='audio',
        help='Transcribes an audio file and sends a prompt with the transcript to Claude',
        description='Transcribes + send to claude w/prompt => returns response+srt+txt',
        usage='!audio [prompt] <url>',
        brief='!audio [prompt] <url>'
    )
    async def audio_command(self, ctx, *, args):
        if not isinstance(ctx.channel, discord.DMChannel):
            await ctx.send("This command can only be used in DMs.")
            return
        try:
            prompt1, url = args.rsplit(' ', 1)
        except ValueError:
            await ctx.send("Invalid format. Use: `!audio Your prompt text here http://audio-url.com`")
            return

        progress_message = await ctx.send("Processing your request. This may take a while...")
        loop = asyncio.get_event_loop()
        try:
            # Download the file asynchronously using executor
            response = await loop.run_in_executor(self.bot.executor, lambda: requests.get(url, timeout=60))
            response.raise_for_status()
            
            # Add check for empty content
            if not response.content:
                await progress_message.edit(content=f"Error: Downloaded audio from {url} is empty.")
                logger.error(f"Downloaded audio content is empty for URL: {url}")
                return # Stop processing if content is empty

            audio_data = io.BytesIO(response.content)
            await progress_message.edit(content="Audio downloaded. Transcribing...")

            # Transcribe the audio asynchronously using executor
            plain_transcript, srt_transcript = await loop.run_in_executor(
                self.bot.executor, transcribe_with_whisper, audio_data
            )
            await progress_message.edit(content="Transcription complete. Uploading transcripts...")

            # Generate unique base filename
            base_filename = f"{prompt1.replace(' ', '_')[:50]}_{uuid.uuid4()}" # Truncate prompt part

            # Upload transcripts to R2 asynchronously using executor
            plain_r2_url = await loop.run_in_executor(
                self.bot.executor, upload_to_r2, io.BytesIO(plain_transcript.encode('utf-8')), f"{base_filename}_transcript", "txt"
            )
            srt_r2_url = await loop.run_in_executor(
                self.bot.executor, upload_to_r2, io.BytesIO(srt_transcript.encode('utf-8')), f"{base_filename}_transcript", "srt"
            )
            await progress_message.edit(content="Transcripts uploaded. Generating article...")

            # Generate the prompt for Claude
            prompt = f"""{prompt1} The following is a transcript of an audio file to help you with your task.

Transcript:
{plain_transcript}
"""
            # Call Claude API asynchronously using executor
            article = await loop.run_in_executor(self.bot.executor, call_claude_api, prompt)
            await progress_message.edit(content="Article generated. Uploading...")

            # Save the article to a file and upload to R2 asynchronously using executor
            article_file = io.BytesIO(article.encode('utf-8'))
            article_r2_url = await loop.run_in_executor(
                self.bot.executor, upload_to_r2, article_file, f"{base_filename}_article", "txt"
            )

            # Send the final results
            await progress_message.edit(content="Processing complete! Here are the results:")
            await ctx.send(f"Claude response: {article_r2_url}")
            await ctx.send(f"Plain text transcript: {plain_r2_url}")
            await ctx.send(f"SRT transcript: {srt_r2_url}")

        except requests.exceptions.RequestException as e:
             await progress_message.edit(content=f"Error downloading audio: {str(e)}")
        except Exception as e:
            await progress_message.edit(content=f"An error occurred while processing your request: {str(e)}")
            print(f"Error details (!audio): {e}", file=sys.stderr)
            traceback.print_exc(file=sys.stderr)

# The mandatory setup function called by discord.py when loading the Cog
async def setup(bot: commands.Bot):
    # Add the Cog instance to the bot
    await bot.add_cog(SummarizationCog(bot))
    print("SummarizationCog loaded.") # Optional confirmation message