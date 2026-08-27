"""anyluck - answers "any luck with the job search?"."""

NEW_MARKER = "\U0001F195"


def load_config(path):
    raise NotImplementedError


def normalize_location(text):
    raise NotImplementedError


def job_key(company, href):
    raise NotImplementedError


def merge_seen(seen, jobs, now):
    raise NotImplementedError


def prune(seen, now, days=30):
    raise NotImplementedError


def humanize_age(first_seen, now):
    raise NotImplementedError


def render_markdown(seen, new_keys, now):
    raise NotImplementedError


def load_seen(path):
    raise NotImplementedError


def save_atomic(path, text):
    raise NotImplementedError


def filter_locations(jobs, locations):
    raise NotImplementedError
