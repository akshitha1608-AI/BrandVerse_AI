# BrandVerse AI

**The Right Creator. The Right Brand. The Right Impact.**

BrandVerse AI is a functional local hackathon MVP for creator-brand discovery, campaign publishing, explainable recommendations, and creator applications. It uses a Flask server-rendered interface, a REST API, SQLAlchemy, and SQLite by default; a MySQL URL can be configured for deployment.

> This workspace was empty when this version was created. This rebuild prioritizes working end-to-end flows. The requested React/Vite + Tailwind client, React Router, Recharts charts, MySQL integration test, agency-specific profile table, collaboration workspace/messaging, notifications, and extended campaign lifecycle are not implemented in this MVP. No paid AI provider is used. The development rate limiter uses in-memory storage and should be replaced with shared storage for multi-worker production deployment.

## Features that work

- Registration, login, logout, role selection, securely hashed passwords, web sessions, and revocable JWT bearer authentication for protected API endpoints.
- Role-based creator and brand/agency dashboards with real database counts and useful empty states.
- Editable creator and company profiles.
- Creator and campaign discovery with name/category filters.
- Brand campaign publishing and draft saving.
- Creator applications with duplicate prevention and brand-side status updates.
- Saved creators and campaigns.
- Basic database-backed creator and brand application analytics.
- Authentication endpoint rate limits and CSRF protection for server-rendered forms.
- Brand-to-creator ranked matches with score components and explanations.
- Creator-to-campaign recommendations with actual profile/campaign data.
- Rule-based brief generator that produces an editable draft.
- PDF text extraction with file type, size, and scanned-document checks. Extracted text is shown for review and does not overwrite profile fields.
- Creator Portfolio & Creative Showcase Studio: upload and manage image/video projects, publish drafts, feature case studies, and build public creator portfolios.
- Project discovery with creator, project type, category, and popularity feeds; public/unlisted/private visibility; likes, saved work, sharing, and project analytics.
- Portfolio media is stored locally under `UPLOAD_FOLDER/portfolio/`, validated by extension and file signature, and delivered through visibility-checked routes. The default request limit is 100 MB; individual image and video limits are 12 MB and 80 MB. Configure durable storage and backups before production use.
- Twelve fictional creator accounts, four fictional brands, and ten fictional campaigns for demonstration.
- REST endpoints are documented in [API.md](./API.md).
- The application structure, request flow, security boundaries, and data model are described in [ARCHITECTURE.md](./ARCHITECTURE.md).

The sample follower and engagement figures are fictional profile data and are explicitly marked as demonstration data. The site does not claim to measure campaign performance.

## Requirements

- Python 3.10+
- Node.js is not required to run this Flask MVP. It was unavailable in the development environment, so no React/Vite frontend is included.
- MySQL is optional; SQLite is the default and needs no separate service. Authentication attempts are limited in the development process memory; use shared rate-limit storage (e.g. Redis) if running multiple production workers.

## Windows PowerShell setup

```powershell
cd C:\path\to\content_creator
py -m venv .venv
.\.venv\Scripts\Activate.ps1
py -m pip install -r requirements.txt
Copy-Item .env.example .env
```

For local use, the default SQLite database is created at `instance/marketplace.db`. Set secure, unique values in `.env` for `SECRET_KEY` and `JWT_SECRET_KEY` before using a shared or deployed environment. The app reads environment variables; for PowerShell in the current terminal:

```powershell
$env:SECRET_KEY = "replace-with-a-long-random-secret"
$env:JWT_SECRET_KEY = "replace-with-another-long-random-secret"
```

Seed fictional demo records and start the app:

```powershell
$env:FLASK_APP = "app.py"
py -m flask seed-demo
py app.py
```

Open <http://127.0.0.1:5000>.

To share creator work, sign in as a creator and open **My Portfolio**. New projects can be saved as drafts or published publicly, privately, or as unlisted links. Project uploads remain on the application server; do not use local disk storage for irreplaceable production media without backups and a persistence plan.

### MySQL configuration (optional)

Install and configure a MySQL server, create an empty database, then set:

```powershell
$env:DATABASE_URL = "mysql+pymysql://username:password@localhost:3306/creatorbridge"
$env:FLASK_APP = "app.py"
py -m flask seed-demo
py app.py
```

The SQLAlchemy models create the current MVP tables on startup. [database/schema.sql](./database/schema.sql) is a corresponding MySQL-oriented reference schema. Do not commit credentials or `.env`.

### Reset local demo data

Stop the server, then remove only the local database file and seed it again:

```powershell
Remove-Item .\instance\marketplace.db
$env:FLASK_APP = "app.py"
py -m flask seed-demo
```

This deletes local accounts and test records in that file. Back it up first if you have data to keep.

## Demo accounts

All seeded accounts use the password `DemoPass123!`:

- Creator: `aarohi@demo.creatorbridge.test`
- Creator: `rayan@demo.creatorbridge.test`
- Brand: `novafit@demo.creatorbridge.test`
- Brand: `pixelnest@demo.creatorbridge.test`
- Additional demo users follow the same naming pattern shown by their profiles.

These are local demo credentials only. Register a new account to test other roles or use the seeded database only on a trusted local machine.

## Tests

```powershell
py -m unittest discover -s tests -v
```

The tests exercise scoring bounds, missing match data, budget compatibility, availability ranking, hashed passwords, registration validation, role checks, campaign creation/ownership, duplicate applications, and private API authentication.

## API quick start

Authenticate to receive a short-lived JWT, then pass it as a bearer token:

```powershell
$body = @{ email = "novafit@demo.creatorbridge.test"; password = "DemoPass123!" } | ConvertTo-Json
$auth = Invoke-RestMethod -Method Post -Uri http://127.0.0.1:5000/api/auth/login -ContentType "application/json" -Body $body
$headers = @{ Authorization = "Bearer $($auth.access_token)" }
Invoke-RestMethod -Uri http://127.0.0.1:5000/api/campaigns -Headers $headers
```

For endpoint shapes and role requirements, see [API.md](./API.md).
