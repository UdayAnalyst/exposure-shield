"""Account deletion links from JustDeleteMe (jdm-contrib/jdm, data/justdelete.json).

Refresh: curl https://raw.githubusercontent.com/jdm-contrib/jdm/master/_data/sites.json -o data/justdelete.json
"""

import json
from pathlib import Path

DATA = Path(__file__).resolve().parents[2] / "data"
DOMAIN_ALIASES = {"x.com": "twitter.com", "tv.apple.com": "apple.com", "office365.com": "microsoft.com"}


def _load() -> dict[str, dict]:
    try:
        sites = json.loads((DATA / "justdelete.json").read_text())
    except (OSError, ValueError):
        return {}
    index = {}
    for s in sites:
        for d in s.get("domains", []):
            index.setdefault(d.lower().removeprefix("www."), s)
    return index


INDEX = _load()


def lookup(domain: str) -> dict | None:
    """{'url', 'difficulty', 'notes'} for a platform domain, or None. Tries parent domains too."""
    d = domain.lower().removeprefix("www.")
    d = DOMAIN_ALIASES.get(d, d)
    parts = d.split(".")
    for i in range(len(parts) - 1):  # account.example.co -> example.co
        site = INDEX.get(".".join(parts[i:]))
        if site and site.get("url"):
            return {"url": site["url"], "difficulty": site.get("difficulty"), "notes": site.get("notes")}
    return None
