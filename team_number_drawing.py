# I save this script with commits locally and then send to the raspberrync
#
# pip install nextcord
# Run with: TOKEN=your_bot_token python team_selection_order.py
import os, json, random, asyncio
import nextcord
from nextcord.ext import commands
from nextcord.ui import View, Button

from dotenv import load_dotenv
load_dotenv(".env.teamdraw")

# ==== CONFIG ====
STATE_FILE = "team_order_state.json"
TOTAL_NUMBERS = 32                 # set to your league size
ROLE_LIMIT = None                  # e.g., "Madden League" or leave None for everyone

NFL_TEAMS = [
    "ARI","ATL","BAL","BUF","CAR","CHI","CIN","CLE","DAL","DEN","DET","GB",
    "HOU","IND","JAX","KC","LV","LAC","LAR","MIA","MIN","NE","NO","NYG","NYJ",
    "PHI","PIT","SEA","SF","TB","TEN","WAS"
]
TEAM_ALIASES = {
    "CARDINALS":"ARI","FALCONS":"ATL","RAVENS":"BAL","BILLS":"BUF","PANTHERS":"CAR",
    "BEARS":"CHI","BENGALS":"CIN","BROWNS":"CLE","COWBOYS":"DAL","BRONCOS":"DEN","LIONS":"DET","PACKERS":"GB",
    "TEXANS":"HOU","COLTS":"IND","JAGUARS":"JAX","CHIEFS":"KC","RAIDERS":"LV","CHARGERS":"LAC","RAMS":"LAR",
    "DOLPHINS":"MIA","VIKINGS":"MIN","PATRIOTS":"NE","SAINTS":"NO","GIANTS":"NYG","JETS":"NYJ",
    "EAGLES":"PHI","STEELERS":"PIT","SEAHAWKS":"SEA","49ERS":"SF","BUCCANEERS":"TB","TITANS":"TEN","COMMANDERS":"WAS"
}


# ---- Context-aware !help (only active phase) ----

DRAW_PUBLIC = {
    "listorder": "Show the current list of assigned numbers (sorted).",
    "status": "Show draw status, counts, remaining numbers, and a link to the button.",
    "notpicked": "List eligible users who haven’t drawn a number yet.",
}
DRAW_ADMIN = {
    "startorder": "Reset and post the 'Get My Number' button (pins it; unpins prior results).",
    "closeorder": "Close the number draw, compact to 1..N, post mapping + final order, init draft order.",
    "resetorder": "Fully reset state (run `!startorder` after).",
    "remindnotpicked": "Ping users who haven’t drawn a number yet.",
}

DRAFT_PUBLIC = {
    "teams": "Show NFL teams still available during the draft.",
    "draftstatus": "Show who’s on the clock and overall progress.",
    "pick": "Pick your NFL team when it’s your turn. Usage: `!pick DAL` or `!pick Cowboys`.",
    "queue": "Show who’s on the clock and the next N users. Usage: !queue [N]",
    "draftsummary": "Show who has picked and who hasn’t yet, with pick numbers.",
    "picked": "List all users who have picked so far and their team.",
}
DRAFT_ADMIN = {
    "startdraft": "Begin the single-pass team draft (no rounds/timer).",
    "skip": "Skip the current user and move to the next.",
    "undo": "Undo the last pick (returns team to the pool; puts that user back on the clock).",
    "enddraft": "End/close the draft (posts summary if enabled).",
    "pickfor": "Admin pick for a user: !pickfor @User TEAM (alias: !forcepick)",
}



# ==== BOT ====
intents = nextcord.Intents.default()
intents.message_content = True
intents.members = True
bot = commands.Bot(command_prefix="!", intents=intents, help_command=None)
bot.remove_command("help")  # extra safety to avoid any default/alias conflicts
lock = asyncio.Lock()

# Prevents actual pings when sending messages with mentions
ALLOWED = nextcord.AllowedMentions(everyone=False, users=False, roles=False, replied_user=False)

# ==== STATE ====
def load_state():
    if not os.path.exists(STATE_FILE):
        return {
            "assigned": {},                       # {user_id: number}
            "available": list(range(1, TOTAL_NUMBERS + 1)),
            "closed": False,
            "button_message": None                # {"channel_id": int, "message_id": int}
        }
    with open(STATE_FILE, "r", encoding="utf-8") as f:
        return json.load(f)

def save_state(state):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2)

state = load_state()

