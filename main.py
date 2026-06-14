import os
from dotenv import load_dotenv
import asyncio
import discord
from discord.ext import commands, tasks
from discord import app_commands
import asyncio
from datetime import datetime, timezone
from pymongo import AsyncMongoClient
from utils import (
    is_admin,
    get_player_count,
    start_vm,
    stop_vm,
    stop_mc_server,
    get_vm_status,
    format_duration,
    gb,
    ping_stats,
)

from stats.graphs import plot_metric
from stats.mongo import server_metrics, players, duels_db

load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")

intents = discord.Intents.default()
intents.message_content = True

MINECRAFT_COMMANDS = {"start", "stop", "stats", "graph", "duels", "players"}

class MinecraftCommandTree(app_commands.CommandTree):
    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.command and interaction.command.name in MINECRAFT_COMMANDS:
            mc_ready_env = os.getenv("MC_READY", "false") or "false"
            mc_ready = mc_ready_env.lower() == "true"
            
            if not mc_ready:
                await interaction.response.send_message(
                    "This feature is currently disabled because the Minecraft server services are offline.",
                    ephemeral=False
                )
                return False
        return True

bot = commands.Bot(command_prefix="$", intents=intents, help_command=None, tree_cls=MinecraftCommandTree)
tree = bot.tree

# new async client for auth db, cuz 1 project db can only have 1 cluster under mongo free plan
mongo_uri = os.getenv("AUTH_MONGO_URI", os.getenv("MONGO_URI", ""))
auth_db_name = os.getenv("AUTH_DB_NAME", "xymic")
_mongo = AsyncMongoClient(mongo_uri, tz_aware=True) if mongo_uri else None
bot.link_collection = _mongo[auth_db_name]["link"] 
bot.verify_enabled = True

empty_time = None
trigger_shutdown = False

VOTE_EMOJI = "👍"
REQUIRED_VOTES = 4

active_vote_message_id = None
current_votes = set()


CLOCK = "<a:Minecraft_clock:1462830831092498671>"
PARROT = "<a:dancing_parrot:1462833253692997797>"
CHEST = "<a:MinecraftChestOpening:1462837623625355430>"
TNT = "<a:TNT:1462841582376980586>"
FLAME = "<a:animated_flame:1462846702191907013>"
SAD = "<:jeb_screm:1462848647149519145>"
RED_DOT = "🔴"
GREEN_DOT = "🟢"


def embed_starting():
    """
    STACK: Discord information
    Send an `Embed` acknowledgment when the server is starting.

    Returns:
        Embed (Discord obj)
    """
    return (
        discord.Embed(
            title=f"{CLOCK} Starting PESU Minecraft Server",
            description=(
                "Your beloved server is booting up!\n\n"
                f"This may take a while {PARROT}"
            ),
            color=discord.Color.blue(),
            timestamp=datetime.now(timezone.utc),
        )
        .set_footer(text="Xymic")
        .set_thumbnail(
            url="https://images-ext-1.discordapp.net/external/7nIEsery5zNVdedxw1ZE4KbpDsdbynTfKfBiVvBxH4k/%3Fsize%3D4096/https/cdn.discordapp.com/icons/1406919525831540817/0c5be54039c065ad713c2e60cdcf1d3d.png?format=webp&quality=lossless&width=579&height=579"
        )
    )


def embed_started():
    """
    STACK: Discord information
    Send an `Embed` acknowledgment when the server has started.

    Returns:
        Embed (Discord obj)
    """
    return discord.Embed(
        title="✅ Server Online",
        description=(f"Get in losers - the server is going live! {CHEST}"),
        color=discord.Color.green(),
        timestamp=datetime.now(timezone.utc),
    ).set_footer(text="Xymic")


def embed_manual_stop():
    """
    STACK: Discord information
    Send an `Embed` acknowledgment when the server is shutting down.

    Returns:
        Embed (Discord obj)
    """
    return discord.Embed(
        title=f"{TNT} Server Shutdown Requested",
        description=("The Minecraft server is now shutting down.\n"),
        color=discord.Color.orange(),
        timestamp=datetime.now(timezone.utc),
    ).set_footer(text="Xymic")


