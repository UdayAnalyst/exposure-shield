"""Find platforms an email is registered on, using user-scanner (kaifcodec/user-scanner, MIT).

user-scanner is the actively maintained successor to holehe. We use its per-site `validate_*`
functions directly instead of its orchestrator, which draws a terminal progress bar and
patches httpx globally.
"""

import asyncio
import inspect
import ssl
from urllib.parse import urlparse

import certifi
import httpx
from user_scanner.core.helpers import (
    get_scan_func,
    get_site_name,
    is_loud,
    load_categories,
    load_modules,
    set_global_timeout,
)
from user_scanner.core.result import Status

# Every module creates its own httpx.AsyncClient, and each one builds an SSL context by loading
# the CA bundle synchronously (~35ms). ~170 of those froze the event loop for 6-10s per scan.
# Share one context (same trust store) unless a caller passes its own `verify`.
_SSL_CONTEXT = ssl.create_default_context(cafile=certifi.where())
_original_async_client_init = httpx.AsyncClient.__init__


def _async_client_init(self, *args, **kwargs):
    kwargs.setdefault("verify", _SSL_CONTEXT)
    _original_async_client_init(self, *args, **kwargs)


httpx.AsyncClient.__init__ = _async_client_init  # type: ignore[method-assign]

DISPLAY_NAMES = {
    "x": "X (Twitter)", "office365": "Office 365", "appletv": "Apple TV", "huggingface": "Hugging Face",
    "myfitnesspal": "MyFitnessPal", "chess_com": "Chess.com", "github": "GitHub", "linkedin": "LinkedIn",
    "youtube": "YouTube", "tiktok": "TikTok", "paypal": "PayPal", "ebay": "eBay", "wordpress": "WordPress",
    "codepen": "CodePen", "hackerrank": "HackerRank", "hackerone": "HackerOne", "hackerearth": "HackerEarth",
    "devrant": "devRant", "soundcloud": "SoundCloud", "deviantart": "DeviantArt", "onlyfans": "OnlyFans",
}


def stem(module) -> str:
    """Module file name, e.g. 'chess_com' — stable key for dedup and display overrides."""
    return module.__name__.split(".")[-1].lower()


def _load(include_adult: bool) -> list[tuple[str, object, object]]:
    """Return (category, module, validate_fn) for every non-loud email module."""
    sites = []
    for category, path in load_categories(is_email=True, no_nsfw=not include_adult).items():
        for module in load_modules(path):
            fn = get_scan_func(module)
            # "Loud" modules can notify the account owner (e.g. send a code) — never run them.
            if fn is not None and not is_loud(get_site_name(module), is_email=True):
                sites.append((category, module, fn))
    return sites


SITES = _load(include_adult=False)
COVERED = {stem(m) for _, m, _ in _load(include_adult=True)}  # everything user-scanner knows, loud included


def _domain(url: str, fallback: str) -> str:
    host = urlparse(url).netloc.lower() if url else ""
    return host.removeprefix("www.") or fallback


async def check_email(email: str, per_site_timeout: float = 6.0, concurrency: int = 50) -> dict:
    """Run every site check concurrently. Same return shape as holehe_scan.check_email."""
    # Measured 2026-09-26: 122/125 answers arrive within 4s, all within 6s.
    # This caps each HTTP request inside user-scanner; wait_for below caps multi-request modules.
    set_global_timeout(per_site_timeout)
    sem = asyncio.Semaphore(concurrency)
    registered: list[dict] = []
    answered = 0

    async def run(category: str, module, fn) -> None:
        nonlocal answered
        async with sem:
            try:
                call = fn(email) if inspect.iscoroutinefunction(fn) else asyncio.to_thread(fn, email)
                result = await asyncio.wait_for(call, per_site_timeout + 1)
            except Exception:
                return
        if result.status == Status.AVAILABLE:
            answered += 1
        elif result.status == Status.TAKEN:
            answered += 1
            s = stem(module)
            registered.append({
                "name": DISPLAY_NAMES.get(s, get_site_name(module)),
                "domain": _domain(result.url, f"{s.replace('_', '.')}"),
                "category": category,
                "details": {k: str(v) for k, v in result.extra.items()} or None,
            })

    await asyncio.gather(*(run(*site) for site in SITES))
    return {"registered": registered, "checked": len(SITES), "answered": answered}