# ==== UI ====
class NumberView(View):
    def __init__(self):
        super().__init__(timeout=None)

    @nextcord.ui.button(
        label="Get My Number",
        style=nextcord.ButtonStyle.green,
        custom_id="team_selection_get_number"  # required for persistence
    )
    async def get_number(self, button: Button, interaction: nextcord.Interaction):
        # Closed?
        if state.get("closed"):
            await interaction.response.send_message("The draw is closed.", ephemeral=True)
            return

        # Optional role limit
        if ROLE_LIMIT:
            role = nextcord.utils.get(interaction.guild.roles, name=ROLE_LIMIT)
            if not role or role not in interaction.user.roles:
                await interaction.response.send_message(
                    f"You need the **{ROLE_LIMIT}** role to draw a number.", ephemeral=True
                )
                return

        if interaction.user.bot:
            await interaction.response.send_message("Bots can’t draw numbers.", ephemeral=True)
            return

        uid = str(interaction.user.id)
        async with lock:
            if uid in state["assigned"]:
                await interaction.response.send_message(
                    f"You already have number **{state['assigned'][uid]}**.", ephemeral=True
                )
                return
            if not state["available"]:
                await interaction.response.send_message("All numbers have been assigned.", ephemeral=True)
                return

            number = random.choice(state["available"])
            state["available"].remove(number)
            state["assigned"][uid] = number
            save_state(state)

        # Public announcement for transparency
        remaining = len(state["available"])
        await interaction.response.send_message(
            f"{interaction.user.mention} got number **{number}**! "
            f"({remaining} numbers remaining)"
        )

class ClosedView(View):
    def __init__(self):
        super().__init__(timeout=None)
        self.add_item(
            Button(
                label="Drawing Closed",
                style=nextcord.ButtonStyle.gray,
                disabled=True,
                custom_id="team_selection_closed"
            )
        )

@bot.event
async def on_ready():
    # Register persistent views so Discord can keep buttons alive across restarts
    bot.add_view(NumberView())
    bot.add_view(ClosedView())

    # Reattach the correct view to the previously posted message (open vs closed)
    await restore_button_message()

    print(f"✅ Logged in as {bot.user}")


# ==== HELPERS ====
def format_order(guild, title="Team Selection Order"):
    if not state["assigned"]:
        return "No numbers assigned."

    pairs = []
    for uid, num in state["assigned"].items():
        member = guild.get_member(int(uid))
        if member:
            display = f"{member.name} ({member.mention})"  # Username + mention
        else:
            display = f"UserID:{uid} (<@{uid}>)"
        pairs.append((num, display))
    pairs.sort(key=lambda x: x[0])

    lines = [f"**{title}**"]
    for num, display in pairs:
        lines.append(f"• **#{num}** — {display}")
    return "\n".join(lines)

def format_mapping(guild, mapping, title="Old → New Mapping"):
    lines = [f"**{title}**"]
    for uid, old_num, new_num in sorted(mapping, key=lambda x: x[2]):
        member = guild.get_member(int(uid))
        if member:
            display = f"{member.name} ({member.mention})"
        else:
            display = f"UserID:{uid} (<@{uid}>)"
        lines.append(f"• **#{new_num}** — {display} *(was #{old_num})*")
    return "\n".join(lines)

def compact_numbers_with_mapping(guild):
    # [(uid, old_num)] sorted by old_num
    pairs = sorted(state["assigned"].items(), key=lambda kv: kv[1])
    mapping = []  # [(uid, old_num, new_num)]
    new_assigned = {}
    for new_num, (uid, old_num) in enumerate(pairs, start=1):
        new_assigned[uid] = new_num
        mapping.append((uid, old_num, new_num))
    state["assigned"] = new_assigned
    state["available"] = []  # no longer needed after close
    save_state(state)
    return mapping


async def disable_button_message():
    ref = state.get("button_message")
    if not ref:
        return
    channel = bot.get_channel(ref.get("channel_id"))
    if not channel:
        try:
            channel = await bot.fetch_channel(ref.get("channel_id"))
        except Exception:
            return
    try:
        msg = await channel.fetch_message(ref.get("message_id"))
        await msg.edit(content="The draw is now closed. Final order below 👇", view=ClosedView())
    except Exception:
        pass

async def restore_button_message():
    """Reattach the correct view to the existing button message on restart."""
    ref = state.get("button_message")
    if not ref:
        return

    channel = bot.get_channel(ref.get("channel_id"))
    if not channel:
        try:
            channel = await bot.fetch_channel(ref.get("channel_id"))
        except Exception:
            return

    try:
        msg = await channel.fetch_message(ref.get("message_id"))
        if state.get("closed"):
            # Show the disabled/closed view if the draw is closed
            await msg.edit(view=ClosedView())
        else:
            # Reattach the active NumberView so users can keep picking
            await msg.edit(view=NumberView())
    except Exception:
        # Message might have been deleted or permissions changed — ignore quietly
        pass

