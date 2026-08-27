"""Pure-logic tests. No network, no browser."""
import json
import os
from datetime import datetime, timedelta, timezone

import pytest

import anyluck

NOW = datetime(2026, 8, 26, 12, 0, tzinfo=timezone.utc)


def job(key, title="Engineer", company="CIBC", location="Toronto, ON",
        posted_text="Posted Today", url="https://x/job/1", remote="", req_id="R1"):
    return dict(key=key, title=title, company=company, location=location,
                posted_text=posted_text, url=url, remote=remote, req_id=req_id,
                source="workday")


# --- config -----------------------------------------------------------------

def test_load_config_missing_file_names_the_file(tmp_path):
    with pytest.raises(FileNotFoundError, match="config.toml"):
        anyluck.load_config(tmp_path / "config.toml")


def test_load_config_reads_search_terms_and_boards(tmp_path):
    p = tmp_path / "config.toml"
    p.write_text('search_terms = ["backend"]\nlocations = []\n'
                 'hours_between_runs = 4\n\n'
                 '[[boards]]\nname = "CIBC"\ntype = "workday"\nurl = "https://x"\n')
    cfg = anyluck.load_config(p)
    assert cfg["search_terms"] == ["backend"]
    assert cfg["boards"][0]["name"] == "CIBC"


# --- location cleaning ------------------------------------------------------

def test_normalize_location_strips_facility_code_and_parenthetical():
    assert anyluck.normalize_location("FTC03 - Ft. Collins, CO B-3 (FTC03)") == "Ft. Collins, CO"


def test_normalize_location_leaves_plain_city_alone():
    assert anyluck.normalize_location("Toronto, Ontario, Canada") == "Toronto, Ontario, Canada"


def test_normalize_location_passes_through_multi_location_placeholder():
    # "2 Locations" carries no place name; dropping it would silently lose
    # every multi-city posting.
    assert anyluck.normalize_location("2 Locations") == "2 Locations"


def test_normalize_location_strips_the_locations_label():
    assert anyluck.normalize_location("locations\nToronto, ON") == "Toronto, ON"


# --- keys -------------------------------------------------------------------

def test_job_key_uses_the_url_slug():
    k = anyluck.job_key("CIBC", "/en-US/search/job/Toronto-ON/Software-Engineer-II_2613338?q=x")
    assert k == "CIBC:Software-Engineer-II_2613338"


def test_job_key_distinguishes_postings_sharing_a_req_id():
    a = anyluck.job_key("CIBC", "/job/Toronto/Backend-Dev_111")
    b = anyluck.job_key("CIBC", "/job/Calgary/Backend-Dev_222")
    assert a != b


def test_job_key_is_namespaced_by_bank():
    assert anyluck.job_key("RBC", "/job/x/Dev_1") != anyluck.job_key("TD", "/job/x/Dev_1")


# --- the seen-set: the heart of the bot ------------------------------------

def test_seed_run_records_everything_and_flags_nothing():
    seen, new = anyluck.merge_seen({}, [job(f"CIBC:{i}") for i in range(5)], NOW)
    assert len(seen) == 5
    assert new == []


def test_second_run_with_same_jobs_flags_nothing_and_keeps_first_seen():
    jobs = [job(f"CIBC:{i}") for i in range(5)]
    seen, _ = anyluck.merge_seen({}, jobs, NOW)
    later = NOW + timedelta(hours=4)
    seen2, new = anyluck.merge_seen(seen, jobs, later)
    assert new == []
    assert seen2["CIBC:0"]["first_seen"] == seen["CIBC:0"]["first_seen"]


def test_second_run_flags_exactly_the_new_job():
    jobs = [job(f"CIBC:{i}") for i in range(5)]
    seen, _ = anyluck.merge_seen({}, jobs, NOW)
    later = NOW + timedelta(hours=4)
    seen2, new = anyluck.merge_seen(seen, jobs + [job("CIBC:99")], later)
    assert new == ["CIBC:99"]
    assert seen2["CIBC:99"]["first_seen"] == later.isoformat()


def test_job_that_disappears_is_retained_and_not_resurrected_as_new():
    jobs = [job("CIBC:1"), job("CIBC:2")]
    seen, _ = anyluck.merge_seen({}, jobs, NOW)
    # board drops CIBC:2 ...
    seen, new = anyluck.merge_seen(seen, [job("CIBC:1")], NOW + timedelta(hours=4))
    assert new == []
    assert "CIBC:2" in seen
    # ... then it comes back. Still not new.
    seen, new = anyluck.merge_seen(seen, jobs, NOW + timedelta(hours=8))
    assert new == []


def test_empty_state_with_one_job_still_seeds_rather_than_alerting():
    _, new = anyluck.merge_seen({}, [job("CIBC:1")], NOW)
    assert new == []


# --- pruning ----------------------------------------------------------------

def test_prune_drops_keys_older_than_thirty_days():
    seen, _ = anyluck.merge_seen({}, [job("CIBC:old")], NOW - timedelta(days=31))
    assert anyluck.prune(seen, NOW) == {}


