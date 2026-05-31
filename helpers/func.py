import asyncio
import os
import re
import time
from pathlib import Path
from typing import Dict, FrozenSet, List, Optional, Tuple

from helpers.logger import LOGGER
from utils.engine import (
    THREAD_POOL,
    collect_datastore_paths,
    deduplicate_and_order,
    invoke_search_engine,
    is_record_blacklisted,
    release_event_loop,
    tokenize_output_lines,
)

_CHUNK_CUTOFF: int = 10000
_BIG_CHUNK: int = 2500
_SMALL_CHUNK: int = 1000
_MIN_TOKEN_LEN: int = 3

_ULP_LINE_RE: re.Pattern = re.compile(
    r'^(?:https?://)?(?:www\.)?([^/:]+).*?[:|]([^:]+)[:|](.+)$'
)

_CRED_PATTERN_RAW: Dict[str, str] = {
    "mailpass": r'(^|[\s])([a-zA-Z0-9][a-zA-Z0-9._%+\-]*@[a-zA-Z0-9][a-zA-Z0-9.\-]*\.[a-zA-Z]{2,})([:|])([^\s]+)',
    "userpass": r'([a-zA-Z0-9_-]{4,})([:|])([^\s]+)',
    "num_pass":  r'((?:\+?)\d[\d\s\-\(\)]*?\d)([:|])([^\s]+)',
}

_STRUCT_PATTERN_MAP: Dict[str, re.Pattern] = {
    "domain": re.compile(
        r'\b(?:[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?\.)+[a-zA-Z]{2,}\b'
    ),
    "url": re.compile(
        r'https?://(?:[-\w.])+(?:[:\d]+)?(?:/(?:[\w/_.])*(?:\?(?:[\w&=%.]*)?)?(?:#(?:[\w.])*)?)?'
    ),
}

_CRED_PATTERN_COMPILED: Dict[str, re.Pattern] = {
    k: re.compile(v, re.MULTILINE if k == "mailpass" else 0) for k, v in _CRED_PATTERN_RAW.items()
}

ACCEPTED_FORMAT_KEYS: List[str] = list(_CRED_PATTERN_RAW.keys()) + list(_STRUCT_PATTERN_MAP.keys())

_PHONE_STRIP_RE: re.Pattern = re.compile(r'[\s\-\(\)]')

# Enhanced email validation regex
_EMAIL_VALIDATION_RE: re.Pattern = re.compile(
    r'^[a-zA-Z0-9][a-zA-Z0-9._%+\-]*@[a-zA-Z0-9][a-zA-Z0-9.\-]*\.[a-zA-Z]{2,}$'
)

# Enhanced username validation regex - allow alphanumeric, underscore, hyphen, at least 4 chars
_USERNAME_VALIDATION_RE: re.Pattern = re.compile(
    r'^[a-zA-Z0-9_-]{4,}$'
)

# Phone number validation - basic format check with at least 7 digits
_PHONE_VALIDATION_RE: re.Pattern = re.compile(
    r'^\+?[\d\s\-\(\)]+$'
)

_COMBO_FINDER_RE: re.Pattern = re.compile(r'\b\S+\s*[:;|,]\s*\S+', re.IGNORECASE)
_LOGIN_CHECK_RE: re.Pattern = re.compile(
    r'^(?:[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}|\S+)$'
)

_URL_HINTS: Tuple[str, ...] = ('://', 'http', 'android://', 'ftp', '.com', '.org', '.net', 'www.')
_BAD_USER_MARKERS: Tuple[str, ...] = (
    'http', 'www.', 'file:///', 'android://', 'ftp://', 'vgecah', 'warning', 'stopped', 'found'
)
_BAD_PATH_MARKERS: Tuple[str, ...] = (
    'auth', 'login', 'register', 'checkout', 'app', 'classroom', 'store', 'affiliation'
)
_BLOCKED_TLDS: FrozenSet[str] = frozenset({'com', 'org', 'net', 'edu', 'gov', 'io', 'co'})
_COMBO_DELIMITERS: Tuple[str, ...] = (':', '|', ';', ',')
_COMBO_SPLIT_RE: re.Pattern = re.compile(r'[:|]')
_MIN_COMBO_FIELD: int = 3

