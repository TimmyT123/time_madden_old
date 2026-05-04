# utils/time_utils.py



def get_time_zones():
    pt_time = datetime.now(pytz.timezone('US/Pacific'))
    az_time = datetime.now(pytz.timezone('US/Arizona'))
    mtn_time = datetime.now(pytz.timezone('US/Mountain'))
    central_time = datetime.now(pytz.timezone('US/Central'))
    eastern_time = datetime.now(pytz.timezone('US/Eastern'))
    return (pt_time, az_time, mtn_time, central_time, eastern_time)

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
           f"example:\n{tz1_nick:<24} is {tz1_time}\n{tz2_nick:<24} is {tz2_time}\n"

def _parse_date(d: str) -> datetime:
    # Stored as YYYY-MM-DD (no time); treat as midnight UTC for comparisons
    return datetime.strptime(d, "%Y-%m-%d").replace(tzinfo=dt_timezone.utc)

def _date_from_str(dstr: str):
    """Return a date object from 'YYYY-MM-DD' (no timezone math)."""
    return datetime.strptime(dstr, "%Y-%m-%d").date()

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

def human_date(dstr: str) -> str:
    """Pretty print the same calendar date (no tz shifting)."""
    d = _date_from_str(dstr)
    dummy = datetime(d.year, d.month, d.day)  # just for strftime parts
    # Avoid %-d portability by injecting the day number
    return f"{dummy.strftime('%a')}, {dummy.strftime('%b')} {d.day}, {dummy.strftime('%Y')}"


