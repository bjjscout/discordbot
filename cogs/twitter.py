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
import tweetsheet # Import the tweetsheet module

# Import necessary functions from other modules
# NOTE: Using service clients - no app4/torch in bot!
try:
    import tweetsheet  # Lightweight
    from tweet_processing import fetch_tweet_data, transcribe_video, fetch_and_format_replies, format_claude_prompt, get_tweet_id_from_url  # Lightweight
    
    # Use service clients for video upload
    from utils.service_clients import get_video_processing_client
except ImportError as e:
    print(f"Error importing helper modules in TwitterCog: {e}", file=sys.stderr)
    # Dummy implementations
    async def fetch_tweet_data(*args, **kwargs): return {"error": "Import failed"}
    async def transcribe_video(*args, **kwargs): return "Transcription failed", None, "Error"
    async def fetch_and_format_replies(*args, **kwargs): return None, "Import failed"
    def format_claude_prompt(*args, **kwargs): return "Prompt failed"
    def get_tweet_id_from_url(*args, **kwargs): return None
    def get_video_processing_client():
        class Dummy:
            async def reformat(self, *args, **kwargs):
                raise Exception("Video processing service not available")
        return Dummy()

# Define constants (consider moving to config or .env)
TWEET_WEBHOOK_URL = "https://n8n.jeffrey-epstein.com/webhook/fde498fe-a99c-4e73-8440-4b42baae09b1"
PULLTWEETS_WEBHOOK_URL = 'https://n8n.jeffrey-epstein.com/webhook/1d1dbaa7-79af-4343-8dbb-d9f0a68b51bc'
CLEARTWEETS_WEBHOOK_URL = 'https://hook.us1.make.com/ikecdi2ugoqilxud9r22o6s65hfiqbhn'
REPLIES_API_KEY = "59d7c6cbec0a4c8dba8c5c7e1540e230" # Strongly recommend moving to .env

