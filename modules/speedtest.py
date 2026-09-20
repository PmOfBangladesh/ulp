import asyncio
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from telethon import events

import config
from bot import ItsMrULPBot
from helpers import LOGGER, edit_message, new_task, send_message

prefixes = "".join(re.escape(p) for p in config.COMMAND_PREFIXES)

_OWNER_ONLY = {config.OWNER_ID}


def _owner_auth(uid: int) -> bool:
    return uid in _OWNER_ONLY
_sptest_pattern = re.compile(rf"^[{prefixes}]sptest$", re.IGNORECASE)


@dataclass
class OsStats:
    cpu_percent: float
    ram_used_mb: float
    ram_total_mb: float
    ram_percent: float
    disk_used_gb: float
    disk_total_gb: float
    disk_percent: float
    uptime_seconds: float
    temperature_celsius: float


def _collect_os_stats() -> OsStats:
    try:
        import psutil
        cpu = psutil.cpu_percent(interval=0.1)
        mem = psutil.virtual_memory()
        disk = psutil.disk_usage("/")
        uptime = time.time() - psutil.boot_time()
        return OsStats(
            cpu_percent=cpu,
            ram_used_mb=mem.used / (1024 * 1024),
            ram_total_mb=mem.total / (1024 * 1024),
            ram_percent=mem.percent,
            disk_used_gb=disk.used / (1024 ** 3),
            disk_total_gb=disk.total / (1024 ** 3),
            disk_percent=disk.percent,
            uptime_seconds=uptime,
            temperature_celsius=_get_temperature(),
        )
    except ImportError:
        return _collect_os_stats_fallback()


def _collect_os_stats_fallback() -> OsStats:
    cpu = 0.0
    ram_used = ram_total = ram_percent = 0.0
    disk_used = disk_total = disk_percent = 0.0
    uptime = 0.0
    try:
        with open("/proc/uptime") as f:
            uptime = float(f.read().split()[0])
    except Exception:
        pass
    try:
        with open("/proc/meminfo") as f:
            meminfo = {}
            for line in f:
                parts = line.split(":")
                if len(parts) == 2:
                    key = parts[0].strip()
                    val = int(parts[1].strip().split()[0])
                    meminfo[key] = val
            ram_total = meminfo.get("MemTotal", 0) / 1024
            ram_free = meminfo.get("MemFree", 0) + meminfo.get("Buffers", 0) + meminfo.get("Cached", 0)
            ram_free /= 1024
            ram_used = ram_total - ram_free
            ram_percent = (ram_used / ram_total * 100) if ram_total > 0 else 0
    except Exception:
        pass
    try:
        stat = os.statvfs("/")
        disk_total = (stat.f_frsize * stat.f_blocks) / (1024 ** 3)
        disk_free = (stat.f_frsize * stat.f_bavail) / (1024 ** 3)
        disk_used = disk_total - disk_free
        disk_percent = (disk_used / disk_total * 100) if disk_total > 0 else 0
    except Exception:
        pass
    try:
        with open("/proc/stat") as f:
            fields = f.readline().split()
            if len(fields) >= 5:
                user, nice, system, idle = (int(x) for x in fields[1:5])
                total = user + nice + system + idle
                cpu = ((total - idle) / total * 100) if total > 0 else 0
    except Exception:
        pass
    return OsStats(
        cpu_percent=cpu, ram_used_mb=ram_used, ram_total_mb=ram_total,
        ram_percent=ram_percent, disk_used_gb=disk_used, disk_total_gb=disk_total,
        disk_percent=disk_percent, uptime_seconds=uptime,
        temperature_celsius=_get_temperature(),
    )


def _get_temperature() -> float:
    try:
        import psutil
        temps = psutil.sensors_temperatures()
        if temps:
            for label in ("coretemp", "k10temp", "acpitz"):
                if label in temps and temps[label]:
                    return temps[label][0].current
            for entries in temps.values():
                if entries:
                    return entries[0].current
    except Exception:
        pass
    try:
        for zone in sorted(Path("/sys/class/thermal").glob("thermal_zone*")):
            ttype = (zone / "type").read_text().strip()
            if ttype in ("x86_pkg_temp", "cpu-thermal", "soc-thermal"):
                raw = (zone / "temp").read_text().strip()
                return int(raw) / 1000.0
        for zone in sorted(Path("/sys/class/thermal").glob("thermal_zone*")):
            raw = (zone / "temp").read_text().strip()
            return int(raw) / 1000.0
    except Exception:
        pass
    return -1.0


def _format_uptime(seconds: float) -> str:
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    d, h = divmod(h, 24)
    parts = []
    if d: parts.append(f"{d}d")
    if h: parts.append(f"{h}h")
    if m: parts.append(f"{m}m")
    if s or not parts: parts.append(f"{s}s")
    return " ".join(parts)


async def _measure_telegram_latency(bot) -> float:
    try:
        t0 = time.perf_counter()
        await bot.get_me()
        return (time.perf_counter() - t0) * 1000
    except Exception:
        return -1


async def _measure_db_latency() -> float:
    db_dir = Path(__file__).resolve().parent.parent / "data"
    paths = sorted(db_dir.glob("*.txt"))
    if not paths:
        return -1
    try:
        t0 = time.perf_counter()
        with open(paths[0], "rb") as f:
            f.read(4096)
        return (time.perf_counter() - t0) * 1000
    except Exception:
        return -1


