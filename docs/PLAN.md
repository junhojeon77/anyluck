# anyluck — implementation plan

## Context

The repo today is three reference documents and no code: `WORKDAY_REFERENCE.md`
(how Workday's CXS API works), `ATS_REFERENCE.md` (Greenhouse/Lever/Ashby, plus
the normalization schema in §9), and `Find_Job_notes.txt` (the user's target
teams and companies).

The goal is a job monitor for Canada's Big Five banks: poll every 4 hours in a
visible browser, keep a running Markdown file grouped by bank and ordered by how
recently each posting appeared, and let Claude Code enrich that feed with
keywords and a fit check against the user's resume. The repo should also ship
docs good enough that someone else can clone it and run it with their own Claude
Code.

**Decisions already made by the user:**

- **Playwright for every board, headed.** `WORKDAY_REFERENCE.md` §10 argues the
  CXS JSON API is strictly better as a runtime. The user read that and chose the
  browser anyway. Building it as asked. Two mitigations for the fragility that
  section warns about: anchor on Workday's `data-automation-id` attributes rather
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
├── anyluck.py                    # everything: scrape, dedupe, render, loop
├── test_anyluck.py               # pure-logic tests
├── test_scrape.py            # scraper tests against saved DOM fixtures
├── tests/fixtures/           # real results HTML, captured in Phase 0
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
    ├── TROUBLESHOOTING.md
    ├── WORKDAY_REFERENCE.md  # moved from root
    └── ATS_REFERENCE.md      # moved from root
```

Two Python files. `anyluck.py` lands around 250 lines and doesn't need splitting —
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

`WORKDAY_REFERENCE.md` §6 is emphatic that `postedOn` is English prose
(`"Posted 30+ Days Ago"` is a floor, not a value) and that freshness logic must
not be built on it. §8 gives the answer: the seen-set *is* the timestamp.

So each job's key gets a `first_seen` ISO timestamp written the moment we first
observe it. "How long ago the posting was made" in `jobs.md` renders as
**first-seen age** (accurate to the poll interval), with the site's own prose
shown alongside as a weaker secondary signal:

```
- **Senior Backend Engineer** — Toronto, ON · Hybrid
  first seen 2h ago · site says "Posted Today"
  https://cibc.wd3.myworkdayjobs.com/en-US/search/job/...
