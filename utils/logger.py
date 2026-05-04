# logger.py

logger = logging.getLogger('discord_bot')
logger.setLevel(logging.INFO)

console_handler = logging.StreamHandler(sys.stdout)
console_handler.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))

file_handler = RotatingFileHandler('bot.log', maxBytes=5 * 1024 * 1024, backupCount=5, encoding='utf-8')

logger.addHandler(console_handler)
logger.addHandler(file_handler)