# Email:Password specific patterns
_PASS_PATTERN: re.Pattern = re.compile(r'password:\s*(\S+)', re.I)
_EMAIL_PASS_SINGLE_LINE: re.Pattern = re.compile(
    r'([a-zA-Z0-9.%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,})\s*:\s*([^\s|;]+)'
)
_EMAIL_LINE_PATTERN: re.Pattern = re.compile(
    r'email:\s*([a-zA-Z0-9.%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,})',
    re.I
)


def _clean_email_pass_token(text: str) -> str:
    """Clean and validate email:password token by removing quotes, trailing punctuation."""
    if not text:
        return ""
    text = text.strip()
    # Remove surrounding quotes
    text = re.sub(r"^['\"]+|['\"]+$", "", text)
    # Remove trailing punctuation
    text = re.sub(r"[|;,\.)\]]+$", "", text)
    return text.strip()


def _extract_email_pass_combo(line: str, next_line: str = "") -> Optional[Tuple[str, str]]:
    """
    Extract email:password combo from various formats.
    Returns (email, password) tuple or None if not found/valid.
    
    Supports formats:
    - email@domain.com:password
    - email: email@domain.com
      password: mypass
    """
    line_lower = line.lower()
    
    # Format 1: Two-line format (email: ... / password: ...)
    if "email:" in line_lower and next_line:
        em_match = _EMAIL_LINE_PATTERN.search(line)
        if em_match and "password:" in next_line.lower():
            pm_match = _PASS_PATTERN.search(next_line)
            if pm_match:
                email = _clean_email_pass_token(em_match.group(1))
                password = _clean_email_pass_token(pm_match.group(1))
                
                # Validate
                if email and password and len(email) >= 5 and len(password) >= 3:
                    if _EMAIL_VALIDATION_RE.match(email.lower()):
                        return (email, password)
    
    # Format 2: Single-line format (email@domain.com:password)
    single_match = _EMAIL_PASS_SINGLE_LINE.search(line)
    if single_match:
        email = _clean_email_pass_token(single_match.group(1))
        password = _clean_email_pass_token(single_match.group(2))
        
        # Validate
        if email and password and len(email) >= 5 and len(password) >= 3:
            if _EMAIL_VALIDATION_RE.match(email.lower()):
                return (email, password)
    
    return None


def clean_email_pass_combos(lines: List[str], keywords: Optional[List[str]] = None) -> List[str]:
    """
    Clean and extract email:password combos from a list of lines.
    Optional keyword filter to only extract combos matching keywords.
    
    Returns list of unique email:password combos.
    """
    if not lines:
        return []
    
    extracted = set()
    valid_combos = []
    
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        if not line:
            i += 1
            continue
        
        line_lower = line.lower()
        next_line_lower = lines[i + 1].lower() if i + 1 < len(lines) else ""
        raw_chunk = line_lower + " " + next_line_lower
        
        # Apply keyword filter if provided
        if keywords:
            matched_kws = [kw.lower() for kw in keywords if kw.lower() in raw_chunk]
            if not matched_kws:
                i += 1
                continue
        
        # Try to extract email:password combo
        next_line = lines[i + 1] if i + 1 < len(lines) else ""
        combo = _extract_email_pass_combo(line, next_line)
        
        if combo:
            email, password = combo
            combo_str = f"{email}:{password}"
            key = combo_str.lower().strip()
            
            if key not in extracted:
                extracted.add(key)
                valid_combos.append(combo_str)
            
            # Skip next line if it was part of two-line format
            if "email:" in line_lower and "password:" in next_line_lower:
                i += 1
        
        i += 1
    
    return sorted(valid_combos)


def _filter_batch(batch: List[str]) -> List[str]:
    out: List[str] = []
    for ln in batch:
        s = ln.strip()
        if s and not is_record_blacklisted(s):
            out.append(s)
    return out


def _reformat_ulp_line(raw: str) -> str:
    m = _ULP_LINE_RE.match(raw.strip())
    if m:
        return f"{m.group(1).strip()}:{m.group(2).strip()}:{m.group(3).strip()}"
    return raw.strip()


def _reformat_ulp_batch(batch: List[str]) -> List[str]:
    return [_reformat_ulp_line(ln) for ln in batch if ln.strip()]


