#  Handles known questions WITHOUT AI
def is_advance_question(msg):
    return "advance" in msg.lower()

def get_advance_response(context):
    return f"Next advance is {context['advance']}"

def is_bot_mentioned(message, bot_user):
    return bot_user in message.mentions
