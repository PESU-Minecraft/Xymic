from __future__ import annotations

import os
from typing import TYPE_CHECKING

import discord

if TYPE_CHECKING:
    from bot import DiscordBot


class Config:
    ROLES = {
        "FUNCTIONAL": {
            "ADMIN": 1456845605476368598,
            "VERIFIED": 1406947977355989124,
        },
        "BRANCH": {
            "CSE": 1499625894338367619,
            "CSE(AI-ML)": 1499625930576888020,
            "ECE": 1499625978765512824,
            "EEE": 1499627646605070447,
            "ME": 1499626017889980466,
            "BT": 1499626064635494462,
            "OTHER": 1499626408572485834,
        },
        "YEAR": {
            "2027": 1434177495862738984,
            "2028": 1434177438434197648,
            "2029": 1434177170850447541,
            "2030": 1499625627454673036,
            "2026": 1434179161332646079,
            "2025": 1434179202365657250,
            "2024": 1434179249295720559,
            "2023": 1434179302181572688,
        },
        "CAMPUS": {
            "RR": 1419668312740069447,
            "EC": 1419668358483280023,
        },
    }

    CHANNELS = {
        "MOD_LOGS": 1515560683230462084,
    }

    def __init__(self, bot: DiscordBot) -> None:
        self.bot = bot
        self.guild_id = int(os.getenv("GUILD_ID", "0"))

    @property
    def guild(self) -> discord.Guild:
        guild = self.bot.get_guild(self.guild_id)
        if guild is None:
            raise ValueError(f"Guild {self.guild_id} not found")
        return guild

    def get_role(self, category: str, name: str) -> discord.Role:
        role_id = self.ROLES.get(category, {}).get(name)
        if not role_id:
            raise ValueError(f"Role '{name}' in '{category}' has no ID configured")
        role = self.guild.get_role(role_id)
        if role is None:
            raise ValueError(f"Role ID {role_id} not found in guild")
        return role

    def get_channel(self, name: str) -> discord.TextChannel:
        channel_id = self.CHANNELS.get(name)
        if not channel_id:
            raise ValueError(f"Channel '{name}' has no ID configured")
        channel = self.guild.get_channel(channel_id)
        if not isinstance(channel, discord.TextChannel):
            raise ValueError(f"Channel ID {channel_id} not found or not a text channel")
        return channel

    @property
    def verified_role(self) -> discord.Role:
        return self.get_role("FUNCTIONAL", "VERIFIED")

    @property
    def admin_role(self) -> discord.Role:
        return self.get_role("FUNCTIONAL", "ADMIN")

    @property
    def mod_logs_channel(self) -> discord.TextChannel:
        return self.get_channel("MOD_LOGS")

    def is_admin(self, member: discord.Member) -> bool:
        try:
            return self.admin_role in member.roles
        except ValueError:
            return False
