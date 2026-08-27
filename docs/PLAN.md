# anyluck — implementation plan

## Context

The repo began as three reference documents — `WORKDAY_REFERENCE.md`,
`ATS_REFERENCE.md` and `Find_Job_notes.txt`. **The user deleted all three on
2026-08-27**, having taken what they needed from them. Every fact they carried
that this design depends on is inlined below, so nothing here points at a file
that no longer exists.

The goal is a job monitor for Canada's Big Five banks: poll every 4 hours in a
visible browser, keep a running Markdown file grouped by bank and ordered by how
recently each posting appeared, and let Claude Code enrich that feed with
keywords and a fit check against the user's resume. The repo should also ship
docs good enough that someone else can clone it and run it with their own Claude
Code.

**Decisions already made by the user:**

- **Playwright for every board, headed.** The (now deleted) Workday notes argued
  the CXS JSON API is strictly better as a runtime — structured JSON, ~25 HTTP
  requests, 30MB, seconds. The user read that and chose the browser anyway.
  Building it as asked. Two mitigations for the fragility: anchor on Workday's
  `data-automation-id` attributes rather
  than CSS classes (they're stable across tenants and survive restyles), and
  reuse one persistent browser profile so Cloudflare clearance carries between
  cycles. A headed real browser is genuinely *better* than `requests` against
  Cloudflare — that's the one place this choice pays for itself.
- **Foreground `--watch` loop**, not cron or systemd. Headed browsing needs a
  live display; a terminal the user starts already has one.
- **Claude runs as slash commands**, not an API call inside the bot. No API key,
  no per-cycle token cost, reasoning visible in console.

---

## Files

```
anyluck/
├── init                      # one-command setup + verify + resume gate
├── anyluck                   # wrapper: ./anyluck instead of .venv/bin/python
├── anyluck.py                # everything: scrape, dedupe, render, loop
├── pytest.ini                # puts the project root on sys.path for tests/
├── tests/
│   ├── test_anyluck.py       # pure-logic tests
│   ├── test_scrape.py        # scraper tests against saved DOM fixtures
│   └── fixtures/             # real results HTML, captured in Phase 1
├── config.toml               # user-editable: search terms, boards, locations
├── resume.md                 # template, user replaces
├── jobs.md                   # OUTPUT — bot-owned, rewritten each cycle
├── matches.md                # OUTPUT — Claude-owned, written by /jobscan
├── seen.json                 # state
├── .pw-profile/              # persistent browser profile (gitignored)
├── README.md
├── .claude/commands/
│   ├── jobscan.md
│   ├── discover.md
│   └── jobsetup.md
└── docs/
    ├── PLAN.md               # this document, verbatim — written first
    ├── ONBOARDING.md
    └── TROUBLESHOOTING.md
```

One module plus its tests. `anyluck.py` lands around 250 lines and doesn't need splitting —
the four Workday banks share a single scraper, so there are two scrapers total,
not five.

**Dependencies: `playwright` and `pytest`.** Config is TOML read with stdlib `tomllib`
(3.11+), so no PyYAML. Everything else is `json`, `pathlib`, `datetime`,
`argparse`, `time`.

**File ownership is a hard boundary.** `jobs.md` is rewritten from `seen.json`
every cycle — never hand-edit it, and `/jobscan` never touches it. Enrichment
goes to `matches.md`. This avoids any merge logic between the bot and Claude.

---

## Design

### Freshness — the part worth getting right

Workday reports dates as English prose — `"Posted Today"`, `"Posted 5 Days
Ago"`, `"Posted 30+ Days Ago"`. That last one is a **floor, not a value**: 31
days and 400 days render identically. Freshness logic must not be built on it.

The answer is that the seen-set *is* the timestamp.

So each job's key gets a `first_seen` ISO timestamp written the moment we first
observe it. "How long ago the posting was made" in `jobs.md` renders as
**first-seen age** (accurate to the poll interval), with the site's own prose
shown alongside as a weaker secondary signal:

```
- **Senior Backend Engineer** — Toronto, ON · Hybrid
  first seen 2h ago · site says "Posted Today"
  https://cibc.wd3.myworkdayjobs.com/en-US/search/job/...
```

This requires four things:

- **Stable keys** — `{bank}:{url slug}`. Never the requisition ID: some tenants
  put a location string there, some omit it entirely. The URL slug is always
  present, because it's required to address the job at all.
- **Durable, atomic state** — temp file + `os.replace`, so a crash mid-write
  can't corrupt it. Losing state means re-alerting on everything.
- **A seed run** — first launch records everything *without* flagging it, or the
  first report is several hundred jobs.
- **30-day TTL pruning** — longer than any realistic posting lifetime, so
  expiry never causes a false "new".

Known trade-off: a genuinely reposted requisition gets a new slug and alerts
again. For a job hunt that's desirable — a repost means the role is still live.

### Normalized record

Every scraper returns one shape, so rendering is source-agnostic:

```python
{
    "key":       str,   # f"{bank}:{slug}" - stable, unique
    "source":    str,   # "workday" | "scotia"
    "company":   str,
    "title":     str,
    "location":  str,   # cleaned; "2 Locations" preserved as-is
    "remote":    str,   # "" if unknown
    "url":       str,
    "req_id":    str,
    "posted_text": str, # the site's own prose, verbatim
    "published": datetime | None,   # only Scotiabank supplies a real one
    "first_seen": str,  # ISO, set by merge_seen on first sighting
}
```

### Scraping

One persistent context for the whole cycle:

```python
ctx = pw.chromium.launch_persistent_context(".pw-profile", headless=False)
```

- `scrape_workday(page, board, term)` — covers RBC, TD, BMO, CIBC. Navigate to
  `{url}?q={term}`, wait for `[data-automation-id="jobTitle"]`, read each
  `li:has(...)` row's `jobTitle` / `locations` / `postedOn` / `subtitle`, click
  `aria-label="next"` until it disables. Location is often just `"2 Locations"`
  — let those through rather than filtering them out, or every multi-city
  posting silently vanishes.
- `scrape_scotia(page, term)` — different ATS: `tr.data-row`, with
  `td.colTitle span.jobTitle.hidden-phone a.jobTitle-link` for title/href,
  `td.colLocation`, and `td.colDate` carrying a **real** date. Same return shape.

Between boards: sequential, 1–2s pauses. Workday rate-limits **per source IP
across all tenants combined**, not per tenant — so a 429 from CIBC is a signal
about total volume across RBC, TD, BMO and CIBC together, and a block aborts the
whole cycle rather than just that board.

Failure of one board logs and continues to the next; a cycle never dies because
one site changed.

### CLI

```
./anyluck               # one cycle
./anyluck --watch       # cycle, sleep 4h, repeat (interval from config)
./anyluck --board cibc  # one board, for debugging
./anyluck --render      # regenerate jobs.md from seen.json, no browser
./anyluck --capture cibc # re-save tests/fixtures/cibc_results.html
```

### config.toml

```toml
search_terms = ["software engineer", "backend developer"]
locations    = ["Toronto", "Ontario", "Remote"]   # substring match; [] = all
resume       = "resume.md"
hours_between_runs = 4

[[boards]]
name = "CIBC"
type = "workday"
url  = "https://cibc.wd3.myworkdayjobs.com/en-US/search"
```

All five board slugs were probed live in Phase 1 and are correct as written
above. They do get renamed over time; `/discover` is the recovery path.

### Slash commands

- **`/jobscan`** — reads `jobs.md` + `resume.md`, opens each new posting's
  description, and writes `matches.md`: per-job keywords, a fit call against the
  resume, and the specific gaps. **`resume.md` is now its only input** — the
  original spec also read `Find_Job_notes.txt` for team preferences, and that
  file is deleted. Anything it should still weigh (preferred teams, target
  companies) belongs in `resume.md` rather than a new config key.
- **`/discover`** — recovery tool for when a board returns zero jobs. Opens the
  careers page headed, finds the current selectors or corrected URL, patches
  `config.toml`. Automates the devtools procedure: open the page, Network tab,
  search, find the request that returns the jobs, read the slug off it.
- **`/jobsetup`** — first-run onboarding. Asks for search terms, locations, and
  resume, then writes `config.toml` and runs a seed cycle.

---

## Build order — test-driven throughout

**Framework:** `pytest` (add `pytest` alongside `playwright`; nothing else). Test
files `tests/test_anyluck.py` and `tests/test_scrape.py`.

**The cycle, enforced per behaviour:**

1. Write one test for one behaviour.
2. Run it. **Paste the real failure output** — a test that has never failed
   proves nothing.
3. Minimum code to green. Re-run, paste the pass.
4. Refactor with tests green, re-run.

**Review gate:** at the start of each phase I write that phase's full test list,
run it to show red, and **stop for sign-off before writing any implementation
code.** No implementation before an approved failing test.

If a test fails, the code is wrong until proven otherwise. No assertion gets
edited to match output, nothing gets skipped or `.only`'d, and a failing test
gets reported rather than removed.

### Phase 0 — write `docs/` and `README.md` first ✅ DONE

The docs are the deliverable that was asked for first, so they get written
before any code, not after it.

- **`docs/PLAN.md`** — this document, verbatim. The plan lives in the repo where
  collaborators and their own Claude Code sessions can read it.
- **`README.md`** (root) — what it is, 4-step quickstart, sample `jobs.md`
  output, and the Playwright-vs-API tradeoff stated plainly so a new user knows
  what they're running and why.
- **`init`** — one-command setup: finds Python 3.11+, builds `.venv`, installs
  deps, downloads Chromium, **launches it to verify**, runs the suite, then
  gates on the user filling in `resume.md` before pointing them at `/jobsetup`.
  Stops at the first failing stage with an actionable message; safe to re-run.
- **`docs/ONBOARDING.md`** — `./init` → `/jobsetup` → seed run → first `/jobscan`.
  Written for someone bringing their own Claude Code: what the three slash
  commands do, the `jobs.md` / `matches.md` ownership boundary, how to point it
  at boards that aren't the Big Five, and how to swap in their own resume.
- **`docs/TROUBLESHOOTING.md`** — board returns zero jobs → `/discover`;
  Cloudflare interstitial; no `$DISPLAY`; corrupted `seen.json`; everything
  flagged NEW at once → state was lost; slug renamed; tests failing after a site
  redesign → recapture fixtures.
- ~~Move `WORKDAY_REFERENCE.md` and `ATS_REFERENCE.md` into `docs/`.~~
  Superseded: the user deleted both on 2026-08-27 (commit `a309e46`).

### Phase 1 — ground truth and fixtures ✅ DONE

All five boards probed live and every slug confirmed. Five fixtures captured to
`tests/fixtures/`.

| Bank | Slug | Probe | Rows | Structure |
|---|---|---|---|---|
| RBC | `rbcglobal1` | 200 | 277 | Workday |
| TD | `TD_Bank_Careers` | 200 | 93 | Workday |
| BMO | `External` | 200 | 68 | Workday |
| CIBC | `search` | 200 | 17 | Workday |
| Scotiabank | — | 200 | 25 | `tr.data-row`, **real dates** |

**Two corrections to this plan's assumptions, discovered here:**

1. The row container is **`li:has([data-automation-id="jobTitle"])`**, not
   `jobItem`. `jobItem` does exist, but as `data-uxi-element-id` on the anchor.
   Per-row fields are `jobTitle` / `locations` / `postedOn` / `subtitle`.
2. Search must go through the URL as **`?q=<term>`**. Typing into the page's
   search box looks like it works and silently fails on RBC — it returned all
   1,517 jobs instead of 277. This is now a documented failure mode.

Scotiabank exposes a **real date** (`Aug 16, 2026`) in `td.colDate`, so its
records can carry a true `published` value that the Workday boards can't.

### Phase 2 — pure logic (`tests/test_anyluck.py`)

One behaviour per cycle, in this order:

| Behaviour under test | The assertion |
|---|---|
| `load_config` | missing `config.toml` raises with a message naming the file |
| `normalize_location` | `"FTC03 - Ft. Collins, CO B-3 (FTC03)"` → `"Ft. Collins, CO"` |
| `normalize_location` | `"2 Locations"` passes through unchanged, not dropped |
| `job_key` | derived from the URL slug; two postings sharing a requisition ID still get distinct keys |
| `merge_seen` | **seed run**: empty state + 5 jobs → 5 stored, **0 flagged new** |
| `merge_seen` | second run, same 5 jobs → 0 new, `first_seen` values unchanged |
| `merge_seen` | second run, 1 extra job → exactly that one flagged new |
| `merge_seen` | job vanishes from the board → stays in state, not resurrected as new later |
| `prune` | 31-day-old key dropped, 29-day-old kept |
| `humanize_age` | 90min → `"1h ago"` (floored, as every other UI does it); 0min → `"just now"`; 50h → `"2d ago"` |
| `render_markdown` | grouped under bank headings, newest first within each bank |
| `render_markdown` | new jobs carry the NEW marker, previously-seen ones don't |
| `render_markdown` | both ages present: first-seen age *and* the site's `posted_text` |
| `save_atomic` | temp file + rename; simulated crash mid-write leaves the original intact |
| `load_seen` | malformed JSON raises loudly rather than returning `{}` — silently empty state re-alerts the entire back catalogue |

The `merge_seen` block is the heart of the bot and gets the most coverage — the
entire freshness design rests on it.

#### Status: 37 tests written, all red, gate approved

`anyluck.py` is 11 stubs raising `NotImplementedError`. Two deviations from the
table above, both flagged and accepted:

- `humanize_age(90min)` → `"1h ago"`, floored. The spec above said `"2h"`; the
  spec was wrong and has been corrected, not the test.
- One extra test not in the table: **a job that disappears from a board and
  returns is not re-alerted.** Workday transiently drops rows from paginated
  results, and without this a flicker produces a false "new".

#### Implementation approach

Going green in three increments, running the suite after each so the count
moves visibly. Stdlib only — `re`, `json`, `os`, `tomllib`, `pathlib`,
`datetime`, `urllib.parse`.

**Increment A — string-level (12 tests):** `load_config` (tomllib, raise
`FileNotFoundError` naming the path), `job_key` (`urlsplit` → last path segment,
prefixed by bank), `filter_locations` (lowercased substring; multi-location
placeholders always kept), and `normalize_location`, which is the fiddly one —
four ordered regex passes:

```
drop label lines ("locations", "posted on", "remote type")   # from inner_text
strip trailing parenthetical      (FTC03)
strip leading facility code       ^[A-Z0-9]{3,}\s*-\s*
strip trailing building code      \s+[A-Z]{1,2}-\d+$
```

Ordered so `"FTC03 - Ft. Collins, CO B-3 (FTC03)"` → `"Ft. Collins, CO"` while
`"Toronto, Ontario, Canada"` and `"2 Locations"` pass through untouched. The
leading-code pattern requires all-caps-or-digits, so no real city name matches.

**Increment B — state and time (14 tests):** `merge_seen` treats **empty state
as a seed run** and returns no new keys — that single branch is what stops the
first run, or any run after state loss, from alerting on hundreds of jobs. It
refreshes each record's mutable fields but never rewrites `first_seen`. `prune`
compares `first_seen` against a `now - 30d` cutoff. `humanize_age` floors:
`<10m` → `just now`, then `m` / `h` / `d`.

**Increment C — render and IO (11 tests):** `render_markdown` groups by company,
sorts by `first_seen` descending, and prints both ages on one line —
`first seen 2h ago · site says "Posted 30+ Days Ago"`. `load_seen` converts a
`JSONDecodeError` into a `ValueError` naming the file and telling the user to
move it aside; returning `{}` there would silently re-alert the whole back
catalogue. `save_atomic` writes `<path>.tmp` then `os.replace`, unlinking the
temp file if the rename fails so a failed write leaves both the original intact
and no litter behind.

No test gets edited to fit the implementation. If one fails, the code is wrong
until proven otherwise.

#### Increment D — repair the dangling doc links

Commit `a309e46` deleted the two reference docs but left five links pointing at
them. Readers hit a 404 on the exact pages meant to unblock them:

| File | Line | Currently points at | Replace with |
|---|---|---|---|
| `docs/TROUBLESHOOTING.md` | 24 | `WORKDAY_REFERENCE.md` §2, devtools procedure | the inlined steps |
| `docs/TROUBLESHOOTING.md` | 28 | §9, the Big Five slug table | the verified table from Phase 1 |
| `docs/TROUBLESHOOTING.md` | 59 | §7, shared rate limit | the inlined rule |
| `docs/ONBOARDING.md` | 210 | `ATS_REFERENCE.md`, other ATS platforms | a short inline note |
| `docs/ONBOARDING.md` | 215 | §2, identifying an unknown site | the inlined steps |

Each is one or two sentences of inlined fact — the same content this plan
absorbed above. `README.md` is already clean; `a309e46` dropped its two dead
links but missed these five.

Then re-sync `docs/PLAN.md` from this file, since the two have drifted.

### Phase 3 — scrapers, against saved fixtures (`tests/test_scrape.py`)

Not a hole any more. Each test spins a real Playwright page, loads the Phase 1
fixture with `page.set_content(html)`, and asserts on parsed output —
deterministic, offline, fast.

- `scrape_workday` on `cibc_results.html` → N records in the normalized shape
- title, `posted_text`, location and URL each read correctly off one known row
- a multi-location row (`"2 Locations"`) is **returned**, not filtered away
- a row missing its requisition-ID field still yields a valid record
- the same function on `rbc_`/`td_`/`bmo_` fixtures — proves one scraper covers
  all four Workday banks
- `scrape_scotia` on `scotia_results.html` → same normalized shape
- an empty/interstitial page returns `[]` and logs, rather than throwing

Live pagination and search-box interaction are the residue that fixtures can't
cover; the smoke runs below verify those.

### Phase 4 — CLI and loop

- `--render` regenerates `jobs.md` from a fixture `seen.json` with no browser
  launched
- `--board cibc` restricts to one board (assert on the board list passed to the
  scrape stage)
- one board raising doesn't abort the cycle — the others still complete. The
  exception is a rate-limit, which **does** abort everything, since the limit is
  shared across all Workday tenants
- `--watch` computes the sleep from `hours_between_runs` (assert on the
  computed interval; don't actually sleep four hours)

### Phase 5 — slash commands

`/jobscan`, `/discover`, `/jobsetup`. Prompt files, not code — no unit tests.
Verified by running them (steps 7–8 below).

### Phase 6 — reconcile the docs

Phase 0's docs were written against the plan; the build will have contradicted
them somewhere. Re-read `README.md` and `docs/ONBOARDING.md` against the shipped
code and fix any command, flag, or filename that drifted. Docs that lie are
worse than no docs.

---

## Verification

**Phase 2 specifically:** `.venv/bin/python -m pytest -q` goes from 37 failed to
37 passed, in three visible steps. No test file is touched after the gate — I'll
show `git diff --stat tests/` is empty at the end to prove it. Then commit as
`feat: implement pure-logic core`.

Full-project verification, automated first, then the live residue the fixtures
can't reach:

1. `pytest -v` — every phase green, with counts shown.
2. `./anyluck --board cibc` — a real headed browser drives one board.
   `seen.json` gains records with `first_seen` set; **nothing** flagged NEW.
3. Same command again — still nothing NEW. Dedupe against a *live* board is the
   likeliest bug and the fixtures can't prove it.
4. `./anyluck` — full cycle, five boards. `jobs.md` grouped by bank, ages
   plausible, links resolve, multi-location jobs present.
5. `./anyluck --render` — byte-identical `jobs.md`, no browser.
6. `./anyluck --watch` — sleeps and re-runs; kill after cycle two starts.
7. `/jobscan` — `matches.md` appears with keywords and resume gaps; `jobs.md`
   byte-identical afterward (ownership boundary holds).
8. `/discover` against a deliberately broken URL in `config.toml` — recovers it.
9. Corrupt `seen.json` by hand, run — fails loudly with a clear message rather
   than silently re-alerting everything.

---

## Deliberately skipped

Email/desktop notifications, a database, concurrency across boards, salary
parsing (Workday exposes none), adapters for other ATS platforms, and any web
UI. `jobs.md` in a terminal is the interface.

Greenhouse, Lever and Ashby adapters remain the most likely next addition — all
three are public, unauthenticated, need no pagination, and carry **real publish
timestamps**, which Workday does not. The normalized record above is already
shaped to absorb them.
