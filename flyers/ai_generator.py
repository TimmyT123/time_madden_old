import os
import io
import base64
import logging

from PIL import Image
from openai import OpenAI

logger = logging.getLogger("discord_bot")

_openai_client = OpenAI()

def generate_chatgpt_flyer_image(prompt: str, out_path: str) -> bool:
    try:
        result = _openai_client.images.generate(
            model="gpt-image-1",
            prompt=prompt,
            size="1024x1536",
            n=1
        )

        image_base64 = result.data[0].b64_json
        image_bytes = base64.b64decode(image_base64)

        os.makedirs(os.path.dirname(out_path), exist_ok=True)
        with Image.open(io.BytesIO(image_bytes)) as img:
            img.save(out_path, format="PNG")

        return os.path.isfile(out_path)

    except Exception as e:
        logger.warning(f"ChatGPT image generation failed: {e}")
        return False

def build_flyer_caption(data: dict) -> str:
    h = data["home"]
    a = data["away"]

    away_players = ", ".join(p["name"] for p in a.get("top_players", []))
    home_players = ", ".join(p["name"] for p in h.get("top_players", []))

    return (
        f"🏈 **WURD Week {data['week']}**\n"
        f"**{a['name']} ({a['record']}, OVR {a['ovr']})**\n"
        f"vs\n"
        f"**{h['name']} ({h['record']}, OVR {h['ovr']})**\n\n"
        f"⭐ **Players to Watch**\n"
        f"{a['name']}: {away_players}\n"
        f"{h['name']}: {home_players}"
    )

def build_flyer_image_prompt(data: dict) -> str:
    h = data["home"]
    a = data["away"]

    return f"""
Create a cinematic Madden-style NFL game flyer.

IMPORTANT:
- Display the matchup text EXACTLY as written
- Do NOT swap team order
- First team is on the LEFT, second team on the RIGHT

League branding:
Include the official league logo at the top or center:
"WURD – Who’s UR Daddy"
Modern metallic badge, professional esports style.

Matchup (exact order):
{h['name']} ({h['record']}, OVR {h['ovr']})
vs
{a['name']} ({a['record']}, OVR {a['ovr']})

Star players:
{h['name']}: {", ".join(p.get('name', 'Unknown') + " (" + p.get('pos', '?') + ")" for p in h.get('top_players', []))}
{a['name']}: {", ".join(p.get('name', 'Unknown') + " (" + p.get('pos', '?') + ")" for p in a.get('top_players', []))}

Style requirements:
- Night stadium
- Dramatic lighting
- Team colors emphasized
- Realistic football players
- Madden-style broadcast look
- High contrast
- Include league logo prominently
"""