async def unpin_button_message():
    """Unpin the original 'Click the button…' post, if possible."""
    ref = state.get("button_message")
    if not ref:
        return
    channel = bot.get_channel(ref.get("channel_id"))
    if not channel:
        try:
            channel = await bot.fetch_channel(ref.get("channel_id"))
        except Exception:
            return
    try:
        msg = await channel.fetch_message(ref.get("message_id"))
        await msg.unpin()
    except nextcord.Forbidden:
        # Missing permission to manage messages; ignore
        pass
    except Exception:
        pass

async def unpin_previous_results(channel):
    """Unpin prior final-results messages posted by this bot in this channel."""
    try:
        pinned = await channel.pins()
    except Exception:
        return
    for msg in pinned:
        try:
            if msg.author == bot.user and (
                "Final Team Selection Order (Compacted)" in (msg.content or "") or
                "Old → New Mapping" in (msg.content or "")
            ):
                await msg.unpin()
        except Exception:
            pass

# ==== ELIGIBILITY HELPERS ====
async def get_eligible_members(guild: nextcord.Guild):
    """
    Return the list of human members eligible to pick:
    - If ROLE_LIMIT is set, only members with that role
    - Excludes bots
    Tries fetch_members() (accurate) and falls back to guild.members (cache).
    """
    # Try API fetch for complete/accurate list
    try:
        members = [m async for m in guild.fetch_members(limit=None)]
    except Exception:
        members = list(guild.members)

    # Optional role filtering
    if ROLE_LIMIT:
        role = nextcord.utils.get(guild.roles, name=ROLE_LIMIT)
        if role:
            members = [m for m in members if role in m.roles]

    # Exclude bots
    members = [m for m in members if not m.bot]
    return members

def _norm_team(s):
    if not s:
        return ""
    key = s.strip().upper()
    if key in NFL_TEAMS:
        return key
    return TEAM_ALIASES.get(key, "")

def _current_uid():
    d = state.get("draft") or {}
    order = d.get("order") or []
    idx = d.get("index", 0)
    if not order or idx >= len(order):
        return None
    return order[idx]

def _advance_picker():
    d = state["draft"]
    d["index"] += 1
    save_state(state)

def _format_available():
    d = state.get("draft") or {}
    av = d.get("available") or []
    return ", ".join(sorted(av))

def _format_draftstatus(guild):
    d = state.get("draft")
    if not d:
        return "**Draft:** No draft state."
    if not d.get("open"):
        return "**Draft:** Not started."
    cur_uid = _current_uid()
    if cur_uid is None:
        return "**Draft:** Complete."
    member = guild.get_member(int(cur_uid))
    who = member.mention if member else f"<@{cur_uid}>"
    return (
        f"**Draft:** Active (single pass)\n"
        f"**On the clock:** {who}\n"
        f"**Available teams:** {len(d['available'])}\n"
        f"**Picks made:** {len(d['picks'])}/{len(d['order'])}"
    )

def format_final_teams(guild, title="Final Teams"):
    d = state.get("draft") or {}
    order = d.get("order") or []
    picks = d.get("picks") or {}
    lines = [f"**{title}**"]
    for uid in order:
        member = guild.get_member(int(uid))
        display = f"{member.name} ({member.mention})" if member else f"UserID:{uid} (<@{uid}>)"
        team = picks.get(uid)
        if team:
            lines.append(f"• {display} — **{team}**")
        else:
            lines.append(f"• {display} — (no pick)")
    return "\n".join(lines)

def _format_queue(guild: nextcord.Guild, n: int = 5) -> str:
    d = state.get("draft")
    if not d:
        return "**Draft:** No draft state."
    if not d.get("open"):
        return "**Draft:** Not started."

    order = d.get("order", [])
    idx = d.get("index", 0)

    if not order:
        return "**Draft:** Empty order."
    if idx >= len(order):
        return "**Draft:** Complete."

    lines = ["**Draft Queue**"]
    # current on the clock
    cur_uid = order[idx]
    cur_member = guild.get_member(int(cur_uid))
    cur_display = cur_member.mention if cur_member else f"<@{cur_uid}>"
    lines.append(f"**On the clock:** {cur_display}")

    # next N users
    upcoming = order[idx+1 : idx+1+n]
    if upcoming:
        lines.append("**Up next:**")
        for i, uid in enumerate(upcoming, start=1):
            m = guild.get_member(int(uid))
            who = m.mention if m else f"<@{uid}>"
            lines.append(f"{i}. {who}")
    else:
        lines.append("_No one else in queue._")

    return "\n".join(lines)


def _existing(names, bot_obj):
    cmds = bot_obj.all_commands
    return [n for n in names if n in cmds]

def _phase_flags():
    """Returns (draw_open, draft_open, draw_closed)."""
    closed = bool(state.get("closed"))
    d = state.get("draft") or {}
    draft_open = bool(d.get("open"))
    return (not closed, draft_open, closed)