def embed_auto_shutdown():
    """
    STACK: Discord information
    Send an `Embed` acknowledgment when the server stops automatically.

    Returns:
        Embed (Discord obj)
    """
    return discord.Embed(
        title=f"{SAD} Server Idle",
        description=(
            "The server has been empty for **5 minute**.\n"
            "Initiating automatic shutdown sequence…"
        ),
        color=discord.Color.gold(),
        timestamp=datetime.now(timezone.utc),
    ).set_footer(text="Xymic")


def embed_stopped():
    """
    STACK: Discord information
    Send an `Embed` acknowledgment when the server has shut down.

    Returns:
        Embed (Discord obj)
    """
    return (
        discord.Embed(
            title="❌ Server Stopped",
            description=(
                "The Minecraft server has been stopped successfully.\n\n"
                f"{FLAME} The VM is now powering off to save resources."
            ),
            color=discord.Color.red(),
            timestamp=datetime.now(timezone.utc),
        )
        .set_footer(text="Xymic")
        .set_thumbnail(
            url="https://images-ext-1.discordapp.net/external/7nIEsery5zNVdedxw1ZE4KbpDsdbynTfKfBiVvBxH4k/%3Fsize%3D4096/https/cdn.discordapp.com/icons/1406919525831540817/0c5be54039c065ad713c2e60cdcf1d3d.png?format=webp&quality=lossless&width=579&height=579"
        )
    )


def embed_no_permission():
    """
    STACK: Discord permissions
    Send an `Embed` acknowledgment when the user doesn't have permissions to run the command.

    Returns:
        Embed (Discord obj)
    """
    return discord.Embed(
        title="🚫 Permission Denied",
        description=(
            "You don’t have permission to use this command.\n\n"
            "🔐 This action is restricted to server admins only."
        ),
        color=discord.Color.dark_red(),
        timestamp=datetime.now(timezone.utc),
    ).set_footer(text="Xymic")


def embed_vote_start():
    return discord.Embed(
        title="🗳️ Vote to Start Server",
        description=(
            f"React with {VOTE_EMOJI} to start the Minecraft server.\n\n"
            f"Votes needed: **{REQUIRED_VOTES+1}**"
        ),
        color=discord.Color.blurple(),
        timestamp=datetime.now(timezone.utc),
    ).set_footer(text="Xymic")

async def expire_vote(channel):
    global active_vote_message_id, current_votes, vote_task

    await asyncio.sleep(60)

    if active_vote_message_id is not None:
        await channel.send("⏱️ Vote expired. Not enough votes to start the server.")
        active_vote_message_id = None
        current_votes.clear()
        vote_task = None

def embed_vm_stop():
    """
    STACK: VM control
    Send an `Embed` acknowledgment when the Google VM stops.

    Returns:
        Embed (Discord obj)
    """
    return discord.Embed(
        title="The VM has been stopped.",
        color=discord.Color.red(),
        timestamp=datetime.now(timezone.utc),
    ).set_footer(text="Xymic")


@bot.event
async def on_ready():
    """
    STACK: Discord Bot
    Login acknowledgement and start timers for `check_server`
    """
    await bot.load_extension("auth.verify")

    guild_id = os.getenv("GUILD_ID")
    if guild_id:
        try:
            guild = discord.Object(id=int(guild_id))
            tree.copy_global_to(guild=guild)
            await tree.sync(guild=guild)
            print(f"[DISCORD BOT] Synced commands to guild {guild_id}")
            
            tree.clear_commands(guild=None)
            await tree.sync()
            # this to clear global comms , and to prevent dups
        except Exception as e:
            print(f"[DISCORD BOT] Failed to sync: {e}")
    else:
        await tree.sync()
        print("[DISCORD BOT] Synced commands globally (no GUILD_ID set)")

    print(f"[DISCORD BOT] Logged in as {bot.user}")
    
    mc_ready_env = os.getenv("MC_READY", "false") or "false"
    mc_ready = mc_ready_env.lower() == "true"
    if mc_ready:
        check_server.start()

