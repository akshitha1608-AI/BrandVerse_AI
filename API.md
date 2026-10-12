# CreatorBridge AI API

Base URL: `http://127.0.0.1:5000`. JSON endpoints use `application/json`. Authenticated endpoints require `Authorization: Bearer <access_token>`. Access tokens expire after one hour. Error responses have an `error` string. Role and resource ownership are validated by the backend.

## Authentication

- `POST /api/auth/register` — JSON `{ "name", "email", "password", "role" }`; role is `creator`, `brand`, or `agency`. Password minimum is 8 characters.
- `POST /api/auth/login` — JSON `{ "email", "password" }`; returns `access_token` and public user details.
- `POST /api/auth/logout` — authenticated; revokes the current access token. Tokens expire after one hour.
- `GET /api/auth/me` — authenticated; returns current public user details.

## Marketplace

- `GET /api/overview` — public demonstration counts.
- `GET /api/creators?q=&category=&page=1&per_page=20` — public paginated profile search, max 100 per page.
- `POST|PUT /api/creators/profile` — creator JWT; update own profile fields.
- `POST /api/creators/portfolio` — creator JWT; parse PDF text and return keyword-based category/skill/format suggestions for review (5 MB limit); does not update profile fields.
- `GET /api/brands/<id>` — public company profile (no private account email).
- `POST|PUT /api/brands/profile` — brand/agency JWT; update own company profile.
- `GET /api/campaigns?category=&page=1&per_page=20` — published campaign listings, max 100 per page.
- `GET /api/campaigns/<id>` — published campaign detail, or an owner's own draft.
- `POST /api/campaigns` — brand/agency JWT; create campaign.
- `PUT /api/campaigns/<id>` — owning brand/agency updates campaign fields/status.
- `DELETE /api/campaigns/<id>` — owning brand/agency may delete only an unapplied draft.
- `GET /api/campaigns/<id>/recommendations` — owner brand/agency JWT; returns ranked eligible profiles with `match.score`, component scores, explanation, and gaps.
- `POST /api/campaigns/<id>/apply` — creator JWT; duplicate application returns 409.
- `GET /api/applications/my` — creator gets their applications; brand/agency gets applications for their own campaigns.
- `DELETE /api/applications/<id>` — owning creator withdraws a pending or shortlisted application.
- `PUT /api/applications/<id>/status` — owning brand/agency; accepts Pending, Shortlisted, Accepted, or Rejected.
- `GET /api/recommendations/campaigns` — creator JWT; personalized published campaign recommendations.
- `POST|DELETE /api/creators/<id>/save` — brand/agency saves or removes a creator.
- `POST /api/campaigns/<id>/save` — creator saves a campaign.
- `DELETE /api/campaigns/<id>/save` — creator removes a saved campaign.
- `POST /api/brief/generate` — brand/agency JWT; returns a locally generated editable draft (not an LLM result).
- `GET /api/analytics/creator` — creator application totals by status.
- `GET /api/analytics/brand` — brand campaign and application totals by status.

Protected API routes return 401 for a missing, invalid, or expired token and 403 for role or ownership violations.
