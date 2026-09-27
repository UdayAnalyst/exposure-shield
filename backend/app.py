"""ExposureShield API — POST /api/scan with {"email": ...} returns the full exposure report.

No user data is stored or logged: every scan is computed in memory and discarded.
"""

import re
import time
from typing import Literal, Optional

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, field_validator

from scanner.accounts import check_email

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
    data_classes: list[str] = []
    description: str = ""


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
    demo_mode: bool = False
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

    # TODO Step 3: run HIBP concurrently with account discovery (asyncio.gather).
    accounts = await check_email(req.email)
    platforms = [Platform(**p) for p in accounts["registered"]]
    skipped = accounts["checked"] - accounts["answered"]
    if skipped:
        errors.append(f"{skipped} of {accounts['checked']} sites could not be checked (blocked, rate-limited or timed out)")

    # TODO Step 3-6: breaches, deletion URLs, score, cleanup plan.
    breaches: list[Breach] = []

    return ScanResponse(
        email=req.email,
        scan_time_seconds=round(time.perf_counter() - start, 2),
        platforms_checked=accounts["checked"],
        platforms_answered=accounts["answered"],
        errors=errors,
        platforms=platforms,
        breaches=breaches,
        score=Score(value=0, label="Low", color="green"),
        cleanup_plan=[],
        summary=Summary(total_platforms=len(platforms), total_breaches=len(breaches)),
    )
