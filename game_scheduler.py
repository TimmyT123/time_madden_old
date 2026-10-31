"""WURD official matchup scheduling v0.1 (Discord only).

Persistent buttons, Arizona-time proposals, two-party confirmation, broadcast flags,
15-minute reminders, and atomic JSON storage. No league advances are triggered.
"""
import asyncio
import json
import logging
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import nextcord

AZ = ZoneInfo('America/Phoenix')
STATE_FILE = Path(os.getenv('WURD_GAME_SCHEDULE_FILE', 'data/game_schedules.json'))
LOG = logging.getLogger('discord_bot')
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


def _display(rec):
    users = rec['users']
    proposed = rec.get('kickoff_utc')
    confirmed = proposed and len(rec.get('confirmed', [])) == 2
    pending = rec.get('pending_kickoff_utc')
    if proposed:
        dt = datetime.fromisoformat(proposed)
        kickoff = f'<t:{int(dt.timestamp())}:F> (<t:{int(dt.timestamp())}:R>)'
        status = '✅ **OFFICIAL — both confirmed**' if confirmed else '🟠 **Proposed — awaiting both confirmations**'
    else:
        kickoff = 'Not proposed yet'
        status = '⚪ **Unscheduled**'
    if pending:
        pending_ts = int(datetime.fromisoformat(pending).timestamp())
        status += f'\n🟠 **Change proposed:** <t:{pending_ts}:F> — awaiting both approvals'
    approval_ids = rec.get('pending_confirmed', []) if pending else rec.get('confirmed', [])
    approvals = '\n'.join(
        f'<@{uid}>: {"✅ Confirmed" if uid in approval_ids else "⏳ Awaiting confirmation"}'
        for uid in users
    )
    broadcasts = '\n'.join(
        f'<@{uid}>: {"📺 Yes" if rec.get("broadcast", {}).get(str(uid)) is True else "No" if rec.get("broadcast", {}).get(str(uid)) is False else "Not selected"}'
        for uid in users
    )
    return (f'🏈 **WURD — Official Game Scheduling**\n'
            f'**{rec.get("matchup", "Matchup")}**\n'
            f'{status}\n**Kickoff:** {kickoff}\n'
            f'**Confirmations:**\n{approvals}\n'
            f'**Broadcasting this game:**\n{broadcasts}\n\n'
            'Use **Propose / Change Time** to enter an Arizona kickoff time. '
            'The other player must confirm. Broadcasting is optional. '
            'The bot will mention both players 15 minutes before a confirmed kickoff.')


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


class ProposeModal(nextcord.ui.Modal):
    def __init__(self):
        super().__init__(title='Propose WURD kickoff (Arizona time)', custom_id='wurd:schedule:modal:v1')
        self.day = nextcord.ui.TextInput(label='Date (YYYY-MM-DD)', placeholder='2026-11-01', min_length=10, max_length=10)
        self.clock = nextcord.ui.TextInput(label='Arizona time (HH:MM, 24-hour)', placeholder='19:30', min_length=5, max_length=5)
        self.add_item(self.day)
        self.add_item(self.clock)

    async def callback(self, interaction):
        channel = interaction.channel
        cid = str(channel.id)
        try:
            local_dt = datetime.strptime(f'{self.day.value.strip()} {self.clock.value.strip()}', '%Y-%m-%d %H:%M').replace(tzinfo=AZ)
        except ValueError:
            await interaction.response.send_message('Use a real date YYYY-MM-DD and time HH:MM (Arizona, 24-hour clock).', ephemeral=True)
            return
        if local_dt <= datetime.now(AZ) + timedelta(minutes=1):
            await interaction.response.send_message('Choose a future kickoff, at least two minutes from now.', ephemeral=True)
            return
        deadline = _advance_deadline()
        if deadline and local_dt >= deadline:
            await interaction.response.send_message(f'That time is after the league advance (<t:{int(deadline.timestamp())}:F>). Choose an earlier time.', ephemeral=True)
            return
        async with _LOCK:
            data = _load()
            rec = data.get(cid)
            if not rec or not _allowed(rec, interaction.user.id):
                await interaction.response.send_message('Only the two players in this matchup can propose a kickoff.', ephemeral=True)
                return
            if any(_on_ap(uid) for uid in rec['users']):
                await interaction.response.send_message('Scheduling is disabled while a matchup player is on AP.', ephemeral=True)
                return
            new_time = local_dt.astimezone(timezone.utc).isoformat()
            if rec.get('kickoff_utc') and len(rec.get('confirmed', [])) == 2:
                # Preserve the original official kickoff until both accept the change.
                rec['pending_kickoff_utc'] = new_time
                rec['pending_confirmed'] = [interaction.user.id]
            else:
                rec['kickoff_utc'] = new_time
                rec['confirmed'] = [interaction.user.id]
                rec['reminder_sent'] = False
            rec.setdefault('history', []).append({'at': _stamp(), 'action': 'propose', 'by': interaction.user.id, 'kickoff_utc': new_time})
            _save(data)
        await interaction.response.send_message('Kickoff proposed. Your opponent must confirm it.', ephemeral=True)
        await _refresh(channel, rec)
        other = next(uid for uid in rec['users'] if uid != interaction.user.id)
        await channel.send(f'<@{other}> — please review the proposed kickoff and click **Confirm Time** if you agree.', allowed_mentions=nextcord.AllowedMentions(users=True))


