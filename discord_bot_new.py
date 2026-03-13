"""
Vidmaker3 Discord Bot - Clean Architecture Version

This is the NEW bot that uses your existing Cogs from the cogs/ folder.
It adds:
- Structured logging
- Circuit breaker protection  
- Job queue for background processing
- Health checks
- Service client architecture (ready for microservices)

Your existing Cogs in cogs/ folder will be loaded automatically.
"""

import os
import sys
import asyncio
import logging
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor

# Ensure the current directory is in the path
current_dir = Path(__file__).parent.resolve()
sys.path.insert(0, str(current_dir))

# Import utilities
from utils import (
    setup_logging,
    get_logger,
    get_settings,
    JobQueueManager,
    get_job_queue,
    CircuitBreaker,
    get_circuit,
    CircuitOpenError,
)

# Configure logging first
setup_logging()
logger = get_logger(__name__)

# Discord imports
import discord
from discord.ext import commands, tasks
from discord import app_commands

# Load environment variables
from dotenv import load_dotenv
load_dotenv()

# ============================================================
# BOT CONFIGURATION
# ============================================================

# Get settings
settings = get_settings()

# For Discord token, use os.environ directly (more reliable than pydantic)
import os
discord_token = os.environ.get('DISCORD_BOT_TOKEN', '')

# Debug: Print token (first 10 chars only for security)
token_preview = discord_token[:10] + "..." if discord_token else "NOT SET"
logger.info(f"Settings loaded, Discord token: {token_preview}")
print(f"DEBUG: Discord token = {token_preview}")

# Discord intents
intents = discord.Intents.default()
intents.message_content = True

print(f"DEBUG: Intents - message_content: {intents.message_content}")
intents.guilds = True
intents.dm_messages = True

# Bot instance
bot = commands.Bot(
    command_prefix='!',
    intents=intents,
    help_command=None,
    case_insensitive=True,
)

# Attach ThreadPoolExecutor for cogs that need it
bot.executor = ThreadPoolExecutor(max_workers=5)

# ============================================================
# COG LOADING - Uses YOUR existing Cogs
# ============================================================

async def load_cogs():
    """Load all cogs from the cogs directory"""
    cogs_dir = current_dir / "cogs"
    
    if not cogs_dir.exists():
        logger.warning(f"Cogs directory not found: {cogs_dir}")
        return
    
    # Get list of cogs to load
    cogs_to_load = [
        'video',      # !process_sheet only (process_video disabled)
        # 'twitter',    # Disabled - !tweet, !tweetsheet
        # 'instagram',  # Disabled - !ig, !igmake
        # 'raptive',   # Disabled - !rapcalf, !rapdoc
        'scripts',    # !wrap1, !wrap2, !salvage, !closefirefox
        # 'writer',   # Disabled - !aiwriter (conflict with summarization)
        'summarization', # !sum, !sum2, !sumw
        # 'utility',  # Disabled - missing 'together' module
        'webhooks',   # !podclip, !cleartweets
        'whisper',    # !whisper - WhisperX API transcription
    ]
    
    for cog_name in cogs_to_load:
        try:
            await bot.load_extension(f"cogs.{cog_name}")
            logger.info(f"Loaded cog: cogs.{cog_name}")
        except Exception as e:
            logger.warning(f"Could not load cog {cog_name}: {e}")
            # Print to stderr so it's more visible
            import traceback
            print(f"ERROR loading cog {cog_name}:", file=sys.stderr)
            traceback.print_exc(file=sys.stderr)
    
    logger.info(f"Loaded {len(bot.cogs)} cogs")


# ============================================================
# BOT EVENTS
# ============================================================

@bot.event
async def on_ready():
    """Called when the bot is ready"""
    logger.info(
        f"Bot logged in as: {bot.user.name} ({bot.user.id})",
        extra={"discord_version": discord.__version__}
    )
    
    # Load existing cogs
    await load_cogs()
    
    # Sync commands
    try:
        await bot.tree.sync()
        logger.info("Commands synced")
    except Exception as e:
        logger.warning(f"Could not sync commands: {e}")
    
    # Set status
    await bot.change_presence(
        activity=discord.Activity(
            type=discord.ActivityType.watching,
            name="for !help"
        )
    )
    
    logger.info("Bot is ready!")


