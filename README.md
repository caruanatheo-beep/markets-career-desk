# Markets Career Desk

A standalone Gradio dashboard for finding and tracking market-finance internships, VIEs and graduate roles. It aggregates public employer career pages and job feeds, supports personal application tracking, and includes an optional AI assistant for offer analysis and cover letters.

## Run locally

Requires Python 3.12 or newer.

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
Copy-Item .env.example .env
python app_internships.py
```

Open `http://127.0.0.1:7861`. The board works without an AI key; AI analysis and letter generation require `OPENAI_API_KEY` in `.env`. Never commit `.env`.

## Optional accounts

For a shared deployment, set `INTERNSHIPS_USERS_JSON` to a JSON object mapping usernames to strong passwords (at least 12 characters), and set `INTERNSHIPS_ADMIN_USER` to one of those usernames. Example format:

```text
{"INTERNSHIPS_USERS_JSON":"{\"admin\":\"replace-with-a-long-unique-password\",\"friend\":\"another-long-unique-password\"}","INTERNSHIPS_ADMIN_USER":"admin"}
```

Set these as host-provided secrets, not in committed files. Set `INTERNSHIPS_SIGNUP_CODE` if public self-registration should require an invitation code. Persistent hosting should mount durable storage and set `INTERNSHIPS_USER_DB` to a path on that volume; the shared accounts, profiles and application records are stored there.

## Data and privacy

Offer cache and user records are created under `internships_project/data/` at runtime and are excluded from Git. AI features send the selected profile information and relevant offer details to the configured OpenAI API. Review the in-app privacy page before enabling these features for other users.

## Sources

See [internships_project/SOURCES.md](internships_project/SOURCES.md) for the connected sources, collection methods and known limitations.

