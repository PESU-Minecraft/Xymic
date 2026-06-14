from datetime import datetime

import discord


def build_unknown_error_embed(error: Exception) -> discord.Embed:
    return (
        discord.Embed(
            title="Unexpected Error",
            description="Something went wrong while processing the command.",
            color=discord.Color.red(),
            timestamp=datetime.now(),
        )
        .add_field(name="Error Type", value=type(error).__name__, inline=True)
        .add_field(
            name="Details",
            value=str(error)[:1000] or "No details available.",
            inline=False,
        )
        .set_footer(text="Xymic")
    )
