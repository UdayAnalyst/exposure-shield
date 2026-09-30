"""Breaches from HaveIBeenPwned (Troy Hunt).

Two sources:
- Catalog (no key): data/hibp_breaches.json, HIBP's public list of every known breach. We match it
  against the platforms the email is registered on: "you have a Dropbox account, and Dropbox was
  breached in 2012". Real data, but it doesn't prove *this* email was in the breach -> confirmed=False.
- Per-email API (needs HIBP_API_KEY): breaches this exact email appears in -> confirmed=True.

Refresh the catalog: curl -A ExposureShield https://haveibeenpwned.com/api/v3/breaches -o data/hibp_breaches.json
"""

import asyncio
import json
import os
import re
from pathlib import Path

import httpx

DATA = Path(__file__).resolve().parents[2] / "data"
USER_AGENT = "ExposureShield-Hackathon"

# Platform domains that HIBP files under a different domain
DOMAIN_ALIASES = {"x.com": "twitter.com", "tv.apple.com": "apple.com"}


def _clean(html: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", html or "")).strip()


def _to_breach(b: dict, confirmed: bool) -> dict:
    return {
        "name": b["Title"],
        "domain": b.get("Domain") or None,
        "date": b.get("BreachDate"),
        "accounts_affected": b.get("PwnCount"),
        "data_classes": b.get("DataClasses", []),
        "description": _clean(b.get("Description", "")),
        "confirmed": confirmed,
    }


def _load_catalog() -> dict[str, list[dict]]:
    try:
        raw = json.loads((DATA / "hibp_breaches.json").read_text())
    except (OSError, ValueError):
        return {}
    by_domain: dict[str, list[dict]] = {}
    for b in raw:
        if b.get("Domain") and not b.get("IsFabricated") and not b.get("IsSpamList"):
            by_domain.setdefault(b["Domain"].lower(), []).append(b)
    return by_domain


CATALOG = _load_catalog()


def catalog_breaches(domain: str) -> list[dict]:
    domain = domain.lower().removeprefix("www.")
    return CATALOG.get(DOMAIN_ALIASES.get(domain, domain), [])


async def confirmed_breaches(email: str) -> tuple[list[dict] | None, str | None]:
    """Per-email lookup. Returns (breaches, error); breaches is None when no key or the call failed."""
    key = os.environ.get("HIBP_API_KEY", "").strip()
    if not key:
        return None, None
    url = f"https://haveibeenpwned.com/api/v3/breachedaccount/{email}"
    headers = {"hibp-api-key": key, "user-agent": USER_AGENT}
    async with httpx.AsyncClient(timeout=10) as client:
        for attempt in range(3):
            try:
                r = await client.get(url, headers=headers, params={"truncateResponse": "false"})
            except httpx.HTTPError:
                return None, "HaveIBeenPwned unreachable; showing breaches at services you use instead"
            if r.status_code == 404:  # not in any breach — good news
                return [], None
            if r.status_code == 429 and attempt < 2:
                await asyncio.sleep(min(float(r.headers.get("retry-after", 2)), 5))
                continue
            if r.status_code != 200:
                return None, f"HaveIBeenPwned error {r.status_code}; showing breaches at services you use instead"
            return [_to_breach(b, confirmed=True) for b in r.json()], None
    return None, "HaveIBeenPwned rate limit; showing breaches at services you use instead"


def breaches_for_platforms(platforms: list[dict], confirmed: list[dict] | None) -> list[dict]:
    """Confirmed breaches (if any) plus catalog breaches at the user's platforms, newest first."""
    out = list(confirmed or [])
    seen = {b["name"] for b in out}
    for p in platforms:
        for b in catalog_breaches(p["domain"]):
            if b["Title"] not in seen:
                seen.add(b["Title"])
                out.append(_to_breach(b, confirmed=False))
    out.sort(key=lambda b: b["date"] or "", reverse=True)
    return out
