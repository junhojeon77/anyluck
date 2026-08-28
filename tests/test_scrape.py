"""Scraper tests against the DOM captured in Phase 1. No network."""
import re
from pathlib import Path

import pytest
from playwright.sync_api import sync_playwright

import anyluck

FIXTURES = Path(__file__).parent / "fixtures"

# The saved pages carry their own React bundle. set_content re-runs it, and it
# hydrates over the server markup and wipes it - CIBC goes from 17 rows to 0.
# Strip the scripts and every fixture is deterministic.
_SCRIPT = re.compile(r"<script\b[^>]*>.*?</script>", re.S | re.I)

BOARDS = {
    "cibc": {"name": "CIBC", "type": "workday",
             "url": "https://cibc.wd3.myworkdayjobs.com/en-US/search"},
    "rbc": {"name": "RBC", "type": "workday",
            "url": "https://rbc.wd3.myworkdayjobs.com/en-US/rbcglobal1"},
    "td": {"name": "TD", "type": "workday",
           "url": "https://td.wd3.myworkdayjobs.com/en-US/TD_Bank_Careers"},
    "bmo": {"name": "BMO", "type": "workday",
            "url": "https://bmo.wd3.myworkdayjobs.com/en-US/External"},
    "scotia": {"name": "Scotiabank", "type": "scotia",
               "url": "https://jobs.scotiabank.com/search/"},
}

RECORD_KEYS = {"key", "source", "company", "title", "location", "remote",
               "url", "req_id", "posted_text", "published"}


@pytest.fixture(scope="module")
def page():
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        yield browser.new_page()
        browser.close()


def load(page, name):
    page.set_content(_SCRIPT.sub("", (FIXTURES / f"{name}_results.html").read_text()))


# --- Workday: how many rows come back ---------------------------------------

def test_cibc_fixture_yields_every_row(page):
    load(page, "cibc")
    assert len(anyluck.parse_workday(page, BOARDS["cibc"])) == 17


@pytest.mark.parametrize("bank", ["rbc", "td", "bmo"])
def test_one_parser_covers_all_four_workday_banks(page, bank):
    load(page, bank)
    assert len(anyluck.parse_workday(page, BOARDS[bank])) == 20


# --- Workday: the fields off a known row ------------------------------------

def test_reads_a_known_row_exactly(page):
    load(page, "cibc")
    row = anyluck.parse_workday(page, BOARDS["cibc"])[0]
    assert row["title"] == "Software Engineer II - Android"
    assert row["location"] == "Toronto, ON"
    assert row["posted_text"] == "Posted Today"
    assert row["req_id"] == "2613338"
    assert row["company"] == "CIBC"
    assert row["source"] == "workday"


def test_url_is_absolute(page):
    # hrefs in the DOM are relative; an unjoined one is a dead link in jobs.md.
    load(page, "cibc")
    for row in anyluck.parse_workday(page, BOARDS["cibc"]):
        assert row["url"].startswith("https://cibc.wd3.myworkdayjobs.com/")


def test_key_is_the_shared_job_key(page):
    load(page, "cibc")
    row = anyluck.parse_workday(page, BOARDS["cibc"])[0]
    assert row["key"] == anyluck.job_key("CIBC", row["url"])
    assert row["key"] == "CIBC:Software-Engineer-II---Android_2613338"


def test_labels_are_stripped_from_values(page):
    # inner_text prefixes "locations" and "posted on" onto their own values.
    load(page, "cibc")
    for row in anyluck.parse_workday(page, BOARDS["cibc"]):
        assert "locations" not in row["location"].lower()
        assert "posted on" not in row["posted_text"].lower()


def test_td_req_id_is_the_first_subtitle_line_only(page):
    # TD's subtitle is two lines: "R_1505562" then "Software Engineering".
    load(page, "td")
    row = anyluck.parse_workday(page, BOARDS["td"])[0]
    assert row["req_id"] == "R_1505562"


def test_every_record_has_the_full_normalized_key_set(page):
    load(page, "bmo")
    for row in anyluck.parse_workday(page, BOARDS["bmo"]):
        assert RECORD_KEYS <= set(row)


def test_workday_records_carry_no_published_date(page):
    # Workday only offers prose like "Posted Today", never a real date.
    load(page, "cibc")
    assert all(row["published"] == "" for row in anyluck.parse_workday(page, BOARDS["cibc"]))


# --- Workday: multi-location rows must survive ------------------------------