class SchedulingView(nextcord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @nextcord.ui.button(label='Propose / Change Time', style=nextcord.ButtonStyle.primary, custom_id='wurd:schedule:propose:v1', row=0)
    async def propose(self, button, interaction):
        rec = _load().get(str(interaction.channel_id))
        if not rec or not _allowed(rec, interaction.user.id):
            await interaction.response.send_message('This button is for the two matchup players only.', ephemeral=True)
            return
        await interaction.response.send_modal(ProposeModal())

    @nextcord.ui.button(label='Confirm Time', style=nextcord.ButtonStyle.success, custom_id='wurd:schedule:confirm:v1', row=0)
    async def confirm(self, button, interaction):
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
            else:
                rec.setdefault('confirmed', []).append(interaction.user.id)
            rec.setdefault('history', []).append({'at': _stamp(), 'action': 'confirm', 'by': interaction.user.id})
            _save(data)
        await interaction.response.send_message('Your confirmation was saved.', ephemeral=True)
        await _refresh(interaction.channel, rec)
        if len(rec['confirmed']) == 2 and not rec.get('pending_kickoff_utc'):
            ts = int(datetime.fromisoformat(rec['kickoff_utc']).timestamp())
            await interaction.channel.send(f'✅ **OFFICIAL KICKOFF CONFIRMED** — <t:{ts}:F>. Both players agreed. A 15-minute reminder will be sent.', allowed_mentions=nextcord.AllowedMentions.none())

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

    @nextcord.ui.button(label='Broadcast: Yes', style=nextcord.ButtonStyle.secondary, custom_id='wurd:schedule:yes:v1', row=1)
    async def yes(self, button, interaction):
        await self._broadcast(interaction, True)

    @nextcord.ui.button(label='Broadcast: No', style=nextcord.ButtonStyle.secondary, custom_id='wurd:schedule:no:v1', row=1)
    async def no(self, button, interaction):
        await self._broadcast(interaction, False)


def setup(bot, advance_reader=None, ap_check=None):
    global _BOT, _ADVANCE_READER, _AP_CHECK
    _BOT = bot
    _ADVANCE_READER = advance_reader
    _AP_CHECK = ap_check
    bot.add_view(SchedulingView())


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
                    if dt - timedelta(minutes=15) <= now < dt and not any(_on_ap(uid) for uid in rec['users']):
                        due.append((cid, rec))
                for cid, rec in due:
                    channel = bot.get_channel(int(cid))
                    if not channel:
                        continue  # deleted/advanced channels never trigger notifications
                    try:
                        # Set sent first, so restart/error cannot duplicate a mention.
                        data[cid]['reminder_sent'] = True
                        data[cid].setdefault('history', []).append({'at': _stamp(), 'action': 'reminder_15m'})
                        _save(data)
                        mentions = ' '.join(f'<@{uid}>' for uid in rec['users'])
                        ts = int(datetime.fromisoformat(kickoff).timestamp())
                        await channel.send(f'⏰ **GAME TIME REMINDER** {mentions}\nYour confirmed kickoff is <t:{ts}:t> (<t:{ts}:R>). Get ready!', allowed_mentions=nextcord.AllowedMentions(users=True))
                    except (nextcord.Forbidden, nextcord.HTTPException) as exc:
                        LOG.warning('[SCHEDULER] Reminder send failed in %s: %s', cid, exc)
        except Exception:
            LOG.exception('[SCHEDULER] Reminder loop failed')
        await asyncio.sleep(poll_seconds)
