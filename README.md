# ULP Telegram Bot (ItsMrULPBot)

A fast, async Telegram bot for searching, extracting, and processing ULP (URL:Login:Password) databases. Built with Telethon + ripgrep for high‑volume text search, with admin tools for file management and broadcasts.

## Features

- Keyword ULP search across large text databases
- Extract formats: mail:pass, user:pass, number:pass, domain, and URL
- Combo generation with selectable formats
- Admin/owner tools: upload DB files, browse files, clean DB/dumps
- Broadcast messages to tracked users
- Usage statistics tracking
- Async I/O with uvloop for performance
- Auto‑cleanup of generated downloads

## Requirements

- Python 3.11+
- `ripgrep` system binary (`rg`)
- Telegram API ID, API Hash, and Bot Token
- `telethon`, `uvloop`, `cryptg` (see `requirements.txt`)

### Install ripgrep

**Debian/Ubuntu**
```bash
sudo apt update
sudo apt install -y ripgrep
```

**Arch**
```bash
sudo pacman -S ripgrep
```

**macOS**
```bash
brew install ripgrep
```

## Quick Start (Local)

```bash
git clone https://github.com/PmOfBangladesh/ulp
cd ulp
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

## Configuration

Edit `config.py` and fill in your values:

```python
API_ID = YOUR_API_ID
API_HASH = 'YOUR_API_HASH'
BOT_TOKEN = 'YOUR_BOT_TOKEN'
UPDATE_CHANNEL_URL = 't.me/abirxdhackz'
COMMAND_PREFIXES = ['/', '!', '.', ',', '$', '#']

OWNER_ID = 123456789
ADMIN_ID = 123456789
```

Get your API credentials at https://my.telegram.org.

## Database Setup

Place `.txt` ULP database files in the `data/` folder:

```
ulp/
└── data/
    ├── database1.txt
    └── database2.txt
```

Supported record patterns include:

```
url:email:password
url:username:password
url:phonenumber:password
```

The bot also stores user tracking and stats in:
- `data/.userdb.json`
- `data/.stats.json`

Keep the `data/` directory persistent if you want broadcasts and stats to survive restarts.

## Run the Bot

```bash
python main.py
```

## Commands

**Public**

| Command | Description |
|---|---|
| `/start` | Welcome message |
| `/help` or `/cmds` | Help and command list |
| `/ulp <keyword>` | Search ULP database by keyword |
| `/extract <keyword>` | Extract data by keyword |
| `/extract` (reply to .txt) | Extract from replied file |
| `/cmb <keyword>` | Generate combo file |

**Admin / Owner**

| Command | Description |
|---|---|
| `/add <count>` | Upload and add DB files |
| `/files` | Browse DB files with pagination |
| `/clean` | DB/dump cleanup tools |
| `/broadcast` (reply) | Broadcast a message to users |
| `/stats` | Bot usage statistics |
| `/restart` | Restart the bot process |
| `/stop` | Stop the bot |

> Command prefixes are configurable in `config.py` (default: `/ ! . , $ #`).

## Project Structure

```
ulp/
├── main.py              # Entry point
├── bot.py               # Telethon client
├── config.py            # Bot configuration
├── core/                # Core handlers
├── modules/             # Command modules
├── helpers/             # Utilities (search, logging, buttons, stats)
├── utils/               # Search engine wrapper
├── data/                # ULP databases + user/stats JSON
├── downloads/           # Generated files (auto‑cleaned)
├── requirements.txt
└── pyproject.toml
```

## Full Deployment Guide (VPS / Server)

### 1) Prepare the server

Recommended: Ubuntu 22.04+ or Debian 12.

```bash
sudo apt update
sudo apt install -y git ripgrep python3.11 python3.11-venv python3.11-dev build-essential
```

### 2) Create a service user (optional but recommended)

```bash
sudo useradd -m -s /bin/bash ulp
sudo su - ulp
```

### 3) Clone and install

```bash
git clone https://github.com/PmOfBangladesh/ulp
cd ulp
python3.11 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### 4) Configure the bot

Edit `config.py` with your API ID, API Hash, Bot Token, OWNER_ID, and ADMIN_ID.

### 5) Add database files

Copy your `.txt` ULP files into `data/`.

### 6) Run as a systemd service

Create a service file (adjust paths/user):

```bash
sudo tee /etc/systemd/system/ulp-bot.service > /dev/null <<'SERVICE'
[Unit]
Description=ULP Telegram Bot
After=network.target

[Service]
Type=simple
User=ulp
WorkingDirectory=/home/ulp/ulp
ExecStart=/home/ulp/ulp/venv/bin/python /home/ulp/ulp/main.py
Restart=on-failure
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
SERVICE
```

Enable and start:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now ulp-bot
```

Logs:

```bash
sudo journalctl -u ulp-bot -f
```

### 7) Update the bot

```bash
cd /home/ulp/ulp
source venv/bin/activate
git pull
pip install -r requirements.txt
sudo systemctl restart ulp-bot
```

### 8) Quick run without systemd (screen/tmux)

```bash
source venv/bin/activate
python main.py
```

## Notes

- `downloads/` is auto‑cleaned after sending files.
- Only Owner/Admin can use `/add`, `/files`, `/clean`, `/broadcast`, `/stats`, `/restart`, `/stop`.
- Keep your API keys and bot token private.
- Handle ULP data responsibly and follow applicable laws.

## Credits

- Main developer: **@ISmartCoder**
- Updates channel: **@abirxdhackz**
- Modified by: PmOfBangladesh
