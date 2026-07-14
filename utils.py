from dotenv import load_dotenv
import yaml
import discord

from mcstatus import JavaServer
import asyncio
import os

from google.cloud import compute_v1
from google.oauth2 import service_account
import json
import base64
import aiohttp

load_dotenv()
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(BASE_DIR, "config.yaml")

with open(CONFIG_PATH, "r") as f:
    config = yaml.safe_load(f)

ADMIN_ID = config["bot"]["ADMIN_ID"].split(",")

SERVER_IP = config["crafty"]["SERVER_IP"]
SMP_SERVER_ID = config["crafty"]["SMP_SERVER_ID"]
PROXY_SERVER_ID = config["crafty"]["PROXY_SERVER_ID"]
LOBBY_SERVER_ID = config["crafty"]["LOBBY_SERVER_ID"]
POLL_INTERVAL = config["crafty"]["POLL_INTERVAL"]
POLL_TIMEOUT = config["crafty"]["POLL_TIMEOUT"]

SERVER_IDS = [SMP_SERVER_ID, PROXY_SERVER_ID, LOBBY_SERVER_ID]

PROJECT_ID = config["gcp"]["PROJECT_ID"]
ZONE = config["gcp"]["ZONE"]
INSTANCE_NAME = config["gcp"]["INSTANCE_NAME"]

GOOGLE_SERVICE_ACCOUNT_BASE64 = os.getenv("GOOGLE_SERVICE_ACCOUNT_BASE64")
CRAFTY_TOKEN = os.getenv("CRAFTY_TOKEN")


key_json = json.loads(base64.b64decode(GOOGLE_SERVICE_ACCOUNT_BASE64))
credentials = service_account.Credentials.from_service_account_info(key_json)
instances_client = compute_v1.InstancesClient(credentials=credentials)


def is_admin(interaction: discord.Interaction):
    """
    STACK: Discord permissions
    Check if the user running the command is a verified administrator
    in the discord server.

    Args:
        ctx: Message object
    """
    for role in interaction.user.roles:
        if str(role.id) in ADMIN_ID:
            return True
    return False


async def get_player_count():
    """
    Returns the total online players across the SMP and Lobby servers.
    """

    url = "https://crafty.pesumc.top/api/v2/servers/status"
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, ssl=False) as resp:
                if resp.status != 200:
                    print(f"[SERVER CONTROL] Failed to fetch status: {resp.status}")
                    return None

                data = await resp.json()

                total_players = 0

                for server in data.get("data", []):
                    if server["id"] in (SMP_SERVER_ID, LOBBY_SERVER_ID):
                        total_players += server.get("online", 0)

                return total_players

    except Exception as e:
        print(f"[SERVER CONTROL] Error fetching player count: {e}")
        return None


async def start_vm():
    """
    STACK: VM control
    Starts the virtual machine on Google cloud.
    """
    print(f"[VM CONTROL] Starting {INSTANCE_NAME}")
    operation = instances_client.start(
        project=PROJECT_ID, zone=ZONE, instance=INSTANCE_NAME
    )
    operation.result()
    print("[VM CONTROL] VM started")


async def stop_vm():
    """
    STACK: VM control
    Stops the virtual machine on Google cloud.
    """
    print(f"[VM CONTROL] Stopping {INSTANCE_NAME}...")

    def send_command():
        operation = instances_client.stop(
            project=PROJECT_ID, zone=ZONE, instance=INSTANCE_NAME
        )
        operation.result()

    result = await asyncio.to_thread(send_command)
    print("[VM CONTROL] VM stopped.")


async def get_vm_status():
    """
    STACK: VM control
    Fetches the status of the virtual machine on Google cloud.
    """
    instance = instances_client.get(
        project=PROJECT_ID, zone=ZONE, instance=INSTANCE_NAME
    )
    return instance.status



async def stop_mc_server():
    """
    Stops all Minecraft servers and waits until they are fully stopped.
    """

    async with aiohttp.ClientSession() as session:
        # Send stop requests in parallel
        await asyncio.gather(
            *(stop_single_server(session, server_id) for server_id in SERVER_IDS)
        )

        print("[SERVER CONTROL] Stop commands sent. Waiting for shutdown...")

        await wait_until_servers_stopped(session)

    print("[SERVER CONTROL] All servers stopped.")


async def stop_single_server(session: aiohttp.ClientSession, server_id: str):
    headers = {
        "Authorization": CRAFTY_TOKEN,
        "Content-Type": "application/json",
    }

    url = f"https://crafty.pesumc.top/api/v2/servers/{server_id}/action/stop_server"

    async with session.post(url, headers=headers, ssl=False) as resp:
        text = await resp.text()

        print(f"[{server_id}] {resp.status}: {text}")

        if resp.status == 400:
            print(f"[{server_id}] Already stopped")
            return

        if resp.status != 200:
            raise Exception(f"Failed to stop {server_id}: {resp.status}")


async def wait_until_servers_stopped(session: aiohttp.ClientSession):
    url = "https://crafty.pesumc.top/api/v2/servers/status"

    deadline = asyncio.get_running_loop().time() + POLL_TIMEOUT

    while True:
        if asyncio.get_running_loop().time() > deadline:
            raise TimeoutError(
                "Timed out waiting for Crafty servers to stop."
            )

        async with session.get(url) as resp:
            if resp.status != 200:
                raise Exception(
                    f"Failed to fetch Crafty status ({resp.status})"
                )
            data = await resp.json()

        running = {}

        for server in data["data"]:
            if server["id"] in SERVER_IDS:
                running[server["id"]] = server["running"]

        print(
            "[SERVER CONTROL]",
            {
                "SMP": running.get(SMP_SERVER_ID),
                "PROXY": running.get(PROXY_SERVER_ID),
                "LOBBY": running.get(LOBBY_SERVER_ID),
            },
        )

        if all(running.get(server_id) is False for server_id in SERVER_IDS):
            return

        await asyncio.sleep(POLL_INTERVAL)

def format_duration(ms):
    """
    STACK: Formatting / Stats
    Formats the duration specified in ms to HH:MM:SS format.

    Args:
        ms: Time in milliseconds.
    Returns:
        str: HHh MMm SSs
    """
    seconds = ms // 1000
    h = seconds // 3600
    m = (seconds % 3600) // 60
    s = seconds % 60
    return f"{h:02d}h {m:02d}m {s:02d}s"


def gb(v):
    """
    STACK: Formatting / Stats
    Convert bytes to gigabytes

    Args:
        v: Value to convert

    Returns:
        str: <value in gb> GB
    """
    return f"{v / (1024**3):.2f} GB"


async def ping_stats(player_uuid: str | None = None):
    STATS_TOKEN = os.getenv("STATS_TOKEN")
    STATS_ENDPOINT = "http://" + SERVER_IP + "/mc/stats"
    headers = {"x-stats-token": STATS_TOKEN}

    params = {}
    if player_uuid:
        params["player"] = player_uuid

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                STATS_ENDPOINT,
                headers=headers,
                params=params,
                timeout=aiohttp.ClientTimeout(total=2),
            ) as resp:
                await resp.text()
    except (aiohttp.ClientConnectorError, asyncio.TimeoutError):
        return False
    except Exception as e:
        print(f"[STATS] Ping failed: {type(e).__name__}")
        return False
