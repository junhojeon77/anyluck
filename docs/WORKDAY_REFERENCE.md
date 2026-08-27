# How Workday Career Sites Expose Job Data

Reference notes for building a job-monitoring bot against Canada's Big Five banks.
Drop this file in your repo so Claude Code can read it while working.

---

## 1. The short version

Every Workday careers page is a JavaScript single-page app. The HTML you get from
a plain `GET` is an empty shell — which is why people reach for Selenium and
Playwright and conclude scraping Workday is hard.

It isn't. The SPA populates itself by calling a JSON API called **CXS**
(Candidate Experience Service). That API is public, unauthenticated, and returns
clean structured data. You call it directly and skip the browser entirely.

**A browser is a discovery tool here, not a runtime dependency.** Open devtools
once to find the endpoint, then never launch a browser again.

---

## 2. URL anatomy

A Workday careers URL looks like:

```
https://cibc.wd3.myworkdayjobs.com/en-US/search
        └┬─┘ └┬┘                    └─┬─┘ └─┬──┘
      tenant  dc                   locale   site
```

| Part | Meaning | Notes |
|---|---|---|
| `tenant` | Company slug | Usually the company name, but not reliably |
| `dc` | Data-centre shard (`wd1`, `wd3`, `wd5`, `wd12`...) | No rule — must be observed, never guessed |
| `locale` | e.g. `en-US` | Present in public URLs, **absent** from CXS paths |
| `site` | Career site slug | One tenant can host several (external, internal, campus) |

The corresponding API base is:

```
https://{tenant}.wd{N}.myworkdayjobs.com/wday/cxs/{tenant}/{site}
```

Note the tenant appears **twice** — once as subdomain, once as a path segment.

### Discovering the slug for a new site

1. Open the careers page.
2. F12 → Network tab → filter `jobs`.
3. Type anything into the site's search box.
4. Find the POST whose URL matches `/wday/cxs/.../jobs`.
5. Copy the URL and the request body.

This takes about fifteen seconds and is the only reliable method. There is no
public directory mapping companies to tenants.

---

## 3. The list endpoint

**`POST /wday/cxs/{tenant}/{site}/jobs`**

It is a POST with a JSON body. A GET returns nothing useful — this is the single
most common reason people give up on the API.

```bash
curl -X POST "https://cibc.wd3.myworkdayjobs.com/wday/cxs/cibc/search/jobs" \
  -H "Content-Type: application/json" \
  -d '{"appliedFacets": {}, "limit": 20, "offset": 0, "searchText": "software engineer"}'
```

### Request body

| Field | Type | Notes |
|---|---|---|
| `searchText` | string | Free-text query. `""` returns everything. |
| `limit` | int | **Hard-capped at 20.** Larger values return an empty array, not an error. |
| `offset` | int | Increment by 20. |
| `appliedFacets` | object | Server-side filters. `{}` = no filtering. See §5. |

### Response shape

```json
{
  "total": 143,
  "jobPostings": [
    {
      "title": "Senior Backend Engineer",
      "externalPath": "/job/Toronto-Ontario/Senior-Backend-Engineer_R-3164651",
      "locationsText": "2 Locations",
      "postedOn": "Posted 5 Days Ago",
      "bulletFields": ["R-3164651"],
      "remoteType": "Hybrid",
      "timeType": "Full time"
    }
  ],
  "facets": [ ... ]
}
```

Paginate until `offset >= total`, or until `jobPostings` comes back empty.

The public job URL is reassembled as:

```
https://{host}/en-US/{site}{externalPath}
```

---

## 4. The detail endpoint

**`GET /wday/cxs/{tenant}/{site}/job/{slug}`**

where `slug` is the last path segment of `externalPath`.

```json
{
  "jobPostingInfo": {
    "title": "Senior Backend Engineer",
    "jobReqId": "R-3164651",
    "jobPostingId": "Senior-Backend-Engineer_R-3164651",
    "jobDescription": "<p>...</p>",
    "startDate": "2026-07-01",
    "location": "Toronto, Ontario, Canada",
    "additionalLocations": ["Mississauga, Ontario, Canada"],
    "timeType": "Full time",
    "jobRequisitionLocation": { "country": { "alpha2Code": "CA" } }
  }
}
```

This is where the data you actually want lives. **Only call it for postings
you've decided are new** — one request per job adds up fast otherwise.

---

## 5. Server-side filtering with facets

The `facets` array in the list response describes the site's own filter sidebar
and contains the IDs you need:

```json
{
  "facets": [
    {
      "facetParameter": "locations",
      "descriptor": "Location",
      "values": [
        { "id": "6d1a...c3", "descriptor": "Toronto, Ontario, Canada", "count": 87 }
      ]
    }
  ]
}
```

Feed those IDs back in:

```json
{"appliedFacets": {"locations": ["6d1a...c3"]}, "limit": 20, "offset": 0, "searchText": ""}
```

**Facet IDs are opaque hashes, tenant-specific, and can change.** They aren't
portable across banks. For a small personal bot, client-side filtering on the
location string is more robust and costs nothing. Use facets only if a query is
returning so many results that pagination becomes expensive.

---

## 6. Gotchas

**`postedOn` is English prose, not a timestamp.** You get `"Posted Today"`,
`"Posted 5 Days Ago"`, `"Posted 30+ Days Ago"`. The `30+` case is a floor, not a
value — 31 days and 400 days look identical. Never build freshness logic on it.
Use `startDate` from the detail call, or better, see §8.

**`bulletFields[0]` is usually the requisition ID — but not always.** Some
tenants put a location string there; some omit `bulletFields` entirely. Don't use
it as a primary dedup key. Use the `externalPath` slug, which is always present
because it's required to address the job at all.