# ==== COMMANDS ====
@bot.command()
@commands.has_permissions(manage_guild=True)
async def startorder(ctx):
    """Reset everything, post a fresh button, unpin old final-results, and pin the new start post."""
    # Fresh state
    global state
    state = {
        "assigned": {},
        "available": list(range(1, TOTAL_NUMBERS + 1)),
        "closed": False,
        "button_message": None
    }
    save_state(state)

    # Housekeeping: unpin any previous final-results pins
    await unpin_previous_results(ctx.channel)

    # Post the new button
    view = NumberView()
    sent = await ctx.send("Click the button to get your **Team Selection Number**:\n"
                          "Numbers are randomly pulled from the remaining pool (no duplicates, no rerolls).", view=view, allowed_mentions=ALLOWED)
    state["button_message"] = {"channel_id": ctx.channel.id, "message_id": sent.id}
    save_state(state)

    # Pin the new start message
    try:
        await sent.pin()
    except nextcord.Forbidden:
        await ctx.send("I couldn’t pin the message (missing permission). You can pin it manually.", allowed_mentions=ALLOWED)
    except Exception:
        pass

@bot.command()
async def listorder(ctx):
    """Show current list (sorted by number)."""
    msg = format_order(ctx.guild, title="Current Team Selection Numbers")
    await ctx.send(msg, allowed_mentions=ALLOWED)

@bot.command()
async def status(ctx):
    """Show draw status, counts, available numbers, and link to the button message."""
    try:
        assigned_count = len(state.get("assigned", {}))
        remaining_numbers = sorted(state.get("available", []))
        remaining_count = len(remaining_numbers)
        closed = state.get("closed", False)
        ref = state.get("button_message")

        # Build a jump link to the button message if we have it
        jump_link = None
        if ref and ctx.guild:
            guild_id = ctx.guild.id
            ch_id = ref.get("channel_id")
            msg_id = ref.get("message_id")
            if ch_id and msg_id:
                jump_link = f"https://discord.com/channels/{guild_id}/{ch_id}/{msg_id}"

        lines = []
        lines.append(f"**Status:** {'Closed 🔒' if closed else 'Open 🔓'}")
        lines.append(f"**Assigned:** {assigned_count}")
        lines.append(f"**Remaining:** {remaining_count}")

        # Show list of available numbers if any remain
        if remaining_numbers:
            nums_str = ", ".join(str(n) for n in remaining_numbers)
            lines.append(f"**Available Numbers:** {nums_str}")

        if jump_link:
            lines.append(f"**Button Post:** <{jump_link}>")

        await ctx.send("\n".join(lines), allowed_mentions=ALLOWED)

    except Exception as e:
        await ctx.send(f"Could not fetch status: `{e}`", allowed_mentions=ALLOWED)

@bot.command(name="notpicked")
async def notpicked(ctx):
    """
    List users (eligible) who have not drawn a number yet.
    Respects ROLE_LIMIT if set. Does not ping users.
    """
    try:
        assigned = state.get("assigned", {})
        eligible = await get_eligible_members(ctx.guild)

        pending = [m for m in eligible if str(m.id) not in assigned]
        pending.sort(key=lambda m: m.name.lower())

        if not pending:
            await ctx.send("Everyone eligible has picked ✅", allowed_mentions=ALLOWED)
            return

        # Format nicely without pinging (ALLOWED disables user mentions)
        lines = [f"**Haven’t picked yet ({len(pending)}):**"]
        # show username + formatted mention text (won’t ping due to ALLOWED)
        for m in pending:
            lines.append(f"• {m.name} (<@{m.id}>)")

        # Discord has a 2000 char limit; chunk if needed
        msg = "\n".join(lines)
        if len(msg) <= 1900:
            await ctx.send(msg, allowed_mentions=ALLOWED)
        else:
            # chunk into smaller messages
            header = lines[0]
            chunk = [header]
            count = 0
            for line in lines[1:]:
                if sum(len(x) for x in chunk) + len(line) + len(chunk) > 1900:
                    await ctx.send("\n".join(chunk), allowed_mentions=ALLOWED)
                    chunk = [header]
                chunk.append(line)
            if len(chunk) > 1:
                await ctx.send("\n".join(chunk), allowed_mentions=ALLOWED)

    except Exception as e:
        await ctx.send(f"Could not compute not-picked list: `{e}`", allowed_mentions=ALLOWED)

