# utils/discord_utils.py

def get_lobby_talk_channel(guild):
    return nextcord.utils.get(guild.text_channels, name="lobby-talk")

def admin_or_authorized():
    async def predicate(ctx: commands.Context) -> bool:
        # DM: allow only if user is on the authorized list
        if ctx.guild is None:
            try:
                return int(ctx.author.id) in AUTHORIZED_USERS
            except Exception:
                return False
        # In-guild: Admin role OR authorized ID
        is_admin_role = any(r.name == ADMIN_ROLE_NAME for r in ctx.author.roles)
        is_authorized = False
        try:
            is_authorized = int(ctx.author.id) in AUTHORIZED_USERS
        except Exception:
            pass
        return is_admin_role or is_authorized
    return commands.check(predicate)

# Gate: only Admin role or AUTHORIZED_USERS can use the log reader
def _is_authorized(member) -> bool:
    if any(r.name == ADMIN_ROLE_NAME for r in getattr(member, "roles", [])):
        return True
    try:
        return int(member.id) in AUTHORIZED_USERS
    except Exception:
        return False

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

# Function to fetch members of each team
async def fetch_team_members(guild, team_name):
    # Example logic to match users based on a naming convention or a lookup list (adjust based on your server setup)
    member_ids = []
    for member in guild.members:
        # Check if the member's nickname or username contains part of the team name
        if any(team_part in (member.display_name or member.name).lower() for team_part in team_name.split('-')):
            member_ids.append(member.id)
    return member_ids