```

Per §8, this requires: stable keys (`{bank}:{externalPath slug}`, never
`bulletFields[0]`), atomic state writes (temp + `os.replace`), a **seed run**
that records everything on first launch without flagging it NEW, and 30-day TTL
pruning.

### Normalized record

Reuse the schema already written in `ATS_REFERENCE.md` §9 verbatim — `key`,
`source`, `company`, `title`, `location`, `remote`, `published`, `url`,
`req_id`, `comp` — plus `first_seen` and `posted_text`. Both scrapers return
this shape so rendering is source-agnostic.

### Scraping

One persistent context for the whole cycle:

```python
ctx = pw.chromium.launch_persistent_context(".pw-profile", headless=False)
```

- `scrape_workday(page, board, term)` — covers RBC, TD, BMO, CIBC. Navigate,
  type into the search box, wait for `[data-automation-id="jobItem"]`, read
  `jobTitle` / `postedOn` / `locations` per row, click "next page" until it
  disables. Location is often just `"2 Locations"` (§6) — let those through
  rather than filtering them out.
- `scrape_scotia(page, term)` — Scotiabank is not Workday
  (`WORKDAY_REFERENCE.md` §9); different DOM, own selectors, same return shape.

Between boards: sequential, 1–2s pauses. §7's rule that Workday rate-limits **per
source IP across all tenants combined** still applies — a block from one bank
aborts the whole cycle, not just that board.

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

Board slugs come from `WORKDAY_REFERENCE.md` §9, which flags them as unverified
— the first task in Phase 1 is probing all five.

### Slash commands

- **`/jobscan`** — reads `jobs.md` + `resume.md`, opens each new posting's
  description, and writes `matches.md`: per-job keywords, a fit call against the
  resume, and the specific gaps. Uses `Find_Job_notes.txt` for the user's team
  preferences (Integrations/Partner API, Developer Platform, Identity/Auth,
  Fraud & Risk).
- **`/discover`** — recovery tool for when a board returns zero jobs. Opens the
  careers page headed, finds the current selectors or corrected URL, patches
  `config.toml`. This is `WORKDAY_REFERENCE.md` §2's devtools procedure,
  automated.
- **`/jobsetup`** — first-run onboarding. Asks for search terms, locations, and
  resume, then writes `config.toml` and runs a seed cycle.

---

## Build order — test-driven throughout

**Framework:** `pytest` (add `pytest` alongside `playwright`; nothing else). Test
files `test_anyluck.py` and `test_scrape.py`.

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

### Phase 0 — write `docs/` and `README.md` first

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
- Move `WORKDAY_REFERENCE.md` and `ATS_REFERENCE.md` into `docs/`.

### Phase 1 — ground truth and fixtures (no production code)

Probe all five boards; `WORKDAY_REFERENCE.md` §9 flags its slug table as
unverified, and a wrong slug invalidates everything downstream. Then load each
careers page headed and **save the rendered results HTML to
`tests/fixtures/{bank}_results.html`**.

Those fixtures are what makes Phase 3 testable — the scrapers get real DOM to
work against with no network. Report what's live before proceeding, and correct
the board table in `docs/` if any slug has moved.

### Phase 2 — pure logic (`test_anyluck.py`)

One behaviour per cycle, in this order:

| Behaviour under test | The assertion |
|---|---|
| `load_config` | missing `config.toml` raises with a message naming the file |
| `normalize_location` | `"FTC03 - Ft. Collins, CO B-3 (FTC03)"` → `"Ft. Collins, CO"` (§6) |
| `normalize_location` | `"2 Locations"` passes through unchanged, not dropped |
| `job_key` | derived from the `externalPath` slug; two postings with identical `bulletFields` still get distinct keys (§6) |
| `merge_seen` | **seed run**: empty state + 5 jobs → 5 stored, **0 flagged new** |
| `merge_seen` | second run, same 5 jobs → 0 new, `first_seen` values unchanged |
| `merge_seen` | second run, 1 extra job → exactly that one flagged new |
| `merge_seen` | job vanishes from the board → stays in state, not resurrected as new later |
| `prune` | 31-day-old key dropped, 29-day-old kept |
| `humanize_age` | 90min → `"2h ago"`; 0min → `"just now"`; 50h → `"2d ago"` |
| `render_markdown` | grouped under bank headings, newest first within each bank |
| `render_markdown` | new jobs carry the NEW marker, previously-seen ones don't |
| `render_markdown` | both ages present: first-seen age *and* the site's `posted_text` |
| `save_atomic` | temp file + rename; simulated crash mid-write leaves the original intact (§8) |
| `load_seen` | malformed JSON raises loudly rather than returning `{}` — silently empty state re-alerts the entire back catalogue |

The `merge_seen` block is the heart of the bot and gets the most coverage; §8's
whole design rests on it.

### Phase 3 — scrapers, against saved fixtures (`test_scrape.py`)

Not a hole any more. Each test spins a real Playwright page, loads the Phase 1
fixture with `page.set_content(html)`, and asserts on parsed output —
deterministic, offline, fast.

- `scrape_workday` on `cibc_results.html` → N records with the normalized
  `ATS_REFERENCE.md` §9 shape
- title, `posted_text`, location and URL each read correctly off one known row
- a multi-location row (`"2 Locations"`) is **returned**, not filtered away (§6)
- a row missing `bulletFields` still yields a valid record
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
- one board raising doesn't abort the cycle — the others still complete (§7's
  global-backoff rule is the exception: a rate-limit **does** abort everything)
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

Automated first, then the live residue the fixtures can't reach.

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
parsing (§6: Workday has none), the Greenhouse/Lever/Ashby adapters from
`ATS_REFERENCE.md`, and any web UI. `jobs.md` in a terminal is the interface.
The ATS adapters are the most likely next addition — the normalized schema is
already designed for them.
