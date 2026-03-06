import discord
from discord.ext import commands
import asyncio
import os
import sys
import subprocess
import traceback
from concurrent.futures import ThreadPoolExecutor # For type hint

# Assuming ytwriter module is importable and has a main function
try:
    import ytwriter
except ImportError:
    print("Warning: ytwriter module not found. !ytwriter command will fail.", file=sys.stderr)
    ytwriter = None # Placeholder

class ScriptsCog(commands.Cog):
    """Cog for running external Python scripts."""
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        # Access executor via self.bot.executor

    # --- Helper Methods (Moved from discord_bot.py) ---

    async def _read_output(self, process, ctx, update_interval=2):
        """Reads process output line by line and sends updates."""
        last_update_time = 0
        message_buffer = []
        all_stdout = []
        
        while True:
            line = await process.stdout.readline()
            if not line:
                break
            line = line.decode('utf-8', errors='ignore').rstrip('\r\n')
            all_stdout.append(line)
            if line: # Avoid empty lines
                message_buffer.append(line)
            
            current_time = asyncio.get_event_loop().time()
            # Send updates periodically or if buffer fills
            if current_time - last_update_time >= update_interval or len(message_buffer) >= 5:
                update_message = "\n".join(message_buffer)
                if update_message:
                    chunks = [update_message[i:i+1900] for i in range(0, len(update_message), 1900)]
                    for chunk in chunks:
                        await ctx.send(f"Script progress update:\n```\n{chunk}\n```")
                    last_update_time = current_time
                    message_buffer = []

        # Send any remaining output
        if message_buffer:
            final_update = "\n".join(message_buffer)
            chunks = [final_update[i:i+1900] for i in range(0, len(final_update), 1900)]
            for chunk in chunks:
                await ctx.send(f"Final script output:\n```\n{chunk}\n```")

        # Send any remaining stdout
        if message_buffer:
            final_update = "\n".join(message_buffer)
            chunks = [final_update[i:i+1900] for i in range(0, len(final_update), 1900)]
            for chunk in chunks:
                await ctx.send(f"Final script output:\n```\n{chunk}\n```")

        # Always send full stdout if no updates were sent
        if not all_stdout:
            # Read any remaining stdout
            while True:
                line = await process.stdout.readline()
                if not line:
                    break
                line = line.decode('utf-8', errors='ignore').rstrip('\r\n')
                all_stdout.append(line)
        
        if all_stdout:
            full_stdout = "\n".join(all_stdout)
            if full_stdout.strip():
                chunks = [full_stdout[i:i+1900] for i in range(0, len(full_stdout), 1900)]
                for chunk in chunks:
                    await ctx.send(f"Full stdout:\n```\n{chunk}\n```")
            else:
                await ctx.send("No stdout output captured.")

        # Capture and send stderr
        stderr_data = await process.stderr.read()
        if stderr_data:
            stderr_content = stderr_data.decode('utf-8', errors='ignore').strip()
            if stderr_content:
                print(f"[Script stderr]\n{stderr_content}", file=sys.stderr)
                chunks = [stderr_content[i:i+1900] for i in range(0, len(stderr_content), 1900)]
                for chunk in chunks:
                    await ctx.send(f"Stderr:\n```\n{chunk}\n```")
            else:
                await ctx.send("No stderr output.")
        else:
            await ctx.send("No stderr output.")

        await process.wait() # Wait for the process to truly finish

    # Removed _run_wrapper1 and _run_wrapper2; now using async subprocess

    def _run_salvage(self):
        script_path = r"C:\Users\test\Desktop\youtube-autoposter\salvage.py" # Consider env var/relative path
        try:
            result = subprocess.run([sys.executable, script_path], capture_output=True, text=True, check=True, timeout=600)
            return True, result.stdout
        except subprocess.CalledProcessError as e:
            return False, f"Error: {e}\nStdout: {e.stdout}\nStderr: {e.stderr}"
        except subprocess.TimeoutExpired:
            return False, "Error: salvage.py timed out."
        except Exception as e:
            return False, f"Unexpected error running salvage.py: {e}"

    # --- Script Runner Commands ---

    @commands.command(
        name='aiwriter',
        help='Execute aiwriter.py script. Use -gpt for OpenAI, -gemini for Gemini, or -ds for DeepSeek.',
        description='Run the AIWriter script. Optionally add "-gpt" to use OpenAI (gpt-4.1), "-gemini" to use Gemini (gemini-2.5-pro-exp-03-25), or "-ds" to use DeepSeek (deepseek-reasoner) instead of Claude.',
        usage='!aiwriter [-gpt | -gemini | -ds]',
        brief='!aiwriter [-gpt | -gemini | -ds]'
    )
    async def aiwriter_command(self, ctx, option: str = None): # Added option parameter
        if not isinstance(ctx.channel, discord.DMChannel):
            await ctx.send("This command can only be used in DMs.")
            return
        
        script_args = []
        ai_provider_message = "Claude (claude-3-7-sonnet-latest, default)"
        if option and option.lower() == "-gpt":
            script_args.extend(["--ai_provider", "openai"])
            ai_provider_message = "OpenAI (gpt-4.1)"
        elif option and option.lower() == "-gemini":
            script_args.extend(["--ai_provider", "gemini"])
            ai_provider_message = "Gemini (gemini-2.5-pro-exp-03-25)"
        elif option and option.lower() == "-ds": # Added DeepSeek option
            script_args.extend(["--ai_provider", "deepseek"])
            ai_provider_message = "DeepSeek (deepseek-reasoner)"

        await ctx.send(f"Executing aiwriter.py using {ai_provider_message}. This may take a while...")
        try:
            # Path relative to the main bot file location
            script_path = os.path.join(os.path.dirname(os.path.abspath(__file__)).replace("cogs", ""), "aiwriter.py")
            
            command_to_run = [sys.executable, script_path]
            command_to_run.extend(script_args) # Add provider args if any

            process = await asyncio.create_subprocess_exec(
                *command_to_run, # Unpack the command and its arguments
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            await self._read_output(process, ctx) # Use internal helper
            await ctx.send(f"aiwriter.py (using {ai_provider_message}) execution completed!")
        except Exception as e:
            await ctx.send(f"An error occurred while executing aiwriter.py: {str(e)}")
            print(f"Error details (!aiwriter): {e}", file=sys.stderr)
            traceback.print_exc(file=sys.stderr)

    @commands.command(
        name='ytwriter',
        help='Execute ytwriter.py script. Use -gpt for OpenAI, -gemini for Gemini, or -ds for DeepSeek.',
        description='Run the YTWriter script. Optionally add "-gpt" to use OpenAI (gpt-4.1), "-gemini" to use Gemini (gemini-2.5-pro-exp-03-25), or "-ds" to use DeepSeek (deepseek-reasoner) instead of Claude.',
        usage='!ytwriter [-gpt | -gemini | -ds]',
        brief='!ytwriter [-gpt | -gemini | -ds]'
    )
    async def ytwriter_command(self, ctx, option: str = None): # Added option parameter
        if not isinstance(ctx.channel, discord.DMChannel):
            await ctx.send("This command can only be used in DMs.")
            return
        if ytwriter is None:
             await ctx.send("Error: ytwriter module failed to import. Command unavailable.")
             return
             
        script_args = []
        ai_provider_message = "Claude (claude-3-7-sonnet-latest, default)"
        if option and option.lower() == "-gpt":
            script_args.extend(["--ai_provider", "openai"])
            ai_provider_message = "OpenAI (gpt-4.1)"
        elif option and option.lower() == "-gemini":
            script_args.extend(["--ai_provider", "gemini"])
            ai_provider_message = "Gemini (gemini-2.5-pro-exp-03-25)"
        elif option and option.lower() == "-ds":
            script_args.extend(["--ai_provider", "deepseek"])
            ai_provider_message = "DeepSeek (deepseek-reasoner)"

        await ctx.send(f"Executing ytwriter.py using {ai_provider_message}. This may take a while...")
        
        # Note: ytwriter.main is synchronous and expects a synchronous progress_callback.
        # We need to pass the ai_provider to the synchronous main function.
        # The progress_callback lambda needs to handle the async nature of ctx.send.
        async def progress_callback_wrapper(message):
             # Use run_coroutine_threadsafe to send message from the executor thread
             asyncio.run_coroutine_threadsafe(ctx.send(message), self.bot.loop).result()

        try:
            # Run the synchronous main function in the executor, passing the provider and the wrapped callback
            await asyncio.get_event_loop().run_in_executor(
                self.bot.executor,
                lambda: ytwriter.main(ai_provider=ai_provider_message.split(' ')[0].lower(), progress_callback=progress_callback_wrapper) # Pass provider string
            )
            await ctx.send(f"ytwriter.py (using {ai_provider_message}) execution completed!")
        except Exception as e:
            await ctx.send(f"An error occurred while executing ytwriter.py: {str(e)}")
            print(f"Error details (!ytwriter): {e}", file=sys.stderr)
            traceback.print_exc(file=sys.stderr)

    @commands.command(name='wrap1', help='Execute wrapper.py script')
    async def wrap1_command(self, ctx):
        if not isinstance(ctx.channel, discord.DMChannel):
            await ctx.send("This command can only be used in DMs.")
            return
        
        await ctx.send("Executing wrapper.py. This may take a while...")
        try:
            script_path = r"C:\Users\test\Desktop\youtube-autoposter\wrapper.py"
            # Set unbuffered output
            env = os.environ.copy()
            env['PYTHONUNBUFFERED'] = '1'
            process = await asyncio.create_subprocess_exec(
                sys.executable, script_path,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env
            )
            await self._read_output(process, ctx)
            
            returncode = process.returncode
            if returncode == 0:
                await ctx.send("wrapper.py executed successfully!")
            else:
                await ctx.send(f"wrapper.py completed with exit code {returncode}. Check output above for details.")
        except Exception as e:
            await ctx.send(f"An error occurred while executing wrapper.py: {str(e)}")
            print(f"Error details (!wrap1): {e}", file=sys.stderr)
            traceback.print_exc(file=sys.stderr)

    @commands.command(name='wrap2', help='Execute wrapper2.py script')
    async def wrap2_command(self, ctx):
        if not isinstance(ctx.channel, discord.DMChannel):
            await ctx.send("This command can only be used in DMs.")
            return
        
        await ctx.send("Executing wrapper2.py. This may take a while...")
        try:
            script_path = r"C:\Users\test\Desktop\youtube-autoposter\wrapper2.py"
            # Set unbuffered output
            env = os.environ.copy()
            env['PYTHONUNBUFFERED'] = '1'
            process = await asyncio.create_subprocess_exec(
                sys.executable, script_path,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env
            )
            await self._read_output(process, ctx)
            
            returncode = process.returncode
            if returncode == 0:
                await ctx.send("wrapper2.py executed successfully!")
            else:
                await ctx.send(f"wrapper2.py completed with exit code {returncode}. Check output above for details.")
        except Exception as e:
            await ctx.send(f"An error occurred while executing wrapper2.py: {str(e)}")
            print(f"Error details (!wrap2): {e}", file=sys.stderr)
            traceback.print_exc(file=sys.stderr)

    @commands.command(name='salvage', help='Execute salvage.py script')
    async def salvage_command(self, ctx):
        if not isinstance(ctx.channel, discord.DMChannel):
            await ctx.send("This command can only be used in DMs.")
            return
        
        await ctx.send("Executing salvage.py. This may take a while...")
        try:
            script_path = r"C:\Users\test\Desktop\youtube-autoposter\salvage.py"
            # Set unbuffered output
            env = os.environ.copy()
            env['PYTHONUNBUFFERED'] = '1'
            process = await asyncio.create_subprocess_exec(
                sys.executable, script_path,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env
            )
            await self._read_output(process, ctx)
            
            returncode = process.returncode
            if returncode == 0:
                await ctx.send("salvage.py executed successfully!")
            else:
                await ctx.send(f"salvage.py completed with exit code {returncode}. Check output above for details.")
        except Exception as e:
            await ctx.send(f"An error occurred while executing salvage.py: {str(e)}")
            print(f"Error details (!salvage): {e}", file=sys.stderr)
            traceback.print_exc(file=sys.stderr)

    @commands.command(
        name='closefirefox',
        help='Execute closefirefox.py script',
        description='Will close all firefox browsers on slave',
        usage='!closefirefox',
        brief='!closefirefox'
    )
    async def closefirefox_command(self, ctx):
        if not isinstance(ctx.channel, discord.DMChannel):
            await ctx.send("This command can only be used in DMs.")
            return
        await ctx.send("Executing closefirefox.py...")
        try:
            # Path relative to main bot file
            script_path = os.path.join(os.path.dirname(os.path.abspath(__file__)).replace("cogs", ""), "closefirefox.py") 
            process = await asyncio.create_subprocess_exec(
                sys.executable, script_path,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            await self._read_output(process, ctx) # Use internal helper
            await ctx.send("closefirefox.py execution completed!")
        except Exception as e:
            await ctx.send(f"An error occurred while executing closefirefox.py: {str(e)}")
            print(f"Error details (!closefirefox): {e}", file=sys.stderr)
            traceback.print_exc(file=sys.stderr)

# The mandatory setup function called by discord.py when loading the Cog
async def setup(bot: commands.Bot):
    await bot.add_cog(ScriptsCog(bot))
    print("ScriptsCog loaded.") # Optional confirmation message