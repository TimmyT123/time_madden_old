# I save this script with commits locally and then send to the raspberrync
#
# pip install nextcord
# Run with: TOKEN=your_bot_token python team_number_drawing.py
import os, json, random, asyncio, time, re
import nextcord
from nextcord.ext import commands
from nextcord.ui import View, Button

from dotenv import load_dotenv
load_dotenv(".env.teamdraw")

# ==== CONFIG ====
STATE_FILE = os.getenv("TEAM_ORDER_STATE_FILE", "team_order_state.json")
WURD_WHEEL_URL = os.getenv("WURD_WHEEL_URL", "https://wurd-madden.com/team-wheel").strip()
SPIN_DURATION_SECONDS = max(4.0, min(float(os.getenv("TEAM_WHEEL_SPIN_SECONDS", "7.0")), 12.0))
# Keep the draw locked while the website holds the winning number on screen.
# This should match RESULT_HOLD_MS in team_wheel.html (4000 ms by default).
RESULT_HOLD_SECONDS = max(2.0, min(float(os.getenv("TEAM_WHEEL_RESULT_HOLD_SECONDS", "4.0")), 10.0))
TOTAL_NUMBERS = 32                 # Always keep the full 1..32 wheel

# Active WURD owners are members with either an AFC or NFC role.
# Set this to () if you ever want every non-bot server member to be eligible.
ELIGIBLE_ROLE_NAMES = ("AFC", "NFC")

# Backward-compatible single-role option. Leave None when using ELIGIBLE_ROLE_NAMES.
ROLE_LIMIT = None

# Uses operating-system randomness instead of the default pseudo-random generator.
SECURE_RANDOM = random.SystemRandom()

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


TEAM_FULL_NAMES = {
    "ARI":"Arizona Cardinals", "ATL":"Atlanta Falcons", "BAL":"Baltimore Ravens", "BUF":"Buffalo Bills",
    "CAR":"Carolina Panthers", "CHI":"Chicago Bears", "CIN":"Cincinnati Bengals", "CLE":"Cleveland Browns",
    "DAL":"Dallas Cowboys", "DEN":"Denver Broncos", "DET":"Detroit Lions", "GB":"Green Bay Packers",
    "HOU":"Houston Texans", "IND":"Indianapolis Colts", "JAX":"Jacksonville Jaguars", "KC":"Kansas City Chiefs",
    "LV":"Las Vegas Raiders", "LAC":"Los Angeles Chargers", "LAR":"Los Angeles Rams", "MIA":"Miami Dolphins",
    "MIN":"Minnesota Vikings", "NE":"New England Patriots", "NO":"New Orleans Saints", "NYG":"New York Giants",
    "NYJ":"New York Jets", "PHI":"Philadelphia Eagles", "PIT":"Pittsburgh Steelers", "SEA":"Seattle Seahawks",
    "SF":"San Francisco 49ers", "TB":"Tampa Bay Buccaneers", "TEN":"Tennessee Titans", "WAS":"Washington Commanders",
}
FULL_TEAM_ALIASES = {name.upper(): code for code, name in TEAM_FULL_NAMES.items()}

# ---- Context-aware !help (only active phase) ----

DRAW_PUBLIC = {
    "listorder": "Show the current list of assigned numbers (sorted).",
    "status": "Show draw status, counts, remaining numbers, and a link to the button.",
    "notpicked": "List eligible users who haven’t drawn a number yet.",
}
DRAW_ADMIN = {
    "startorder": "Reset and post the live wheel buttons (pins them; unpins prior results).",
    "closeorder": "Close the draw, compact spinners to 1..N, put non-spinners last, and init draft order.",
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
    "pickfromlist": "Use the current owner's highest-ranked available saved team preference.",
    "skip": "Skip the current user and move to the next.",
    "undo": "Undo the last pick (returns team to the pool; puts that user back on the clock).",
    "enddraft": "End/close the draft (posts summary if enabled).",
    "pickfor": "Admin pick for a user: !pickfor @User TEAM (alias: !forcepick)",
}


