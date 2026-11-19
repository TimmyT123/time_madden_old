# pipeline.py — WURD current-season flyer fix v1.1
async def handle_game_stream_post(bot, msg):
    import sys
    state = sys.modules["__main__"]
    from datetime import datetime
    import logging

    logger = logging.getLogger("discord_bot")

    logger.info("PIPELINE: function called")

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
        logger.info(f"DEBUG: matchup resolved → {t1} vs {t2}")

        # Always refresh from the live /api/teams endpoint. The bot can stay
        # online across Madden advances, so cached team IDs are not reliable.
        # load_team_id_mapping() clears the cache and returns False on failure.
        if not state.load_team_id_mapping():
            logger.error("[FLYER] Skipping %s vs %s: live team IDs unavailable", t1, t2)
            return

        first_id = state.TEAM_NAME_TO_ID.get(t1)
        second_id = state.TEAM_NAME_TO_ID.get(t2)
        if not first_id or not second_id or first_id == second_id:
            logger.error("[FLYER] Missing/duplicate live team IDs: %s=%s, %s=%s",
                         t1, first_id, t2, second_id)
            return

        # The parsed matchup order is not necessarily home/away. Flask checks
        # the current season's exported schedule and returns the actual order.
        flyer_data = state.fetch_flyer_data(first_id, second_id, week=week)
        if not flyer_data:
            logger.error("[FLYER] Skipping %s vs %s: current flyer data failed validation",
                         t1, t2)
            return

        flyer_data["team1"] = flyer_data["home"]
        flyer_data["team2"] = flyer_data["away"]
        logger.info("[FLYER] Schedule order: home=%s (%s), away=%s (%s)",
                    flyer_data["home"].get("name"), flyer_data["home"].get("teamId"),
                    flyer_data["away"].get("name"), flyer_data["away"].get("teamId"))

        # Duplicate checks use canonicalized team pairs, not home/away order.
        season = state.get_current_season(flyer_data)
        if state.registry_has(season, week or 0, t1, t2):
            await msg.channel.send("⚠️ Flyer already posted for this game.")
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
        logger.info(f"DEBUG: flyer generated → path={flyer_path}, source={flyer_source}")
        logger.info("DEBUG: about to post flyer")

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

        # Save to registry
        state.registry_put(season, week or 0, t1, t2, {
            "message_id": None,
            "source": "game-streams-channel",
            "author_id": getattr(msg.author, "id", 0),
            "ts": datetime.utcnow().isoformat()
        })

    except Exception:
        logger.exception("flyer pipeline failed")
