"""WURD streamed-matchup pregame previews, v1.0.

Only confirmed scheduled matchups where at least one participant opted to stream.
Does not tag participants, publish stream URLs, or change the game reminder.
"""
import asyncio
import json
import logging
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import nextcord
import requests

import game_scheduler

LOG = logging.getLogger('discord_bot')
API_BASE = os.getenv('API_BASE_URL', 'http://127.0.0.1:5000/api').rstrip('/')
STREAMS_CHANNEL_ID = int(os.getenv('GAME_STREAMS_CHANNEL_ID', '0') or 0)
_EXPORT_ROOT = Path(os.getenv('WURD_MADDEN_UPLOADS_DIR', '/home/pi/projects/madden_flask_app/uploads'))


def _get_flyer(rec):
    """Retrieve current, real franchise teams and top-OVR players; never fabricate statistics."""
    try:
        latest = json.loads((_EXPORT_ROOT / '_latest.json').read_text(encoding='utf-8'))
        league, season, period = (str(latest.get(k) or '') for k in ('league', 'season', 'week'))
        if not league or not re.fullmatch(r'season_\d+', season) or not re.fullmatch(r'(pre|week)_\d+', period):
            return None
        if rec.get('league_id') and str(rec['league_id']) != league:
            return None
        if rec.get('season') and str(rec['season']) not in (season, season.split('_')[-1]):
            return None
        if str(rec.get('week')) != period.split('_')[-1]:
            return None
        parts = re.split(r'[-_]', str(rec.get('matchup') or '').lower())
        if len(parts) != 2:
            return None
        wanted = [game_scheduler._simple_name(p) for p in parts]
        team_map = json.loads((_EXPORT_ROOT / league / 'team_map.json').read_text(encoding='utf-8'))
        resolved = []
        for name in wanted:
            hits = [str(tid) for tid, info in team_map.items() if name in game_scheduler._team_aliases(info)]
            if len(hits) != 1:
                return None
            resolved.append(hits[0])
        if resolved[0] == resolved[1]:
            return None
        response = requests.get(f'{API_BASE}/flyer/game', params={
            'league': league, 'season': season, 'week': period,
            'home': resolved[0], 'away': resolved[1],
        }, timeout=7)
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict) or not all(isinstance(payload.get(k), dict) for k in ('home', 'away')):
            return None
        return payload
    except (OSError, ValueError, TypeError, requests.RequestException) as exc:
        LOG.warning('[PREGAME] Franchise statistics unavailable for %s: %s', rec.get('matchup'), exc)
        return None


def _safe(value, max_len=80):
    """Avoid unexpected mentions or markdown injection in Discord sourced strings."""
    text = str(value or '').replace('@', '@\u200b').replace('`', "'").replace('\n', ' ')
    return text[:max_len]


def _team_name(team, fallback):
    return _safe(team.get('name') or fallback, 54)


def _record(team):
    return _safe(team.get('record') or 'Record unavailable', 24)


def _top_player(team):
    players = team.get('top_players') or []
    eligible = [p for p in players if isinstance(p, dict) and p.get('name')]
    if not eligible:
        return None
    player = eligible[0]
    name, position = _safe(player['name'], 44), _safe(player.get('pos'), 8)
    # This is a roster-rating spotlight, not a claim about season leaders.
    rating = player.get('ovr')
    try:
        rating = int(rating)
    except (ValueError, TypeError):
        rating = None
    return f'{name} ({position}, {rating} OVR)' if rating is not None else f'{name} ({position})'