@pytest.mark.parametrize("bank,expected", [("cibc", 1), ("rbc", 8), ("td", 4), ("bmo", 2)])
def test_multi_location_rows_are_kept(page, bank, expected):
    # These carry no place name. Dropping them loses every multi-city posting,
    # so they are returned verbatim for the location filter to let through.
    load(page, bank)
    rows = anyluck.parse_workday(page, BOARDS[bank])
    placeholders = [r for r in rows if re.fullmatch(r"\d+ Locations?", r["location"])]
    assert len(placeholders) == expected


# --- Workday: degraded pages ------------------------------------------------

def test_a_row_without_a_subtitle_still_yields_a_record(page):
    # No fixture row lacks one, but some tenants omit it entirely.
    page.set_content("""
      <ul><li>
        <h3><a data-automation-id="jobTitle" href="/job/Toronto/Dev_1">Developer</a></h3>
        <div data-automation-id="locations">locations<br>Toronto, ON</div>
        <div data-automation-id="postedOn">posted on<br>Posted Today</div>
      </li></ul>""")
    rows = anyluck.parse_workday(page, BOARDS["cibc"])
    assert len(rows) == 1
    assert rows[0]["req_id"] == ""
    assert rows[0]["title"] == "Developer"


def test_a_page_with_no_rows_returns_empty_not_an_error(page):
    # What a Cloudflare interstitial looks like from here.
    page.set_content("<html><body><h1>Checking your browser</h1></body></html>")
    assert anyluck.parse_workday(page, BOARDS["cibc"]) == []


# --- Scotiabank -------------------------------------------------------------

def test_scotia_fixture_yields_every_row(page):
    load(page, "scotia")
    assert len(anyluck.parse_scotia(page, BOARDS["scotia"])) == 25


def test_scotia_reads_a_known_row(page):
    load(page, "scotia")
    row = anyluck.parse_scotia(page, BOARDS["scotia"])[0]
    assert row["title"] == "Front end Software Engineer Associate"
    assert row["location"] == "Bogota, DC, CO"
    assert row["url"].startswith("https://jobs.scotiabank.com/job/")
    assert row["company"] == "Scotiabank"
    assert row["source"] == "scotia"


def test_scotia_publishes_a_real_date(page):
    # The one board that gives a genuine date rather than English prose.
    load(page, "scotia")
    assert anyluck.parse_scotia(page, BOARDS["scotia"])[0]["published"] == "2026-08-16"


def test_scotia_empty_page_returns_empty(page):
    page.set_content("<html><body>no results</body></html>")
    assert anyluck.parse_scotia(page, BOARDS["scotia"]) == []


# --- pagination, against a fake page ----------------------------------------

class FakeNext:
    def __init__(self, owner):
        self.owner = owner

    @property
    def first(self):
        return self

    def count(self):
        return 1 if self.owner.pages_remaining > 0 else 0

    def is_enabled(self):
        return self.owner.pages_remaining > 0

    def click(self):
        self.owner.pages_remaining -= 1


class FakePage:
    """Answers only the handful of calls scrape_workday makes."""

    def __init__(self, total_pages):
        self.pages_remaining = total_pages - 1
        self.goto_urls = []

    def goto(self, url, **kwargs):
        self.goto_urls.append(url)

    def wait_for_selector(self, selector, **kwargs):
        return True

    def wait_for_timeout(self, ms):
        pass

    def locator(self, selector):
        return FakeNext(self)


@pytest.fixture
def counted_parse(monkeypatch):
    calls = []
    monkeypatch.setattr(anyluck, "parse_workday",
                        lambda page, board: calls.append(1) or [])
    return calls


def test_scrape_stops_at_max_pages(counted_parse):
    anyluck.scrape_workday(FakePage(10), BOARDS["cibc"], "dev", max_pages=3)
    assert len(counted_parse) == 3


def test_max_pages_zero_reads_every_page(counted_parse):
    anyluck.scrape_workday(FakePage(4), BOARDS["cibc"], "dev", max_pages=0)
    assert len(counted_parse) == 4


def test_scrape_stops_when_next_is_gone(counted_parse):
    anyluck.scrape_workday(FakePage(1), BOARDS["cibc"], "dev", max_pages=0)
    assert len(counted_parse) == 1


def test_scrape_puts_the_search_term_in_the_url(counted_parse):
    # Typing into the page's search box silently fails on RBC and returns the
    # whole catalogue; the query has to go through the URL.
    fake = FakePage(1)
    anyluck.scrape_workday(fake, BOARDS["cibc"], "software engineer", max_pages=0)
    assert fake.goto_urls[0].startswith(BOARDS["cibc"]["url"] + "?q=")
    assert "software" in fake.goto_urls[0]
    assert " " not in fake.goto_urls[0]
