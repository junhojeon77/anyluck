"""Resume discovery and format extraction. No network."""
import os
import zipfile
from datetime import datetime, timezone
from pathlib import Path

import pytest

import anyluck

NOW = datetime(2026, 8, 27, 9, 0, tzinfo=timezone.utc)
FIXTURES = Path(__file__).parent / "fixtures"


def make_docx(path, paragraphs):
    """A .docx is a ZIP with XML inside - build a real one, no binary fixture.

    Each paragraph is a list of runs, because Word splits a single sentence
    across many <w:r> runs and they must be joined without a break.
    """
    body = ""
    for runs in paragraphs:
        cells = "".join(f"<w:r><w:t>{run}</w:t></w:r>" for run in runs)
        body += f"<w:p>{cells}</w:p>"
    doc = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/'
        'wordprocessingml/2006/main"><w:body>' + body + "</w:body></w:document>"
    )
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("[Content_Types].xml", "<Types/>")
        zf.writestr("word/document.xml", doc)
    return path


def make_odt(path, paragraphs):
    body = "".join(f"<text:p>{p}</text:p>" for p in paragraphs)
    content = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<office:document-content '
        'xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0" '
        'xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0">'
        "<office:body><office:text>" + body + "</office:text></office:body>"
        "</office:document-content>"
    )
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("content.xml", content)
    return path


# --- finding the file -------------------------------------------------------

def test_find_resume_finds_a_pdf(tmp_path):
    (tmp_path / "Resume.pdf").write_bytes(b"x")
    assert anyluck.find_resume(tmp_path).name == "Resume.pdf"


@pytest.mark.parametrize("name", [
    "RESUME.PDF", "resume.docx", "My_Resume_2026.docx",
    "jane-doe-resume.pdf", "ReSuMe.txt",
])
def test_find_resume_is_case_and_position_insensitive(tmp_path, name):
    (tmp_path / name).write_bytes(b"x")
    assert anyluck.find_resume(tmp_path).name == name


def test_find_resume_ignores_our_own_markdown_output(tmp_path):
    # resume.md matches *RESUME*.* - without an exclusion we would re-import
    # our own output on every run.
    (tmp_path / "resume.md").write_text("# converted earlier")
    assert anyluck.find_resume(tmp_path) is None


def test_find_resume_returns_none_when_nothing_matches(tmp_path):
    (tmp_path / "notes.txt").write_text("x")
    assert anyluck.find_resume(tmp_path) is None


def test_find_resume_picks_the_most_recently_modified(tmp_path):
    old = tmp_path / "Resume_2024.pdf"
    new = tmp_path / "Resume_2026.pdf"
    old.write_bytes(b"x")
    new.write_bytes(b"x")
    os.utime(old, (1_600_000_000, 1_600_000_000))
    os.utime(new, (1_700_000_000, 1_700_000_000))
    assert anyluck.find_resume(tmp_path).name == "Resume_2026.pdf"


# --- extraction -------------------------------------------------------------

def test_extract_text_reads_plain_text(tmp_path):
    p = tmp_path / "my_resume.txt"
    p.write_text("Jane Doe\nBackend Developer")
    assert "Backend Developer" in anyluck.extract_text(p)


def test_extract_text_reads_markdown(tmp_path):
    p = tmp_path / "my_resume.md"
    p.write_text("# Jane Doe\n\nBackend Developer")
    assert "Backend Developer" in anyluck.extract_text(p)


def test_extract_text_reads_docx_paragraphs(tmp_path):
    p = make_docx(tmp_path / "Resume.docx", [["Jane Doe"], ["Backend Developer"]])
    text = anyluck.extract_text(p)
    assert "Jane Doe" in text
    assert "Backend Developer" in text
    assert text.index("Jane Doe") < text.index("Backend Developer")


def test_extract_text_joins_runs_within_one_docx_paragraph(tmp_path):
    # Word splits a sentence across runs constantly. Treating each run as its
    # own line is the classic docx bug and shreds every sentence.
    p = make_docx(tmp_path / "Resume.docx", [["Back", "end Dev", "eloper"]])
    assert "Backend Developer" in anyluck.extract_text(p)


def test_extract_text_reads_odt(tmp_path):
    p = make_odt(tmp_path / "Resume.odt", ["Jane Doe", "Backend Developer"])
    assert "Backend Developer" in anyluck.extract_text(p)


def test_extract_text_reads_pdf():
    text = anyluck.extract_text(FIXTURES / "resume_sample.pdf")
    assert "Jane Doe" in text
    assert "Backend Developer" in text


def test_extract_text_rejects_a_pdf_with_no_text_layer():
    # A scan is an image. Writing an empty resume.md would leave /jobscan
    # silently matching against nothing.
    with pytest.raises(ValueError, match="no text"):
        anyluck.extract_text(FIXTURES / "resume_scanned.pdf")


def test_extract_text_rejects_legacy_doc_with_a_usable_message(tmp_path):
    p = tmp_path / "Resume.doc"
    p.write_bytes(b"\xd0\xcf\x11\xe0")
    with pytest.raises(ValueError, match=r"\.docx"):
        anyluck.extract_text(p)


def test_extract_text_rejects_an_unknown_format_naming_it(tmp_path):
    p = tmp_path / "Resume.pages"
    p.write_bytes(b"x")
    with pytest.raises(ValueError, match=r"\.pages"):
        anyluck.extract_text(p)


# --- importing --------------------------------------------------------------

def test_import_resume_writes_markdown_with_provenance(tmp_path):
    src = tmp_path / "Resume.txt"
    src.write_text("Jane Doe\nBackend Developer")
    dest = tmp_path / "resume.md"
    anyluck.import_resume(src, dest, NOW)
    written = dest.read_text()
    assert "Backend Developer" in written
    assert "Resume.txt" in written.splitlines()[0]


def test_import_resume_refuses_to_clobber_a_handwritten_file(tmp_path):
    src = tmp_path / "Resume.txt"
    src.write_text("from the pdf")
    dest = tmp_path / "resume.md"
    dest.write_text("# notes I typed myself\n")
    with pytest.raises(FileExistsError, match="resume.md"):
        anyluck.import_resume(src, dest, NOW)
    assert dest.read_text() == "# notes I typed myself\n"


def test_import_resume_may_overwrite_its_own_earlier_output(tmp_path):
    src = tmp_path / "Resume.txt"
    src.write_text("first version")
    dest = tmp_path / "resume.md"
    anyluck.import_resume(src, dest, NOW)
    src.write_text("second version")
    anyluck.import_resume(src, dest, NOW)
    assert "second version" in dest.read_text()
