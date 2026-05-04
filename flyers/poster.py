# poster.py
import asyncio
import logging

import nextcord
from nextcord import File, AllowedMentions

from flyers.renderer import week_label

logger = logging.getLogger("discord_bot")


def build_discord_caption(
    week: int | None,
    t1: str,
    t2: str,
    streamer: str,
    link: str | None,
    include_everyone: bool = True
) -> str:
    if link:
        link_line = f"Live: {link}"
        logger.info(f"Caption will contain link: {repr(link_line)}")
        logger.info(f"[CAPTION LINK VALUE] -> {repr(link)}")
    else:
        link_line = "Live: (link pending)"

    header = week_label(week).replace("WURD • ", "**") + "**"

    prefix = "@everyone\n" if include_everyone else ""

    return (
        f"{prefix}"
        f"{header}\n"
        f"{t1} vs {t2}\n"
        f"Streamer: {streamer}\n"
        f"{link_line}"
    )


async def post_flyer_with_everyone(
    thread,
    flyer_path: str,
    week: int | None,
    t1: str,
    t2: str,
    streamer: str,
    link: str | None
):
    perms = thread.permissions_for(thread.guild.me)

    if not perms.mention_everyone:
        msg = await thread.send(
            content=(
                "**Heads-up:** I don’t have `Mention Everyone` permission here.\n\n"
                + build_discord_caption(
                    week,
                    t1,
                    t2,
                    streamer,
                    link,
                    include_everyone=False
                )
            ),
            file=File(flyer_path),
            allowed_mentions=AllowedMentions.none(),
            suppress_embeds=True
        )

        logger.info(f"[FINAL LINK USED IN CAPTION] -> {repr(link)}")
        return msg

    msg = await thread.send(
        content=build_discord_caption(
            week,
            t1,
            t2,
            streamer,
            link,
            include_everyone=True
        ),
        file=File(flyer_path),
        allowed_mentions=AllowedMentions(everyone=True),
        suppress_embeds=True
    )

    logger.info(f"[FINAL LINK USED IN CAPTION] -> {repr(link)}")
    return msg


async def watch_first_link_and_edit(
    bot,
    thread,
    author_id: int,
    posted_msg_id: int,
    week: int,
    t1: str,
    t2: str,
    streamer: str,
    find_stream_link_func
):
    def _check(m: nextcord.Message):
        return (
            m.channel.id == thread.id
            and m.author.id == author_id
            and find_stream_link_func(m.content) is not None
        )

    try:
        m = await bot.wait_for("message", timeout=3600, check=_check)
        link = find_stream_link_func(m.content)

        if not link:
            return

        msg = await thread.fetch_message(posted_msg_id)

        await msg.edit(
            content=build_discord_caption(
                week,
                t1,
                t2,
                streamer,
                link,
                include_everyone=False
            ),
            allowed_mentions=AllowedMentions.none(),
            suppress_embeds=True
        )

    except asyncio.TimeoutError:
        pass
    except Exception as e:
        logger.warning(f"late-link watcher: {e}")
