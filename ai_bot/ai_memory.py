import time
import random

last_message_time = 0
last_bot_post_time = 0

# =============================
# TRACK CHAT ACTIVITY
# =============================
def update_last_message_time():
    global last_message_time
    last_message_time = time.time()


# =============================
# CONTROL PERSONALITY POSTING
# =============================
def can_send_personality_message():
    global last_message_time, last_bot_post_time

    now = time.time()

    # 1. Chat must be quiet for at least 10 minutes
    if (now - last_message_time) < 600:
        return False

    # 2. Bot cooldown (random 1–2 hours)
    cooldown = random.randint(4200, 7200)

    if (now - last_bot_post_time) < cooldown:
        return False

    # 3. Update last bot post time
    last_bot_post_time = now
    return True
