import os
import json
import logging
from PIL import Image, ImageDraw, ImageFont
import qrcode

from flyers.ai_generator import generate_chatgpt_flyer_image

logger = logging.getLogger("discord_bot")

LOGOS_DIR = os.getenv("LOGOS_DIR", "./static/logos")
FLYER_OUT_DIR = os.getenv("FLYER_OUT_DIR", "./static/flyers")
WURD_LOGO_PATH = "./static/branding/wurd_logo.png"

TEAM_COLORS = {
    "CARDINALS":   ("#97233F", "#000000"),
    "FALCONS":     ("#A71930", "#000000"),
    "RAVENS":      ("#241773", "#9E7C0C"),
    "BILLS":       ("#00338D", "#C60C30"),
    "PANTHERS":    ("#0085CA", "#BFC0BF"),
    "BEARS":       ("#0B162A", "#C83803"),
    "BENGALS":     ("#FB4F14", "#000000"),
    "BROWNS":      ("#311D00", "#FF3C00"),
    "COWBOYS":     ("#041E42", "#7F9695"),
    "BRONCOS":     ("#002244", "#FB4F14"),
    "LIONS":       ("#0076B6", "#B0B7BC"),
    "PACKERS":     ("#203731", "#FFB612"),
    "TEXANS":      ("#03202F", "#A71930"),
    "COLTS":       ("#003A70", "#A2AAAD"),
    "JAGUARS":     ("#006778", "#101820"),
    "CHIEFS":      ("#E31837", "#FFB81C"),
    "RAIDERS":     ("#000000", "#A5ACAF"),
    "CHARGERS":    ("#0080C6", "#FFC20E"),
    "RAMS":        ("#003594", "#FFA300"),
    "DOLPHINS":    ("#008E97", "#F26A24"),
    "VIKINGS":     ("#4F2683", "#FFC62F"),
    "PATRIOTS":    ("#002244", "#C60C30"),
    "SAINTS":      ("#D3BC8D", "#101820"),
    "GIANTS":      ("#0B2265", "#A71930"),
    "JETS":        ("#125740", "#FFFFFF"),
    "EAGLES":      ("#004C54", "#A5ACAF"),
    "STEELERS":    ("#101820", "#FFB612"),
    "49ERS":       ("#AA0000", "#B3995D"),
    "SEAHAWKS":    ("#002244", "#69BE28"),
    "BUCCANEERS":  ("#D50A0A", "#34302B"),
    "TITANS":      ("#0C2340", "#4B92DB"),
    "COMMANDERS":  ("#5A1414", "#FFB612"),
}

# Flyer generator (logos inside badges)
def _font(size: int, bold=False):
    try:
        path = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
        return ImageFont.truetype(path, size=size)
    except Exception:
        return ImageFont.load_default()

FONT_HDR = _font(86, bold=True)
FONT_SUB = _font(48, bold=True)
FONT_HEADER = _font(52, bold=True)
FONT_BODY = _font(32)

_PRE_LABELS = {-3: "PRE 1", -2: "PRE 2", -1: "PRE 3"}

def week_label(week: int | None) -> str:
    if week == 19: return "WURD • WILD CARD"
    if week == 20: return "WURD • DIVISIONAL"
    if week == 21: return "WURD • CONFERENCE"
    if week == 23: return "WURD • SUPER BOWL"

    if week in _PRE_LABELS:
        return f"WURD • {_PRE_LABELS[week]}"
    if not week:
        return "WURD"
    return f"WURD • WEEK {week}"