@bot.command(name="remindnotpicked")
@commands.has_permissions(manage_guild=True)
async def remindnotpicked(ctx):
    """
    ADMIN: Mention users who haven't picked yet (pings them).
    """
    try:
        assigned = state.get("assigned", {})
        eligible = await get_eligible_members(ctx.guild)
        pending = [m for m in eligible if str(m.id) not in assigned]
        if not pending:
            await ctx.send("Everyone eligible has picked ✅", allowed_mentions=ALLOWED)
            return

        # Build a ping line (this will ping due to explicit AllowedMentions here)
        mentions_line = " ".join(m.mention for m in pending)
        await ctx.send(
            f"{mentions_line}\nPlease click the button to draw your **Team Selection Number**.",
            allowed_mentions=nextcord.AllowedMentions(everyone=False, users=True, roles=False, replied_user=False)
        )
    except Exception as e:
        await ctx.send(f"Could not send reminders: `{e}`", allowed_mentions=ALLOWED)

def _send_chunks_factory(max_len=1900):
    async def _send_chunks(ctx, text):
        lines = text.splitlines()
        buf = []
        size = 0
        for line in lines:
            # +1 for newline that will be added on join
            if size + len(line) + 1 > max_len and buf:
                await ctx.send("\n".join(buf), allowed_mentions=ALLOWED)
                buf = []
                size = 0
            buf.append(line)
            size += len(line) + 1
        if buf:
            await ctx.send("\n".join(buf), allowed_mentions=ALLOWED)
    return _send_chunks

@bot.command()
@commands.has_permissions(manage_guild=True)
async def closeorder(ctx):
    async with lock:
        if state.get("closed"):
            await ctx.send("Order is already closed.", allowed_mentions=ALLOWED)
            return
        if not state.get("assigned"):  # guard against empty
            await ctx.send(
                "Cannot close: no numbers have been assigned yet. Have users click **Get My Number** first.",
                allowed_mentions=ALLOWED
            )
            return

        state["closed"] = True
        mapping = compact_numbers_with_mapping(ctx.guild)

        # init draft order
        pairs = sorted(state["assigned"].items(), key=lambda kv: kv[1])
        state["draft"] = {
            "open": False,
            "order": [uid for uid, _ in pairs],
            "index": 0,
            "available": [],
            "picks": {},
            "history": []
        }
        save_state(state)

    try:
        await ctx.message.add_reaction("🔒")
    except Exception:
        pass
    await ctx.reply("🔒 Number draw closed. Posting final results…", mention_author=False)

    await disable_button_message()

    old_new = format_mapping(ctx.guild, mapping, title="Old → New Mapping")
    final_order = format_order(ctx.guild, title="Final Team Selection Order (Compacted)")

    # NEW: send in chunks to avoid 2,000-char limit
    send_chunks = _send_chunks_factory()
    await send_chunks(ctx, old_new)
    await send_chunks(ctx, final_order)

    await unpin_button_message()
    # Optionally pin only the final order header chunk: re-send a short header to pin
    try:
        header_msg = await ctx.send("📌 **Final Team Selection Order (Compacted)** — see messages above for full list.", allowed_mentions=ALLOWED)
        try:
            await header_msg.pin()
        except nextcord.Forbidden:
            await ctx.send("I couldn’t pin the final results (missing permission). You can pin it manually.", allowed_mentions=ALLOWED)
    except Exception:
        pass

@bot.command()
@commands.has_permissions(manage_guild=True)
async def showfinal(ctx):
    mapping = compact_numbers_with_mapping(ctx.guild)
    old_new = format_mapping(ctx.guild, mapping, title="Old → New Mapping")
    final_order = format_order(ctx.guild, title="Final Team Selection Order (Compacted)")

    send_chunks = _send_chunks_factory()
    await send_chunks(ctx, old_new)
    await send_chunks(ctx, final_order)

@bot.command()
@commands.has_permissions(manage_guild=True)
async def resetorder(ctx):
    """Reset everything."""
    global state
    state = {
        "assigned": {},
        "available": list(range(1, TOTAL_NUMBERS + 1)),
        "closed": False,
        "button_message": None
    }
    save_state(state)
    await ctx.send("Order has been reset. Use `!startorder` to begin again.")

# ==== RUN ====
TOKEN = os.getenv("TEAM_ORDER_TOKEN")  # <-- unique var for this bot
if not TOKEN:
    raise RuntimeError("TEAM_ORDER_TOKEN is not set")

