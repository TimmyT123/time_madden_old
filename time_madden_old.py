# This file is time_madden_old.py, dev on the windows laptop and automatically runs on the raspberrync pi
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
import requests
import hashlib

from nextcord import File, AllowedMentions

from collections import defaultdict, OrderedDict
import glob

from logging.handlers import RotatingFileHandler
from nfl_teams_divisions import nfl_teams  # Import the complete NFL teams mapping

from ai_bot.ai_handler import generate_ai_reply
from ai_bot.ai_responses import is_bot_mentioned
from ai_bot.ai_memory import update_last_message_time

from ai_bot.lobby_bot import lobby_personality_loop, load_ai_advance_info
from flyers.pipeline import handle_game_stream_post
from flyers.renderer import generate_flyer_with_fallback
from flyers.ai_generator import build_flyer_caption, build_flyer_image_prompt
from flyers.registry import registry_has, registry_put
from flyers.poster import post_flyer_with_everyone, watch_first_link_and_edit

from utils.file_utils import (
    load_week_state,
    save_week_state,
    get_current_week_and_matchups_from_file,
    load_playtime_map,
    save_playtime_map,
    load_gotw_config_from_file,
    load_gotw_state_from_file,
    save_gotw_state_to_file,
    save_week_cache_if_changed,
    load_notified_set,
    save_notified_set,
    load_last_ap_state_from_file,
    save_ap_state_to_file,
)

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
GG_COOLDOWN_SEC = 600  # 10 minutes
_last_gg_alert_ts = 0.0

EXPORT_RETRY_IN_PROGRESS = False
EXPORT_MAX_ATTEMPTS = 5
EXPORT_RETRY_DELAY = 120  # seconds

personality_loop_started = False

GG_WORD_RE = re.compile(r"\bggs?\b", re.IGNORECASE)

# Use YOUR provided IDs from .env (see sample below)
GG_GUILD_ID = int(os.getenv("GG_GUILD_ID", "0") or 0)
GG_CATEGORY_ID = int(os.getenv("GG_CATEGORY_ID", "0") or 0)  # category that holds your game threads/channels
GG_ALERT_CHANNEL_ID = int(os.getenv("GG_ALERT_CHANNEL_ID", "0") or 0)  # where the bot will post the alert
GG_ALERT_MENTION_USER_ID = int(os.getenv("GG_ALERT_MENTION_USER_ID", "0") or 0)  # who to @mention in the alert

GAME_STREAMS_FORUM_ID = int(os.getenv("GAME_STREAMS_FORUM_ID", "0") or 0)
GAME_STREAMS_CHANNEL_ID = int(os.getenv("GAME_STREAMS_CHANNEL_ID", "0") or 0)
LOGOS_DIR = os.getenv("LOGOS_DIR", "flyers/assets/logos")
FLYER_OUT_DIR = os.getenv("FLYER_OUT_DIR", "./static/flyers")
EVERYONE_MENTIONS = AllowedMentions(everyone=True, users=False, roles=False, replied_user=False)

TEAM_NAME_TO_ID = {}

WURD_LOGO_PATH = "flyers/assets/wurd_logo.png"

ADVANCE_INFO_FILE = "/home/pi/projects/advance_info.json"


# =========================
# GAMES OF THE WEEK SYSTEM
# =========================

GOTW_CONFIG_FILE = "data/gotw_config.json"
GOTW_STATE_FILE = "data/gotw_state.json"

_current_gotw_pairs = set()

PLAYOFF_WEEKS = {19, 20, 21, 23}   # WC, Div, Conf, Super Bowl

def should_use_ai_flyer(week: int | None, t1: str, t2: str) -> bool:
    if not week:
        return False

    # Playoffs + Super Bowl ALWAYS AI
    if week in PLAYOFF_WEEKS:
        return True

    pair = tuple(sorted((t1, t2)))
    if pair in _current_gotw_pairs:
        return True

    return False

os.makedirs(os.path.dirname(FLYER_OUT_DIR), exist_ok=True)




def same_division_check(teamA, teamB):
    # Convert canonical uppercase names to proper case
    teamA_proper = teamA.title()
    teamB_proper = teamB.title()

    divA = nfl_teams.get(teamA_proper)
    divB = nfl_teams.get(teamB_proper)

    return divA is not None and divA == divB

async def select_games_of_the_week():
    global _current_gotw_pairs

    config = load_gotw_config()

    if not config.get("enabled", True):
        print("[GOTW] Disabled in config.")
        return

    if _current_week is None:
        print("[GOTW] No current week loaded yet.")
        return

    if _current_week < config.get("start_week", 5):
        print("[GOTW] Week below start threshold.")
        return

    if _current_week in PLAYOFF_WEEKS:
        print("[GOTW] Skipping playoffs.")
        return

    if not _current_pairs:
        print("[GOTW] No matchups loaded yet.")
        return

    state = load_gotw_state()

    if state.get("last_week_posted") == _current_week:
        print("[GOTW] Already posted for this week.")

        saved_pairs = state.get("pairs", [])
        _current_gotw_pairs.clear()
        for p in saved_pairs:
            _current_gotw_pairs.add(tuple(sorted(p)))

        print(f"[GOTW] Restored {len(_current_gotw_pairs)} GOTW pairs from state.")
        return

    # ---- fetch standings ----
    try:
        resp = requests.get(f"{API_BASE_URL}/teams", timeout=5)
        resp.raise_for_status()
        teams_data = resp.json()
    except Exception as e:
        print(f"[GOTW] Failed to fetch standings: {e}")
        return

    team_records = {}
    for team in teams_data:
        wins = team.get("wins", 0)
        losses = team.get("losses", 0)
        ties = team.get("ties", 0)
        games = wins + losses + ties
        pct = wins / games if games > 0 else 0
        team_records[(team.get("name") or "").upper()] = (wins, losses, pct)

    # ---- resolve team->member ----
    guild = bot.get_guild(GUILD_ID)
    if not guild:
        print("[GOTW] Guild not found.")
        return

    team_to_member = {}
    for m in guild.members:
        if getattr(m, "bot", False):
            continue
        t = extract_team_from_nick(m.display_name or "")
        if t:
            team_to_member[t] = m

    ap_list = load_ap_users()

    scored_games = []

    for teamA, teamB in _current_pairs:

        teamA = canonical_team(teamA)
        teamB = canonical_team(teamB)

        mA = team_to_member.get(teamA)
        mB = team_to_member.get(teamB)

        print(f"[CHECK] {teamA} vs {teamB}")  # DEBUG
        print(f"  mA: {bool(mA)}, mB: {bool(mB)}")  # DEBUG

        print(f"  AP: {is_on_ap(mA.id, ap_list) if mA else 'N/A'}")  # DEBUG

        # Must be user-user
        if not mA or not mB:
            continue

        # Skip AP users
        if is_on_ap(mA.id, ap_list) or is_on_ap(mB.id, ap_list):
            continue

        recA = team_records.get(teamA)
        recB = team_records.get(teamB)

        print(f"  recA: {recA}, recB: {recB}")  # DEBUG

        if not recA or not recB:
            continue

        pctA = recA[2]
        pctB = recB[2]

        print(f"  pctA: {pctA}, pctB: {pctB}")  # DEBUG
        # 🔥 HARD RULE: both teams must be .450+
        if pctA < 0.45 or pctB < 0.45:
            continue

        # 🔥 Remove big mismatches
        diff = abs(pctA - pctB)
        print(f"  diff: {diff}")  # DEBUG
        if diff >= 0.50:
            continue

        combined_strength = pctA + pctB
        closeness = (1 - diff) ** 2  # non-linear reward for close records

        both_strong = 1 if (pctA >= 0.6 and pctB >= 0.6) else 0
        same_division = 1 if same_division_check(teamA, teamB) else 0

        score = (
            (combined_strength * 50) +
            (closeness * 20) +
            (20) +                 # both winning guaranteed at this point
            (both_strong * 15) +
            (same_division * 10)
        )

        scored_games.append((score, teamA, teamB))

    scored_games.sort(key=lambda x: x[0], reverse=True)

    max_games = int(config.get("max_games", 5))
    selected = scored_games[:max_games]

    if not selected:
        print("[GOTW] No eligible games found.")
        return

    _current_gotw_pairs = set(tuple(sorted((a, b))) for _, a, b in selected)

    await post_gotw_message()

    save_gotw_state({
        "last_week_posted": _current_week,
        "pairs": [list(p) for p in _current_gotw_pairs]
    })