async def _async_filter(lines: List[str]) -> Tuple[List[str], int]:
    total = len(lines)
    chunk = _BIG_CHUNK if total > _CHUNK_CUTOFF else _SMALL_CHUNK
    gathered: List[str] = []
    loop = asyncio.get_running_loop()
    for i in range(0, total, chunk):
        gathered.extend(await loop.run_in_executor(THREAD_POOL, _filter_batch, lines[i:i + chunk]))
        await release_event_loop(i)
    unique, removed = await deduplicate_and_order(gathered)
    return unique, removed


async def _async_reformat_ulp(lines: List[str]) -> List[str]:
    total = len(lines)
    chunk = _BIG_CHUNK if total > _CHUNK_CUTOFF else _SMALL_CHUNK
    out: List[str] = []
    loop = asyncio.get_running_loop()
    for i in range(0, total, chunk):
        out.extend(await loop.run_in_executor(THREAD_POOL, _reformat_ulp_batch, lines[i:i + chunk]))
        await release_event_loop(i)
    return out


async def run_ulp_search(keyword: str, caller_file: str) -> Tuple[List[str], int, float]:
    t0 = time.perf_counter()
    paths = collect_datastore_paths(caller_file)
    if not paths:
        return [], 0, _ms(t0)
    rg = ["rg", "-i", "--no-heading", "--no-line-number", "--no-filename", "--fixed-strings", keyword] + paths
    code, out, _ = await invoke_search_engine(rg)
    if code not in (0, 1):
        return [], 0, _ms(t0)
    raw = tokenize_output_lines(out)
    if not raw:
        return [], 0, _ms(t0)
    unique, removed = await _async_filter(raw)
    reformatted = await _async_reformat_ulp(unique)
    return reformatted, removed, _ms(t0)


def _extract_cred_batch(batch: List[str], fmt: str) -> Tuple[List[str], int]:
    pattern = _CRED_PATTERN_COMPILED[fmt]
    results: List[str] = []
    tally: int = 0
    seen: set = set()
    for ln in batch:
        s = ln.strip()
        if not s or is_record_blacklisted(s):
            continue
        hits = pattern.findall(s)
        if not hits:
            continue
        tally += len(hits)
        
        # Handle mailpass format with new group structure (anchor, email, sep, pwd)
        if fmt == "mailpass":
            _, ident, sep, pwd = hits[-1]
        else:
            ident, sep, pwd = hits[-1]
        
        if len(ident) < _MIN_TOKEN_LEN or len(pwd) < _MIN_TOKEN_LEN:
            continue
        
        # Add validation for each format (2-step extraction process)
        if fmt == "mailpass":
            # Step 1: Extract email, Step 2: Validate email format
            if not _EMAIL_VALIDATION_RE.match(ident.lower()):
                continue
            # Additional check: reject if email or password contains URL/path patterns
            ident_lower = ident.lower()
            pwd_lower = pwd.lower()
            if any(marker in ident_lower for marker in ('://', '//', 'http://', 'https://', 'ftp://', 'android://')):
                continue
            if pwd_lower.startswith('http://') or pwd_lower.startswith('https://') or pwd_lower.startswith('//'):
                continue
        elif fmt == "userpass":
            # Step 1: Extract username, Step 2: Validate username format
            if not _USERNAME_VALIDATION_RE.match(ident):
                continue
        elif fmt == "num_pass":
            # Step 1: Extract number, Step 2: Validate phone number format
            cleaned = _PHONE_STRIP_RE.sub('', ident).replace('+', '')
            if not _PHONE_VALIDATION_RE.match(ident) or len(cleaned) < 7:
                continue
        
        key = _PHONE_STRIP_RE.sub('', ident) if fmt == "num_pass" else ident
        fp = key.lower()
        if fp not in seen:
            seen.add(fp)
            results.append(f"{ident}{sep}{pwd}")
    return results, tally


def _extract_struct_batch(batch: List[str], fmt: str) -> List[str]:
    pattern = _STRUCT_PATTERN_MAP[fmt]
    results: List[str] = []
    for ln in batch:
        s = ln.strip()
        if not s or is_record_blacklisted(s):
            continue
        for m in pattern.findall(s):
            results.append("".join(m) if isinstance(m, tuple) else str(m))
    return results