PREFERENCE_PUBLIC = {
    "preferences": "Open a private form to set/update your ranked M27 team list.",
    "mychoices": "DM yourself your saved ranked team list (during the draft it marks available/taken teams).",
    "clearmychoices": "Delete your own saved team preference list.",
    "notchoices": "List active owners who have not submitted any team preferences yet (no pings).",
}
PREFERENCE_ADMIN = {
    "haschoices": "List active owners who have submitted a team preference list (no rankings).",
    "remindchoices": "Ping active owners who have not submitted a preference list yet.",
    "resetchoices": "Clear every saved team preference list without resetting the number draw.",
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
def fresh_state():
    return {
        "assigned": {},           # {user_id: current number}; raw wheel numbers while open, final pick numbers after close
        "wheel_numbers": {},      # {user_id: original 1..32 wheel number}; preserved after compaction
        "available": list(range(1, TOTAL_NUMBERS + 1)),
        "closed": False,
        "button_message": None,   # {"channel_id": int, "message_id": int}
        "late_users": [],         # eligible owners who did not spin; appended to the end at close
        "final_mapping": [],      # [[uid, old_wheel_number_or_None, final_pick_number], ...]
        "spin_seq": 0,            # increments once per successful spin
        "last_spin": None,        # latest live wheel event
        "spin_history": [],       # public-friendly recent spin events for the WURD wheel page
        "owner_names": {},        # {uid: {username, display_name}} for final order display
        "spin_active_until": 0.0, # prevents overlapping wheel animations
        "team_preferences": {}   # {user_id: [team_code, ...]} private ranked backup lists
    }

def load_state():
    if not os.path.exists(STATE_FILE):
        return fresh_state()

    with open(STATE_FILE, "r", encoding="utf-8") as f:
        loaded = json.load(f)

    # Backward-compatible defaults for state files created by older versions.
    loaded.setdefault("assigned", {})
    loaded.setdefault("available", list(range(1, TOTAL_NUMBERS + 1)))
    loaded.setdefault("closed", False)
    loaded.setdefault("button_message", None)
    loaded.setdefault("wheel_numbers", dict(loaded["assigned"]) if not loaded["closed"] else {})
    loaded.setdefault("late_users", [])
    loaded.setdefault("final_mapping", [])
    loaded.setdefault("spin_seq", 0)
    loaded.setdefault("last_spin", None)
    loaded.setdefault("spin_history", [])
    loaded.setdefault("owner_names", {})
    loaded.setdefault("spin_active_until", 0.0)
    loaded.setdefault("team_preferences", {})
    return loaded

def save_state(state):
    """Atomically save state so the Discord bot / future website never reads a half-written JSON file."""
    tmp_file = STATE_FILE + ".tmp"
    with open(tmp_file, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2)
    os.replace(tmp_file, STATE_FILE)

state = load_state()

# ==== UI ====
class TeamPreferenceModal(nextcord.ui.Modal):
    """Private editor for an owner's ranked Madden 27 team preferences."""

    def __init__(self, user_id: str):
        super().__init__(title="Madden 27 Team Preferences", timeout=300)
        self.user_id = str(user_id)
        existing = (state.get("team_preferences") or {}).get(self.user_id, [])
        default_text = "\n".join(
            f"{i}. {TEAM_FULL_NAMES.get(code, code)}"
            for i, code in enumerate(existing, start=1)
        )
        self.ranked_teams = nextcord.ui.TextInput(
            label="Rank teams: 1st choice, 2nd, 3rd...",
            placeholder="1. Falcons\n2. Ravens\n3. Bills\n...",
            style=nextcord.TextInputStyle.paragraph,
            required=True,
            max_length=1600,
            default_value=default_text or None,
        )
        self.add_item(self.ranked_teams)

    async def callback(self, interaction: nextcord.Interaction):
        if str(interaction.user.id) != self.user_id:
            await interaction.response.send_message("That preference form belongs to another user.", ephemeral=True)
            return

        teams, invalid, duplicates = _parse_preference_text(str(self.ranked_teams.value or ""))
        if invalid:
            shown = ", ".join(f"`{x}`" for x in invalid[:8])
            extra = f" (+{len(invalid)-8} more)" if len(invalid) > 8 else ""
            await interaction.response.send_message(
                "❌ I couldn't recognize: " + shown + extra +
                "\nUse NFL abbreviations, nicknames, or full team names. Nothing was saved.",
                ephemeral=True,
            )
            return
        if not teams:
            await interaction.response.send_message("Enter at least one NFL team. Nothing was saved.", ephemeral=True)
            return

        async with lock:
            state.setdefault("team_preferences", {})[self.user_id] = teams
            member = interaction.user
            state.setdefault("owner_names", {})[self.user_id] = {
                "username": getattr(member, "name", str(member)),
                "display_name": getattr(member, "display_name", getattr(member, "name", str(member))),
            }
            save_state(state)

        preview = " > ".join(teams[:12])
        if len(teams) > 12:
            preview += f" > … (+{len(teams)-12} more)"
        dup_note = f"\n♻️ {len(duplicates)} duplicate(s) were ignored." if duplicates else ""
        guarantee_note = (
            "\n✅ You ranked all 32 teams, so the list can always supply a backup pick."
            if len(teams) == 32 else
            f"\n💡 You saved {len(teams)} team(s). You can rank all 32 if you want a guaranteed backup."
        )
        await interaction.response.send_message(
            f"✅ **Saved privately.**\n{preview}{dup_note}{guarantee_note}\n"
            "You can update this list any time before your team is picked.",
            ephemeral=True,
        )


class PreferencePromptView(View):
    """Short-lived launcher used by !preferences; only the requester may open it."""
    def __init__(self, user_id: int):
        super().__init__(timeout=180)
        self.user_id = int(user_id)

    @nextcord.ui.button(label="Set / Update My Team List", style=nextcord.ButtonStyle.blurple, emoji="📋")
    async def open_preferences(self, button: Button, interaction: nextcord.Interaction):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("This button belongs to another owner. Type `!preferences` for your own.", ephemeral=True)
            return
        await interaction.response.send_modal(TeamPreferenceModal(str(interaction.user.id)))


class NumberView(View):
    def __init__(self):
        super().__init__(timeout=None)

        # A link button cannot also run a Discord callback, so the pinned post
        # has one button to WATCH the WURD wheel and one button to SPIN it.
        if WURD_WHEEL_URL:
            self.add_item(
                Button(
                    label="Open Live Wheel",
                    style=nextcord.ButtonStyle.link,
                    url=WURD_WHEEL_URL,
                    emoji="🎡",
                    row=0,
                )
            )

    @nextcord.ui.button(
        label="Team Preferences",
        style=nextcord.ButtonStyle.blurple,
        custom_id="team_selection_preferences_open",
        emoji="📋",
        row=1,
    )
    async def set_preferences(self, button: Button, interaction: nextcord.Interaction):
        if interaction.user.bot:
            await interaction.response.send_message("Bots don't need team preferences.", ephemeral=True)
            return
        await interaction.response.send_modal(TeamPreferenceModal(str(interaction.user.id)))

    @nextcord.ui.button(
        label="Spin My Number",
        style=nextcord.ButtonStyle.green,
        custom_id="team_selection_get_number",
        emoji="🎲",
        row=0,
    )
    async def get_number(self, button: Button, interaction: nextcord.Interaction):
        """Handle one official wheel spin.

        Keep the component callback fast and deterministic: acknowledge Discord
        immediately, trust access to the official team-selection post, choose the
        official result locally, persist it, then let the website animate it.
        """
        # Fast local checks before acknowledging.
        if state.get("closed"):
            await interaction.response.send_message("The draw is closed.", ephemeral=True)
            return

        if interaction.user.bot:
            await interaction.response.send_message("Bots can’t draw numbers.", ephemeral=True)
            return

        # Reject stale buttons from an older drawing.
        ref = state.get("button_message") or {}
        current_message_id = ref.get("message_id")
        current_channel_id = ref.get("channel_id")
        if (
            not interaction.message
            or interaction.message.id != current_message_id
            or getattr(interaction.channel, "id", None) != current_channel_id
        ):
            await interaction.response.send_message(
                "This is an old team-selection drawing button. Please use the current pinned draw post.",
                ephemeral=True,
            )
            return

        # ACK with a real ephemeral message immediately.  Do not call Discord's
        # member API here; everyone who can use the official team-selection post
        # is already controlled by the channel/league setup, and the extra fetch
        # was able to stall a component interaction on the Pi.
        await interaction.response.send_message(
            "🎡 Starting your spin… watch the live WURD wheel!",
            ephemeral=True,
        )

        uid = str(interaction.user.id)
        member = interaction.user
        if interaction.guild:
            member = interaction.guild.get_member(interaction.user.id) or interaction.user

        spin_event = None

        try:
            async with lock:
                if uid in state["assigned"]:
                    wheel_num = state.get("wheel_numbers", {}).get(uid, state["assigned"][uid])
                    await interaction.edit_original_message(
                        content=f"You already spun **#{wheel_num}**."
                    )
                    return

                if not state["available"]:
                    await interaction.edit_original_message(content="All 32 numbers have been assigned.")
                    return

                now = time.time()
                active_until = float(state.get("spin_active_until") or 0.0)
                if active_until > now:
                    wait_for = max(1, int(active_until - now + 0.999))
                    await interaction.edit_original_message(
                        content=f"🎡 The wheel is already spinning. Try again in about **{wait_for} second(s)**."
                    )
                    return

                pool = sorted(int(n) for n in state["available"])
                number = SECURE_RANDOM.choice(pool)
                state["available"].remove(number)
                state["assigned"][uid] = number
                state.setdefault("wheel_numbers", {})[uid] = number
                state.setdefault("owner_names", {})[uid] = {
                    "username": getattr(member, "name", interaction.user.name),
                    "display_name": getattr(member, "display_name", interaction.user.display_name),
                }

                state["spin_seq"] = int(state.get("spin_seq", 0)) + 1
                started_at_ms = int(time.time() * 1000)
                duration_ms = int(SPIN_DURATION_SECONDS * 1000)
                spin_event = {
                    "seq": state["spin_seq"],
                    "user_id": uid,
                    "username": getattr(member, "name", interaction.user.name),
                    "display_name": getattr(member, "display_name", interaction.user.display_name),
                    "number": number,
                    "remaining": len(state["available"]),
                    "started_at_ms": started_at_ms,
                    "duration_ms": duration_ms,
                    "pool": pool,
                }
                state["last_spin"] = spin_event
                history = state.setdefault("spin_history", [])
                history.append(dict(spin_event))
                state["spin_history"] = history[-32:]
                state["spin_active_until"] = time.time() + SPIN_DURATION_SECONDS + RESULT_HOLD_SECONDS + 0.35
                save_state(state)

            # At this point the result is official and the website can see it.
            await interaction.edit_original_message(
                content="🎡 Your spin is underway — watch the live WURD wheel!"
            )

            watch_line = f"\n👀 Watch it live: <{WURD_WHEEL_URL}>" if WURD_WHEEL_URL else ""
            try:
                await interaction.channel.send(
                    f"🎡 **{interaction.user.display_name} is spinning the wheel!**{watch_line}",
                    allowed_mentions=ALLOWED,
                )
            except Exception as e:
                print(f"⚠️ Could not post spin-start announcement: {e}", flush=True)

            # Let the website animation finish before Discord reveals the result.
            await asyncio.sleep(SPIN_DURATION_SECONDS)

            # Do NOT clear spin_active_until yet.  The website deliberately keeps
            # the winning number parked under the pointer for RESULT_HOLD_SECONDS.
            # Leaving the timestamp in place prevents another owner from starting
            # a new spin until that result display/reset window has finished.

            remaining = spin_event["remaining"]
            result_text = (
                f"🎉 **{interaction.user.display_name} landed on #{spin_event['number']}!** "
                f"({remaining} numbers remaining)"
            )
            try:
                await interaction.channel.send(result_text, allowed_mentions=ALLOWED)
            except Exception as e:
                print(f"⚠️ Could not post spin result: {e}", flush=True)
                await interaction.edit_original_message(
                    content=f"{result_text} (Public Discord announcement failed, but this result is saved.)"
                )

        except Exception as e:
            # Make failures visible both in Discord and systemd journal.
            import traceback
            print(
                f"❌ Team draw spin failed for user {uid}: {type(e).__name__}: {e}",
                flush=True,
            )
            traceback.print_exc()
            try:
                if spin_event is None:
                    await interaction.edit_original_message(
                        content=(
                            "❌ The spin could not start and **no number was assigned**. "
                            f"Error: `{type(e).__name__}: {e}`"
                        )
                    )
                else:
                    await interaction.edit_original_message(
                        content=(
                            f"⚠️ Your official number is **#{spin_event['number']}**, but a display/announcement step failed. "
                            f"Error: `{type(e).__name__}: {e}`"
                        )
                    )
            except Exception:
                pass

class ClosedView(View):
    def __init__(self):
        super().__init__(timeout=None)
        self.add_item(
            Button(
                label="Drawing Closed",
                style=nextcord.ButtonStyle.gray,
                disabled=True,
                custom_id="team_selection_closed",
                row=0,
            )
        )
        if WURD_WHEEL_URL:
            self.add_item(
                Button(
                    label="View Wheel Results",
                    style=nextcord.ButtonStyle.link,
                    url=WURD_WHEEL_URL,
                    emoji="🎡",
                    row=0,
                )
            )

    @nextcord.ui.button(
        label="Team Preferences",
        style=nextcord.ButtonStyle.blurple,
        custom_id="team_selection_preferences_closed",
        emoji="📋",
        row=1,
    )
    async def set_preferences(self, button: Button, interaction: nextcord.Interaction):
        if interaction.user.bot:
            await interaction.response.send_message("Bots don't need team preferences.", ephemeral=True)
            return
        await interaction.response.send_modal(TeamPreferenceModal(str(interaction.user.id)))

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

        if old_num is None:
            lines.append(f"• **#{new_num}** — {display} *(did not spin — placed at end)*")
        else:
            lines.append(f"• **#{new_num}** — {display} *(wheel #{old_num})*")
    return "\n".join(lines)

def compact_numbers_with_mapping(guild, late_uids=None):
    """
    Compact actual wheel spins by their original 1..32 number.
    Eligible users who never spun are appended after all spinners.
    Their order is randomized within the late group for fairness.
    """
    late_uids = list(late_uids or [])

    # Preserve original wheel numbers before overwriting state["assigned"].
    raw_pairs = sorted(state["assigned"].items(), key=lambda kv: kv[1])
    state.setdefault("wheel_numbers", {})
    for uid, old_num in raw_pairs:
        state["wheel_numbers"].setdefault(uid, old_num)

    mapping = []  # [(uid, old_num_or_None, new_num)]
    new_assigned = {}

    for new_num, (uid, old_num) in enumerate(raw_pairs, start=1):
        new_assigned[uid] = new_num
        mapping.append((uid, old_num, new_num))

    next_num = len(new_assigned) + 1
    for uid in late_uids:
        # Ignore duplicates defensively.
        if uid in new_assigned:
            continue
        new_assigned[uid] = next_num
        mapping.append((uid, None, next_num))
        next_num += 1

    state["assigned"] = new_assigned
    state["late_users"] = late_uids
    state["final_mapping"] = [list(item) for item in mapping]
    state["available"] = []  # wheel is no longer active after close
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
def _role_tokens(role_name: str) -> set[str]:
    """Normalize a Discord role name into uppercase word tokens.

    Examples:
      "NFC" -> {"NFC"}
      "🏈 NFC Owners" -> {"NFC", "OWNERS"}
      "AFC-Team" -> {"AFC", "TEAM"}
    """
    return set(re.findall(r"[A-Z0-9]+", (role_name or "").upper()))

def member_is_eligible(member: nextcord.Member) -> bool:
    """True when a human member is an active team owner for this drawing."""
    if not member or member.bot:
        return False

    roles = list(getattr(member, "roles", []) or [])

    if ELIGIBLE_ROLE_NAMES:
        wanted = {name.upper() for name in ELIGIBLE_ROLE_NAMES}
        for role in roles:
            if wanted & _role_tokens(getattr(role, "name", "")):
                return True
        return False

    if ROLE_LIMIT:
        target = ROLE_LIMIT.strip().casefold()
        return any((getattr(role, "name", "") or "").strip().casefold() == target for role in roles)

    return True

async def get_fresh_interaction_member(interaction: nextcord.Interaction):
    """Return a guild Member with fresh role data for a button interaction.

    Discord component interactions can occasionally hand the bot a member object
    whose cached role list is stale.  Prefer the guild cache, then fetch the member
    directly from Discord before deciding that an owner is ineligible.
    """
    if not interaction.guild:
        return interaction.user

    member = interaction.guild.get_member(interaction.user.id)

    # Fetch directly as a final authority. This is only done when someone clicks
    # the draw button, so the extra API request is tiny and makes role checks robust.
    try:
        member = await interaction.guild.fetch_member(interaction.user.id)
    except Exception:
        pass

    return member or interaction.user

def _eligible_role_text() -> str:
    if ELIGIBLE_ROLE_NAMES:
        pretty = " or ".join(f"**{name}**" for name in ELIGIBLE_ROLE_NAMES)
        return f"the {pretty} role"
    if ROLE_LIMIT:
        return f"the **{ROLE_LIMIT}** role"
    return "an eligible league role"

async def get_eligible_members(guild: nextcord.Guild):
    """
    Return the current human members eligible to pick.
    With the default WURD config, that means members with AFC or NFC.
    """
    try:
        members = [m async for m in guild.fetch_members(limit=None)]
    except Exception:
        members = list(guild.members)

    return [m for m in members if member_is_eligible(m)]

def _norm_team(s):
    if not s:
        return ""
    key = str(s).strip().upper()
    if key in NFL_TEAMS:
        return key
    return TEAM_ALIASES.get(key, "") or FULL_TEAM_ALIASES.get(key, "")

def _parse_preference_text(text: str):
    """Return (team_codes, invalid_items, duplicate_items), preserving rank order."""
    normalized = str(text or "").replace(";", "\n").replace(",", "\n").replace(">", "\n")
    teams, invalid, duplicates = [], [], []
    seen = set()
    for raw in normalized.splitlines():
        item = raw.strip()
        if not item:
            continue
        item = re.sub(r"^\s*\d+\s*(?:[.)\-:]\s*|\s+)", "", item).strip()
        code = _norm_team(item)
        if not code:
            invalid.append(item or raw.strip())
            continue
        if code in seen:
            duplicates.append(code)
            continue
        seen.add(code)
        teams.append(code)
    return teams, invalid, duplicates

def _format_preferences(uid: str, draft_available=None) -> str:
    prefs = (state.get("team_preferences") or {}).get(str(uid), [])
    if not prefs:
        return "No saved team preferences."
    available_set = set(draft_available) if draft_available is not None else None
    lines = [f"**Your Madden 27 Team Preferences ({len(prefs)}):**"]
    for rank, code in enumerate(prefs, start=1):
        name = TEAM_FULL_NAMES.get(code, code)
        marker = "" if available_set is None else (" ✅ available" if code in available_set else " ❌ taken")
        lines.append(f"{rank}. **{code}** — {name}{marker}")
    return "\n".join(lines)

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
    """Reset everything, retire the previous button, post a fresh button, and pin it."""
    global state

    # Retire the previously tracked draw button BEFORE replacing state. This keeps
    # an old season/test button from looking active after a new drawing begins.
    await disable_button_message()
    await unpin_button_message()

    # Fresh number/draft state, but preserve private team preferences.
    # Owners can submit them days early; use !resetchoices if you really want to wipe them.
    saved_preferences = dict(state.get("team_preferences") or {})
    state = fresh_state()
    state["team_preferences"] = saved_preferences
    save_state(state)

    # Housekeeping: unpin any previous final-results pins
    await unpin_previous_results(ctx.channel)

    # Post the new button
    view = NumberView()
    sent = await ctx.send(
        "🎡 **Madden 27 Official Team Selection Number Draw**\n"
        "1. Open the **Live Wheel** so you can watch it spin.\n"
        "2. Click **Spin My Number** when you are ready.\n"
        "3. Use **Team Preferences** any time to privately rank your Madden 27 teams in advance.\n\n"
        "The wheel starts with **32 numbers**. Every result is randomly selected from the remaining pool "
        "(no duplicates, no rerolls). Owners who do not spin before the draw closes are placed at the end.\n"
        "Your preference list is a backup only — your live manual team pick always wins.",
        view=view,
        allowed_mentions=ALLOWED,
    )
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

@bot.command(name="mydrawroles")
async def mydrawroles(ctx):
    """Diagnostic: show the role names the bot currently sees for you."""
    member = ctx.guild.get_member(ctx.author.id) if ctx.guild else ctx.author
    roles = [r.name for r in (getattr(member, "roles", []) or []) if r.name != "@everyone"]
    text = ", ".join(roles) if roles else "(no named roles found)"
    await ctx.send(f"**Roles I see for {ctx.author.name}:** {text}", allowed_mentions=ALLOWED)

@bot.command(name="notpicked")
async def notpicked(ctx):
    """
    List users (eligible) who have not drawn a number yet.
    Uses the active-owner role filter. Does not ping users.
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
            f"{mentions_line}\nPlease click **Spin My Number** to draw your **Team Selection Number**.",
            allowed_mentions=nextcord.AllowedMentions(everyone=False, users=True, roles=False, replied_user=False)
        )
    except Exception as e:
        await ctx.send(f"Could not send reminders: `{e}`", allowed_mentions=ALLOWED)

@bot.command(name="preferences", aliases=["choices", "teamchoices"])
async def preferences_cmd(ctx):
    """Open a private modal launcher for the requesting owner's ranked team list."""
    await ctx.send(
        f"📋 {ctx.author.mention} click below to privately set or update your Madden 27 team preferences. "
        "The rankings and save confirmation are private.",
        view=PreferencePromptView(ctx.author.id),
        allowed_mentions=ALLOWED,
        delete_after=180,
    )

@bot.command(name="mychoices")
async def mychoices(ctx):
    """DM the requesting owner their private ranked list."""
    d = state.get("draft") or {}
    available = d.get("available") if d.get("open") else None
    text = _format_preferences(str(ctx.author.id), available)
    try:
        await ctx.author.send(text + "\n\nUse `!preferences` in team-selection to edit it.")
        await ctx.send(f"📬 {ctx.author.mention} I sent your saved team preferences by DM.", allowed_mentions=ALLOWED, delete_after=20)
    except Exception:
        await ctx.send(
            f"{ctx.author.mention} I couldn't DM you. Use `!preferences`; the private form will open with your saved list pre-filled.",
            allowed_mentions=ALLOWED,
        )

@bot.command(name="clearmychoices")
async def clearmychoices(ctx):
    uid = str(ctx.author.id)
    async with lock:
        existed = bool((state.get("team_preferences") or {}).pop(uid, None))
        save_state(state)
    await ctx.send(
        "🗑️ Your saved team preference list was cleared." if existed else "You did not have a saved team preference list.",
        allowed_mentions=ALLOWED, delete_after=25,
    )

@bot.command(name="notchoices")
async def notchoices(ctx):
    """List active owners with no preference list; rankings remain private."""
    try:
        eligible = await get_eligible_members(ctx.guild)
        prefs = state.get("team_preferences") or {}
        pending = [m for m in eligible if not prefs.get(str(m.id))]
        pending.sort(key=lambda m: m.display_name.lower())
        submitted = len(eligible) - len(pending)
        if not pending:
            await ctx.send(f"✅ All **{len(eligible)}** active owners have submitted team preferences.", allowed_mentions=ALLOWED)
            return
        lines = [f"**Team preference lists:** {submitted}/{len(eligible)} submitted", f"**Still missing ({len(pending)}):**"]
        lines.extend(f"• {m.display_name}" for m in pending)
        await ctx.send("\n".join(lines), allowed_mentions=ALLOWED)
    except Exception as e:
        await ctx.send(f"Could not compute preference status: `{e}`", allowed_mentions=ALLOWED)

@bot.command(name="haschoices")
@commands.has_permissions(manage_guild=True)
async def haschoices(ctx):
    """Admin: list active owners who have a saved preference list; never reveal rankings."""
    try:
        eligible = await get_eligible_members(ctx.guild)
        prefs = state.get("team_preferences") or {}
        submitted_members = [m for m in eligible if prefs.get(str(m.id))]
        submitted_members.sort(key=lambda m: m.display_name.lower())

        if not submitted_members:
            await ctx.send(
                "**Owners with Team Preferences (0):**\n• (none yet)",
                allowed_mentions=ALLOWED,
            )
            return

        lines = [f"**Owners with Team Preferences ({len(submitted_members)}/{len(eligible)}):**"]
        lines.extend(f"• {m.display_name}" for m in submitted_members)
        await ctx.send("\n".join(lines), allowed_mentions=ALLOWED)
    except Exception as e:
        await ctx.send(f"Could not compute submitted preference status: `{e}`", allowed_mentions=ALLOWED)

@bot.command(name="remindchoices")
@commands.has_permissions(manage_guild=True)
async def remindchoices(ctx):
    """Admin: ping active owners who still have no saved preference list."""
    try:
        eligible = await get_eligible_members(ctx.guild)
        prefs = state.get("team_preferences") or {}
        pending = [m for m in eligible if not prefs.get(str(m.id))]
        if not pending:
            await ctx.send("Everyone active has submitted team preferences ✅", allowed_mentions=ALLOWED)
            return
        mentions = " ".join(m.mention for m in pending)
        await ctx.send(
            f"{mentions}\n📋 Please submit a private Madden 27 team preference list now so we have a backup if you're unavailable during team selection. "
            "Type `!preferences` or use the **Team Preferences** button on the number-draw post.",
            allowed_mentions=nextcord.AllowedMentions(everyone=False, users=True, roles=False, replied_user=False),
        )
    except Exception as e:
        await ctx.send(f"Could not send preference reminders: `{e}`", allowed_mentions=ALLOWED)

@bot.command(name="resetchoices")
@commands.has_permissions(manage_guild=True)
async def resetchoices(ctx):
    """Admin: clear all saved preference lists without touching number/draft state."""
    async with lock:
        count = len(state.get("team_preferences") or {})
        state["team_preferences"] = {}
        save_state(state)
    await ctx.send(f"🗑️ Cleared **{count}** saved team preference list(s). The number draw was not changed.", allowed_mentions=ALLOWED)

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
    # Determine the CURRENT active-owner list before closing.
    # Owners who are still AFC/NFC but never spun will be sent to the back.
    eligible_members = await get_eligible_members(ctx.guild)

    async with lock:
        if state.get("closed"):
            await ctx.send("Order is already closed.", allowed_mentions=ALLOWED)
            return
        if float(state.get("spin_active_until") or 0.0) > time.time():
            await ctx.send("The wheel is still spinning. Close the draw after the current spin finishes.", allowed_mentions=ALLOWED)
            return
        if not state.get("assigned"):  # guard against empty
            await ctx.send(
                "Cannot close: no numbers have been assigned yet. Have users click **Spin My Number** first.",
                allowed_mentions=ALLOWED
            )
            return

        owner_names = state.setdefault("owner_names", {})
        for m in eligible_members:
            owner_names[str(m.id)] = {
                "username": m.name,
                "display_name": m.display_name,
            }

        assigned_uids = set(state.get("assigned", {}).keys())
        late_uids = [str(m.id) for m in eligible_members if str(m.id) not in assigned_uids]

        # Non-spinners are all behind the spinners, but randomize their order
        # so there is no commissioner-controlled ordering within that last group.
        SECURE_RANDOM.shuffle(late_uids)

        state["closed"] = True
        mapping = compact_numbers_with_mapping(ctx.guild, late_uids=late_uids)

        # init draft order (now includes non-spinners at the end)
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

    old_new = format_mapping(ctx.guild, mapping, title="Wheel → Final Pick Mapping")
    final_order = format_order(ctx.guild, title="Final Team Selection Order (Compacted)")

    # NEW: send in chunks to avoid 2,000-char limit
    send_chunks = _send_chunks_factory()
    await send_chunks(ctx, old_new)
    await send_chunks(ctx, final_order)

    late_uids = state.get("late_users", [])
    if late_uids:
        await ctx.send(
            f"⏰ **{len(late_uids)} owner(s) did not spin.** "
            "They were placed after everyone who spun; their order within the late group was randomized.",
            allowed_mentions=ALLOWED
        )

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
    if not state.get("closed"):
        await ctx.send("The number draw is still open. Use `!closeorder` first.", allowed_mentions=ALLOWED)
        return

    mapping = state.get("final_mapping") or []
    if not mapping:
        await ctx.send("No saved final mapping was found.", allowed_mentions=ALLOWED)
        return

    old_new = format_mapping(ctx.guild, mapping, title="Wheel → Final Pick Mapping")
    final_order = format_order(ctx.guild, title="Final Team Selection Order (Compacted)")

    send_chunks = _send_chunks_factory()
    await send_chunks(ctx, old_new)
    await send_chunks(ctx, final_order)

@bot.command()
@commands.has_permissions(manage_guild=True)
async def resetorder(ctx):
    """Reset number/draft state while preserving saved team preferences."""
    global state
    saved_preferences = dict(state.get("team_preferences") or {})
    state = fresh_state()
    state["team_preferences"] = saved_preferences
    save_state(state)
    await ctx.send("Order has been reset. Saved team preferences were kept. Use `!startorder` to begin again.")

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
        "Saved team preferences are **backup lists only**; a manual pick always wins. "
        "If an owner is unavailable, an admin can use `!pickfromlist` to take their highest-ranked available team.\n"
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

@bot.command(name="pickfromlist")
@commands.has_permissions(manage_guild=True)
async def pickfromlist(ctx):
    """Admin: use the current owner's highest-ranked saved preference still available."""
    msg_primary = None
    msg_followup = None
    draft_just_completed = False
    async with lock:
        d = state.get("draft")
        if not d or not d.get("open"):
            msg_primary = "Draft not started. Use `!startdraft`."
        else:
            uid = _current_uid()
            if uid is None:
                msg_primary = "Draft is already complete."
            else:
                prefs = (state.get("team_preferences") or {}).get(uid, [])
                member = ctx.guild.get_member(int(uid))
                who = member.mention if member else f"<@{uid}>"
                if not prefs:
                    # Keep preference details private in public draft chat.
                    msg_primary = f"⚠️ I couldn't make an automatic pick for {who}. They remain on the clock."
                else:
                    chosen = rank = None
                    for i, code in enumerate(prefs, start=1):
                        if code in d["available"]:
                            chosen, rank = code, i
                            break
                    if not chosen:
                        # Do not reveal how many teams the owner ranked or which ones were unavailable.
                        msg_primary = f"⚠️ I couldn't make an automatic pick for {who}. They remain on the clock."
                    else:
                        d["available"].remove(chosen)
                        d["picks"][uid] = chosen
                        d["history"].append({
                            "uid": uid, "team": chosen, "by": str(ctx.author.id),
                            "from_preferences": True, "preference_rank": rank,
                        })
                        save_state(state)
                        team_name = TEAM_FULL_NAMES.get(chosen, chosen)
                        # Public result intentionally does not reveal that a preference list was used
                        # or where this team ranked on the owner's private list.
                        msg_primary = f"{who} picked **{team_name} ({chosen})**."
                        _advance_picker()
                        next_uid = _current_uid()
                        if next_uid is None:
                            draft_just_completed = True
                        else:
                            m = ctx.guild.get_member(int(next_uid))
                            next_who = m.mention if m else f"<@{next_uid}>"
                            msg_followup = f"{next_who} is on the clock. Use `!pick <TEAM>`."
    if msg_primary:
        await ctx.send(msg_primary, allowed_mentions=ALLOWED)
    if draft_just_completed:
        summary = format_final_teams(ctx.guild, "Final Teams")
        await ctx.send("**Draft complete.**\n" + summary, allowed_mentions=ALLOWED)
    elif msg_followup:
        await ctx.send(msg_followup, allowed_mentions=ALLOWED)

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

    lines = ["**WURD M27 Team Order & Draft – Help**", ""]

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
        if ROLE_LIMIT and not ELIGIBLE_ROLE_NAMES:
            notes.append(f"Only members with the **{ROLE_LIMIT}** role can draw a number.")
        notes.append("Open the live WURD wheel, then use **Spin My Number** to draw your number.")
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

    lines.append("")
    lines.append("__Private Team Preferences (available anytime)__")
    for n in _existing(list(PREFERENCE_PUBLIC.keys()), bot):
        lines.append(f"• **!{n}** — {PREFERENCE_PUBLIC[n]}")
    if is_admin:
        lines.append("_Preference admin_")
        for n in _existing(list(PREFERENCE_ADMIN.keys()), bot):
            lines.append(f"• **!{n}** — {PREFERENCE_ADMIN[n]}")
    lines.append("• Rankings stay private; `!notchoices` only shows who has/hasn't submitted.")
    lines.append("• A saved list never overrides an owner's live manual pick.")

    msg = "\n".join(lines)
    send_chunks = _send_chunks_factory()
    await send_chunks(ctx, msg)


bot.run(TOKEN)