async def post_gotw_message():
    channel = bot.get_channel(GAME_STREAMS_CHANNEL_ID)
    if not channel:
        print("[GOTW] game-streams channel not found.")
        return

    lines = [
        "🏆━━━━━━━━━━━━━━━━━━━━━━",
        f"WURD • WEEK {_current_week}",
        "GAMES OF THE WEEK",
        "━━━━━━━━━━━━━━━━━━━━━━🏆",
        ""
    ]

    for teamA, teamB in sorted(_current_gotw_pairs):
        lines.append(f"🔥 {teamA} vs {teamB}")

    lines.append("")
    lines.append("🎥 These games will receive AI flyers if broadcasted.")

    msg = await channel.send("\n".join(lines))

    # 🔥 PIN MESSAGE
    try:
        await msg.pin()
        print("[GOTW] Message pinned successfully.")
    except Exception as e:
        print(f"[GOTW] Failed to pin message: {e}")

async def schedule_games_of_the_week():
    config = load_gotw_config()
    delay = config.get("delay_seconds", 480)
    await asyncio.sleep(delay)
    await select_games_of_the_week()

async def rebuild_channel_activity():
    guild = bot.get_guild(GUILD_ID)
    if not guild:
        return

    category = guild.get_channel(CATEGORY_ID)
    if not category or not isinstance(category, nextcord.CategoryChannel):
        return

    for ch in category.text_channels:
        # 🔥 Always rebuild tracker from explicit member overwrites
        member_ids = []

        for target, overwrite in ch.overwrites.items():
            if (
                isinstance(target, nextcord.Member)
                and overwrite.read_messages is True
                and not target.bot
            ):
                member_ids.append(target.id)

        tracker = {
            "created_at": datetime.now(pytz.utc),
            "member_ids": member_ids,
            "responses": set()
        }

        channel_activity_tracker[ch.id] = tracker

        # 🔎 Rebuild response set from message history
        try:
            async for msg in ch.history(limit=100):
                if msg.author.id in tracker["member_ids"]:
                    tracker["responses"].add(msg.author.id)
        except Exception as e:
            logger.warning(f"History scan failed for {ch.name}: {e}")

async def pre_advance_reminder_loop():
    await asyncio.sleep(15)

    while True:
        try:
            st = _load_week_state()

            week = int(st.get("week", 0))
            matchups = st.get("matchups", [])
            pre_sent = st.get("pre_reminder_sent", False)
            advance_time_str = st.get("advance_time")

            # Only run for regular season
            if not week or week < 1 or week > 18:
                await asyncio.sleep(60)
                continue

            if not advance_time_str:
                await asyncio.sleep(60)
                continue

            tz = pytz.timezone("US/Arizona")
            now = datetime.now(tz)

            # 🔥 Convert stored advance time
            advance_time = datetime.fromisoformat(advance_time_str).astimezone(tz)

            # 🔥 HARD RULE: 23 hours AFTER advance
            reminder_time = advance_time + timedelta(hours=23)

            # logger.info(
            #     "[24H DEBUG] week=%s | pre_sent=%s | advance_time_az=%s | reminder_time_az=%s | now_az=%s",
            #     week,
            #     pre_sent,
            #     advance_time.strftime("%Y-%m-%d %I:%M:%S %p %Z"),
            #     reminder_time.strftime("%Y-%m-%d %I:%M:%S %p %Z"),
            #     now.strftime("%Y-%m-%d %I:%M:%S %p %Z"),
            # )

            # Only fire once
            if now >= reminder_time and not pre_sent:

                guild = bot.get_guild(GUILD_ID)
                category = guild.get_channel(CATEGORY_ID)

                for ch in category.text_channels:
                    tracker = channel_activity_tracker.get(ch.id)
                    if not tracker:
                        continue

                    member_ids = tracker.get("member_ids", [])
                    if not member_ids:
                        continue

                    ap_list = load_ap_users()

                    # 🔥 Check AP status
                    ap_users_in_channel = [
                        mid for mid in member_ids
                        if is_on_ap(mid, ap_list)
                    ]

                    # 🚫 Skip if ANY user is on AP
                    if ap_users_in_channel:
                        logger.info(f"[24H REMINDER SKIP] {ch.name} has AP user(s), skipping.")
                        continue

                    # Normal non-responder logic
                    non_responders = [
                        mid for mid in member_ids
                        if mid not in tracker["responses"]
                    ]

                    if not non_responders:
                        continue

                    mentions = " ".join(
                        guild.get_member(mid).mention
                        for mid in non_responders
                        if guild.get_member(mid)
                    )

                    await ch.send(
                        f"🔔 **24-Hour Scheduling Reminder**\n"
                        f"{mentions}\n"
                        "Advance is approaching tomorrow.\n"
                        "Please confirm scheduling.\n"
                        "Failure to communicate may result in AP status.",
                        allowed_mentions=AllowedMentions(users=True)
                    )

                    await asyncio.sleep(1.2)

                # 🔒 Mark as sent (prevents spam)
                _save_week_state(
                    week,
                    matchups,
                    pre_sent=True,
                    advance_time=advance_time_str
                )

            await asyncio.sleep(60)

        except Exception as e:
            logger.warning(f"pre_advance_reminder_loop error: {e}")
            await asyncio.sleep(120)

def write_advance_file(advance_dt, week):
    try:
        data = {
            "week": week,
            "advance_time_iso": advance_dt.isoformat(),
            "advance_display": advance_dt.strftime("%A, %b %d @ ~%I:%M %p AZ"),
            "status_text": f"Next Advance: {advance_dt.strftime('%A, %b %d @ ~%I:%M %p AZ')}",
            "updated_at": datetime.now(pytz.timezone("US/Arizona")).isoformat()
        }

        tmp_file = ADVANCE_INFO_FILE + ".tmp"

        with open(tmp_file, "w") as f:
            json.dump(data, f, indent=2)

        os.replace(tmp_file, ADVANCE_INFO_FILE)

        logger.info(f"Advance file updated: {ADVANCE_INFO_FILE}")

    except Exception as e:
        logger.error(f"Failed to write advance file: {e}")

def load_team_id_mapping():
    global TEAM_NAME_TO_ID
    try:
        resp = requests.get("http://127.0.0.1:5000/api/teams", timeout=5)
        resp.raise_for_status()

        teams = resp.json()
        mapping = {}

        for team in teams:
            name = team.get("name") or team.get("displayName")
            team_id = team.get("teamId") or team.get("id")

            if name and team_id is not None:
                full = name.upper()
                short = name.upper().split()[-1]

                mapping[full] = str(team_id)
                mapping[short] = str(team_id)

        TEAM_NAME_TO_ID = mapping
        logger.info(f"Loaded {len(TEAM_NAME_TO_ID)} team ID mappings")

    except Exception as e:
        logger.error(f"Failed to load team ID mapping: {e}")
        TEAM_NAME_TO_ID = {}