@bot.event
async def on_message(message):
    if message.author.bot:
        return

    if message.content.startswith("$"):
        await message.reply(
            "⚠️ **Prefix commands are being deprecated.**\n"
            "Please start using **slash commands** instead.\n\n"
            "Examples:\n"
            "`/start`  `/stop`  `/stats`  `/help`",
            mention_author=False,
        )

    await bot.process_commands(message)


@bot.event
async def on_reaction_add(reaction, user):
    """
    Reaction counter to check if the reactions matched the
    required number and start the VM accordingly.

    Args:
        reaction: Reaction object
        user: User that reacted
    """
    global current_votes, active_vote_message_id

    if user.bot:
        return
    if active_vote_message_id is None:
        return
    if reaction.message.id != active_vote_message_id:
        return
    if str(reaction.emoji) != VOTE_EMOJI:
        return
    if user.id in current_votes:
        return

    current_votes.add(user.id)

    print(f"[DISCORD BOT] Votes: {len(current_votes)}/{REQUIRED_VOTES}")

    if len(current_votes) >= REQUIRED_VOTES:
        channel = reaction.message.channel
        active_vote_message_id = None
        current_votes.clear()

        await channel.send(embed=embed_starting())
        await start_vm()
        await channel.send(embed=embed_started())


@tree.command(name="start", description="Start the Minecraft server")
async def start(interaction: discord.Interaction):
    """
    STACK: Server control
    Starts the minecraft server if the user is admin, if not,
    make a poll to get 4+ votes in order to start the server.

    """
    global active_vote_message_id, current_votes, vote_task

    status = await get_vm_status()

    if status == "RUNNING":
        await interaction.response.send_message(
            "🟢 The server is already online.",
            ephemeral=True
        )
        return

    if status != "TERMINATED":
        await interaction.response.send_message(
            "🟡 The VM is currently busy. Please wait until it is fully stopped.",
            ephemeral=True
        )
        return

    if is_admin(interaction):
        await interaction.response.send_message(embed=embed_starting(), ephemeral=True)
        await start_vm()
        await interaction.followup.send(embed=embed_started())
        return

    if active_vote_message_id is not None:
        await interaction.response.send_message(
            "🗳️ A vote is already in progress. Please wait.",
            ephemeral=True
        )
        return

    current_votes = set()
    await interaction.response.send_message(
        "🗳️ Vote started in this channel. React to participate.",
        ephemeral=True
    )

    vote_message = await interaction.channel.send(embed=embed_vote_start())
    active_vote_message_id = vote_message.id
    await vote_message.add_reaction(VOTE_EMOJI)

    vote_task = bot.loop.create_task(expire_vote(interaction.channel))


@tree.command(name="stop", description="Stop the Minecraft server")
async def stop(interaction: discord.Interaction):
    """
    STACK: Server control
    Stop the server.
    """
    if not is_admin(interaction):
        await interaction.response.send_message(
            embed=embed_no_permission(),
            ephemeral=True
        )
        return

    await interaction.response.send_message(embed=embed_manual_stop())
    await shutdown_server(manual=True)

@tasks.loop(seconds=10)
async def check_server():
    """
    STACK: Server control
    Poll to check if server has no members for longer than a minute and shutdown accordingly.
    """
    global empty_time, trigger_shutdown
    status = await get_vm_status()

    if status == "RUNNING":
        player_count = await get_player_count()
        if player_count is None:
            return

        print(f"[SERVER CONTROL] Players online: {player_count}")
        if player_count == 0:
            if empty_time is None:
                empty_time = datetime.now()
            else:
                elapsed = (datetime.now() - empty_time).total_seconds()
                if elapsed >= 300 and not trigger_shutdown:
                    trigger_shutdown = True
                    await shutdown_server()
        else:
            empty_time = None
            trigger_shutdown = False
    else:
        print("[SERVER CONTROL] Server is off")

