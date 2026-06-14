from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone
from typing import Any

import discord
import httpx
from discord import app_commands
from discord.ext import commands

from auth.config import Config
from auth.general import build_unknown_error_embed

# this shit not gonna change so, no need .env
_PESUAUTH_URL = "https://pesu-auth.onrender.com/authenticate"

_SRN_RE = re.compile(r"^PES\dUG(\d{2})", re.IGNORECASE)  # Sem-1 python U3 ig :HA:

_BRANCH_MAP: dict[str, str] = {
    "computer science and engineering": "CSE",
    "computer science and engineering (artificial intelligence and machine learning)": "CSE (AI&ML)",
    "cse (ai&ml)": "CSE (AI&ML)",
    "electronics and communication engineering": "ECE",
    "electrical and electronics engineering": "EEE",
    "mechanical engineering": "ME",
    "biotechnology": "BT",
}


def _hash_srn(srn: str) -> str:
    return hashlib.sha256(srn.strip().upper().encode()).hexdigest()


def _year_from_srn(srn: str) -> str | None:
    m = _SRN_RE.match(srn.strip())
    return str(2000 + int(m.group(1))) if m else None


def _normalise_branch(raw: str) -> str:
    return _BRANCH_MAP.get(raw.strip().lower(), "OTHER")


async def _call_pesuauth(username: str, password: str) -> dict | None:
    payload = {
        "username": username,
        "password": password,
        "profile": True,  # this is needed to get the below fields
        "fields": ["srn", "branch", "campus", "campusCode"],
    }
    try:
        async with httpx.AsyncClient(timeout=60) as http:
            resp = await http.post(_PESUAUTH_URL, json=payload)
            return resp.json() if resp.status_code == 200 else None
    except (httpx.HTTPError, httpx.TimeoutException):
        return None


