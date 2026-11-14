"""WURD official matchup scheduling v0.2 (Discord and website JSON).

Persistent buttons, Arizona-time proposals, two-party confirmation, broadcast flags,
20-minute reminders, matchup-specific timestamps, and atomic JSON storage. No league advances are triggered.
"""
import asyncio
import json
import logging
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import nextcord

AZ = ZoneInfo('America/Phoenix')
# Timezone codes use IANA zones, so daylight-saving changes are automatic.
PLAYER_ZONES = {
    'AZ': 'America/Phoenix', 'ARIZONA': 'America/Phoenix',
    'ET': 'America/New_York', 'EST': 'America/New_York', 'EDT': 'America/New_York',
    'CT': 'America/Chicago', 'CST': 'America/Chicago', 'CDT': 'America/Chicago',
    'MT': 'America/Denver', 'MST': 'America/Denver', 'MDT': 'America/Denver',
    'PT': 'America/Los_Angeles', 'PST': 'America/Los_Angeles', 'PDT': 'America/Los_Angeles',
}

def _parse_player_kickoff(date_text, time_text, zone_text):
    zone_key = zone_text.strip().upper()
    if zone_key not in PLAYER_ZONES:
        raise ValueError('Use AZ, ET, CT, MT, or PT for your timezone.')
    zone = ZoneInfo(PLAYER_ZONES[zone_key])
    # Year is deliberately inferred; choose the next occurrence within 12 months.
    today = datetime.now(zone)
    date_bits = date_text.strip()
    if not re.fullmatch(r'\d{1,2}/\d{1,2}', date_bits):
        raise ValueError('Enter a date like 11/1 (month/day).')
    month, day = map(int, date_bits.split('/'))
    parsed_time = datetime.strptime(time_text.strip().upper().replace(' ', ''), '%I:%M%p')
    for year in (today.year, today.year + 1):
        try:
            naive = datetime(year, month, day, parsed_time.hour, parsed_time.minute)
        except ValueError:
            raise ValueError('Invalid calendar date.')
        local = naive.replace(tzinfo=zone)
        # Reject nonexistent local wall times around the spring DST transition.
        roundtrip = local.astimezone(timezone.utc).astimezone(zone)
        if (roundtrip.hour, roundtrip.minute, roundtrip.date()) != (local.hour, local.minute, local.date()):
            raise ValueError('That local time does not exist due to a daylight-saving change.')
        if local > today + timedelta(minutes=1):
            return local
    raise ValueError('Choose a future date and time.')

STATE_FILE = Path(os.getenv('WURD_GAME_SCHEDULE_FILE', 'data/game_schedules.json'))
LOG = logging.getLogger('discord_bot')
EXPORT_ROOT = Path(os.getenv('WURD_MADDEN_UPLOADS_DIR', '/home/pi/projects/madden_flask_app/uploads'))

_LOCK = asyncio.Lock()
_BOT = None
_ADVANCE_READER = None
_AP_CHECK = None


def _load():
    try:
        with STATE_FILE.open(encoding='utf-8') as f:
            payload = json.load(f)
        return payload if isinstance(payload, dict) else {}
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as exc:
        LOG.warning('[SCHEDULER] Cannot read scheduling file: %s', exc)
        return {}


def _save(data):
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    temp = STATE_FILE.with_name(STATE_FILE.name + '.tmp')
    with temp.open('w', encoding='utf-8') as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.flush()
        os.fsync(f.fileno())
    os.replace(temp, STATE_FILE)


def _stamp():
    return datetime.now(timezone.utc).isoformat()


def _member_zone(uid):
    """Resolve the saved nickname timezone; no question in the proposal form."""
    if _BOT:
        for guild in _BOT.guilds:
            member = guild.get_member(uid)
            if member:
                name = member.display_name or ''
                match = re.search(r'\b(PT|AZ|MT|CT|ET)\b\s*$', name, re.I)
                if match:
                    code = match.group(1).upper()
                    return code, ZoneInfo(PLAYER_ZONES[code])
    return None, None