def render_preview(rec, flyer=None, featured=False):
    week = rec.get('week')
    matchup = str(rec.get('matchup') or 'Upcoming matchup')
    names = re.split(r'[-_]', matchup)
    a, b = [p.title() for p in names] if len(names) == 2 else ('Home', 'Away')
    home, away = ((flyer or {}).get('home') or {}), ((flyer or {}).get('away') or {})
    a, b = _team_name(home, a), _team_name(away, b)
    record_a, record_b = _record(home), _record(away)
    kickoff = datetime.fromisoformat(rec['kickoff_utc'])
    ts = int(kickoff.timestamp())
    same_division = False
    try:
        from nfl_teams_divisions import nfl_teams
        same_division = nfl_teams.get(a.title()) is not None and nfl_teams.get(a.title()) == nfl_teams.get(b.title())
    except ImportError:
        pass
    title = ('🏆 FEATURED WURD MATCHUP' if featured else '🏈 WURD SPORTS NETWORK • PREGAME')
    lines = [title, f'**WEEK {_safe(week, 5)} • {a} vs. {b}**',
             f'📊 **{a}:** {record_a}  |  **{b}:** {record_b}']
    if same_division:
        lines.append('🔥 **Division rivals collide!** Familiar opponents, with bragging rights on the line.')
    else:
        lines.append('🔥 **The stage is set!** Two WURD opponents are getting ready to battle it out.')
    stars = [(a, _top_player(home)), (b, _top_player(away))]
    stars = [(team, player) for team, player in stars if player]
    if stars:
        lines.append('⭐ **Roster stars to watch**')
        lines.extend(f'• **{team}:** {player}' for team, player in stars)
    lines.append('🎙️ **Pregame take:** "The preparations are over. Who will deliver when the game is on the line?"')
    lines.append(f'📺 **Broadcast scheduled • Kickoff <t:{ts}:t> (<t:{ts}:R>)**')
    return '\n'.join(lines)


def _eligible(rec, now):
    kickoff = rec.get('kickoff_utc')
    if not kickoff or len(set(rec.get('confirmed', []))) != 2 or not rec.get('reminder_sent'):
        return False
    try:
        dt = datetime.fromisoformat(kickoff)
    except (TypeError, ValueError):
        return False
    if not (dt - timedelta(minutes=20) <= now < dt):
        return False
    return any(value is True for value in (rec.get('broadcast') or {}).values())


async def pregame_loop(bot, featured_pairs=None, poll_seconds=30):
    """Independent, restart-safe posting loop: only after the normal 20-minute reminder."""
    await bot.wait_until_ready()
    while not bot.is_closed():
        try:
            if not STREAMS_CHANNEL_ID:
                await asyncio.sleep(poll_seconds)
                continue
            destination = bot.get_channel(STREAMS_CHANNEL_ID)
            if destination is None:
                LOG.warning('[PREGAME] GAME_STREAMS_CHANNEL_ID unavailable')
                await asyncio.sleep(poll_seconds)
                continue
            async with game_scheduler._LOCK:
                data = game_scheduler._load()
                now = datetime.now(timezone.utc)
                for cid, rec in data.items():
                    kickoff = rec.get('kickoff_utc')
                    if not _eligible(rec, now) or rec.get('pregame_sent_for') == kickoff:
                        continue
                    # Channel must still exist, and both players must still be active.
                    if not bot.get_channel(int(cid)) or any(game_scheduler._on_ap(uid) for uid in rec['users']):
                        continue
                    if game_scheduler.exported_game_completed(rec):
                        continue
                    pair = tuple(sorted(re.split(r'[-_]', str(rec.get('matchup') or '').upper())))
                    featured = bool(featured_pairs and pair in (featured_pairs() or set()))
                    flyer = await asyncio.to_thread(_get_flyer, rec)
                    message = render_preview(rec, flyer, featured)
                    try:
                        posted = await destination.send(message, allowed_mentions=nextcord.AllowedMentions.none())
                    except (nextcord.Forbidden, nextcord.HTTPException) as exc:
                        LOG.warning('[PREGAME] Post failed, will retry for %s: %s', rec.get('matchup'), exc)
                        continue
                    # Record successful deliveries separately from game reminders.
                    data[cid]['pregame_sent_for'] = kickoff
                    data[cid]['pregame_message_id'] = posted.id
                    data[cid].setdefault('history', []).append({
                        'at': game_scheduler._stamp(), 'action': 'pregame_posted',
                        'kickoff_utc': kickoff, 'message_id': posted.id,
                    })
                    game_scheduler._save(data)
        except Exception:
            LOG.exception('[PREGAME] Preview loop failed')
        await asyncio.sleep(poll_seconds)