async def _run_extraction_pipeline(lines: List[str], fmt: str) -> Tuple[List[str], int]:
    total = len(lines)
    chunk = _BIG_CHUNK if total > _CHUNK_CUTOFF else _SMALL_CHUNK
    gathered: List[str] = []
    loop = asyncio.get_running_loop()
    if fmt in _CRED_PATTERN_COMPILED:
        for i in range(0, total, chunk):
            batch_res, _ = await loop.run_in_executor(THREAD_POOL, _extract_cred_batch, lines[i:i + chunk], fmt)
            gathered.extend(batch_res)
            await release_event_loop(i)
    else:
        for i in range(0, total, chunk):
            gathered.extend(await loop.run_in_executor(THREAD_POOL, _extract_struct_batch, lines[i:i + chunk], fmt))
            await release_event_loop(i)
    unique, removed = await deduplicate_and_order(gathered)
    return unique, removed


async def run_extract_on_datastore(keyword: str, fmt: str, caller_file: str) -> Tuple[List[str], int, float]:
    t0 = time.perf_counter()
    paths = collect_datastore_paths(caller_file)
    if not paths:
        return [], 0, _ms(t0)
    rg = ["rg", "-i", "--no-heading", "--no-line-number", "--no-filename"]
    if fmt in _CRED_PATTERN_RAW:
        rg += ["-e", _CRED_PATTERN_RAW[fmt]]
    else:
        rg += ["--fixed-strings", keyword]
    rg += paths
    code, out, _ = await invoke_search_engine(rg)
    if code not in (0, 1):
        return [], 0, _ms(t0)
    raw = tokenize_output_lines(out)
    if not raw:
        return [], 0, _ms(t0)
    unique, removed = await _run_extraction_pipeline(raw, fmt)
    return unique, removed, _ms(t0)


async def run_extract_on_lines(source_lines: List[str], fmt: str) -> Tuple[List[str], int, float]:
    t0 = time.perf_counter()
    if not source_lines:
        return [], 0, _ms(t0)
    unique, removed = await _run_extraction_pipeline(source_lines, fmt)
    return unique, removed, _ms(t0)


async def read_lines_from_file(file_path: str) -> List[str]:
    def _read() -> List[str]:
        with open(file_path, "r", encoding="utf-8", errors="replace") as fh:
            return [ln.rstrip("\n") for ln in fh if ln.strip()]
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(THREAD_POOL, _read)


def _scan_combo_batch(batch: List[str]) -> Tuple[List[str], int]:
    results: List[str] = []
    rejected: int = 0
    for ln in batch:
        s = ln.strip()
        if not s or is_record_blacklisted(s):
            rejected += 1
            continue
        segs = [x for x in _COMBO_SPLIT_RE.split(s) if x]
        if len(segs) < 2:
            rejected += 1
            continue
        user = pwd = ""
        if any(h in s.lower()[:30] for h in _URL_HINTS) or s.count(':') >= 2:
            user = segs[-2].strip()
            pwd = segs[-1].strip()
        else:
            found = _COMBO_FINDER_RE.findall(s)
            if not found:
                rejected += 1
                continue
            cand = found[0].strip()
            ok = False
            for delim in _COMBO_DELIMITERS:
                if delim in cand:
                    parts = cand.split(delim, 1)
                    if len(parts) == 2:
                        user, pwd = parts[0].strip(), parts[1].strip()
                        ok = True
                        break
            if not ok:
                rejected += 1
                continue
        if len(user) < _MIN_COMBO_FIELD or len(pwd) < _MIN_COMBO_FIELD:
            rejected += 1
            continue
        ul = user.lower()
        if ul == 'unknown' or not _LOGIN_CHECK_RE.fullmatch(user):
            rejected += 1
            continue
        tld_hit = '.' in ul and '@' not in ul and any(ul.endswith(f'.{t}') for t in _BLOCKED_TLDS)
        path_hit = '@' not in ul and any(m in ul for m in _BAD_PATH_MARKERS)
        if any(m in ul for m in _BAD_USER_MARKERS) or tld_hit or path_hit:
            rejected += 1
            continue
        clean_user = user.replace(' ', '')
        if len(clean_user) < _MIN_COMBO_FIELD:
            rejected += 1
            continue
        results.append(f"{clean_user}:{pwd}")
    return results, rejected