def _kickoff_lines(rec, value):
    utc_dt = datetime.fromisoformat(value)
    lines = []
    for uid in rec['users']:
        code, zone = _member_zone(uid)
        if zone is None:
            lines.append(f'<@{uid}>: timezone not set in Discord nickname')
            continue
        local = utc_dt.astimezone(zone)
        lines.append(f'<@{uid}> ({code}): **{local.strftime("%a, %b %d, %I:%M %p").replace(" 0", " ")}**')
    return '\n'.join(lines)


def _extension_note(rec, value):
    deadline = _advance_deadline()
    if not deadline or not value or datetime.fromisoformat(value) < deadline:
        return ''
    approved = rec.get('extension_approved_for') == value
    status = '✅ **Commissioner extension approved**' if approved else '⚠️ **KICKOFF AT/AFTER ADVANCE — commissioner approval required**'
    return (f'\n{status}\n'
            f'League advance: **{deadline.strftime("%a, %b %d, %I:%M %p").replace(" 0", " ")} AZ**. '
            'A kickoff proposal does not delay advance.')


def _display(rec):
    proposed = rec.get('kickoff_utc')
    pending = rec.get('pending_kickoff_utc')
    confirmed = bool(proposed and len(set(rec.get('confirmed', []))) == 2)
    if proposed:
        expired = datetime.fromisoformat(proposed) <= datetime.now(timezone.utc)
        if confirmed:
            status = '✅ **OFFICIAL — both confirmed**'
        elif expired:
            status = '⚠️ **Proposal expired — propose a new kickoff**'
        else:
            status = '🟠 **PROPOSED — awaiting opponent confirmation**'
        kickoff = _kickoff_lines(rec, proposed)
    else:
        status = '⚪ **Unscheduled**'
        kickoff = 'Not proposed yet'
    lines = ['🏈 **WURD — Official Game Scheduling**',
             f'**{rec.get("matchup", "Matchup")}**',status,
             '**Kickoff (each player’s local timezone):**',kickoff]
    if proposed:
        lines.append(_extension_note(rec, proposed))
    if pending:
        lines += ['🟠 **CHANGE PROPOSED — awaiting both approvals:**', _kickoff_lines(rec,pending),_extension_note(rec,pending)]
    approvals = rec.get('pending_confirmed', []) if pending else rec.get('confirmed', [])
    lines.append('**Confirmations:**')
    lines += [f'<@{uid}>: {"✅ Confirmed" if uid in approvals else "⏳ Awaiting confirmation"}' for uid in rec['users']]
    lines.append('**📺 Will YOU broadcast this game? Choose below or answer in the kickoff popup:**')
    for uid in rec['users']:
        b = rec.get('broadcast', {}).get(str(uid))
        lines.append(f'<@{uid}>: {"📺 Yes" if b is True else "No" if b is False else "Not sure yet"}')
    lines += ['', 'Use **Propose / Change Time**. Enter the time in YOUR saved local timezone; '
              'the bot converts it for both players. Both must confirm. Each player can select their own broadcast status below. '
              'A 20-minute reminder is sent before confirmed kickoffs.']
    return '\n'.join(x for x in lines if x is not None)


async def _refresh(channel, rec):
    try:
        msg = await channel.fetch_message(int(rec['message_id']))
        await msg.edit(content=_display(rec), view=SchedulingView(), allowed_mentions=nextcord.AllowedMentions.none())
    except (nextcord.NotFound, nextcord.Forbidden, nextcord.HTTPException) as exc:
        LOG.warning('[SCHEDULER] Could not update panel in %s: %s', channel.id, exc)


def _advance_deadline():
    if _ADVANCE_READER is None:
        return None
    info = _ADVANCE_READER() or {}
    raw = info.get('advance_time_iso')
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(raw.replace('Z', '+00:00'))
        return dt.replace(tzinfo=AZ) if dt.tzinfo is None else dt.astimezone(AZ)
    except (ValueError, TypeError):
        return None