@tree.command(name="players", description="List online players")
async def players_cmd(interaction: discord.Interaction):
    """
    STACK: Players
    Lists all currently online players.
    """

    status = await get_vm_status()
    if status != "RUNNING":
        embed = discord.Embed(
            title="🔴 Server Offline",
            description="The server is currently offline.",
            color=discord.Color.red(),
            timestamp=datetime.now(timezone.utc),
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)
        return

    await ping_stats()

    online_players = list(players.find({"online": True}))

    if not online_players:
        embed = discord.Embed(
            title="🟡 No Players Online",
            description="There are currently no players on the server.",
            color=discord.Color.gold(),
            timestamp=datetime.now(timezone.utc),
        )
        await interaction.response.send_message(embed=embed)
        return

    names = [doc.get("name", "Unknown") for doc in online_players]

    embed = discord.Embed(
        title="🟢 Players Online",
        description=f"**{len(names)} player(s) currently online**\n\n"
        + "\n".join(f"• `{name}`" for name in names),
        color=discord.Color.green(),
        timestamp=datetime.now(timezone.utc),
    )

    await interaction.response.send_message(embed=embed)


@tree.command(name="stats", description="View server or player stats")
@app_commands.describe(mode="server or player", player="Player username")
async def stats(interaction: discord.Interaction, mode: str, player: str = None):
    """
    STACK: Stats
    Bot command definition for `stats`.
    - If no mode is passed (or unknown mode), return syntax.
    - If the mode is `server`, call `stats_server`.
    - If the mode is player, call `stats_player`.

    Args:
        mode: Whether to get server information or induvidual player information
        player: The player for which information is to be retreived.
    """
    if mode is None:
        await interaction.response.send_message("Usage: `/stats server` or `/stats player <name>`", ephemeral=True)
        return

    if mode.lower() == "server":
        await interaction.response.defer()
        await stats_server(interaction)
    elif mode.lower() == "player":
        if not player:
            await interaction.response.send_message("Usage: `/stats player <username>`", ephemeral=True)
            return
        await interaction.response.defer()
        await stats_player(interaction, player)
    else:
        await interaction.response.send_message("Unknown option. Use `server` or `player`.", ephemeral=True)


@tree.command(name="graph", description="Show server performance graphs")
@app_commands.describe(metric="Metric name", minutes="Time window in minutes")
async def graph(interaction: discord.Interaction, metric: str = None, minutes: int = 60):
    """
    STACK: Stats
    Usage:
      /graph <metric> [minutes]

    Metrics:
      players
      cpu_sys || cpu
      cpu_jvm
      ram_sys || ram
      ram_jvm
      heap
      chunks
      joins
      deaths
    """

    if not metric:
        await interaction.response.send_message(
            "Usage: `/graph <metric> [minutes]`\n\n"
            "**Available metrics:**\n"
            "`players`              : Players online\n"
            "`cpu_sys`              : System CPU %\n"
            "`cpu_jvm`              : JVM CPU %\n"
            "`ram_sys`              : System RAM used (GB)\n"
            "`ram_jvm`              : JVM RSS (GB)\n"
            "`heap`                 : JVM heap used (GB)\n"
            "`chunks`               : Loaded chunks\n"
            "`joins`                : Total joins\n"
            "`uniq_joins`           : Total unique joins\n"
            "`deaths`               : Total deaths\n\n"
            "Example:\n"
            "`/graph cpu_sys 30`",
            ephemeral=True
        )
        return

    metric_map = {
        "players": ("player_count", "Players Online", 1.0, None),
        "chunks": ("loaded_chunks", "Loaded Chunks", 1.0, None),
        "joins": ("total_joins", "Total Joins", 1.0, None),
        "uniq_joins": ("total_unique_joins", "Total Unique Joins", 1.0, None),
        "deaths": ("total_deaths", "Total Deaths", 1.0, None),
        "cpu_sys": ("cpu_system_pct", "System CPU (%)", 1.0, (0, 100)),
        "cpu": ("cpu_system_pct", "System CPU (%)", 1.0, (0, 100)),
        "cpu_jvm": ("cpu_jvm_pct", "JVM CPU (%)", 1.0, (0, 100)),
        "ram_sys": ("ram_system_used", "System RAM Used (GB)", 1 / (1024**3), None),
        "ram": ("ram_system_used", "System RAM Used (GB)", 1 / (1024**3), None),
        "ram_jvm": ("jvm_rss_used", "JVM RSS Used (GB)", 1 / (1024**3), None),
        "heap": ("jvm_heap_used", "JVM Heap Used (GB)", 1 / (1024**3), None),
    }

    metric = metric.lower()

    if metric not in metric_map:
        await interaction.response.send_message(f"Unknown metric.\nAvailable: {', '.join(metric_map.keys())}", ephemeral=True)
        return

    await interaction.response.defer()

    col, label, scale, clamp = metric_map[metric]

    path = plot_metric(
        col,
        minutes=minutes,
        ylabel=label,
        scale=scale,
        clamp=clamp,
    )

    if not path:
        await interaction.followup.send("No data available for that time range.")
        return

    await interaction.followup.send(file=discord.File(path))

    try:
        os.remove(path)
    except Exception as e:
        print(f"[STATS] Failed to delete graph file {path}: {e}")


