# Public Job APIs: Greenhouse, Lever, Ashby

Reference notes for extending the job monitor beyond Workday to the ATS platforms
that tech companies actually use. Companion to `WORKDAY_REFERENCE.md`.

---

## 1. Why this matters

Workday forced an awkward architecture. Its list endpoint is a POST, paginates in
hard-capped pages of 20, needs a second request per job for real data, and — the
painful part — reports dates as English prose like `"Posted 5 Days Ago"`. The
seen-set design exists specifically to work around that.

Greenhouse, Lever, and Ashby are all easier **and** give you real timestamps:

| ATS | Endpoint | Auth | Pagination | Publish date field |
|---|---|---|---|---|
| Greenhouse | `boards-api.greenhouse.io/v1/boards/{token}/jobs?content=true` | none | none — one response | `first_published` (ISO 8601) |
| Lever | `api.lever.co/v0/postings/{company}?mode=json` | none | none by default | `createdAt` (epoch **ms**) |
| Ashby | `api.ashbyhq.com/posting-api/job-board/{board}?includeCompensation=true` | none | none | `publishedAt` (ISO 8601) |

All three are public, unauthenticated, documented, and intended to be read —
syndication to job boards is the whole point of them existing.

Try them right now:

```bash
curl "https://boards-api.greenhouse.io/v1/boards/stripe/jobs"
curl "https://api.lever.co/v0/postings/palantir?mode=json"
curl "https://api.ashbyhq.com/posting-api/job-board/openai"
```

---

## 2. Greenhouse

**`GET https://boards-api.greenhouse.io/v1/boards/{board_token}/jobs`**

Add `?content=true` for full descriptions, department, and office per job.
Single job: `GET .../jobs/{job_id}`.

```json
{
  "jobs": [
    {
      "id": 127817,
      "internal_job_id": 144381,
      "title": "Product Security Engineer",
      "first_published": "2026-08-01T20:00:00Z",
      "updated_at": "2026-08-14T10:55:28-05:00",
      "requisition_id": "50",
      "location": { "name": "Toronto, ON" },
      "absolute_url": "https://boards.greenhouse.io/example/jobs/127817",
      "metadata": null,
      "content": "This is the job description."
    }
  ]
}
```

### The `updated_at` trap

`updated_at` changes when **anything** about the post is edited — a typo fix, a
department reassignment, a salary band tweak. Use it as a publish date and you'll
get alerted about six-month-old jobs every time a recruiter touches them.

**Use `first_published`.** It is the actual publish timestamp.

Guard for its absence anyway — older or unusually configured boards sometimes
omit it. Fall back to the seen-set rather than to `updated_at`.

### Other notes

- `location.name` is free text with no structure. Some boards put only a work
  mode there (`"Remote"`), some put a city, some put several comma-joined.
- `content` is HTML **and** HTML-escaped — you'll need to unescape before parsing.
- `metadata` holds employer-defined custom fields. Inconsistent across boards;
  never depend on a specific key.
- Board token is the path segment at `boards.greenhouse.io/{token}`, usually the
  company name lowercased.
- No published rate limit, but aggressive hammering can get you blocked. Poll on
  a schedule, not per page view.

---

## 3. Lever

**`GET https://api.lever.co/v0/postings/{company}?mode=json`**

The `mode=json` parameter matters — without it you may get HTML, since Lever
switches on the `Accept` header and `?mode=` takes precedence.

```json
[
  {
    "id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
    "text": "Security Engineer, Infrastructure",
    "hostedUrl": "https://jobs.lever.co/example-co/a1b2c3d4-...",
    "applyUrl": "https://jobs.lever.co/example-co/a1b2c3d4-.../apply",
    "categories": {
      "team": "Security",
      "location": "Toronto",
      "commitment": "Full-time",
      "allLocations": ["Toronto", "Remote - Canada"]
    },
    "createdAt": 1740000000000,
    "workplaceType": "hybrid",
    "descriptionPlain": "..."
  }
]
```

Note the response is a **bare array**, not an object with a `jobs` key. Every
platform here differs on this and it's a common source of adapter bugs.

### Quirks

- **`createdAt` is epoch milliseconds, not ISO.** Divide by 1000 before handing
  it to `datetime.fromtimestamp()`. Passing it raw yields a date around the year
  57,000, which is a memorable but avoidable bug.
- **Unknown company slugs return a clean HTTP 404.** Genuinely useful — it makes
  slug validation trivial and reliable. Lean on this.
- Server-side filters: `team`, `location`, `commitment`, `level`, `skip`,
  `limit`. **Location and team filters need exact string matches** as Lever
  stores them — `"New York, NY"` works, `"New York"` returns nothing. Safer to
  fetch everything and filter client-side.
- `categories.allLocations` is the multi-location field; `categories.location` is
  just the primary. Read both.
- Lever publishes no customer list, so slug discovery is manual.

---

## 4. Ashby

**`GET https://api.ashbyhq.com/posting-api/job-board/{board}`**

Add `?includeCompensation=true`.

```json
{
  "jobs": [
    {
      "id": "...",
      "title": "Product Security Engineer",
      "location": "Toronto, Ontario, Canada",
      "secondaryLocations": [...],
      "department": "Engineering",
      "team": "Security",
      "employmentType": "FullTime",
      "publishedAt": "2026-08-19T14:22:00Z",
      "isListed": true,
      "jobUrl": "https://jobs.ashbyhq.com/example/...",
      "applyUrl": "...",
      "compensation": { ... }
    }
  ]
}
```

### Notes