def test_prune_keeps_keys_younger_than_thirty_days():
    seen, _ = anyluck.merge_seen({}, [job("CIBC:recent")], NOW - timedelta(days=29))
    assert "CIBC:recent" in anyluck.prune(seen, NOW)


# --- age rendering ----------------------------------------------------------

@pytest.mark.parametrize("minutes,expected", [
    (0, "just now"),
    (5, "just now"),
    (45, "45m ago"),
    (90, "1h ago"),      # floored, as every other UI does it
    (60 * 5, "5h ago"),
    (60 * 50, "2d ago"),
    (60 * 24 * 9, "9d ago"),
])
def test_humanize_age(minutes, expected):
    then = (NOW - timedelta(minutes=minutes)).isoformat()
    assert anyluck.humanize_age(then, NOW) == expected


# --- markdown ---------------------------------------------------------------

def test_render_groups_under_bank_headings():
    seen, _ = anyluck.merge_seen({}, [job("CIBC:1", company="CIBC"),
                                  job("RBC:1", company="RBC")], NOW)
    md = anyluck.render_markdown(seen, [], NOW)
    assert "## CIBC" in md and "## RBC" in md


def test_render_sorts_newest_first_within_a_bank():
    seen, _ = anyluck.merge_seen({}, [job("CIBC:old", title="Old")], NOW - timedelta(days=2))
    seen, _ = anyluck.merge_seen(seen, [job("CIBC:old", title="Old"),
                                    job("CIBC:new", title="New")], NOW)
    md = anyluck.render_markdown(seen, [], NOW)
    assert md.index("New") < md.index("Old")


def test_render_marks_only_the_new_jobs():
    seen, _ = anyluck.merge_seen({}, [job("CIBC:1", title="Alpha"),
                                  job("CIBC:2", title="Beta")], NOW)
    md = anyluck.render_markdown(seen, ["CIBC:2"], NOW)
    alpha = [l for l in md.splitlines() if "Alpha" in l][0]
    beta = [l for l in md.splitlines() if "Beta" in l][0]
    assert anyluck.NEW_MARKER in beta
    assert anyluck.NEW_MARKER not in alpha


def test_render_shows_both_first_seen_age_and_the_sites_own_prose():
    seen, _ = anyluck.merge_seen({}, [job("CIBC:1", posted_text="Posted 30+ Days Ago")],
                             NOW - timedelta(hours=2))
    md = anyluck.render_markdown(seen, [], NOW)
    assert "2h ago" in md
    assert "Posted 30+ Days Ago" in md


def test_render_includes_the_url():
    seen, _ = anyluck.merge_seen({}, [job("CIBC:1", url="https://cibc/job/42")], NOW)
    assert "https://cibc/job/42" in anyluck.render_markdown(seen, [], NOW)


def test_render_empty_state_does_not_crash():
    assert isinstance(anyluck.render_markdown({}, [], NOW), str)


# --- state io ---------------------------------------------------------------

def test_load_seen_raises_loudly_on_malformed_json(tmp_path):
    p = tmp_path / "seen.json"
    p.write_text("{ this is not json")
    # Silently returning {} would re-alert on the entire back catalogue.
    with pytest.raises(ValueError, match="seen.json"):
        anyluck.load_seen(p)


def test_load_seen_returns_empty_when_file_absent(tmp_path):
    assert anyluck.load_seen(tmp_path / "seen.json") == {}


def test_save_atomic_roundtrips(tmp_path):
    p = tmp_path / "seen.json"
    anyluck.save_atomic(p, json.dumps({"a": 1}))
    assert json.loads(p.read_text()) == {"a": 1}


def test_save_atomic_leaves_original_intact_when_write_fails(tmp_path, monkeypatch):
    p = tmp_path / "seen.json"
    p.write_text('{"good": true}')
    monkeypatch.setattr(os, "replace", lambda *a: (_ for _ in ()).throw(OSError("disk full")))
    with pytest.raises(OSError):
        anyluck.save_atomic(p, '{"bad": true}')
    assert json.loads(p.read_text()) == {"good": True}


def test_save_atomic_leaves_no_temp_files_behind(tmp_path):
    p = tmp_path / "seen.json"
    anyluck.save_atomic(p, "{}")
    assert [f.name for f in tmp_path.iterdir()] == ["seen.json"]


# --- location filtering -----------------------------------------------------

def test_empty_location_filter_keeps_everything():
    jobs = [job("a", location="Bogota, DC, CO"), job("b", location="Toronto, ON")]
    assert anyluck.filter_locations(jobs, []) == jobs


def test_location_filter_matches_case_insensitive_substring():
    jobs = [job("a", location="Bogota, DC, CO"), job("b", location="Toronto, ON")]
    assert [j["key"] for j in anyluck.filter_locations(jobs, ["toronto"])] == ["b"]


def test_location_filter_keeps_multi_location_placeholders():
    # No place name to match on, but dropping these loses every multi-city job.
    jobs = [job("a", location="2 Locations"), job("b", location="Bogota, DC, CO")]
    assert [j["key"] for j in anyluck.filter_locations(jobs, ["toronto"])] == ["a"]