def _allowed(rec, user_id):
    return user_id in rec.get('users', [])


def _on_ap(uid):
    if not _AP_CHECK:
        return False
    try:
        return bool(_AP_CHECK(uid))
    except Exception:
        return False


def _parse_selected_time(day, clock, zone):
    try:
        parsed = datetime.strptime(clock.strip().upper().replace(' ', ''), '%I:%M%p')
    except ValueError:
        raise ValueError('Use a time such as 9:30 PM.')
    local = datetime(day.year,day.month,day.day,parsed.hour,parsed.minute,tzinfo=zone)
    roundtrip = local.astimezone(timezone.utc).astimezone(zone)
    if (roundtrip.date(),roundtrip.hour,roundtrip.minute)!=(local.date(),local.hour,local.minute):
        raise ValueError('This local time does not exist because of daylight saving time.')
    # Ambiguous fall-back hour is intentionally rejected rather than guessing.
    if local.replace(fold=0).utcoffset() != local.replace(fold=1).utcoffset():
        raise ValueError('That hour occurs twice due to daylight saving time. Pick an unambiguous time.')
    return local


def _stream_label(value):
    """Text for the modal; None means the player is undecided."""
    return 'Yes' if value is True else 'No' if value is False else 'Not sure yet'


def _parse_stream_choice(value):
    """Parse the three choices without accidentally recording a guess as No."""
    selected = re.sub(r'\s+', ' ', value.strip().lower())
    if selected in ('yes', 'y'):
        return True
    if selected in ('no', 'n'):
        return False
    if selected in ('not sure yet', 'not sure', 'maybe', 'unsure'):
        return None
    raise ValueError('For streaming, enter Yes, No, or Not sure yet.')


