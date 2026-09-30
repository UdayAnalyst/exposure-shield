"""AI advisor: turns scan results into a plain-language explanation and a short, personalized action plan.

Uses Gemini (GEMINI_API_KEY; model via GEMINI_MODEL). Privacy: the email address is never sent —
only platform names, breach facts and the score. Falls back to the rule-based plan on any failure,
so the demo never shows a blank panel.
"""

import json
import os

import httpx

# Tried in order; a model that errors (e.g. 503 "high demand") falls through to the next.
# Measured 2026-09-30: 3.6-flash ~1s, flash-lite-latest ~0.7s, 3.5-flash ~7s; flash-latest was 503.
MODELS = [m for m in [os.environ.get("GEMINI_MODEL"), "gemini-3.6-flash", "gemini-flash-lite-latest",
                      "gemini-3.5-flash"] if m]
URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"

SYSTEM = """You are ExposureShield's privacy advisor. You explain a person's digital exposure to someone \
who is NOT technical — think a retiree, a busy parent, or a student working two jobs. Scams and identity \
theft hit people who can least afford the loss hardest, so be clear, calm and practical — never alarmist.

You get: the accounts found for their email, breaches at those services (from HaveIBeenPwned), and an \
exposure score. A breach with confirmed=false means the SERVICE was breached, not proof their own account \
was in it. If no breach is confirmed, never say their data or password "was found" or "was leaked" — say \
"services you use were breached" and "your details may have been exposed". Only use facts from the input; \
never invent breaches, dates, numbers or links.

Write:
- headline: one short sentence (max 15 words) naming the single most important risk.
- explanation: 2-3 plain sentences on what their exposure means in real life (e.g. phishing emails that \
quote a real breach, password reuse letting one leak unlock other accounts).
- actions: 3 to 5 steps, most important first. Each has a short imperative title, one sentence on why it \
matters for THEM (name the service and breach year), the exact account name from the input (or "" for a \
general step), and its kind. Don't include URLs — the app adds the right link.
- encouragement: one sentence on how much safer these steps make them."""

SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "headline": {"type": "STRING"},
        "explanation": {"type": "STRING"},
        "actions": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "properties": {
                    "title": {"type": "STRING"},
                    "why": {"type": "STRING"},
                    "account": {"type": "STRING"},
                    "kind": {"type": "STRING", "enum": ["change_password", "enable_2fa", "delete_account", "other"]},
                },
                "required": ["title", "why", "account", "kind"],
            },
        },
        "encouragement": {"type": "STRING"},
    },
    "required": ["headline", "explanation", "actions", "encouragement"],
}


def _facts(scan: dict) -> dict:
    """The only data sent to the model — no email address."""
    return {
        "score": scan["score"],
        "accounts": [
            {k: p.get(k) for k in ("name", "domain", "category", "risk_level", "breach_date", "data_leaked",
                                   "delete_url", "delete_difficulty")}
            for p in scan["platforms"]
        ],
        "breaches": [
            {k: b.get(k) for k in ("name", "domain", "date", "data_classes", "accounts_affected", "confirmed")}
            for b in scan["breaches"]
        ],
        "rule_based_plan": [{k: a.get(k) for k in ("action", "url", "type")} for a in scan["cleanup_plan"][:12]],
    }


def _link(action: dict, scan: dict) -> dict:
    """Attach the right URL from our own data: deletion page for deletes, the service's site otherwise."""
    p = next((p for p in scan["platforms"] if p["name"].lower() == (action.get("account") or "").lower()), None)
    url = None
    if p:
        url = p.get("delete_url") if action.get("kind") == "delete_account" else f"https://{p['domain']}"
    return {"title": action.get("title", ""), "why": action.get("why", ""), "url": url}


def fallback(scan: dict, reason: str) -> dict:
    plan = scan["cleanup_plan"]
    return {
        "ai": False,
        "reason": reason,
        "headline": f"Exposure score {scan['score']['value']}/100 ({scan['score']['label']})",
        "explanation": "",
        "actions": [{"title": a["action"], "why": "", "url": a.get("url")} for a in plan[:5]],
        "encouragement": "",
    }


async def advise(scan: dict) -> dict:
    key = os.environ.get("GEMINI_API_KEY", "").strip()
    if not key:
        return fallback(scan, "No GEMINI_API_KEY set")
    body = {
        "systemInstruction": {"parts": [{"text": SYSTEM}]},
        "contents": [{"role": "user", "parts": [{"text": json.dumps(_facts(scan))}]}],
        "generationConfig": {"responseMimeType": "application/json", "responseSchema": SCHEMA, "temperature": 0.4},
    }
    reason = "AI unavailable"
    async with httpx.AsyncClient(timeout=15) as client:
        for model in MODELS:
            try:
                r = await client.post(URL.format(model=model), headers={"x-goog-api-key": key}, json=body)
                if r.status_code != 200:
                    reason = f"Gemini error {r.status_code}"
                    continue
                parts = r.json()["candidates"][0]["content"]["parts"]
                out = json.loads("".join(p.get("text", "") for p in parts))
                out["actions"] = [_link(a, scan) for a in out.get("actions", [])[:5]]
                return {"ai": True, "model": model, **out}
            except (httpx.HTTPError, KeyError, IndexError, ValueError) as e:
                reason = f"AI unavailable ({type(e).__name__})"
    return fallback(scan, reason)