def _draw_header(draw, canvas, week: int | None):
    bar_h = 130
    bar = Image.new("RGBA", (canvas.width, bar_h), (0, 0, 0, 160))
    canvas.alpha_composite(bar, (0, 0))

    # Header text: WURD • WEEK X
    if week:
        header = week_label(week)  # already "WURD • WEEK 15"
    else:
        header = "WURD"

    tw, th = draw.textbbox((0, 0), header, font=FONT_HEADER)[2:]

    draw.text(
        ((canvas.width - tw) // 2, (bar_h - th) // 2),
        header,
        fill="white",
        font=FONT_HEADER
    )

def _gradient_bg(left_color: str, right_color: str, W=1280, H=960):
    def to_rgb(h): h=h.lstrip("#"); return tuple(int(h[i:i+2],16) for i in (0,2,4))
    c1 = to_rgb(left_color); c2 = to_rgb(right_color)
    im = Image.new("RGB", (W,H), "black").convert("RGBA")
    dr = ImageDraw.Draw(im)
    for x in range(W):
        t = x/(W-1)
        r = int(c1[0]*(1-t)+c2[0]*t); g = int(c1[1]*(1-t)+c2[1]*t); b = int(c1[2]*(1-t)+c2[2]*t)
        dr.line([(x,0),(x,H)], fill=(r,g,b))
    im = Image.alpha_composite(im, Image.new("RGBA",(W,H),(0,0,0,150)))  # darken
    return im

def _badge_with_logo(team: str, logo_path: str, size=260):
    prim, sec = TEAM_COLORS.get(team, ("#333333","#999999"))
    badge = Image.new("RGBA", (size, size), (0,0,0,0))
    bd = ImageDraw.Draw(badge)
    bd.ellipse([8,8,size-8,size-8], fill=prim, outline=sec, width=8)
    try:
        lg = Image.open(logo_path).convert("RGBA")
        max_side = size - 50

        try:
            RESAMPLE = Image.Resampling.LANCZOS  # This may not exist on raspberry pi
        except AttributeError:
            RESAMPLE = Image.ANTIALIAS
        # ...
        lg.thumbnail((max_side, max_side), RESAMPLE)

        badge.alpha_composite(lg, ((size-lg.width)//2, (size-lg.height)//2))
    except Exception:
        pass
    return badge

def _logo_path_for(team: str) -> str | None:
    candidates = [
        os.path.join(LOGOS_DIR, f"{team}.png"),
        os.path.join(LOGOS_DIR, f"{team.title()}.png"),
        os.path.join(LOGOS_DIR, f"{team.capitalize()}.png"),
        # extra safety for 49ers
        os.path.join(LOGOS_DIR, "49ers.png"),
        os.path.join(LOGOS_DIR, "49ERS.png"),
    ]
    for p in candidates:
        if os.path.isfile(p):
            return p
    return None

def _load_wurd_logo(max_width=220):
    try:
        logo = Image.open(WURD_LOGO_PATH).convert("RGBA")
        ratio = max_width / logo.width
        logo = logo.resize(
            (int(logo.width * ratio), int(logo.height * ratio)),
            Image.Resampling.LANCZOS
        )
        return logo
    except Exception as e:
        logger.warning(f"WURD logo load failed: {e}")
        return None

def _team_block_by_name(data: dict, team_name: str):
    if not data:
        return None, None, []

    for side in ("home", "away"):
        t = data.get(side)
        if not t:
            continue

        if t.get("name", "").upper() == team_name.upper():
            return (
                t.get("record"),
                t.get("ovr"),
                (t.get("top_players") or [])[:2]
            )

    return None, None, []

def render_flyer_png(week: int, team1: str, team2: str, streamer: str, link: str | None, flyer_data=None) -> str:
    W, H = 1280, 960

    prim1, _ = TEAM_COLORS.get(team1, ("#333333", "#777777"))
    prim2, _ = TEAM_COLORS.get(team2, ("#333333", "#777777"))

    canvas = _gradient_bg(prim1, prim2, W, H)
    draw = ImageDraw.Draw(canvas)

    # ---------------- HEADER ----------------
    _draw_header(draw, canvas, week)

    # ---------------- CENTER BADGES ----------------
    lcx = W // 2 - 280
    rcx = W // 2 + 280
    cy = H // 2 - 40

    logo1 = _logo_path_for(team1)
    logo2 = _logo_path_for(team2)

    if not logo1 or not logo2:
        raise FileNotFoundError("Missing logo PNG(s) in LOGOS_DIR")

    b1 = _badge_with_logo(team1, logo1)
    b2 = _badge_with_logo(team2, logo2)

    canvas.alpha_composite(b1, (lcx - b1.width // 2, cy - b1.height // 2))
    canvas.alpha_composite(b2, (rcx - b2.width // 2, cy - b2.height // 2))

    # VS
    vs = "VS"
    tw, th = draw.textbbox((0, 0), vs, font=FONT_HDR)[2:]
    draw.text((W // 2 - tw // 2, cy - th // 2), vs, fill="white", font=FONT_HDR)

    # ---------------- TEAM NAMES ----------------
    name_y = cy + 170

    def centered_text(x_center, y, text, font):
        tw, th = draw.textbbox((0, 0), text, font=font)[2:]
        draw.text((x_center - tw // 2, y), text, fill="white", font=font)

    centered_text(lcx, name_y, team1, FONT_SUB)
    centered_text(rcx, name_y, team2, FONT_SUB)

    # ---------------- TEAM STATS ----------------
    stats_y = name_y + 50

    if flyer_data:
        t1_rec, t1_ovr, t1_stars = _team_block_by_name(flyer_data, team1)
        t2_rec, t2_ovr, t2_stars = _team_block_by_name(flyer_data, team2)

        if t1_rec and t1_ovr:
            centered_text(lcx, stats_y, f"{t1_rec} | OVR {t1_ovr}", FONT_BODY)

        if t2_rec and t2_ovr:
            centered_text(rcx, stats_y, f"{t2_rec} | OVR {t2_ovr}", FONT_BODY)

    # ---------------- STAR PLAYERS ----------------
    star_y = stats_y + 45

    def draw_stars(center_x, stars):
        y = star_y
        for p in stars[:2]:
            name = p.get("name")
            pos = p.get("pos")
            if name and pos:
                text = f"{name} ({pos})"
                tw, th = draw.textbbox((0, 0), text, font=FONT_BODY)[2:]
                draw.text((center_x - tw // 2, y), text, fill="white", font=FONT_BODY)
                y += 34

    if flyer_data:
        draw_stars(lcx, t1_stars)
        draw_stars(rcx, t2_stars)

    # ---------------- BOTTOM BROADCAST BAR ----------------
    bottom_h = 160
    bottom_y = H - bottom_h

    canvas.alpha_composite(
        Image.new("RGBA", (W, bottom_h), (0, 0, 0, 190)),
        (0, bottom_y)
    )

    # Streamer
    draw.text((60, bottom_y + 30), f"Streamer: {streamer}", font=FONT_BODY, fill="white")

    # Live line
    live_text = "Live • Scan QR Code" if link else "Live: (link pending)"
    draw.text((60, bottom_y + 75), live_text, font=FONT_BODY, fill="white")

    # QR
    if link:
        qr = qrcode.make(link).resize((140, 140))
        canvas.paste(qr, (W - 60 - 140, bottom_y + 10))
        logger.info(f"[QR LINK VALUE] -> {repr(link)}")

    # ---------------- SAVE ----------------
    out_dir = os.path.join(FLYER_OUT_DIR, f"week_{week}")
    os.makedirs(out_dir, exist_ok=True)

    path = os.path.join(out_dir, f"{team1}_vs_{team2}.png")
    canvas.convert("RGB").save(path, "PNG")

    return path

def generate_flyer_with_fallback(
    week: int,
    t1: str,
    t2: str,
    streamer: str,
    link: str | None,
    flyer_prompt: str | None,
    flyer_data: dict | None = None
) -> tuple[str, str]:
    """
    Returns: (flyer_path, source) where source is 'AI' or 'STATIC'
    """

    logger.info("FLYER_PROMPT_PRESENT: %s", bool(flyer_prompt))
    logger.info("FLYER_DATA_PRESENT: %s", bool(flyer_data))

    # ---- TRY AI FIRST ----
    if flyer_prompt:
        try:
            out_dir = os.path.join(FLYER_OUT_DIR, f"week_{week}")
            os.makedirs(out_dir, exist_ok=True)
            ai_path = os.path.join(out_dir, f"{t1}_vs_{t2}_ai.png")

            logger.info("Flyer data payload: %s", json.dumps(flyer_data, indent=2))

            if flyer_data:
                t1d = flyer_data["team1"]
                t2d = flyer_data["team2"]

                logger.info("AI TEAM 1: %s | Rec=%s | OVR=%s | Players=%s",
                            t1d["name"], t1d["record"], t1d["ovr"],
                            ", ".join(p["name"] for p in t1d.get("top_players", []))
                            )

                logger.info("AI TEAM 2: %s | Rec=%s | OVR=%s | Players=%s",
                            t2d["name"], t2d["record"], t2d["ovr"],
                            ", ".join(p["name"] for p in t2d.get("top_players", []))
                            )

            logger.info("AI PROMPT:\n%s", flyer_prompt)

            ok = generate_chatgpt_flyer_image(
                prompt=flyer_prompt,
                out_path=ai_path
            )

            if ok and os.path.isfile(ai_path):
                logger.info("Flyer image source: AI")
                return ai_path, "AI"

        except Exception:
            logger.exception("AI flyer failed, falling back to static")

    # ---- FALLBACK TO STATIC ----
    static_path = render_flyer_png(
        week,
        t1,
        t2,
        streamer=streamer,
        link=link,
        flyer_data=flyer_data
    )

    logger.info("Flyer image source: STATIC")
    return static_path, "STATIC"