async def _run_combo_pipeline(lines: List[str]) -> Tuple[List[str], int]:
    total = len(lines)
    chunk = _BIG_CHUNK if total > _CHUNK_CUTOFF else _SMALL_CHUNK
    gathered: List[str] = []
    loop = asyncio.get_running_loop()
    for i in range(0, total, chunk):
        res, _ = await loop.run_in_executor(THREAD_POOL, _scan_combo_batch, lines[i:i + chunk])
        gathered.extend(res)
        await release_event_loop(i)
    unique, removed = await deduplicate_and_order(gathered)
    return unique, removed


async def run_combo_search(keyword: str, caller_file: str) -> Tuple[List[str], int, float]:
    t0 = time.perf_counter()
    paths = collect_datastore_paths(caller_file)
    if not paths:
        return [], 0, _ms(t0)
    rg = ["rg", "-i", "--no-heading", "--no-line-number", "--no-filename", "--fixed-strings", keyword] + paths
    code, out, _ = await invoke_search_engine(rg)
    if code not in (0, 1):
        return [], 0, _ms(t0)
    raw = tokenize_output_lines(out)
    if not raw:
        return [], 0, _ms(t0)
    unique, removed = await _run_combo_pipeline(raw)
    return unique, removed, _ms(t0)


def write_result_file(prefix: str, label: str, lines: List[str]) -> str:
    dl_dir = Path(__file__).resolve().parent.parent / "downloads"
    dl_dir.mkdir(exist_ok=True)
    safe = re.sub(r'[^\w\-.]', '_', label)
    path = str(dl_dir / f"{prefix}_{safe}.txt")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines))
    return path


def write_ulp_file(keyword: str, lines: List[str]) -> str:
    return write_result_file("ULP", keyword, lines)


def log_user_extraction(user_id: int, keyword: Optional[str], fmt_key: str, matched_count: int, source: str = "keyword") -> None:
    """Log user extraction activity to LOGGER and track stats"""
    from helpers.userdb import add_user, increment_stat
    
    # Track user
    add_user(user_id)
    
    # Track statistics
    if fmt_key == "ULP":
        increment_stat("total_ulp_searches")
    elif fmt_key in ("mailpass", "userpass", "num_pass", "domain", "url"):
        increment_stat("total_extract_searches")
    elif fmt_key.upper().startswith("CMB"):
        increment_stat("total_combo_searches")
    
    search_source = f"keyword: {keyword}" if keyword and source == "keyword" else source
    LOGGER.info(
        f"USER_EXTRACTION | User ID: {user_id} | Format: {fmt_key} | "
        f"Source: {search_source} | Matched Lines: {matched_count}"
    )


def _clean_mixed_combo_line(line: str) -> Optional[Tuple[str, str, str]]:
    """
    Clean a mixed combo line and extract url:username:password or email:pass or phone:pass.
    Returns (credential_type, identifier, password) or None if invalid.
    credential_type: "email", "user", "phone", "url_email", "url_user", "url_phone"
    """
    line = line.strip().replace('\r', '')
    if not line or line.startswith('#'):
        return None

    parts = [p.strip() for p in line.split(':')]
    if len(parts) < 2:
        return None

    # Check if it's a URL format (http://, https://, ftp://, etc.)
    # For URL patterns, we need to recombine the protocol and domain
    if parts[0].lower() in ('http', 'https', 'ftp', 'android'):
        # Recombine URL: http://domain... -> http://domain (parts[0]:parts[1])
        # Then identifier and password are the last two meaningful parts
        if len(parts) >= 4:  # protocol : //domain : identifier : password
            identifier = parts[-2]
            password = parts[-1]
            
            # Detect identifier type
            if '@' in identifier and '.' in identifier:
                return ("url_email", identifier, password)
            elif re.match(r'^\+?[\d\s\-\(\)]+$', identifier):
                return ("url_phone", identifier, password)
            elif re.match(r'^[a-zA-Z0-9_-]{4,}$', identifier):
                return ("url_user", identifier, password)
        return None

    # Standard 2-part format: identifier:pass (should be checked first before domain format)
    if len(parts) == 2:
        identifier = parts[0]
        password = parts[1]
        
        if not identifier or not password or len(identifier) < 3 or len(password) < 3:
            return None
        
        # Detect identifier type
        if '@' in identifier and '.' in identifier:
            # Email validation
            if _EMAIL_VALIDATION_RE.match(identifier.lower()):
                return ("email", identifier, password)
        elif re.match(r'^\+?[\d\s\-\(\)]+$', identifier):
            # Phone validation
            cleaned = _PHONE_STRIP_RE.sub('', identifier).replace('+', '')
            if len(cleaned) >= 7:
                return ("phone", identifier, password)
        elif re.match(r'^[a-zA-Z0-9_-]{4,}$', identifier):
            # Username validation
            return ("user", identifier, password)
        return None

    # Check if it's a domain format (has a dot but not a URL) - 3+ parts
    if '.' in parts[0] and not parts[0].startswith(('http', 'https', 'ftp', 'android')):
        # Domain format: domain.com:identifier:pass or domain.com:identifier:pass
        if len(parts) >= 3:
            identifier = parts[-2]
            password = parts[-1]
            
            # Detect identifier type
            if '@' in identifier and '.' in identifier:
                return ("email", identifier, password)
            elif re.match(r'^\+?[\d\s\-\(\)]+$', identifier):
                return ("phone", identifier, password)
            elif re.match(r'^[a-zA-Z0-9_-]{4,}$', identifier):
                return ("user", identifier, password)
        return None

    return None


