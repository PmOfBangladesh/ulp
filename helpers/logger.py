import logging
import os

LOG_FILE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "logs", "bot.log")
os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
    ],
)

for _name in ("telethon", "telethon.client", "telethon.network",
              "telethon.extensions", "telethon.sessions"):
    logging.getLogger(_name).setLevel(logging.ERROR)

LOGGER = logging.getLogger(__name__)