- **Best salary data of the three.** `?includeCompensation=true` returns
  structured ranges — min, max, currency, pay interval — on most postings.
  Greenhouse rarely has it; Lever sometimes; Ashby usually.
- **Filter on `isListed`.** Unlisted postings exist in the response but aren't
  meant to be public-facing.
- Common among well-funded startups — OpenAI, Linear, Ramp, Notion all use it.
- `publishedAt` is clean ISO 8601, the most straightforward date field of the three.

---

## 5. Finding a company's board

There's no directory. Two approaches, use the first:

### Brute-force discovery (recommended)

All three 404 cleanly on unknown slugs, so just try the company name against each:

```bash
COMPANY=wealthsimple
curl -s -o /dev/null -w "greenhouse %{http_code}\n" \
  "https://boards-api.greenhouse.io/v1/boards/$COMPANY/jobs"
curl -s -o /dev/null -w "lever      %{http_code}\n" \
  "https://api.lever.co/v0/postings/$COMPANY?mode=json"
curl -s -o /dev/null -w "ashby      %{http_code}\n" \
  "https://api.ashbyhq.com/posting-api/job-board/$COMPANY"
```

Whichever returns 200 is the answer. Three requests, no guessing. Automate this.

### Manual fallback

Open the careers page and look at where an "Apply" link points:

| URL pattern | ATS | Slug is |
|---|---|---|
| `boards.greenhouse.io/{x}` or `job-boards.greenhouse.io/{x}` | Greenhouse | `{x}` |
| `jobs.lever.co/{x}` | Lever | `{x}` |
| `jobs.ashbyhq.com/{x}` | Ashby | `{x}` |
| `{x}.wd{N}.myworkdayjobs.com` | Workday | see `WORKDAY_REFERENCE.md` |

Slugs sometimes differ from the company name (hyphenation, legal entity name,
old branding). If the obvious guess 404s, check the careers page directly.

---

## 6. Target companies

Toronto-cluster employers worth monitoring for security/platform roles. **The ATS
column is unverified** — run discovery rather than trusting this table.

| Company | Notes |
|---|---|
| Wealthsimple | Fintech, remote-first. Has run "Security Engineer (Application & Cloud)" and "Software Engineer, Security Tooling" |
| 1Password | Security company, TypeScript-heavy, remote-first |
| Shopify | Large security org, Ottawa HQ with Toronto presence |
| Ada | Toronto, remote-first |
| KOHO | Toronto fintech, remote-first |
| Clio | Canadian, remote-first |
| Faire, Float, Vena, Cohere | Smaller Toronto tech, worth adding |

Security vendors are also worth including even when not Toronto-based — remote
Canadian roles are common, and security-product companies value the exact
security-plus-TypeScript profile that banks under-use.

---

## 7. Freshness: now you have a choice

Workday's lack of timestamps forced a pure seen-set design. All three of these
platforms give you a real publish time, so you can do both.

**Recommendation: keep the seen-set as primary, add timestamp as a secondary filter.**

The seen-set is the more robust signal because it doesn't care about clock skew,
timezone handling, or a board that omits the date field. The timestamp adds two
things worth having:

1. **Display.** "Posted 12 minutes ago" in the alert email, computed accurately.
2. **A cold-start guard.** If state is lost or corrupted, a `max_age_hours`
   filter prevents re-alerting on the entire back catalogue.

Do **not** switch to timestamp-only. A board that omits `first_published`, or a
posting republished with an old date, silently disappears from your alerts — and
a filter that fails closed on a job hunt is the expensive direction to fail.

---

## 8. Rate limits and etiquette

Far more permissive than Workday. These feeds are designed for syndication, and
none of the three publishes a hard limit. Realistically:

- One request per company per cycle. No pagination means no request multiplier —
  a 20-company watchlist is 20 requests per cycle, and at one cycle per 20
  minutes that's 60 requests/hour total.
- Still: 1 second between requests, sequential, realistic `User-Agent`.
- Back off on 429 and honour `Retry-After`.
- These are separate services from each other and from Workday, so unlike the
  Workday cross-tenant situation, a 429 from Greenhouse says nothing about Lever.
  Back off **per platform**, not globally.

That last asymmetry is worth encoding deliberately, since it's the opposite of
the Workday rule.

---

## 9. Normalization

Four sources, four shapes. Normalize on read so nothing downstream knows or cares
where a posting came from:

```python
{
    "key":        str,   # f"{source}:{company}:{native_id}" - stable, unique
    "source":     str,   # "greenhouse" | "lever" | "ashby" | "workday"
    "company":    str,
    "title":      str,
    "location":   str,   # joined, cleaned
    "remote":     str,   # workplaceType / remoteType, "" if unknown
    "published":  datetime | None,   # tz-aware UTC
    "url":        str,
    "req_id":     str,
    "comp":       str,   # "" when unavailable
}
```

Mapping table:

| Field | Greenhouse | Lever | Ashby | Workday |
|---|---|---|---|---|
| id | `id` | `id` | `id` | `externalPath` slug |
| title | `title` | `text` | `title` | `title` |
| location | `location.name` | `categories.location` + `allLocations` | `location` + `secondaryLocations` | detail `location` + `additionalLocations` |
| published | `first_published` | `createdAt` ÷ 1000 | `publishedAt` | detail `startDate` (day only) |
| url | `absolute_url` | `hostedUrl` | `jobUrl` | host + site + `externalPath` |
| req id | `requisition_id` | — | — | `jobReqId` |
| response root | `{"jobs": [...]}` | bare array | `{"jobs": [...]}` | `{"jobPostings": [...]}` |

The response-root column is the one that bites. Lever's bare array breaks any
adapter that assumes a dict wrapper.