async def stats_server(interaction):
    """
    STACK: Stats
    Fetches latest server statistics from MongoDB and returns a Discord embed.
    """
    await ping_stats()
    doc = server_metrics.find_one(sort=[("timestamp", -1)])

    if not doc:
        embed = discord.Embed(
            title="🔴 Minecraft Server Stats",
            description="No data available.",
            color=discord.Color.red(),
            timestamp=datetime.now(timezone.utc),
        )
        await interaction.followup.send(embed=embed)
        return

    status = await get_vm_status()
    offline = status != "RUNNING"

    embed = discord.Embed(
        title="Minecraft Server Stats",
        color=discord.Color.red() if offline else discord.Color.green(),
        timestamp=datetime.now(timezone.utc),
    )

    embed.description = (
        "🔴 Server is **offline**. Showing last known data."
        if offline
        else "🟢 Server is **online**. Showing live data."
    )

    embed.add_field(name="Players Online", value=doc.get("player_count", 0), inline=True)
    embed.add_field(name="Loaded Chunks", value=doc.get("loaded_chunks", 0), inline=True)

    embed.add_field(
        name="CPU Usage",
        value=(
            f"System: `{doc.get('cpu_system_pct', 0):.2f}%`\n"
            f"JVM: `{doc.get('cpu_jvm_pct', 0):.2f}%`"
        ),
        inline=False,
    )

    embed.add_field(
        name="Memory (System)",
        value=(
            f"Used: `{gb(doc.get('ram_system_used', 0))}`\n"
            f"Total: `{gb(doc.get('ram_system_total', 0))}`"
        ),
        inline=False,
    )

    embed.add_field(
        name="Memory (JVM)",
        value=(
            f"Heap: `{gb(doc.get('jvm_heap_used', 0))} / {gb(doc.get('jvm_heap_max', 0))}`\n"
            f"RSS: `{gb(doc.get('jvm_rss_used', 0))}`"
        ),
        inline=False,
    )

    embed.add_field(
        name="Totals",
        value=(
            f"Total Joins: `{doc.get('total_joins', 0)}`\n"
            f"Unique Joins: `{doc.get('total_unique_joins', 0)}`\n"
            f"Total Deaths: `{doc.get('total_deaths', 0)}`"
        ),
        inline=False,
    )

    embed.add_field(name="Uptime", value=format_duration(doc.get("uptime_ms", 0)), inline=True)
    embed.add_field(name="Total Runtime", value=format_duration(doc.get("total_runtime_ms", 0)), inline=True)

    await interaction.followup.send(embed=embed)


