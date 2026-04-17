# THE BOT BRAIN - this is what the bot calls

from openai import OpenAI
from ai_bot.ai_prompts import PERSONALITY

client = OpenAI()


# =============================
# MAIN AI REPLY
# =============================
def generate_ai_reply(user_message, context):
    prompt = f"""
{PERSONALITY}

League Info:
- Week: {context.get('week')}
- Advance: {context.get('advance')}

User said:
"{user_message}"

Reply naturally like you're in the chat.
"""

    try:
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
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
    prompt = f"""
{PERSONALITY}

League Info:
- Week: {context.get('week')}
- Advance: {context.get('advance')}

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
            messages=[{"role": "user", "content": prompt}],
            max_tokens=50,
            temperature=0.9
        )

        return response.choices[0].message.content.strip()

    except Exception:
        return "Who’s on right now? 👀"
