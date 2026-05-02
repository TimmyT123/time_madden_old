
async def handle_game_stream_post(bot, msg):
    import sys
    state = sys.modules["__main__"]
    from datetime import datetime
    import logging

    logger = logging.getLogger(__name__)

    try:
        if not msg.guild:
            return

        if msg.author == bot.user:
            return

        full_content = msg.content or ""
        link = state.find_stream_link(full_content)

        # ❌ If no link → do nothing
        if not link:
            return

        # ✅ Must have advance loaded (use LIVE state)
        if not state._current_week or not state._current_matchups:
            logger.warning("No advance loaded yet.")
            return

        # ✅ Resolve matchup ONLY from learned state
        week = state.prefer_learned_week(None)
        t1, t2 = state.normalize_matchup_with_learned(None, None, author=msg.author)

        if not (t1 and t2):
            logger.warning(f"Could not resolve matchup for {msg.author.display_name}")
            return

        # Load team IDs
        if not state.TEAM_NAME_TO_ID:
            state.load_team_id_mapping()

        home_id = state.TEAM_NAME_TO_ID.get(t1)
        away_id = state.TEAM_NAME_TO_ID.get(t2)

        flyer_data = (
            state.fetch_flyer_data(home_id, away_id)
            if home_id and away_id
            else None
        )

        if flyer_data:
            if "home" in flyer_data and "team1" not in flyer_data:
                flyer_data["team1"] = flyer_data["home"]
                flyer_data["team2"] = flyer_data["away"]

        # 🚫 Duplicate check
        if flyer_data:
            season = state.get_current_season(flyer_data)
            if state.registry_has(season, week or 0, t1, t2):
                logger.info(f"Flyer already exists for {t1} vs {t2}")
                return

        # Decide AI or static
        use_ai = state.should_use_ai_flyer(week, t1, t2)

        flyer_prompt = state.build_flyer_image_prompt(flyer_data) if (flyer_data and use_ai) else None

        flyer_path, flyer_source = state.generate_flyer_with_fallback(
            week=week or 0,
            t1=t1,
            t2=t2,
            streamer=msg.author.display_name,
            link=link,
            flyer_prompt=flyer_prompt,
            flyer_data=flyer_data
        )

        await state.post_flyer_with_everyone(
            msg.channel,
            flyer_path,
            week or 0,
            t1,
            t2,
            msg.author.display_name,
            link
        )

        # Send discussion redirect
        lobby = state.get_lobby_talk_channel(msg.guild)
        if lobby:
            await msg.channel.send(
                f"💬 **Game discussion**\nPlease use {lobby.mention} for game discussion."
            )

        season = state.get_current_season(flyer_data) if flyer_data else 0

        # Save to registry
        state.registry_put(season, week or 0, t1, t2, {
            "message_id": None,
            "source": "game-streams-channel",
            "author_id": getattr(msg.author, "id", 0),
            "ts": datetime.utcnow().isoformat()
        })

    except Exception as e:
        logger.warning(f"flyer pipeline failed: {e}")
