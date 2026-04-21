import asyncio
from datetime import datetime
import pytz
import json

from ai_bot.ai_handler import generate_personality_message
from ai_bot.ai_memory import can_send_personality_message

# =============================
# TIME WINDOW (1PM–7PM AZ)
# =============================
def is_active_hours():
    az = pytz.timezone("US/Arizona")
    now = datetime.now(az)
    return 13 <= now.hour < 19


def load_ai_advance_info(logger, ADVANCE_INFO_FILE):
    try:
        with open(ADVANCE_INFO_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)

        return {
            "week": data.get("week", "Unknown"),
            "advance": data.get("advance_display", "Unknown")
        }

    except Exception as e:
        logger.warning(f"load_ai_advance_info failed: {e}")
        return {
            "week": "Unknown",
            "advance": "Unknown"
        }


# =============================
# PERSONALITY LOOP
# =============================
async def lobby_personality_loop(bot, logger, ADVANCE_INFO_FILE, get_lobby_talk_channel):
    await bot.wait_until_ready()

    while not bot.is_closed():
        try:
            logger.info(f"[AI BOT] ActiveHours={is_active_hours()} | CanSend={can_send_personality_message()}")

            if is_active_hours():
                logger.info("[AI BOT] Within active hours")

                if can_send_personality_message():
                    logger.info("[AI BOT] Sending personality message")

                    for guild in bot.guilds:
                        channel = get_lobby_talk_channel(guild)

                        if not channel:
                            logger.info(f"[AI BOT] lobby-talk not found in {guild.name}")
                            continue

                        context = load_ai_advance_info(logger, ADVANCE_INFO_FILE)
                        msg = generate_personality_message(context)

                        if msg and msg.strip():
                            await channel.send(msg)
                            logger.info(f"[AI BOT] Sent message in #{channel.name}")

                            await asyncio.sleep(1.5)

                else:
                    logger.info("[AI BOT] Skipped (cooldown not met)")

            else:
                logger.info("[AI BOT] Outside active hours")

            await asyncio.sleep(300)  # 5 minutes

        except Exception as e:
            logger.warning(f"lobby_personality_loop error: {e}")
            await asyncio.sleep(60)