**`locationsText` is often just `"2 Locations"`** with no place name for
multi-site postings. If you filter on it naively you will silently drop every
multi-city job. Let those through and resolve them via the detail call's
`additionalLocations`.

**Location strings carry internal facility codes.** Expect things like
`"FTC03 - Ft. Collins, CO B-3 (FTC03)"`. Strip the leading code and trailing
parenthetical before displaying.

**A 422 may mean hyphen/underscore mismatch.** Tenant slugs containing
underscores can't appear in DNS, so the subdomain shows a hyphen — but the API's
path segment sometimes still wants the underscore. If you get a 422 and your
tenant has a hyphen, retry with an underscore.

**There's a 10,000-result hard cap per query.** Irrelevant at personal scale;
relevant if you ever try to enumerate a whole tenant.

**No salary data anywhere**, in either response. Workday's description templates
don't consistently include it as parseable text either.

---

## 7. Rate limiting and bot protection

Workday sits behind Cloudflare, and some tenants add Akamai bot management.
Aggressive enumeration from one IP gets blocked quickly. Personal-scale polling
does not.

The rule that actually matters:

> **Rate limiting appears to be per source IP across all tenants combined, not
> per tenant.** A 429 from CIBC is a signal about your total volume across RBC,
> TD, BMO and CIBC together.

So: back off **globally**, not per-board. Abort the whole cycle, not just the
board that complained. Honour `Retry-After` when present.

Practical settings that stay well clear of trouble:

- 1–2 seconds between any two requests
- Sequential, not concurrent
- Narrow `searchText` so each query is 1–3 pages, not 50
- Realistic `User-Agent`; `Content-Type: application/json` is required

If a response is HTML instead of JSON, you've hit a Cloudflare interstitial. The
fix is `curl_cffi` with `impersonate="chrome"`, which presents a genuine browser
TLS/HTTP2 fingerprint. This is rarely needed at low volume.

**Rough budget:** 4 boards × 3 queries × ~2 pages ≈ 25 requests per cycle. At one
cycle per 20 minutes that's ~75 requests/hour spread across four tenants. Not
close to any threshold.

---

## 8. Detecting genuinely new postings

This is the important design idea, and it removes the need for timestamps.

Workday can't tell you a posting went up 15 minutes ago — the coarsest it gets is
"Posted Today". But you don't need it to. If you:

1. poll every 20 minutes,
2. persist every job key you've ever seen,
3. treat anything absent from that set as new,

then **"new to the set" means "appeared within the last 20 minutes"**, by
construction. That's more precise than any timestamp the API offers.

Requirements for this to work:

- **Stable keys.** Use `{bank}:{externalPath slug}`. Slugs are stable across
  polls; array positions and `postedOn` strings are not.
- **Durable state.** Persist to disk, write atomically (temp file + rename) so a
  crash mid-write can't corrupt it. Losing state means re-alerting on everything.
- **A seed step.** On first run, record everything currently listed *without*
  emailing, or the first alert is hundreds of jobs.
- **TTL pruning.** Expire keys after ~30 days so the file doesn't grow forever.
  Longer than any realistic posting lifetime, so no false "new" on re-detection.

Trade-off worth knowing: a genuinely reposted requisition gets a new slug and
will alert again. For a job hunt that's usually desirable — a repost means the
role is still live.

---

## 9. The Big Five

Verify all of these before relying on them; career site slugs get renamed.

| Bank | Host | Tenant | Site | Notes |
|---|---|---|---|---|
| RBC | `rbc.wd3.myworkdayjobs.com` | `rbc` | `rbcglobal1` | Also runs `RBCEARLYTALENT1` for campus/new-grad |
| TD | `td.wd3.myworkdayjobs.com` | `td` | `TD_Bank_Careers` | |
| BMO | `bmo.wd3.myworkdayjobs.com` | `bmo` | `External` | |
| CIBC | `cibc.wd3.myworkdayjobs.com` | `cibc` | `search` | |
| Scotiabank | `jobs.scotiabank.com` | — | — | **Not Workday.** See below. |

Validate each with a cheap probe:

```bash
curl -s -X POST "https://{host}/wday/cxs/{tenant}/{site}/jobs" \
  -H "Content-Type: application/json" \
  -d '{"appliedFacets":{},"limit":1,"offset":0,"searchText":""}' | head -c 300
```

A `total` field means the slug is right. A 404 means it isn't.

### Scotiabank

Runs a different ATS at `jobs.scotiabank.com`. Find its endpoint the same way —
devtools, Network tab, search, look for the XHR returning JSON. Then write an
adapter that returns the same normalized dict shape as the Workday path, so the
rest of the pipeline is source-agnostic.

Fallback: the LinkedIn logged-out guest endpoint
(`/jobs-guest/jobs/api/seeMoreJobPostings/search`) covers Scotiabank scoped by
company. Lower fidelity, but no new integration work.

---

## 10. Why not Playwright

Worth stating explicitly, because it's the natural instinct:

| | CXS API | Playwright |
|---|---|---|
| Data | Structured JSON | HTML you must parse |
| Per cycle | ~25 HTTP requests | 4 browser launches, JS execution, waits |
| Memory | ~30 MB | ~400 MB+ |
| Breaks when | The API changes (rare) | Any CSS class changes (often) |
| Detectability | Looks like the site's own frontend | Headless fingerprint is a known signal |
| Runtime | Seconds | Minutes |

The browser is strictly worse as a runtime. Where it *is* useful: discovering an
undocumented endpoint on a new site (§2), and debugging a board that starts
returning HTML instead of JSON.

If you ever do need it, prefer Playwright over Selenium — better auto-waiting,
`launch_persistent_context()` for session reuse, cleaner async. But reach for
`requests` first, every time.