class ProposeModal(nextcord.ui.Modal):
    def __init__(self, selected_day, zone_code, zone, prior_time=None, prior_stream=None):
        super().__init__(title=f'Kickoff on {selected_day.strftime("%a, %b %d")}', custom_id='wurd:schedule:modal:v3')
        self.selected_day, self.zone = selected_day, zone
        self.clock = nextcord.ui.TextInput(
            label=f'Your local time ({zone_code}) — HH:MM AM/PM',
            placeholder='9:30 PM', min_length=6, max_length=8,
            default_value=prior_time or None,
        )
        self.stream = nextcord.ui.TextInput(
            label='Will you stream? Yes / No / Not sure yet',
            placeholder='Yes / No / Not sure yet',
            min_length=2, max_length=20,
            default_value=_stream_label(prior_stream),
        )
        self.add_item(self.clock)
        self.add_item(self.stream)

    async def callback(self, interaction):
        channel = interaction.channel
        cid = str(channel.id)
        try:
            local_dt = _parse_selected_time(self.selected_day, self.clock.value, self.zone)
            broadcast = _parse_stream_choice(self.stream.value)
        except ValueError as exc:
            await interaction.response.send_message(f'Invalid proposal: {exc}', ephemeral=True)
            return
        if local_dt <= datetime.now(timezone.utc) + timedelta(minutes=1):
            await interaction.response.send_message('Choose a future kickoff at least two minutes from now.', ephemeral=True)
            return
        async with _LOCK:
            data = _load()
            rec = data.get(cid)
            if not rec or not _allowed(rec, interaction.user.id):
                await interaction.response.send_message('Only matchup players can propose a kickoff.', ephemeral=True)
                return
            if any(_on_ap(uid) for uid in rec['users']):
                await interaction.response.send_message('Scheduling is disabled while a player is on AP.', ephemeral=True)
                return
            new_time = local_dt.astimezone(timezone.utc).isoformat()
            if rec.get('kickoff_utc') and len(set(rec.get('confirmed', []))) == 2:
                rec['pending_kickoff_utc'] = new_time
                rec['pending_confirmed'] = [interaction.user.id]
            else:
                rec['kickoff_utc'] = new_time
                rec['confirmed'] = [interaction.user.id]
                rec.pop('pending_kickoff_utc', None)
                rec.pop('pending_confirmed', None)
                rec['reminder_sent'] = False
                # A new unconfirmed proposal replaces the old proposal and its approval.
                rec['extension_approved_for'] = None
                rec['extension_approved_by'] = None
            # An undecided answer is stored as None; Yes/No can still be changed
            # later using the existing broadcast buttons on the matchup panel.
            rec.setdefault('broadcast', {})[str(interaction.user.id)] = broadcast
            rec.setdefault('history', []).append({
                'at': _stamp(), 'action': 'propose', 'by': interaction.user.id,
                'kickoff_utc': new_time, 'broadcast': broadcast,
            })
            _save(data)
        await interaction.response.send_message('Kickoff proposed. Your opponent must confirm it.', ephemeral=True)
        await _refresh(channel, rec)
        other = next(uid for uid in rec['users'] if uid != interaction.user.id)
        deadline = _advance_deadline()
        late = bool(deadline and local_dt >= deadline)
        mention = f'<@{other}>'
        message = (f'{mention} — **New kickoff proposal**\n'
                   f'{_kickoff_lines(rec, new_time)}\n'
                   'Please choose **Confirm Time** if you agree, or **Change Time** to suggest another kickoff.')
        if late:
            role_name = os.getenv('GAME_CHANNEL_COMMISSIONER_ROLE_NAME', 'Commissioner')
            role = nextcord.utils.get(channel.guild.roles, name=role_name) if channel.guild else None
            if role:
                message += f'\n⚠️ {role.mention} — kickoff is at/after advance. Commissioner extension approval is required.'
            else:
                message += '\n⚠️ Kickoff is at/after advance. Please contact a commissioner for approval.'
        notice = await channel.send(message, view=ProposalNotificationView(),
                                    allowed_mentions=nextcord.AllowedMentions(users=True, roles=late))
        # Record which notice is current; old notices cannot approve a newer proposal.
        async with _LOCK:
            data = _load()
            current = data.get(cid)
            if current and (current.get('pending_kickoff_utc') or current.get('kickoff_utc')) == new_time:
                current['proposal_notice_id'] = notice.id
                _save(data)


class DayChoiceView(nextcord.ui.View):
    """Temporary, private date buttons; persistent matchup panel remains unchanged."""
    def __init__(self,uid,zone_code,zone):
        super().__init__(timeout=180)
        self.uid,self.zone_code,self.zone=uid,zone_code,zone
        today=datetime.now(zone).date()
        deadline=_advance_deadline()
        advance_date=deadline.astimezone(zone).date() if deadline else None
        options=[('Today',today)]
        tomorrow=today+timedelta(days=1)
        if not advance_date or tomorrow<=advance_date:
            options.append(('Tomorrow'+(' (Advance Day)' if tomorrow==advance_date else ''),tomorrow))
        if advance_date and advance_date>tomorrow:
            options.append(('Advance Day',advance_date))
        if advance_date==today:
            options[0]=('Today (Advance Day)',today)
        for i,(label,day) in enumerate(options):
            button=nextcord.ui.Button(label=label,style=nextcord.ButtonStyle.primary if i==0 else nextcord.ButtonStyle.secondary,row=0)
            async def select(interaction,selected=day):
                if interaction.user.id != self.uid:
                    await interaction.response.send_message('This date selection belongs to another player.',ephemeral=True)
                    return
                rec = _load().get(str(interaction.channel_id))
                if not rec or not _allowed(rec, interaction.user.id):
                    await interaction.response.send_message('Only matchup players can propose a kickoff.', ephemeral=True)
                    return
                if any(_on_ap(uid) for uid in rec['users']):
                    await interaction.response.send_message('Scheduling is disabled while a player is on AP.', ephemeral=True)
                    return
                # Prefill the currently proposed/official time in THIS player's zone.
                # The day still comes from the Today/Tomorrow/Advance Day button.
                existing = rec.get('pending_kickoff_utc') or rec.get('kickoff_utc')
                prior_time = None
                if existing:
                    prior = datetime.fromisoformat(existing).astimezone(self.zone)
                    prior_time = prior.strftime('%I:%M %p').lstrip('0')
                prior_stream = rec.get('broadcast', {}).get(str(interaction.user.id))
                await interaction.response.send_modal(
                    ProposeModal(selected, self.zone_code, self.zone, prior_time, prior_stream)
                )
            button.callback=select
            self.add_item(button)




