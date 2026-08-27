# Onboarding

For someone who has cloned this and wants it watching their own job search, with
their own Claude Code. Should take about ten minutes, most of it waiting for
Chromium to download.

---

## 1. Run `./init`

Requires **Python 3.11+** (the config loader uses stdlib `tomllib`) and Claude
Code. Everything else the script handles.

```bash
git clone <your-fork> WorkdayBot
cd WorkdayBot
./init
```

It works through five stages and **stops at the first one that fails**, with a
message saying what to do about it:

| Stage | What it does |
|---|---|
| 1 Python | Finds a 3.11+ interpreter, or tells you none is installed |
| 2 Dependencies | Creates `.venv`, installs `playwright` and `pytest` |
| 3 Browser | Downloads Chromium (~150MB, once) |
| 4 Verify | **Actually launches Chromium**, checks for a desktop session, runs the test suite |
| 5 Resume | Writes a `resume.md` template and waits while you fill it in |

Stage 4 is the one worth having. It doesn't assume the install worked — it opens
a real browser. The usual Linux failure is Chromium installing fine and then
refusing to start because a system library is missing, and this catches that
immediately with the package names to install, rather than at your first scrape.

Re-running `./init` is safe. It skips anything already done, so it's also the
right thing to run when something breaks later.

### If a stage fails

The script tells you what to do. The two common ones:

- **Chromium won't launch** — missing system libraries. On Arch/Manjaro:
  `sudo pacman -S nss atk at-spi2-atk libcups libdrm libxkbcommon`. Elsewhere:
  `sudo .venv/bin/playwright install-deps chromium`.
- **No `$DISPLAY`** — a warning, not a failure. The bot shows the browser on
  purpose, which needs a desktop session. See [TROUBLESHOOTING.md](TROUBLESHOOTING.md).

---

## 2. Your resume

Stage 5 writes a `resume.md` template and waits. Open it, replace it with yours
in Markdown, save, then press **1** and Enter.

No particular structure is needed — `/jobscan` reads it as prose:

```markdown
# Your Name

## Skills
Python, TypeScript, PostgreSQL, Docker, ...

## Experience
### Backend Developer, Somewhere (2023-now)
- Built and maintained a payments integration handling ...
```

Specificity is what makes the matching useful. "Familiar with AWS" produces
vague results; "built CI pipelines in GitHub Actions, deployed to ECS" produces
specific ones.

Don't want resume matching? Press **s** to skip. Everything else works without
it; only `/jobscan` needs it.

---

## 3. Configure

In Claude Code:

```
/jobsetup
```

It asks what you're looking for and writes `config.toml`. Or write it yourself:

```toml
# Every term is searched against every board. More terms = more browsing time.
search_terms = ["software engineer", "backend developer"]

# Keep only postings whose location contains one of these (case-insensitive).
# Empty list keeps everything.
locations = ["Toronto", "Ontario", "Remote"]

# Used by /jobscan. Ignored if the file is missing.
resume = "resume.md"

hours_between_runs = 4

[[boards]]
name = "CIBC"
type = "workday"
url  = "https://cibc.wd3.myworkdayjobs.com/en-US/search"
```

**`search_terms` is the knob that matters.** It's a free-text search against each
bank's own search box, so it behaves like typing into that box. Two or three
focused terms beat one broad one — `""` returns the bank's entire posting list
and turns a 3-minute cycle into a 30-minute one.

**`locations` filters after scraping, not during.** Substring match, so
`"Ontario"` catches `"Toronto, Ontario, Canada"`. Multi-location postings often
render as just `"2 Locations"` with no place name — those are deliberately let
through rather than dropped, since silently discarding every multi-city job is a
worse failure than showing a few irrelevant ones.

---

## 4. Seed run

```bash
.venv/bin/python bot.py
```

A browser window opens and works through each board. Let it finish — closing the
window mid-run leaves `seen.json` incomplete.

**This first run flags nothing as new**, by design. It's recording the current
state of the world. If it flagged everything it found, your first report would
be hundreds of jobs and useless. Every run after this one reports only the
difference.

Check `jobs.md`. Wrong jobs? Adjust `search_terms` and re-run. Zero jobs from a
board? See [TROUBLESHOOTING.md](TROUBLESHOOTING.md).

---

## 5. Leave it running

```bash
.venv/bin/python bot.py --watch
```

Scrapes, sleeps 4 hours, repeats. Ctrl-C to stop.

It runs in the foreground on purpose. A visible browser needs a live desktop
session, and a terminal you started already has one — a cron job doesn't, which
is why cron and headed browsing is a bad combination on Linux. Leave it in a tab.

Stopping it loses nothing. `seen.json` is on disk; restarting picks up where it
left off, and jobs found while it was down get reported on the next cycle.

---

## 6. Analysis

Whenever you want it, in Claude Code:

```
/jobscan
```

Reads `jobs.md` and `resume.md`, opens the postings, writes `matches.md`:
keywords per job, whether it fits, and what's missing if it doesn't.

Run it as often or as rarely as you like — it reads the current `jobs.md`, so
running it once a day covers the last six cycles at once.

---

## File ownership

The one rule worth internalising:

| File | Owner | Meaning |
|---|---|---|
| `jobs.md` | the bot | Rewritten every cycle. Hand-edits vanish. |
| `matches.md` | `/jobscan` | Claude's analysis. The bot never touches it. |
| `seen.json` | the bot | State. Don't edit. Deleting it re-seeds. |
| `config.toml` | you | Yours. `/jobsetup` and `/discover` also write here. |
| `resume.md` | you | Yours. Read-only to everything else. |

Two output files instead of one because the bot overwrites `jobs.md` on a
schedule. Putting Claude's analysis in there too would mean it disappeared every
four hours, or that something had to merge them. Separate owners, no merge.

---

## Pointing this somewhere else

Nothing here is bank-specific. `type = "workday"` handles **any** Workday site,
which is most large employers.

Add one:

1. Find the careers page. If the URL looks like
   `something.wd3.myworkdayjobs.com`, it's Workday.
2. Add a board block:

```toml
[[boards]]
name = "Shopify"
type = "workday"
url  = "https://shopify.wd3.myworkdayjobs.com/en-US/External"
```

3. Run `.venv/bin/python bot.py --board shopify` and see if jobs come back.

Not Workday? Check `docs/ATS_REFERENCE.md` — Greenhouse, Lever, and Ashby all
have clean public JSON APIs that are far easier to read than any HTML, and §9
there has the normalized shape any new adapter should return. Most Toronto tech
companies are on one of those three rather than Workday.

`docs/WORKDAY_REFERENCE.md` §2 covers identifying an unknown site by hand.

---

## Working on the code

Tests first, always. The scraper tests run against saved HTML in
`tests/fixtures/`, so `pytest` needs no network and no browser download beyond
the initial one.

If a bank redesigns its site, the fixtures go stale and the tests keep passing
while the real scraper fails. That's the known limitation of fixture-based
scraper tests. When a board goes quiet, recapture:

```bash
.venv/bin/python bot.py --capture cibc     # re-saves tests/fixtures/cibc_results.html
```

then re-run `pytest` and fix what actually broke.