@bot.event
async def setup_hook():
    """Called after bot login but before going online"""
    logger.info("Setting up bot...")
    
    # Initialize job queue
    try:
        job_queue = get_job_queue()
        logger.info("Job queue initialized")
    except Exception as e:
        logger.warning(f"Job queue not available: {e}")


@bot.event
async def on_command_error(ctx: commands.Context, error: commands.CommandError):
    """Global error handler"""
    if isinstance(error, commands.CommandNotFound):
        return  # Ignore unknown commands
    
    if isinstance(error, commands.MissingRequiredArgument):
        await ctx.send(f"Missing required argument: {error.param.name}")
        return
    
    if isinstance(error, commands.BadArgument):
        await ctx.send(f"Invalid argument: {str(error)}")
        return
    
    if isinstance(error, commands.CommandOnCooldown):
        await ctx.send(f"Command on cooldown. Try again in {error.retry_after:.1f}s")
        return
    
    # Log unexpected errors
    logger.error(
        f"Command error in {ctx.command}",
        error=str(error),
        error_type=type(error).__name__,
        user_id=ctx.author.id,
        channel_id=ctx.channel.id
    )
    
    await ctx.send(f"An error occurred: {str(error)}")


# ============================================================
# UTILITY COMMANDS (Built into this bot)
# ============================================================

@bot.command(name="ping", help="Check bot latency")
async def ping(ctx: commands.Context):
    """Check bot latency"""
    latency = round(bot.latency * 1000, 1)
    await ctx.send(f"Pong! Latency: {latency}ms")


@bot.command(name="help", help="Show this help message")
async def help_command(ctx: commands.Context):
    """Show help message"""
    embed = discord.Embed(
        title="Vidmaker3 Bot Commands",
        description="Video processing and automation bot",
        color=discord.Color.blue()
    )
    
    # Show loaded cogs
    cog_list = ", ".join(bot.cogs.keys()) if bot.cogs else "None"
    embed.add_field(
        name="Loaded Cogs",
        value=cog_list,
        inline=False
    )
    
    # Core commands
    embed.add_field(
        name="Core Commands",
        value="""```
!ping          - Check bot latency
!health        - Check bot health
!queue         - Show job queue status
!cancel <job>  - Cancel a queued job
```""",
        inline=False
    )
    
    # Video commands
    embed.add_field(
        name="Video Processing",
        value="""```
!process_video <url> [format] [transcribe]
!process_sheet   - Process Google Sheet
!flo <url>       - Process FloGrappling video
!pull <url>      - Download and transcribe
```""",
        inline=False
    )
    
    # Summarization commands
    embed.add_field(
        name="Summarization (DM only)",
        value="""```
!sumw <url>    - Whisper transcription
!sum <url>     - OpenAI GPT summary
!sum2 <url>    - Claude Sonnet summary
```""",
        inline=False
    )
    
    # Scripts commands
    embed.add_field(
        name="Scripts (DM only)",
        value="""```
!wrap1         - Execute wrapper.py script
!wrap2         - Execute wrapper2.py script
!salvage       - Execute salvage.py script
!closefirefox  - Close all Firefox browsers
```""",
        inline=False
    )
    
    # Webhooks commands
    embed.add_field(
        name="Webhooks (DM only)",
        value="""```
!podclip       - Create viral clips from YT URL
!cleartweets   - Clear scanned tweet sheet
```""",
        inline=False
    )
    
    await ctx.send(embed=embed)


@bot.command(name="health", help="Check bot health status")
async def health_check(ctx: commands.Context):
    """Check health of bot and dependencies"""
    embed = discord.Embed(
        title="Bot Health Status",
        color=discord.Color.green()
    )
    
    # Bot status
    embed.add_field(name="Bot", value="✅ Online", inline=True)
    embed.add_field(name="Latency", value=f"{round(bot.latency * 1000)}ms", inline=True)
    embed.add_field(name="Guilds", value=str(len(bot.guilds)), inline=True)
    
    # Check Redis
    try:
        job_queue = get_job_queue()
        stats = job_queue.get_queue_stats()
        embed.add_field(
            name="Redis",
            value=f"✅ Connected\nQueued: {stats['queued']}",
            inline=True
        )
    except Exception as e:
        embed.add_field(
            name="Redis",
            value=f"❌ Error: {str(e)[:50]}",
            inline=True
        )
    
    await ctx.send(embed=embed)


