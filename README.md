# ExposureShield

**Hack for Humanity 2026 (Hartford) · Challenge track: Open Category**

Enter one email address. In about 10 seconds, see every account tied to it, which of those services have been breached, an exposure score, and a plain-language cleanup plan written by AI.

## The problem, in Connecticut

In 2024, Connecticut residents filed **43,890 fraud reports** with the FTC and reported **$90.3 million in fraud losses**. The **median loss was $432**, which is a serious hit for households already stretched thin. They also filed **8,502 identity-theft reports**; 45% involved credit cards, 14% bank accounts and 11% loans or leases. Imposter scams made up 13% of all reports.
*Source: FTC Consumer Sentinel Network Data Book 2024, Connecticut state page ([ftc.gov/data](https://www.ftc.gov/system/files/ftc_gov/pdf/csn-annual-data-book-2024.pdf)).*

Much of this starts with exposure that people can't see:
- Old accounts they forgot about.
- Services that were breached years ago.
- Passwords reused across sites, so one leak unlocks everything.

Security tools that show this are built for security professionals, not for a retiree, a busy parent, or a student.

## What it does

1. **Finds your accounts.** It checks about 230 sites to see whether your email is registered there. It uses each site's own sign-up and login checks, which **never send an alert or email to your accounts**.
2. **Matches breaches.** It compares your accounts against HaveIBeenPwned's catalog of 1,000+ known breaches. It's honest about certainty: "this service was breached" is a different claim from "your data was in it".
3. **Scores your exposure** from 0 to 100.
4. **AI advisor (Google Gemini).** It explains what your exposure means in real life and gives 3–5 prioritized steps, in plain language.
5. **Cleanup plan.** A checklist with direct password and account-deletion links and how hard each deletion is (from JustDeleteMe).

## How AI is used

The AI turns raw breach data (breach names, data classes, dates) into advice a non-technical person can act on: which accounts to secure first and why, in their own situation.

Guardrails:
- The model **never receives the email address**, only platform and breach facts.
- It's told to say "may have been exposed" unless a breach is confirmed for that specific email.
- It can't make up links: it names the account and the kind of action, and the app attaches the real URL.
- If the AI is unavailable, the app falls back to a rule-based plan, so the page never goes blank.

## Privacy

There's no database, no logs of scanned emails and no cookies. Everything is computed for each request and then discarded.

## Run it locally

```bash
cd backend
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
echo "GEMINI_API_KEY=your_key" > .env        # optional; without it, the rule-based plan is shown
uvicorn app:app --port 8000 --loop asyncio   # then open http://localhost:8000
```

Architecture notes, measurements and deployment (Azure VM, deployed automatically by GitHub Actions) are in [`CLAUDE.md`](CLAUDE.md).

## Credits

Built on [user-scanner](https://github.com/kaifcodec/user-scanner) (kaifcodec), [Holehe](https://github.com/megadose/holehe) (megadose), [HaveIBeenPwned](https://haveibeenpwned.com) (Troy Hunt), [JustDeleteMe](https://github.com/jdm-contrib/jdm) (jdm-contrib) and Google Gemini.

MIT License.
