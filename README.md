# anyluck

> *"Any luck with the job search?"*

Now you can answer that. It watches the careers boards of Canada's Big Five banks
for jobs matching your search terms, in a visible browser, every 4 hours, and
writes them to `jobs.md`, grouped by bank and ordered by how recently each one
appeared.

Then Claude Code reads that feed, pulls out keywords, and tells you which ones
actually fit your resume.

```
$ ./anyluck
3 new since this morning:
  CIBC  Software Engineer II - Android   just now
  RBC   Backend Developer, Payments      2h ago
  TD    Software Engineer II (SDET)       3h ago
```

## Quickstart

```bash
cd anyluck
./init
```

That's the whole install. `./init` finds a Python 3.11+, builds a venv, installs
Playwright and pytest, downloads Chromium, **launches it to confirm it actually
works**, runs the test suite, then walks you through dropping in your resume.
It stops at the first thing that fails and tells you how to fix it. Re-running
it is safe.

Then, in Claude Code:

```
/jobsetup
```

which asks for your search terms and locations, writes `config.toml`, and does
the first scrape. After that:

```bash
./anyluck --watch     # leave it running
```

and whenever you want the analysis:

```
/jobscan
```

## What comes out

`jobs.md`, rewritten every cycle:

```markdown
## CIBC

- **Senior Backend Engineer** — Toronto, ON · Hybrid  🆕
  first seen just now · site says "Posted Today"
  https://cibc.wd3.myworkdayjobs.com/en-US/search/job/...

- **Software Engineer, Payments** — 2 Locations · Full time
  first seen 8h ago · site says "Posted 5 Days Ago"
  https://cibc.wd3.myworkdayjobs.com/en-US/search/job/...

## RBC
...
```

`matches.md`, written by `/jobscan`: per-job keywords, whether it fits your
resume, and the specific gaps if it doesn't.

**These two files have different owners.** `jobs.md` is the bot's — it gets
overwritten every cycle, so don't hand-edit it. `matches.md` is Claude's.
Nothing has to merge.

## The three commands

| Command | What it does |
|---|---|
| `/jobsetup` | First-run config. Search terms, locations, seed run. (`./init` handles the resume.) |
| `/jobscan` | Reads `jobs.md` + `resume.md`, writes `matches.md`. |
| `/discover` | A board went quiet? This finds its new URL/selectors and patches `config.toml`. |

## How it knows a posting is new

Not from the site. Workday reports dates as English prose — `"Posted Today"`,
`"Posted 30+ Days Ago"` — and that last one is a floor, not a value: 31 days and
400 days look identical.

So the bot doesn't trust it. It remembers every job it has ever seen in
`seen.json`, and anything absent from that set is new. Poll every 4 hours and
"new to the set" means "appeared in the last 4 hours" by construction — more
precise than anything the site will tell you. The site's own prose still gets
printed next to it, as a weaker second opinion.

First run records everything **without** flagging it, or your first report would
be several hundred jobs.

## A note on the browser

This drives a real, visible Chromium at every board. 

The browser was chosen deliberately. It's uniform across all five banks
including Scotiabank, which isn't Workday at all; you can watch it work and see
exactly where it breaks; and against Cloudflare a real browser is genuinely
better than a scripted HTTP client. The cost is that it breaks when a site is
restyled. That's mitigated by anchoring on Workday's `data-automation-id`
attributes rather than CSS classes, and by `/discover` when it happens anyway.

## Docs

- [`docs/ONBOARDING.md`](docs/ONBOARDING.md) — full setup, and how to point this at boards that aren't banks
- [`docs/TROUBLESHOOTING.md`](docs/TROUBLESHOOTING.md) — when a board goes quiet
- [`docs/PLAN.md`](docs/PLAN.md) — the design and why