class TwitterCog(commands.Cog):
    """Cog for handling Twitter/X related commands."""
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        # Access executor via self.bot.executor

    # --- Helper Methods for Webhooks ---
    async def _send_webhook_request(self, ctx, title, body, image_url, blog_type="calf", row_number=None):
        """Internal helper to send webhook requests for tweet articles."""
        data = {
            "blog": blog_type, # "calf" or "doc"
            "Title": str(title),
            "Body": str(body),
            "image": str(image_url),
            "user_id": str(ctx.author.id)
        }
        if row_number is not None:
            data["row"] = row_number
        try:
            loop = asyncio.get_event_loop()
            # Use self.bot.executor
            response = await loop.run_in_executor(
                self.bot.executor,
                lambda: requests.post(TWEET_WEBHOOK_URL, json=data, timeout=60) # Add timeout
            )
            response.raise_for_status()
            print(f"Successfully sent webhook request ({blog_type})")
            print(f"Response status code: {response.status_code}")
            print(f"Response body: {response.text}")
            return True
        except requests.exceptions.Timeout:
            print(f"Failed to send webhook request ({blog_type}): Timeout")
            return False
        except requests.exceptions.RequestException as e:
            print(f"Failed to send webhook request ({blog_type}): {str(e)}")
            print(f"Request data that failed: {data}")
            if hasattr(e, 'response') and e.response is not None:
                print(f"Error response status code: {e.response.status_code}")
                print(f"Error response body: {e.response.text}")
            return False
        except Exception as e:
             print(f"Unexpected error in _send_webhook_request ({blog_type}): {e}")
             traceback.print_exc(file=sys.stderr)
             return False

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

    # --- AI API Call Functions (Copied from aiwriter.py) ---
    def call_openai_api(prompt_text, model_name="gpt-4.1"):
        try:
            api_key = os.getenv("OPENAI_API_KEY")
            if not api_key:
                print("Error: OPENAI_API_KEY not found in environment variables. Cannot call OpenAI.")
                return None
            
            client = openai.OpenAI(api_key=api_key)
            
            response = client.chat.completions.create(
                model=model_name,
                messages=[
                    {"role": "user", "content": prompt_text}
                ]
            )
            return response.choices[0].message.content
        except Exception as e:
            print(f"Error calling OpenAI API (model: {model_name}): {str(e)}")
            print(traceback.format_exc())
            return None

    def call_gemini_api(prompt_text, model_name="gemini-2.5-pro-exp-03-25"):
        try:
            api_key = os.getenv("GEMINI_API_KEY")
            if not api_key:
                print("Error: GEMINI_API_KEY not found in environment variables. Cannot call Gemini.")
                return None
            
            genai.configure(api_key=api_key)
            
            model = genai.GenerativeModel(model_name)
            response = model.generate_content(prompt_text)
            
            if not response.candidates:
                print(f"Gemini API call (model: {model_name}) returned no candidates. This might be due to safety filters or an issue with the prompt.")
                if hasattr(response, 'prompt_feedback') and response.prompt_feedback:
                    print(f"Prompt Feedback: {response.prompt_feedback}")
                return None

            if response.candidates[0].content and response.candidates[0].content.parts:
                return "".join(part.text for part in response.candidates[0].content.parts if hasattr(part, 'text'))
            else:
                print(f"Gemini API response (model: {model_name}) did not contain expected text parts.")
                return None

        except Exception as e:
            print(f"Error calling Gemini API (model: {model_name}): {str(e)}")
            print(traceback.format_exc())
            return None

    def call_deepseek_api(prompt_text, model_name="deepseek-reasoner"):
        try:
            api_key = os.getenv("DEEPSEEK_API_KEY")
            if not api_key:
                print("Error: DEEPSEEK_API_KEY not found in environment variables. Cannot call DeepSeek.")
                return None
            
            client = openai.OpenAI(
                api_key=api_key,
                base_url="https://api.deepseek.com" # DeepSeek API endpoint
            )
            
            response = client.chat.completions.create(
                model=model_name,
                messages=[
                    {"role": "user", "content": prompt_text}
                ]
            )
            return response.choices[0].message.content
        except Exception as e:
            print(f"Error calling DeepSeek API (model: {model_name}): {str(e)}")
            print(traceback.format_exc())
            return None

    # --- Twitter Commands ---
    @commands.command(
        name='tweet',
        help='Download Twitter/X video, metadata, transcription, and optionally generate an article for calfkicker. Use -gpt for OpenAI, -gemini for Gemini, or -ds for DeepSeek.',
        description='Downloads video from Twitter/X URL, returns video links, title, thumbnail, transcription, and optionally generates an article using the specified AI provider (default: Claude).',
        usage='!tweet <twitter_url> [-gpt | -gemini | -ds] -title: Your custom title -add: Additional context (optional) -r (optional)',
        brief='!tweet <twitter_url> [-gpt | -gemini | -ds] -title: Your custom title -add: Additional context (optional) -r (optional)'
    )
    async def tweet_command(self, ctx, *, args_string: str): # Use * to capture all arguments as a single string
        if not isinstance(ctx.channel, discord.DMChannel):
            await ctx.send("This command can only be used in DMs.")
            return
        
        # Parse arguments from the string
        args = args_string.split()
        url = None
        custom_title, additional_context = None, None
        fetch_replies = False
        ai_provider = "claude" # Default provider
        ai_provider_message = "Claude (claude-3-7-sonnet-latest, default)"

        # Find the URL (assumed to be the first argument not starting with '-')
        for arg in args:
            if not arg.startswith('-'):
                url = arg
                break

        # Parse options and their values
        args_string_lower = args_string.lower()
        
        # Check for AI provider flags first
        if "-gpt" in args_string_lower:
            ai_provider = "openai"
            ai_provider_message = "OpenAI (gpt-4.1)"
        elif "-gemini" in args_string_lower:
            ai_provider = "gemini"
            ai_provider_message = "Gemini (gemini-2.5-pro-exp-03-25)"
        elif "-ds" in args_string_lower:
            ai_provider = "deepseek"
            ai_provider_message = "DeepSeek (deepseek-reasoner)"

        # Extract -title and -add using regex on the original args_string
        title_match = re.search(r"-title:\s*(.*?)(?=\s*-add:|\s*-r|\s*-gpt|\s*-gemini|\s*-ds|$)", args_string, re.IGNORECASE)
        add_match = re.search(r"-add:\s*(.*?)(?=\s*-title:|\s*-r|\s*-gpt|\s*-gemini|\s*-ds|$)", args_string, re.IGNORECASE)
        r_match = re.search(r"\s-r\b", args_string_lower)

        if title_match: custom_title = title_match.group(1).strip()
        if add_match: additional_context = add_match.group(1).strip()
        if r_match: fetch_replies = True
        
        if not url: await ctx.send("Please provide a Twitter/X URL."); return
        
        await ctx.send(f"Processing Twitter/X video for calfkicker using {ai_provider_message}...")
        print(f"Processing tweet: {url}, Title: '{custom_title}', Replies: {fetch_replies}, AI Provider: {ai_provider}")
        sys.stdout.flush()

        article, embed_code = None, None
        transcript_r2_url, replies_r2_url, article_r2_url = None, None, None
        loop = asyncio.get_event_loop()
        try:
            # 1. Fetch Tweet Info (Use executor)
            await ctx.send("Fetching tweet information...")
            api_key = os.getenv('RAPIDAPI_KEY')
            if not api_key: await ctx.send("Error: RAPIDAPI_KEY not set."); return
            # Use self.bot.executor
            tweet_data_results = await loop.run_in_executor(self.bot.executor, fetch_tweet_data, url, api_key)
            if tweet_data_results["error"]: await ctx.send(f"Error fetching tweet data: {tweet_data_results['error']}"); return
            highest_quality_url = tweet_data_results["highest_quality_url"]
            tweet_title = tweet_data_results["tweet_title"]
            thumbnail_url = tweet_data_results["thumbnail_url"]
            if not highest_quality_url: await ctx.send("Error: Could not extract video URL."); return
            await ctx.send("Tweet information fetched.")
            
            # 2. Transcribe Video (Use executor)
            await ctx.send("Downloading and transcribing the video...")
            # Use self.bot.executor
            plain_transcript, srt_transcript, transcribe_error = await loop.run_in_executor(self.bot.executor, transcribe_video, highest_quality_url)
            is_valid_transcript = not transcribe_error and plain_transcript and plain_transcript != "No transcript available."
            if transcribe_error: await ctx.send(f"Transcription Error: {transcribe_error}")
            elif not is_valid_transcript: await ctx.send("Warning: Transcription returned empty text.")
            else: await ctx.send("Transcription successful.")
            
            # 3. Upload Transcription (Use executor)
            if is_valid_transcript:
                try:
                    transcript_filename = f"tweet_transcript_{uuid.uuid4()}"
                    # Use self.bot.executor
                    transcript_r2_url = await loop.run_in_executor(
                        self.bot.executor, upload_to_r2, io.BytesIO(plain_transcript.encode('utf-8')), transcript_filename, 'txt'
                    )
                    if transcript_r2_url: await ctx.send("Transcription uploaded to R2.")
                    else: await ctx.send("Failed to upload transcription to R2.")
                except Exception as upload_error: await ctx.send(f"Error uploading transcription: {upload_error}")
            else: await ctx.send("No valid transcription to upload.")
                
            # 4. Fetch Replies (Use executor)
            tweet_replies = None
            if fetch_replies:
                await ctx.send("Fetching tweet replies...")
                tweet_id = get_tweet_id_from_url(url)
                if tweet_id:
                    # Use self.bot.executor
                    formatted_replies, replies_error = await loop.run_in_executor(self.bot.executor, fetch_and_format_replies, tweet_id, REPLIES_API_KEY)
                    if replies_error: await ctx.send(f"❌ Error fetching/formatting replies: {replies_error}")
                    elif formatted_replies:
                        tweet_replies = formatted_replies
                        await ctx.send("✅ Successfully fetched and formatted tweet replies.")
                        # Upload Replies (Use executor)
                        await ctx.send("Uploading replies to R2...")
                        try:
                            replies_filename = f"tweet_replies_{uuid.uuid4()}"
                            # Use self.bot.executor
                            replies_r2_url = await loop.run_in_executor(
                                 self.bot.executor, upload_to_r2, io.BytesIO(tweet_replies.encode('utf-8')), replies_filename, 'txt'
                            )
                            if replies_r2_url: await ctx.send("✅ Tweet replies uploaded to R2.")
                            else: await ctx.send("❌ Failed to upload tweet replies to R2.")
                        except Exception as replies_upload_error: await ctx.send(f"❌ Error uploading replies: {replies_upload_error}")
                    else: await ctx.send("⚠️ Could not fetch or format tweet replies.")
                else: await ctx.send("⚠️ Could not extract Tweet ID for replies.")
                
            # 5. Upload to Raptive (Subprocess - Keep as is)
            await ctx.send("Uploading video to Calf Raptive...")
            try:
                raptive_title = custom_title if custom_title else tweet_title
                script_path = os.path.join(os.path.dirname(os.path.abspath(__file__)).replace("cogs", ""), "calfupload.py") # Adjust path relative to main bot file
                process = await asyncio.create_subprocess_exec(
                    sys.executable, script_path, highest_quality_url, "-title", raptive_title,
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
                    if embed_code: await ctx.send("✅ Video successfully uploaded to Raptive.")
                    else: await ctx.send("⚠️ Raptive upload finished but couldn't extract embed code.")
                else: await ctx.send(f"❌ Error uploading to Raptive (exit code {exit_code}).")
            except Exception as e: await ctx.send(f"⚠️ Error in Raptive upload process: {str(e)}")
            
            # 6. Generate Article (Use executor for Claude call)
            if custom_title and is_valid_transcript and embed_code:
                await ctx.send(f"Generating CALFKICKER article...")
                try:
                    # Generate AI response based on provider
                    ai_response = None
                    
                    # This prompt is used for all providers
                    ai_prompt = format_claude_prompt(custom_title, plain_transcript, tweet_title, additional_context, tweet_replies)

                    try:
                        if ai_provider == "openai":
                            await self._send_message_with_rate_limit(ctx, f"Calling OpenAI API using model gpt-4.1...")
                            ai_response = await loop.run_in_executor(self.bot.executor, self.call_openai_api, ai_prompt, "gpt-4.1")
                        elif ai_provider == "gemini":
                            await self._send_message_with_rate_limit(ctx, f"Calling Gemini API using model gemini-2.5-pro-exp-03-25...")
                            ai_response = await loop.run_in_executor(self.bot.executor, self.call_gemini_api, ai_prompt, "gemini-2.5-pro-exp-03-25")
                        elif ai_provider == "deepseek":
                            await self._send_message_with_rate_limit(ctx, f"Calling DeepSeek API using model deepseek-reasoner...")
                            ai_response = await loop.run_in_executor(self.bot.executor, self.call_deepseek_api, ai_prompt, "deepseek-reasoner")
                        elif ai_provider == "claude":
                            await self._send_message_with_rate_limit(ctx, f"Calling Claude API using model claude-3-7-sonnet-latest...")
                            claude_response = await loop.run_in_executor(self.bot.executor, call_claude_api, ai_prompt, "claude-3-7-sonnet-latest")
                            # call_claude_api might return dict or string, ensure we get the text
                            if isinstance(claude_response, dict):
                                ai_response = claude_response.get('text', '')
                            elif isinstance(claude_response, str):
                                ai_response = claude_response
                            else:
                                await self._send_message_with_rate_limit(ctx, f"Error: Unexpected Claude API response format: {type(claude_response)}")
                                raise Exception("Bad Claude API response format") # Raise to be caught by outer try/except
                        
                        if not ai_response or len(ai_response.strip()) == 0:
                            await self._send_message_with_rate_limit(ctx, f"Error: {ai_provider_message} call failed or returned empty content.")
                            raise Exception(f"{ai_provider_message} call failed or returned empty content") # Raise to be caught by outer try/except

                        await self._send_message_with_rate_limit(ctx, f"Article content generated using {ai_provider_message}.")
                        article = f"{ai_response}\n\n{embed_code}" # Use ai_response
                        
                        # Upload Article (Use executor)
                        await self._send_message_with_rate_limit(ctx, "Uploading article to R2...")
                        try:
                            article_filename = f"tweet_article_{uuid.uuid4()}"
                            article_r2_url = await loop.run_in_executor(
                                self.bot.executor, upload_to_r2, io.BytesIO(article.encode('utf-8')), article_filename, 'txt'
                            )
                            if article_r2_url: await self._send_message_with_rate_limit(ctx, "Article uploaded to R2.")
                            else: await self._send_message_with_rate_limit(ctx, "Failed to upload article to R2.")
                        except Exception as article_upload_error: await self._send_message_with_rate_limit(ctx, f"Error uploading article: {article_upload_error}")

                    except Exception as article_gen_error:
                        await self._send_message_with_rate_limit(ctx, f"Error during AI article generation: {article_gen_error}")
                        print(f"Error during AI article generation: {article_gen_error}", file=sys.stderr)
                        traceback.print_exc(file=sys.stderr)
                        # Do not re-raise, allow the rest of the command to finish
                except Exception as article_gen_error: await ctx.send(f"Error in article generation: {article_gen_error}") # Keep original catch for format_claude_prompt errors etc.
            else: await ctx.send("ℹ️ Skipping article generation (missing title, transcript, or embed code).")
            
            # 7. Send Final Response
            response = f"**Tweet Title:** {tweet_title}\n\n**Video URL:** {highest_quality_url}\n\n**Thumbnail:** {thumbnail_url}\n\n"
            if transcript_r2_url: response += f"**Transcription:** {transcript_r2_url}\n\n"
            if replies_r2_url: response += f"**Replies:** {replies_r2_url}\n\n"
            if article_r2_url: response += f"**Article:** {article_r2_url}\n\n"
            if embed_code: response += f"**Embed Code:**\n```{embed_code}```"
            await ctx.send(response.strip())
            
            # 8. Send Webhook (Use internal helper method)
            if article and embed_code and custom_title:
                await ctx.send("Sending webhook request...")
                # Use self._send_webhook_request
                webhook_success = await self._send_webhook_request(ctx, custom_title, article, thumbnail_url, blog_type="calf") 
                if webhook_success: await ctx.send("✅ Webhook request sent successfully!")
                else: await ctx.send("❌ Failed to send webhook request.")
            
        except Exception as e:
            await ctx.send(f"An error occurred: {str(e)}")
            print(f"Tweet command error: {e}", file=sys.stderr)
            traceback.print_exc(file=sys.stderr)

    @commands.command(
        name='tweetdoc',
        help='Download Twitter/X video, metadata, transcription, and optionally generate an article for bjjdoc. Use -gpt for OpenAI, -gemini for Gemini, or -ds for DeepSeek.',
        description='Downloads video from Twitter/X URL, returns video links, title, thumbnail, transcription, and optionally generates an article for bjjdoc using the specified AI provider (default: Claude).',
        usage='!tweetdoc <twitter_url> [-gpt | -gemini | -ds] -title: Your custom title -add: Additional context (optional) -r (optional)',
        brief='!tweetdoc <twitter_url> [-gpt | -gemini | -ds] -title: Your custom title -add: Additional context (optional) -r (optional)'
    )
    async def tweetdoc_command(self, ctx, *, args_string: str): # Use * to capture all arguments as a single string
        if not isinstance(ctx.channel, discord.DMChannel):
            await ctx.send("This command can only be used in DMs.")
            return
        
        # Parse arguments from the string
        args = args_string.split()
        url = None
        custom_title, additional_context = None, None
        fetch_replies = False
        ai_provider = "claude" # Default provider
        ai_provider_message = "Claude (claude-3-7-sonnet-latest, default)"

        # Find the URL (assumed to be the first argument not starting with '-')
        for arg in args:
            if not arg.startswith('-'):
                url = arg
                break

        # Parse options and their values
        args_string_lower = args_string.lower()
        
        # Check for AI provider flags first
        if "-gpt" in args_string_lower:
            ai_provider = "openai"
            ai_provider_message = "OpenAI (gpt-4.1)"
        elif "-gemini" in args_string_lower:
            ai_provider = "gemini"
            ai_provider_message = "Gemini (gemini-2.5-pro-exp-03-25)"
        elif "-ds" in args_string_lower:
            ai_provider = "deepseek"
            ai_provider_message = "DeepSeek (deepseek-reasoner)"

        # Extract -title and -add using regex on the original args_string
        title_match = re.search(r"-title:\s*(.*?)(?=\s*-add:|\s*-r|\s*-gpt|\s*-gemini|\s*-ds|$)", args_string, re.IGNORECASE)
        add_match = re.search(r"-add:\s*(.*?)(?=\s*-title:|\s*-r|\s*-gpt|\s*-gemini|\s*-ds|$)", args_string, re.IGNORECASE)
        r_match = re.search(r"\s-r\b", args_string_lower)

        if title_match: custom_title = title_match.group(1).strip()
        if add_match: additional_context = add_match.group(1).strip()
        if r_match: fetch_replies = True
        
        if not url: await ctx.send("Please provide a Twitter/X URL."); return
        
        await ctx.send(f"Processing Twitter/X video for bjjdoc using {ai_provider_message}...")
        print(f"Processing tweetdoc: {url}, Title: '{custom_title}', Replies: {fetch_replies}, AI Provider: {ai_provider}")
        sys.stdout.flush()

        article, embed_code = None, None
        transcript_r2_url, replies_r2_url, article_r2_url = None, None, None
        loop = asyncio.get_event_loop()
        try:
            # 1. Fetch Tweet Info (Use executor)
            await ctx.send("Fetching tweet information...")
            api_key = os.getenv('RAPIDAPI_KEY')
            if not api_key: await ctx.send("Error: RAPIDAPI_KEY not set."); return
            # Use self.bot.executor
            tweet_data_results = await loop.run_in_executor(self.bot.executor, fetch_tweet_data, url, api_key)
            if tweet_data_results["error"]: await ctx.send(f"Error fetching tweet data: {tweet_data_results['error']}"); return
            highest_quality_url = tweet_data_results["highest_quality_url"]
            tweet_title = tweet_data_results["tweet_title"]
            thumbnail_url = tweet_data_results["thumbnail_url"]
            if not highest_quality_url: await ctx.send("Error: Could not extract video URL."); return
            await ctx.send("Tweet information fetched.")
            
            # 2. Transcribe Video (Use executor)
            await ctx.send("Downloading and transcribing the video...")
            # Use self.bot.executor
            plain_transcript, srt_transcript, transcribe_error = await loop.run_in_executor(self.bot.executor, transcribe_video, highest_quality_url)
            is_valid_transcript = not transcribe_error and plain_transcript and plain_transcript != "No transcript available."
            if transcribe_error: await ctx.send(f"Transcription Error: {transcribe_error}")
            elif not is_valid_transcript: await ctx.send("Warning: Transcription returned empty text.")
            else: await ctx.send("Transcription successful.")
            
            # 3. Upload Transcription (Use executor)
            if is_valid_transcript:
                await ctx.send("Uploading transcription to R2...")
                try:
                    transcript_filename = f"tweet_transcript_{uuid.uuid4()}"
                    # Use self.bot.executor
                    transcript_r2_url = await loop.run_in_executor(
                        self.bot.executor, upload_to_r2, io.BytesIO(plain_transcript.encode('utf-8')), transcript_filename, 'txt'
                    )
                    if transcript_r2_url: await ctx.send("Transcription completed and uploaded to R2.")
                    else: await ctx.send("Failed to upload transcription to R2.")
                except Exception as upload_error: await ctx.send(f"Error uploading transcription: {upload_error}")
            else: await ctx.send("No valid transcription to upload.")

            # 4. Fetch Replies (Use executor)
            tweet_replies = None
            if fetch_replies:
                await ctx.send("Fetching tweet replies...")
                tweet_id = get_tweet_id_from_url(url)
                if tweet_id:
                    # Use self.bot.executor
                    formatted_replies, replies_error = await loop.run_in_executor(self.bot.executor, fetch_and_format_replies, tweet_id, REPLIES_API_KEY)
                    if replies_error: await ctx.send(f"❌ Error fetching/formatting replies: {replies_error}")
                    elif formatted_replies:
                        tweet_replies = formatted_replies
                        await ctx.send("✅ Successfully fetched and formatted tweet replies.")
                        # Upload Replies (Use executor)
                        await ctx.send("Uploading replies to R2...")
                        try:
                            replies_filename = f"tweet_replies_{uuid.uuid4()}"
                            # Use self.bot.executor
                            replies_r2_url = await loop.run_in_executor(
                                 self.bot.executor, upload_to_r2, io.BytesIO(tweet_replies.encode('utf-8')), replies_filename, 'txt'
                            )
                            if replies_r2_url: await ctx.send("✅ Tweet replies uploaded to R2.")
                            else: await ctx.send("❌ Failed to upload tweet replies to R2.")
                        except Exception as replies_upload_error: await ctx.send(f"❌ Error uploading replies: {replies_upload_error}")
                    else: await ctx.send("⚠️ Could not fetch or format tweet replies.")
                else: await ctx.send("⚠️ Could not extract Tweet ID for replies.")
                
            # 5. Upload to Raptive (Subprocess - Keep as is, use docupload.py)
            await ctx.send("Uploading video to BJJDOC Raptive...")
            try:
                raptive_title = custom_title if custom_title else tweet_title
                script_path = os.path.join(os.path.dirname(os.path.abspath(__file__)).replace("cogs", ""), "docupload.py") # Adjust path
                process = await asyncio.create_subprocess_exec(
                    sys.executable, script_path, highest_quality_url, "-title", raptive_title,
                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
                )
                all_output_lines = []
                async def capture_raptive_output():
                     proc_stdout, proc_stderr = await process.communicate() 
                     if proc_stdout: all_output_lines.extend(proc_stdout.decode().strip().splitlines())
                     if proc_stderr: print(f"[docupload.py stderr]\n{proc_stderr.decode().strip()}", file=sys.stderr)
                await capture_raptive_output()
                exit_code = process.returncode
                if exit_code == 0:
                    for line in reversed(all_output_lines):
                        if line.startswith('[adthrive-in-post-video-player'): embed_code = line; break
                    if embed_code: await ctx.send("✅ Video successfully uploaded to Raptive.")
                    else: await ctx.send("⚠️ Raptive upload finished but couldn't extract embed code.")
                else: await ctx.send(f"❌ Error uploading to Raptive (exit code {exit_code}).")
            except Exception as e: await ctx.send(f"⚠️ Error in Raptive upload process: {str(e)}")
            
            # 6. Generate Article (Use executor for Claude call)
            if custom_title and is_valid_transcript and embed_code:
                await ctx.send(f"Generating BJJDOC article...")
                try:
                    # Generate AI response based on provider
                    ai_response = None
                    
                    # This prompt is used for all providers
                    ai_prompt = format_claude_prompt(custom_title, plain_transcript, tweet_title, additional_context, tweet_replies)

                    try:
                        if ai_provider == "openai":
                            await self._send_message_with_rate_limit(ctx, f"Calling OpenAI API using model gpt-4.1...")
                            ai_response = await loop.run_in_executor(self.bot.executor, self.call_openai_api, ai_prompt, "gpt-4.1")
                        elif ai_provider == "gemini":
                            await self._send_message_with_rate_limit(ctx, f"Calling Gemini API using model gemini-2.5-pro-exp-03-25...")
                            ai_response = await loop.run_in_executor(self.bot.executor, self.call_gemini_api, ai_prompt, "gemini-2.5-pro-exp-03-25")
                        elif ai_provider == "deepseek":
                            await self._send_message_with_rate_limit(ctx, f"Calling DeepSeek API using model deepseek-reasoner...")
                            ai_response = await loop.run_in_executor(self.bot.executor, self.call_deepseek_api, ai_prompt, "deepseek-reasoner")
                        elif ai_provider == "claude":
                            await self._send_message_with_rate_limit(ctx, f"Calling Claude API using model claude-3-7-sonnet-latest...")
                            claude_response = await loop.run_in_executor(self.bot.executor, call_claude_api, ai_prompt, "claude-3-7-sonnet-latest")
                            # call_claude_api might return dict or string, ensure we get the text
                            if isinstance(claude_response, dict):
                                ai_response = claude_response.get('text', '')
                            elif isinstance(claude_response, str):
                                ai_response = claude_response
                            else:
                                await self._send_message_with_rate_limit(ctx, f"Error: Unexpected Claude API response format: {type(claude_response)}")
                                raise Exception("Bad Claude API response format") # Raise to be caught by outer try/except
                        
                        if not ai_response or len(ai_response.strip()) == 0:
                            await self._send_message_with_rate_limit(ctx, f"Error: {ai_provider_message} call failed or returned empty content.")
                            raise Exception(f"{ai_provider_message} call failed or returned empty content") # Raise to be caught by outer try/except

                        await self._send_message_with_rate_limit(ctx, f"Article content generated using {ai_provider_message}.")
                        article = f"{ai_response}\n\n{embed_code}" # Use ai_response
                        
                        # Upload Article (Use executor)
                        await self._send_message_with_rate_limit(ctx, "Uploading article to R2...")
                        try:
                            article_filename = f"tweet_article_{uuid.uuid4()}"
                            article_r2_url = await loop.run_in_executor(
                                self.bot.executor, upload_to_r2, io.BytesIO(article.encode('utf-8')), article_filename, 'txt'
                            )
                            if article_r2_url: await self._send_message_with_rate_limit(ctx, "Article uploaded to R2.")
                            else: await self._send_message_with_rate_limit(ctx, "Failed to upload article to R2.")
                        except Exception as article_upload_error: await self._send_message_with_rate_limit(ctx, f"Error uploading article: {article_upload_error}")

                    except Exception as article_gen_error:
                        await self._send_message_with_rate_limit(ctx, f"Error during AI article generation: {article_gen_error}")
                        print(f"Error during AI article generation: {article_gen_error}", file=sys.stderr)
                        traceback.print_exc(file=sys.stderr)
                        # Do not re-raise, allow the rest of the command to finish
                except Exception as article_gen_error: await ctx.send(f"Error in article generation: {article_gen_error}") # Keep original catch for format_claude_prompt errors etc.
            else: await ctx.send("ℹ️ Skipping article generation (missing title, transcript, or embed code).")
            
            # 7. Send Final Response
            response_msg = f"**Tweet Title:** {tweet_title}\n\n**Video URL:** {highest_quality_url}\n\n**Thumbnail:** {thumbnail_url}\n\n"
            if transcript_r2_url: response_msg += f"**Transcription:** {transcript_r2_url}\n\n"
            if replies_r2_url: response_msg += f"**Replies:** {replies_r2_url}\n\n"
            if article_r2_url: response_msg += f"**Article:** {article_r2_url}\n\n"
            if embed_code: response_msg += f"**Embed Code:**\n```{embed_code}```"
            await ctx.send(response_msg.strip())
            
            # 8. Send Webhook (Use internal helper method)
            # Only send webhook if article was successfully generated and uploaded
            if article_r2_url and embed_code and custom_title: # Check article_r2_url instead of article
                await ctx.send("Sending webhook request...")
                # Use self._send_webhook_request with blog_type="doc"
                # Pass the article content (not the R2 URL) to the webhook
                webhook_success = await self._send_webhook_request(ctx, custom_title, article, thumbnail_url, blog_type="doc") # Pass article content
                if webhook_success: await ctx.send("✅ Webhook request sent successfully!")
                else: await ctx.send("❌ Failed to send webhook request.")
            else:
                 await ctx.send("ℹ️ Skipping webhook request (missing article, embed code, or title).")

        except Exception as e:
            await ctx.send(f"An error occurred: {str(e)}")
            print(f"Tweetdoc command error: {e}", file=sys.stderr)
            traceback.print_exc(file=sys.stderr)

    @commands.command(
        name='pulltweets',
        help='pulls last 24h tweets from watched accounts',
        description='via a N8N webhook, uses twitterapi.io extract a tweets, needs no additional arguments',
        brief='!pulltweets'  
    )
    async def pulltweets_webhook_command(self, ctx): # Renamed from send_command
        if not isinstance(ctx.channel, discord.DMChannel):
            await ctx.send("This command can only be used in DMs.")
            return
        payload = {"user_id": str(ctx.author.id)}
        await ctx.send("Triggering N8N webhook to pull tweets...")
        try:
            loop = asyncio.get_event_loop()
            # Use self.bot.executor
            response = await loop.run_in_executor(
                self.bot.executor, lambda: requests.post(PULLTWEETS_WEBHOOK_URL, json=payload, timeout=30)
            )
            response.raise_for_status()
            await ctx.send(f"Webhook triggered successfully. Status: {response.status_code}. Response: `{response.text}`")
        except requests.exceptions.Timeout:
            await ctx.send("Error: Request to N8N webhook timed out.")
        except requests.RequestException as e:
            error_message = f"Failed to send to webhook. Error: {str(e)}"
            if hasattr(e, 'response'): error_message += f"\nStatus: {e.response.status_code}\nContent: {e.response.text}"
            await ctx.send(error_message)
        except Exception as e:
            await ctx.send(f"An unexpected error occurred: {str(e)}")
            print(f"Error details (!pulltweets): {e}", file=sys.stderr)
            traceback.print_exc(file=sys.stderr)

    @commands.command(
        name='cleartweets',
        help='clears scanned top tweet sheet',
        description='via a make. com webhook, needs no additional arguments',
        brief='!cleartweets'  
    )
    async def cleartweets_webhook_command(self, ctx): # Renamed from send_command
        if not isinstance(ctx.channel, discord.DMChannel):
            await ctx.send("This command can only be used in DMs.")
            return
        payload = {"user_id": str(ctx.author.id)}
        await ctx.send("Triggering Make.com webhook to clear tweetsheet...")
        try:
            loop = asyncio.get_event_loop()
            # Use self.bot.executor
            response = await loop.run_in_executor(
                self.bot.executor, lambda: requests.post(CLEARTWEETS_WEBHOOK_URL, json=payload, timeout=30)
            )
            response.raise_for_status()
            await ctx.send(f"Webhook triggered successfully. Status: {response.status_code}. Response: `{response.text}`")
        except requests.exceptions.Timeout:
            await ctx.send("Error: Request to Make.com webhook timed out.")
        except requests.RequestException as e:
            error_message = f"Failed to send to webhook. Error: {str(e)}"
            if hasattr(e, 'response'): error_message += f"\nStatus: {e.response.status_code}\nContent: {e.response.text}"
            await ctx.send(error_message)
        except Exception as e:
            await ctx.send(f"An unexpected error occurred: {str(e)}")
            print(f"Error details (!cleartweets): {e}", file=sys.stderr)
            traceback.print_exc(file=sys.stderr)

    @commands.command(
        name='tweetsheet',
        help='Processes tweets marked in the Google Sheet for article generation (Calf). Use -gpt for OpenAI, -gemini for Gemini, or -ds for DeepSeek.',
        description='Reads the configured Google Sheet, processes rows marked with "y" in the CONVERT column for Calf, and generates articles using the specified AI provider (default: Claude).',
        usage='!tweetsheet [-gpt | -gemini | -ds]',
        brief='!tweetsheet [-gpt | -gemini | -ds]'
    )
    async def tweetsheet_command(self, ctx, option: str = None): # Added option parameter
        if not isinstance(ctx.channel, discord.DMChannel):
            await ctx.send("This command can only be used in DMs.")
            return
        
        ai_provider = "claude" # Default provider
        ai_provider_message = "Claude (claude-3-7-sonnet-latest, default)"

        if option and option.lower() == "-gpt":
            ai_provider = "openai"
            ai_provider_message = "OpenAI (gpt-4.1)"
        elif option and option.lower() == "-gemini":
            ai_provider = "gemini"
            ai_provider_message = "Gemini (gemini-2.5-pro-exp-03-25)"
        elif option and option.lower() == "-ds":
            ai_provider = "deepseek"
            ai_provider_message = "DeepSeek (deepseek-reasoner)"
        elif option is not None: # Handle invalid options
             await ctx.send(f"⚠️ Unknown option: `{option}`. Using default provider: {ai_provider_message}.")
             # Continue with default provider

        await ctx.send(f"🚀 Starting Google Sheet processing for tweets (Calf) using {ai_provider_message}...")
        
        # Define the async progress callback function to send messages back to the user
        async def progress_callback(message):
            # Use the internal rate-limited sender function
            await self._send_message_with_rate_limit(ctx, message)

        loop = asyncio.get_event_loop()
        try:
            # Run the main tweetsheet processing function in the executor
            await loop.run_in_executor(
                self.bot.executor, # Use shared executor
                lambda: asyncio.run_coroutine_threadsafe(
                    # Pass the selected ai_provider to tweetsheet.main
                    tweetsheet.main(ai_provider=ai_provider, progress_callback=progress_callback, user_id=str(ctx.author.id)), # Pass provider, callback and user ID
                    loop # Specify the loop for threadsafe execution
                ).result() # Wait for the coroutine to complete in the other thread
            )
            await ctx.send("✅ Google Sheet processing finished.")
        except Exception as e:
            await ctx.send(f"❌ An error occurred during sheet processing: {str(e)}")
            print(f"Error during !tweetsheet execution: {e}", file=sys.stderr)
            traceback.print_exc(file=sys.stderr)

# The mandatory setup function called by discord.py when loading the Cog
async def setup(bot: commands.Bot):
    await bot.add_cog(TwitterCog(bot))
    print("TwitterCog loaded.") # Optional confirmation message