async def stats_player(interaction, username):
    """
    STACK: Stats
    Fetches individual player statistics based on username from MongoDB.
    """
    await ping_stats()
    doc = players.find_one({"name": {"$regex": f"^{username}$", "$options": "i"}})
    true_deaths = doc.get("total_deaths", 0)
    true_player_kills = doc.get("player_kills", 0)

    if not doc:
        await interaction.followup.send("Player not found.")
        return

    status = await get_vm_status() == "RUNNING"
    online = bool(doc.get("online", False)) & status

    embed = discord.Embed(
        title=f"Player Stats: {doc.get('name', 'Unknown')}",
        color=discord.Color.green() if online else discord.Color.red(),
        timestamp=datetime.now(timezone.utc),
    )

    embed.add_field(name="Status", value="🟢 Online" if online else "🔴 Offline", inline=True)
    embed.add_field(name="Playtime", value=format_duration(doc.get("total_playtime_ms", 0)), inline=True)
    embed.add_field(name="Total Joins", value=doc.get("total_joins", 0), inline=True)
    embed.add_field(name="Deaths", value=f"{true_deaths}", inline=True)
    embed.add_field(name="Player Kills", value=f"{true_player_kills}", inline=True)
    embed.add_field(name="Mob Kills", value=doc.get("mob_kills", 0), inline=True)
    embed.add_field(name="Blocks Broken", value=doc.get("blocks_broken", 0), inline=True)
    embed.add_field(name="Blocks Placed", value=doc.get("blocks_placed", 0), inline=True)
    embed.add_field(name="Villager Trades", value=doc.get("villager_trades", 0), inline=True)
    embed.add_field(name="Animals bred", value=doc.get("animals_bred", 0), inline=True)
    embed.add_field(name="Advancements", value=doc.get("advancements", 0), inline=True)
    embed.add_field(name="Messages Sent", value=doc.get("messages_sent", 0), inline=True)

    first_join = doc.get("first_join_ts")
    last_seen = doc.get("last_seen_ts")

    embed.add_field(
        name="First Join",
        value=(f"<t:{first_join // 1000}:R>" if isinstance(first_join, int) and first_join > 0 else "-"),
        inline=True,
    )

    embed.add_field(
        name="Last Seen",
        value=(f"<t:{last_seen // 1000}:R>" if isinstance(last_seen, int) and last_seen > 0 else "-"),
        inline=True,
    )

    embed.set_footer(text=f"UUID: {doc.get('uuid', 'unknown')}")

    await interaction.followup.send(embed=embed)


@tree.command(name="duels", description="Show duel statistics for a player")
@app_commands.describe(username="Player username")
async def duels(interaction: discord.Interaction, username: str = None):
    """
    STACK: Duels
    Shows duel statistics for a player.
    """

    if not username:
        await interaction.response.send_message("Usage: `/duels <username>`", ephemeral=True)
        return

    await interaction.response.defer()
    await ping_stats()

    doc = duels_db.find_one({"name": {"$regex": f"^{username}$", "$options": "i"}})

    if not doc:
        await interaction.followup.send("No duel data found for that player.")
        return

    embed = discord.Embed(
        title=f"Duel Stats - {doc.get('name', 'Unknown')}",
        color=discord.Color.blurple(),
        timestamp=datetime.now(timezone.utc),
    )

    wins = int(doc.get("wins", 0))
    losses = int(doc.get("losses", 0))
    total = int(doc.get("total_matches", wins + losses))

    win_rate = (wins / total * 100) if total > 0 else 0.0

    embed.add_field(name="Duels Record", value=f"**{wins}W / {losses}L**", inline=True)
    embed.add_field(name="Win Rate", value=f"**{win_rate:.1f}%**", inline=True)
    embed.add_field(name="Total Matches", value=f"**{total}**", inline=True)

    # FIX LATER WHEN API IS INTRODUCED FOR DUELS
    # last_match = doc.get("last_match_ts", 0)
    # embed.add_field(
    #     name="Last Match",
    #     value=f"<t:{last_match // 1000}:R>" if last_match <= 0 else "-",
    #     inline=True,
    # )

    rating = doc.get("rating") or {}

    rating = doc.get("rating") or {}

    if rating:
        rating_lines = [f"**{mode}**: `{value}`" for mode, value in sorted(rating.items())]
        embed.add_field(name="Ratings", value="\n".join(rating_lines), inline=False)

    embed.set_footer(text="Duels stats are synced periodically from the server.")

    await interaction.followup.send(embed=embed)