@bot.command(name="startdraft")
@commands.has_permissions(manage_guild=True)
async def startdraft(ctx):
    """Start the single-pass team draft (no rounds, no timer)."""
    if not state.get("closed"):
        await ctx.send("Close the number draw first with `!closeorder`.", allowed_mentions=ALLOWED)
        return

    # We'll compute who is on the clock while holding the lock,
    # but send the message after releasing it.
    cur_uid = None

    async with lock:
        d = state.get("draft")
        if not d:
            await ctx.send("No draft state found. Try `!closeorder` again.", allowed_mentions=ALLOWED)
            return

        # sanity: must have an order from closeorder
        if not d.get("order"):
            await ctx.send("Draft order is empty. Did anyone draw numbers?", allowed_mentions=ALLOWED)
            return

        # (re)initialize the simple one-pass draft
        d["open"] = True
        d["index"] = 0
        d["available"] = NFL_TEAMS.copy()
        d["picks"] = {}
        d["history"] = []

        save_state(state)  # persist before announcing

        # who is up first?
        cur_uid = _current_uid()

    # Announce after releasing the lock
    if cur_uid is None:
        await ctx.send("Draft cannot start: empty order.", allowed_mentions=ALLOWED)
        return

    member = ctx.guild.get_member(int(cur_uid))
    who = member.mention if member else f"<@{cur_uid}>"
    await ctx.send(
        f"**Draft started.** {who} is on the clock.\n"
        "➡️ To make a pick, type `!pick TEAMCODE` (e.g., `!pick DAL`) "
        "or `!pick TeamName` (e.g., `!pick Cowboys`).\n"
        "If you type it wrong, I’ll remind you of the correct format.",
        allowed_mentions=ALLOWED
    )


@bot.command(name="teams")
async def teams(ctx):
    """Show teams still available to pick."""
    d = state.get("draft")
    if not d or not d.get("open"):
        await ctx.send("Draft not started. Use `!startdraft`.", allowed_mentions=ALLOWED)
        return
    await ctx.send(f"**Available ({len(d['available'])}):**\n{_format_available()}",
                   allowed_mentions=ALLOWED)

@bot.command(name="queue")
async def queue_cmd(ctx, n: int = 5):
    """Show who's on the clock and the next N users. Usage: !queue [N]"""
    # clamp N to something reasonable
    n = max(0, min(n, 25))
    await ctx.send(_format_queue(ctx.guild, n), allowed_mentions=ALLOWED)

@bot.command(name="draftstatus")
async def draftstatus(ctx):
    base = _format_draftstatus(ctx.guild)
    tail = _format_queue(ctx.guild, 3)
    # avoid repeating headings; show queue lines after base
    out = base
    q_lines = tail.splitlines()
    if q_lines and q_lines[0].startswith("**Draft Queue**"):
        q_lines = q_lines[1:]  # drop the header for a tighter message
    if q_lines:
        out += "\n" + "\n".join(q_lines)
    await ctx.send(out, allowed_mentions=ALLOWED)

@bot.command(name="draftsummary")
async def draftsummary(ctx):
    """
    Show who has picked (and which team) and who hasn't yet — without pinging users.
    Shows pick numbers for pending picks.
    """
    d = state.get("draft") or {}
    order = d.get("order") or []
    picks = d.get("picks") or {}

    if not order:
        await ctx.send("No draft order found. Close the number draw and start the draft.", allowed_mentions=ALLOWED)
        return

    picked_lines = ["**Picked so far:**"]
    pending_lines = ["**Not picked yet:**"]

    for idx, uid in enumerate(order, start=1):
        member = ctx.guild.get_member(int(uid))
        name = member.name if member else f"UserID:{uid}"
        team = picks.get(uid)
        if team:
            picked_lines.append(f"• {name} — **{team}**")
        else:
            pending_lines.append(f"• Pick #{idx} — {name}")

    # If nobody has picked yet
    if len(picked_lines) == 1:
        picked_lines.append("• (none yet)")

    # If everyone has picked
    if len(pending_lines) == 1:
        pending_lines.append("• (everyone has picked)")

    # Discord 2000-char safety: send in chunks if needed
    def _send_chunks(lines):
        chunk = []
        count = 0
        for line in lines:
            if count + len(line) + 1 > 1900:
                yield "\n".join(chunk)
                chunk = []
                count = 0
            chunk.append(line)
            count += len(line) + 1
        if chunk:
            yield "\n".join(chunk)

    for block in (_send_chunks(picked_lines), _send_chunks(pending_lines)):
        for msg in block:
            await ctx.send(msg, allowed_mentions=ALLOWED)  # Prevent pings

