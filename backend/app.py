"""ExposureShield API — POST /api/scan with {"email": ...} returns the full exposure report.

No user data is stored or logged: every scan is computed in memory and discarded.
"""

import asyncio
import os
import re
import time
from pathlib import Path
from typing import Literal, Optional

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, field_validator


def _load_dotenv(path: Path) -> None:
    """Local dev: read backend/.env (git-ignored). On the VM, systemd loads /etc/exposureshield.env."""
    if path.exists():
        for line in path.read_text().splitlines():
            k, sep, v = line.partition("=")
            if sep and k.strip() and not k.lstrip().startswith("#"):
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


_load_dotenv(Path(__file__).with_name(".env"))

import advisor  # noqa: E402  (reads GEMINI_MODEL at import)
from scanner import breaches as hibp
from scanner import justdelete
from scanner.accounts import check_email
from scoring import engine

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


# ---------- Request / response schema ----------

class ScanRequest(BaseModel):
    email: str

    @field_validator("email")
    @classmethod
    def validate_email(cls, v: str) -> str:
        v = v.strip().lower()
        if not EMAIL_RE.match(v):
            raise ValueError("Invalid email address")
        return v


class Platform(BaseModel):
    name: str
    domain: str
    category: Optional[str] = None
    details: Optional[dict[str, str]] = None  # extra info the site leaked, e.g. masked recovery phone/email
    exists: bool = True
    breached: bool = False
    breach_date: Optional[str] = None
    data_leaked: list[str] = []
    risk_level: Literal["low", "moderate", "high", "critical"] = "low"
    delete_url: Optional[str] = None
    delete_difficulty: Optional[str] = None


class Breach(BaseModel):
    name: str
    domain: Optional[str] = None
    date: Optional[str] = None
    accounts_affected: Optional[int] = None
    data_classes: list[str] = []
    description: str = ""
    confirmed: bool = False  # True: this email is in the breach (HIBP API). False: a service you use was breached.


class Score(BaseModel):
    value: int
    label: Literal["Low", "Moderate", "High", "Critical"]
    color: Literal["green", "yellow", "orange", "red"]


class CleanupAction(BaseModel):
    priority: int
    action: str
    url: Optional[str] = None
    difficulty: Optional[str] = None
    type: Literal["change_password", "delete_account", "enable_2fa", "review"]


class Summary(BaseModel):
    total_platforms: int = 0
    total_breaches: int = 0
    passwords_leaked: int = 0
    accounts_to_delete: int = 0
    accounts_to_secure: int = 0


class ScanResponse(BaseModel):
    email: str
    scan_time_seconds: float
    platforms_checked: int = 0
    platforms_answered: int = 0  # sites that gave a definite yes/no (others blocked or timed out)
    breach_mode: Literal["confirmed", "services"] = "services"  # "confirmed" needs HIBP_API_KEY
    errors: list[str] = []  # non-fatal issues (e.g. HIBP rate-limited) — partial results still returned
    platforms: list[Platform] = []
    breaches: list[Breach] = []
    score: Score
    cleanup_plan: list[CleanupAction] = []
    summary: Summary


# ---------- App ----------

app = FastAPI(title="ExposureShield", version="0.1.0")

# Frontend is a static file (file:// or python -m http.server), so allow any origin.
# No cookies/credentials are used, so this is safe.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)


@app.get("/api/health")
async def health():
    return {"status": "ok"}


@app.post("/api/scan", response_model=ScanResponse)
async def scan(req: ScanRequest) -> ScanResponse:
    start = time.perf_counter()
    errors: list[str] = []

    accounts, (confirmed, hibp_error) = await asyncio.gather(
        check_email(req.email), hibp.confirmed_breaches(req.email))
    skipped = accounts["checked"] - accounts["answered"]
    if skipped:
        errors.append(f"{skipped} of {accounts['checked']} sites could not be checked (blocked, rate-limited or timed out)")
    if hibp_error:
        errors.append(hibp_error)

    platforms = accounts["registered"]
    breaches = hibp.breaches_for_platforms(platforms, confirmed)
    by_domain = engine.breaches_by_domain(breaches)
    for p in platforms:
        p["_breaches"] = by_domain.get(hibp.DOMAIN_ALIASES.get(p["domain"], p["domain"]), [])
        p["breached"] = bool(p["_breaches"])
        if p["breached"]:
            latest = max(p["_breaches"], key=lambda b: b["date"] or "")
            p["breach_date"] = latest["date"]
            p["data_leaked"] = sorted({c for b in p["_breaches"] for c in b["data_classes"]})
        p["risk_level"] = engine.platform_risk(p["_breaches"])
        if d := justdelete.lookup(p["domain"]):
            p["delete_url"], p["delete_difficulty"] = d["url"], d["difficulty"]

    score = engine.score(platforms, breaches)
    plan = engine.cleanup_plan(platforms)
    platforms.sort(key=lambda p: ({"critical": 0, "high": 1, "moderate": 2, "low": 3}[p["risk_level"]], p["name"].lower()))

    return ScanResponse(
        email=req.email,
        scan_time_seconds=round(time.perf_counter() - start, 2),
        platforms_checked=accounts["checked"],
        platforms_answered=accounts["answered"],
        breach_mode="confirmed" if confirmed is not None else "services",
        errors=errors,
        platforms=[Platform(**{k: v for k, v in p.items() if not k.startswith("_")}) for p in platforms],
        breaches=[Breach(**b) for b in breaches],
        score=Score(**score),
        cleanup_plan=[CleanupAction(**a) for a in plan],
        summary=Summary(
            total_platforms=len(platforms),
            total_breaches=len(breaches),
            passwords_leaked=sum("Passwords" in b["data_classes"] for b in breaches),
            accounts_to_delete=sum(1 for p in platforms if p.get("delete_url")),
            accounts_to_secure=sum(1 for p in platforms if p["breached"]),
        ),
    )


@app.post("/api/advise")
async def advise(scan: ScanResponse) -> dict:
    """AI explanation + action plan for a finished scan. Separate from /api/scan so results show first."""
    return await advisor.advise(scan.model_dump())


# Local dev: serve the frontend from the same origin (on the VM, Caddy serves it). Mounted last so /api/* wins.
_FRONTEND = Path(__file__).resolve().parent.parent / "frontend"
if _FRONTEND.is_dir():
    from fastapi.staticfiles import StaticFiles

    app.mount("/", StaticFiles(directory=_FRONTEND, html=True), name="frontend")
