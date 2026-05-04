# utils/message_utils.py

# Function to split a long message into chunks of a specific size
def split_message(message, max_length=2000):
    # Initialize list to hold split messages
    split_messages = []

    # Split message by return characters
    paragraphs = message.split('\n')
    current_message = ""

    for paragraph in paragraphs:
        # Add the paragraph and a newline to the current message if it stays within max_length
        if len(current_message) + len(paragraph) + 1 <= max_length:
            current_message += paragraph + '\n'
        else:
            # Append the current message to split_messages and start a new one
            split_messages.append(current_message.strip())
            current_message = paragraph + '\n'

    # Append the remaining message part
    if current_message:
        split_messages.append(current_message.strip())

    return split_messages

def _sanitize_message(msg: str) -> str:
    """
    Remove Discord embeds by breaking URLs.
    Example: 'https://twitch.tv/foo' -> '<https://twitch.tv/foo>'
    (wrapped in angle brackets disables embedding)
    """
    if not msg:
        return msg
    # Wrap all http/https URLs in < >
    return re.sub(r'(https?://\S+)', r'<\1>', msg)

def sanitize_playtime_text(raw: str) -> str:
    """
    Remove Discord mention tokens and trim whitespace.
    Keeps everything else verbatim (times, days, emojis, punctuation).
    """
    if not raw:
        return ""
    txt = MENTION_TOKENS_RE.sub("", raw)
    # collapse accidental double spaces left by removals
    txt = re.sub(r"\s{2,}", " ", txt).strip()
    # guardrail: avoid empty string after stripping mentions
    return txt if txt else "(no details provided)"

def is_exact_word(msg_text, word):
    """
    Checks if msg_text exactly matches the specified word.

    Parameters:
    - msg_text (str): The input string to be checked.
    - word (str): The target word to match exactly.

    Returns:
    - bool: True if msg_text is exactly word (case-insensitive), False otherwise.
    """
    # Validate inputs
    if not isinstance(msg_text, str):
        raise TypeError("msg_text must be a string.")
    if not isinstance(word, str):
        raise TypeError("word must be a string.")
    if not word:
        raise ValueError("word must not be an empty string.")

    # Define the regex pattern for exact match
    # Using re.escape to handle any special regex characters in word
    pattern = r'^' + re.escape(word) + r'$'

    # Perform the match using re.fullmatch for exact matching
    match = re.fullmatch(pattern, msg_text, re.IGNORECASE)

    return bool(match)

