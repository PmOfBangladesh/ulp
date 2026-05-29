import re

from telethon import events

import config
from bot import ItsMrULPBot
from helpers import LOGGER, SmartButtons

prefixes = "".join(re.escape(p) for p in config.COMMAND_PREFIXES)


def build_back_markup():
    sb = SmartButtons()
    sb.button("◀️ Back", callback_data="back_to_start")
    return sb.build_menu(b_cols=1)


def build_start_markup():
    sb = SmartButtons()
    sb.button("⚙ Main Menu", callback_data="main_menu", position="header")
    sb.button("ℹ️ About Me", callback_data="about")
    sb.button("📄 Policy & Terms", callback_data="policy")
    return sb.build_menu(b_cols=2, h_cols=1)


@ItsMrULPBot.on(events.CallbackQuery(data=re.compile(rb"^(about|policy|main_menu|back_to_start)$")))
async def callback_handler(event):
    data = event.data

    if data == b"about":
        text = (
            "**ℹ️ About Aliya Ulp**\n"
            "**━━━━━━━━━━━━━━━━━**\n"
            "**Name:** Aliya Ulp ⚙️\n"
            "**Version:** v1.0 (Beta) 🛠\n\n"
            "**Development Team:**\n"
            "• Owner: [CodeNinjaXd](https://t.me/CodeNinjaXd)\n\n"
            "**Technical Stack:**\n"
            "• Language: Python 🐍\n"
            "• Libraries: Telethon, ripgrep 📚\n\n"
            "**About:** A powerful ULP utility bot for Telegram — search, process & more!"
        )
        await event.edit(text, link_preview=False, buttons=build_back_markup())

    elif data == b"policy":
        text = (
            "**📜 Privacy Policy for Aliya Ulp**\n\n"
            "Welcome to **Aliya Ulp** Bot. By using our services, you agree to this privacy policy.\n\n"
            "**1. Information We Collect:**\n"
            "   • **Personal Information:** User ID and username for personalization.\n"
            "   • **Usage Data:** Information on how you use the bot to improve our services.\n\n"
            "**2. Usage of Information:**\n"
            "   • **Service Enhancement:** To provide and improve **Aliya Ulp.**\n"
            "   • **Communication:** Updates and new features.\n"
            "   • **Security:** To prevent unauthorized access.\n\n"
            "**3. Data Security:**\n"
            "   • This bot does not permanently store any media or personal data.\n"
            "   • Temporary files are cleaned up after each task automatically.\n"
            "   • We use strong security measures, although no system is 100% secure.\n\n"
            "Thank you for using **Aliya Ulp**. We prioritize your privacy and security."
        )
        await event.edit(text, link_preview=False, buttons=build_back_markup())

    elif data == b"main_menu":
        text = (
            "**🤖 Aliya Ulp Commands**\n\n"
            "**Basic Commands:**\n"
            "`/start` - **Show welcome message**\n"
            "`/help` - **Show help message**\n"
            "`/cmds` - **Show all commands**\n\n"
            "**Aliya Ulp Commands:**\n"
            "`/ulp` - **Search ULP based on keyword**\n"
            "`/extract` - **Extract specific format from keyword or file**\n"
            "`/cmb` - **Generate combo for specific keyword**\n\n"
            "**Admin Commands:**\n"
            "`/add` - **Add textual databases to the server**\n"
            "`/files` - **View all database files with navigation**\n"
            "`/clean` - **View DB stats and clean up files**\n"
            "`/broadcast` - **Broadcast message to all users**\n"
            "`/stats` - **Show bot statistics**\n\n"
            "**Owner Commands:**\n"
            "`/restart` - **Restart the bot**\n"
            "`/stop` - **Immediately stop this bot**\n\n"
            "**📌 Note:** All commands work only in private chats when the bot is online."
        )
        await event.edit(text, link_preview=False, buttons=build_back_markup())

    elif data == b"back_to_start":
        sender = await event.get_sender()
        first_name = sender.first_name or ""
        last_name = sender.last_name or ""
        name = f"{first_name} {last_name}".strip() or "User"
        text = (
            f"**Hi** {name} **Welcome To This Bot!**\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n"
            f"**Aliya Ulp ⚙️** is your ultimate ULP toolkit on Telegram — process files & more with ease!\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n"
            f"Don't forget to [join](https://{config.UPDATE_CHANNEL_URL}) for updates!"
        )
        await event.edit(text, link_preview=False, buttons=build_start_markup())