@bot.command(name="pick")
async def pick(ctx, *, team: str):
    """Pick an NFL team when it's your turn. Example: !pick DAL  or !pick Cowboys"""
    msg_primary = None
    msg_followup = None
    draft_just_completed = False

    async with lock:
        d = state.get("draft")
        if not d or not d.get("open"):
            msg_primary = "Draft not started. Use `!startdraft`."
        else:
            uid = str(ctx.author.id)
            cur_uid = _current_uid()

            if cur_uid is None:
                msg_primary = "Draft is already complete."
            elif uid != cur_uid:
                msg_primary = "It’s not your turn."
            elif uid in d.get("picks", {}):
                msg_primary = f"You already picked **{d['picks'][uid]}**."
            else:
                code = _norm_team(team)
                if not code:
                    msg_primary = (
                        "❌ Unknown team. Please use a valid team code (e.g., `DAL`, `SF`, `NE`) "
                        "or a full name (e.g., `Cowboys`, `49ers`, `Patriots`).\n"
                        "Type `!teams` to see what’s still available."
                    )
                elif code not in d["available"]:
                    msg_primary = "That team is already taken."
                else:
                    d["available"].remove(code)
                    d["picks"][uid] = code
                    d["history"].append({"uid": uid, "team": code})
                    save_state(state)

                    msg_primary = f"{ctx.author.mention} picked **{code}**."

                    _advance_picker()
                    next_uid = _current_uid()
                    if next_uid is None:
                        draft_just_completed = True
                    else:
                        member = ctx.guild.get_member(int(next_uid))
                        who = member.mention if member else f"<@{next_uid}>"
                        msg_followup = f"{who} is on the clock. Use `!pick <TEAM>`."

    # Send after releasing the lock
    if msg_primary:
        await ctx.send(msg_primary, allowed_mentions=ALLOWED)

    if draft_just_completed:
        summary = format_final_teams(ctx.guild, "Final Teams")
        await ctx.send("**Draft complete.**\n" + summary, allowed_mentions=ALLOWED)
    elif msg_followup:
        await ctx.send(msg_followup, allowed_mentions=ALLOWED)

@pick.error
async def pick_error(ctx, error):
    if isinstance(error, commands.MissingRequiredArgument):
        await ctx.send(
            "❌ Usage: `!pick TEAMCODE` (e.g., `!pick DAL`) or `!pick TeamName` (e.g., `!pick Cowboys`).\n"
            "Type `!teams` to see what’s still available.",
            allowed_mentions=ALLOWED
        )
    else:
        # Let other errors bubble to the global handler if you add one later
        raise error

@bot.command(name="pickfor", aliases=["forcepick"])
@commands.has_permissions(manage_guild=True)
async def pickfor(ctx, member: nextcord.Member, *, team: str):
    """Admin: pick a team for a specific user. Usage: !pickfor @User SF  or  !pickfor 123456789012345678 49ers"""
    msg_primary = None
    msg_followup = None
    draft_just_completed = False

    async with lock:
        d = state.get("draft")
        if not d or not d.get("open"):
            msg_primary = "Draft not started. Use `!startdraft`."
        else:
            uid = str(member.id)

            # Must be in the draft order
            if uid not in (d.get("order") or []):
                msg_primary = f"{member.mention} is not in the draft order."
            elif uid in d.get("picks", {}):
                msg_primary = f"{member.mention} already picked **{d['picks'][uid]}**."
            else:
                code = _norm_team(team)
                if not code:
                    msg_primary = "Unknown team. Use a code like `DAL` or a name like `Cowboys`."
                elif code not in d["available"]:
                    msg_primary = "That team is already taken."
                else:
                    # Apply the pick
                    d["available"].remove(code)
                    d["picks"][uid] = code
                    d["history"].append({"uid": uid, "team": code, "by": str(ctx.author.id), "forced": True})
                    save_state(state)

                    msg_primary = f"🛠️ {ctx.author.mention} picked **{code}** for {member.mention}."

                    # If they were on the clock, advance
                    cur_uid = _current_uid()
                    if cur_uid == uid:
                        _advance_picker()
                        next_uid = _current_uid()
                        if next_uid is None:
                            draft_just_completed = True
                        else:
                            m = ctx.guild.get_member(int(next_uid))
                            who = m.mention if m else f"<@{next_uid}>"
                            msg_followup = f"{who} is on the clock. Use `!pick <TEAM>`."
                    # If they weren't on the clock, we don't change turn—queue stays as-is.

    if msg_primary:
        await ctx.send(msg_primary, allowed_mentions=ALLOWED)
    if draft_just_completed:
        summary = format_final_teams(ctx.guild, "Final Teams")
        await ctx.send("**Draft complete.**\n" + summary, allowed_mentions=ALLOWED)
    elif msg_followup:
        await ctx.send(msg_followup, allowed_mentions=ALLOWED)

@bot.command(name="picked")
async def picked(ctx):
    """List all users who have picked so far and their team."""
    d = state.get("draft")
    if not d or not d.get("open"):
        await ctx.send("Draft not started. Use `!startdraft`.", allowed_mentions=ALLOWED)
        return

    picks = d.get("picks", {})
    if not picks:
        await ctx.send("No picks have been made yet.", allowed_mentions=ALLOWED)
        return

    lines = ["**Picks so far:**"]
    for uid, team in picks.items():
        member = ctx.guild.get_member(int(uid))
        display = f"{member.name} ({member.mention})" if member else f"UserID:{uid} (<@{uid}>)"
        lines.append(f"• {display} — **{team}**")

    await ctx.send("\n".join(lines), allowed_mentions=ALLOWED)