class SlashVerify(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.client: Any = bot
        self.config = Config(bot)

    @app_commands.command(
        name="verify",
        description="Verify yourself using your SRN and PESU Academy password.",
    )
    @app_commands.describe(
        srn="SRN/PRN (e.g. PESxUGxxXXX or PESx20xxXXX)",
        password="Your PESU Academy portal password",
    )
    async def verify(
        self, interaction: discord.Interaction, srn: str, password: str
    ) -> None:
        await interaction.response.defer(ephemeral=True)

        if not self.client.verify_enabled:
            await interaction.followup.send(
                "Verification is currently disabled. Try again later!", ephemeral=True
            )
            return

        if not isinstance(interaction.user, discord.Member):
            await interaction.followup.send(
                "What you wanna achieve with this brother/sister? (you must be a member of the PESU-MC to verify)",
                ephemeral=True,
            )
            return

        member = interaction.user
        srn_hash = _hash_srn(srn)

        existing = await self.client.link_collection.find_one(
            {"userId": str(member.id), "linkedAt": {"$exists": True}}
        )
        if existing:
            await interaction.followup.send(
                "Your account is already verified gng, chillout!",
                ephemeral=True,
            )
            return

        srn_conflict = await self.client.link_collection.find_one(
            {
                "srnHash": srn_hash,
                "linkedAt": {"$exists": True},
                "userId": {"$ne": str(member.id)},
            }
        )
        if srn_conflict:
            try:
                log = discord.Embed(
                    title="Duplicate SRN attempt",
                    color=discord.Color.red(),
                    timestamp=datetime.now(tz=timezone.utc),
                )
                log.add_field(
                    name="Attempting user", value=f"{member.mention} ({member.id})"
                )
                log.add_field(
                    name="Already linked to",
                    value=f"<@{srn_conflict['userId']}> ({srn_conflict['userId']})",
                )
                await self.config.mod_logs_channel.send(embed=log)
            except Exception:
                pass
            await interaction.followup.send(
                "Big brain move uh? (This SRN is already linked to another account. Contact an admin if this is an error)",
                ephemeral=True,
            )
            return

        data = await _call_pesuauth(srn, password)
        if not data or not data.get("status"):
            msg = (
                data.get("message", "Authentication failed.")
                if data
                else "Could not reach the PESUAuth API."
            )
            await interaction.followup.send(
                f"Verification failed: {msg}\n\nCheck your SRN and password and try again.",
                ephemeral=True,
            )
            return

        profile = data.get("profile", {})
        verified_srn = profile.get("srn", srn.strip().upper())
        raw_branch = profile.get("branch", "")
        campus_api = profile.get("campus", "RR").strip().upper()
        year = _year_from_srn(verified_srn)
        branch_key = _normalise_branch(raw_branch)

        if year:
            grad_year = str(int(year) + 4)
            display_year = f"Batch of {grad_year}"
            
        display_campus = "RRC" if campus_api == "RR" else "ECC" if campus_api == "EC" else campus_api
        # i like to call it RR'C' and EC'C' so ....had to do this drama ^^ 
        roles_to_add: list[discord.Role] = []
        skipped: list[str] = []
        for role_type, key, label in [
            ("YEAR", grad_year, f"Year ({display_year})"),
            ("BRANCH", branch_key, f"Branch ({branch_key})"),
            ("CAMPUS", campus_api, f"Campus ({display_campus})"),
        ]:
            if key:
                try:
                    roles_to_add.append(self.config.get_role(role_type, key))
                except ValueError:
                    skipped.append(label)
            else:
                skipped.append(label)
        try:
            roles_to_add.append(self.config.verified_role)
        except ValueError:
            skipped.append("Verified")

        if roles_to_add:
            try:
                await member.add_roles(*roles_to_add, reason="Verified via /verify")
            except discord.Forbidden:
                await interaction.followup.send(
                    "Verified, but I lack permission to assign roles. Contact an admin!",
                    ephemeral=True,
                )
                return

        now = datetime.now(tz=timezone.utc)
        await self.client.link_collection.update_one(
            {"userId": str(member.id)},
            {
                "$set": {
                    "userId": str(member.id),
                    "srnHash": srn_hash,
                    "branch": branch_key,
                    "year": display_year,
                    "campus": display_campus,
                    "linkedAt": now,
                }
            },
            upsert=True,
        )

        try:
            log = discord.Embed(
                title="Member verified", color=discord.Color.green(), timestamp=now
            )
            log.add_field(
                name="User", value=f"{member.mention} ({member.id})", inline=False
            )
            log.add_field(name="Branch", value=branch_key, inline=True)
            log.add_field(name="Year", value=display_year, inline=True)
            log.add_field(name="Campus", value=display_campus, inline=True)
            if skipped:
                log.add_field(
                    name="Roles not found", value=", ".join(skipped), inline=False
                )
            await self.config.mod_logs_channel.send(embed=log)
        except Exception:
            pass

        role_names = ", ".join(r.name for r in roles_to_add) if roles_to_add else "none"
        reply = f"Verified. Roles assigned: {role_names}."
        if skipped:
            reply += f"\nNote: some roles are not configured and were skipped: {', '.join(skipped)}."
        await interaction.followup.send(reply, ephemeral=True)

    @verify.error
    async def verify_error(
        self, interaction: discord.Interaction, error: app_commands.AppCommandError
    ) -> None:
        orig = (
            error.original
            if isinstance(error, app_commands.CommandInvokeError)
            else error
        )
        await interaction.followup.send(
            embed=build_unknown_error_embed(orig), ephemeral=True
        )

    @app_commands.command(
        name="deverify", description="Remove a user's verification. Admin only!"
    )
    @app_commands.describe(user="The member to deverify")
    async def deverify(
        self, interaction: discord.Interaction, user: discord.Member
    ) -> None:
        await interaction.response.defer(ephemeral=True)

        if not isinstance(interaction.user, discord.Member):
            await interaction.followup.send(
                "This command can only be used inside the server.", ephemeral=True
            )
            return
        if not self.config.is_admin(interaction.user):
            await interaction.followup.send(
                "HAHAHA noob, YOU can't do that!", ephemeral=False
            )
            return

        result = await self.client.link_collection.delete_one({"userId": str(user.id)})
        if result.deleted_count == 0:
            await interaction.followup.send(
                f"{user.mention} was not verified.", ephemeral=True
            )
            return

        verif_role_ids = set()
        for cat_name in ["BRANCH", "YEAR", "CAMPUS"]:
            for role_id in self.config.ROLES.get(cat_name, {}).values():
                verif_role_ids.add(role_id)
        
        verified_id = self.config.ROLES.get("FUNCTIONAL", {}).get("VERIFIED")
        if verified_id:
            verif_role_ids.add(verified_id)

        roles_to_remove = [r for r in user.roles if r.id in verif_role_ids]

        if roles_to_remove:
            try:
                await user.remove_roles(
                    *roles_to_remove, reason=f"Deverified by {interaction.user}"
                )
            except discord.Forbidden:
                await interaction.followup.send(
                    "Record removed from database, but could not strip roles ..check bot permissions.",
                    ephemeral=True,
                )
                return

        try:
            log = discord.Embed(
                title="Member de-verified",
                color=discord.Color.orange(),
                timestamp=datetime.now(tz=timezone.utc),
            )
            log.add_field(name="User", value=f"{user.mention} ({user.id})")
            log.add_field(name="By", value=f"{interaction.user.mention}")
            await self.config.mod_logs_channel.send(embed=log)
        except Exception:
            pass

        await interaction.followup.send(
            f"De-verified {user.mention} and removed their roles.", ephemeral=True
        )

    @deverify.error
    async def deverify_error(
        self, interaction: discord.Interaction, error: app_commands.AppCommandError
    ) -> None:
        orig = (
            error.original
            if isinstance(error, app_commands.CommandInvokeError)
            else error
        )
        await interaction.followup.send(
            embed=build_unknown_error_embed(orig), ephemeral=True
        )

    @app_commands.command(
        name="info", description="Show verification details for a user. Admin only!"
    )
    @app_commands.describe(user="The member to look up")
    async def info(
        self, interaction: discord.Interaction, user: discord.Member
    ) -> None:
        await interaction.response.defer(ephemeral=True)

        if not isinstance(interaction.user, discord.Member):
            await interaction.followup.send(
                "This command can only be used inside the server.", ephemeral=True
            )
            return
        if not self.config.is_admin(interaction.user):
            await interaction.followup.send(
                "HAHAHA noob, YOU can't do that!", ephemeral=False
            )
            return

        record = await self.client.link_collection.find_one({"userId": str(user.id)})
        if not record:
            await interaction.followup.send(
                f"{user.mention} has not been verified.", ephemeral=True
            )
            return

        embed = discord.Embed(
            title=f"Info: {user.display_name}",
            color=discord.Color.blurple(),
            timestamp=discord.utils.utcnow(),
        )
        embed.add_field(name="Branch", value=record.get("branch", "N/A"), inline=True)
        embed.add_field(name="Year", value=record.get("year", "N/A"), inline=True)
        embed.add_field(name="Campus", value=record.get("campus", "N/A"), inline=True)
        linked_at = record.get("linkedAt")
        if linked_at:
            embed.add_field(
                name="Verified at",
                value=f"<t:{int(linked_at.timestamp())}:f>",
                inline=False,
            )

        embed.set_footer(text="Xymic")
        await interaction.followup.send(embed=embed, ephemeral=True)

    @info.error
    async def info_error(
        self, interaction: discord.Interaction, error: app_commands.AppCommandError
    ) -> None:
        orig = (
            error.original
            if isinstance(error, app_commands.CommandInvokeError)
            else error
        )
        await interaction.followup.send(
            embed=build_unknown_error_embed(orig), ephemeral=True
        )

    @app_commands.command(
        name="auth", description="Enable or disable the /verify command. Admin only!"
    )
    @app_commands.describe(state="on to enable, off to disable")
    @app_commands.choices(
        state=[
            app_commands.Choice(name="on", value="on"),
            app_commands.Choice(name="off", value="off"),
        ]
    )
    async def auth(
        self, interaction: discord.Interaction, state: app_commands.Choice[str]
    ) -> None:
        await interaction.response.defer(ephemeral=True)

        if not isinstance(interaction.user, discord.Member):
            await interaction.followup.send(
                "This command can only be used inside the server.", ephemeral=True
            )
            return
        if not self.config.is_admin(interaction.user):
            await interaction.followup.send(
                "HAHAHA noob, YOU can't do that!", ephemeral=False
            )
            return

        enabling = state.value == "on"
        if self.client.verify_enabled == enabling:
            status = "already enabled" if enabling else "already disabled"
            await interaction.followup.send(
                f"Verification is {status}.", ephemeral=True
            )
            return

        self.client.verify_enabled = enabling
        status = "enabled" if enabling else "disabled"

        try:
            log = discord.Embed(
                title=f"Verification {status}",
                color=discord.Color.green() if enabling else discord.Color.red(),
                timestamp=datetime.now(tz=timezone.utc),
            )
            log.add_field(
                name="Changed by",
                value=f"{interaction.user.mention} ({interaction.user.id})",
            )
            await self.config.mod_logs_channel.send(embed=log)
        except Exception:
            pass

        await interaction.followup.send(
            f"Verification has been {status}.", ephemeral=True
        )

    @auth.error
    async def auth_error(
        self, interaction: discord.Interaction, error: app_commands.AppCommandError
    ) -> None:
        orig = (
            error.original
            if isinstance(error, app_commands.CommandInvokeError)
            else error
        )
        await interaction.followup.send(
            embed=build_unknown_error_embed(orig), ephemeral=True
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(SlashVerify(bot))
