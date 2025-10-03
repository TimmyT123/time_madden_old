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

def is_on_ap(user_id: int, ap_users=None):
    ap_users = ap_users if ap_users is not None else load_ap_users()
    for u in ap_users:
        if int(u.get("user_id", 0)) == int(user_id):
            return u
    return None

def _active_ap_signature():
    """
    Returns a stable signature (tuple) of currently ACTIVE AP entries.
    Active = start <= today <= until in AP_ALERT_TZ (as enforced by load_ap_users).
    Only keys that define 'active identity' are included so edits to notes, etc.
    won't spam unless they affect activation window or user id.
    """
    active = load_ap_users()  # already respects start/until
    key_tuples = []
    for u in active:
        key_tuples.append((
            str(u.get("user_id", "")),
            (u.get("start") or ""),   # may be ""
            (u.get("until") or "")
        ))
    # Sort so order doesn’t affect the signature
    return tuple(sorted(key_tuples))


async def ap_autopost_watcher():
    """
    Periodically checks if the set of ACTIVE AP users has changed.
    If changed, post a fresh AP bulletin to the on-vacation forum.
    """
    interval = int(os.getenv("AP_AUTOPUBLISH_INTERVAL_SEC", "600"))  # default: 10 min
    prev_sig = None

    # Initial post shortly after startup
    await asyncio.sleep(5)
    try:
        curr_sig = _active_ap_signature()
        if curr_sig != prev_sig:
            await post_ap_bulletin(bot)
            prev_sig = curr_sig
    except Exception as e:
        logger.warning(f"ap_autopost_watcher initial post: {e}")

    # Keep watching
    while True:
        try:
            curr_sig = _active_ap_signature()
            if curr_sig != prev_sig:
                await post_ap_bulletin(bot)
                prev_sig = curr_sig
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
def nicknames_to_users_file():
    channel = bot.get_channel(1144688789248282806)  # Get specific channel by ID - lobby-talk
    if channel is None:
        logger.error("Channel with ID 1144688789248282806 not found.")
        return

    members = channel.members  # List of members in the channel
    memids = []  # List for storing member names/nicknames
    wurd_teams = []  # List for storing team names matched with members

    # Loop through each member in the channel to get nicknames
    for member in members:
        memids.append(member.nick or member.global_name)  # Add nickname or global name if nickname is None
    for team in teams:
        team_name = ''.join(team)  # Convert team list to a string
        for mem in memids:
            if mem[:4].lower() in team_name.lower():
                wurd_teams.append(team_name)  # Append matching team name

    # Write matched team names to a CSV file
    with open('wurd24users.csv', 'w', newline='') as f:
        wr = csv.writer(f, delimiter='\n')
        wr.writerow(wurd_teams)
    logger.info("wurd24users.csv has been updated with matched team names.")

    # # Print matched team names for verification
    # print('\nmembers:\n')
    # for wt in wurd_teams:
    #     print(wt)
    # print()

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
        pattern_week = r"^week \d{1,2}$"  # accept only 'week' plus one or two numbers
        pattern_all = r"^all$"  # accept only 'all'
        if re.fullmatch(pattern_week, msg_text) or re.fullmatch(pattern_all, msg_text):
            week_schedule = wrd.wurd_sched_main(msg_text)  # Get the weekly or all schedule
            if TEST:
                # TEMP await create_channel() *******************************************************************
                # guild = bot.get_guild(GUILD_ID)
                # await delete_category_channels(guild)  # First, delete existing channels in the category
                # await create_user_user_channels(guild)  # Create channels for user-user teams
                print(f'--------TEST PRINT--------------\n{week_schedule}\n------------TEST PRINT---------')
            else:
                #             schedule forum ID                                advance forum ID
                channel_id = 1290487933131952138 if 'all' in msg_text else 1149401984466681856
                channel = bot.get_channel(channel_id)

                # Split the week_schedule into chunks and send each one
                for chunk in split_message(week_schedule):
                    await channel.send(chunk)  # Send each chunk separately

                if 'week' in msg_text:
                    guild = bot.get_guild(GUILD_ID)
                    await delete_category_channels(guild)  # First, delete existing channels in the category
                    channel_activity_tracker.clear()  # clear channel_activity_tracker for new week
                    await create_user_user_channels(guild)  # Create channels for user-user teams

    # Regex search for time patterns in the message
    player_msg_time = re.search(r'\d{1,2}:\d{2}', msg.content)

    # # Respond with time if a valid time pattern is found in the message
    # if player_msg_time:
    #     await msg.channel.send(f'[testing]The time is {player_msg_time.group()}\n')




# Run the bot with the provided token
bot.run(token)

