"""Exposure score (0-100) and rule-based cleanup plan.

Formula from CLAUDE.md Step 5. Breaches that only come from the HIBP catalog (the service was breached,
but we can't confirm this email was in it) count at CATALOG_WEIGHT; confirmed ones count in full.
The score should feel right and read clearly on stage — it isn't a scientific measure.
"""

CATALOG_WEIGHT = 0.3
SENSITIVE = {"Passwords": 15, "Phone numbers": 5, "Physical addresses": 10, "IP addresses": 3}
LEVELS = [(25, "Low", "green"), (50, "Moderate", "yellow"), (75, "High", "orange"), (100, "Critical", "red")]


def _weight(b: dict) -> float:
    return 1.0 if b["confirmed"] else CATALOG_WEIGHT


def breaches_by_domain(breaches: list[dict]) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    for b in breaches:
        if b.get("domain"):
            out.setdefault(b["domain"], []).append(b)
    return out


def score(platforms: list[dict], breaches: list[dict]) -> dict:
    base = len(platforms) * 1.5
    for b in breaches:
        w = _weight(b)
        base += 8 * w
        base += sum(pts for cls, pts in SENSITIVE.items() if cls in b["data_classes"]) * w
    # Registered AND breached: only confirmed breaches count here — catalog breaches are matched *by* platform,
    # so every one of them would count twice.
    base += 12 * sum(1 for p in platforms if any(b["confirmed"] for b in p["_breaches"]))
    value = min(int(base), 100)
    label, color = next((l, c) for cap, l, c in LEVELS if value <= cap)
    return {"value": value, "label": label, "color": color}


def platform_risk(breaches: list[dict]) -> str:
    if not breaches:
        return "low"
    leaked_pw = any("Passwords" in b["data_classes"] for b in breaches)
    if any(b["confirmed"] for b in breaches):
        return "critical" if leaked_pw else "high"
    return "high" if leaked_pw else "moderate"


def cleanup_plan(platforms: list[dict]) -> list[dict]:
    """Rule-based plan: secure breached accounts (passwords first), then offer deletions (easiest first)."""
    actions = []
    breached = [p for p in platforms if p["_breaches"]]
    order = {"critical": 0, "high": 1, "moderate": 2, "low": 3}
    for p in sorted(breached, key=lambda p: order[p["risk_level"]]):
        latest = max(p["_breaches"], key=lambda b: b["date"] or "")
        year = (latest["date"] or "")[:4]
        if "Passwords" in latest["data_classes"]:
            why = "your password was leaked" if latest["confirmed"] else f"passwords leaked in its {year} breach"
            actions.append({"action": f"Change your {p['name']} password ({why})",
                            "url": f"https://{p['domain']}", "type": "change_password"})
        actions.append({"action": f"Turn on two-factor authentication for {p['name']} (breached {year})",
                        "url": f"https://{p['domain']}", "type": "enable_2fa"})
    rank = {"easy": 0, "medium": 1, "hard": 2, "limited": 3, "impossible": 4, None: 5}
    deletable = [p for p in platforms if p.get("delete_url")]
    for p in sorted(deletable, key=lambda p: (not p["_breaches"], rank.get(p.get("delete_difficulty"), 5))):
        actions.append({"action": f"Delete your {p['name']} account if you no longer use it",
                        "url": p["delete_url"], "difficulty": p.get("delete_difficulty"), "type": "delete_account"})
    if breached:
        actions.append({"action": "Use a password manager so no two sites share a password — one leak then can't unlock the rest",
                        "type": "review"})
    for i, a in enumerate(actions, 1):
        a["priority"] = i
    return actions
