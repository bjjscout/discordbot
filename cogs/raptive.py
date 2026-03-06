import discord
from discord.ext import commands
import asyncio
import os
import sys
import re # Needed for parsing in rapcalf/rapdoc
import traceback
from concurrent.futures import ThreadPoolExecutor # For type hint

class RaptiveCog(commands.Cog):
    """Cog for handling Raptive uploads and related commands."""
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        # Access executor via self.bot.executor if needed

    # --- Helper Function (Moved from discord_bot.py) ---
    async def _read_output_with_delay(self, process, ctx):
        """Reads stdout and stderr concurrently, sending stdout lines with delay."""
        
        async def stream_reader(stream, stream_name):
            """Helper coroutine to read a stream line by line."""
            output_lines = []
            while True:
                line = await stream.readline()
                if not line:
                    break
                decoded_line = line.decode().strip()
                output_lines.append(decoded_line)
                if stream_name == 'stdout' and decoded_line:
                    # Send stdout lines to Discord with delay
                    await ctx.send(decoded_line)
                    await asyncio.sleep(1)
                elif stream_name == 'stderr' and decoded_line:
                    # Print stderr immediately to console
                    print(f"[Script stderr] {decoded_line}", file=sys.stderr)
                    # Optionally send stderr to Discord too (might be noisy)
                    # await ctx.send(f"```stderr\n{decoded_line}\n```")
            return "\n".join(output_lines)

        # Run readers for stdout and stderr concurrently
        stdout_task = asyncio.create_task(stream_reader(process.stdout, 'stdout'))
        stderr_task = asyncio.create_task(stream_reader(process.stderr, 'stderr'))

        # Wait for both streams to be fully read and the process to exit
        stdout_full, stderr_full = await asyncio.gather(stdout_task, stderr_task)
        await process.wait()
        
        # Optional: Log full stderr if needed after process completion
        # if stderr_full:
        #     print(f"--- Full stderr for {process.pid} ---\n{stderr_full}\n---------------------------------", file=sys.stderr)

    # --- Raptive Commands ---

    @commands.command(
        name='rapcalf',
        help='Upload video to Raptive Calf site and get embed code',
        description='Downloads video from URL, uploads to Raptive Calf site, returns embed code',
        usage='!rapcalf <direct video_url> -title: Your video title',
        brief='!rapcalf <direct video_url> -title: Your video title'
    )
    async def rapcalf_command(self, ctx):
        if not isinstance(ctx.channel, discord.DMChannel):
            await ctx.send("This command can only be used in DMs.")
            return
        
        content = ctx.message.content
        # Remove the command prefix and leading/trailing whitespace
        command_args_string = content[len('!rapcalf '):].strip()

        # Split the string by '-title:' to separate URL and title
        parts = command_args_string.split('-title:', 1)

        url = parts[0].strip()
        custom_title = None

        if len(parts) > 1:
            custom_title = parts[1].strip()

        if not url:
            await ctx.send("Please provide a video URL.")
            return

        if not custom_title:
            await ctx.send("Please provide a title using: !rapcalf <url> -title: Your title")
            return

        await ctx.send(f"Starting Raptive upload for: {custom_title}...")
        try:
            # Adjust path relative to the main bot file location
            script_path = os.path.join(os.path.dirname(os.path.abspath(__file__)).replace("cogs", ""), "calfupload.py") 
            print(f"Running script: {script_path}")
            process = await asyncio.create_subprocess_exec(
                sys.executable, script_path, url, "-title", custom_title, # Pass URL and title with -title flag
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
            )
            
            # Use internal helper method
            await self._read_output_with_delay(process, ctx) 
            
            exit_code = process.returncode # Get exit code after waiting in helper

            if exit_code == 0:
                # Output should have been streamed by the helper
                # We could potentially re-read stdout here if needed, but let's rely on streaming for now
                await ctx.send(f"✅ Upload script finished successfully for {custom_title}.") 
            else: 
                await ctx.send(f"❌ Error running upload script (exit code {exit_code}). Check logs or previous messages for details.")
                
        except Exception as e:
            await ctx.send(f"⚠️ Unexpected error in !rapcalf command: {str(e)}")
            traceback.print_exc(file=sys.stderr)

    @commands.command(
        name='rapdoc',
        help='Upload video to Raptive Doc site and get embed code',
        description='Downloads video from URL, uploads to Raptive Doc site, returns embed code',
        usage='!rapdoc <direct video_url> -title: Your video title',
        brief='!rapdoc <direct video_url> -title: Your video title'
    )
    async def rapdoc_command(self, ctx):
        if not isinstance(ctx.channel, discord.DMChannel):
            await ctx.send("This command can only be used in DMs.")
            return
        
        content = ctx.message.content
        # Remove the command prefix and leading/trailing whitespace
        command_args_string = content[len('!rapdoc '):].strip()

        # Split the string by '-title:' to separate URL and title
        parts = command_args_string.split('-title:', 1)

        url = parts[0].strip()
        custom_title = None

        if len(parts) > 1:
            custom_title = parts[1].strip()

        if not url:
            await ctx.send("Please provide a video URL.")
            return

        if not custom_title:
            await ctx.send("Please provide a title using: !rapdoc <url> -title: Your title")
            return

        await ctx.send(f"Starting Raptive upload for: {custom_title}...")
        try:
            # Adjust path relative to the main bot file location
            script_path = os.path.join(os.path.dirname(os.path.abspath(__file__)).replace("cogs", ""), "docupload.py") # Use docupload
            print(f"Running script: {script_path}")
            process = await asyncio.create_subprocess_exec(
                sys.executable, script_path, url, "-title", custom_title, # Pass URL and title with -title flag
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
            )
            
            # Use internal helper method
            await self._read_output_with_delay(process, ctx) 
            
            exit_code = process.returncode # Get exit code after waiting in helper

            if exit_code == 0:
                await ctx.send(f"✅ Upload script finished successfully for {custom_title}.")
            else: 
                await ctx.send(f"❌ Error running upload script (exit code {exit_code}). Check logs or previous messages for details.")
                
        except Exception as e: # Catch errors in the outer try block for the command
            await ctx.send(f"⚠️ Unexpected error in !rapdoc command: {str(e)}")
            traceback.print_exc(file=sys.stderr)

    @commands.command(name='upload_sheet', help='Upload videos to Raptive based on parameters set in sheet')
    async def upload_sheet_command(self, ctx):
        if not isinstance(ctx.channel, discord.DMChannel):
            await ctx.send("This command can only be used in DMs.")
            return

        await ctx.send("Starting to process the upload sheet. This may take a while...")

        try:
            # Use the absolute path as in the original code
            script_path = r"C:\raptive\app2b.py"
            
            # Check if the script exists at the absolute path
            if not os.path.exists(script_path):
                 await ctx.send(f"Error: Script file not found: {script_path}")
                 return

            process = await asyncio.create_subprocess_exec(
                sys.executable, script_path,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
                # No cwd parameter, run from the bot's default CWD
            )

            # Use the updated internal helper method to read output
            await self._read_output_with_delay(process, ctx)

            # Check the return code *after* reading output, mirroring original logic
            if process.returncode != 0:
                # The helper should have printed stderr, but send a generic error too
                await ctx.send(f"An error occurred while processing the upload sheet (exit code {process.returncode}). Check logs.")
            else:
                 await ctx.send("Upload sheet processing completed.") # Keep completion message

        except Exception as e:
             await ctx.send(f"An error occurred launching upload_sheet: {str(e)}")
             print(f"Error details (!upload_sheet): {e}", file=sys.stderr)
             traceback.print_exc(file=sys.stderr)

    @commands.command(name="login")
    async def run_login(self, ctx):
        """Runs the login script and sends progress updates via DMs."""
        if not isinstance(ctx.channel, discord.DMChannel):
            await ctx.send("This command can only be used in DMs.")
            return
        await ctx.send("Starting login process...")
        try:
            # Consider making path relative or env var
            # Path needs to be relative to the main bot file
            script_path = r"c:/raptive/login.py" 
            process = await asyncio.create_subprocess_exec(
                sys.executable, script_path, 
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            )
            # Read output asynchronously and send as DMs (don't use delay helper here)
            async for line in process.stdout:
                content = line.decode().strip()
                if content: # Avoid sending empty lines
                    await ctx.send(content)
            
            # Handle errors
            stdout, stderr = await process.communicate()
            returncode = process.returncode
            if returncode != 0:
                await ctx.send(f"Error during login process (code {returncode}):")
                if stderr: 
                    err_content = stderr.decode().strip()
                    if err_content: await ctx.send(f"```\n{err_content}\n```")
            else:
                await ctx.send("Login process completed.")
        except Exception as e:
             await ctx.send(f"An error occurred launching login script: {str(e)}")
             print(f"Error details (!login): {e}", file=sys.stderr)
             traceback.print_exc(file=sys.stderr)

# The mandatory setup function called by discord.py when loading the Cog
async def setup(bot: commands.Bot):
    await bot.add_cog(RaptiveCog(bot))
    print("RaptiveCog loaded.") # Optional confirmation message