# utils/parsing.py

# Link finder + nickname/team parsing (helpers)
LINK_RE = re.compile(
    r"(https?://(?:www\.)?twitch\.tv/[A-Za-z0-9_]{4,25}"
    r"|https?://(?:www\.)?youtube\.com/[^\s<>]+"
    r"|https?://(?:www\.)?youtu\.be/[^\s<>]+)",
    re.IGNORECASE
)

TITLE_RE = re.compile(
    r"(?:W|Week)\s*(\d+).*?\b([A-Z0-9][A-Z0-9 ]+?)\b\s+vs\s+\b([A-Z0-9][A-Z0-9 ]+?)\b",
    re.IGNORECASE
)

TEAM_NAME_RE = r"[A-Z0-9][A-Za-z0-9 ]+"  # simple, forgiving

ADVANCE_LINE_RE = re.compile(
    r"^\s*([A-Za-z0-9 .’'-]+)\s*\([^)]*\)\s*vs\s*([A-Za-z0-9 .’'-]+)\s*\([^)]*\)\s*$",
    re.IGNORECASE
)

# put this near TITLE_RE (or replace TITLE_RE with this teams-only regex)
TEAMS_IN_TITLE_RE = re.compile(
    r"\b([A-Z0-9][A-Z0-9 ]+?)\b\s+vs\s+\b([A-Z0-9][A-Z0-9 ]+?)\b",
    re.IGNORECASE
)

MENTION_TOKENS_RE = re.compile(
    r"(?:<@!?\d+>|<@&\d+>|@everyone|@here)",  # user, role, mass mentions
    flags=re.IGNORECASE,
)

def parse_week_token(text: str) -> int | None:
    if not text:
        return None

    t = text.upper()

    # ---- PLAYOFFS ----
    if re.search(r"\bWILD\s*CARD\b|\bWC\b", t):
        return 19

    if re.search(r"\bDIVISIONAL\b|\bDIVISION\b|\bDIV\b", t):
        return 20

    if re.search(r"\bCONFERENCE\b|\bCONF\b|\bCF\b", t):
        return 21

    if re.search(r"\bSUPER\s*BOWL\b|\bSUPERBOWL\b|\bSB\b", t):
        return 23

    # ---- REGULAR SEASON ----
    m = re.search(r"\bW(?:EEK)?\s*(\d{1,2})\b", t)
    if m:
        return int(m.group(1))

    # ---- PRESEASON ----
    m = re.search(r"\bPRE(?:SEASON)?\s*(\d)\b", t)
    if m:
        n = int(m.group(1))
        if 1 <= n <= 3:
            return _pre_to_week(n)

    return None

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

def canonical_team(s: str) -> str:
    s = (s or "").upper().strip()
    # keep digits (for 49ERS) but drop punctuation
    s = re.sub(r"[^A-Z0-9 ]+", "", s)

    # common aliases
    aliases = {
        "DAL": "COWBOYS", "DALLAS": "COWBOYS",
        "MIN": "VIKINGS", "MINNESOTA": "VIKINGS",
        "NYG": "GIANTS", "SF": "49ERS", "SAN FRANCISCO": "49ERS",
        "NINERS": "49ERS"
    }
    s = aliases.get(s, s)
    return s

def extract_team_from_nick(nick: str) -> str | None:
    """
    Return a canonical TEAM (ALL CAPS) if the nickname starts with an official team
    name (Title Case in NFL_Teams.csv), ignoring leading symbols/digits/emoji.
    """
    if not nick:
        return None

    # normalize: drop leading non-alnum, then casefold for robust comparison
    lead = _leading_alnum_lower(nick)

    try:
        titles, upper_map = _load_nfl_title_and_upper()  # Title Case list + UPPER->Title map
    except Exception:
        return None

    # Find the first official team whose name is a prefix of the nickname
    for title in titles:                  # e.g., "Raiders"
        if lead.startswith(title.casefold()):
            return title.upper()          # -> "RAIDERS"

    return None

def parse_title_for_week_and_teams(title: str) -> tuple[int | None, str | None, str | None]:
    # week can be regular (e.g., WEEK 7) or preseason (e.g., PRE 2 / PRESEASON 2 -> negative week)
    wk = parse_week_token(title or "")

    m = TEAMS_IN_TITLE_RE.search(title or "")
    if not m:
        return wk, None, None

    t1 = canonical_team(m.group(1))
    t2 = canonical_team(m.group(2))
    return wk, t1, t2

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

def find_stream_link(text: str) -> str | None:
    if not text:
        return None

    # Remove Discord embed wrappers <...>
    cleaned = text.replace("<", "").replace(">", "")

    logger.info(f"[LINK DEBUG] Cleaned message: {repr(cleaned)}")

    m = LINK_RE.search(cleaned)
    if m:
        logger.info(f"[LINK DEBUG] Matched link: {m.group(1)}")
        return m.group(1)

    logger.warning("[LINK DEBUG] No link detected in message.")
    return None

def _canon_team_upper(s: str) -> str | None:
    t = extract_team_from_nick(s or "")
    return t if t else None

def _canon_team_for_lookup(s: str) -> str:
    return canonical_team(s.upper())

# --- Preseason support ---
# Internally: PRE 1 -> week = -3, PRE 2 -> -2, PRE 3 -> -1
def _pre_to_week(n: int) -> int:
    return -(4 - n)  # 1->-3, 2->-2, 3->-1


