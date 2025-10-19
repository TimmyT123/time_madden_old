# This file automatically runs in time_madden.py on the raspberry pi under: sudo nano /etc/systemd/system/my_python_script.service
# TODO IF NEW PLAYER THEN DELETE SCHEDULE AND POST NEW SCHEDULE IN SCHEDULE FORUM - MAKE SURE DELETE SCHEDULE FORUM IS APPLIED
#!/usr/bin/env python3

import time
import nextcord  # Import the nextcord library for interacting with Discord's API
from nextcord.ext import commands  # Import commands module for bot commands
import random
import re  # Import regex for pattern matching in strings
from datetime import datetime, timedelta, timezone as dt_timezone
from pytz import timezone  # Import timezone handling for managing time zones
import pytz  # Import pytz for timezone support
import Wurd24Scheduler as wrd  # Import a custom scheduler module named Wurd24Scheduler
import csv  # Import CSV module for handling CSV files
import json
from dotenv import load_dotenv
import os
import sys
import asyncio
import logging

from nextcord import File, AllowedMentions
from PIL import Image, ImageDraw, ImageFont, ImageFilter
import qrcode

from collections import defaultdict, OrderedDict
import glob

from logging.handlers import RotatingFileHandler
from nfl_teams_divisions import nfl_teams  # Import the complete NFL teams mapping

try:
    from zoneinfo import ZoneInfo
    def _tz(name: str): return ZoneInfo(name)
except Exception:
    import pytz
    def _tz(name: str): return pytz.timezone(name)


TEST = False  # Debug mode variable. If True, print outputs instead of sending to Discord advance or schedule forum

# Load environment variables from a .env file
load_dotenv()
token = os.getenv('DISCORD_BOT_TOKEN')

GUILD_ID = int(os.getenv("GUILD_ID"))  # WURD_CHAMPIONSHIPS
CATEGORY_ID = int(os.getenv("CATEGORY_ID"))  # Text Channels
ADMIN_ROLE_NAME = 'Admin'  # Admin role name
# AUTHORIZED_USERS stored as comma-separated string -> convert to list of ints
AUTHORIZED_USERS = [int(uid.strip()) for uid in os.getenv("AUTHORIZED_USERS", "").split(",") if uid.strip()]  # Bernard and me

# Key: channel_id, Value: {"created_at": datetime, "member_ids": [member1_id, member2_id], "responses": set()}
channel_activity_tracker = {}

# Add a new dictionary to track last reminder times - used in check_inactivity function
last_reminder_time = {}

# Configure logging
logger = logging.getLogger('discord_bot')
logger.setLevel(logging.INFO)

# Create a console handler with utf-8 encoding
console_handler = logging.StreamHandler(sys.stdout)  # Use sys.stdout for standard output
console_handler.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))

# Create a rotating file handler with utf-8 encoding
file_handler = RotatingFileHandler('bot.log', maxBytes=5 * 1024 * 1024, backupCount=5, encoding='utf-8')


# === GG ALERT CONFIG (put this near the top; before on_message) ===
GG_COOLDOWN_SEC = 300  # 5 minutes
_last_gg_alert_ts = 0.0

GG_WORD_RE = re.compile(r"\bggs?\b", re.IGNORECASE)

# Use YOUR provided IDs from .env (see sample below)
GG_GUILD_ID = int(os.getenv("GG_GUILD_ID", "0") or 0)
GG_CATEGORY_ID = int(os.getenv("GG_CATEGORY_ID", "0") or 0)  # category that holds your game threads/channels
GG_ALERT_CHANNEL_ID = int(os.getenv("GG_ALERT_CHANNEL_ID", "0") or 0)  # where the bot will post the alert
GG_ALERT_MENTION_USER_ID = int(os.getenv("GG_ALERT_MENTION_USER_ID", "0") or 0)  # who to @mention in the alert

GAME_STREAMS_FORUM_ID = int(os.getenv("GAME_STREAMS_FORUM_ID", "0") or 0)
GAME_STREAMS_CHANNEL_ID = int(os.getenv("GAME_STREAMS_CHANNEL_ID", "0") or 0)
LOGOS_DIR = os.getenv("LOGOS_DIR", "./static/logos")
FLYER_OUT_DIR = os.getenv("FLYER_OUT_DIR", "./static/flyers")
EVERYONE_MENTIONS = AllowedMentions(everyone=True, users=False, roles=False, replied_user=False)

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

FLYER_REGISTRY = "data/flyers.json"  # de-dupe store
os.makedirs(os.path.dirname(FLYER_OUT_DIR), exist_ok=True)

# Add handlers to the logger
logger.addHandler(console_handler)
logger.addHandler(file_handler)

# Initialize time zone variables
utc = pytz.utc
phoenix_time = datetime.now(pytz.timezone('US/Arizona'))
logger.info(f"AZ time now is: {phoenix_time.strftime('%I:%M %p')}")

# Print current system time
my_time = datetime.now()
logger.info(f"My time now is: {time.strftime('%I:%M %p  %Z')}")


# Load team data from NFL_Teams.csv into a list of lists
try:
    with open('NFL_Teams.csv', newline='') as f:
        reader = csv.reader(f)
        teams = list(reader)
    logger.info("Successfully loaded NFL_Teams.csv")
except FileNotFoundError:
    logger.error("NFL_Teams.csv file not found. Please ensure it exists in the script directory.")
    teams = []

# Configure bot permissions and initialize bot with command prefix "!"
intents = nextcord.Intents.default()
intents.members = True
intents.dm_messages = True  # Enable direct message handling
intents.message_content = True   # Enable content reading for messages
bot = commands.Bot(command_prefix="!", intents=intents, help_command=None)


# Link finder + nickname/team parsing (helpers)
LINK_RE = re.compile(
    r"(https?://(?:www\.)?(?:twitch\.tv/[A-Za-z0-9_/-]+|youtu\.be/[^\s>]+|youtube\.com/[^\s>]+))",
    re.IGNORECASE
)

TITLE_RE = re.compile(
    r"(?:W|Week)\s*(\d+).*?\b([A-Z0-9][A-Z0-9 ]+?)\b\s+vs\s+\b([A-Z0-9][A-Z0-9 ]+?)\b",
    re.IGNORECASE
)


ADVANCE_CHANNEL_ID = int(os.getenv("ADVANCE_CHANNEL_ID", "0") or 0)

WEEK_STATE_FILE = "data/week_state.json"
os.makedirs("data", exist_ok=True)

# Learned each advance:
_current_week: int | None = None
# team -> opponent
_current_matchups: dict[str, str] = {}
# list of (left_team, right_team) in the written order
_current_pairs: list[tuple[str, str]] = []

TEAM_NAME_RE = r"[A-Z][A-Za-z ]+"  # simple, forgiving

def _canon_team_upper(s: str) -> str | None:
    t = extract_team_from_nick(s or "")
    return t if t else None

def _load_nfl_title_and_upper():
    with open('NFL_Teams.csv', 'r', encoding='utf-8') as f:
        titles = [ln.strip() for ln in f if ln.strip()]
    uppers = {t.upper(): t for t in titles}
    return titles, uppers

def _scan_guild_for_team_claims(guild):
    """Return (claims, conflicts, unknowns)
       claims: dict[TEAM_UPPER] -> list[member]
       conflicts: subset of claims where more than one member matches the same team
       unknowns: members whose name looks like a team but isn't in NFL_Teams.csv
    """
    titles, upper_map = _load_nfl_title_and_upper()
    claims = {}
    unknowns = []
    for m in guild.members:
        if getattr(m, "bot", False):
            continue
        team_up = _canon_team_upper(m.display_name or m.name)
        if not team_up:
            continue
        if team_up in upper_map:
            claims.setdefault(team_up, []).append(m)
        else:
            unknowns.append((m, team_up))
    conflicts = {t: members for t, members in claims.items() if len(members) > 1}
    return claims, conflicts, unknowns, upper_map

def _format_audit_report(guild, claims, conflicts, unknowns, upper_map):
    total_claimed = len(claims)
    unique_members = sum(len(v) for v in claims.values())
    taken_titles = sorted(upper_map[t] for t in claims.keys())
    lines = []
    lines.append(f"**WURD users → teams audit for {guild.name}**")
    lines.append(f"Claimed teams: {total_claimed} | Claiming members: {unique_members}")
    if conflicts:
        lines.append("")
        lines.append("__Conflicts (multiple members claiming same team)__")
        for t, members in sorted(conflicts.items(), key=lambda x: upper_map[x[0]]):
            who = ", ".join(f"{m.display_name} ({m.id})" for m in members)
            lines.append(f"- {upper_map[t]}: {who}")
    else:
        lines.append("")
        lines.append("No conflicts detected ✅")

    if unknowns:
        lines.append("")
        lines.append("__Unknown team-like prefixes (not in NFL_Teams.csv)__")
        for m, t in unknowns:
            lines.append(f"- {m.display_name} ({m.id}) → “{t}”")
    else:
        lines.append("")
        lines.append("No unknown/invalid team prefixes detected ✅")

    lines.append("")
    lines.append("__Teams currently considered taken__")
    for tt in taken_titles:
        lines.append(f"- {tt}")

    return "\n".join(lines)


# --- Preseason support ---
# Internally: PRE 1 -> week = -3, PRE 2 -> -2, PRE 3 -> -1
def _pre_to_week(n: int) -> int:
    return -(4 - n)  # 1->-3, 2->-2, 3->-1

_PRE_LABELS = {-3: "PRE 1", -2: "PRE 2", -1: "PRE 3"}

def week_label(week: int | None) -> str:
    if week in _PRE_LABELS:
        return f"WURD • {_PRE_LABELS[week]}"
    if not week:
        return "WURD"   # fallback if unknown
    return f"WURD • WEEK {week}"