@bot.command(name="skip")
@commands.has_permissions(manage_guild=True)
async def skip(ctx):
    """Admin: skip current user and move to the next."""
    msg = None  # what we'll send after releasing the lock

    async with lock:
        d = state.get("draft")
        if not d or not d.get("open"):
            msg = "Draft not started. Use `!startdraft`."
        else:
            cur_uid = _current_uid()
            if cur_uid is None:
                msg = "Draft is already complete."
            else:
                # advance turn
                _advance_picker()
                next_uid = _current_uid()
                if next_uid is None:
                    msg = "Skipped. **Draft complete.**"
                else:
                    member = ctx.guild.get_member(int(next_uid))
                    who = member.mention if member else f"<@{next_uid}>"
                    msg = f"Skipped. {who} is now on the clock."

    # Send after releasing the lock
    await ctx.send(msg, allowed_mentions=ALLOWED)

@bot.command(name="undo")
@commands.has_permissions(manage_guild=True)
async def undo(ctx):
    """Admin: undo the last pick (returns team to the pool and rewinds the turn)."""
    msg = None  # send after releasing the lock

    async with lock:
        d = state.get("draft")
        if not d or not d.get("open"):
            msg = "Draft not started. Use `!startdraft`."
        elif not d.get("history"):
            msg = "No picks to undo."
        else:
            last = d["history"].pop()
            uid = last["uid"]
            team = last["team"]

            # return team and remove assignment
            if team not in d["available"]:
                d["available"].append(team)
            d["picks"].pop(uid, None)

            # rewind turn to the user who just got undone
            d["index"] = max(0, d["index"] - 1)

            save_state(state)

            member = ctx.guild.get_member(int(uid))
            who = member.mention if member else f"<@{uid}>"
            msg = f"Undid last pick: {who} — **{team}**. It’s their turn again."

    await ctx.send(msg, allowed_mentions=ALLOWED)

@bot.command(name="enddraft")
@commands.has_permissions(manage_guild=True)
async def enddraft(ctx):
    """Admin: end the draft."""
    had_draft = False
    async with lock:
        d = state.get("draft")
        if d:
            d["open"] = False
            save_state(state)
            had_draft = True

    if not had_draft:
        await ctx.send("No draft state.", allowed_mentions=ALLOWED)
        return

    summary = format_final_teams(ctx.guild, "Final Teams")
    await ctx.send("Draft ended.\n" + summary, allowed_mentions=ALLOWED)

@bot.command(name="help")
async def help_cmd(ctx):
    """Show help for the active phase only."""
    is_admin = getattr(ctx.author.guild_permissions, "manage_guild", False)
    draw_open, draft_open, draw_closed = _phase_flags()

    lines = ["**WURD25 Team Order & Draft – Help**", ""]

    if draft_open:
        # TEAM DRAFT is active
        lines.append("__Team Draft (active)__")
        for n in _existing(list(DRAFT_PUBLIC.keys()), bot):
            lines.append(f"• **!{n}** — {DRAFT_PUBLIC[n]}")
        if is_admin:
            lines.append("_Admin (requires Manage Server)_")
            for n in _existing(list(DRAFT_ADMIN.keys()), bot):
                lines.append(f"• **!{n}** — {DRAFT_ADMIN[n]}")
        lines.append("")
        lines.append("• Only the user **on the clock** can `!pick`.")
    elif draw_open:
        # NUMBER DRAW is active
        lines.append("__Number Draw (active)__")
        for n in _existing(list(DRAW_PUBLIC.keys()), bot):
            lines.append(f"• **!{n}** — {DRAW_PUBLIC[n]}")
        if is_admin:
            lines.append("_Admin (requires Manage Server)_")
            for n in _existing(list(DRAW_ADMIN.keys()), bot):
                lines.append(f"• **!{n}** — {DRAW_ADMIN[n]}")
        lines.append("")
        notes = []
        if ROLE_LIMIT:
            notes.append(f"Only members with the **{ROLE_LIMIT}** role can draw a number.")
        notes.append("Use the **Get My Number** button to draw your number.")
        lines.extend(f"• {n}" for n in notes)
    else:
        # Between phases (draw closed; draft not started)
        lines.append("__Between Phases__")
        lines.append("• Number Draw is finished. Waiting for the Team Draft to start.")
        if is_admin:
            # Show only what's needed to proceed
            if "startdraft" in bot.all_commands:
                lines.append("• Admin: run **!startdraft** to begin the Team Draft.")
            else:
                lines.append("• Admin: add the draft commands to start the next phase.")
        lines.append("")
        if "finalteams" in bot.all_commands:
            lines.append("• You can view `!finalteams` at any time (if enabled).")

    msg = "\n".join(lines)
    if len(msg) > 1900:
        msg = msg[:1900] + "\n…"
    await ctx.send(msg, allowed_mentions=ALLOWED)


bot.run(TOKEN)
