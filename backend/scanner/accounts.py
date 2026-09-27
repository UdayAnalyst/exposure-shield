"""Combined account discovery: user-scanner (primary, maintained) + holehe (for sites user-scanner lacks).

Both run concurrently; holehe skips any site user-scanner already covers so each site is checked once.
Neither source had false positives against a random unregistered address (tested 2026-09-26).
"""

import asyncio
import re
import sys
import time

from scanner import holehe_scan, userscanner_scan

# holehe site name -> user-scanner module stem, where they differ
ALIASES = {"twitter": "x"}


def _norm(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", name.lower())


_covered = {_norm(s) for s in userscanner_scan.COVERED}
HOLEHE_SKIP = frozenset(site for site, _, _ in holehe_scan.SITES if _norm(ALIASES.get(site, site)) in _covered)


async def check_email(email: str) -> dict:
    """Returns {"registered": [...], "checked": int, "answered": int}; never raises for site failures."""
    results = await asyncio.gather(
        userscanner_scan.check_email(email),
        holehe_scan.check_email(email, skip=HOLEHE_SKIP),
        return_exceptions=True,
    )
    registered, seen = [], set()
    checked = answered = 0
    for r in results:
        if isinstance(r, BaseException):  # a whole scanner failed — keep the other one's results
            continue
        checked += r["checked"]
        answered += r["answered"]
        for p in r["registered"]:
            key = p["domain"] or _norm(p["name"])
            if key not in seen:
                seen.add(key)
                registered.append(p)
    registered.sort(key=lambda p: p["name"].lower())
    return {"registered": registered, "checked": checked, "answered": answered}


if __name__ == "__main__":
    # Manual test: python -m scanner.accounts someone@example.com
    start = time.perf_counter()
    result = asyncio.run(check_email(sys.argv[1]))
    for p in result["registered"]:
        print(f"  {p['name']:<20} {p['domain']:<25} {p['category']}")
    print(f"{len(result['registered'])} registered, {result['answered']}/{result['checked']} sites answered "
          f"in {time.perf_counter() - start:.1f}s")