async def _propose_action(interaction):
    rec = _load().get(str(interaction.channel_id))
    if not rec or not _allowed(rec, interaction.user.id):
        await interaction.response.send_message('This button is for the two matchup players only.', ephemeral=True)
        return
    code, zone = _member_zone(interaction.user.id)
    if not zone:
        await interaction.response.send_message('Your Discord nickname needs a timezone suffix (PT, AZ, MT, CT, or ET). Please ask a commissioner to set it.',ephemeral=True)
        return
    await interaction.response.send_message(f'Choose a kickoff day. All times you enter are **{code}** (your local timezone). Opponent times convert automatically.',view=DayChoiceView(interaction.user.id,code,zone),ephemeral=True)



async def _confirm_action(interaction):
    cid = str(interaction.channel_id)
    async with _LOCK:
        data = _load()
        rec = data.get(cid)
        if not rec or not _allowed(rec, interaction.user.id):
            await interaction.response.send_message('Only matchup participants can confirm.', ephemeral=True)
            return
        if any(_on_ap(uid) for uid in rec['users']):
            await interaction.response.send_message('This matchup currently includes an AP player.', ephemeral=True)
            return
        if not (rec.get('pending_kickoff_utc') or rec.get('kickoff_utc')):
            await interaction.response.send_message('Propose a kickoff first.', ephemeral=True)
            return
        target = rec.get('pending_kickoff_utc') or rec['kickoff_utc']
        if datetime.fromisoformat(target) <= datetime.now(timezone.utc):
            await interaction.response.send_message('That kickoff has passed. Propose a new time.', ephemeral=True)
            return
        confirmations = rec.get('pending_confirmed', []) if rec.get('pending_kickoff_utc') else rec.get('confirmed', [])
        if interaction.user.id in confirmations:
            await interaction.response.send_message('You have already confirmed this time.', ephemeral=True)
            return
        if rec.get('pending_kickoff_utc'):
            rec.setdefault('pending_confirmed', []).append(interaction.user.id)
            if len(rec['pending_confirmed']) == 2:
                rec['kickoff_utc'] = rec.pop('pending_kickoff_utc')
                rec['confirmed'] = rec.pop('pending_confirmed')
                rec['reminder_sent'] = False
                # Extension approval only carries forward when the approved timestamp matches.
                if rec.get('extension_approved_for') != rec['kickoff_utc']:
                    rec['extension_approved_for'] = None
                    rec['extension_approved_by'] = None
        else:
            rec.setdefault('confirmed', []).append(interaction.user.id)
        rec.setdefault('history', []).append({'at': _stamp(), 'action': 'confirm', 'by': interaction.user.id})
        _save(data)
    await interaction.response.send_message('Your confirmation was saved.', ephemeral=True)
    await _refresh(interaction.channel, rec)
    if len(rec['confirmed']) == 2 and not rec.get('pending_kickoff_utc'):
        ts = int(datetime.fromisoformat(rec['kickoff_utc']).timestamp())
        await interaction.channel.send(f'✅ **OFFICIAL KICKOFF CONFIRMED** — <t:{ts}:F>. Both players agreed. A 20-minute reminder will be sent.', allowed_mentions=nextcord.AllowedMentions.none())
        notice_id = rec.get('proposal_notice_id')
        if notice_id:
            try:
                notice = await interaction.channel.fetch_message(int(notice_id))
                await notice.edit(view=None)
            except (nextcord.NotFound, nextcord.Forbidden, nextcord.HTTPException) as exc:
                LOG.warning('[SCHEDULER] Could not clear confirmed notice: %s', exc)