def parse_week_token(text: str) -> int | None:
    """
    Finds 'WEEK 7' or 'PRE 2' in text and returns a normalized week number:
      WEEK N -> N
      PRE N  -> -3..-1 (for N=1..3)
    """
    if not text:
        return None
    # WEEK N
    m = re.search(r"\bW(?:EEK)?\s*(\d{1,2})\b", text, re.IGNORECASE)
    if m:
        return int(m.group(1))

    # PRE N or PRESEASON N
    m = re.search(r"\bPRE(?:SEASON)?\s*(\d)\b", text, re.IGNORECASE)
    if m:
        n = int(m.group(1))
        if 1 <= n <= 3:
            return _pre_to_week(n)
    return None


def _canon_team_for_lookup(s: str) -> str:
    return canonical_team(s.upper())

def _parse_advance_block(text: str):
    """
    Parses an advance post like:
      WEEK 17
      Eagles(U) vs Bills(U)
      ...
    Returns (week, pairs, mapping)
    """
    week = None
    pairs = []
    mapping = {}

    lines = [ln.strip() for ln in (text or "").splitlines() if ln.strip()]
    for ln in lines:
        wk_token = parse_week_token(ln)
        if wk_token is not None:
            week = wk_token
            continue

        m = re.match(
            rf"^\s*({TEAM_NAME_RE})\s*\([^)]*\)\s*vs\s*({TEAM_NAME_RE})\s*\([^)]*\)\s*$",
            ln, flags=re.IGNORECASE
        )
        if not m:
            # try without (U)/(C)
            m = re.match(
                rf"^\s*({TEAM_NAME_RE})\s*vs\s*({TEAM_NAME_RE})\s*$",
                ln, flags=re.IGNORECASE
            )
        if m:
            left = _canon_team_for_lookup(m.group(1))
            right = _canon_team_for_lookup(m.group(2))
            pairs.append((left, right))
            mapping[left] = right
            mapping[right] = left

    return week, pairs, mapping


