"""User tracking and notification system for broadcasts and updates."""

import json
from pathlib import Path
from typing import Set, Dict, Any
from helpers.logger import LOGGER

_USER_DB_FILE = Path(__file__).resolve().parent.parent / "data" / ".userdb.json"
_STATS_FILE = Path(__file__).resolve().parent.parent / "data" / ".stats.json"

# In-memory cache
_user_cache: Set[int] = set()
_stats_cache: Dict[str, Any] = {
    "total_ulp_searches": 0,
    "total_extract_searches": 0,
    "total_combo_searches": 0,
    "total_users": 0,
}


def _ensure_data_dir():
    """Ensure data directory exists."""
    data_dir = _USER_DB_FILE.parent
    data_dir.mkdir(parents=True, exist_ok=True)


def load_users() -> Set[int]:
    """Load user IDs from database."""
    global _user_cache
    _ensure_data_dir()
    
    if not _USER_DB_FILE.exists():
        _user_cache = set()
        return _user_cache
    
    try:
        with open(_USER_DB_FILE, 'r', encoding='utf-8') as f:
            data = json.load(f)
            _user_cache = set(data.get('users', []))
    except Exception as e:
        LOGGER.error(f"Failed to load users: {e}")
        _user_cache = set()
    
    return _user_cache


def save_users():
    """Save user IDs to database."""
    _ensure_data_dir()
    try:
        with open(_USER_DB_FILE, 'w', encoding='utf-8') as f:
            json.dump({'users': list(_user_cache)}, f)
    except Exception as e:
        LOGGER.error(f"Failed to save users: {e}")


def add_user(user_id: int) -> bool:
    """Add user to tracking list. Returns True if new user."""
    if user_id in _user_cache:
        return False
    _user_cache.add(user_id)
    save_users()
    return True


def get_all_users() -> Set[int]:
    """Get all tracked user IDs."""
    if not _user_cache:
        load_users()
    return _user_cache.copy()


def load_stats() -> Dict[str, Any]:
    """Load statistics from database."""
    global _stats_cache
    _ensure_data_dir()
    
    if not _STATS_FILE.exists():
        _stats_cache = {
            "total_ulp_searches": 0,
            "total_extract_searches": 0,
            "total_combo_searches": 0,
            "total_users": 0,
        }
        return _stats_cache
    
    try:
        with open(_STATS_FILE, 'r', encoding='utf-8') as f:
            _stats_cache = json.load(f)
    except Exception as e:
        LOGGER.error(f"Failed to load stats: {e}")
        _stats_cache = {
            "total_ulp_searches": 0,
            "total_extract_searches": 0,
            "total_combo_searches": 0,
            "total_users": 0,
        }
    
    return _stats_cache


def save_stats():
    """Save statistics to database."""
    _ensure_data_dir()
    try:
        with open(_STATS_FILE, 'w', encoding='utf-8') as f:
            json.dump(_stats_cache, f)
    except Exception as e:
        LOGGER.error(f"Failed to save stats: {e}")


def increment_stat(stat_name: str, by: int = 1):
    """Increment a statistic."""
    global _stats_cache
    if not _stats_cache:
        load_stats()
    
    if stat_name not in _stats_cache:
        _stats_cache[stat_name] = 0
    
    _stats_cache[stat_name] += by
    save_stats()


def get_stats() -> Dict[str, Any]:
    """Get current statistics."""
    if not _stats_cache:
        load_stats()
    stats = _stats_cache.copy()
    stats['total_users'] = len(_user_cache) if _user_cache else len(load_users())
    return stats
