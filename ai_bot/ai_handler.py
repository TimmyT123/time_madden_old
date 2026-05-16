# THE BOT BRAIN - this is what the bot calls

from openai import OpenAI
from ai_bot.ai_prompts import PERSONALITY

from datetime import datetime
import pytz

client = OpenAI()

def get_temp_personality_override():
    az = pytz.timezone("US/Arizona")
    now = datetime.now(az)

    # Temporary nice mode for today only
    if now.date().isoformat() == "2026-05-16":
        return """
TEMPORARY NICE MODE:
- For today only, do NOT insult, roast, clown, or trash talk the person chatting with you.
- Be friendly, positive, and encouraging.
- Mention that the person chatting with you is great, appreciated, or doing a good job.
- Keep the WURD Bot style casual and funny, but make it wholesome instead of savage.
- No mean jokes today.
"""

    return ""

def get_time_context(context):
    az = pytz.timezone("US/Arizona")
    now = datetime.now(az)

    today_str = now.strftime("%A, %b %d")

    return f"""
Current Date (DO NOT GUESS):
- Today is {today_str} (Arizona Time)

League Info:
- Week: {context.get('week')} (this is NOT the day)
- Advance: {context.get('advance')}
"""


# =============================
# MAIN AI REPLY
# =============================
def generate_ai_reply(user_message, context):
    time_context = get_time_context(context)
    temp_override = get_temp_personality_override()

    try:
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {
                    "role": "system",
                    "content": f"{PERSONALITY}\n{time_context}\n{temp_override}\nReply naturally like you're in the chat."
                },
                {
                    "role": "user",
                    "content": user_message
                }
            ],
            max_tokens=80,
            temperature=0.8
        )

        return response.choices[0].message.content.strip()

    except Exception:
        return "I'm here… just lagging a bit 😄"


# =============================
# PERSONALITY MESSAGE
# =============================
def generate_personality_message(context):
    time_context = get_time_context(context)
    temp_override = get_temp_personality_override()

    try:
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {
                    "role": "system",
                    "content": f"{PERSONALITY}\n{time_context}\n{temp_override}"
                },
                {
                    "role": "user",
                    "content": "Say ONE short friendly message to keep the chat active. Compliment the person chatting if natural."
                }
            ],
            max_tokens=50,
            temperature=0.9
        )

        return response.choices[0].message.content.strip()

    except Exception:
        return "Who’s on right now? 👀"
