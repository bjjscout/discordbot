import discord
from discord.ext import commands
import asyncio
import os
import sys
import requests
import traceback
from concurrent.futures import ThreadPoolExecutor # For type hint

# Define webhook URLs (Consider moving to .env or config)
SEND_SHEET_WEBHOOK_URL = 'https://n8n.jeffrey-epstein.com/webhook/84bc4496-104f-4c28-bfc8-94d9fd693bd8'
PROCESS_SHEET2_WEBHOOK_URL = 'https://n8n.jeffrey-epstein.com/webhook-test/d5f3ae63-ca09-420a-9a83-2ef5febb517c'

class WebhooksCog(commands.Cog):
    """Cog for handling simple webhook trigger commands."""
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        # Access executor via self.bot.executor

    @commands.command(
        name='send_sheet',
        help='Triggers uploading to Doc or Calf socials',
        description='This command triggers uploading from Google Sheet via N8N webhook.',
        brief='!send_sheet'
    )
    async def send_sheet_webhook_command(self, ctx):
        if not isinstance(ctx.channel, discord.DMChannel):
            await ctx.send("This command can only be used in DMs.")
            return
        
        payload = {"user_id": str(ctx.author.id)}
        await ctx.send("Triggering N8N webhook for sheet upload...")
        try:
            loop = asyncio.get_event_loop()
            # Use GET as per original, run in executor
            response = await loop.run_in_executor(
                self.bot.executor, lambda: requests.get(SEND_SHEET_WEBHOOK_URL, json=payload, timeout=30)
            )
            response.raise_for_status()
            await ctx.send(f"Webhook triggered successfully. Status: {response.status_code}. Response: `{response.text}`")
        except requests.exceptions.Timeout:
            await ctx.send("Error: Request to N8N webhook timed out.")
        except requests.RequestException as e:
            await ctx.send(f"Failed to send to webhook. Error: {str(e)}")
            if hasattr(e, 'response') and e.response is not None:
                await ctx.send(f"Response status code: {e.response.status_code}")
                await ctx.send(f"Response content: {e.response.text}")
        except Exception as e:
            await ctx.send(f"An unexpected error occurred: {str(e)}")
            print(f"Error details (!send_sheet): {e}", file=sys.stderr)
            traceback.print_exc(file=sys.stderr)

    @commands.command(
        name='process_sheet2',
        help='Triggers uploading to social media channels via n8n',
        description='This command triggers n8n to upload from a specific Google Sheet.',
        brief='Triggers N8N sheet processing'
    )
    async def process_sheet2_webhook_command(self, ctx):
        if not isinstance(ctx.channel, discord.DMChannel):
            await ctx.send("This command can only be used in DMs.")
            return
        
        payload = {"user_id": str(ctx.author.id)}
        await ctx.send("Triggering N8N webhook for sheet processing (v2)...")
        try:
            loop = asyncio.get_event_loop()
            # Use GET as per original, run in executor
            response = await loop.run_in_executor(
                self.bot.executor, lambda: requests.get(PROCESS_SHEET2_WEBHOOK_URL, json=payload, timeout=30)
            )
            response.raise_for_status()
            await ctx.send(f"Webhook triggered successfully. Status: {response.status_code}. Response: `{response.text}`")
        except requests.exceptions.Timeout:
            await ctx.send("Error: Request to N8N webhook timed out.")
        except requests.RequestException as e:
            await ctx.send(f"Failed to send to webhook. Error: {str(e)}")
            if hasattr(e, 'response') and e.response is not None:
                await ctx.send(f"Response status code: {e.response.status_code}")
                await ctx.send(f"Response content: {e.response.text}")
        except Exception as e:
            await ctx.send(f"An unexpected error occurred: {str(e)}")
            print(f"Error details (!process_sheet2): {e}", file=sys.stderr)
            traceback.print_exc(file=sys.stderr)

    @commands.command(
        name='podclip',
        help='Creates viral clips from a YT URL',
        description='via a N8N webhook, will return split clips and transcripts to a google sheet',
        brief='!podclip <YouTube Link>'
    )
    async def podclip_command(self, ctx, *, youtube_link: str):
        # Check if command is used in DMs
        if not isinstance(ctx.channel, discord.DMChannel):
            await ctx.send("This command can only be used in DMs.")
            return

        # TODO: Consider making webhook URL configurable via .env
        webhook_url = 'https://podclip.jeffrey-epstein.com/process_video'
        payload = {
            "user_id": str(ctx.author.id),
            "video_url": youtube_link  # Changed from "YT URL" to "video_url"
        }
        headers = {
            "Authorization": "Bearer 914214",
            "Content-Type": "application/json"
        }

        await ctx.send("Sending request to podclip.jeffrey-epstein.com. Wait...") # Give feedback first

        try:
            # Run the blocking request in the executor
            loop = asyncio.get_event_loop()
            response = await loop.run_in_executor(
                self.bot.executor,
                lambda: requests.post(webhook_url, json=payload, headers=headers, timeout=120) # Add headers
            )
            response.raise_for_status() # Raise an exception for bad status codes (4xx or 5xx)
            # N8N likely sends back an acceptance message, not the summary itself.
            # The summary might come back via a separate webhook call TO the bot,
            # or the user just has to wait for N8N to finish.
            # For now, just confirm the request was sent.
            await ctx.send(f"Webhook request sent successfully. Status: {response.status_code}. Response: `{response.text}`")

        except requests.exceptions.Timeout:
             await ctx.send("Error: Request to Podclip webhook timed out.")
        except requests.exceptions.RequestException as e:
            error_message = f"Failed to send to webhook. Error: {str(e)}"
            if hasattr(e, 'response') and e.response is not None:
                error_message += f"\nResponse status code: {e.response.status_code}\nResponse content: {e.response.text}"
            await ctx.send(error_message)
            print(f"Error details (!podclip): {e}", file=sys.stderr) # Log error
        except Exception as e:
             await ctx.send(f"An unexpected error occurred: {str(e)}")
             print(f"Error details (!podclip): {e}", file=sys.stderr)
             traceback.print_exc(file=sys.stderr) # Add traceback

    @commands.command(
        name='whisperx',
        help='fast transcription via whisperx',
        description='via a N8N webhook, will transcribe/translate with WhisperX on Slave',
        brief='!whisperx <YouTube Link>'
    )
    async def whisperx_command(self, ctx, *, youtube_link: str):
        # Check if command is used in DMs
        if not isinstance(ctx.channel, discord.DMChannel):
            await ctx.send("This command can only be used in DMs.")
            return

        # TODO: Consider making webhook URL configurable via .env
        webhook_url = 'https://n8n.jeffrey-epstein.com/webhook/whisperx'
        payload = {
            "user_id": str(ctx.author.id),
            "video_url": youtube_link  # Changed from "YT URL" to "video_url"
        }
        headers = {
            "Content-Type": "application/json"
        }

        await ctx.send("Sending request to Slave. Wait...") # Give feedback first

        try:
            # Run the blocking request in the executor
            loop = asyncio.get_event_loop()
            response = await loop.run_in_executor(
                self.bot.executor,
                lambda: requests.post(webhook_url, json=payload, headers=headers, timeout=120) # Add headers
            )
            response.raise_for_status() # Raise an exception for bad status codes (4xx or 5xx)
            # N8N likely sends back an acceptance message, not the summary itself.
            # The summary might come back via a separate webhook call TO the bot,
            # or the user just has to wait for N8N to finish.
            # For now, just confirm the request was sent.
            await ctx.send(f"Webhook request sent successfully. Status: {response.status_code}. Response: `{response.text}`")

        except requests.exceptions.Timeout:
             await ctx.send("Error: Request to Whisperx timed out.")
        except requests.exceptions.RequestException as e:
            error_message = f"Failed to send to webhook. Error: {str(e)}"
            if hasattr(e, 'response') and e.response is not None:
                error_message += f"\nResponse status code: {e.response.status_code}\nResponse content: {e.response.text}"
            await ctx.send(error_message)
            print(f"Error details (!whisperx): {e}", file=sys.stderr) # Log error
        except Exception as e:
             await ctx.send(f"An unexpected error occurred: {str(e)}")
             print(f"Error details (!whisperx): {e}", file=sys.stderr)
             traceback.print_exc(file=sys.stderr) # Add traceback

    @commands.command(
        name='rmbg',
        help='Removes background with RMBG 2.0 from an image URL',
        description='via a N8N webhook, will run rmbg 2.0 and return a segmented main subject',
        brief='!rmbg <Image Link>'
    )
    async def rmbg_command(self, ctx, *, rmbg_link: str): 
        # Check if command is used in DMs
        if not isinstance(ctx.channel, discord.DMChannel):
            await ctx.send("This command can only be used in DMs.")
            return

        # TODO: Consider making webhook URL configurable via .env
        webhook_url = 'https://n8n.jeffrey-epstein.com/webhook/ce574ed7-99fb-4317-b8f0-f054a0d06d1b'
        payload = {
            "user_id": str(ctx.author.id),
            "YT URL": rmbg_link  # Add the YouTube link to the payload
        }

        await ctx.send("Sending request to RMBG N8N webhook. Wait...") # Give feedback first

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
             await ctx.send("Error: Request to RMBG webhook timed out.")
        except requests.exceptions.RequestException as e:
            error_message = f"Failed to send to webhook. Error: {str(e)}"
            if hasattr(e, 'response') and e.response is not None:
                error_message += f"\nResponse status code: {e.response.status_code}\nResponse content: {e.response.text}"
            await ctx.send(error_message)
            print(f"Error details (!rmbg): {e}", file=sys.stderr) # Log error
        except Exception as e:
             await ctx.send(f"An unexpected error occurred: {str(e)}")
             print(f"Error details (!rmbg): {e}", file=sys.stderr)
             traceback.print_exc(file=sys.stderr) # Add traceback

    @commands.command(
        name='blurbg',
        help='!blurbg <Image Link>',
        description='via a N8N webhook, will run rmbg 2.0 and blur the background',
        brief='!blurbg <Image Link>'
    )
    async def blurbg_command(self, ctx, *, blurbg_link: str): 
        # Check if command is used in DMs
        if not isinstance(ctx.channel, discord.DMChannel):
            await ctx.send("This command can only be used in DMs.")
            return

        # TODO: Consider making webhook URL configurable via .env
        webhook_url = 'https://n8n.jeffrey-epstein.com/webhook/d44c92d6-9e95-473f-a54c-ed0ad054b57b'
        payload = {
            "user_id": str(ctx.author.id),
            "URL": blurbg_link  # Add the YouTube link to the payload
        }

        await ctx.send("Sending request to BLUR BG N8N webhook. Wait...") # Give feedback first

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
             await ctx.send("Error: Request to BLURBG webhook timed out.")
        except requests.exceptions.RequestException as e:
            error_message = f"Failed to send to webhook. Error: {str(e)}"
            if hasattr(e, 'response') and e.response is not None:
                error_message += f"\nResponse status code: {e.response.status_code}\nResponse content: {e.response.text}"
            await ctx.send(error_message)
            print(f"Error details (!blurbg): {e}", file=sys.stderr) # Log error
        except Exception as e:
             await ctx.send(f"An unexpected error occurred: {str(e)}")
             print(f"Error details (!blurbg): {e}", file=sys.stderr)
             traceback.print_exc(file=sys.stderr) # Add traceback

    @commands.command(
        name='upscale',
        help='!upscale <Image Link>',
        description='via a N8N webhook, will run rmbg 2.0 and upscale an image',
        brief='!upscale <Image Link>'
    )
    async def upscale_command(self, ctx, *, upscale_link: str): 
        # Check if command is used in DMs
        if not isinstance(ctx.channel, discord.DMChannel):
            await ctx.send("This command can only be used in DMs.")
            return

        # TODO: Consider making webhook URL configurable via .env
        webhook_url = 'https://n8n.jeffrey-epstein.com/webhook/e9c9726e-7d24-4c66-840d-98d3016c996b'
        payload = {
            "user_id": str(ctx.author.id),
            "URL": upscale_link  # Add the YouTube link to the payload
        }

        await ctx.send("Sending request to upscale N8N webhook. Wait...") # Give feedback first

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
             await ctx.send("Error: Request to upscale webhook timed out.")
        except requests.exceptions.RequestException as e:
            error_message = f"Failed to send to webhook. Error: {str(e)}"
            if hasattr(e, 'response') and e.response is not None:
                error_message += f"\nResponse status code: {e.response.status_code}\nResponse content: {e.response.text}"
            await ctx.send(error_message)
            print(f"Error details (!upscale): {e}", file=sys.stderr) # Log error
        except Exception as e:
             await ctx.send(f"An unexpected error occurred: {str(e)}")
             print(f"Error details (!upscale): {e}", file=sys.stderr)
             traceback.print_exc(file=sys.stderr) # Add traceback

    @commands.command(
        name='replacebg',
        help='replaces background with RMBG 2.0 from an image URL and a prompt',
        description='!rmbg <Image Link> in a living room',
        brief='!replacebg <Image Link> prompt'
    )
    async def rplcbg_command(self, ctx, img_link: str, *bgprompt_words):
        bgprompt = " ".join(bgprompt_words)
        # Check if command is used in DMs
        if not isinstance(ctx.channel, discord.DMChannel):
            await ctx.send("This command can only be used in DMs.")
            return

        # TODO: Consider making webhook URL configurable via .env
        webhook_url = 'https://n8n.jeffrey-epstein.com/webhook/7f511cb0-c96e-42f8-a0a9-ea4b00313a1f'
        payload = {
            "user_id": str(ctx.author.id),
            "URL": img_link,  # Add the YouTube link to the payload
            "prompt": bgprompt  # Add the YouTube link to the payload
        }

        await ctx.send("Sending request to replace BG N8N webhook. Wait...") # Give feedback first

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
             await ctx.send("Error: Request to replace BG webhook timed out.")
        except requests.exceptions.RequestException as e:
            error_message = f"Failed to send to webhook. Error: {str(e)}"
            if hasattr(e, 'response') and e.response is not None:
                error_message += f"\nResponse status code: {e.response.status_code}\nResponse content: {e.response.text}"
            await ctx.send(error_message)
            print(f"Error details (!replacebg): {e}", file=sys.stderr) # Log error
        except Exception as e:
             await ctx.send(f"An unexpected error occurred: {str(e)}")
             print(f"Error details (!replacebg): {e}", file=sys.stderr)
             traceback.print_exc(file=sys.stderr) # Add traceback

    @commands.command(
        name='reimagine',
        help='Bria.ai reimagines an img url with a prompt',
        description='!reimagine <Image Link> in a living room',
        brief='!reimagine <Image Link> prompt'
    )
    async def reimagine_command(self, ctx, img_link: str, *bgprompt_words):
        bgprompt = " ".join(bgprompt_words)
        # Check if command is used in DMs
        if not isinstance(ctx.channel, discord.DMChannel):
            await ctx.send("This command can only be used in DMs.")
            return

        # TODO: Consider making webhook URL configurable via .env
        webhook_url = 'https://n8n.jeffrey-epstein.com/webhook/52ec541e-3972-4906-b2a6-e169157df75b'
        payload = {
            "user_id": str(ctx.author.id),
            "URL": img_link,  # Add the YouTube link to the payload
            "prompt": bgprompt  # Add the YouTube link to the payload
        }

        await ctx.send("Sending request to reimagine image to  N8N webhook. Wait...") # Give feedback first

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
             await ctx.send("Error: Request to reimagine image webhook timed out.")
        except requests.exceptions.RequestException as e:
            error_message = f"Failed to send to webhook. Error: {str(e)}"
            if hasattr(e, 'response') and e.response is not None:
                error_message += f"\nResponse status code: {e.response.status_code}\nResponse content: {e.response.text}"
            await ctx.send(error_message)
            print(f"Error details (!reimagine): {e}", file=sys.stderr) # Log error
        except Exception as e:
             await ctx.send(f"An unexpected error occurred: {str(e)}")
             print(f"Error details (!reimagine): {e}", file=sys.stderr)
             traceback.print_exc(file=sys.stderr) # Add traceback


    @commands.command(
        name='bria',
        help='bria.ai HQ image generation',
        description='via a N8N webhook, add a prompt to generate image',
        brief='!bria <prompt>'
    )
    async def bria_command(self, ctx, *, bria_prompt: str): 
        # Check if command is used in DMs
        if not isinstance(ctx.channel, discord.DMChannel):
            await ctx.send("This command can only be used in DMs.")
            return

        # TODO: Consider making webhook URL configurable via .env
        webhook_url = 'https://n8n.jeffrey-epstein.com/webhook/d92349f1-e23a-4ce9-9e2c-273b910f1d33'
        payload = {
            "user_id": str(ctx.author.id),
            "YT URL": bria_prompt  # Add the YouTube link to the payload
        }

        await ctx.send("Sending request to BRIA Image Gen N8N webhook. Wait...") # Give feedback first

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
             await ctx.send("Error: Request to BRIA Image Gen webhook timed out.")
        except requests.exceptions.RequestException as e:
            error_message = f"Failed to send to webhook. Error: {str(e)}"
            if hasattr(e, 'response') and e.response is not None:
                error_message += f"\nResponse status code: {e.response.status_code}\nResponse content: {e.response.text}"
            await ctx.send(error_message)
            print(f"Error details (!bria): {e}", file=sys.stderr) # Log error
        except Exception as e:
             await ctx.send(f"An unexpected error occurred: {str(e)}")
             print(f"Error details (!bria): {e}", file=sys.stderr)
             traceback.print_exc(file=sys.stderr) # Add traceback



    @commands.command(
        name='xshot',
        help='screenshot tweet url via rapidapi',
        description='!xshot <twitter url>',
        brief='!xshot <twitter url>'
    )
    async def xshot_command(self, ctx, *, xshot_prompt: str): 
        # Check if command is used in DMs
        if not isinstance(ctx.channel, discord.DMChannel):
            await ctx.send("This command can only be used in DMs.")
            return

        # TODO: Consider making webhook URL configurable via .env
        webhook_url = 'https://n8n.jeffrey-epstein.com/webhook/6e4d7b21-2c3e-443f-8513-128a290e2619'
        payload = {
            "user_id": str(ctx.author.id),
            "X URL": xshot_prompt  # Add the YouTube link to the payload
        }

        await ctx.send("Sending request to xshot N8N webhook. Wait...") # Give feedback first

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
             await ctx.send("Error: Request to XSHOT webhook timed out.")
        except requests.exceptions.RequestException as e:
            error_message = f"Failed to send to webhook. Error: {str(e)}"
            if hasattr(e, 'response') and e.response is not None:
                error_message += f"\nResponse status code: {e.response.status_code}\nResponse content: {e.response.text}"
            await ctx.send(error_message)
            print(f"Error details (!xshot): {e}", file=sys.stderr) # Log error
        except Exception as e:
             await ctx.send(f"An unexpected error occurred: {str(e)}")
             print(f"Error details (!xshot): {e}", file=sys.stderr)
             traceback.print_exc(file=sys.stderr) # Add traceback


# The mandatory setup function called by discord.py when loading the Cog
async def setup(bot: commands.Bot):
    await bot.add_cog(WebhooksCog(bot))
    print("WebhooksCog loaded.") # Optional confirmation message