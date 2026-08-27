# Onboarding

For someone who has cloned this and wants it watching their own job search, with
their own Claude Code. Should take about ten minutes, most of it waiting for
Chromium to download.

---

## 1. Run `./init`

Requires **Python 3.11+** (the config loader uses stdlib `tomllib`) and Claude
Code. Everything else the script handles.

```bash
git clone <your-fork> anyluck
cd anyluck
./init
```

It works through five stages and **stops at the first one that fails**, with a
message saying what to do about it:

| Stage | What it does |
|---|---|
| 1 Python | Finds a 3.11+ interpreter, or tells you none is installed |
| 2 Dependencies | Creates `.venv`, installs `playwright`, `pytest` and `pypdf` |
| 3 Browser | Downloads Chromium (~150MB, once) |
| 4 Verify | **Actually launches Chromium**, checks for a desktop session, runs the test suite |
| 5 Resume | Finds your resume file, converts it to `resume.md`, shows you the result |

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

Drop your resume into the project folder — **whatever format you already have
it in**. Stage 5 looks for any file with `resume` in the name and converts it:

| Format | Notes |
|---|---|
| `.pdf` | Uses `pdftotext` when available, `pypdf` otherwise |
| `.docx` | Modern Word |
| `.odt` | LibreOffice / OpenOffice |
| `.md`, `.txt` | Read as-is |
| `.doc` | **Not supported** — the old binary Word format. Open it and re-save as `.docx` or PDF. |

The name just has to contain "resume" somewhere, in any casing —
`Resume.pdf`, `RESUME.PDF`, `Jane-Doe-Resume-2026.docx` all work. If several
match, the most recently modified wins and stage 5 prints which one it took.

Then press **1** and Enter. You get a preview:

```
  ok  imported Jane-Doe-Resume.pdf - 3,412 characters -> resume.md
        Jane Doe
        416-555-0134 | jane@example.com | Toronto, ON
        Education
```

**Read that preview.** It is the one moment where a bad parse is obvious. If the
lines look shuffled or full of fragments, the conversion mangled your layout and
`/jobscan` will match against nonsense — re-export the PDF or supply a `.docx`.

Two things stage 5 will refuse to do:

- **Overwrite a `resume.md` you wrote yourself.** It only replaces files it
  created, so hand-written notes are safe.
- **Accept a scanned PDF.** A scan is an image with no text in it. Rather than
  writing an empty `resume.md` and leaving `/jobscan` silently matching against
  nothing, it tells you the file has no text layer. There is no OCR.

Your resume never leaves your machine. `.gitignore` excludes anything with
"resume" in the name — the original *and* the converted `resume.md` — so
neither is ever committed or pushed.

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
./anyluck
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
./anyluck --watch
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
| `resume.md` | `./init` | Converted from your resume file. Hand-write it instead and init leaves it alone. |

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

3. Run `./anyluck --board shopify` and see if jobs come back.

**Not Workday?** Most Toronto tech companies run Greenhouse, Lever, or Ashby
instead. None of those has an adapter yet, but all three are public,
unauthenticated JSON APIs that are far easier to read than scraped HTML — and
unlike Workday they carry real publish timestamps. Identify which one a company
uses by looking at where its "Apply" link points:

| URL pattern | Platform |
|---|---|
| `boards.greenhouse.io/{slug}` | Greenhouse |
| `jobs.lever.co/{slug}` | Lever |
| `jobs.ashbyhq.com/{slug}` | Ashby |
| `{slug}.wd{N}.myworkdayjobs.com` | Workday — works today |

Any new adapter just has to return the same record shape as the existing two;
see `docs/PLAN.md`.

To identify an unknown site by hand: open the careers page, F12 → Network tab,
type into its search box, and find the request that returns the job list.

---

## Working on the code

Tests first, always. The scraper tests run against saved HTML in
`tests/fixtures/`, so `pytest` needs no network and no browser download beyond
the initial one.

If a bank redesigns its site, the fixtures go stale and the tests keep passing
while the real scraper fails. That's the known limitation of fixture-based
scraper tests. When a board goes quiet, recapture:

```bash
./anyluck --capture cibc     # re-saves tests/fixtures/cibc_results.html
```

then re-run `pytest` and fix what actually broke.