class ProposalNotificationView(nextcord.ui.View):
    """Persistent actions beside the ping, not just on the matchup panel."""
    def __init__(self):
        super().__init__(timeout=None)

    @nextcord.ui.button(label='Confirm Time', style=nextcord.ButtonStyle.success,
                        custom_id='wurd:schedule:notice-confirm:v1')
    async def confirm(self, button, interaction):
        # An older notification must not confirm a different/newer proposal.
        rec = _load().get(str(interaction.channel_id))
        if not rec or rec.get('proposal_notice_id') != interaction.message.id:
            await interaction.response.send_message(
                'This notice is outdated. Use the newest proposal or main scheduling panel.', ephemeral=True)
            return
        await _confirm_action(interaction)

    @nextcord.ui.button(label='Change Time', style=nextcord.ButtonStyle.primary,
                        custom_id='wurd:schedule:notice-change:v1')
    async def change(self, button, interaction):
        rec = _load().get(str(interaction.channel_id))
        if not rec or rec.get('proposal_notice_id') != interaction.message.id:
            await interaction.response.send_message(
                'This notice is outdated. Use the newest proposal or main scheduling panel.', ephemeral=True)
            return
        await _propose_action(interaction)


class SchedulingView(nextcord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @nextcord.ui.button(label='Propose / Change Time', style=nextcord.ButtonStyle.primary, custom_id='wurd:schedule:propose:v1', row=0)
    async def propose(self, button, interaction):
        await _propose_action(interaction)

    @nextcord.ui.button(label='Confirm Time', style=nextcord.ButtonStyle.success, custom_id='wurd:schedule:confirm:v1', row=0)
    async def confirm(self, button, interaction):
        await _confirm_action(interaction)

    @nextcord.ui.button(label='Approve Late Kickoff',style=nextcord.ButtonStyle.secondary,custom_id='wurd:schedule:extension:v2',row=2)
    async def approve_extension(self,button,interaction):
        role_name=os.getenv('GAME_CHANNEL_COMMISSIONER_ROLE_NAME','Commissioner')
        permitted = (interaction.guild is not None and
                     (interaction.user.guild_permissions.administrator or
                      any(role.name==role_name for role in interaction.user.roles)))
        if not permitted:
            await interaction.response.send_message('Only commissioners can approve a late kickoff.',ephemeral=True)
            return
        async with _LOCK:
            data=_load()
            rec=data.get(str(interaction.channel_id))
            if not rec:
                await interaction.response.send_message('No matchup schedule found.',ephemeral=True)
                return
            target=rec.get('pending_kickoff_utc') or rec.get('kickoff_utc')
            deadline=_advance_deadline()
            if not target or not deadline or datetime.fromisoformat(target)<deadline:
                await interaction.response.send_message('No at/after-advance kickoff needs approval.',ephemeral=True)
                return
            rec['extension_approved_for']=target
            rec['extension_approved_by']=interaction.user.id
            rec.setdefault('history',[]).append({'at':_stamp(),'action':'extension_approved','by':interaction.user.id,'kickoff_utc':target})
            _save(data)
        await interaction.response.send_message('Commissioner approval recorded for this kickoff. This does not change the league advance time.',ephemeral=True)
        await _refresh(interaction.channel,rec)
        await interaction.channel.send(f'✅ Commissioner <@{interaction.user.id}> approved an extension for this scheduled kickoff. **The league advance time itself has not changed.**',allowed_mentions=nextcord.AllowedMentions.none())

    async def _broadcast(self, interaction, value):
        cid = str(interaction.channel_id)
        async with _LOCK:
            data = _load()
            rec = data.get(cid)
            if not rec or not _allowed(rec, interaction.user.id):
                await interaction.response.send_message('Only matchup participants can choose broadcasting.', ephemeral=True)
                return
            rec.setdefault('broadcast', {})[str(interaction.user.id)] = value
            rec.setdefault('history', []).append({'at': _stamp(), 'action': 'broadcast', 'by': interaction.user.id, 'value': value})
            _save(data)
        await interaction.response.send_message('Broadcast preference saved.', ephemeral=True)
        await _refresh(interaction.channel, rec)

    @nextcord.ui.button(label='📺 I Will Broadcast', style=nextcord.ButtonStyle.success, custom_id='wurd:schedule:yes:v1', row=1)
    async def yes(self, button, interaction):
        await self._broadcast(interaction, True)

    @nextcord.ui.button(label='🚫 Not Broadcasting', style=nextcord.ButtonStyle.danger, custom_id='wurd:schedule:no:v1', row=1)
    async def no(self, button, interaction):
        await self._broadcast(interaction, False)

    @nextcord.ui.button(label='❔ Not Sure Yet', style=nextcord.ButtonStyle.secondary,
                        custom_id='wurd:schedule:unsure:v1', row=1)
    async def unsure(self, button, interaction):
        await self._broadcast(interaction, None)


def setup(bot, advance_reader=None, ap_check=None):
    global _BOT, _ADVANCE_READER, _AP_CHECK
    _BOT = bot
    _ADVANCE_READER = advance_reader
    _AP_CHECK = ap_check
    bot.add_view(SchedulingView())
    bot.add_view(ProposalNotificationView())


async def create_panel(channel, member_ids, matchup, league_id=None, season=None, week=None):
    """Called only for newly created user-vs-user channels."""
    users = list(dict.fromkeys(int(x) for x in member_ids))
    if len(users) != 2 or any(_on_ap(uid) for uid in users):
        return
    async with _LOCK:
        data = _load()
        cid = str(channel.id)
        if cid in data:
            return
        rec = {'channel_id': channel.id, 'users': users, 'matchup': matchup,
               'league_id': str(league_id or ''), 'season': str(season or ''),
               'week': week, 'created_at': _stamp(), 'kickoff_utc': None,
               'confirmed': [], 'broadcast': {}, 'reminder_sent': False, 'history': []}
        msg = await channel.send(_display(rec), view=SchedulingView(), allowed_mentions=nextcord.AllowedMentions.none())
        rec['message_id'] = msg.id
        data[cid] = rec
        _save(data)



def _simple_name(value):
    return re.sub(r'[^a-z0-9]+', '', str(value or '').lower())


def _team_aliases(info):
    if not isinstance(info, dict):
        return set()
    out = set()
    for key in ('nickName', 'nickname', 'name', 'displayName', 'teamName', 'abbr', 'abbrName', 'teamAbbr'):
        val = info.get(key)
        if val:
            out.add(_simple_name(val))
            # Team-map names can contain the city, such as "Buffalo Bills".
            out.add(_simple_name(str(val).split()[-1]))
    return out - {''}


def exported_game_completed(rec):
    """True only when the current export unambiguously identifies this completed matchup.

    Unknown/missing/stale/wrong-week data returns False (reminder stays enabled).
    The current `_latest.json` pointer selects the actual active league rather than
    relying on older fallback DEFAULT_LEAGUE_ID values in either process.
    """
    try:
        latest_path = EXPORT_ROOT / '_latest.json'
        latest = json.loads(latest_path.read_text(encoding='utf-8'))
        league = str(latest.get('league') or '')
        season = str(latest.get('season') or '')
        period = str(latest.get('week') or '').lower()
        if not league or not re.fullmatch(r'season_\d+', season):
            return False
        if not re.fullmatch(r'(?:pre|week)_\d+', period):
            return False
        # Never use an old week or season's result for a current reminder.
        rec_week = rec.get('week')
        if rec_week is None or str(rec_week) != period.rsplit('_', 1)[-1]:
            return False
        # Matchup channels use the two team nicknames, e.g. bills-jets.
        channel_parts = re.split(r'[-_]', str(rec.get('matchup') or '').lower())
        if len(channel_parts) != 2:
            return False
        wanted = [_simple_name(x) for x in channel_parts]
        if not all(wanted) or wanted[0] == wanted[1]:
            return False
        schedule = json.loads((EXPORT_ROOT / league / season / period / 'parsed_schedule.json').read_text(encoding='utf-8'))
        team_map = json.loads((EXPORT_ROOT / league / 'team_map.json').read_text(encoding='utf-8'))
        if not isinstance(schedule, list) or not isinstance(team_map, dict):
            return False
        # Resolve both names independently and uniquely to exported team IDs.
        matched_ids = []
        for name in wanted:
            ids = [str(tid) for tid, info in team_map.items() if name in _team_aliases(info)]
            if len(ids) != 1:
                return False
            matched_ids.append(ids[0])
        if matched_ids[0] == matched_ids[1]:
            return False
        desired = set(matched_ids)
        matches = [game for game in schedule if isinstance(game, dict) and
                   {str(game.get('homeTeamId')), str(game.get('awayTeamId'))} == desired]
        if len(matches) != 1:
            return False
        return bool(matches[0].get('completedAt'))
    except (OSError, ValueError, TypeError, KeyError) as exc:
        LOG.info('[SCHEDULER] Completion check unavailable; keeping reminder: %s', exc)
        return False


async def reminder_loop(bot, poll_seconds=30):
    await bot.wait_until_ready()
    while not bot.is_closed():
        try:
            async with _LOCK:
                data = _load()
                now = datetime.now(timezone.utc)
                due = []
                for cid, rec in data.items():
                    kickoff = rec.get('kickoff_utc')
                    if not kickoff or rec.get('reminder_sent') or len(rec.get('confirmed', [])) != 2:
                        continue
                    dt = datetime.fromisoformat(kickoff)
                    if dt - timedelta(minutes=20) <= now < dt and not any(_on_ap(uid) for uid in rec['users']):
                        due.append((cid, rec))
                for cid, rec in due:
                    channel = bot.get_channel(int(cid))
                    if channel and exported_game_completed(rec):
                        data[cid]['reminder_sent'] = True
                        data[cid]['completed_from_export'] = True
                        data[cid].setdefault('history', []).append({'at': _stamp(), 'action': 'skipped_reminder_game_completed'})
                        _save(data)
                        LOG.info('[SCHEDULER] Skipped reminder; exported game is complete for %s', rec.get('matchup'))
                        continue
                    if not channel:
                        continue  # deleted/advanced channels never trigger notifications
                    try:
                        # Set sent first, so restart/error cannot duplicate a mention.
                        data[cid]['reminder_sent'] = True
                        data[cid].setdefault('history', []).append({'at': _stamp(), 'action': 'reminder_20m'})
                        _save(data)
                        mentions = ' '.join(f'<@{uid}>' for uid in rec['users'])
                        # IMPORTANT: kickoff from the previous scanning loop belongs to
                        # its final record, not necessarily this due matchup.
                        # Always derive the timestamp from the CURRENT record.
                        reminder_kickoff = datetime.fromisoformat(rec['kickoff_utc'])
                        ts = int(reminder_kickoff.timestamp())
                        await channel.send(f'⏰ **GAME TIME REMINDER** {mentions}\nYour confirmed kickoff is <t:{ts}:t> (<t:{ts}:R>). Get ready!', allowed_mentions=nextcord.AllowedMentions(users=True))
                    except (nextcord.Forbidden, nextcord.HTTPException) as exc:
                        LOG.warning('[SCHEDULER] Reminder send failed in %s: %s', cid, exc)
        except Exception:
            LOG.exception('[SCHEDULER] Reminder loop failed')
        await asyncio.sleep(poll_seconds)