@dataclass
class SpeedtestResult:
    upload_mbps: float
    download_mbps: float
    ping_ms: float
    timestamp: str
    bytes_sent_mb: float
    bytes_received_mb: float
    server_name: str
    server_country: str
    server_cc: str
    server_sponsor: str
    server_latency: float
    server_lat: float
    server_lon: float
    client_ip: str
    client_lat: float
    client_lon: float
    client_country: str
    client_isp: str
    client_isp_rating: str


def _run_speedtest() -> SpeedtestResult:
    import speedtest
    st = speedtest.Speedtest(secure=True)
    st.get_best_server()
    server = st.results.server
    client = st.results.client
    download = st.download()
    upload = st.upload()
    return SpeedtestResult(
        upload_mbps=upload / 1e6, download_mbps=download / 1e6,
        ping_ms=st.results.ping, timestamp=st.results.timestamp,
        bytes_sent_mb=st.results.bytes_sent / (1024 * 1024),
        bytes_received_mb=st.results.bytes_received / (1024 * 1024),
        server_name=server.get("name", "Unknown"),
        server_country=server.get("country", "Unknown"),
        server_cc=server.get("cc", ""),
        server_sponsor=server.get("sponsor", "Unknown"),
        server_latency=server.get("latency", 0),
        server_lat=server.get("lat", 0), server_lon=server.get("lon", 0),
        client_ip=client.get("ip", "Unknown"),
        client_lat=client.get("lat", 0), client_lon=client.get("lon", 0),
        client_country=client.get("country", "Unknown"),
        client_isp=client.get("isp", "Unknown"),
        client_isp_rating=client.get("isprating", "Unknown"),
    )


@ItsMrULPBot.on(events.NewMessage(pattern=_sptest_pattern))
@new_task
async def sptest_handler(event, bot):
    sender = await event.get_sender()
    if not _owner_auth(sender.id):
        LOGGER.warning(f"Unauthorized /sptest attempt by {sender.id}")
        return
    from helpers import add_user
    add_user(sender.id)
    chat_id = event.chat_id
    first_name = sender.first_name or ""
    last_name = sender.last_name or ""
    full_name = (first_name + " " + last_name).strip() or str(sender.id)

    status_msg = await send_message(chat_id, "**⚡ Running Speedtest... Please Wait ⏳**")
    if not status_msg:
        return

    telegram_latency_ms = await _measure_telegram_latency(bot)
    db_latency_ms = await _measure_db_latency()

    loop = asyncio.get_running_loop()
    try:
        speed: SpeedtestResult = await loop.run_in_executor(None, _run_speedtest)
    except Exception as exc:
        LOGGER.error(f"Speedtest failed: {exc}")
        fallback = (
            "⚠️ **Speedtest Failed**\n\n"
            f"**Error**: `{exc}`\n\n"
            f"📡 **Telegram Latency**: `{telegram_latency_ms:.0f} ms`\n"
            f"🗄️ **Database Latency**: `{db_latency_ms:.0f} ms`"
        )
        await edit_message(chat_id, status_msg.id, fallback)
        return

    os_stats = _collect_os_stats()

    temp_line = (
        f"┠ Temperature: `{os_stats.temperature_celsius:.1f}°C`\n"
        if os_stats.temperature_celsius >= 0
        else f"┠ Temperature: `N/A`\n"
    )

    text_out = (
        "➲ **SPEEDTEST INFO**\n"
        f"┠ Upload: `{speed.upload_mbps:.2f} MB/s`\n"
        f"┠ Download: `{speed.download_mbps:.2f} MB/s`\n"
        f"┠ Ping: `{speed.ping_ms:.3f} ms`\n"
        f"┠ Time: `{speed.timestamp}`\n"
        f"┠ Data Sent: `{speed.bytes_sent_mb:.2f} MB`\n"
        f"┖ Data Received: `{speed.bytes_received_mb:.2f} MB`\n\n"
        "➲ **SPEEDTEST SERVER**\n"
        f"┠ Name: `{speed.server_name}`\n"
        f"┠ Country: `{speed.server_country}`, `{speed.server_cc}`\n"
        f"┠ Sponsor: `{speed.server_sponsor}`\n"
        f"┠ Latency: `{speed.server_latency}`\n"
        f"┠ Latitude: `{speed.server_lat}`\n"
        f"┖ Longitude: `{speed.server_lon}`\n\n"
        "➲ **CLIENT DETAILS**\n"
        f"┠ IP Address: `{speed.client_ip}`\n"
        f"┠ Latitude: `{speed.client_lat}`\n"
        f"┠ Longitude: `{speed.client_lon}`\n"
        f"┠ Country: `{speed.client_country}`\n"
        f"┠ ISP: `{speed.client_isp}`\n"
        f"┖ ISP Rating: `{speed.client_isp_rating}`\n\n"
        "➲ **OS STATS**\n"
        f"┠ CPU Usage: `{os_stats.cpu_percent:.1f}%`\n"
        f"┠ RAM: `{os_stats.ram_used_mb:.0f} MB / {os_stats.ram_total_mb:.0f} MB ({os_stats.ram_percent:.1f}%)`\n"
        f"┠ Storage: `{os_stats.disk_used_gb:.1f} GB / {os_stats.disk_total_gb:.1f} GB ({os_stats.disk_percent:.1f}%)`\n"
        f"┠ Uptime: `{_format_uptime(os_stats.uptime_seconds)}`\n"
        + temp_line +
        f"┠ Telegram Latency: `{telegram_latency_ms:.0f} ms`\n"
        f"┖ Database Latency: `{db_latency_ms:.0f} ms`\n\n"
        f"👤 **Requested by**: `{full_name}`"
    )

    await edit_message(chat_id, status_msg.id, text_out)