#--------------chatgpt flyers--------------

FLYER_API_BASE = "http://127.0.0.1:5000/api/flyer/game"
API_BASE_URL = os.getenv("API_BASE_URL", "http://127.0.0.1:5000/api")

def fetch_flyer_data(home_id: str, away_id: str):
    url = FLYER_API_BASE
    params = {"home": home_id, "away": away_id}

    try:
        logger.info("[FLYER FETCH] Requesting %s params=%s", url, params)

        r = requests.get(url, params=params, timeout=5)
        logger.info("[FLYER FETCH] Status code: %s", r.status_code)

        if r.status_code != 200:
            logger.error("[FLYER FETCH] Non-200 response: %s | Body: %s",
                         r.status_code, r.text)
            return None

        data = r.json()
        logger.info("[FLYER FETCH] Raw data: %s", data)

        # 🔎 Validate structure
        if not isinstance(data, dict):
            logger.error("[FLYER FETCH] Payload is not a dict. Type=%s", type(data))
            return None

        if "home" not in data or "away" not in data:
            logger.error("[FLYER FETCH] Missing required keys. Keys present: %s",
                         list(data.keys()))
            return None

        if not data.get("home") or not data.get("away"):
            logger.error("[FLYER FETCH] Home or Away data is empty.")
            return None

        logger.info("[FLYER FETCH] Flyer data validated successfully.")
        return data

    except requests.exceptions.Timeout:
        logger.error("[FLYER FETCH] Request timed out.")
        return None

    except requests.exceptions.ConnectionError:
        logger.error("[FLYER FETCH] Connection error — Flask server unreachable.")
        return None

    except Exception:
        logger.exception("[FLYER FETCH] Unexpected API error")
        return None


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
bot = commands.Bot(
    command_prefix="!",
    intents=intents,
    help_command=None
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


# === PLAYTIME: storage & helpers =============================================
PLAYTIME_FILE = "data/playtime.json"
os.makedirs("data", exist_ok=True)


def get_playtime(user_id: int | str) -> str | None:
    d = _load_playtime_map()
    return d.get(str(user_id))

def set_playtime(user_id: int | str, text: str) -> str:
    d = _load_playtime_map()
    d[str(user_id)] = text
    _save_playtime_map(d)
    return text

async def _find_availability_message(channel: nextcord.TextChannel) -> nextcord.Message | None:
    """
    Look back a bit for our board message so we can edit it in place.
    """
    try:
        async for m in channel.history(limit=50, oldest_first=False):
            if m.author.id == channel.guild.me.id and (m.content or "").startswith("📅 Availability"):
                return m
    except Exception:
        pass
    return None

def _availability_panel_lines(guild: nextcord.Guild, member_ids: list[int]) -> list[str]:
    """
    Build 2-3 clean lines showing each user and their latest availability text.
    """
    lines = ["📅 Availability Schedule — set with !playtime (auto-updating)"]
    for uid in member_ids:
        m = guild.get_member(uid)
        disp = (m.display_name if m else f"User {uid}")
        txt = get_playtime(uid) or "— not set —  Set by typing: !playtime <your availability message here>"
        lines.append(f"• **{disp}**: {txt}")
    return lines

async def _ensure_or_update_availability_board(channel: nextcord.TextChannel) -> None:
    """
    Create or update the '📅 Availability' message for this matchup channel.
    Uses channel_activity_tracker[channel.id]['member_ids'] to show both players.
    """
    try:
        tracker = channel_activity_tracker.get(channel.id)
        if not tracker or not tracker.get("member_ids"):
            return

        lines = _availability_panel_lines(channel.guild, tracker["member_ids"])
        content = "\n".join(lines)

        board = await _find_availability_message(channel)
        if board:
            await board.edit(content=content, allowed_mentions=AllowedMentions.none())
        else:
            await channel.send(content, allowed_mentions=AllowedMentions.none())

    except Exception as e:
        logger.warning(f"availability board update failed in #{getattr(channel, 'name', '?')}: {e}")

async def _update_user_matchup_boards(user: nextcord.Member | nextcord.User) -> int:
    """
    After a user runs !playtime, update every matchup channel they are part of.
    Returns number of channels updated.
    """
    guild = bot.get_guild(GUILD_ID)
    if not guild:
        return 0
    category = guild.get_channel(CATEGORY_ID)
    if not category or not isinstance(category, nextcord.CategoryChannel):
        return 0

    updated = 0
    for ch in category.text_channels:
        tracker = channel_activity_tracker.get(ch.id)
        if tracker and user.id in tracker.get("member_ids", []):
            await _ensure_or_update_availability_board(ch)
            updated += 1
    return updated
# === END PLAYTIME helpers ======================================================


def _load_nfl_title_and_upper():
    with open('NFL_Teams.csv', 'r', encoding='utf-8') as f:
        titles = [ln.strip() for ln in f if ln.strip()]
    uppers = {t.upper(): t for t in titles}
    return titles, uppers


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

def get_current_season(flyer_data: dict | None = None) -> int | str:
    if not flyer_data:
        return "unknown"

    raw = flyer_data.get("season")

    if not raw:
        return "unknown"

    try:
        # Handle "season_4" → 4
        if isinstance(raw, str) and raw.startswith("season_"):
            return int(raw.split("_")[1])

        # Handle normal int or numeric string
        return int(raw)

    except Exception:
        return raw  # fallback to raw instead of "unknown"

def prefer_learned_week(parsed_week: int | None) -> int | None:
    """If we’ve learned a week from the advance, prefer it over any parsed/user week."""
    return _current_week if _current_week is not None else parsed_week

def order_by_advance(a: str, b: str) -> tuple[str, str]:
    """
    Return (L,R) in the exact left/right order from _current_pairs if possible,
    otherwise return (a,b) unchanged.
    """
    if _current_pairs:
        ab = {a, b}
        for L, R in _current_pairs:
            if {L, R} == ab:
                return (L, R)
    return (a, b)

def normalize_matchup_with_learned(t1: str | None, t2: str | None, author=None) -> tuple[str | None, str | None]:
    """
    Use _current_matchups/_current_pairs to complete/override/validate the matchup.
    Priority is the learned schedule:
      - If only one team, fill opponent from _current_matchups
      - If both teams but not a learned pair, force the correct opponent from mapping
      - If nothing parsed, try author’s nickname -> team -> opponent
      - Finally, enforce advance left/right order
    """
    t1 = canonical_team(t1) if t1 else None
    t2 = canonical_team(t2) if t2 else None

    if _current_matchups:
        if t1 and not t2:
            t2 = _current_matchups.get(t1)
        elif t2 and not t1:
            t1 = _current_matchups.get(t2)
        elif t1 and t2:
            # If user provided the wrong opponent, snap to the learned opponent
            mapped = _current_matchups.get(t1)
            if mapped and mapped != t2:
                t2 = mapped

        # If still missing both, try author nickname
        if not (t1 and t2) and author:
            author_team = extract_team_from_nick(getattr(author, "display_name", "") or "")
            if author_team:
                t1 = author_team
                t2 = _current_matchups.get(author_team)

    if t1 and t2:
        t1, t2 = order_by_advance(t1, t2)
    return t1, t2

MACRODROID_ADVANCE_URL = "https://trigger.macrodroid.com/0173acce-c77b-4627-9c87-25fb2f03d580/wurd_advance"

def trigger_macrodroid_advance():
    try:
        r = requests.get(MACRODROID_ADVANCE_URL, timeout=5)
        logger.info(f"MacroDroid webhook sent. Status: {r.status_code}")
    except Exception as e:
        logger.error(f"MacroDroid webhook failed: {e}")


# The trigger: on_thread_create (new block)
@bot.event
async def on_thread_create(thread: nextcord.Thread):
    if thread.parent_id != GAME_STREAMS_FORUM_ID:
        return

    parsed_week, pt1, pt2 = parse_title_for_week_and_teams(thread.name or "")

    try:
        author = await thread.guild.fetch_member(thread.owner_id)
    except Exception:
        author = None

    # ✅ Prefer learned week over user/title week
    week = prefer_learned_week(parsed_week)

    # ✅ Force teams to the learned mapping/order
    t1, t2 = normalize_matchup_with_learned(pt1, pt2, author=author)

    if not week or not (t1 and t2):
        return

    # eligibility unchanged...
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

    if not TEAM_NAME_TO_ID:
        load_team_id_mapping()

    home_id = TEAM_NAME_TO_ID.get(t1)
    away_id = TEAM_NAME_TO_ID.get(t2)

    logger.info("MATCHUP LOOKUP: %s (%s) vs %s (%s)",
                t1, home_id, t2, away_id)

    flyer_data = (
        fetch_flyer_data(home_id, away_id)
        if home_id and away_id
        else None
    )

    if flyer_data:
        if "home" in flyer_data and "team1" not in flyer_data:
            flyer_data["team1"] = flyer_data["home"]
            flyer_data["team2"] = flyer_data["away"]

    season = get_current_season(flyer_data)

    if not flyer_data:
        logger.warning("Thread: flyer_data missing — using season='unknown' and skipping registry duplicate check.")
    else:
        if registry_has(season, week, t1, t2):
            logger.info(
                f"Thread: flyer already exists for season={season} week={week} {t1} vs {t2}"
            )
            return

    link = None
    try:
        parent = thread.parent
        starter = await parent.fetch_message(thread.id)
        link = find_stream_link(getattr(starter, "content", ""))
    except Exception:
        pass

    streamer_display = getattr(author, "display_name", "Unknown")

    use_ai = should_use_ai_flyer(week, t1, t2)

    if flyer_data and use_ai:
        flyer_caption = build_flyer_caption(flyer_data)
        flyer_prompt = build_flyer_image_prompt(flyer_data)
    else:
        flyer_caption = None
        flyer_prompt = None  # forces static fallback

    logger.info("=== FLYER API DATA ===")
    logger.info(json.dumps(flyer_data, indent=2))
    logger.info("=== FLYER CAPTION ===")
    logger.info(flyer_caption)
    logger.info("=== FLYER IMAGE PROMPT ===")
    logger.info(flyer_prompt)

    flyer_path, flyer_source = generate_flyer_with_fallback(
        week=week,
        t1=t1,
        t2=t2,
        streamer=streamer_display,
        link=link,
        flyer_prompt=flyer_prompt,
        flyer_data=flyer_data
    )

    # ❌ was: post_flyer_with_everyone(..., *sorted_pair(t1, t2), ...)
    msg = await post_flyer_with_everyone(
        thread,
        flyer_path,
        week,
        t1,
        t2,
        streamer_display,
        link,
        EVERYONE_MENTIONS
    )

    # Keep dedupe key sorted so A/B == B/A for registry only
    registry_put(season, week, t1, t2, {
        "thread_id": thread.id,
        "message_id": msg.id,
        "flyer_path": flyer_path,
        "author_id": getattr(author, "id", 0),
        "ts": datetime.now(dt_timezone.utc).isoformat()
    })

    if not link and author:
        bot.loop.create_task(
            watch_first_link_and_edit(
                bot,
                thread,
                author.id,
                msg.id,
                week,
                t1,
                t2,
                streamer_display,
                find_stream_link
            )
        )



AP_FILE = 'ap_users.json'
# --- AP auto-reload state ---
_AP_MTIME = 0
_AP_CACHE = None

ON_VACATION_FORUM_ID = int(os.getenv("ON_VACATION_FORUM_ID", "0"))

AP_NOTIFIED_FILE = 'ap_notified.json'
AP_ALERT_ADMIN_ID = int(os.getenv("AP_ALERT_ADMIN_ID", "0"))
AP_ALERT_CHANNEL_ID = int(os.getenv("AP_ALERT_CHANNEL_ID", "0"))
AP_ALERT_TZ = os.getenv("AP_ALERT_TZ", "US/Arizona")


os.makedirs("data", exist_ok=True)


def load_ap_users(force=False):
    global _AP_MTIME, _AP_CACHE

    try:
        mtime = os.path.getmtime(AP_FILE)
    except FileNotFoundError:
        return []

    if force or mtime != _AP_MTIME:
        with open(AP_FILE, "r", encoding="utf-8") as f:
            _AP_CACHE = json.load(f)
        _AP_MTIME = mtime

    # 🔥 ALWAYS FILTER HERE
    tz = _tz(AP_ALERT_TZ)
    today_local = datetime.now(tz).date()

    active = []
    for u in _AP_CACHE:
        try:
            start_date = _start_date(u)
            until_date = _date_from_str(u["until"])

            # skip invalid ranges
            if start_date > until_date:
                continue

            if start_date <= today_local < until_date:
                u = dict(u)
                u["user_id"] = _normalize_id(u.get("user_id"))
                active.append(u)
        except Exception:
            continue

    return active


def _normalize_id(value) -> str | None:
    """
    Coerce a Discord ID (int or string) to a normalized numeric-string.
    Returns None if it cannot be parsed as an integer.
    """
    if value is None:
        return None

    # Keep ONLY ASCII digits — no int() conversion!
    digits = "".join(ch for ch in str(value) if ch.isdigit())
    return digits if digits else None

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

AP_STATE_FILE = "ap_state.json"


def get_current_ap_state():
    ap_users = load_ap_users()

    # Only store what matters for comparison
    return sorted([
        {
            "user_id": _normalize_id(u.get("user_id")),
            "until": u.get("until")
        }
        for u in ap_users
    ], key=lambda x: x["user_id"] or "")



def ap_state_changed():
    current = get_current_ap_state()
    previous = load_last_ap_state()

    if current != previous:
        save_ap_state(current)
        return True

    return False


AP_TRIGGER_FILE = "_ap_trigger.json"
LAST_TRIGGER_TS = 0
LAST_POST_TIME = 0

async def ap_trigger_watcher():
    await bot.wait_until_ready()

    while not bot.is_closed():
        try:
            with open("_ap_trigger.json", "r") as f:
                data = json.load(f)

            if data.get("ready"):
                print("🔥 AP TRIGGER FIRED")

                await post_ap_bulletin(bot)

                data["ready"] = False

                with open("_ap_trigger.json", "w") as f:
                    json.dump(data, f, indent=2)

        except Exception as e:
            print(f"AP trigger error: {e}")

        await asyncio.sleep(2)


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
                    uid = _normalize_id(u.get("user_id")) or ""
                    key = (uid, u.get("until"))
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
                            uid = _normalize_id(u.get("user_id")) or ""
                            key = (uid, u.get("until"))
                            notified.add(key)
                        _save_notified(notified)
        except Exception as e:
            logger.warning(f"ap_return_reminder_loop: {e}")

        # Check hourly
        await asyncio.sleep(3600)


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

            await asyncio.sleep(1.5)  # throttle between channel creations (prevents global rate limit)

            channel_activity_tracker[channel.id] = {
                "created_at": datetime.now(pytz.utc),
                "member_ids": member_ids,
                "responses": set()
            }

            # === MENTIONS (TOP) ===
            ap_list = load_ap_users()
            non_ap_mentions = []

            for member_id in member_ids:
                member = guild.get_member(member_id)
                if not member:
                    continue
                if not is_on_ap(member_id, ap_list):
                    non_ap_mentions.append(member.mention)

            if non_ap_mentions:
                await channel.send(" ".join(non_ap_mentions))
                await asyncio.sleep(1.1)  # throttle between sends

            # channel welcome message
            await channel.send(message_content)
            await asyncio.sleep(1.1)

            await channel.send("\u200b")
            await asyncio.sleep(1.1)

            # Timezone difference logic (keep as-is)
            if len(member_info) == 2:
                tz1_code = extract_timezone_code(member_info[0][1])
                tz2_code = extract_timezone_code(member_info[1][1])
                if tz1_code and tz2_code:
                    tz_msg = get_timezone_offset_info(tz1_code, tz2_code, member_info[0][1], member_info[1][1])
                    if tz_msg:
                        await channel.send(tz_msg)
                        await channel.send("\u200b")  # zero-width space

            # === PLAYTIME: seed or update the availability board for this matchup
            try:
                await _ensure_or_update_availability_board(channel)
            except Exception as e:
                logger.warning(f"could not seed availability board for {channel.name}: {e}")

            await channel.send("\u200b")  # zero-width space

            # 4️⃣ Reminder
            await channel.send("**Reminder:** Please **@mention** your opponent — some users won’t see messages otherwise.")

            # === AP INSERT START ===
            # spacer so AP notice stands alone
            await channel.send("\u200b")

            # === AP HEADS-UP (BOTTOM) ===
            ap_notes = []

            for member_id in member_ids:
                member = guild.get_member(member_id)
                if not member:
                    continue

                ap = is_on_ap(member_id, ap_list)
                if ap:
                    shown_name = ap.get("display", member.display_name)
                    until = ap.get("until", "")
                    ap_notes.append(
                        f"🚨 **Heads-up:** {shown_name} is on **Auto-Pilot** until "
                        f"**{human_date(until)}**.\n"
                        f"➡️ Please play the **CPU** for this matchup."
                    )

            if ap_notes:
                await channel.send("\n\n".join(ap_notes))
            # === AP INSERT END ===

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

            await asyncio.sleep(1.5)  # 🔴 throttle deletions (very important)

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
    # 🔄 Load the latest AP file before creating any new channels
    # ap_list = load_ap_users()

    user_user_teams = load_user_user_teams()
    for team_name in user_user_teams:
        # Fetch the member IDs associated with the team
        member_ids = await fetch_team_members(guild, team_name)
        # Create the channel for the team and invite members
        await create_channel_helper(
            guild,
            team_name=team_name,
            member_ids=member_ids,
            message_content=f"Welcome to the {team_name} channel!",
            )
        await asyncio.sleep(0.8)


# Function to check members in a specific channel and save nicknames matching NFL teams
from pathlib import Path


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

@bot.command(name="playtime")
async def playtime_cmd(ctx, *, availability: str = ""):
    """
    Usage (anywhere or in DM):
      !playtime I work M-F 9AM - 5PM and weekends I'm available anytime

    - Strips mentions automatically.
    - Saves your text.
    - Updates the matchup forum(s) you’re in to show both players’ availability.
    """
    try:
        clean = sanitize_playtime_text(availability)
        if not clean:
            await ctx.reply("Please include a short note after `!playtime` (e.g., `!playtime Weeknights after 7pm, weekends open`).")
            return

        # persist
        set_playtime(ctx.author.id, clean)

        # acknowledge privately unless they said it in a matchup channel
        ack = f"Saved your playtime:\n> {clean}"
        try:
            await ctx.author.send(ack)
            # If they invoked in-guild, give a tiny heads-up
            if ctx.guild:
                await ctx.reply("Got it — I DM’d you a confirmation and updated your matchup forum(s).", mention_author=False)
        except Exception:
            # fallback to replying in place if we can’t DM
            await ctx.reply(ack, allowed_mentions=AllowedMentions.none())

        # update any relevant matchup channels for this user
        updated = await _update_user_matchup_boards(ctx.author)
        if updated == 0 and ctx.guild:
            # If invoked in a specific matchup channel, try to update that one at least.
            try:
                if ctx.channel and isinstance(ctx.channel, nextcord.TextChannel):
                    await _ensure_or_update_availability_board(ctx.channel)
            except Exception:
                pass

    except Exception as e:
        logger.error(f"!playtime failed: {e}")
        try:
            await ctx.reply("Sorry — couldn’t save your playtime just now.")
        except Exception:
            pass

@bot.command()
async def test_gotw(ctx):
    print("[CMD] Running GOTW test...")
    await select_games_of_the_week()
    await ctx.send("GOTW test complete (check logs)")


@bot.command(name="seed_week")
@admin_or_authorized()
async def seed_week(ctx, week: int):
    global _current_week, _current_pairs, _current_matchups
    _current_week = week
    _current_pairs = []
    _current_matchups = {}
    _save_week_state(
        week,
        [],
        pre_sent=False,
        advance_time=None
    )
    await ctx.reply(f"Seeded week_state.json → WEEK {week} (no matchups).")

@bot.command(name="seed_advance")
@admin_or_authorized()
async def seed_advance(ctx, *, block: str):
    wk, pairs, mapping = _parse_advance_block(block or "")
    if not wk or not pairs:
        return await ctx.reply("Couldn’t parse a week + matchups from your block.")
    global _current_week, _current_pairs, _current_matchups
    _current_week = wk
    _current_pairs = pairs
    _current_matchups = mapping

    _save_week_state(
        wk,
        [[L, R] for (L, R) in pairs],
        pre_sent=False,
        advance_time=datetime.now(pytz.utc).isoformat()
    )
    # write flyer cache
    build_week_cache_from_current_state()
    await ctx.reply(f"Seeded WEEK {wk} with {len(pairs)} matchups. No messages posted.")


# ===== BOT.LOG READER =====

LOG_BACKUP_GLOB = "bot.log*"       # we'll read bot.log, bot.log.1, ... (in modified-time order)

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

def _render_user(
    entries: list,
    user_query: str,
    limit: int = DEFAULT_FORUM_LIMIT,
    only_date: str | None = None
) -> str:
    """
    Render log lines filtered by username / ID fragment across ALL forums.

    - user_query can be part of the username (e.g., 'panthers') or a full ID.
    - Looks only within DEFAULT_LOG_LOOKBACK_DAYS worth of log files.
    """
    tz_az = pytz.timezone('US/Arizona')
    q = (user_query or "").strip().lower()
    if not q:
        return "No user query provided."

    filtered = []

    for e in entries:
        dt = e.get("dt")

        # Optional date filter (YYYY-MM-DD in AZ time)
        if only_date:
            if not dt:
                continue
            if dt.astimezone(tz_az).strftime("%Y-%m-%d") != only_date:
                continue

        uname = (e.get("username") or "").strip()
        uid_raw = str(e.get("user_id") or "").strip()

        # Match if query is in username or equals the numeric ID
        if q in uname.lower() or q == uid_raw.lower():
            filtered.append(e)

    if not filtered:
        msg = f"No messages found for user '{user_query}'"
        if only_date:
            msg += f" on {only_date}"
        msg += f" in the last {DEFAULT_LOG_LOOKBACK_DAYS} day(s)."
        return msg

    # Sort by time and limit to last N
    filtered.sort(key=lambda e: e.get("dt") or datetime.min.replace(tzinfo=tz_az))
    if limit and limit > 0:
        filtered = filtered[-limit:]

    # Group by date for neat output
    by_date = defaultdict(list)
    for e in filtered:
        if e.get("dt"):
            dkey = e["dt"].astimezone(tz_az).date().strftime("%Y-%m-%d (%a)")
        else:
            dkey = "Unknown Date"
        by_date[dkey].append(e)

    lines = [f"__**User filter:**__ `{user_query}` — {len(filtered)} message(s)"]
    if only_date:
        lines[0] += f" on {only_date}"
    lines.append("")

    for dkey in sorted(by_date.keys()):
        lines.append(f"**{dkey}**")
        for e in by_date[dkey]:
            dt = e.get("dt")
            t = dt.astimezone(tz_az).strftime("%I:%M %p").lstrip("0") if dt else "??:??"
            forum = e.get("forum") or "Unknown"
            uname = (e.get("username") or "").strip()
            msg = _sanitize_message((e.get("message") or "").strip()) or "(no content)"
            lines.append(f"- **{t}** — #{forum} — {uname}: {msg}")
        lines.append("")

    return "\n".join(lines).rstrip()


@bot.command(name="logs")
async def logs_cmd(ctx, *, rest: str = ""):
    """
    View logs by date, forum, or user.

    Usage:
      • !logs date=YYYY-MM-DD [here]
      • !logs forum=bills-panthers [limit=100] [date=YYYY-MM-DD] [here]
      • !logs bills-panthers            # shorthand for forum=
      • !logs bills-panthers here
      • !logs forum="bills panthers"    # spaces ok if quoted
      • !logs user=panthers [limit=50] [date=YYYY-MM-DD] [here]

    Notes:
      - Replies by DM unless you add 'here'.
      - Looks back DEFAULT_LOG_LOOKBACK_DAYS across bot.log files.
      - Limit defaults to DEFAULT_FORUM_LIMIT (env overridable).
    """

    author = ctx.author
    text = (rest or "").strip()

    # detect and strip the 'here' flag (so it doesn't pollute forum/user names)
    want_here = bool(re.search(r'\bhere\b', text, flags=re.IGNORECASE))
    if want_here:
        text = re.sub(r'\bhere\b', '', text, flags=re.IGNORECASE).strip()

    # --- parse args ---
    m_date = re.search(r'date\s*=\s*(\d{4}-\d{2}-\d{2})', text, flags=re.IGNORECASE)
    m_forum = re.search(r'forum\s*=\s*("?)(.+?)\1($|\s)', text, flags=re.IGNORECASE)  # supports forum="bills panthers"
    m_limit = re.search(r'limit\s*=\s*(\d{1,4})', text, flags=re.IGNORECASE)
    m_user = re.search(r'user\s*=\s*("?)(.+?)\1($|\s)', text, flags=re.IGNORECASE)

    only_date = m_date.group(1) if m_date else None
    limit = int(m_limit.group(1)) if m_limit else DEFAULT_FORUM_LIMIT

    forum_query = None
    user_query = None

    if m_forum:
        forum_query = m_forum.group(2).strip()
    elif m_user:
        user_query = m_user.group(2).strip()
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

    elif user_query:
        # User mode
        header = f"**bot.log — user: {user_query}**"
        if only_date:
            header += f" — {only_date}"
        body = _render_user(entries, user_query=user_query, limit=limit, only_date=only_date)
        chunks = [header] + split_message(body)

    else:
        # Date mode (original behavior)
        if not only_date:
            usage = (
                "Usage:\n"
                "  `!logs date=YYYY-MM-DD`\n"
                "OR\n"
                "  `!logs forum=NAME [limit=N] [date=YYYY-MM-DD]`\n"
                "OR\n"
                "  `!logs user=NAME [limit=N] [date=YYYY-MM-DD]`"
            )
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

### THIS IS A DEBUGGING COMMAND TEMP
@bot.command(name="debug_advance")
@commands.has_role(ADMIN_ROLE_NAME)
async def debug_advance(ctx):
    await ctx.send(f"Current week: {_current_week}")
    await ctx.send(f"Current pairs: {_current_pairs}")
    await ctx.send(f"Current matchups: {_current_matchups}")


# Event handler for bot login and startup details
@bot.event
async def on_ready():
    global personality_loop_started
    load_team_id_mapping()

    await rebuild_channel_activity()
    bot.loop.create_task(pre_advance_reminder_loop())


    if not personality_loop_started:
        bot.loop.create_task(
            lobby_personality_loop(
                bot,
                logger,
                ADVANCE_INFO_FILE,
                get_lobby_talk_channel
            )
        )
        personality_loop_started = True
        print("✅ AI personality loop started")

    try:
        guild = bot.get_guild(GUILD_ID)
        if not guild:
            print(f"Guild with ID {GUILD_ID} not found.")
            return
        logger.info(f'Logged in as {bot.user.name}')
        load_ap_users(force=True)
    except Exception as e:
        logger.error(f"Error during bot startup: {e}")

    # Restore last learned advance (survives reboot)
    try:
        st = _load_week_state()
        wk = int(st.get("week", 0))
        pairs = [tuple(x) for x in st.get("matchups", [])]
        mapping = {}
        for a, b in pairs:
            mapping[a] = b
            mapping[b] = a

        global _current_week, _current_pairs, _current_matchups
        _current_week = wk if wk != 0 else None
        _current_pairs = pairs
        _current_matchups = mapping
        logger.info(f"Restored advance from file: WEEK={_current_week}, games={len(_current_pairs)}")
        if os.path.exists("data/week_cache.json"):
            logger.info("Week cache found on disk — flyer system ready")
        else:
            logger.warning("No week cache found — AI flyers will require seeding")

        # Restore GOTW pairs on startup
        try:
            state = load_gotw_state()
            if state and state.get("last_week_posted") == _current_week:
                _current_gotw_pairs.clear()
                saved_pairs = state.get("pairs", [])
                for p in saved_pairs:
                    _current_gotw_pairs.add(tuple(sorted(p)))
                logger.info(f"[GOTW] Restored {len(_current_gotw_pairs)} GOTW pairs on startup.")
        except Exception as e:
            logger.warning(f"GOTW restore on startup failed: {e}")

    except Exception as e:
        logger.warning(f"Could not restore advance state: {e}")

    # Start the AP reminder loop
    bot.loop.create_task(ap_return_reminder_loop())

    # Start the AP auto-post watcher
    bot.loop.create_task(ap_trigger_watcher())

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
    nicknames_to_users_file()  # make sure available teams is up-to-date
    channel = member.guild.system_channel  # Get the system channel
    if channel is not None:
        welcome_message = (
            f"Welcome {member.mention} to WURD!!! :football::fire:\n\n"
            "Please start by reviewing https://discord.com/channels/1144688789248282804/1144692100697423972.\n"
            "If you have any questions, post in lobby-talk forum here and we'll be glad to answer.\n\n"
            "Next step: Complete the WURD application :point_right: https://wurd-madden.com/recruits/new\n\n"
            "Important: Communication matters here.\n"
            "Make sure notifications are enabled for **WURD :trophy: CHAMPIONSHIP** (All Messages or @mentions).\n\n"
            "Queue Process:\n"
            "New users may start in the Queue while commissioners evaluate communication, availability, and readiness.\n"
            "When a team opens — placement is based on preparedness, not order of arrival, at commissioner discretion.\n\n"
            "Coaches:\n"
            "Real-life coaches are not allowed. Choose Offensive, Defensive, or Development coaches only.\n\n"
            "Let us know which team you’re interested in :smiley:\n"
        )
        teams_message = await get_available_teams_output()
        full_message = welcome_message + teams_message
        # Send the message in chunks if necessary
        for chunk in split_message(full_message):
            await channel.send(chunk)

# ---------------------------------------




WEEK_CACHE_PATH = "data/week_cache.json"

def build_week_cache_from_current_state():
    if not _current_week or not _current_pairs:
        logger.warning("Cannot build week cache — no advance loaded")
        return

    guild = bot.get_guild(GUILD_ID)
    if not guild:
        return

    games = {}

    for a, b in _current_pairs:
        user_a = None
        user_b = None

        for m in guild.members:
            team = extract_team_from_nick(m.display_name or "")
            if team == a:
                user_a = m
            elif team == b:
                user_b = m

        if not user_a or not user_b:
            logger.warning(f"Week cache: missing users for {a} vs {b}")
            continue

        game_id = f"{_current_week}_{a}_{b}"

        games[a] = {
            "team": a,
            "discord_id": str(user_a.id),
            "display": user_a.display_name,
            "opponent": b,
            "game_id": game_id
        }
        games[b] = {
            "team": b,
            "discord_id": str(user_b.id),
            "display": user_b.display_name,
            "opponent": a,
            "game_id": game_id
        }

    cache = {
        "season": datetime.now().year,
        "week": _current_week,
        "games": games
    }

    write_week_cache_if_changed(cache)


async def safe_async_sleep(sec: float):
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            await asyncio.sleep(sec)
        else:
            time.sleep(sec)
    except RuntimeError:
        # no loop or loop closed
        time.sleep(sec)

# ---------------------------------------------------------------
async def trigger_companion_export():
    await asyncio.sleep(300)

    trigger_macrodroid_advance()

# ---------------------------------------------------------------

def get_stats_hash():
    try:
        r = requests.get("http://127.0.0.1:5000/stats-hash", timeout=5)
        if r.status_code == 200:
            return r.json().get("hash")
    except Exception as e:
        logger.warning(f"Could not get stats hash: {e}")
    return None

async def retry_export_until_changed():
    global EXPORT_RETRY_IN_PROGRESS

    if EXPORT_RETRY_IN_PROGRESS:
        logger.info("Export retry already in progress. Skipping new loop.")
        return

    EXPORT_RETRY_IN_PROGRESS = True
    logger.info("Starting export retry loop.")

    try:
        previous_hash = get_stats_hash()

        for attempt in range(EXPORT_MAX_ATTEMPTS):
            trigger_macrodroid_advance()
            logger.info(f"Export attempt {attempt + 1} sent.")

            await asyncio.sleep(EXPORT_RETRY_DELAY)

            new_hash = get_stats_hash()

            if new_hash and (previous_hash is None or new_hash != previous_hash):
                logger.info("New stats detected. Stopping retry loop.")
                return

            logger.info("Stats unchanged. Retrying...")

        logger.warning("Max export attempts reached. No stat change detected.")

    except Exception as e:
        logger.error(f"Retry export error: {e}")

    finally:
        EXPORT_RETRY_IN_PROGRESS = False
        logger.info("Export retry loop finished.")

# ---------------------------------------------------------------


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

    # =============================
    # 🤖 AI LOBBY BOT (SAFE INSERT)
    # =============================
    try:
        if msg.guild:
            lobby_channel = get_lobby_talk_channel(msg.guild)

            if lobby_channel and msg.channel.id == lobby_channel.id:
                if msg.author != bot.user:
                    update_last_message_time()

                if msg.author != bot.user and is_bot_mentioned(msg, bot.user):
                    await asyncio.sleep(random.randint(2, 4))  # thinking time
                    async with msg.channel.typing():
                        await asyncio.sleep(random.randint(4, 7))

                        context = load_ai_advance_info(logger, ADVANCE_INFO_FILE)
                        reply = generate_ai_reply(msg.content, context)

                        if reply and reply.strip():
                            reply = reply.strip().strip('"').strip("'").strip("“").strip("”")  # strip quotation marks add mention in front
                            await msg.channel.send(f"{msg.author.mention} {reply}")

    except Exception as e:
        logger.warning(f"AI handler failed: {e}")

    # --- Advance channel watcher: cache WEEK + matchups -------------------------
    try:
        if msg.guild and ADVANCE_CHANNEL_ID and msg.channel.id == ADVANCE_CHANNEL_ID:
            wk, pairs, mapping = _parse_advance_block(msg.content or "")
            if wk and pairs:
                global _current_week, _current_pairs, _current_matchups
                _current_week = wk
                _current_pairs = pairs
                _current_matchups = mapping

                # ⬇️ persist to disk so it survives restarts
                _save_week_state(
                    wk,
                    [[L, R] for (L, R) in pairs],
                    pre_sent=False,
                    advance_time=datetime.now(pytz.utc).isoformat()
                )
                logger.info(f"Advance learned & saved: WEEK={wk}, games={len(pairs)}")

                # 🔥 ONLY POST AP IF IT CHANGED
                if ap_state_changed():
                    logger.info("AP state changed — posting update.")
                    await post_ap_bulletin(bot)
                else:
                    logger.info("AP state unchanged — no post.")

    except Exception as e:
        logger.warning(f"advance parse failed: {e}")

    # ---------------------------------------------------------------------------

    # --- Text-channel flyer trigger for game-streams ----------------------------
    if msg.guild and GAME_STREAMS_CHANNEL_ID and msg.channel.id == GAME_STREAMS_CHANNEL_ID:
        await handle_game_stream_post(bot, msg)
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
                        f"GG detected in #{msg.channel.name} by {msg.author.display_name}\n"
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

                        # 🚀 Schedule GG-based export in 60 seconds
                        asyncio.create_task(retry_export_until_changed())
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

        # =============================  This DM message cannot ping users or @everyone -  it's on purpose so it won't spam
        # 📢 DM → LOBBY (!send command) - 🏈 **WURD Update**\n
        # =============================
        try:
            guild = bot.get_guild(GUILD_ID)
            lobby_channel = get_lobby_talk_channel(guild) if guild else None

            # Only trigger if message starts with !send (case-insensitive)
            if msg.content.lower().startswith("!send"):

                if not lobby_channel:
                    await msg.channel.send("❌ Lobby channel not found.")
                    return

                # Safe removal of command
                parts = msg.content.split(" ", 1)

                if len(parts) < 2:
                    await msg.channel.send("❌ Please include a message after !send.")
                    return

                text = parts[1].strip()

                await lobby_channel.send(
                    f"{text}"
                )  # 🏈 **WURD Update**\n

                await msg.channel.send("✅ Sent to lobby-chat.")
                return

        except Exception as e:
            logger.warning(f"!send relay failed: {e}")

        nicknames_to_users_file()  # Call function to save users with matching team names.  This updates discord teams to wurd24users.csv

        # checking 'week' plus one or two numbers or 'all'
        pattern_week = r"^week \d{1,2}$"
        pattern_pre = r"^pre\s*[123]$"  # NEW: pre 1, pre 2, pre 3
        pattern_all = r"^all$"
        pattern_playoffs = r"^week\s+(wild\s*card|divisional|conference|super\s*bowl)$"

        if (
                re.fullmatch(pattern_week, msg_text) or
                re.fullmatch(pattern_pre, msg_text) or
                re.fullmatch(pattern_playoffs, msg_text) or
                re.fullmatch(pattern_all, msg_text)
        ):

            # Normalize what we pass into the scheduler
            norm = msg_text.upper()
            if re.fullmatch(pattern_pre, msg_text):
                # If your Wurd24Scheduler expects uppercase "PRE 1" tokens:
                norm = msg_text.replace("pre", "PRE").upper()  # -> "PRE 1"
                # If instead your scheduler expects negative "week -3/-2/-1",
                # you can convert here instead (uncomment if needed):
                # n = int(re.search(r"\d", msg_text).group(0))   # 1..3
                # week_map = {1: -3, 2: -2, 3: -1}
                # norm = f"week {week_map[n]}"

            # Convert playoff tokens to numeric weeks for the scheduler
            playoff_map = {
                "WEEK WILD CARD": "week 19",
                "WEEK DIVISIONAL": "week 20",
                "WEEK CONFERENCE": "week 21",
                "WEEK SUPER BOWL": "week 23"
            }

            norm_sched = playoff_map.get(norm.upper(), norm)

            playoff_weeks = {"week 19", "week 20", "week 21", "week 23"}

            if norm_sched.lower() in playoff_weeks:
                week_schedule = ""  # playoffs handled by seed_advance
            else:
                week_schedule = wrd.wurd_sched_main(norm_sched)

            # IF TEST IS TRUE THEN WE PRINT SCHEDULE WITHOUT POSTING IT
            if TEST:
                logger.info("--------TEST PRINT--------------\n%s\n------------TEST PRINT---------", week_schedule)
                return  # Prevent it from posting to Discord
            else:
                # schedule forum ID (for 'all')  vs  advance forum ID (for a single week/pre)
                channel_id = 1290487933131952138 if 'all' in msg_text else 1149401984466681856
                channel = bot.get_channel(channel_id)

                def format_schedule_for_discord(raw_text: str, week_token: int | None) -> str:
                    lines = [line.strip() for line in raw_text.splitlines() if line.strip()]

                    # Detect playoffs
                    is_playoffs = week_token in (19, 20, 21, 23)

                    if not is_playoffs:
                        # ---- ORIGINAL BEHAVIOR FOR REGULAR SEASON ----
                        formatted_lines = []
                        for line in lines:
                            if line.upper().startswith("PRE") or line.upper().startswith("WEEK"):
                                formatted_lines.append(f"\n🏈  **{line.upper()}**\n────────────────────")
                            else:
                                formatted_lines.append(f" {line}")
                        return "\n".join(formatted_lines)

                    # ---- PLAYOFF BEHAVIOR: SPLIT BY AFC / NFC ----
                    header = None
                    games = []

                    for line in lines:
                        if line.upper().startswith(
                                ("PRE", "WEEK", "WURD", "WILD", "DIVISIONAL", "CONFERENCE", "SUPER")):
                            header = line.upper()
                        else:
                            games.append(line)

                    afc_games = []
                    nfc_games = []

                    for g in games:
                        # Expect: "Broncos(U) vs Raiders(U)" or similar
                        m = re.match(r"^\s*([A-Za-z0-9 .’'-]+)\s*\([^)]*\)\s*vs\s*([A-Za-z0-9 .’'-]+)", g,
                                     re.IGNORECASE)
                        if not m:
                            continue

                        t1 = canonical_team(m.group(1).upper())
                        t2 = canonical_team(m.group(2).upper())

                        # Look up conference from nfl_teams mapping
                        div1 = nfl_teams.get(t1)
                        div2 = nfl_teams.get(t2)

                        # Default to AFC if unknown (safe fallback)
                        if div1 and div2 and div1.startswith("NFC") and div2.startswith("NFC"):
                            nfc_games.append(g)
                        elif div1 and div2 and div1.startswith("AFC") and div2.startswith("AFC"):
                            afc_games.append(g)
                        else:
                            # Fallback (should never happen in playoffs)
                            afc_games.append(g)

                    out = []

                    if header:
                        out.append(f"\n🏈  **{header}**\n────────────────────")

                    if afc_games:
                        out.append("\n**AFC**")
                        for g in afc_games:
                            out.append(f" {g}")

                    if nfc_games:
                        out.append("\n**NFC**")
                        for g in nfc_games:
                            out.append(f" {g}")

                    return "\n".join(out)

                parsed_week = parse_week_token(msg_text)
                week_schedule = format_schedule_for_discord(week_schedule, parsed_week)

                # Regular season only (weeks 1–18)
                is_playoffs = parsed_week in (19, 20, 21, 23)

                first = True
                for chunk in split_message(week_schedule):

                    # Prevent Discord empty message crash
                    if not chunk or not chunk.strip():
                        continue

                    if first and ('all' not in msg_text) and not is_playoffs:

                        now_az = datetime.now(pytz.timezone("US/Arizona"))

                        # Preseason weeks are negative: -3, -2, -1
                        is_preseason = parsed_week is not None and parsed_week < 0

                        if is_preseason:
                            target = now_az + timedelta(hours=24)  # 1 day for preseason
                            timing_note = "This is the preseason 24-hour target time (around 5 PM Arizona)."
                        else:
                            target = now_az + timedelta(hours=48)  # 2 days for season
                            timing_note = "This is the normal 48-hour target time (around 5 PM Arizona)."

                        advance = target.replace(hour=17, minute=0, second=0, microsecond=0)
                        write_advance_file(advance, parsed_week)

                        advance_block = (
                            "\n\n⏰ **Advance Time**\n"
                            "Next week is scheduled to advance on\n"
                            f"**{advance.strftime('%A, %b %d @ ~%I:%M %p')} AZ**\n"
                            f"{timing_note}\n"
                            "If all User-vs-User games finish early, the advance may happen sooner.\n"
                            "If games are still being played, commissioners will notify everyone of any delay.\n\n"
                        )

                        await channel.send(
                            f"@everyone\n{chunk}{advance_block}",
                            allowed_mentions=EVERYONE_MENTIONS
                        )
                        first = False
                        await safe_async_sleep(1.1)

                    else:
                        await channel.send(chunk, allowed_mentions=AllowedMentions.none())
                        await safe_async_sleep(1.1)


                # For both 'week N' *and* 'pre N', build the game forums
                if any(k in msg_text for k in ("week", "pre")):
                    guild = bot.get_guild(GUILD_ID)
                    await delete_category_channels(guild)
                    channel_activity_tracker.clear()

                    if parsed_week in (19, 20, 21, 23):  # playoffs
                        for team1, team2 in _current_pairs:
                            channel_name = f"{team1.lower()}-{team2.lower()}"

                            members = []
                            for m in guild.members:
                                team = extract_team_from_nick(m.display_name or "")
                                if team in (team1, team2):
                                    members.append(m.id)

                            logger.info(f"Creating playoff channel: {team1}-{team2}")

                            await create_channel_helper(
                                guild,
                                team_name=channel_name,
                                member_ids=members,
                                message_content=f"Welcome to the {team1} vs {team2} playoff matchup!"
                            )
                            await asyncio.sleep(0.8)

                    else:
                        await create_user_user_channels(guild)

                    build_week_cache_from_current_state()

                    asyncio.create_task(schedule_games_of_the_week())

                    # 🚀 Start 5-minute delayed Companion App export
                    logger.info("Week advance complete — scheduling Companion export in 5 minutes")
                    asyncio.create_task(trigger_companion_export())

    # Regex search for time patterns in the message
    # player_msg_time = re.search(r'\d{1,2}:\d{2}', msg.content)

    # # Respond with time if a valid time pattern is found in the message
    # if player_msg_time:
    #     await msg.channel.send(f'[testing]The time is {player_msg_time.group()}\n')




if __name__ == "__main__":
    logger.info("Starting Discord bot...")
    bot.run(token)