def _scan_mixed_combo_batch(batch: List[str]) -> Tuple[List[Tuple[str, str, str]], int]:
    """Scan batch of lines and extract mixed format combos. Returns list of (type, identifier, password)."""
    results: List[Tuple[str, str, str]] = []
    rejected: int = 0
    
    for ln in batch:
        result = _clean_mixed_combo_line(ln)
        if result:
            results.append(result)
        else:
            rejected += 1
    
    return results, rejected


async def _run_mixed_combo_pipeline(lines: List[str]) -> Tuple[List[Tuple[str, str, str]], int]:
    """Run async pipeline for mixed combo extraction."""
    total = len(lines)
    chunk = _BIG_CHUNK if total > _CHUNK_CUTOFF else _SMALL_CHUNK
    gathered: List[Tuple[str, str, str]] = []
    loop = asyncio.get_running_loop()
    
    for i in range(0, total, chunk):
        res, _ = await loop.run_in_executor(THREAD_POOL, _scan_mixed_combo_batch, lines[i:i + chunk])
        gathered.extend(res)
        await release_event_loop(i)
    
    # Deduplicate by (type, identifier, password) tuple
    seen: set = set()
    unique: List[Tuple[str, str, str]] = []
    for item in gathered:
        key = (item[0], item[1].lower(), item[2].lower())
        if key not in seen:
            seen.add(key)
            unique.append(item)
    
    removed = len(gathered) - len(unique)
    return unique, removed


