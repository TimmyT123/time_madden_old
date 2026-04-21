# THE BOT BRAIN - this is what the bot calls

from openai import OpenAI
from ai_bot.ai_prompts import PERSONALITY

from datetime import datetime
import pytz

client = OpenAI()

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

    try:
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {
                    "role": "system",
                    "content": f"{PERSONALITY}\n{time_context}\nReply naturally like you're in the chat."
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

    prompt = f"""
    {PERSONALITY}

    {time_context}

    Say ONE short message to keep the chat active.

    Ideas:
    - Ask if anyone is playing
    - Mention advance
    - Light trash talk

    Keep it to ONE sentence.
    """

    try:
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {
                    "role": "system",
                    "content": f"{PERSONALITY}\n{time_context}"
                },
                {
                    "role": "user",
                    "content": "Say ONE short message to keep the chat active."
                }
            ],
            max_tokens=50,
            temperature=0.9
        )

        return response.choices[0].message.content.strip()

    except Exception:
        return "Who’s on right now? 👀"
