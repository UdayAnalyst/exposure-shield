"""Find platforms an email is registered on, using holehe's per-site check modules.

holehe's CLI runs its modules under trio; each module is just `async fn(email, client, out)`
that appends a result dict to `out`, so we run them directly with asyncio.gather.
"""

import asyncio
import sys
import time

import httpx
from holehe.core import import_submodules

# Sites whose check goes through a password-recovery flow and may notify the account owner.
# Same list holehe skips with --no-password-recovery. We promise "no alerts are sent", so skip them.
ALERTING_SITES = {"adobe", "mail_ru", "odnoklassniki", "samsung"}

# Hidden by default: results are shown on a projector with a volunteer's email.
ADULT_CATEGORIES = {"porn"}

DISPLAY_NAMES = {
    "github": "GitHub", "linkedin": "LinkedIn", "lastfm": "Last.fm", "lastpass": "LastPass",
    "office365": "Office 365", "protonmail": "ProtonMail", "soundcloud": "SoundCloud",
    "wordpress": "WordPress", "codepen": "CodePen", "hubspot": "HubSpot", "ebay": "eBay",
    "aboutme": "About.me", "anydo": "Any.do", "sevencups": "7 Cups", "buymeacoffee": "Buy Me a Coffee",
    "cracked_to": "Cracked.to", "mail_ru": "Mail.ru", "devrant": "devRant", "vsco": "VSCO",
    "amocrm": "amoCRM", "nocrm": "noCRM", "dominosfr": "Domino's FR", "voxmedia": "Vox Media",
}


def _load_sites() -> list[tuple[str, str, object]]:
    """Return (site_name, category, check_fn) for every holehe module."""
    sites = []
    for full_name, module in import_submodules("holehe.modules").items():
        parts = full_name.split(".")  # holehe.modules.<category>.<site>
        if len(parts) != 4:
            continue
        category, site = parts[2], parts[3]
        fn = getattr(module, site, None)
        if fn is not None and site not in ALERTING_SITES:
            sites.append((site, category, fn))
    return sites


SITES = _load_sites()


def display_name(site: str) -> str:
    return DISPLAY_NAMES.get(site, site.replace("_", " ").title())


async def check_email(email: str, per_site_timeout: float = 6.0, include_adult: bool = False,
                      skip: frozenset[str] = frozenset()) -> dict:
    """Run every site check concurrently; total time is capped at roughly per_site_timeout.

    `skip` holds site names already covered by another scanner (see scanner/accounts.py).
    Returns {"registered": [...], "checked": int, "answered": int}.
    Never raises for individual site failures — partial results are the norm.
    """
    sites = [s for s in SITES if (include_adult or s[1] not in ADULT_CATEGORIES) and s[0] not in skip]
    categories = {site: category for site, category, _ in sites}
    out: list[dict] = []

    async def run(fn) -> None:
        try:
            await asyncio.wait_for(fn(email, client, out), per_site_timeout)
        except Exception:  # timeouts, parse errors, blocked requests — skip the site
            pass

    # holehe was written for httpx < 0.20, which followed redirects by default. Without this,
    # ~10 sites misreport a redirect as a rate limit.
    async with httpx.AsyncClient(timeout=per_site_timeout, follow_redirects=True) as client:
        await asyncio.gather(*(run(fn) for _, _, fn in sites))

    registered = []
    for r in out:
        if r.get("exists") is not True:
            continue
        site = r.get("name", "")
        # Partially masked recovery info some sites leak (e.g. "u*****1@gmail.com") — itself an exposure.
        details = {k: v for k, v in (("email_recovery", r.get("emailrecovery")),
                                     ("phone_number", r.get("phoneNumber"))) if v}
        registered.append({
            "name": display_name(site),
            "domain": r.get("domain", ""),
            "category": categories.get(site),
            "details": details or None,
        })

    return {
        "registered": registered,
        "checked": len(sites),
        "answered": sum(1 for r in out if r.get("rateLimit") is False),
    }


if __name__ == "__main__":
    # Manual test: python -m scanner.holehe_scan someone@example.com
    start = time.perf_counter()
    result = asyncio.run(check_email(sys.argv[1]))
    for p in result["registered"]:
        print(f"  {p['name']:<20} {p['domain']:<25} {p['category']}")
    print(f"{len(result['registered'])} registered, {result['answered']}/{result['checked']} sites answered "
          f"in {time.perf_counter() - start:.1f}s")
