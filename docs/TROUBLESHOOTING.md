# Troubleshooting

Ordered roughly by how often each one happens.

---

## A board returns zero jobs

The most common failure, and almost always one of three things.

**First, check it's not just an honest zero.** Open the board's URL yourself and
type your search term into its search box. No results there either? Nothing is
broken — your `search_terms` are too narrow for that bank.

Otherwise, in Claude Code:

```
/discover cibc
```

It opens the board in a visible browser, works out what changed — a renamed
career-site slug, moved selectors, a redirect — and patches `config.toml`.

If you'd rather do it by hand, it takes about fifteen seconds:

1. Open the careers page.
2. F12 → Network tab → filter on `jobs`.
3. Type anything into the site's own search box.
4. Find the request that comes back with the job list; its URL carries the
   tenant and career-site slug.

This is the only reliable method — there is no public directory mapping
companies to Workday tenants.

**Career site slugs get renamed.** These were all verified live, but a slug that
worked last year may not work today:

| Bank | Career site slug |
|---|---|
| RBC | `rbcglobal1` |
| TD | `TD_Bank_Careers` |
| BMO | `External` |
| CIBC | `search` |
| Scotiabank | not Workday — `jobs.scotiabank.com` |

---

## A board returns the bank's entire catalogue

You asked for "software engineer" and got 1,517 jobs. The search never applied.

Search is passed through the URL (`?q=software%20engineer`), **not** by typing
into the page's search box. Typing into the box looks like it works and silently
fails on some tenants — RBC in particular. If you've been editing the scraper,
check the query is still going through the URL.

---

## A page loads but no job rows appear

Usually Cloudflare. The scraper is anchored on Workday's `data-automation-id`
attributes, so if those never appear, you're probably looking at an interstitial
rather than the real page.

Since the browser is visible, look at it. If you see "Checking your browser" or
a captcha, that's the answer.

**Fixes, in order:**

1. **Solve it by hand once.** The browser profile in `.pw-profile/` persists, so
   the clearance cookie carries to later cycles. This works more often than it
   should.
2. **Slow down.** Workday's rate limit appears to be **per source IP across all
   tenants combined**, not per bank — so a block from CIBC is a signal about
   your total volume across RBC, TD, BMO and CIBC together. Cut `search_terms`
   or raise `hours_between_runs`.
3. **Wait it out.** These clear on their own, usually within the hour.

A headed real browser is much harder to block than a scripted HTTP client, which
is part of why this project uses one. But it isn't immune.

---

## Everything is flagged NEW at once

State was lost. `seen.json` was deleted, corrupted, or you're running from a
different directory than last time (paths are relative to the working directory).

Check first:

```bash
ls -la seen.json && .venv/bin/python -c "import json;print(len(json.load(open('seen.json'))))"
```

Zero or an error means state is gone. There's no recovering it — the next run
re-seeds and the report after that is accurate again. Ignore the one bad report.

To avoid it: don't delete `seen.json`, and always run from the project root.
`./anyluck` cds there itself, so prefer it over calling the module directly.

---

## `seen.json` is corrupted

You'll get a loud error naming the file, not a silent recovery. That's
deliberate — silently falling back to empty state would re-alert you about every
job at every bank, which looks exactly like a genuine flood of new postings and
wastes an afternoon before you work out what happened. Failing loudly is the
cheaper direction to fail.

The file is written atomically (temp file, then rename), so a crash mid-write
can't corrupt it. If it happened anyway, something else touched the file.

```bash
mv seen.json seen.json.broken
./anyluck                    # re-seeds, flags nothing
```

---

## Browser won't launch / no display

Symptoms: `Missing X server or $DISPLAY`, or Playwright exits immediately.

This bot runs the browser **visibly**, which needs a live desktop session.

```bash
echo $DISPLAY $WAYLAND_DISPLAY    # at least one should be set
```

Both empty means no graphical session — you're over plain SSH, in a container,
or in a cron job. Cron in particular has no display, which is exactly why this
uses a foreground `--watch` loop instead of a cron entry.

Options: run it in a desktop terminal; `ssh -X` for forwarding; or set
`headless=True` in `anyluck.py`'s `launch_persistent_context` call, accepting
that headless browsers are a known bot signal and Cloudflare will notice.

---

## Chromium is installed but won't start

Missing system libraries — the classic Linux case. `./init` stage 4 catches this
before you ever run a scrape, and prints the packages to install.

```bash
sudo .venv/bin/playwright install-deps chromium     # Debian/Ubuntu
sudo pacman -S nss atk at-spi2-atk libcups libdrm libxkbcommon   # Arch/Manjaro
```

---

## `playwright: executable doesn't exist`

```bash
./init
```

The browser is a separate download from the library, and `./init` stage 3
handles it. It's safe to re-run and skips whatever is already done.

---

## `No .venv yet - run ./init first`

The `./anyluck` wrapper needs the virtualenv that `./init` builds. Run `./init`.

---

## Tests pass but scraping fails

Expected, and worth understanding.

The scraper tests run against saved HTML in `tests/fixtures/`. That makes them
fast, offline, and deterministic — but it also means they test the scraper
against **last month's version of the site**. A bank redesigns, the live scraper
breaks, the tests stay green.

Recapture:

```bash
./anyluck --capture cibc
.venv/bin/python -m pytest -v
```

Now the tests fail, showing you exactly what changed. Fix the scraper, get them
green, and the fixture is a regression test for that redesign.

---

## Cycles take too long

Time scales with `len(search_terms) × number_of_boards`. Each term is searched
against each board and paginated separately.

- Cut `search_terms` to two or three focused ones. `""` fetches a bank's entire
  catalogue.
- Drop boards you don't care about.

Don't parallelise the boards. Per §7's shared rate limit, concurrent requests
across tenants are what actually gets you blocked.

---

## `/jobscan` says it can't find jobs.md

Run `./anyluck` at least once first. `jobs.md` doesn't exist until a cycle
completes.

If it exists but `/jobscan` finds nothing in it, every job was filtered out by
`locations`. Set `locations = []` to disable filtering and re-run.

---

## Jobs I want are being filtered out

`locations` is a case-insensitive substring match against the location string.
It misses when:

- The posting reads `"2 Locations"` with no place name. These are deliberately
  **let through**, not dropped — check they're actually reaching `jobs.md`.
- The location carries an internal facility code, like
  `"FTC03 - Ft. Collins, CO B-3 (FTC03)"` (§6). These get stripped before
  matching, but an unusual format may survive.
- You wrote `"Toronto"` and the bank writes `"Toronto Region"` or `"GTA"`.

Diagnose by emptying the filter:

```toml
locations = []
```

Re-run, look at what the location strings actually say, then filter on that.

---

## Still stuck

The browser is visible for a reason — watch a cycle and see where it stops.
`./anyluck --board cibc` runs one board so you're not waiting on four others.