async def _run_mixed_combo_pipeline_streaming(
    file_paths: List[str],
    max_results: int = 1000,
    cache_size: int = 50000
) -> Tuple[List[Tuple[str, str, str]], int, float]:
    """
    Memory-efficient streaming pipeline for mixed combo extraction.
    
    Processes files sequentially, deduplicates on-the-fly, and limits memory usage.
    Returns only first max_results unique combos.
    
    Args:
        file_paths: List of database file paths to scan
        max_results: Maximum number of unique combos to return (default 1000)
        cache_size: Size of deduplication cache (default 50000)
    
    Returns:
        Tuple of (unique_combos, total_removed_duplicates, elapsed_ms)
    """
    t0 = time.perf_counter()
    loop = asyncio.get_running_loop()
    
    # Track deduplication with bounded cache
    seen: set = set()
    unique: List[Tuple[str, str, str]] = []
    total_removed = 0
    
    # Process each file individually to avoid loading all data at once
    for path in file_paths:
        try:
            # Read file line by line in streaming fashion
            batch: List[str] = []
            batch_count = 0
            
            with open(path, 'r', encoding='utf-8', errors='replace') as f:
                for ln in f:
                    ln = ln.rstrip('\n')
                    if ln.strip():
                        batch.append(ln)
                        batch_count += 1
                    
                    # Process batch when it reaches target size
                    if batch_count >= _SMALL_CHUNK:
                        res, _ = await loop.run_in_executor(THREAD_POOL, _scan_mixed_combo_batch, batch)
                        
                        # Deduplicate on-the-fly
                        for item in res:
                            if len(unique) >= max_results:
                                # Reached result limit, stop processing
                                break
                            
                            key = (item[0], item[1].lower(), item[2].lower())
                            if key not in seen:
                                seen.add(key)
                                unique.append(item)
                            else:
                                total_removed += 1
                            
                            # Keep cache bounded to prevent memory issues
                            if len(seen) > cache_size:
                                # Remove oldest half from seen to maintain bounded memory
                                seen_list = list(seen)
                                seen = set(seen_list[len(seen_list)//2:])
                        
                        batch = []
                        batch_count = 0
                        await release_event_loop(len(unique))
                        
                        # Stop if we've reached the result limit
                        if len(unique) >= max_results:
                            break
            
            # Process remaining batch
            if batch and len(unique) < max_results:
                res, _ = await loop.run_in_executor(THREAD_POOL, _scan_mixed_combo_batch, batch)
                for item in res:
                    if len(unique) >= max_results:
                        break
                    key = (item[0], item[1].lower(), item[2].lower())
                    if key not in seen:
                        seen.add(key)
                        unique.append(item)
                    else:
                        total_removed += 1
            
            # Stop if we've reached the result limit
            if len(unique) >= max_results:
                break
                
        except Exception as e:
            LOGGER.error(f"Error reading {path}: {e}")
            continue
    
    elapsed = _ms(t0)
    return unique, total_removed, elapsed


async def scan_db_for_mixed_combos(
    caller_file: str,
    max_results: int = 1000,
    use_streaming: bool = True
) -> Tuple[List[Tuple[str, str, str]], int, float]:
    """
    Scan database for mixed format combos with memory-efficient streaming.
    
    Args:
        caller_file: Module file path for locating datastore
        max_results: Maximum number of results to return (default 1000)
        use_streaming: Use memory-efficient streaming mode (default True)
    
    Returns:
        Tuple of (unique_combos, total_removed_duplicates, elapsed_ms)
    """
    t0 = time.perf_counter()
    paths = collect_datastore_paths(caller_file)
    if not paths:
        return [], 0, _ms(t0)
    
    if use_streaming:
        # Use memory-efficient streaming approach
        unique, removed, _ = await _run_mixed_combo_pipeline_streaming(paths, max_results)
        return unique, removed, _ms(t0)
    else:
        # Original approach for backward compatibility
        # Read all lines from all database files
        all_lines: List[str] = []
        for path in paths:
            try:
                with open(path, 'r', encoding='utf-8', errors='replace') as f:
                    all_lines.extend([ln.rstrip('\n') for ln in f if ln.strip()])
            except Exception as e:
                LOGGER.error(f"Error reading {path}: {e}")
                continue
        
        if not all_lines:
            return [], 0, _ms(t0)
        
        unique, removed = await _run_mixed_combo_pipeline(all_lines)
        return unique, removed, _ms(t0)


def get_file_size_str(path: str) -> str:
    b = os.path.getsize(path)
    if b < 1024:
        return f"{b} B"
    if b < 1024 * 1024:
        return f"{b / 1024:.2f} KB"
    return f"{b / (1024 * 1024):.2f} MB"


def _ms(t0: float) -> float:
    return round((time.perf_counter() - t0) * 1000, 2)


async def notify_combo_extraction(user_id: int, keyword: str, fmt_key: str, matched_count: int) -> None:
    """Send notification to owner when anyone extracts combos (not to admin)"""
    import config
    from helpers.botutils import send_message
    
    # Only send to owner, not admin
    if user_id == config.OWNER_ID:
        # Don't notify owner about their own actions
        return
    
    try:
        notification_text = (
            f"**🔔 Combo Extraction Alert**\n"
            f"**━━━━━━━━━━━━━━━━**\n"
            f"**User ID** : `{user_id}`\n"
            f"**Keyword** : `{keyword}`\n"
            f"**Format** : `{fmt_key}`\n"
            f"**Matched Lines** : `{matched_count}`\n"
            f"**━━━━━━━━━━━━━━━━**"
        )
        await send_message(config.OWNER_ID, notification_text)
        LOGGER.info(f"Sent combo extraction notification to owner for user {user_id}")
    except Exception as e:
        LOGGER.error(f"Failed to send combo extraction notification: {e}")