async def shutdown_server(manual=False):
    """
    STACK: Server control
    Shuts down the minecraft server.

    Args:
        manual: Whether the shutdown was manual or automatic (by polling).
    """
    channel = discord.utils.get(bot.get_all_channels(), name="minecraft-chat")
    if channel:
        if manual:
            pass
            # await channel.send(embed=embed_manual_stop())
        else:
            await channel.send(embed=embed_auto_shutdown())
        await stop_mc_server()
        await channel.send(embed=embed_stopped())
        await stop_vm()
        await channel.send(embed=embed_vm_stop())

@tree.command(name="help", description="Show all bot commands or details about one command/category")
@app_commands.describe(target="Command name or category")
async def help_cmd(interaction: discord.Interaction, target: str = None):
    """
    STACK: Help
    Shows this message
    """

    commands_list = tree.get_commands()

    stacks = {}
    command_map = {}

    for command in commands_list:
        doc = command.callback.__doc__ or ""
        lines = [line.strip() for line in doc.splitlines() if line.strip()]

        stack = "No Category"
        for line in lines:
            if line.startswith("STACK:"):
                stack = line.replace("STACK:", "").strip()
                break

        if stack not in stacks:
            stacks[stack] = []

        stacks[stack].append(command)
        command_map[command.name.lower()] = (command, stack, doc)

    embed = discord.Embed(
        title="Xymic — Command Help",
        color=discord.Color.blurple(),
        timestamp=datetime.now(timezone.utc),
    )

    if target:
        key = target.lower()

        if key in command_map:
            command, stack, doc = command_map[key]

            embed.title = f"/{command.name}"
            embed.add_field(name="Category", value=stack, inline=False)
            embed.add_field(name="Description", value=command.description or "No description", inline=False)

            doc_lines = [line for line in doc.splitlines() if not line.strip().startswith("STACK:")]

            if doc_lines:
                embed.add_field(
                    name="Documentation",
                    value="```" + "\n".join(doc_lines).strip() + "```",
                    inline=False,
                )

            embed.set_footer(text="Detailed command help")

            await interaction.response.send_message(embed=embed, ephemeral=False)
            return

        matched_stack = None
        for stack in stacks:
            if stack.lower() == key:
                matched_stack = stack
                break

        if matched_stack:
            embed.title = f"{matched_stack} Commands"

            value_lines = []
            for cmd in sorted(stacks[matched_stack], key=lambda c: c.name):
                value_lines.append(f"`/{cmd.name}`  {cmd.description}")

            embed.add_field(
                name=matched_stack,
                value="\n".join(value_lines),
                inline=False,
            )

            embed.set_footer(text=f"Category: {matched_stack}")

            await interaction.response.send_message(embed=embed, ephemeral=False)
            return

        await interaction.response.send_message(
            "Command or category not found.",
            ephemeral=True,
        )
        return

    for stack, cmds in sorted(stacks.items()):
        value_lines = []

        for cmd in sorted(cmds, key=lambda c: c.name):
            value_lines.append(f"`/{cmd.name}`  {cmd.description}")

        embed.add_field(
            name=stack,
            value="\n".join(value_lines),
            inline=False,
        )

    embed.add_field(
        name="‎",
        value=(
            "Type `/help <command>` for more info on a command.\n"
            "You can also type `/help <category>` for more info on a category."
        ),
        inline=False,
    )

    embed.set_footer(text="Slash command help system")

    await interaction.response.send_message(embed=embed, ephemeral=False)


bot.run(BOT_TOKEN)