def _load_week_state():
    try:
        with open(WEEK_STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return {"week": 0, "matchups": []}  # matchups: list of [TEAM_A, TEAM_B]
    except Exception:
        return {"week": 0, "matchups": []}

def _save_week_state(week: int, matchups: list[list[str]]):
    with open(WEEK_STATE_FILE, "w", encoding="utf-8") as f:
        json.dump({"week": week, "matchups": matchups}, f, indent=2)

def get_current_week_and_matchups():
    st = _load_week_state()
    return int(st.get("week", 0)), st.get("matchups", [])

ADVANCE_LINE_RE = re.compile(
    r"^\s*([A-Za-z .’'-]+)\s*\([^)]*\)\s*vs\s*([A-Za-z .’'-]+)\s*\([^)]*\)\s*$",
    re.IGNORECASE
)

def parse_advance_message(text: str):
    """
    Expects something like:
      WEEK 16
      49ers(U) vs Colts(U)
      Bengals(U) vs Dolphins(U)
      ...
    Returns: (week:int, matchups:[[TEAM, TEAM], ...]) with canonical ALL-CAPS names
    """
    lines = [l.strip() for l in (text or "").splitlines() if l.strip()]
    if not lines:
        return 0, []

    # find "WEEK N"
    week = 0
    for ln in lines[:3]:
        wk = parse_week_token(ln)
        if wk is not None:
            week = wk
            break

    matchups = []
    for ln in lines:
        m = ADVANCE_LINE_RE.match(ln)
        if not m:
            continue
        a = canonical_team(m.group(1).upper())
        b = canonical_team(m.group(2).upper())
        if a and b:
            matchups.append([a, b])
    return week, matchups

def opponent_for_team(team: str, matchups: list[list[str]]):
    team = canonical_team(team)
    for a, b in matchups:
        if team == a: return b
        if team == b: return a
    return None


def find_stream_link(text: str) -> str | None:
    if not text: return None
    m = LINK_RE.search(text)
    return m.group(1) if m else None

def canonical_team(s: str) -> str:
    s = (s or "").upper().strip()
    s = re.sub(r"[^A-Z ]+", "", s)  # remove punctuation
    # simple alias examples
    aliases = {"DAL":"COWBOYS","DALLAS":"COWBOYS","MIN":"VIKINGS","MINNESOTA":"VIKINGS","NYG":"GIANTS","GIANTS":"GIANTS"}
    return aliases.get(s, s)

def extract_team_from_nick(nick: str) -> str | None:
    if not nick:
        return None
    m = re.match(r"^([A-Z0-9][A-Z0-9 ]+)\b", nick.strip())
    return canonical_team(m.group(1)) if m else None

# put this near TITLE_RE (or replace TITLE_RE with this teams-only regex)
TEAMS_IN_TITLE_RE = re.compile(
    r"\b([A-Z0-9][A-Z0-9 ]+?)\b\s+vs\s+\b([A-Z0-9][A-Z0-9 ]+?)\b",
    re.IGNORECASE
)

def parse_title_for_week_and_teams(title: str) -> tuple[int | None, str | None, str | None]:
    # week can be regular (e.g., WEEK 7) or preseason (e.g., PRE 2 / PRESEASON 2 -> negative week)
    wk = parse_week_token(title or "")

    m = TEAMS_IN_TITLE_RE.search(title or "")
    if not m:
        return wk, None, None

    t1 = canonical_team(m.group(1))
    t2 = canonical_team(m.group(2))
    return wk, t1, t2


def sorted_pair(a: str, b: str) -> tuple[str, str]:
    return tuple(sorted([a, b]))


# Tiny JSON registry (prevents duplicates)
def _load_registry():
    try:
        with open(FLYER_REGISTRY, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return {}
    except Exception:
        return {}

def _save_registry(d: dict):
    os.makedirs(os.path.dirname(FLYER_REGISTRY), exist_ok=True)
    tmp = FLYER_REGISTRY + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(d, f, indent=2)
    os.replace(tmp, FLYER_REGISTRY)

def flyer_key(week: int, team_a: str, team_b: str) -> str:
    a, b = sorted_pair(team_a, team_b)
    return f"week:{week}:{a}:{b}"

def registry_has(week: int, t1: str, t2: str) -> bool:
    reg = _load_registry()
    return flyer_key(week, t1, t2) in reg

def registry_put(week: int, t1: str, t2: str, record: dict):
    reg = _load_registry()
    reg[flyer_key(week, t1, t2)] = record
    _save_registry(reg)


# Flyer generator (logos inside badges)
def _font(size: int, bold=False):
    try:
        path = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
        return ImageFont.truetype(path, size=size)
    except Exception:
        return ImageFont.load_default()

FONT_HDR = _font(86, bold=True)
FONT_SUB = _font(48, bold=True)
FONT_BODY= _font(32)

def _draw_header(draw, canvas, week: int | None):
    bar = Image.new("RGBA", (canvas.width, 110), (0,0,0,140))
    canvas.alpha_composite(bar, (0,0))
    txt = week_label(week)  # <<— uses PRE/WEEK label
    tw, th = draw.textbbox((0,0), txt, font=FONT_HDR)[2:]
    draw.text(((canvas.width - tw)//2, 55 - th//2), txt, fill="white", font=FONT_HDR)

def _gradient_bg(left_color: str, right_color: str, W=1280, H=720):
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
    ]
    for p in candidates:
        if os.path.isfile(p):
            return p
    return None

def render_flyer_png(week: int, team1: str, team2: str, streamer: str, link: str | None) -> str:
    W,H = 1280,720
    prim1, _ = TEAM_COLORS.get(team1, ("#333333","#777777"))
    prim2, _ = TEAM_COLORS.get(team2, ("#333333","#777777"))
    canvas = _gradient_bg(prim1, prim2, W, H)
    draw = ImageDraw.Draw(canvas)

    _draw_header(draw, canvas, week)

    # badges + logos
    lcx, rcx, cy = W//2 - 260, W//2 + 260, H//2 - 10
    logo1 = _logo_path_for(team1)
    logo2 = _logo_path_for(team2)
    if not logo1 or not logo2:
        raise FileNotFoundError("Missing logo PNG(s) in LOGOS_DIR")
    b1 = _badge_with_logo(team1, logo1)
    b2 = _badge_with_logo(team2, logo2)
    canvas.alpha_composite(b1, (lcx - b1.width//2, cy - b1.height//2))
    canvas.alpha_composite(b2, (rcx - b2.width//2, cy - b2.height//2))

    # VS
    vs = "VS"; tw, th = draw.textbbox((0,0), vs, font=FONT_HDR)[2:]
    draw.text((W//2 - tw//2, cy - th//2), vs, fill="white", font=FONT_HDR)

    # team labels
    draw.text((lcx - 120, cy + 160), team1, font=FONT_SUB, fill="white")
    draw.text((rcx - 110, cy + 160), team2, font=FONT_SUB, fill="white")

    # bottom info
    y = 560
    canvas.alpha_composite(Image.new("RGBA",(W,H-y),(0,0,0,120)), (0,y))
    draw.text((60, y+20), f"Streamer: {streamer}", font=FONT_BODY, fill="white")
    link_text = f"Live: {link}" if link else "Live: (link pending)"
    draw.text((60, y+64), link_text, font=FONT_BODY, fill="white")
    if link:
        qr = qrcode.make(link).resize((150,150))
        canvas.paste(qr, (W-60-150, y+2))

    # save
    out_dir = os.path.join(FLYER_OUT_DIR, f"week_{week}")
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, f"{team1}_vs_{team2}.png")
    canvas.convert("RGB").save(path, "PNG")
    return path

def _noembed(url: str | None) -> str:
    return f"<{url}>" if url else ""

# Posting helper with @everyone (and pin)
def _caption(week: int | None, t1: str, t2: str, streamer: str, link: str | None) -> str:
    link_line = f"Live: {_noembed(link) or '(link pending)'}"
    header = week_label(week).replace("WURD • ", "**") + "**"
    return (
        "@everyone\n"
        f"{header}\n"
        f"{t1} vs {t2}\n"
        f"Streamer: {streamer}\n"
        f"{link_line}"
    )

async def post_flyer_with_everyone(thread, flyer_path, week, t1, t2, streamer, link):
    perms = thread.permissions_for(thread.guild.me)
    if not perms.mention_everyone:
        msg = await thread.send(
            content=("**Heads-up:** I don’t have `Mention Everyone` permission here.\n\n"
                     + _caption(week,t1,t2,streamer,link).replace("@everyone\n","")),
            file=File(flyer_path),
            allowed_mentions=AllowedMentions.none()
        )
        try:
            await msg.pin()
        except:
            pass
        # ⬇️ suppress the URL preview
        try:
            await msg.edit(suppress=True)
        except:
            pass
        return msg

    msg = await thread.send(
        content=_caption(week,t1,t2,streamer,link),
        file=File(flyer_path),
        allowed_mentions=EVERYONE_MENTIONS
    )
    try:
        await msg.pin()
    except:
        pass
    # ⬇️ suppress the URL preview
    try:
        await msg.edit(suppress=True)
    except:
        pass
    return msg

# Late-link watcher (edits pinned caption once; no new ping)
async def watch_first_link_and_edit(thread, author_id: int, posted_msg_id: int, week: int, t1: str, t2: str, streamer: str):
    def _check(m: nextcord.Message):
        return (
            m.channel.id == thread.id and
            m.author.id == author_id and
            find_stream_link(m.content) is not None
        )
    try:
        m = await bot.wait_for("message", timeout=3600, check=_check)
        link = find_stream_link(m.content)
        if not link:
            return
        msg = await thread.fetch_message(posted_msg_id)
        await msg.edit(
            content=_caption(week, t1, t2, streamer, link),
            allowed_mentions=AllowedMentions.none()
        )
        # ⬇️ make sure the edited message also has no preview
        try:
            await msg.edit(suppress=True)
        except:
            pass
    except asyncio.TimeoutError:
        pass
    except Exception as e:
        logger.warning(f"late-link watcher: {e}")

def _leading_alnum_lower(s: str) -> str:
    """Lowercased display name starting at the first letter/digit."""
    s = s or ""
    i = 0
    while i < len(s) and not s[i].isalnum():
        i += 1
    return s[i:].casefold()

def name_starts_with_team(display_name: str, team_name: str) -> bool:
    """True if display_name starts with team_name (case-insensitive), ignoring leading symbols."""
    return _leading_alnum_lower(display_name).startswith(team_name.strip().casefold())



@bot.command(name="check_users")
@commands.has_role(ADMIN_ROLE_NAME)
async def check_users(ctx):
    # Load official team list exactly as in NFL_Teams.csv (Title Case)
    try:
        with open('NFL_Teams.csv', 'r', encoding='utf-8') as f:
            official = [ln.strip() for ln in f if ln.strip()]
    except FileNotFoundError:
        await ctx.send("NFL_Teams.csv not found on the Pi.")
        return

    # Build case-insensitive lookup
    teams_cf = [(t, t.casefold()) for t in official]

    guild = bot.get_guild(GUILD_ID)
    if guild is None:
        await ctx.send(f"Guild {GUILD_ID} not found.")
        return

    claimed_by_user = {}   # team -> member
    unknown = []           # (member, preview)
    for m in guild.members:
        if getattr(m, "bot", False):
            continue
        disp = m.display_name or m.name or ""
        lead = _leading_alnum_lower(disp)

        found_team = None
        for t_title, t_cf in teams_cf:
            if lead.startswith(t_cf):
                found_team = t_title
                break

        if found_team:
            # first come wins; if you want to detect duplicates, keep a list per team
            claimed_by_user.setdefault(found_team, m)
        else:
            # show the first token-ish for debugging
            preview = (disp.strip().split(maxsplit=2)[0:2])
            preview = " ".join(preview) if preview else disp.strip()[:12]
            unknown.append((m, preview))

    # Compose report
    lines = []
    lines.append(f"**WURD users → teams audit for {guild.name}**")
    lines.append(f"Claimed teams: {len(claimed_by_user)} | Claiming members: {len(claimed_by_user)}")
    lines.append("")
    dupes = []  # populate if you change claimed_by_user to track multiples
    if dupes:
        lines.append("Conflicts detected ❗")
        for team, members in dupes:
            lines.append(f"- {team}: " + ", ".join(m.display_name for m in members))
    else:
        lines.append("No conflicts detected ✅")
    lines.append("")

    if unknown:
        lines.append("__Unknown team-like names (don’t start with an official team)__")
        for m, prev in unknown:
            lines.append(f"- {m.display_name} ({m.id}) → “{prev}”")
        lines.append("")

    # Teams currently considered taken
    taken = sorted(claimed_by_user.keys())
    if taken:
        lines.append("__Teams currently considered taken__")
        for t in taken:
            lines.append(f"- {t}")

    report = "\n".join(lines)
    for chunk in split_message(report):
        await ctx.send(chunk)


@bot.command(name="rebuild_users")
@commands.has_role(ADMIN_ROLE_NAME)
async def rebuild_users(ctx):
    guild = bot.get_guild(GUILD_ID)
    if not guild:
        return await ctx.reply("Guild not found.")
    claims, conflicts, unknowns, upper_map = _scan_guild_for_team_claims(guild)

    # Write Title Case team names to wurd24users.csv
    titles = [upper_map[t] for t in sorted(claims.keys())]
    with open('wurd24users.csv', 'w', encoding='utf-8', newline='') as f:
        for t in titles:
            f.write(t + '\n')

    report = _format_audit_report(guild, claims, conflicts, unknowns, upper_map)
    try:
        await ctx.author.send("`wurd24users.csv` regenerated.\n\n" + report)
        await ctx.reply("Regenerated `wurd24users.csv` and DM’d you the summary.")
    except:
        await ctx.reply("`wurd24users.csv` regenerated.\n\n" + report)



# The trigger: on_thread_create (new block)
@bot.event
async def on_thread_create(thread: nextcord.Thread):
    # only in game-streams forum
    if thread.parent_id != GAME_STREAMS_FORUM_ID:
        return

    # 0) Do not create flyers during preseason
    if _current_week is not None and _current_week < 1:
        return

    # 1) Try to parse from title first (week may be None here)
    week, t1, t2 = parse_title_for_week_and_teams(thread.name or "")

    # 2) Fall back to learned advance mapping + author's nickname
    try:
        author = await thread.guild.fetch_member(thread.owner_id)
    except Exception:
        author = None

    if (not t1 or not t2) and _current_matchups and author:
        author_team = extract_team_from_nick(getattr(author, "display_name", "") or "")
        if author_team:
            opp = _current_matchups.get(author_team)
            if opp:
                # Preserve left/right order from the advance list if we can
                for L, R in _current_pairs:
                    if {L, R} == {author_team, opp}:
                        t1, t2 = L, R
                        break
                if not (t1 and t2):
                    t1, t2 = author_team, opp

    # 3) Normalize week: prefer parsed week, else use learned current week
    if week is None and _current_week is not None:
        week = _current_week

    # 4) If we still don't have week or both teams, do nothing (no nag)
    if not week or not (t1 and t2):
        return

    # 5) Eligibility: author must be one of teams unless Admin/Broadcaster
    allowed = False
    if author:
        is_staff = any(r.name in (ADMIN_ROLE_NAME, "Broadcaster") for r in author.roles)
        if is_staff:
            allowed = True
        else:
            author_team = extract_team_from_nick(getattr(author, "display_name", "") or "")
            if author_team and author_team in (t1, t2):
                allowed = True
    if not allowed:
        return

    # 6) De-dupe
    if registry_has(week, t1, t2):
        return

    # 7) Starter message & link (best-effort)
    link = None
    try:
        parent = thread.parent
        starter = await parent.fetch_message(thread.id)
        link = find_stream_link(getattr(starter, "content", ""))
    except Exception:
        pass

    streamer_display = getattr(author, "display_name", "Unknown")

    # 8) Render & post flyer
    try:
        flyer_path = render_flyer_png(week, *sorted_pair(t1, t2), streamer=streamer_display, link=link)
    except Exception as e:
        logger.error(f"flyer render failed: {e}")
        return

    msg = await post_flyer_with_everyone(thread, flyer_path, week, *sorted_pair(t1, t2), streamer_display, link)
    registry_put(week, t1, t2, {
        "thread_id": thread.id,
        "message_id": msg.id,
        "flyer_path": flyer_path,
        "author_id": getattr(author, "id", 0),
        "ts": datetime.utcnow().isoformat()+"Z"
    })

    # 9) Late-link watcher
    if not link and author:
        bot.loop.create_task(watch_first_link_and_edit(thread, author.id, msg.id, week, *sorted_pair(t1, t2), streamer_display))


# Timezone explanation helper
def extract_timezone_code(nickname):
    if not nickname:
        return None
    match = re.search(r'\b(PT|AZ|MT|CT|ET)\b(?=[^\w]*$)', nickname, flags=re.IGNORECASE)
    return match.group(1) if match else None

def get_timezone_offset_info(tz1_code, tz2_code, tz1_nick, tz2_nick):
    tz_map = {
        'PT': 'US/Pacific',
        'AZ': 'US/Arizona',
        'MT': 'US/Mountain',
        'CT': 'US/Central',
        'ET': 'US/Eastern'
    }

    if tz1_code not in tz_map or tz2_code not in tz_map:
        return None

    now = datetime.now(pytz.utc)
    tz1 = pytz.timezone(tz_map[tz1_code])
    tz2 = pytz.timezone(tz_map[tz2_code])

    time1 = now.astimezone(tz1)
    time2 = now.astimezone(tz2)
    offset1 = time1.utcoffset().total_seconds() // 3600
    offset2 = time2.utcoffset().total_seconds() // 3600
    diff = int(offset1 - offset2)

    if diff == 0:
        return f"You're both in the same time zone ({tz1_code})."

    ahead_behind = "ahead of" if diff > 0 else "behind"
    tz1_time = time1.strftime('%I').lstrip('0') + time1.strftime(' %p')
    tz2_time = time2.strftime('%I').lstrip('0') + time2.strftime(' %p')

    return f"**{tz1_code} is {abs(diff)} hour(s) {ahead_behind} {tz2_code}**\n" \
           f"example:\n{tz1_nick:<24} is {tz1_time}\n{tz2_nick:<24} is {tz2_time}"


AP_FILE = 'ap_users.json'
ON_VACATION_FORUM_ID = int(os.getenv("ON_VACATION_FORUM_ID", "0"))

AP_NOTIFIED_FILE = 'ap_notified.json'
AP_ALERT_ADMIN_ID = int(os.getenv("AP_ALERT_ADMIN_ID", "0"))
AP_ALERT_CHANNEL_ID = int(os.getenv("AP_ALERT_CHANNEL_ID", "0"))
AP_ALERT_TZ = os.getenv("AP_ALERT_TZ", "US/Arizona")


AP_SIG_FILE = "data/ap_last_sig.json"
os.makedirs("data", exist_ok=True)

def _sig_to_str(sig_tuple: tuple) -> str:
    # stable JSON string for comparison/persistence
    # sig looks like: (("1234567890","2025-01-03","2025-01-10"), ...)
    return json.dumps(sig_tuple, separators=(",", ":"))

def _load_last_ap_sig_str() -> str:
    try:
        with open(AP_SIG_FILE, "r", encoding="utf-8") as f:
            return f.read().strip()
    except FileNotFoundError:
        return ""
    except Exception:
        return ""

def _save_last_ap_sig_str(sig_str: str):
    tmp = AP_SIG_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(sig_str)
    os.replace(tmp, AP_SIG_FILE)


def _parse_date(d: str) -> datetime:
    # Stored as YYYY-MM-DD (no time); treat as midnight UTC for comparisons
    return datetime.strptime(d, "%Y-%m-%d").replace(tzinfo=dt_timezone.utc)

def load_ap_users():
    """
    Return only entries that are 'active' *today* in the alert timezone:
      - start <= today <= until
    If 'start' is missing/invalid, treat as today (active immediately).
    """
    tz = _tz(AP_ALERT_TZ)
    today_local = datetime.now(tz).date()
    try:
        with open(AP_FILE, "r", encoding="utf-8") as f:
            users = json.load(f)
    except FileNotFoundError:
        return []

    active = []
    for u in users:
        try:
            start_date = _start_date(u)                # new
            until_date = _date_from_str(u["until"])   # existing helper
            if start_date <= today_local <= until_date:
                active.append(u)
        except Exception:
            # skip malformed rows
            continue
    return active

def _normalize_id(value) -> str | None:
    """
    Coerce a Discord ID (int or string) to a normalized numeric-string.
    Returns None if it cannot be parsed as an integer.
    """
    if value is None:
        return None
    s = str(value).strip()
    try:
        # int(...) tolerates leading/trailing spaces and leading zeros;
        # We return canonical digits as a string.
        return str(int(s))
    except Exception:
        return None

def is_on_ap(user_id: int | str, ap_users=None):
    """
    Returns the AP entry dict if user_id is currently on AP, else None.
    Works whether ap_users.json stores IDs as ints or strings.
    """
    ap_users = ap_users if ap_users is not None else load_ap_users()
    target = _normalize_id(user_id)
    if target is None:
        return None
    for u in ap_users:
        uid = _normalize_id(u.get("user_id"))
        if uid is not None and uid == target:
            return u
    return None

def _active_ap_signature():
    """
    Returns a stable signature (tuple) of currently ACTIVE AP entries.
    Active = start <= today <= until in AP_ALERT_TZ (as enforced by load_ap_users).
    Only keys that define 'active identity' are included so edits to notes, etc.
    won't spam unless they affect activation window or user id.
    """
    active = load_ap_users()
    key_tuples = []
    for u in active:
        uid = _normalize_id(u.get("user_id")) or ""  # normalize
        key_tuples.append((uid, (u.get("start") or ""), (u.get("until") or "")))
    return tuple(sorted(key_tuples))

async def ap_autopost_watcher():
    """
    Periodically checks if the set of ACTIVE AP users has changed.
    If changed, post a fresh AP bulletin to the on-vacation forum.
    Avoids posting at startup if nothing changed since last post.
    """
    interval = int(os.getenv("AP_AUTOPUBLISH_INTERVAL_SEC", "600"))  # default: 10 min

    # Load last posted signature from disk (prevents repost on restart)
    prev_sig_str = _load_last_ap_sig_str()

    # Initial check shortly after startup
    await asyncio.sleep(5)
    try:
        curr_sig = _active_ap_signature()
        curr_sig_str = _sig_to_str(curr_sig)
        if curr_sig_str != prev_sig_str:
            await post_ap_bulletin(bot)
            _save_last_ap_sig_str(curr_sig_str)
            prev_sig_str = curr_sig_str
    except Exception as e:
        logger.warning(f"ap_autopost_watcher initial post: {e}")

    # Keep watching
    while True:
        try:
            curr_sig = _active_ap_signature()
            curr_sig_str = _sig_to_str(curr_sig)
            if curr_sig_str != prev_sig_str:
                await post_ap_bulletin(bot)
                _save_last_ap_sig_str(curr_sig_str)
                prev_sig_str = curr_sig_str
        except Exception as e:
            logger.warning(f"ap_autopost_watcher: {e}")

        await asyncio.sleep(interval)


def _start_date(u: dict):
    """
    Returns the date the AP should begin (date object).
    If 'start' missing/invalid, treat as today in the alert timezone.
    """
    tz = _tz(AP_ALERT_TZ)
    today_local = datetime.now(tz).date()
    s = (u.get("start") or "").strip()
    if not s:
        return today_local
    try:
        return _date_from_str(s)
    except Exception:
        return today_local

def _date_from_str(dstr: str):
    """Return a date object from 'YYYY-MM-DD' (no timezone math)."""
    return datetime.strptime(dstr, "%Y-%m-%d").date()

def human_date(dstr: str) -> str:
    """Pretty print the same calendar date (no tz shifting)."""
    d = _date_from_str(dstr)
    dummy = datetime(d.year, d.month, d.day)  # just for strftime parts
    # Avoid %-d portability by injecting the day number
    return f"{dummy.strftime('%a')}, {dummy.strftime('%b')} {d.day}, {dummy.strftime('%Y')}"

def render_ap_bulletin():
    ap_users = sorted(load_ap_users(), key=lambda u: _date_from_str(u["until"]))
    # use your alert timezone, not UTC
    today_str = datetime.now(_tz(AP_ALERT_TZ)).strftime("%b %d, %Y")
    if not ap_users:
        return f"🏝️ Auto-Pilot (AP) Status — updated {today_str}\n\nNo users are on Auto-Pilot right now."

    lines = [f"🏝️ Auto-Pilot (AP) Status — updated {today_str}", ""]
    for u in ap_users:
        disp = u.get("display", f"User {u.get('user_id')}")
        reason = u.get("reason", "").strip()
        until = u.get("until", "")
        notes = u.get("notes", "").strip()
        header = f"• {disp}" + (f" — {reason}" if reason else "")
        lines.append(header)
        lines.append(f"  Returns: {human_date(until)}")
        if notes:
            lines.append(f"  Notes: {notes}")
        lines.append("")

    lines.append("Notes:")
    lines.append("• If they’re your opponent, play their CPU.")
    lines.append("• AP auto-expires the day after the return date.")
    return "\n".join(lines).rstrip()

async def post_ap_bulletin(bot):
    if not ON_VACATION_FORUM_ID:
        logger.warning("ON_VACATION_FORUM_ID not set; skipping AP bulletin.")
        return
    ch = bot.get_channel(ON_VACATION_FORUM_ID)
    if not ch:
        logger.warning("on-vacation-not-avail channel not found.")
        return
    msg = render_ap_bulletin()
    for chunk in split_message(msg):
        await ch.send(chunk)


@bot.command(name='ap')
@commands.has_role(ADMIN_ROLE_NAME)
async def ap(ctx):
    await post_ap_bulletin(bot)
    await ctx.send("AP bulletin posted.")


def _load_notified():
    try:
        with open(AP_NOTIFIED_FILE, "r", encoding="utf-8") as f:
            return set(tuple(x) for x in json.load(f))
    except FileNotFoundError:
        return set()
    except Exception:
        return set()

def _save_notified(s: set[tuple]):
    try:
        with open(AP_NOTIFIED_FILE, "w", encoding="utf-8") as f:
            json.dump(list(s), f)
    except Exception:
        pass


async def ap_return_reminder_loop():
    notified = _load_notified()
    tz = _tz(AP_ALERT_TZ)

    while True:
        try:
            ap_users = load_ap_users()
            now_local = datetime.now(tz)
            today_local = now_local.date()
            tomorrow_local = today_local + timedelta(days=1)

            due = []
            for u in ap_users:
                try:
                    until_date = _date_from_str(u["until"])
                    if until_date == tomorrow_local:
                        due.append(u)
                except Exception:
                    continue
            if due:
                guild = bot.get_guild(GUILD_ID)
                # Where to send: DM admin if set; else channel if set; else skip
                admin_member = guild.get_member(AP_ALERT_ADMIN_ID) if AP_ALERT_ADMIN_ID else None
                alert_channel = bot.get_channel(AP_ALERT_CHANNEL_ID) if AP_ALERT_CHANNEL_ID else None

                # Prepare message
                header = f"⚠️ AP return reminder for {tomorrow_local.strftime('%a, %b %d, %Y')}"
                lines = [header, ""]
                for u in due:
                    key = (str(u.get("user_id")), u.get("until"))
                    if key in notified:
                        continue  # already sent for this (user, date)
                    disp = u.get("display", f"User {u.get('user_id')}")
                    reason = u.get("reason", "").strip()
                    line = f"• {disp}" + (f" — {reason}" if reason else "")
                    line += f"\n  Returns: {human_date(u['until'])}\n  Action: Turn off AP."
                    lines.append(line)
                    lines.append("")
                msg = "\n".join(lines).rstrip()

                # If there is something new to notify
                if len(lines) > 2:
                    sent_ok = False
                    if admin_member:
                        try:
                            await admin_member.send(msg)
                            sent_ok = True
                        except Exception:
                            pass
                    if not sent_ok and alert_channel:
                        try:
                            await alert_channel.send(msg)
                            sent_ok = True
                        except Exception:
                            pass

                    if sent_ok:
                        for u in due:
                            key = (str(u.get("user_id")), u.get("until"))
                            notified.add(key)
                        _save_notified(notified)
        except Exception as e:
            logger.warning(f"ap_return_reminder_loop: {e}")

        # Check hourly
        await asyncio.sleep(3600)

def _is_in_target_game_channel(ch) -> bool:
    try:
        # Text channel under the category
        if ch.type == nextcord.ChannelType.text:
            cat = getattr(ch, "category", None)
            return bool(cat and cat.id == GG_CATEGORY_ID)

        # Threads whose parent (forum/text) is under the category
        if ch.type in (
            nextcord.ChannelType.public_thread,
            nextcord.ChannelType.private_thread,
            nextcord.ChannelType.news_thread,
        ):
            parent = getattr(ch, "parent", None)
            cat = getattr(parent, "category", None) if parent else None
            return bool(cat and cat.id == GG_CATEGORY_ID)

        # Forum channel itself
        if ch.type == nextcord.ChannelType.forum:
            cat = getattr(ch, "category", None)
            return bool(cat and cat.id == GG_CATEGORY_ID)
    except Exception:
        pass
    return False


# BOT CREATING PRIVATE CHANNELS
async def create_channel_helper(guild, team_name, member_ids, ctx=None, message_content="Good Luck and Have Fun!"):
    category = guild.get_channel(CATEGORY_ID)
    if category is None or not isinstance(category, nextcord.CategoryChannel):
        if ctx:
            await ctx.send("Category not found or is not a valid category.")
        else:
            logger.warning("Category not found or is not valid.")
        return

    admin_role = nextcord.utils.get(guild.roles, name=ADMIN_ROLE_NAME)
    if not admin_role:
        if ctx:
            await ctx.send(f"Unable to find the admin role '{ADMIN_ROLE_NAME}'. Please check your server settings.")
        else:
            print(f"Admin role '{ADMIN_ROLE_NAME}' not found.")
        return

    overwrites = {
        guild.default_role: nextcord.PermissionOverwrite(read_messages=False),
        guild.me: nextcord.PermissionOverwrite(read_messages=True, send_messages=True),
        admin_role: nextcord.PermissionOverwrite(read_messages=True, send_messages=True)
    }

    member_info = []
    for member_id in member_ids:
        member = guild.get_member(member_id)
        if member:
            overwrites[member] = nextcord.PermissionOverwrite(read_messages=True, send_messages=True)
            member_info.append((member.mention, member.display_name or member.name))

    existing_channel = nextcord.utils.get(guild.channels, name=team_name, category=category)
    if existing_channel:
        logger.info(f"Channel '{team_name}' already exists in the category '{category.name}'.")
    else:
        try:
            channel = await guild.create_text_channel(
                name=team_name,
                category=category,
                overwrites=overwrites,
                reason="Creating a private channel with the bot."
            )
            channel_activity_tracker[channel.id] = {
                "created_at": datetime.now(pytz.utc),
                "member_ids": member_ids,
                "responses": set()
            }

            # === AP INSERT START ===
            # Only @mention non-AP folks; add a CPU note for AP users.
            ap_list = load_ap_users()  # uses helpers you’ll add above (load_ap_users, is_on_ap, human_date)
            non_ap_mentions = []
            ap_notes = []

            # Build a quick lookup from member_id -> (mention, display)
            id_to_info = {}
            for member_id in member_ids:
                member = guild.get_member(member_id)
                if member:
                    mention = member.mention
                    display = member.display_name or member.name
                    id_to_info[member_id] = (mention, display)

            for member_id in member_ids:
                info = id_to_info.get(member_id)
                if not info:
                    continue
                ap = is_on_ap(member_id, ap_list)
                if ap:
                    shown_name = ap.get("display", info[1])
                    until = ap.get("until", "")
                    note = f"Heads-up: {shown_name} is on **Auto-Pilot** until {human_date(until)}. Please play the CPU for this matchup."
                    ap_notes.append(note)
                else:
                    non_ap_mentions.append(info[0])

            if non_ap_mentions:
                await channel.send(" ".join(non_ap_mentions))
            if ap_notes:
                await channel.send("\n".join(ap_notes))
            # === AP INSERT END ===

            # Timezone difference logic (keep as-is)
            if len(member_info) == 2:
                tz1_code = extract_timezone_code(member_info[0][1])
                tz2_code = extract_timezone_code(member_info[1][1])
                if tz1_code and tz2_code:
                    tz_msg = get_timezone_offset_info(tz1_code, tz2_code, member_info[0][1], member_info[1][1])
                    if tz_msg:
                        await channel.send(tz_msg)

            await channel.send(message_content)

            logger.info(f"Channel '{channel.name}' created successfully in category '{category.name}'.")
        except nextcord.Forbidden:
            logger.error("Bot does not have permission to create channels.")
        except nextcord.HTTPException as e:
            logger.error(f"Failed to create channel: {e}")


# Function to delete all channels in the specified category
async def delete_category_channels(guild):
    if TEST:
        logger.info("Test mode is ON: Skipping channel deletions.")
        return

    category = guild.get_channel(CATEGORY_ID)
    if category is None or not isinstance(category, nextcord.CategoryChannel):
        logger.warning("Category not found or is not a valid category.")
        return

    # Iterate through each channel in the category and delete it
    for channel in category.text_channels:
        try:
            await channel.delete(reason="Clearing category for new user-user channels")
            logger.info(f"Deleted channel '{channel.name}' in category '{category.name}'.")
        except nextcord.Forbidden:
            logger.error(f"Bot does not have permission to delete channel '{channel.name}'.")
        except nextcord.HTTPException as e:
            logger.error(f"Failed to delete channel '{channel.name}': {e}")


# Load the user-user team names from the file generated by Wurd24Scheduler.py
def load_user_user_teams():
    try:
        with open('user_user_teams.txt', 'r') as f:
            return [line.strip() for line in f if line.strip()]
    except FileNotFoundError:
        logger.error("user_user_teams.txt file not found. Please run Wurd24Scheduler to generate it.")
        return []

# Function to fetch members of each team
async def fetch_team_members(guild, team_name):
    # Example logic to match users based on a naming convention or a lookup list (adjust based on your server setup)
    member_ids = []
    for member in guild.members:
        # Check if the member's nickname or username contains part of the team name
        if any(team_part in (member.display_name or member.name).lower() for team_part in team_name.split('-')):
            member_ids.append(member.id)
    return member_ids

async def check_inactivity():
    while True:
        now = datetime.now(pytz.utc)
        for channel_id, data in channel_activity_tracker.items():
            elapsed_time = (now - data["created_at"]).total_seconds()
            non_responders = [member_id for member_id in data["member_ids"] if member_id not in data["responses"]]

            # Check if a reminder has been sent for this channel and ensure 4-hour interval
            if channel_id in last_reminder_time:
                time_since_last_reminder = (now - last_reminder_time[channel_id]).total_seconds()
                if time_since_last_reminder < 4 * 3600:  # 4 hours
                    continue  # Skip sending another reminder

            # If no recent reminder or 4+ hours have passed, send a new reminder
            guild = bot.get_guild(GUILD_ID)
            channel = guild.get_channel(channel_id)

            if len(non_responders) == 1:
                member = guild.get_member(non_responders[0])
                await channel.send(f"{member.mention}, your opponent is waiting. Please respond.")
            elif len(non_responders) == 2:
                mentions = " ".join([guild.get_member(mid).mention for mid in non_responders])
                await channel.send(f"{mentions}, let's get this game on. Please respond.")

            # Update the last reminder time for the channel
            last_reminder_time[channel_id] = now

        # Check inactivity every 4 hours
        await asyncio.sleep(4 * 3600)

# Create channels for each user-user team with member invites
async def create_user_user_channels(guild):
    user_user_teams = load_user_user_teams()
    for team_name in user_user_teams:
        # Fetch the member IDs associated with the team
        member_ids = await fetch_team_members(guild, team_name)
        # Create the channel for the team and invite members
        await create_channel_helper(guild, team_name=team_name, member_ids=member_ids, message_content=f"Welcome to the {team_name} channel!")


def get_time_zones():
    pt_time = datetime.now(pytz.timezone('US/Pacific'))
    az_time = datetime.now(pytz.timezone('US/Arizona'))
    mtn_time = datetime.now(pytz.timezone('US/Mountain'))
    central_time = datetime.now(pytz.timezone('US/Central'))
    eastern_time = datetime.now(pytz.timezone('US/Eastern'))
    return (pt_time, az_time, mtn_time, central_time, eastern_time)


# Function to split a long message into chunks of a specific size
def split_message(message, max_length=2000):
    # Initialize list to hold split messages
    split_messages = []

    # Split message by return characters
    paragraphs = message.split('\n')
    current_message = ""

    for paragraph in paragraphs:
        # Add the paragraph and a newline to the current message if it stays within max_length
        if len(current_message) + len(paragraph) + 1 <= max_length:
            current_message += paragraph + '\n'
        else:
            # Append the current message to split_messages and start a new one
            split_messages.append(current_message.strip())
            current_message = paragraph + '\n'

    # Append the remaining message part
    if current_message:
        split_messages.append(current_message.strip())

    return split_messages


# Function to check members in a specific channel and save nicknames matching NFL teams
from pathlib import Path

def nicknames_to_users_file():
    """
    Scan the whole guild and write wurd24users.csv with user-controlled teams.
    Exact "starts-with" match against NFL_Teams.csv entries (title case kept).
    Writes to the project directory (next to this .py).
    """
    guild = bot.get_guild(GUILD_ID)
    if guild is None:
        logger.error(f"Guild {GUILD_ID} not found. Not writing wurd24users.csv.")
        return

    # resolve output to project directory
    base_dir = Path(__file__).resolve().parent
    out_path = base_dir / "wurd24users.csv"

    # load canonical team names exactly as in NFL_Teams.csv (title case preserved)
    try:
        with open(base_dir / 'NFL_Teams.csv', 'r', encoding='utf-8') as f:
            teams_exact = [ln.strip() for ln in f if ln.strip()]
    except FileNotFoundError:
        logger.error(f"NFL_Teams.csv not found at {base_dir}.")
        return

    # precompute (UPPER, ORIGINAL) for quick startswith checks
    lookup = [(t.upper(), t) for t in teams_exact]

    taken_original_case = set()

    for m in guild.members:
        if getattr(m, "bot", False):
            continue
        disp = (m.display_name or m.name or "").strip()
        disp_up = disp.upper()
        for up, original in lookup:
            # exact “starts with team name”
            if disp_up.startswith(up):
                taken_original_case.add(original)
                break

    # write result
    try:
        with open(out_path, 'w', encoding='utf-8', newline='') as f:
            for t in sorted(taken_original_case):
                f.write(t + '\n')
        logger.info(f"wurd24users.csv updated with {len(taken_original_case)} team(s) at {out_path}")
    except Exception as e:
        logger.error(f"Failed writing {out_path}: {e}")



@bot.command(name='bot_permissions')
async def bot_permissions(ctx):
    bot_member = ctx.guild.get_member(bot.user.id)  # Get the bot's member object
    permissions = bot_member.guild_permissions  # Get the bot's permissions in the guild

    # Format and display permissions as a list
    permissions_list = [perm for perm, value in permissions if value]
    await ctx.send(f"Bot's Guild Permissions: {', '.join(permissions_list)}")

@bot.command(name='bot_channel_permissions')
async def bot_channel_permissions(ctx):
    permissions = ctx.channel.permissions_for(ctx.guild.me)  # Get bot's permissions in the current channel

    # Format and display permissions as a list
    permissions_list = [perm for perm, value in permissions if value]
    await ctx.send(f"Bot's Permissions in #{ctx.channel.name}: {', '.join(permissions_list)}")
    logger.info(f"bot_permissions command invoked by {ctx.author} (ID: {ctx.author.id}).")

async def get_available_teams_output():
    try:
        with open('wurd24users.csv', 'r') as f:
            listed_teams = {line.strip() for line in f if line.strip()}
    except FileNotFoundError:
        return "wurd24users.csv file not found. Please update teams first."

    missing_teams = set(nfl_teams.keys()) - listed_teams
    if not missing_teams:
        return "No available teams. All teams are taken!"

    # Group missing teams by division
    division_groups = {}
    for team in missing_teams:
        division = nfl_teams[team]
        division_groups.setdefault(division, []).append(team)

    # Identify divisions with the most available teams
    max_missing = max(len(teams) for teams in division_groups.values())
    top_divisions = {
        division: teams for division, teams in division_groups.items()
        if len(teams) == max_missing
    }

    # Format the output
    output_lines = []
    output_lines.append("\nAvailable teams are:\n")
    for division in sorted(top_divisions.keys()):
        output_lines.append(division)
        for team in sorted(top_divisions[division]):
            output_lines.append(f"- {team}")
        output_lines.append("")  # Blank line between divisions

    return "\n".join(output_lines)


@bot.command(name='available_teams')
async def available_teams(ctx):
    nicknames_to_users_file()  # Call function to save users with matching team names.
    teams_message = await get_available_teams_output()
    # Use split_message to avoid hitting Discord's 2000 character limit
    for chunk in split_message(teams_message):
        await ctx.send(chunk)


def is_exact_word(msg_text, word):
    """
    Checks if msg_text exactly matches the specified word.

    Parameters:
    - msg_text (str): The input string to be checked.
    - word (str): The target word to match exactly.

    Returns:
    - bool: True if msg_text is exactly word (case-insensitive), False otherwise.
    """
    # Validate inputs
    if not isinstance(msg_text, str):
        raise TypeError("msg_text must be a string.")
    if not isinstance(word, str):
        raise TypeError("word must be a string.")
    if not word:
        raise ValueError("word must not be an empty string.")

    # Define the regex pattern for exact match
    # Using re.escape to handle any special regex characters in word
    pattern = r'^' + re.escape(word) + r'$'

    # Perform the match using re.fullmatch for exact matching
    match = re.fullmatch(pattern, msg_text, re.IGNORECASE)

    return bool(match)


# ===== BOT.LOG READER =====

LOG_BACKUP_GLOB = "bot.log*"       # we'll read bot.log, bot.log.1, ... (in modified-time order)

# Gate: only Admin role or AUTHORIZED_USERS can use the log reader
def _is_authorized(member) -> bool:
    if any(r.name == ADMIN_ROLE_NAME for r in getattr(member, "roles", [])):
        return True
    try:
        return int(member.id) in AUTHORIZED_USERS
    except Exception:
        return False

# Parse one cohesive entry that looks like:
# Phoenix Time: 09-23-25 06:52 PM
# Channel: lobby-talk (or "Direct Message")
# Username: Foo | ID: 123
# User Message: text...
#
# We detect entries by the "Phoenix Time:" line and collect the following 3 lines.
PT_LINE = "Phoenix Time:"
CH_LINE = "Channel:"
UN_LINE = "Username:"
UM_LINE = "User Message:"

def _iter_log_entries(lines: list[str]):
    """Yield dicts with datetime, forum, username, user_id, message. Tolerant to missing parts."""
    entry = {}
    expect_block = False
    tz_az = pytz.timezone('US/Arizona')

    for raw in lines:
        line = raw.rstrip("\n")
        if line.startswith(PT_LINE):
            # Start new entry
            # Example: "Phoenix Time: 09-23-25 06:52 PM"
            entry = {}
            expect_block = True
            try:
                stamp = line.split(":", 1)[1].strip()
                dt = datetime.strptime(stamp, "%m-%d-%y %I:%M %p")
                entry["dt"] = tz_az.localize(dt)
            except Exception:
                entry["dt"] = None

        elif expect_block and line.startswith(CH_LINE):
            entry["forum"] = line.split(":", 1)[1].strip()

        elif expect_block and line.startswith(UN_LINE):
            # "Username: Name | ID: 123"
            try:
                payload = line.split(":", 1)[1].strip()
                if " | ID:" in payload:
                    uname, uid = payload.split(" | ID:", 1)
                    entry["username"] = uname.strip()
                    entry["user_id"] = uid.strip()
                else:
                    entry["username"] = payload
                    entry["user_id"] = ""
            except Exception:
                entry["username"] = ""
                entry["user_id"] = ""

        elif expect_block and line.startswith(UM_LINE):
            entry["message"] = line.split(":", 1)[1].strip()
            # Emit on message line (our block ends here in your logger)
            yield entry
            entry = {}
            expect_block = False

    # no trailing yield; block closes only when we see User Message

def _read_recent_log_lines(days_back: int) -> list[str]:
    """Read bot.log and its backups newest->oldest; return lines within days_back window."""
    files = sorted(
        glob.glob(LOG_BACKUP_GLOB),
        key=lambda p: os.path.getmtime(p),
        reverse=True
    )

    lines_kept = []
    # Read newest first; we’ll still filter by date after parsing
    for path in files:
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                lines_kept.extend(f.readlines())
        except Exception:
            continue
    # We return raw lines; time filtering happens after parsing so we don’t miss multi-line blocks
    return lines_kept

def _group_entries(entries):
    """Group into OrderedDict[date_str][forum] -> list[entry]. date_str in AZ local."""
    tz_az = pytz.timezone('US/Arizona')
    by_date = defaultdict(lambda: defaultdict(list))
    for e in entries:
        dt = e.get("dt")
        if not dt:
            continue
        d = dt.astimezone(tz_az).date()
        date_key = d.strftime("%Y-%m-%d (%a)")
        forum = e.get("forum") or "Unknown"
        by_date[date_key][forum].append(e)
    # Sorted by date ascending
    ordered = OrderedDict(sorted(by_date.items(), key=lambda kv: kv[0]))
    # Sort each forum’s entries by time
    for date_key in ordered:
        for forum in ordered[date_key]:
            ordered[date_key][forum].sort(key=lambda e: e["dt"])
    return ordered

def _sanitize_message(msg: str) -> str:
    """
    Remove Discord embeds by breaking URLs.
    Example: 'https://twitch.tv/foo' -> '<https://twitch.tv/foo>'
    (wrapped in angle brackets disables embedding)
    """
    if not msg:
        return msg
    # Wrap all http/https URLs in < >
    return re.sub(r'(https?://\S+)', r'<\1>', msg)

def _render_day_all_forums(grouped, the_date: str):
    """
    Render ALL forums for a specific date (YYYY-MM-DD), excluding 'Direct Message'.
    Sections:
      # Date header
      ## Forum name — N messages
         - HH:MM AM/PM — user: message
    """
    tz_az = pytz.timezone('US/Arizona')
    date_key_prefix = the_date.strip()  # our grouped keys look like "YYYY-MM-DD (Dow)"

    # Find the matching date key
    day_forums = None
    for date_key, forum_map in grouped.items():
        if date_key.startswith(date_key_prefix):
            day_forums = forum_map
            break

    if day_forums is None:
        return f"No messages found on {the_date}."

    # Build output: sort forums by message count desc, then name asc
    forums = []
    for forum, items in day_forums.items():
        if forum.lower() == "direct message":
            continue
        forums.append((forum, items))
    if not forums:
        return f"No server-channel messages on {the_date}."

    forums.sort(key=lambda x: (-len(x[1]), x[0]))

    lines = [f"__**{the_date}**__"]
    for forum, items in forums:
        # items should already be time-sorted by _group_entries
        lines.append(f"**{forum}** — {len(items)} message(s)")
        for e in items:
            t = e["dt"].astimezone(tz_az).strftime("%I:%M %p").lstrip("0")
            user = e.get("username", "")
            msg = _sanitize_message((e.get("message") or "").strip()) or "(no content)"
            lines.append(f"- **{t}** — {user}: {msg}")
        lines.append("")  # blank line between forums

    return "\n".join(lines).rstrip()

# ---- Forum filtering / rendering helpers ----

DEFAULT_LOG_LOOKBACK_DAYS = int(os.getenv("LOG_LOOKBACK_DAYS", "14"))
DEFAULT_FORUM_LIMIT = int(os.getenv("LOGS_FORUM_DEFAULT_LIMIT", "100"))

def _normalize_forum_name(s: str) -> str:
    if not s:
        return ""
    s = s.strip().lower()
    s = s.replace("_", "-").replace(" ", "-")
    # collapse duplicate hyphens
    s = re.sub(r"-{2,}", "-", s)
    return s

def _entries_for_forum(entries: list, forum_query: str):
    """
    Return (filtered_entries, canonical_forum_name or forum_query)
    Matches exact normalized name first; otherwise substring match on normalized names.
    """
    qn = _normalize_forum_name(forum_query)
    if not qn:
        return [], forum_query

    # Build map: forum_name -> [entries...]
    by_forum = {}
    for e in entries:
        f = e.get("forum") or ""
        by_forum.setdefault(f, []).append(e)

    # Score forums for best match
    scored = []  # (score, forum_name)
    for forum_name in by_forum.keys():
        fn = _normalize_forum_name(forum_name)
        score = 0
        if fn == qn:
            score = 3
        elif fn.startswith(qn):
            score = 2
        elif qn in fn:
            score = 1
        if score:
            scored.append((score, forum_name))

    if not scored:
        return [], forum_query

    # Pick best score, tie-break by shorter name
    best_score = max(s for s, _ in scored)
    candidates = [name for s, name in scored if s == best_score]
    candidates.sort(key=len)
    chosen = candidates[0]

    return by_forum.get(chosen, []), chosen

def _render_forum(entries: list, forum_query: str, limit: int = DEFAULT_FORUM_LIMIT, only_date: str | None = None) -> str:
    """
    entries: list from _iter_log_entries (each has dt, forum, username, message)
    forum_query: name or partial (e.g., 'bills-panthers')
    limit: last N messages across the lookback window
    only_date: optional 'YYYY-MM-DD' to restrict to that day (AZ time)
    """
    tz_az = pytz.timezone('US/Arizona')

    # Filter to forum
    f_entries, canonical = _entries_for_forum(entries, forum_query)
    if not f_entries:
        return f"No messages found for forum '{forum_query}' in the last {DEFAULT_LOG_LOOKBACK_DAYS} day(s)."

    # Optional date filter
    if only_date:
        def same_day(e):
            if not e.get("dt"): return False
            return e["dt"].astimezone(tz_az).strftime("%Y-%m-%d") == only_date
        f_entries = [e for e in f_entries if same_day(e)]

        if not f_entries:
            return f"No messages found for **{canonical}** on {only_date}."

    # Sort by time, take the last `limit`
    f_entries.sort(key=lambda e: e.get("dt") or datetime.min.replace(tzinfo=tz_az))
    if limit and limit > 0:
        f_entries = f_entries[-limit:]

    # Group by date for nice headings
    by_date = defaultdict(list)
    for e in f_entries:
        dkey = e["dt"].astimezone(tz_az).date().strftime("%Y-%m-%d (%a)") if e.get("dt") else "Unknown Date"
        by_date[dkey].append(e)

    # Build output
    lines = [f"__**Forum:**__ **{canonical}** — {len(f_entries)} message(s)"]
    if only_date:
        lines[0] += f" on {only_date}"
    lines.append("")  # blank

    for dkey in sorted(by_date.keys()):
        lines.append(f"**{dkey}**")
        for e in by_date[dkey]:
            t = e["dt"].astimezone(tz_az).strftime("%I:%M %p").lstrip("0") if e.get("dt") else "??:??"
            user = e.get("username", "")
            msg = _sanitize_message((e.get("message") or "").strip()) or "(no content)"
            lines.append(f"- **{t}** — {user}: {msg}")
        lines.append("")

    return "\n".join(lines).rstrip()


@bot.command(name="logs")
async def logs_cmd(ctx, *, rest: str = ""):
    """
    View logs by date or by forum.

    Usage:
      • !logs date=YYYY-MM-DD [here]
      • !logs forum=bills-panthers [limit=100] [date=YYYY-MM-DD] [here]
      • !logs bills-panthers            # shorthand for forum=
      • !logs bills-panthers here
      • !logs forum="bills panthers"    # spaces ok if quoted

    Notes:
      - Replies by DM unless you add 'here'.
      - Looks back DEFAULT_LOG_LOOKBACK_DAYS across bot.log files.
      - Limit defaults to DEFAULT_FORUM_LIMIT (env overridable).
    """
    author = ctx.author
    text = (rest or "").strip()

    # --- parse 'here' flag first ---
    want_here = bool(re.search(r'(^|\s)here($|\s)', text, flags=re.IGNORECASE))
    text = re.sub(r'(^|\s)here($|\s)', ' ', text, flags=re.IGNORECASE).strip()

    # --- parse args ---
    m_date   = re.search(r'date\s*=\s*(\d{4}-\d{2}-\d{2})', text, flags=re.IGNORECASE)
    m_forum  = re.search(r'forum\s*=\s*("?)(.+?)\1($|\s)', text, flags=re.IGNORECASE)  # supports forum="bills panthers"
    m_limit  = re.search(r'limit\s*=\s*(\d{1,4})', text, flags=re.IGNORECASE)

    only_date = m_date.group(1) if m_date else None
    limit = int(m_limit.group(1)) if m_limit else DEFAULT_FORUM_LIMIT

    forum_query = None
    if m_forum:
        forum_query = m_forum.group(2).strip()
    else:
        # if user typed a bare token and NOT a pure date usage, treat it as forum shorthand
        # (e.g., "!logs bills-panthers")
        if text and not re.search(r'\bdate\s*=', text, flags=re.IGNORECASE):
            forum_query = text.strip()

    # --- auth check for all logs ---
    if not _is_authorized(author):
        try:
            await author.send("Sorry, you’re not authorized to use the log reader.")
        except:
            if ctx.guild:
                await ctx.reply("Sorry, you’re not authorized to use the log reader.")
        return

    # --- acknowledge if in-guild and not forcing 'here' ---
    if ctx.guild and not want_here:
        try:
            await ctx.reply("I’m sending the log info to your DMs…")
        except:
            pass

    # --- read logs once ---
    raw_lines = _read_recent_log_lines(DEFAULT_LOG_LOOKBACK_DAYS)
    entries = list(_iter_log_entries(raw_lines))

    chunks = []

    if forum_query:
        # Forum mode
        header = f"**bot.log — forum: {forum_query}**"
        if only_date:
            header += f" — {only_date}"
        body = _render_forum(entries, forum_query=forum_query, limit=limit, only_date=only_date)
        chunks = [header] + split_message(body)
    else:
        # Date mode (original behavior)
        if not only_date:
            usage = "Usage:\n  `!logs date=YYYY-MM-DD`\nOR\n  `!logs forum=NAME [limit=N] [date=YYYY-MM-DD]`"
            try:
                if ctx.guild and not want_here:
                    await author.send(usage)
                else:
                    await ctx.reply(usage)
            except:
                pass
            return

        grouped = _group_entries([e for e in entries if e.get("dt")])
        header = f"**bot.log — {only_date} — all forums**"
        body = _render_day_all_forums(grouped, the_date=only_date)
        chunks = [header] + split_message(body)

    # --- deliver (DM first unless 'here') ---
    delivered = False
    if not want_here:
        try:
            for c in chunks:
                await author.send(c)
            delivered = True
        except Exception as dm_err:
            logger.warning(f"!logs DM failed; falling back to channel: {dm_err}")

    if not delivered:
        try:
            for c in chunks:
                await ctx.reply(c)
            if not want_here:
                await ctx.reply("*(Heads-up: I couldn’t DM you. Check Privacy Settings → Allow DMs from server members.)*")
        except Exception as ch_err:
            logger.error(f"!logs delivery failed in channel as well: {ch_err}")

# ===== END BOT.LOG READER =====


# Event handler for bot login and startup details
@bot.event
async def on_ready():
    try:
        guild = bot.get_guild(GUILD_ID)
        if not guild:
            print(f"Guild with ID {GUILD_ID} not found.")
            return
        logger.info(f'Logged in as {bot.user.name}')
    except Exception as e:
        logger.error(f"Error during bot startup: {e}")

    # Start the AP reminder loop
    bot.loop.create_task(ap_return_reminder_loop())

    # Start the AP auto-post watcher
    bot.loop.create_task(ap_autopost_watcher())

    # start inactivity loop
    # bot.loop.create_task(check_inactivity())  #This is Turned Off for now

    logger.info(f'\nLogged in as {bot.user.name}')
    logger.info(f'User ID: {bot.user.id}')

    channels = bot.get_all_channels()
    for channel in channels:
        logger.info(f"Channel Name: {channel.name}, ID: {channel.id}, Category: {channel.category}")

# Event to welcome new members to the server
@bot.event
async def on_member_join(member):
    channel = member.guild.system_channel  # Get the system channel
    if channel is not None:
        welcome_message = (
            f"Welcome {member.mention} to WURD!!! :football:\n\n"
            "Check out the https://discord.com/channels/1144688789248282804/1144692100697423972.\n"
            "If you have any questions, just post in lobby-talk forum here and we'll be glad to answer.\n\n"
            "If you haven't yet - fill out the WURD application at https://wurd-madden.com/recruits/new.\n\n"
            "Communication is important! Tap on WURD :trophy: CHAMPIONSHIP and notifications will pop up.\n"
            "Choose All Messages or Only @mentions\n\n"
            "Let us know which team you would like to play for. :smiley:\n"
        )
        teams_message = await get_available_teams_output()
        full_message = welcome_message + teams_message
        # Send the message in chunks if necessary
        for chunk in split_message(full_message):
            await channel.send(chunk)


# Event handler for processing incoming messages
@bot.event
async def on_message(msg):
    global _last_gg_alert_ts

    # ignore all bot messages except *our own DM*
    if msg.author == bot.user and msg.guild is None:
        # this is the bot's own DM going out
        _last_gg_alert_ts = time.monotonic()
        logger.info("Cooldown started because bot sent a GG DM.")
        return

    msg_text = str(msg.content).lower()
    username = msg.author.display_name
    userid = msg.author.id
    user_message = str(msg.content)
    channel = "Direct Message" if msg.guild is None else str(msg.channel)  # Distinguish DM from server messages

    # log the time the message was sent
    phoenix_time = datetime.now(pytz.timezone('US/Arizona'))
    logger.info(f"Phoenix Time: {phoenix_time.strftime('%m-%d-%y %I:%M %p')}")

    # Log message metadata
    logger.info(f"Channel: {channel}")
    logger.info(f"Username: {username} | ID: {userid}")
    logger.info(f"User Message: {user_message}\n")

    # Process command if itâ€™s not from the bot itself
    if msg.author != bot.user:
        # Update to track member responses
        if msg.guild and msg.channel.id in channel_activity_tracker:
            tracker = channel_activity_tracker[msg.channel.id]
            if msg.author.id in tracker["member_ids"]:
                tracker["responses"].add(msg.author.id)  # Mark the member as having responded
        await bot.process_commands(msg)  # Ensure bot commands in on_message are handled

    # --- Advance channel watcher: cache WEEK + matchups -------------------------
    try:
        if msg.guild and ADVANCE_CHANNEL_ID and msg.channel.id == ADVANCE_CHANNEL_ID:
            wk, pairs, mapping = _parse_advance_block(msg.content or "")
            if wk and pairs:
                global _current_week, _current_pairs, _current_matchups
                _current_week = wk
                _current_pairs = pairs
                _current_matchups = mapping
                logger.info(f"Advance learned: WEEK={wk}, games={len(pairs)}")
    except Exception as e:
        logger.warning(f"advance parse failed: {e}")

    # ---------------------------------------------------------------------------

    # --- Text-channel flyer trigger for game-streams ----------------------------
    try:
        if msg.guild and GAME_STREAMS_CHANNEL_ID and msg.channel.id == GAME_STREAMS_CHANNEL_ID:
            link = find_stream_link(msg.content or "")
            if not link:
                # nothing to do if there's no stream link
                pass
            else:
                week, t1, t2 = parse_title_for_week_and_teams(msg.content or "")
                if not week:
                    week = parse_week_token(msg.content or "")
                if not week and _current_week:
                    week = _current_week

                # skip preseason entirely
                if (week or 0) < 1:
                    return

                # 3) Fallback: "TEAM vs TEAM" in free text
                if not (t1 and t2):
                    m = re.search(r"\b([A-Z][A-Z ]+?)\s*(?:vs|[-–])\s*([A-Z][A-Z ]+)\b", (msg.content or "").upper())
                    if m:
                        t1, t2 = canonical_team(m.group(1)), canonical_team(m.group(2))

                # 4) Still missing something? Use the author's nickname to infer their team,
                #    then pull the opponent from the advance mapping we learned.
                if _current_matchups and (not t1 or not t2):
                    author_team = extract_team_from_nick(msg.author.display_name or "")
                    if author_team:
                        # If we have exactly one team, make sure it's aligned with the author's team
                        if not t1 and not t2:
                            # Neither found in text -> start from author
                            opp = _current_matchups.get(author_team)
                            if opp:
                                # Preserve left/right as posted in the advance list if possible
                                for L, R in _current_pairs:
                                    if {L, R} == {author_team, opp}:
                                        t1, t2 = L, R
                                        break
                                if not (t1 and t2):
                                    t1, t2 = author_team, opp
                        elif t1 and not t2:
                            # We found t1 in text; if t1 == author, find their opponent
                            opp = _current_matchups.get(t1) or _current_matchups.get(author_team)
                            if opp:
                                # Keep advance ordering if we can
                                for L, R in _current_pairs:
                                    if {L, R} == {t1, opp}:
                                        t1, t2 = L, R
                                        break
                                if not (t1 and t2):
                                    t2 = opp
                        elif t2 and not t1:
                            opp = _current_matchups.get(t2) or _current_matchups.get(author_team)
                            if opp:
                                for L, R in _current_pairs:
                                    if {L, R} == {t2, opp}:
                                        t1, t2 = L, R
                                        break
                                if not (t1 and t2):
                                    t1 = opp

                # 5) Final guard: if we have both teams, render & post; else ask for a hint
                if t1 and t2:
                    flyer_path = render_flyer_png(
                        week or 0, t1, t2,
                        streamer=msg.author.display_name,
                        link=link
                    )
                    await post_flyer_with_everyone(
                        msg.channel, flyer_path,
                        week or 0, t1, t2,
                        msg.author.display_name, link
                    )
                    # Registry uses a sorted key; this prevents duplicate flyers for same matchup.
                    registry_put(week or 0, t1, t2, {"message_id": None})
                else:
                    logger.warning("The bot was not able to post the flyer in game-streams")
                    # await msg.channel.send(
                    #     "✅ I found your link, but the flyer is still a work in progress...\n"
                    # )
    except Exception as e:
        logger.warning(f"game-streams text handler failed: {e}")
    # --- end text-channel flyer trigger -----------------------------------------


    # PUT ONE WORD COMMANDS AFTER THIS STATEMENT THAT EVERYONE CAN USE

    # Display current times in various time zones if message contains "time"
    if is_exact_word(msg_text, 'time'):
        pt_time, az_time, mtn_time, central_time, eastern_time = get_time_zones()
        await msg.author.send(f'{pt_time.strftime("%I:%M %p-PT")}')
        await msg.author.send(f'{az_time.strftime("%I:%M %p-AZ")} <- server time (No DST)')
        await msg.author.send(f'{mtn_time.strftime("%I:%M %p-MT")}')
        await msg.author.send(f'{central_time.strftime("%I:%M %p-CT")}')
        await msg.author.send(f'{eastern_time.strftime("%I:%M %p-ET")}')

    # ===== GG DETECTOR (category + text channels) =====
    try:
        if msg.guild and _is_in_target_game_channel(msg.channel):
            if GG_WORD_RE.search(msg.content or ""):
                now = time.monotonic()
                if now - _last_gg_alert_ts >= GG_COOLDOWN_SEC:
                    # Build a nice alert with a jump link
                    alert_text = (
                        f"[WURD GG] GG detected in #{msg.channel.name} by {msg.author.display_name}\n"
                        f"Jump: {msg.jump_url}"
                    )

                    sent_ok = False

                    # 1) Prefer posting to a specific server channel (so phones get push immediately)
                    if GG_ALERT_CHANNEL_ID:
                        ch = bot.get_channel(GG_ALERT_CHANNEL_ID)
                        if ch:
                            mention = f"<@{GG_ALERT_MENTION_USER_ID}>" if GG_ALERT_MENTION_USER_ID else ""
                            try:
                                await ch.send(f"{mention} {alert_text}".strip())
                                sent_ok = True
                                logger.info("GG alert sent to GG_ALERT_CHANNEL_ID.")
                            except Exception as e:
                                logger.warning(f"GG alert channel send failed: {e}")

                    if sent_ok:
                        _last_gg_alert_ts = now
                    else:
                        logger.warning("GG alert not sent (no channel/DM path worked).")
                else:
                    logger.info("GG detected but still in cooldown, skipping alert.")
    except Exception as e:
        logger.warning(f"GG detector error: {e}")
    # ===== END GG DETECTOR =====


    # Check for messages in Direct Messages or containing specific keywords
    if 'Direct Message' in channel:

        # ONLY AUTHORIZED_USERS CAN GO PASS HERE
        if msg.author.id not in AUTHORIZED_USERS:
            return

        nicknames_to_users_file()  # Call function to save users with matching team names.  This updates discord teams to wurd24users.csv

        # checking 'week' plus one or two numbers or 'all'
        pattern_week = r"^week \d{1,2}$"
        pattern_pre = r"^pre\s*[123]$"  # NEW: pre 1, pre 2, pre 3
        pattern_all = r"^all$"

        if re.fullmatch(pattern_week, msg_text) or re.fullmatch(pattern_pre, msg_text) or re.fullmatch(pattern_all,
                                                                                                       msg_text):
            # Normalize what we pass into the scheduler
            norm = msg_text
            if re.fullmatch(pattern_pre, msg_text):
                # If your Wurd24Scheduler expects uppercase "PRE 1" tokens:
                norm = msg_text.replace("pre", "PRE").upper()  # -> "PRE 1"
                # If instead your scheduler expects negative "week -3/-2/-1",
                # you can convert here instead (uncomment if needed):
                # n = int(re.search(r"\d", msg_text).group(0))   # 1..3
                # week_map = {1: -3, 2: -2, 3: -1}
                # norm = f"week {week_map[n]}"

            week_schedule = wrd.wurd_sched_main(norm)
            if TEST:
                print(f'--------TEST PRINT--------------\n{week_schedule}\n------------TEST PRINT---------')
            else:
                # schedule forum ID (for 'all')  vs  advance forum ID (for a single week/pre)
                channel_id = 1290487933131952138 if 'all' in msg_text else 1149401984466681856
                channel = bot.get_channel(channel_id)

                for chunk in split_message(week_schedule):
                    await channel.send(chunk)

                # For both 'week N' *and* 'pre N', build the game forums
                if any(k in msg_text for k in ("week", "pre")):
                    guild = bot.get_guild(GUILD_ID)
                    await delete_category_channels(guild)  # clear previous week’s forums
                    channel_activity_tracker.clear()
                    await create_user_user_channels(guild)  # create new matchup forums

    # Regex search for time patterns in the message
    player_msg_time = re.search(r'\d{1,2}:\d{2}', msg.content)

    # # Respond with time if a valid time pattern is found in the message
    # if player_msg_time:
    #     await msg.channel.send(f'[testing]The time is {player_msg_time.group()}\n')




# Run the bot with the provided token
bot.run(token)