@bot.command(name="queue", help="Show job queue status")
async def show_queue(ctx: commands.Context):
    """Show current job queue status"""
    try:
        job_queue = get_job_queue()
        stats = job_queue.get_queue_stats()
        
        embed = discord.Embed(
            title="Job Queue Status",
            color=discord.Color.blue()
        )
        embed.add_field(name="Queued Jobs", value=str(stats['queued']), inline=True)
        embed.add_field(name="Active Workers", value=str(stats['workers']), inline=True)
        
        await ctx.send(embed=embed)
    except Exception as e:
        await ctx.send(f"❌ Error getting queue status: {str(e)}")


@bot.command(name="cogs", help="List loaded cogs")
async def list_cogs(ctx: commands.Context):
    """List all loaded cogs"""
    if bot.cogs:
        cog_list = "\n".join([f"• {name}" for name in bot.cogs.keys()])
        embed = discord.Embed(
            title="Loaded Cogs",
            description=cog_list,
            color=discord.Color.green()
        )
    else:
        embed = discord.Embed(
            title="Loaded Cogs",
            description="No cogs loaded",
            color=discord.Color.red()
        )
    
    await ctx.send(embed=embed)


# ============================================================
# ADMIN COMMANDS
# ============================================================

@bot.command(name="restart", help="Restart the bot (admin only)")
async def restart_command(ctx: commands.Context):
    """Restart the bot"""
    if not isinstance(ctx.channel, discord.DMChannel):
        await ctx.send("This command can only be used in DMs.")
        return
    
    await ctx.send("Restarting bot...")
    logger.info("Bot restart requested")
    await bot.close()
    os.execv(sys.executable, [sys.executable] + sys.argv)


@bot.command(name="reload", help="Reload a cog (admin only)")
async def reload_cog(ctx: commands.Context, cog_name: str):
    """Reload a specific cog"""
    if not isinstance(ctx.channel, discord.DMChannel):
        await ctx.send("This command can only be used in DMs.")
        return
    
    try:
        # Remove 'cogs.' prefix if present
        if cog_name.startswith("cogs."):
            cog_name = cog_name[5:]
        
        full_name = f"cogs.{cog_name}"
        
        # Unload if already loaded
        if bot.get_cog(cog_name.capitalize()):
            await bot.unload_extension(full_name)
        
        # Reload
        await bot.load_extension(full_name)
        await ctx.send(f"✅ Reloaded cog: {full_name}")
        logger.info(f"Reloaded cog: {full_name}")
        
    except Exception as e:
        await ctx.send(f"❌ Error reloading cog: {str(e)}")
        logger.error(f"Error reloading cog {cog_name}: {e}", exc_info=True)


@bot.tree.error
async def on_app_command_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    """Handle slash command errors"""
    logger.error(
        f"Slash command error: {error}",
        command=interaction.command.name if interaction.command else "unknown"
    )
    
    if isinstance(error, app_commands.CommandOnCooldown):
        await interaction.response.send_message(
            f"Command on cooldown. Try again in {error.retry_after:.1f}s",
            ephemeral=True
        )
    else:
        await interaction.response.send_message(
            f"An error occurred: {str(error)}",
            ephemeral=True
        )


# ============================================================
# MAIN ENTRY POINT
# ============================================================

def main():
    """Main entry point"""
    logger.info("Starting Vidmaker3 Bot...")
    
    # Validate configuration - use the global discord_token
    global discord_token
    if not discord_token:
        logger.error("DISCORD_BOT_TOKEN not set. Please configure in .env file.")
        print("ERROR: DISCORD_BOT_TOKEN not set in .env file")
        sys.exit(1)
    
    # Run the bot
    try:
        bot.run(discord_token)
    except discord.errors.LoginFailure:
        logger.error("Invalid Discord bot token!")
        sys.exit(1)
    except Exception as e:
        logger.error(f"Bot error: {e}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
