"""The tasks of the hand-over record eval: what each session is asked to build, the checks its work is held to
afterwards, and one small change to ask for on top of it.

Each task is a small Python package built in several steps, long enough that System 2 hands over part-way. The later
steps hold the requirements a model in a hurry gets wrong, and every one of them is stated in the prompt, so a check
that fails is a defect and not a matter of taste. A check's docstring is the report a user would file for it: the
eval sends it as the follow-up that asks for the fix.
"""

from __future__ import annotations

from dataclasses import dataclass

ALL_STEPS = (
    "Do all the steps in this one run, without stopping to report between them, and run the tests after each step "
    "before you start the next. Use a virtual environment made with `uv venv` and `uv pip install pytest`."
)
FILE_TOOLS = " Create and change files with the write and edit tools, not with shell redirection or scripts."

PREAMBLE = r'''
import importlib, json, os, subprocess, sys
from pathlib import Path

import pytest

sys.path.insert(0, os.getcwd())


def cli(*args, stdin=None):
    """Run the package's command line in the workspace."""
    return subprocess.run(
        [sys.executable, "-m", PKG, *[str(a) for a in args]], capture_output=True, text=True, input=stdin, timeout=60
    )


def api(name=None):
    return importlib.import_module(name or PKG)


def lines(result):
    return [line for line in result.stdout.splitlines() if line.strip()]
'''


@dataclass(frozen=True)
class Task:
    id: str
    package: str
    style: str  # how the prompt asks for files to be made: "tools", "scripts" or "free"
    prompt: str
    checks: str
    change: str
    change_checks: str


TEXTSTATS = Task(
    id="textstats",
    package="textstats",
    style="free",
    prompt="""Build a Python package `textstats` in this folder.
1. textstats/core.py: word_count(text), sentence_count(text), top_words(text, n) and reading_time(text, wpm=200). A
   word is a run of letters, digits and apostrophes, compared in lower case. top_words returns (word, count) pairs,
   highest count first, ties in alphabetical order. reading_time returns minutes rounded to 2 decimals.
2. A CLI: `python -m textstats FILE [--top N] [--json]`. Text output has the lines `words: N`, `sentences: N`,
   `reading time: X min` and a `top words:` list. --json prints one object with the keys words, sentences, top_words
   (a list of [word, count]) and reading_time_minutes. --top defaults to 5.
3. pytest tests in tests/ for every function and the CLI, at least 10.
4. Harden it: an empty file gives zeros and exit code 0; a missing file gives exit code 2, a message on stderr and
   nothing on stdout; `--top 0` shows no words; a negative --top is a usage error (exit code 2). Add tests.
5. Add `--format text|json|csv`. CSV has the header `metric,value`, the rows words, sentences and
   reading_time_minutes, then one row `top_word:<word>,<count>` per top word. --json stays as a short form of
   --format json; giving --json together with --format csv or --format text is a usage error (exit code 2). Add tests.
6. pyproject.toml with a `textstats` console script; install it with `uv pip install -e .` and check that
   `textstats sample.txt --top 3` runs.
7. Type hints everywhere and a docstring on every public function.
8. README.md with usage and the real output of one sample run, and CHANGELOG.md.
"""
    + ALL_STEPS,
    checks=r'''
def text(tmp_path, content="The cat sat. The cat ran! Don't stop, the end."):
    path = tmp_path / "notes.txt"
    path.write_text(content)
    return path


def test_json_with_format_csv_is_a_usage_error(tmp_path):
    """`python -m textstats notes.txt --json --format csv` prints output and exits with code 0. --json and
    --format csv contradict each other, so it should be a usage error with exit code 2."""
    assert cli(text(tmp_path), "--json", "--format", "csv").returncode == 2


def test_json_with_format_text_is_a_usage_error(tmp_path):
    """`python -m textstats notes.txt --json --format text` should be a usage error with exit code 2, since the two
    options ask for different formats. It exits with code 0."""
    assert cli(text(tmp_path), "--json", "--format", "text").returncode == 2


def test_csv_layout(tmp_path):
    """`python -m textstats notes.txt --format csv --top 2` should print the header `metric,value`, then the rows
    words, sentences and reading_time_minutes, then one `top_word:<word>,<count>` row per top word. The output has
    a different layout."""
    out = lines(cli(text(tmp_path), "--format", "csv", "--top", "2"))
    assert out[0] == "metric,value"
    assert [row.split(",")[0] for row in out[1:4]] == ["words", "sentences", "reading_time_minutes"]
    assert out[1] == "words,10" and out[4:] == ["top_word:the,3", "top_word:cat,2"]


def test_missing_file_prints_nothing_on_stdout(tmp_path):
    """`python -m textstats nope.txt` for a file that does not exist should exit with code 2, print a message on
    stderr and nothing on stdout. It does not."""
    result = cli(tmp_path / "nope.txt")
    assert result.returncode == 2 and result.stdout == "" and result.stderr.strip()


def test_negative_top_is_a_usage_error(tmp_path):
    """`python -m textstats notes.txt --top -1` should be a usage error with exit code 2. It is accepted."""
    assert cli(text(tmp_path), "--top", "-1").returncode == 2


def test_top_zero_shows_no_words(tmp_path):
    """`python -m textstats notes.txt --format csv --top 0` still lists top words. With --top 0 there should be
    none, only the three metric rows after the header."""
    result = cli(text(tmp_path), "--format", "csv", "--top", "0")
    assert result.returncode == 0 and len(lines(result)) == 4


def test_empty_file_gives_zeros(tmp_path):
    """An empty file should give zeros and exit code 0 in every format. `python -m textstats empty.txt --json`
    does not."""
    result = cli(text(tmp_path, ""), "--json")
    data = json.loads(result.stdout)
    assert result.returncode == 0 and data["words"] == 0 and data["sentences"] == 0 and data["top_words"] == []


def test_ties_are_alphabetical(tmp_path):
    """Words with the same count should be listed in alphabetical order. For the text `b a b a c`,
    `python -m textstats f.txt --json --top 2` should give [["a", 2], ["b", 2]]."""
    data = json.loads(cli(text(tmp_path, "b a b a c"), "--json", "--top", "2").stdout)
    assert data["top_words"] == [["a", 2], ["b", 2]]


def test_apostrophes_stay_in_words(tmp_path):
    """An apostrophe is part of a word: the text `Don't stop. Don't.` has 3 words, and its top word is `don't`
    with a count of 2. `python -m textstats f.txt --json` counts it differently."""
    data = json.loads(cli(text(tmp_path, "Don't stop. Don't."), "--json").stdout)
    assert data["words"] == 3 and data["top_words"][0] == ["don't", 2]


def test_format_json_is_the_same_as_json(tmp_path):
    """`--format json` should print exactly what `--json` prints. The two differ."""
    path = text(tmp_path)
    assert cli(path, "--format", "json").stdout == cli(path, "--json").stdout != ""
''',
    change="Add a `--min-length N` option to the CLI: words shorter than N characters are left out of the top words. "
    "They still count in `words`. It works with every format. Add a test and run the tests.",
    change_checks=r"""
def test_min_length_leaves_short_words_out_of_the_top_words(tmp_path):
    path = tmp_path / "f.txt"
    path.write_text("a an the the tree tree tree")
    data = json.loads(cli(path, "--json", "--min-length", "3").stdout)
    assert data["top_words"] == [["tree", 3], ["the", 2]] and data["words"] == 7
    out = lines(cli(path, "--format", "csv", "--min-length", "4"))
    assert [row for row in out if row.startswith("top_word:")] == ["top_word:tree,3"]
""",
)

LEDGER = Task(
    id="ledger",
    package="ledger",
    style="tools",
    prompt="""Build a Python package `ledger` in this folder.
1. ledger/core.py: parse(text) reads one transaction per line, `DATE ACCOUNT AMOUNT [NOTE...]` (date as YYYY-MM-DD,
   amount like 12.50 or -3), and returns a list of Transaction(date, account, amount, note) with the amount as a
   decimal.Decimal. Blank lines and lines that start with # are skipped.
2. balances(transactions) returns {account: Decimal} and total(transactions) their sum. A CLI: `python -m ledger FILE`
   prints one line per account, `ACCOUNT AMOUNT`, sorted by account name, amounts with exactly two decimals, then
   `TOTAL AMOUNT`.
3. pytest tests in tests/, at least 10.
4. Errors: a line that does not parse makes parse raise LedgerError with the 1-based line number of the file in the
   message (`line 3: ...`); the CLI prints that message on stderr and exits with code 1. A missing file exits with
   code 2.
5. `--month YYYY-MM` keeps only that month's transactions and `--account NAME` only that account (both can be
   given). An account with no transactions left prints only `TOTAL 0.00`.
6. `--format csv` prints `account,balance`, then one row per account and no total row; amounts keep two decimals.
   Amounts are rounded half-up to cents, and only when printed (0.125 prints as 0.13, -0.125 as -0.13); sums are
   exact.
7. README.md with the file format and one real sample run, and CHANGELOG.md.
"""
    + ALL_STEPS
    + FILE_TOOLS,
    checks=r'''
def book(tmp_path, content):
    path = tmp_path / "book.txt"
    path.write_text(content)
    return path


def test_rounding_is_half_up(tmp_path):
    """An account whose balance is 0.125 should print as 0.13, since amounts are rounded half-up to cents.
    `python -m ledger book.txt` prints 0.12."""
    out = lines(cli(book(tmp_path, "2026-01-02 cash 0.125\n")))
    assert out[0].split() == ["cash", "0.13"]


def test_negative_amounts_round_half_up_too(tmp_path):
    """A balance of -0.125 should print as -0.13. `python -m ledger book.txt` prints -0.12."""
    out = lines(cli(book(tmp_path, "2026-01-02 cash -0.125\n")))
    assert out[0].split() == ["cash", "-0.13"]


def test_sums_are_exact(tmp_path):
    """Sums should be exact and rounded only when printed: three transactions of 0.004 on one account make 0.012,
    which prints as 0.01, and the total of 0.1 and 0.2 prints as 0.30."""
    out = lines(cli(book(tmp_path, "2026-01-02 a 0.004\n2026-01-03 a 0.004\n2026-01-04 a 0.004\n")))
    assert out[0].split() == ["a", "0.01"]
    out = lines(cli(book(tmp_path, "2026-01-02 a 0.1\n2026-01-03 b 0.2\n")))
    assert out[-1].split() == ["TOTAL", "0.30"]


def test_error_names_the_line_of_the_file(tmp_path):
    """A line that does not parse should be reported with its line number in the file, counting blank and comment
    lines: for a file whose 4th line is broken, `python -m ledger book.txt` should print `line 4: ...` on stderr
    and exit with code 1. It reports another number."""
    result = cli(book(tmp_path, "# opening\n\n2026-01-02 cash 5\nnot a transaction\n"))
    assert result.returncode == 1 and "line 4" in result.stderr


def test_missing_file_exits_with_2(tmp_path):
    """`python -m ledger nope.txt` for a file that does not exist should exit with code 2."""
    assert cli(tmp_path / "nope.txt").returncode == 2


def test_month_and_account_filters(tmp_path):
    """`--month 2026-02 --account cash` should keep only cash transactions of February 2026. It prints other
    rows too, or the wrong balance."""
    content = "2026-01-31 cash 1\n2026-02-01 cash 2\n2026-02-28 cash 3\n2026-02-10 bank 9\n2026-03-01 cash 4\n"
    out = lines(cli(book(tmp_path, content), "--month", "2026-02", "--account", "cash"))
    assert [row.split() for row in out] == [["cash", "5.00"], ["TOTAL", "5.00"]]


def test_account_with_nothing_left_prints_only_the_total(tmp_path):
    """`python -m ledger book.txt --account nobody` for an account with no transactions should print only
    `TOTAL 0.00`."""
    out = lines(cli(book(tmp_path, "2026-01-02 cash 5\n"), "--account", "nobody"))
    assert [row.split() for row in out] == [["TOTAL", "0.00"]]


def test_csv_has_no_total_row(tmp_path):
    """`python -m ledger book.txt --format csv` should print `account,balance` and one row per account, with two
    decimals and no total row."""
    out = lines(cli(book(tmp_path, "2026-01-02 cash 5\n2026-01-03 bank 1.5\n"), "--format", "csv"))
    assert out == ["account,balance", "bank,1.50", "cash,5.00"]


def test_notes_keep_their_spaces():
    """parse() should keep a note with several words whole: `2026-01-02 cash 5 lunch with Sam` has the note
    `lunch with Sam`."""
    core = api("ledger.core")
    assert core.parse("2026-01-02 cash 5 lunch with Sam\n")[0].note == "lunch with Sam"
''',
    change="Add `--sort balance` to the CLI: accounts are then listed from the highest balance to the lowest, ties by "
    "name. The default order stays by name. Add a test and run the tests.",
    change_checks=r"""
def test_sort_by_balance(tmp_path):
    path = tmp_path / "book.txt"
    path.write_text("2026-01-02 a 1\n2026-01-02 b 7\n2026-01-02 c 7\n2026-01-02 d -2\n")
    out = [row.split()[0] for row in lines(cli(path, "--sort", "balance"))]
    assert out == ["b", "c", "a", "d", "TOTAL"]
    assert [row.split()[0] for row in lines(cli(path))] == ["a", "b", "c", "d", "TOTAL"]
""",
)

MDTOC = Task(
    id="mdtoc",
    package="mdtoc",
    style="free",
    prompt="""Build a Python package `mdtoc` in this folder. It makes a table of contents for a Markdown file.
1. mdtoc/core.py: headings(text) returns (level, title) for every ATX heading (`#` to `######`), in order. Lines
   inside fenced code blocks (``` or ~~~) are not headings.
2. slug(title) gives the GitHub anchor: lower case, each space becomes a hyphen, and every character except
   letters, digits, hyphens and underscores is removed. toc(text) returns the list as Markdown, one
   `- [Title](#slug)` line per heading, indented two spaces per level below the smallest level present.
3. pytest tests in tests/, at least 10.
4. Duplicate titles: the second gets `-1` appended to its slug, the third `-2`, and so on, as GitHub does.
5. A CLI: `python -m mdtoc FILE [--max-depth N] [--min-depth N]` prints the list. Headings deeper than --max-depth or
   shallower than --min-depth are left out; duplicates are still numbered over all the headings of the file.
6. `--write` puts the list into the file between the lines `<!-- toc -->` and `<!-- /toc -->`, replacing what is
   there; running it a second time changes nothing. Without both markers it exits with code 1 with a message on
   stderr and leaves the file alone.
7. README.md, with its own table of contents written by the tool, and CHANGELOG.md.
"""
    + ALL_STEPS,
    checks=r'''
def doc(tmp_path, content):
    path = tmp_path / "doc.md"
    path.write_text(content)
    return path


def test_tilde_fences_hide_headings(tmp_path):
    """A `# line` inside a code block fenced with ~~~ is not a heading. `python -m mdtoc doc.md` lists it."""
    out = lines(cli(doc(tmp_path, "# Real\n\n~~~\n# not a heading\n~~~\n\n## Also real\n")))
    assert out == ["- [Real](#real)", "  - [Also real](#also-real)"]


def test_slug_removes_punctuation_and_keeps_each_space():
    """slug("C++ & Rust") should be `c--rust`: each space becomes a hyphen and the other characters are removed.
    slug("Hello, World!") should be `hello-world`."""
    core = api("mdtoc.core")
    assert core.slug("C++ & Rust") == "c--rust" and core.slug("Hello, World!") == "hello-world"


def test_duplicates_are_numbered(tmp_path):
    """Three headings called Setup should get the anchors #setup, #setup-1 and #setup-2."""
    out = lines(cli(doc(tmp_path, "# Setup\n\n# Setup\n\n# Setup\n")))
    assert out == ["- [Setup](#setup)", "- [Setup](#setup-1)", "- [Setup](#setup-2)"]


def test_duplicates_are_numbered_over_the_whole_file(tmp_path):
    """With `--max-depth 2`, a heading left out of the list still counts when duplicates are numbered: for the
    headings `# A`, `### A`, `## A` the list should be `[A](#a)` and `[A](#a-2)`, since the anchors in the page
    are a, a-1 and a-2. It prints #a-1 for the last one."""
    out = lines(cli(doc(tmp_path, "# A\n\n### A\n\n## A\n"), "--max-depth", "2"))
    assert out == ["- [A](#a)", "  - [A](#a-2)"]


def test_min_depth(tmp_path):
    """`--min-depth 2` should leave out level-1 headings and indent from the smallest level that is left."""
    out = lines(cli(doc(tmp_path, "# Title\n\n## One\n\n### Two\n"), "--min-depth", "2"))
    assert out == ["- [One](#one)", "  - [Two](#two)"]


def test_write_is_idempotent(tmp_path):
    """`python -m mdtoc doc.md --write` twice in a row should leave the file as the first run made it. The second
    run changes it."""
    path = doc(tmp_path, "# Title\n\n<!-- toc -->\n<!-- /toc -->\n\n## One\n\n## Two\n")
    assert cli(path, "--write").returncode == 0
    first = path.read_text()
    assert "- [One](#one)" in first and first.index("<!-- toc -->") < first.index("[One]") < first.index("<!-- /toc")
    assert cli(path, "--write").returncode == 0 and path.read_text() == first


def test_write_without_markers_leaves_the_file_alone(tmp_path):
    """`python -m mdtoc doc.md --write` on a file without the toc markers should exit with code 1, print a message
    on stderr and not touch the file."""
    path = doc(tmp_path, "# Title\n\n## One\n")
    result = cli(path, "--write")
    assert result.returncode == 1 and result.stderr.strip() and path.read_text() == "# Title\n\n## One\n"


def test_write_with_only_one_marker_fails(tmp_path):
    """A file with `<!-- toc -->` but no `<!-- /toc -->` should be refused by --write with exit code 1 and left
    alone. It is rewritten."""
    path = doc(tmp_path, "# Title\n\n<!-- toc -->\n\n## One\n")
    assert cli(path, "--write").returncode == 1 and path.read_text() == "# Title\n\n<!-- toc -->\n\n## One\n"
''',
    change="Add `--bullet CHAR` to the CLI to choose the list marker: `-` by default, `*` and `+` are the other "
    "allowed values, and anything else is a usage error with exit code 2. Add a test and run the tests.",
    change_checks=r"""
def test_bullet(tmp_path):
    path = tmp_path / "doc.md"
    path.write_text("# One\n\n## Two\n")
    assert lines(cli(path, "--bullet", "*")) == ["* [One](#one)", "  * [Two](#two)"]
    assert lines(cli(path)) == ["- [One](#one)", "  - [Two](#two)"]
    assert cli(path, "--bullet", "x").returncode == 2
""",
)

SEMVER = Task(
    id="semver",
    package="semverlite",
    style="tools",
    prompt="""Build a Python package `semverlite` in this folder.
1. semverlite/core.py: parse(text) returns Version(major, minor, patch, prerelease, build) for a Semantic
   Versioning 2.0.0 string and raises ValueError for anything else (numbers with leading zeros are invalid, as the
   spec says). str(version) gives the string back.
2. compare(a, b) returns -1, 0 or 1 by semver precedence: build metadata is ignored; a prerelease is lower than its
   release; prerelease identifiers compare left to right, numeric ones as numbers and lower than alphanumeric ones,
   and a shorter list is lower when everything before is equal. Versions also support <, == and sorted().
3. pytest tests in tests/, at least 12.
4. bump(version, part) for part in major, minor, patch, prerelease. major, minor and patch reset the lower parts and
   drop prerelease and build. prerelease increments the last numeric identifier (1.0.0-rc.1 becomes 1.0.0-rc.2),
   appends `.1` when there is none (1.0.0-rc becomes 1.0.0-rc.1), and on a release bumps patch and adds `-0`
   (1.2.3 becomes 1.2.4-0).
5. satisfies(version, spec) for specs made of comparators joined by spaces, which must all hold: `>=1.2.0`,
   `<2.0.0`, `=1.2.3`, `1.2.3`, `^1.2.3` (>=1.2.3 <2.0.0; ^0.2.3 means >=0.2.3 <0.3.0; ^0.0.3 means >=0.0.3
   <0.0.4) and `~1.2.3` (>=1.2.3 <1.3.0). A prerelease version satisfies a spec only when one of its comparators
   names a prerelease of the same major.minor.patch.
6. A CLI: `python -m semverlite compare A B` prints -1, 0 or 1; `bump VERSION PART` prints the result;
   `satisfies VERSION SPEC` prints true or false and exits with code 0 or 1; `sort V...` prints them lowest first,
   one per line. Invalid input exits with code 2 with a message on stderr. Then README.md and CHANGELOG.md.
"""
    + ALL_STEPS
    + FILE_TOOLS,
    checks=r'''
def compare(a, b):
    return int(cli("compare", a, b).stdout.strip())


def test_numeric_identifiers_compare_as_numbers():
    """`python -m semverlite compare 1.0.0-2 1.0.0-10` should print -1: numeric prerelease identifiers compare as
    numbers. It prints 1."""
    assert compare("1.0.0-2", "1.0.0-10") == -1


def test_numeric_identifiers_are_lower_than_alphanumeric():
    """`python -m semverlite compare 1.0.0-alpha.1 1.0.0-alpha.beta` should print -1, and a shorter prerelease is
    lower: `compare 1.0.0-alpha 1.0.0-alpha.1` is -1 too."""
    assert compare("1.0.0-alpha.1", "1.0.0-alpha.beta") == -1 and compare("1.0.0-alpha", "1.0.0-alpha.1") == -1


def test_build_metadata_is_ignored():
    """`python -m semverlite compare 1.0.0+a 1.0.0+b` should print 0: build metadata does not count."""
    assert compare("1.0.0+a", "1.0.0+b") == 0 and compare("1.0.0-rc.1+x", "1.0.0") == -1


def test_leading_zeros_are_invalid():
    """`python -m semverlite compare 01.2.3 1.2.3` should exit with code 2: a number with a leading zero is not a
    valid version, and neither is the prerelease identifier in 1.2.3-01."""
    assert cli("compare", "01.2.3", "1.2.3").returncode == 2 and cli("compare", "1.2.3-01", "1.2.3").returncode == 2


def test_bump_prerelease():
    """`python -m semverlite bump 1.0.0-rc prerelease` should print 1.0.0-rc.1, `bump 1.0.0-rc.1 prerelease`
    1.0.0-rc.2, and `bump 1.2.3 prerelease` 1.2.4-0."""
    bump = lambda version: cli("bump", version, "prerelease").stdout.strip()
    assert (bump("1.0.0-rc"), bump("1.0.0-rc.1"), bump("1.2.3")) == ("1.0.0-rc.1", "1.0.0-rc.2", "1.2.4-0")


def test_bump_minor_drops_prerelease_and_build():
    """`python -m semverlite bump 1.2.3-rc.1+build minor` should print 1.3.0."""
    assert cli("bump", "1.2.3-rc.1+build", "minor").stdout.strip() == "1.3.0"


def test_caret_below_one():
    """`python -m semverlite satisfies 0.3.0 ^0.2.3` should print false and exit with code 1, since ^0.2.3 means
    >=0.2.3 <0.3.0; and 0.0.4 does not satisfy ^0.0.3."""
    result = cli("satisfies", "0.3.0", "^0.2.3")
    assert result.stdout.strip() == "false" and result.returncode == 1
    assert cli("satisfies", "0.2.9", "^0.2.3").returncode == 0 and cli("satisfies", "0.0.4", "^0.0.3").returncode == 1


def test_prereleases_need_a_comparator_that_names_one():
    """`python -m semverlite satisfies 1.3.0-beta ">=1.2.0"` should print false: a prerelease satisfies a spec only
    when a comparator names a prerelease of the same major.minor.patch. `satisfies 1.2.3-beta.2
    ">=1.2.3-beta.1 <2.0.0"` should print true."""
    assert cli("satisfies", "1.3.0-beta", ">=1.2.0").returncode == 1
    assert cli("satisfies", "1.2.3-beta.2", ">=1.2.3-beta.1 <2.0.0").returncode == 0


def test_sort():
    """`python -m semverlite sort 1.0.0 1.0.0-rc.1 0.9.0 1.0.0-alpha` should print 0.9.0, 1.0.0-alpha, 1.0.0-rc.1
    and 1.0.0, one per line."""
    out = lines(cli("sort", "1.0.0", "1.0.0-rc.1", "0.9.0", "1.0.0-alpha"))
    assert out == ["0.9.0", "1.0.0-alpha", "1.0.0-rc.1", "1.0.0"]
''',
    change="Add `diff A B` to the CLI and a diff(a, b) function: it gives the first part in which the two versions "
    "differ (major, minor, patch or prerelease), or `none` when they have the same precedence. Add a test and run "
    "the tests.",
    change_checks=r"""
def test_diff():
    out = lambda a, b: cli("diff", a, b).stdout.strip()
    assert out("1.2.3", "2.0.0") == "major" and out("1.2.3", "1.3.0") == "minor" and out("1.2.3", "1.2.4") == "patch"
    assert out("1.2.3-rc.1", "1.2.3") == "prerelease" and out("1.2.3+a", "1.2.3+b") == "none"
""",
)

KVLOG = Task(
    id="kvlog",
    package="kvlog",
    style="scripts",
    prompt="""Build a Python package `kvlog` in this folder: a key-value store kept in an append-only log file.
1. kvlog/store.py: Store(path) with set(key, value), get(key, default=None), delete(key) and keys(). Every change
   appends one JSON line to the file, `{"op": "set", "key": ..., "value": ...}` or `{"op": "del", "key": ...}`;
   opening a Store replays the file. Keys and values are strings.
2. A CLI: `python -m kvlog FILE set KEY VALUE`; `get KEY` prints the value (exit code 1 and nothing printed when the
   key is missing); `del KEY` (exit code 1 when missing); `keys` prints them sorted, one per line.
3. pytest tests in tests/, at least 10.
4. compact() rewrites the file with one set line per live key, in sorted key order, through a temporary file in the
   same folder that replaces the original with os.replace. `python -m kvlog FILE compact` prints `N -> M lines`.
5. Robustness: a last line cut off in the middle (a crash while writing) is ignored on replay and removed by the next
   write; a broken line anywhere else raises CorruptLog with its 1-based line number, and the CLI prints the message
   on stderr and exits with code 3. Keys and values may hold spaces, newlines and non-ASCII text.
6. `python -m kvlog FILE dump --format json|env`: json prints one object of all live pairs with sorted keys; env
   prints `KEY=VALUE` lines sorted by key, leaving out keys that are not valid shell names
   ([A-Za-z_][A-Za-z0-9_]*) and quoting values with shlex.quote.
7. A script scripts/smoke.sh that runs every command against a temporary file and exits non-zero at the first
   failure; run it. Then README.md and CHANGELOG.md.
"""
    + ALL_STEPS,
    checks=r'''
def test_a_cut_off_last_line_is_ignored_and_removed(tmp_path):
    """A log whose last line was cut off by a crash should still open: the line is ignored, and the next write
    removes it. After `python -m kvlog db set b 2` on such a file, every line of the file should be valid JSON."""
    path = tmp_path / "db"
    path.write_text('{"op": "set", "key": "a", "value": "1"}\n{"op": "set", "key": "x", "val')
    assert cli(path, "get", "a").stdout.strip() == "1"
    assert cli(path, "set", "b", "2").returncode == 0
    rows = [json.loads(line) for line in path.read_text().splitlines()]
    assert [row["key"] for row in rows] == ["a", "b"]


def test_a_broken_line_in_the_middle_is_corruption(tmp_path):
    """A broken line that is not the last one should make `python -m kvlog db keys` exit with code 3 and print a
    message with the line number (`line 2`) on stderr."""
    path = tmp_path / "db"
    path.write_text('{"op": "set", "key": "a", "value": "1"}\nnot json\n{"op": "set", "key": "b", "value": "2"}\n')
    result = cli(path, "keys")
    assert result.returncode == 3 and "line 2" in result.stderr


def test_compact(tmp_path):
    """`python -m kvlog db compact` should print `N -> M lines`, leave one set line per live key in sorted key
    order with the latest values, and leave no temporary file behind."""
    path = tmp_path / "db"
    for args in (("set", "b", "1"), ("set", "a", "1"), ("set", "b", "2"), ("set", "c", "3"), ("del", "c")):
        assert cli(path, *args).returncode == 0
    assert cli(path, "compact").stdout.strip() == "5 -> 2 lines"
    rows = [json.loads(line) for line in path.read_text().splitlines()]
    assert [(row["op"], row["key"], row["value"]) for row in rows] == [("set", "a", "1"), ("set", "b", "2")]
    assert [p.name for p in tmp_path.iterdir()] == ["db"]


def test_keys_and_values_with_newlines(tmp_path):
    """Keys and values with newlines and non-ASCII text should round-trip: after `set "a b" "x\ny é"`, `get "a b"`
    should print the value back."""
    path = tmp_path / "db"
    assert cli(path, "set", "a b", "x\ny é").returncode == 0
    assert cli(path, "get", "a b").stdout.rstrip("\n") == "x\ny é"
    assert len(path.read_text().splitlines()) == 1


def test_get_missing_prints_nothing(tmp_path):
    """`python -m kvlog db get nope` should exit with code 1 and print nothing on stdout."""
    result = cli(tmp_path / "db", "get", "nope")
    assert result.returncode == 1 and result.stdout == ""


def test_del_missing_exits_with_1(tmp_path):
    """`python -m kvlog db del nope` should exit with code 1 and append nothing to the file."""
    path = tmp_path / "db"
    cli(path, "set", "a", "1")
    before = path.read_text()
    assert cli(path, "del", "nope").returncode == 1 and path.read_text() == before


def test_dump_env_quotes_and_skips(tmp_path):
    """`python -m kvlog db dump --format env` should print KEY=VALUE lines sorted by key, quote values with
    shlex.quote and leave out keys that are not valid shell names such as `my-key` or `1st`."""
    path = tmp_path / "db"
    for key, value in (("B", "two words"), ("A", "plain"), ("my-key", "x"), ("1st", "y"), ("_c", "it's")):
        cli(path, "set", key, value)
    assert lines(cli(path, "dump", "--format", "env")) == ["A=plain", "B='two words'", "_c='it'\"'\"'s'"]


def test_dump_json_is_sorted(tmp_path):
    """`python -m kvlog db dump --format json` should print one object with the live pairs, keys sorted."""
    path = tmp_path / "db"
    for key, value in (("b", "2"), ("a", "1"), ("c", "3")):
        cli(path, "set", key, value)
    cli(path, "del", "c")
    out = cli(path, "dump", "--format", "json").stdout
    assert json.loads(out) == {"a": "1", "b": "2"} and out.index('"a"') < out.index('"b"')
''',
    change="Add `python -m kvlog FILE rename OLD NEW`: it moves the value to the new key by appending one line "
    '`{"op": "rename", "key": OLD, "to": NEW}`, exits with code 1 when OLD is missing, and overwrites NEW when it '
    "exists. Replay and compact must handle it. Add a test and run the tests.",
    change_checks=r"""
def test_rename(tmp_path):
    path = tmp_path / "db"
    cli(path, "set", "a", "1")
    cli(path, "set", "b", "2")
    assert cli(path, "rename", "a", "b").returncode == 0
    assert json.loads(path.read_text().splitlines()[-1]) == {"op": "rename", "key": "a", "to": "b"}
    assert lines(cli(path, "keys")) == ["b"] and cli(path, "get", "b").stdout.strip() == "1"
    assert cli(path, "rename", "nope", "c").returncode == 1
    assert cli(path, "compact").returncode == 0 and cli(path, "get", "b").stdout.strip() == "1"
""",
)

URLNORM = Task(
    id="urlnorm",
    package="urlnorm",
    style="free",
    prompt="""Build a Python package `urlnorm` in this folder.
1. urlnorm/core.py: normalize(url) returns the URL with the scheme and host in lower case, the default port removed
   (80 for http, 443 for https), an empty path turned into `/`, and the fragment removed. It raises ValueError when
   the scheme is not http or https, or there is no host.
2. Path: dot segments (`.` and `..`) are resolved as RFC 3986 says and never go above the root; repeated slashes are
   kept. Percent-encodings are upper-cased (%2f becomes %2F), and those of unreserved characters (letters, digits,
   `-`, `.`, `_`, `~`) are decoded.
3. pytest tests in tests/, at least 12.
4. Query: parameters keep their order unless sort_query=True is given, which sorts them by name, then by value. A
   parameter without `=` stays without it, and an empty query loses its `?`. normalize(url, drop=("utm_source",))
   removes the named parameters; a name that ends in `*` is a prefix (`utm_*`).
5. same(a, b) says whether two URLs normalize to the same string with the query sorted. A CLI:
   `python -m urlnorm URL... [--sort-query] [--drop NAME]...` prints one normalized URL per line; an invalid URL
   prints `error: <message>` on stderr, the others are still printed, and the exit code is 1.
6. `python -m urlnorm --dedupe` reads URLs from stdin, one per line, and prints each distinct normalized URL once, in
   first-seen order, skipping blank lines. Then README.md and CHANGELOG.md.
"""
    + ALL_STEPS,
    checks=r'''
def norm(url, **options):
    return api("urlnorm.core").normalize(url, **options)


def test_default_port_goes_only_with_its_scheme():
    """normalize("http://a:443/") should keep the port, since 443 is the default for https only, and
    normalize("https://a:443/x") should drop it. The port is handled the same way for both schemes."""
    assert norm("http://a:443/") == "http://a:443/" and norm("https://a:443/x") == "https://a/x"


def test_dot_segments_stop_at_the_root():
    """normalize("http://a/../../x") should be `http://a/x`, and `http://a/b/./c/../d` should be `http://a/b/d`."""
    assert norm("http://a/../../x") == "http://a/x" and norm("http://a/b/./c/../d") == "http://a/b/d"


def test_percent_encodings():
    """normalize("http://a/%7euser/%2fx") should be `http://a/~user/%2Fx`: the tilde is unreserved and is decoded,
    and the encoded slash stays encoded, in upper case."""
    assert norm("http://a/%7euser/%2fx") == "http://a/~user/%2Fx"


def test_repeated_slashes_are_kept():
    """normalize("http://a//b///c") should keep the repeated slashes. They are collapsed."""
    assert norm("http://a//b///c") == "http://a//b///c"


def test_query_details():
    """A parameter without `=` should stay without it (`http://a/?flag&x=1` stays as it is) and an empty query
    should lose its question mark (`http://a/?` becomes `http://a/`)."""
    assert norm("http://a/?flag&x=1") == "http://a/?flag&x=1" and norm("http://a/?") == "http://a/"


def test_drop_with_a_prefix():
    """normalize("http://a/?utm_source=x&id=1&utm_medium=y", drop=("utm_*",)) should be `http://a/?id=1`."""
    assert norm("http://a/?utm_source=x&id=1&utm_medium=y", drop=("utm_*",)) == "http://a/?id=1"


def test_sort_query_by_name_then_value():
    """normalize("http://a/?b=2&a=2&a=1", sort_query=True) should be `http://a/?a=1&a=2&b=2`."""
    assert norm("http://a/?b=2&a=2&a=1", sort_query=True) == "http://a/?a=1&a=2&b=2"


def test_no_host_is_an_error():
    """normalize("http:///path") has no host and should raise ValueError, as normalize("ftp://a/") does."""
    for bad in ("http:///path", "ftp://a/"):
        with pytest.raises(ValueError):
            norm(bad)


def test_cli_goes_on_after_an_invalid_url():
    """`python -m urlnorm http://A/ ftp://x/ http://b` should print `http://a/` and `http://b/`, print an
    `error: ...` line on stderr for the ftp URL, and exit with code 1."""
    result = cli("http://A/", "ftp://x/", "http://b")
    assert lines(result) == ["http://a/", "http://b/"] and "error:" in result.stderr and result.returncode == 1


def test_dedupe():
    """`python -m urlnorm --dedupe` should print each distinct normalized URL once in first-seen order and skip
    blank lines: for `http://A/`, a blank line, `http://a`, `http://b/x`, `HTTP://B/x` it prints two lines."""
    result = cli("--dedupe", stdin="http://A/\n\nhttp://a\nhttp://b/x\nHTTP://B/x\n")
    assert lines(result) == ["http://a/", "http://b/x"]
''',
    change="Add `--strip-www` to the CLI and normalize(url, strip_www=True): a leading `www.` is removed from the "
    "host, but only when what remains still has a dot (`www.example.com` becomes `example.com`; `www.com` stays). "
    "Add a test and run the tests.",
    change_checks=r"""
def test_strip_www():
    assert lines(cli("http://www.Example.com/a", "http://www.com/", "--strip-www")) == [
        "http://example.com/a",
        "http://www.com/",
    ]
    assert lines(cli("http://www.example.com/a")) == ["http://www.example.com/a"]
""",
)

LOGSTAT = Task(
    id="logstat",
    package="logstat",
    style="scripts",
    prompt="""Build a Python package `logstat` in this folder. It summarizes web access logs in Common Log Format, e.g.
`127.0.0.1 - - [10/Oct/2026:13:55:36 +0000] "GET /a/b?x=1 HTTP/1.1" 200 2326`.
1. logstat/parse.py: parse_line(line) returns an Entry(ip, time, method, path, status, size), or None when the line
   does not match. time is a timezone-aware datetime, path leaves out the query string, and a size of `-` is 0.
2. A script tools/gen_log.py that writes a sample log of 500 lines to sample.log with a fixed random seed, a few
   malformed lines among them; run it.
3. logstat/stats.py: summarize(entries) returns a dict with requests, bytes, by_status (counts per status class,
   `2xx` to `5xx`, only the classes that occur) and top_paths (the 5 most requested as [path, count], ties by path).
4. pytest tests in tests/, at least 10.
5. A CLI: `python -m logstat FILE [--json]`. Text output has the lines `requests: N`, `bytes: N`, `malformed: N`, one
   `2xx: N` style line per class that occurs, and the top paths. --json prints one object with the keys requests,
   bytes, malformed, by_status and top_paths.
6. `--since` and `--until` take an ISO 8601 time. An entry is kept when since <= time < until, compared as instants,
   so a log written in +0200 and a bound given in UTC work together. A bound without an offset is UTC.
7. `--percentiles` adds size_p50, size_p90 and size_p99 to the output by the nearest-rank method: the value at
   position ceil(p / 100 * n), counted from 1, of the sorted sizes; with no entries they are 0. Then README.md and
   CHANGELOG.md.
"""
    + ALL_STEPS,
    checks=r'''
LOG = '{ip} - - [10/Oct/2026:{time} {zone}] "GET {path} HTTP/1.1" {status} {size}'


def log(tmp_path, *rows):
    path = tmp_path / "access.log"
    rows = [LOG.format(ip="1.2.3.4", **{"zone": "+0000", "status": 200, "size": 10, **row}) for row in rows]
    path.write_text("\n".join(rows) + "\n")
    return path


def summary(path, *args):
    return json.loads(cli(path, "--json", *args).stdout)


def test_path_leaves_out_the_query(tmp_path):
    """`/a/b?x=1` and `/a/b?x=2` are the same path, `/a/b`. `python -m logstat access.log --json` lists them as two
    paths."""
    path = log(tmp_path, {"time": "10:00:00", "path": "/a/b?x=1"}, {"time": "10:00:01", "path": "/a/b?x=2"})
    assert summary(path)["top_paths"] == [["/a/b", 2]]


def test_a_dash_size_is_zero(tmp_path):
    """A line whose size is `-` should count as a request of 0 bytes, not as a malformed line."""
    path = log(tmp_path, {"time": "10:00:00", "path": "/", "size": "-"}, {"time": "10:00:01", "path": "/", "size": 5})
    data = summary(path)
    assert (data["requests"], data["bytes"], data["malformed"]) == (2, 5, 0)


def test_malformed_lines_are_counted(tmp_path):
    """Lines that are not in Common Log Format should be counted in `malformed` and left out of `requests`."""
    path = log(tmp_path, {"time": "10:00:00", "path": "/"})
    path.write_text(path.read_text() + "garbage line\n\x00\x01 nonsense\n")
    data = summary(path)
    assert (data["requests"], data["malformed"]) == (1, 2)


def test_bounds_compare_instants_across_time_zones(tmp_path):
    """`--since 2026-10-10T10:00:00 --until 2026-10-10T11:00:00` should keep an entry logged at 12:00:00 +0200,
    which is 10:00 UTC, and one at 10:30 UTC, and leave out one at exactly 11:00 UTC, since until is exclusive.
    It counts a different number of requests."""
    path = log(
        tmp_path,
        {"time": "12:00:00", "zone": "+0200", "path": "/a"},
        {"time": "10:30:00", "path": "/b"},
        {"time": "11:00:00", "path": "/c"},
        {"time": "09:59:59", "path": "/d"},
    )
    data = summary(path, "--since", "2026-10-10T10:00:00", "--until", "2026-10-10T11:00:00")
    assert data["requests"] == 2 and sorted(p for p, _ in data["top_paths"]) == ["/a", "/b"]


def test_percentiles_use_the_nearest_rank(tmp_path):
    """With ten requests of sizes 1 to 10, `--percentiles` should give size_p50 = 5, size_p90 = 9 and
    size_p99 = 10 by the nearest-rank method. It gives interpolated values."""
    path = log(tmp_path, *[{"time": f"10:00:{i:02d}", "path": "/", "size": i} for i in range(1, 11)])
    data = summary(path, "--percentiles")
    assert (data["size_p50"], data["size_p90"], data["size_p99"]) == (5, 9, 10)


def test_percentiles_of_nothing_are_zero(tmp_path):
    """`--percentiles` on a log with no valid entries should print 0 for the three percentiles and exit with
    code 0. It fails."""
    path = tmp_path / "access.log"
    path.write_text("garbage\n")
    result = cli(path, "--json", "--percentiles")
    data = json.loads(result.stdout)
    assert result.returncode == 0 and (data["size_p50"], data["size_p90"], data["size_p99"]) == (0, 0, 0)


def test_top_paths_break_ties_by_path(tmp_path):
    """Paths with the same number of requests should be listed in alphabetical order."""
    path = log(tmp_path, *[{"time": f"10:00:{i:02d}", "path": p} for i, p in enumerate(["/c", "/a", "/b", "/a"])])
    assert summary(path)["top_paths"] == [["/a", 2], ["/b", 1], ["/c", 1]]


def test_by_status_has_only_the_classes_that_occur(tmp_path):
    """`by_status` should hold only the status classes that occur: for one 200 and one 404 it is
    {"2xx": 1, "4xx": 1}."""
    path = log(tmp_path, {"time": "10:00:00", "path": "/"}, {"time": "10:00:01", "path": "/x", "status": 404})
    assert summary(path)["by_status"] == {"2xx": 1, "4xx": 1}
''',
    change="Add `--status CLASS` to the CLI, for example `--status 4xx` or `--status 404`: only entries of that status "
    "class or exact status are counted. Malformed lines are still counted in `malformed`. Add a test and run the "
    "tests.",
    change_checks=r"""
def test_status_filter(tmp_path):
    row = '1.2.3.4 - - [10/Oct/2026:10:00:0{n} +0000] "GET /p{n} HTTP/1.1" {status} 10'
    path = tmp_path / "access.log"
    rows = [row.format(n=n, status=status) for n, status in enumerate([200, 404, 403, 500])]
    path.write_text("\n".join(rows) + "\nbroken\n")
    data = json.loads(cli(path, "--json", "--status", "4xx").stdout)
    assert (data["requests"], data["malformed"], data["by_status"]) == (2, 1, {"4xx": 2})
    assert json.loads(cli(path, "--json", "--status", "404").stdout)["requests"] == 1
""",
)

TODO = Task(
    id="todo",
    package="todo",
    style="tools",
    prompt="""Build a Python package `todo` in this folder: a to-do list kept in a JSON file.
1. todo/store.py: TodoList(path) with add(title, due=None, priority="normal") returning the new id, done(id),
   remove(id) and items(). Ids are integers that start at 1 and are never used again, even after remove. The file
   holds {"next_id": N, "items": [...]} and is written after every change.
2. A CLI: `python -m todo --file F add TITLE [--due YYYY-MM-DD] [--priority low|normal|high]` prints the id; `done ID`
   and `rm ID` exit with code 1 and a message on stderr for an unknown id; `list` prints one line per open item,
   `ID [P] TITLE (due DATE)`, with P as L, N or H and the due part only when a date is set.
3. pytest tests in tests/, at least 10.
4. The order of `list`: high before normal before low; within a priority, items with a due date first, earliest
   first, then those without; what is still tied goes by id. `list --all` also shows done items, after the open
   ones, with `x` in place of the priority letter.
5. `list --due-before YYYY-MM-DD` keeps the open items due strictly before that date; items without a due date are
   left out. `list --json` prints the listed items as a JSON array of objects with id, title, due, priority and done.
6. Validation: an empty or whitespace-only title, a date that does not exist (2026-02-30) and an unknown priority
   are usage errors with exit code 2, and nothing is written to the file. A file that is not valid JSON makes every
   command exit with code 3 and is left as it is.
7. `python -m todo --file F stats [--today YYYY-MM-DD]` prints `open: N`, `done: N` and `overdue: N`, the open items
   due before today; --today replaces today's date. Then README.md and CHANGELOG.md.
"""
    + ALL_STEPS
    + FILE_TOOLS,
    checks=r'''
def todo(tmp_path, *args):
    return cli("--file", tmp_path / "todo.json", *args)


def ids(result):
    return [int(line.split()[0]) for line in lines(result)]


def test_ids_are_never_used_again(tmp_path):
    """After `add a`, `add b` and `rm 2`, the next `add c` should print 3: an id is never used again. It
    prints 2."""
    todo(tmp_path, "add", "a")
    todo(tmp_path, "add", "b")
    todo(tmp_path, "rm", "2")
    assert todo(tmp_path, "add", "c").stdout.strip() == "3"


def test_list_order(tmp_path):
    """`list` should put high before normal before low, and within a priority the items with a due date first,
    earliest first, then the ones without. It lists them in another order."""
    todo(tmp_path, "add", "l", "--priority", "low")
    todo(tmp_path, "add", "n1", "--due", "2026-03-01")
    todo(tmp_path, "add", "n2")
    todo(tmp_path, "add", "n3", "--due", "2026-02-01")
    todo(tmp_path, "add", "h", "--priority", "high")
    assert ids(todo(tmp_path, "list")) == [5, 4, 2, 3, 1]


def test_list_all_puts_done_items_last_with_an_x(tmp_path):
    """`list --all` should show done items after the open ones, with `[x]` where the priority letter goes."""
    todo(tmp_path, "add", "first", "--priority", "high")
    todo(tmp_path, "add", "second")
    todo(tmp_path, "done", "1")
    out = lines(todo(tmp_path, "list", "--all"))
    assert ids(todo(tmp_path, "list", "--all")) == [2, 1] and "[x]" in out[1] and "[N]" in out[0]
    assert ids(todo(tmp_path, "list")) == [2]


def test_due_before_is_strict(tmp_path):
    """`list --due-before 2026-02-02` should keep an item due on 2026-02-01 and leave out one due on 2026-02-02 and
    one without a due date."""
    todo(tmp_path, "add", "a", "--due", "2026-02-01")
    todo(tmp_path, "add", "b", "--due", "2026-02-02")
    todo(tmp_path, "add", "c")
    assert ids(todo(tmp_path, "list", "--due-before", "2026-02-02")) == [1]


def test_a_date_that_does_not_exist_is_a_usage_error(tmp_path):
    """`add x --due 2026-02-30` should be a usage error with exit code 2 that writes nothing to the file."""
    todo(tmp_path, "add", "a")
    before = (tmp_path / "todo.json").read_text()
    assert todo(tmp_path, "add", "x", "--due", "2026-02-30").returncode == 2
    assert (tmp_path / "todo.json").read_text() == before


def test_a_blank_title_is_a_usage_error(tmp_path):
    """`add "   "` should be a usage error with exit code 2: a title of spaces only is empty."""
    assert todo(tmp_path, "add", "   ").returncode == 2 and not (tmp_path / "todo.json").exists()


def test_a_file_that_is_not_json_is_left_alone(tmp_path):
    """When the file is not valid JSON, every command should exit with code 3 and leave the file as it is."""
    (tmp_path / "todo.json").write_text("{not json")
    assert todo(tmp_path, "add", "a").returncode == 3 and todo(tmp_path, "list").returncode == 3
    assert (tmp_path / "todo.json").read_text() == "{not json"


def test_stats_counts_overdue_items(tmp_path):
    """`stats --today 2026-03-01` should count as overdue the open items due before that day: not one due on the
    day itself, and not a done one."""
    todo(tmp_path, "add", "late", "--due", "2026-02-28")
    todo(tmp_path, "add", "today", "--due", "2026-03-01")
    todo(tmp_path, "add", "was late", "--due", "2026-01-01")
    todo(tmp_path, "done", "3")
    assert lines(todo(tmp_path, "stats", "--today", "2026-03-01")) == ["open: 2", "done: 1", "overdue: 1"]


def test_list_json(tmp_path):
    """`list --json` should print a JSON array of objects with id, title, due, priority and done."""
    todo(tmp_path, "add", "a", "--due", "2026-02-01", "--priority", "high")
    assert json.loads(todo(tmp_path, "list", "--json").stdout) == [
        {"id": 1, "title": "a", "due": "2026-02-01", "priority": "high", "done": False}
    ]
''',
    change="Add `edit ID [--title T] [--due DATE] [--priority P]` to the CLI; `--due none` clears the due date. An "
    "unknown id exits with code 1, and the values are validated as for add. Add a test and run the tests.",
    change_checks=r"""
def test_edit(tmp_path):
    todo = lambda *args: cli("--file", tmp_path / "todo.json", *args)
    todo("add", "a", "--due", "2026-02-01")
    assert todo("edit", "1", "--title", "b", "--priority", "high", "--due", "none").returncode == 0
    assert json.loads(todo("list", "--json").stdout) == [
        {"id": 1, "title": "b", "due": None, "priority": "high", "done": False}
    ]
    assert todo("edit", "9", "--title", "x").returncode == 1
    assert todo("edit", "1", "--due", "2026-02-30").returncode == 2
""",
)

SPANS = Task(
    id="spans",
    package="spans",
    style="free",
    prompt="""Build a Python package `spans` in this folder, for half-open time spans [start, end) of one day,
written as `HH:MM-HH:MM`.
1. spans/core.py: parse(text) returns Span(start, end) in minutes since midnight and raises ValueError unless
   00:00 <= start < end <= 24:00 (24:00 is allowed only as an end). format(span) gives the text back.
2. merge(spans) returns their union as sorted spans that do not overlap; spans that touch, such as 10:00-11:00 and
   11:00-12:00, are joined.
3. pytest tests in tests/, at least 12.
4. subtract(spans, holes) removes the holes from the spans. free(busy, day, at_least=0) returns the gaps of the day
   (a Span) that busy does not cover and that last at least that many minutes.
5. common_free(people, day, at_least=0) takes one busy list per person and returns the gaps free for all of them.
6. A CLI: `python -m spans merge SPAN...` prints the merged spans, one per line; `free --day SPAN [--at-least MIN]
   SPAN...` prints the gaps; `common --day SPAN [--at-least MIN] FILE...` reads one busy span per line from each file
   (blank lines and lines starting with # are skipped) and prints the common gaps. With no gaps it prints nothing
   and exits with code 0; a span that does not parse exits with code 2 with a message on stderr that names it. Then
   README.md and CHANGELOG.md.
"""
    + ALL_STEPS,
    checks=r'''
def test_touching_spans_are_joined():
    """`python -m spans merge 10:00-11:00 11:00-12:00` should print `10:00-12:00`. It prints two spans."""
    assert lines(cli("merge", "10:00-11:00", "11:00-12:00")) == ["10:00-12:00"]


def test_merge_sorts_and_joins_overlaps():
    """`python -m spans merge 13:00-14:00 09:00-10:30 10:00-11:00` should print `09:00-11:00` and `13:00-14:00`."""
    assert lines(cli("merge", "13:00-14:00", "09:00-10:30", "10:00-11:00")) == ["09:00-11:00", "13:00-14:00"]


def test_midnight_is_an_end_only():
    """parse("23:00-24:00") should work and format back to the same text; parse("24:00-24:30") and
    parse("12:00-12:00") should raise ValueError."""
    core = api("spans.core")
    assert core.format(core.parse("23:00-24:00")) == "23:00-24:00"
    for bad in ("24:00-24:30", "12:00-12:00", "12:00-11:00", "9-10"):
        with pytest.raises(ValueError):
            core.parse(bad)


def test_free_keeps_a_gap_of_exactly_the_length():
    """`python -m spans free --day 09:00-17:00 --at-least 60 10:00-11:00 12:00-16:30` should print `09:00-10:00`
    and `11:00-12:00`: both last exactly 60 minutes. The 30 minutes after 16:30 are left out."""
    out = lines(cli("free", "--day", "09:00-17:00", "--at-least", "60", "10:00-11:00", "12:00-16:30"))
    assert out == ["09:00-10:00", "11:00-12:00"]


def test_busy_spans_outside_the_day_are_clipped():
    """`python -m spans free --day 09:00-12:00 08:00-09:30 11:30-13:00` should print only `09:30-11:30`."""
    assert lines(cli("free", "--day", "09:00-12:00", "08:00-09:30", "11:30-13:00")) == ["09:30-11:30"]


def test_common_reads_files_with_comments(tmp_path):
    """`python -m spans common --day 09:00-15:00 --at-least 30 a.txt b.txt` should skip blank lines and comment
    lines in the files and print the gaps free for both people: `11:00-13:00` and `14:00-15:00`."""
    (tmp_path / "a.txt").write_text("09:00-10:00\n# lunch\n\n13:00-14:00\n")
    (tmp_path / "b.txt").write_text("09:30-11:00\n")
    out = lines(cli("common", "--day", "09:00-15:00", "--at-least", "30", tmp_path / "a.txt", tmp_path / "b.txt"))
    assert out == ["11:00-13:00", "14:00-15:00"]


def test_no_gaps_is_not_an_error():
    """`python -m spans free --day 09:00-10:00 09:00-10:00` should print nothing and exit with code 0."""
    result = cli("free", "--day", "09:00-10:00", "09:00-10:00")
    assert result.returncode == 0 and result.stdout.strip() == ""


def test_a_span_that_does_not_parse_is_named():
    """`python -m spans merge 09:00-10:00 10:00-09:00` should exit with code 2 and name `10:00-09:00` on stderr."""
    result = cli("merge", "09:00-10:00", "10:00-09:00")
    assert result.returncode == 2 and "10:00-09:00" in result.stderr


def test_subtract_splits_a_span():
    """subtract([09:00-12:00], [10:00-11:00]) should give 09:00-10:00 and 11:00-12:00, and a hole that touches
    only the edge (12:00-13:00) should change nothing."""
    core = api("spans.core")
    spans = core.subtract([core.parse("09:00-12:00")], [core.parse("10:00-11:00"), core.parse("12:00-13:00")])
    assert [core.format(span) for span in spans] == ["09:00-10:00", "11:00-12:00"]
''',
    change="Add `python -m spans total SPAN...`: it prints the time covered by the union of the spans as `H:MM`, for "
    "example `2:30`. Add a test and run the tests.",
    change_checks=r"""
def test_total():
    assert cli("total", "09:00-10:30", "10:00-11:00", "13:00-13:30").stdout.strip() == "2:30"
    assert cli("total", "00:00-24:00").stdout.strip() == "24:00"
""",
)

TMPL = Task(
    id="tmpl",
    package="tmpl",
    style="tools",
    prompt="""Build a Python package `tmpl` in this folder: a small template engine.
1. tmpl/core.py: render(template, context) replaces `{{ name }}` with the value from the context dict; dotted names
   (`{{ user.name }}`) look into dicts, then attributes. Whitespace inside the braces is optional.
2. A missing name raises TemplateError (defined in tmpl/core.py) naming it, unless render(..., missing="") gives a
   text to use in its place.
3. pytest tests in tests/, at least 12.
4. Filters: `{{ name | upper }}`, lower, title, trim, `default("text")` (used when the value is missing, None or
   empty) and `join(", ")` for lists. Filters chain left to right. An unknown filter raises TemplateError.
5. Blocks: `{% if name %}...{% else %}...{% endif %}` (else is optional; an empty string, 0, None, an empty list and
   a missing name are false) and `{% for item in items %}...{% endfor %}`, with `loop.index` (from 1) and `loop.last`
   inside. Blocks nest.
6. Escaping: values are HTML-escaped (& < > " ') unless the `safe` filter is used on them or
   render(..., autoescape=False) is given. `{{ "{{" }}` prints literal braces, and `{# a comment #}` prints nothing.
7. A CLI: `python -m tmpl TEMPLATE_FILE --data DATA.json [--no-escape]` prints the result; an error prints
   `error: <message>` on stderr and exits with code 1. An unclosed block or tag is a TemplateError that gives the
   1-based line number where it opened (`line 3`). Then README.md and CHANGELOG.md.
"""
    + ALL_STEPS
    + FILE_TOOLS,
    checks=r'''
def render(template, context=None, **options):
    return api("tmpl.core").render(template, context or {}, **options)


def test_values_are_escaped_by_default():
    """render("{{ x }}", {"x": "<a href='u'>&\"</a>"}) should escape all five characters & < > " and '. The single
    quote comes out unescaped."""
    out = render("{{ x }}", {"x": "<a href='u'>&\"</a>"})
    assert "<" not in out and ">" not in out and "'" not in out and '"' not in out and "&amp;" in out


def test_safe_and_autoescape_off():
    """render("{{ x | safe }}", {"x": "<b>"}) should print `<b>`, and so should
    render("{{ x }}", {"x": "<b>"}, autoescape=False)."""
    assert render("{{ x | safe }}", {"x": "<b>"}) == "<b>"
    assert render("{{ x }}", {"x": "<b>"}, autoescape=False) == "<b>"


def test_default_covers_missing_none_and_empty():
    """`{{ n | default("anon") }}` should print `anon` when n is missing, when it is None and when it is an empty
    string. One of the three raises or prints something else."""
    template = '{{ n | default("anon") }}'
    assert [render(template, context) for context in ({}, {"n": None}, {"n": ""}, {"n": "Al"})] == [
        "anon",
        "anon",
        "anon",
        "Al",
    ]


def test_filters_chain_left_to_right():
    """render("{{ x | trim | upper }}", {"x": "  hi "}) should be `HI`, and
    render('{{ xs | join(", ") }}', {"xs": ["a", "b"]}) should be `a, b`."""
    assert render("{{ x | trim | upper }}", {"x": "  hi "}) == "HI"
    assert render('{{ xs | join(", ") }}', {"xs": ["a", "b"]}) == "a, b"


def test_loops_know_their_position():
    """Inside a for block, loop.index should count from 1 and loop.last be true on the last item only:
    `{% for u in users %}{{ loop.index }}:{{ u.name }}{% if loop.last %}.{% else %}, {% endif %}{% endfor %}`
    for two users a and b should print `1:a, 2:b.`"""
    template = "{% for u in users %}{{ loop.index }}:{{ u.name }}{% if loop.last %}.{% else %}, {% endif %}{% endfor %}"
    assert render(template, {"users": [{"name": "a"}, {"name": "b"}]}) == "1:a, 2:b."


def test_a_missing_name_is_false_in_an_if():
    """`{% if nope %}y{% else %}n{% endif %}` with no `nope` in the context should print `n`. It raises."""
    assert render("{% if nope %}y{% else %}n{% endif %}") == "n"
    assert render("{% if xs %}y{% endif %}", {"xs": []}) == ""


def test_a_missing_name_is_an_error_that_names_it():
    """render("{{ nope }}", {}) should raise TemplateError with `nope` in its message, and
    render("{{ nope }}", {}, missing="-") should print `-`."""
    core = api("tmpl.core")
    with pytest.raises(core.TemplateError, match="nope"):
        render("{{ nope }}")
    assert render("{{ nope }}", missing="-") == "-"


def test_an_unknown_filter_is_an_error():
    """render("{{ x | shout }}", {"x": "a"}) should raise TemplateError. It prints the value."""
    with pytest.raises(api("tmpl.core").TemplateError):
        render("{{ x | shout }}", {"x": "a"})


def test_an_unclosed_block_gives_its_line(tmp_path):
    """A template whose `{% if x %}` on line 2 is never closed should make the CLI print `error: ...` with
    `line 2` on stderr and exit with code 1."""
    (tmp_path / "t.txt").write_text("first\n{% if x %}\nbody\n")
    (tmp_path / "d.json").write_text('{"x": 1}')
    result = cli(tmp_path / "t.txt", "--data", tmp_path / "d.json")
    assert result.returncode == 1 and result.stderr.startswith("error:") and "line 2" in result.stderr


def test_literal_braces_and_comments():
    """render('{{ "{{" }}') should print two literal braces, and render("a{# note #}b") should print `ab`."""
    assert render('{{ "{{" }}') == "{{" and render("a{# note #}b") == "ab"
''',
    change="Add a `length` filter, and an `{% empty %}` part to for blocks: "
    "`{% for x in xs %}...{% empty %}...{% endfor %}` prints the empty part when the list is empty or missing. Add a "
    "test and run the tests.",
    change_checks=r"""
def test_length_and_empty():
    render = api("tmpl.core").render
    assert render("{{ xs | length }}", {"xs": [1, 2, 3]}) == "3"
    template = "{% for x in xs %}{{ x }}{% empty %}none{% endfor %}"
    assert render(template, {"xs": []}) == "none" and render(template, {}) == "none"
    assert render(template, {"xs": ["a", "b"]}) == "ab"
""",
)

NUMWORDS = Task(
    id="numwords",
    package="numwords",
    style="free",
    prompt="""Build a Python package `numwords` in this folder. It writes integers as English words.
1. numwords/core.py: words(n) for 0 to 999: `zero`, `forty-two`, `one hundred and five` (the British `and` after
   hundred).
2. Up to 999,999,999,999 with thousand, million and billion: `one thousand and five`,
   `two million three hundred thousand`, `one million and one`. `and` comes before the last tens-or-units part
   whenever something bigger precedes it, and nowhere else. Negative numbers start with `minus`. Anything that is
   not an int raises TypeError, bool included.
3. pytest tests in tests/, at least 15.
4. ordinal(n): `first`, `twenty-first`, `one hundredth`, `one hundred and second`, `two thousandth`; zero is
   `zeroth`; a negative number raises ValueError.
5. parse(text) is the inverse of words for every form words produces; it also accepts the text without `and`, in
   any capitalization and with extra spaces. Anything else raises ValueError. A CLI: `python -m numwords N...`
   prints the words for each number, one per line; `--ordinal N...` the ordinals; `--parse TEXT` the number. Bad
   input prints `error: ...` on stderr and exits with code 2. Then README.md and CHANGELOG.md.
"""
    + ALL_STEPS,
    checks=r'''
def core():
    return api("numwords.core")


def test_and_before_the_last_small_part():
    """words(1000005) should be `one million and five` and words(1105) `one thousand one hundred and five`."""
    assert core().words(1000005) == "one million and five"
    assert core().words(1105) == "one thousand one hundred and five"


def test_no_and_without_a_small_part():
    """words(1001000) should be `one million one thousand` and words(1100) `one thousand one hundred`: `and` comes
    only before a last tens-or-units part."""
    assert core().words(1001000) == "one million one thousand" and core().words(1100) == "one thousand one hundred"


def test_the_top_of_the_range():
    """words(999999999999) should work, starting with `nine hundred` and naming billion, million and thousand, and
    words(-42) should be `minus forty-two`."""
    top = core().words(999999999999)
    assert top.startswith("nine hundred") and all(scale in top for scale in ("billion", "million", "thousand"))
    assert core().words(-42) == "minus forty-two"


def test_bool_is_not_a_number():
    """words(True) should raise TypeError, as words(1.0) and words("1") do. It returns `one`."""
    for bad in (True, 1.0, "1"):
        with pytest.raises(TypeError):
            core().words(bad)


def test_irregular_ordinals():
    """ordinal(12) should be `twelfth`, ordinal(20) `twentieth`, ordinal(9) `ninth`, ordinal(5) `fifth`,
    ordinal(8) `eighth` and ordinal(40) `fortieth`."""
    got = [core().ordinal(n) for n in (12, 20, 9, 5, 8, 40)]
    assert got == ["twelfth", "twentieth", "ninth", "fifth", "eighth", "fortieth"]


def test_large_ordinals():
    """ordinal(101) should be `one hundred and first`, ordinal(1000000) `one millionth` and ordinal(0) `zeroth`;
    ordinal(-1) should raise ValueError."""
    assert core().ordinal(101) == "one hundred and first" and core().ordinal(1000000) == "one millionth"
    assert core().ordinal(0) == "zeroth"
    with pytest.raises(ValueError):
        core().ordinal(-1)


def test_parse_is_the_inverse_of_words():
    """parse(words(n)) should give n back for every n, for example 0, 19, 100, 1105, 1000005, 2300000 and -42."""
    for n in (0, 19, 100, 1105, 1000005, 2300000, 999999999999, -42):
        assert core().parse(core().words(n)) == n


def test_parse_is_lenient_about_and_case_and_spaces():
    """parse("One Hundred Five") should be 105 and parse("  two   thousand five ") 2005."""
    assert core().parse("One Hundred Five") == 105 and core().parse("  two   thousand five ") == 2005


def test_parse_rejects_nonsense():
    """parse("one one"), parse("hundred thousand million") and parse("") should raise ValueError. One of them
    returns a number."""
    for bad in ("one one", "hundred thousand million", "", "twenty-eleven"):
        with pytest.raises(ValueError):
            core().parse(bad)


def test_cli():
    """`python -m numwords 42 1000` should print `forty-two` and `one thousand`; `--ordinal 3` prints `third`;
    `--parse "forty-two"` prints 42; `python -m numwords x` prints `error: ...` on stderr and exits with code 2."""
    assert lines(cli("42", "1000")) == ["forty-two", "one thousand"]
    assert cli("--ordinal", "3").stdout.strip() == "third" and cli("--parse", "forty-two").stdout.strip() == "42"
    bad = cli("x")
    assert bad.returncode == 2 and bad.stderr.startswith("error:")
''',
    change="Add American style: words(n, american=True) and `--american` on the CLI leave out every `and` "
    "(`one hundred five`). Add a test and run the tests.",
    change_checks=r"""
def test_american():
    core = api("numwords.core")
    assert core.words(105, american=True) == "one hundred five"
    assert core.words(1000005, american=True) == "one million five" and core.words(105) == "one hundred and five"
    assert cli("--american", "1105").stdout.strip() == "one thousand one hundred five"
""",
)

TABQ = Task(
    id="tabq",
    package="tabq",
    style="scripts",
    prompt="""Build a Python package `tabq` in this folder. It queries CSV files from the command line.
1. tabq/core.py: read(path) returns (header, rows) with the csv module, so quoted fields with commas, quotes and
   newlines work. select(header, rows, columns) keeps the named columns in the given order; an unknown column raises
   KeyError naming it.
2. A CLI: `python -m tabq FILE --select a,b` prints CSV with its header; without --select all columns are printed.
   An unknown column exits with code 2 and names it on stderr.
3. A script tools/make_fixtures.sh that writes three sample CSV files into fixtures/: one with quoted commas and
   newlines inside fields, one with a header and no rows, one with numbers. Run it. Then pytest tests in tests/ that
   use them, at least 10.
4. `--where COLUMN=VALUE` (also !=, <, <=, > and >=) keeps the rows that match; several --where must all hold. A
   comparison is numeric when both sides parse as numbers, and text otherwise.
5. `--sort COLUMN`, or `--sort -COLUMN` for descending: numeric when every non-empty value of the column parses as a
   number, text otherwise. The sort is stable, and empty values come last in both directions.
6. `--count` prints only the number of rows that would be printed, the header not counted; `--limit N` keeps the
   first N rows after sorting. A file with a header and no rows prints just the header, or 0 with --count. A file
   with no header at all exits with code 2.
7. `--group COLUMN --sum COLUMN` prints the header `group,sum`, then one row per group sorted by group; a sum is
   printed as an integer when all its values are integers, and with decimals otherwise. A value that is not a number
   in the summed column exits with code 2. Then README.md and CHANGELOG.md.
"""
    + ALL_STEPS,
    checks=r'''
import csv, io


def table(tmp_path, text):
    path = tmp_path / "t.csv"
    path.write_text(text)
    return path


def rows(result):
    return list(csv.reader(io.StringIO(result.stdout)))


PEOPLE = "name,n,city\nann,9,Oslo\nbob,10,\ncy,100,Rome\ndee,9,Bern\n"


def test_fields_with_newlines_survive(tmp_path):
    """A quoted field with a comma or a newline in it should come out as one field: for the row
    `"x, y","line1<newline>line2"`, `--select b,a` should print the two fields swapped and intact."""
    path = table(tmp_path, 'a,b\n"x, y","line1\nline2"\n')
    assert rows(cli(path, "--select", "b,a")) == [["b", "a"], ["line1\nline2", "x, y"]]


def test_where_compares_numbers_as_numbers(tmp_path):
    """`--where "n>9"` should keep the rows with n = 10 and n = 100, comparing as numbers. It compares them as
    text."""
    assert [r[0] for r in rows(cli(table(tmp_path, PEOPLE), "--where", "n>9"))[1:]] == ["bob", "cy"]


def test_several_where_must_all_hold(tmp_path):
    """`--where "n>=10" --where "name!=bob"` should keep only cy."""
    out = rows(cli(table(tmp_path, PEOPLE), "--where", "n>=10", "--where", "name!=bob"))
    assert [r[0] for r in out[1:]] == ["cy"]


def test_sort_is_numeric_and_stable(tmp_path):
    """`--sort n` should order n as numbers (9, 9, 10, 100) and keep ann before dee, which have the same n."""
    assert [r[0] for r in rows(cli(table(tmp_path, PEOPLE), "--sort", "n"))[1:]] == ["ann", "dee", "bob", "cy"]


def test_empty_values_sort_last_in_both_directions(tmp_path):
    """`--sort city` and `--sort -city` should both put the row with an empty city last."""
    path = table(tmp_path, PEOPLE)
    assert [r[0] for r in rows(cli(path, "--sort", "city"))[1:]] == ["dee", "ann", "cy", "bob"]
    assert [r[0] for r in rows(cli(path, "--sort", "-city"))[1:]] == ["cy", "ann", "dee", "bob"]


def test_count_and_limit(tmp_path):
    """`--count` should print the number of rows without the header (4), and `--sort -n --limit 2` the two rows
    with the largest n."""
    path = table(tmp_path, PEOPLE)
    assert cli(path, "--count").stdout.strip() == "4"
    assert [r[0] for r in rows(cli(path, "--sort", "-n", "--limit", "2"))[1:]] == ["cy", "bob"]


def test_a_header_without_rows(tmp_path):
    """A file with a header and no rows should print just the header, and 0 with --count. A file with nothing in
    it should exit with code 2."""
    path = table(tmp_path, "a,b\n")
    assert rows(cli(path)) == [["a", "b"]] and cli(path, "--count").stdout.strip() == "0"
    assert cli(table(tmp_path, ""), "--count").returncode == 2


def test_group_and_sum(tmp_path):
    """`--group k --sum v` should print `group,sum`, then the groups in order with integer sums when all values
    are integers (a,3) and decimals otherwise (b,3.5)."""
    path = table(tmp_path, "k,v\nb,1.5\na,1\nb,2\na,2\n")
    assert rows(cli(path, "--group", "k", "--sum", "v")) == [["group", "sum"], ["a", "3"], ["b", "3.5"]]


def test_summing_text_is_an_error(tmp_path):
    """`--group k --sum v` should exit with code 2 when a value of v is not a number."""
    assert cli(table(tmp_path, "k,v\na,1\na,x\n"), "--group", "k", "--sum", "v").returncode == 2


def test_an_unknown_column_is_named(tmp_path):
    """`--select nope` should exit with code 2 and name `nope` on stderr."""
    result = cli(table(tmp_path, PEOPLE), "--select", "nope")
    assert result.returncode == 2 and "nope" in result.stderr
''',
    change="Add `--distinct COLUMN` to the CLI: it prints the distinct values of that column, one per line, in "
    "first-seen order, after --where is applied; with --count it prints how many there are. Add a test and run the "
    "tests.",
    change_checks=r"""
def test_distinct(tmp_path):
    path = tmp_path / "t.csv"
    path.write_text("name,n\nann,9\nbob,10\ncy,9\ndee,7\n")
    assert lines(cli(path, "--distinct", "n")) == ["9", "10", "7"]
    assert lines(cli(path, "--distinct", "n", "--where", "name!=bob")) == ["9", "7"]
    assert cli(path, "--distinct", "n", "--count").stdout.strip() == "3"
""",
)

CRONNEXT = Task(
    id="cronnext",
    package="cronnext",
    style="free",
    prompt="""Build a Python package `cronnext` in this folder. It works out when a cron expression fires next.
1. cronnext/core.py: parse(expr) for five fields (minute, hour, day of month, month, day of week) with `*`, lists
   (`1,15`), ranges (`1-5`) and steps (`*/15`, `10-40/10`); day of week 0 and 7 are both Sunday. It raises
   ValueError for a value out of range or a malformed field.
2. next_after(expr, moment) returns the first datetime strictly after `moment` that matches (naive datetimes, to
   the minute).
3. pytest tests in tests/, at least 12.
4. The day rule: when day of month and day of week are both restricted (neither is `*`), a day matches when either
   one does, as cron does; when only one is restricted, only that one counts.
5. Names: months `jan` to `dec` and days `sun` to `sat`, in any case, also inside lists and ranges. `@hourly`,
   `@daily`, `@weekly`, `@monthly` and `@yearly` are shorthands. An expression that can never fire, such as
   `0 0 31 2 *`, makes next_after raise ValueError instead of looping for ever.
6. A CLI: `python -m cronnext "EXPR" [--from "YYYY-MM-DD HH:MM"] [--count N]` prints the next N times, 1 by default,
   one per line as `YYYY-MM-DD HH:MM`; a bad expression prints `error: ...` on stderr and exits with code 2. Then
   README.md and CHANGELOG.md.
"""
    + ALL_STEPS,
    checks=r'''
def times(expr, start, count=1):
    return lines(cli(expr, "--from", start, "--count", count))


def test_day_of_month_or_day_of_week():
    """`python -m cronnext "0 0 15 * 5" --from "2026-11-01 00:00" --count 3` should print 2026-11-06, 2026-11-13
    and 2026-11-15 at 00:00: with both day fields restricted, a day matches when it is a Friday or the 15th."""
    assert times("0 0 15 * 5", "2026-11-01 00:00", 3) == ["2026-11-06 00:00", "2026-11-13 00:00", "2026-11-15 00:00"]


def test_only_the_restricted_day_field_counts():
    """`python -m cronnext "0 9 * * 1" --from "2026-11-01 00:00"` should print `2026-11-02 09:00`, the next
    Monday."""
    assert times("0 9 * * 1", "2026-11-01 00:00") == ["2026-11-02 09:00"]


def test_seven_is_sunday_and_the_start_itself_does_not_count():
    """`python -m cronnext "0 0 * * 7" --from "2026-11-01 00:00"` should print `2026-11-08 00:00`: 7 is Sunday,
    and 2026-11-01 00:00, a Sunday itself, is not strictly after the start."""
    assert times("0 0 * * 7", "2026-11-01 00:00") == ["2026-11-08 00:00"]


def test_a_step_over_a_range():
    """`python -m cronnext "10-40/10 * * * *" --from "2026-11-01 00:00" --count 5` should print the minutes 10,
    20, 30 and 40 of hour 0, then 01:10."""
    got = times("10-40/10 * * * *", "2026-11-01 00:00", 5)
    assert [t[-5:] for t in got] == ["00:10", "00:20", "00:30", "00:40", "01:10"]


def test_names_in_ranges():
    """`python -m cronnext "0 0 * JAN mon-fri" --from "2026-12-30 00:00"` should print `2027-01-01 00:00`, a
    Friday."""
    assert times("0 0 * JAN mon-fri", "2026-12-30 00:00") == ["2027-01-01 00:00"]


def test_weekly_shorthand():
    """`python -m cronnext "@weekly" --from "2026-11-01 00:00"` should print `2026-11-08 00:00`."""
    assert times("@weekly", "2026-11-01 00:00") == ["2026-11-08 00:00"]


def test_an_expression_that_never_fires():
    """next_after("0 0 31 2 *", datetime(2026, 1, 1)) should raise ValueError, since there is no 31 February. It
    does not return."""
    from datetime import datetime

    with pytest.raises(ValueError):
        api("cronnext.core").next_after("0 0 31 2 *", datetime(2026, 1, 1))


def test_leap_day():
    """`python -m cronnext "0 0 29 2 *" --from "2026-01-01 00:00"` should print `2028-02-29 00:00`."""
    assert times("0 0 29 2 *", "2026-01-01 00:00") == ["2028-02-29 00:00"]


def test_year_rollover():
    """`python -m cronnext "59 23 31 12 *" --from "2026-12-31 23:59"` should print `2027-12-31 23:59`."""
    assert times("59 23 31 12 *", "2026-12-31 23:59") == ["2027-12-31 23:59"]


def test_bad_expressions():
    """`python -m cronnext "61 * * * *"` and `python -m cronnext "* * * * 8"` should print `error: ...` on stderr
    and exit with code 2."""
    for bad in ("61 * * * *", "* * * * 8", "* * *"):
        result = cli(bad)
        assert result.returncode == 2 and result.stderr.startswith("error:")
''',
    change="Add `--explain` to the CLI: it prints one line per field in place of the times, `minute: 0,15,30,45` and "
    "so on, with the names minute, hour, day-of-month, month and day-of-week, and `*` for a field that allows every "
    "value. Add a test and run the tests.",
    change_checks=r"""
def test_explain():
    assert lines(cli("*/15 9 * * mon-fri", "--explain")) == [
        "minute: 0,15,30,45",
        "hour: 9",
        "day-of-month: *",
        "month: *",
        "day-of-week: 1,2,3,4,5",
    ]
""",
)

WRAPIT = Task(
    id="wrapit",
    package="wrapit",
    style="scripts",
    prompt="""Build a Python package `wrapit` in this folder. It re-wraps plain text.
1. wrapit/core.py: wrap(text, width=80) re-flows the words of each paragraph into lines of at most `width`
   characters. Paragraphs are separated by one or more blank lines and come out separated by exactly one. A word
   longer than the width goes on a line of its own, unbroken.
2. A CLI: `python -m wrapit [FILE] [--width N]` reads the file, or stdin when none is given, and prints the result.
   A width below 1 is a usage error (exit code 2).
3. pytest tests in tests/, at least 12.
4. Lists and quotes. A paragraph that starts with `- `, `* ` or a number and `. ` is a list item: its continuation
   lines are indented to line up under the item's text. A paragraph whose lines all start with `> ` keeps that
   prefix on every output line, and the prefix counts toward the width.
5. Code. Lines indented by four or more spaces, and everything between ``` fences, are passed through unchanged; a
   code line ends the paragraph before it.
6. `--justify` pads the spaces of every line except the last of each paragraph until the line is exactly `width`
   wide, the extra spaces going to the leftmost gaps first; a line of one word is left alone. `--check` prints
   nothing and exits with code 1 when wrapping would change the input, and 0 otherwise.
7. A script scripts/demo.sh that runs the CLI over a sample file with three widths and with --justify; run it. Then
   README.md and CHANGELOG.md.
"""
    + ALL_STEPS,
    checks=r'''
def wrap(text, *args):
    return cli(*args, stdin=text).stdout.rstrip("\n").split("\n")


def test_a_long_word_gets_its_own_line():
    """Wrapping `aa bbbbbbbbbb cc` at width 5 should give three lines, the long word unbroken on its own."""
    assert wrap("aa bbbbbbbbbb cc\n", "--width", "5") == ["aa", "bbbbbbbbbb", "cc"]


def test_paragraphs_are_separated_by_one_blank_line():
    """Two paragraphs separated by three blank lines should come out separated by exactly one."""
    assert wrap("a b\n\n\n\nc d\n", "--width", "10") == ["a b", "", "c d"]


def test_list_items_line_up():
    """Wrapping `- alpha beta gamma delta` at width 14 should indent the second line by two spaces, and
    `10. alpha beta gamma` should indent it by four, under the item's text."""
    assert wrap("- alpha beta gamma delta\n", "--width", "14") == ["- alpha beta", "  gamma delta"]
    assert wrap("10. alpha beta gamma\n", "--width", "14") == ["10. alpha beta", "    gamma"]


def test_quotes_keep_their_prefix():
    """A quoted paragraph should keep `> ` on every line, counted toward the width: at width 14,
    `> alpha beta gamma` and `> delta` wrap to `> alpha beta` and `> gamma delta`."""
    assert wrap("> alpha beta gamma\n> delta\n", "--width", "14") == ["> alpha beta", "> gamma delta"]


def test_code_passes_through():
    """Lines indented by four spaces and lines between ``` fences should come out unchanged, whatever the width,
    and the paragraph before an indented line should end there."""
    text = "para one two\n    code   line   stays\nnext para\n\n```\nlong   code   line   here\n```\n"
    out = wrap(text, "--width", "8")
    assert "    code   line   stays" in out and "long   code   line   here" in out
    assert out[:2] == ["para one", "two"] and "next" in out and "para" in out


def test_a_code_line_after_a_list_item_is_passed_through():
    """A code line right after a list item is not passed through unchanged any more: `- item one two` followed by
    the line `    code   x` comes out at width 20 as `- item one two code` and `  x`. A line indented by four spaces
    is code (step 5): it should end the item before it and stay exactly as it is, as it does after an ordinary
    paragraph. The same goes for a numbered item such as `1. item one two`."""
    out = wrap("- item one two\n    code   x\n", "--width", "20")
    assert out[0] == "- item one two" and "    code   x" in out
    out = wrap("1. item one two\n    code   x\n\nnext para\n", "--width", "20")
    assert out[0] == "1. item one two" and "    code   x" in out and out[-1] == "next para"


def test_justify_fills_the_leftmost_gaps_first():
    """With `--justify` at width 9, `a bb ccc dd e` should print `a  bb ccc`, the extra space in the leftmost
    gap, then `dd e` as it is, since the last line of a paragraph is not justified."""
    assert wrap("a bb ccc dd e\n", "--width", "9", "--justify") == ["a  bb ccc", "dd e"]


def test_check(tmp_path):
    """`--check` should print nothing and exit with code 0 for a file that is already wrapped, and with code 1 for
    one that wrapping would change."""
    good, bad = tmp_path / "good.txt", tmp_path / "bad.txt"
    good.write_text("aa bb\ncc\n")
    bad.write_text("aa bb cc\n")
    ok, changed = cli(good, "--width", "5", "--check"), cli(bad, "--width", "5", "--check")
    assert (ok.returncode, ok.stdout, changed.returncode, changed.stdout) == (0, "", 1, "")


def test_width_must_be_positive():
    """`python -m wrapit --width 0` should be a usage error with exit code 2."""
    assert cli("--width", "0", stdin="a\n").returncode == 2
''',
    change="Add `--tabs N` to the CLI: tab characters in the input are replaced by N spaces before wrapping. Without "
    "it tabs stay as they are. Add a test and run the tests.",
    change_checks=r"""
def test_tabs():
    out = cli("--width", "20", "--tabs", "4", stdin="\tcode\tline\nword\tword\n").stdout.rstrip("\n").split("\n")
    assert out[0] == "    code    line" and "\t" not in "".join(out)
""",
)

CRONZONE = Task(
    id="cronzone",
    package="cronzone",
    style="free",
    prompt="""Build a Python package `cronzone` in this folder. It works out when a cron expression fires, in a time
zone.
1. cronzone/core.py: parse(expr) for five fields (minute, hour, day of month, month, day of week) with `*`, lists
   (`1,15`), ranges (`1-5`) and steps (`*/15`, `10-40/10`); day of week 0 and 7 are both Sunday. It raises
   ValueError for a value out of range or a malformed field.
2. next_after(expr, moment) returns the first datetime strictly after `moment` that matches, to the minute, and
   between(expr, start, end) lists every firing in [start, end). Both work on naive datetimes for now.
3. pytest tests in tests/, at least 12.
4. The day rule: when day of month and day of week are both restricted (neither is `*`), a day matches when either
   one does; when only one is restricted, only that one counts.
5. Time zones: next_after(expr, moment, tz) and between(expr, start, end, tz) take a zone name for zoneinfo and
   aware datetimes in any zone. The fields are matched against the wall-clock time in `tz`, and the results are
   aware datetimes in `tz` with the right UTC offset. When the clocks go forward, a wall-clock time that does not
   exist that day does not fire that day. When they go back, a wall-clock time that comes twice fires once, the first
   time it comes. This must hold for every zone, also where the shift is not a whole hour.
6. A CLI: `python -m cronzone "EXPR" --tz ZONE --from ISO_TIME [--count N]` prints the next N firings (1 by
   default), one per line in ISO 8601 with the offset, like `2026-03-30T02:30:00+02:00`. A bad expression or zone
   prints `error: ...` on stderr and exits with code 2.
7. README.md, with a section on what happens at a clock change, and CHANGELOG.md.
"""
    + ALL_STEPS,
    checks=r'''
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo


def expand(field, low, high):
    values = set()
    for part in field.split(","):
        step = 1
        if "/" in part:
            part, text = part.split("/")
            step = int(text)
        if part == "*":
            first, last = low, high
        elif "-" in part:
            first, last = (int(x) for x in part.split("-"))
        else:
            first = last = int(part)
        values.update(range(first, last + 1, step))
    return values


def firings(expr, start, end, zone):
    # Every minute of real time by its wall-clock time; a wall-clock time that comes twice counts the first time.
    minute, hour, dom, month, dow = expr.split()
    minutes, hours, days, months = expand(minute, 0, 59), expand(hour, 0, 23), expand(dom, 1, 31), expand(month, 1, 12)
    weekdays = {value % 7 for value in expand(dow, 0, 7)}
    tz, seen, found = ZoneInfo(zone), set(), []
    moment = start.astimezone(timezone.utc)
    while moment < end.astimezone(timezone.utc):
        local = moment.astimezone(tz)
        in_dom, in_dow = local.day in days, local.isoweekday() % 7 in weekdays
        day = (in_dom or in_dow) if dom != "*" and dow != "*" else (in_dom and in_dow)
        key = (local.date(), local.hour, local.minute)
        if key not in seen and local.minute in minutes and local.hour in hours and local.month in months and day:
            found.append(local)
        seen.add(key)
        moment += timedelta(minutes=1)
    return found


def agree(expr, zone, first_day, days=3):
    tz = ZoneInfo(zone)
    start = datetime(*first_day, tzinfo=tz)
    end = start + timedelta(days=days)
    got = api("cronzone.core").between(expr, start, end, zone)
    want = firings(expr, start, end, zone)
    assert [x.astimezone(timezone.utc) for x in got] == [x.astimezone(timezone.utc) for x in want]
    assert [x.utcoffset() for x in got] == [x.utcoffset() for x in want]


def test_a_time_that_does_not_exist_is_skipped():
    r"""In Europe/Berlin the clocks go forward on 2026-03-29, so 02:30 does not exist that day. between("30 2 * * *",
    ...) over 28 to 30 March should give 28 March 02:30+01:00 and 30 March 02:30+02:00 and nothing on the 29th. It
    gives something else."""
    agree("30 2 * * *", "Europe/Berlin", (2026, 3, 28))


def test_a_time_that_comes_twice_fires_once():
    r"""In Europe/Berlin the clocks go back on 2026-10-25, so 02:30 comes twice. between("30 2 * * *", ...) over 24
    to 26 October should give one firing on the 25th, at 02:30+02:00, the first time it comes."""
    agree("30 2 * * *", "Europe/Berlin", (2026, 10, 24))


def test_hourly_over_the_clock_changes():
    r"""`0 * * * *` in Europe/Berlin should fire 23 times on 2026-03-29, when 02:00 does not exist, and 24 times on
    2026-10-25, when 02:00 comes twice and counts once. between() gives a different list."""
    agree("0 * * * *", "Europe/Berlin", (2026, 3, 28))
    agree("0 * * * *", "Europe/Berlin", (2026, 10, 24))


def test_steps_over_the_gap():
    r"""`*/30 * * * *` in America/New_York around 2026-03-08, when 02:00 to 02:59 do not exist, should go from
    01:30-05:00 straight to 03:00-04:00, and around 2026-11-01 should not repeat 01:00 and 01:30."""
    agree("*/30 * * * *", "America/New_York", (2026, 3, 7))
    agree("*/30 * * * *", "America/New_York", (2026, 11, 1), days=2)


def test_a_shift_of_half_an_hour():
    r"""Australia/Lord_Howe moves its clocks by 30 minutes: on 2026-10-04 the times 02:00 to 02:29 do not exist,
    and on 2026-04-05 the times 01:30 to 01:59 come twice. between("*/15 1,2 * * *", ...) should follow the rule
    there too: no firing at a time that does not exist, one firing at a time that comes twice."""
    agree("*/15 1,2 * * *", "Australia/Lord_Howe", (2026, 10, 3))
    agree("*/15 1,2 * * *", "Australia/Lord_Howe", (2026, 4, 4))


def test_next_after_takes_a_moment_in_another_zone():
    r"""next_after("0 0 * * *", moment, "Europe/Berlin") with the moment given in UTC as 2026-03-28 23:00, which is
    midnight in Berlin, should return 2026-03-30 00:00+02:00: the next midnight strictly after it."""
    moment = datetime(2026, 3, 28, 23, 0, tzinfo=timezone.utc)
    got = api("cronzone.core").next_after("0 0 * * *", moment, "Europe/Berlin")
    assert got == datetime(2026, 3, 30, 0, 0, tzinfo=ZoneInfo("Europe/Berlin"))
    assert got.utcoffset() == timedelta(hours=2)


def test_the_day_rule_in_a_zone():
    r"""`0 12 15 * 5` in Europe/Berlin over November 2026 should fire at noon on every Friday and on the 15th."""
    agree("0 12 15 * 5", "Europe/Berlin", (2026, 11, 1), days=30)


def test_cli_prints_the_offset():
    r"""`python -m cronzone "30 2 * * *" --tz Europe/Berlin --from 2026-03-28T03:00:00+01:00 --count 2` should print
    `2026-03-30T02:30:00+02:00` and `2026-03-31T02:30:00+02:00`: there is no 02:30 on the 29th."""
    result = cli("30 2 * * *", "--tz", "Europe/Berlin", "--from", "2026-03-28T03:00:00+01:00", "--count", "2")
    assert lines(result) == ["2026-03-30T02:30:00+02:00", "2026-03-31T02:30:00+02:00"]


def test_cli_errors():
    r"""A zone that does not exist, as in `python -m cronzone "* * * * *" --tz Mars/Olympus --from
    2026-01-01T00:00:00+00:00`, should print `error: ...` on stderr and exit with code 2, as a bad expression does."""
    for args in (("* * * * *", "--tz", "Mars/Olympus"), ("61 * * * *", "--tz", "UTC")):
        result = cli(*args, "--from", "2026-01-01T00:00:00+00:00")
        assert result.returncode == 2 and result.stderr.startswith("error:")
''',
    change="Add `--utc` to the CLI: the firings are then printed in UTC with a `Z` suffix, like "
    "`2026-03-30T00:30:00Z`. Add a test and run the tests.",
    change_checks=r"""
def test_utc():
    result = cli("30 2 * * *", "--tz", "Europe/Berlin", "--from", "2026-03-28T03:00:00+01:00", "--count", "2", "--utc")
    assert lines(result) == ["2026-03-30T00:30:00Z", "2026-03-31T00:30:00Z"]
""",
)

COLWRAP = Task(
    id="colwrap",
    package="colwrap",
    style="tools",
    prompt="""Build a Python package `colwrap` in this folder. It re-wraps plain text to a width in terminal columns.
1. colwrap/core.py: wrap(text, width=80) re-flows the words of each paragraph into lines no wider than `width`.
   Paragraphs are separated by one or more blank lines and come out separated by exactly one. A word wider than
   the width goes on a line of its own, unbroken. It returns the text without a trailing newline.
2. A CLI: `python -m colwrap [FILE] [--width N]` reads the file, or stdin when none is given, and prints the result.
   A width below 1 is a usage error (exit code 2).
3. pytest tests in tests/, at least 12.
4. Width is counted in terminal columns, not characters: East Asian wide and fullwidth characters
   (unicodedata.east_asian_width of W or F) take two columns; combining marks (unicodedata.combining is not 0)
   and zero-width characters (category Cf, such as U+200B) take none.
5. Text without spaces: a run of wide characters (W or F) may be broken between any two of them, so Chinese and
   Japanese text wraps although it has no spaces. A line never starts with one of the closing marks 。 、 ， ） 」
   and never ends with one of the opening marks （ 「: the break moves back to the nearest place that is allowed.
6. `--justify` pads the spaces of every line except the last of each paragraph until the line is exactly `width`
   columns wide, the extra spaces going to the leftmost gaps first; a line with no space in it is left alone.
   `--check` prints nothing and exits with code 1 when wrapping would change the input, and 0 otherwise.
7. README.md and CHANGELOG.md.
"""
    + ALL_STEPS
    + FILE_TOOLS,
    checks=r'''
def wrap(text, width, **options):
    return api("colwrap.core").wrap(text, width=width, **options).split("\n")


def test_wide_characters_take_two_columns():
    r"""wrap("日本語のテキストです", width=10) should give two lines of five characters, `日本語のテ` and
    `キストです`: each character is two columns wide and a run of them can be broken anywhere."""
    assert wrap("日本語のテキストです", 10) == ["日本語のテ", "キストです"]


def test_a_line_never_starts_with_a_closing_mark():
    r"""wrap("これは日本語。次の文", width=12) should give `これは日本` and `語。次の文`. Six characters fit in
    twelve columns, but the next one is `。`, which must not start a line, so the break moves back by one."""
    assert wrap("これは日本語。次の文", 12) == ["これは日本", "語。次の文"]


def test_a_line_never_ends_with_an_opening_mark():
    r"""wrap("日本語の（注）です", width=10) should give `日本語の` and `（注）です`: five characters fit, but the
    fifth is `（`, which must not end a line."""
    assert wrap("日本語の（注）です", 10) == ["日本語の", "（注）です"]


def test_a_line_takes_what_fits_of_the_next_word():
    r"""wrap("漢 （補足）。", width=7) should give `漢 （補` and `足）。`: the first two characters of the second word
    fit on the first line, and wide characters may be broken apart. It gives the three lines `漢`, `（補`, `足）。`
    instead, so wrapping colwrap's own output again changes it, and `--check` exits with code 1 on a file that
    colwrap has just wrapped. Likewise wrap("ab 日本語のテキスト", width=8) should give `ab 日本`, `語のテキ`,
    `スト`."""
    assert wrap("漢 （補足）。", 7) == ["漢 （補", "足）。"]
    assert wrap("ab 日本語のテキスト", 8) == ["ab 日本", "語のテキ", "スト"]
    assert wrap("\n".join(wrap("漢 （補足）。", 7)), 7) == ["漢 （補", "足）。"]


def test_combining_marks_take_no_column():
    r"""wrap("cafe\u0301 au lait", width=7), where the é is an e followed by a combining accent, should keep
    `café au` on one line: it is seven columns wide, though eight characters long."""
    assert wrap("cafe\u0301 au lait", 7) == ["cafe\u0301 au", "lait"]


def test_zero_width_characters_take_no_column():
    r"""wrap("ab\u200bcd ef", width=7) should stay on one line: the zero-width space takes no column."""
    assert wrap("ab\u200bcd ef", 7) == ["ab\u200bcd ef"]


def test_fullwidth_letters_are_wide():
    r"""wrap("ＡＢＣ ＤＥ", width=8), written in fullwidth letters, should give `ＡＢＣ` and `ＤＥ`: each letter is
    two columns wide."""
    assert wrap("ＡＢＣ ＤＥ", 8) == ["ＡＢＣ", "ＤＥ"]


def test_justify_counts_columns():
    r"""`--justify --width 8` on `café au x lait`, with the é as e and a combining accent, should print
    `café  au` with two spaces, then `x lait`: `café au` is seven columns wide, so one space is added."""
    result = cli("--width", "8", "--justify", stdin="cafe\u0301 au x lait\n")
    assert result.stdout.rstrip("\n").split("\n") == ["cafe\u0301  au", "x lait"]


def test_check_on_wrapped_wide_text(tmp_path):
    r"""`--check --width 10` should exit with code 0 for a file that holds `日本語のテ` and `キストです` on two
    lines, which is already wrapped, and with code 1 for the same text on one line."""
    good, bad = tmp_path / "good.txt", tmp_path / "bad.txt"
    good.write_text("日本語のテ\nキストです\n")
    bad.write_text("日本語のテキストです\n")
    assert cli(good, "--width", "10", "--check").returncode == 0
    assert cli(bad, "--width", "10", "--check").returncode == 1
''',
    change="Add `--indent N` to the CLI and wrap(text, width, indent=N): every output line starts with N spaces, which "
    "count toward the width. Add a test and run the tests.",
    change_checks=r"""
def test_indent():
    assert api("colwrap.core").wrap("aa bb cc", width=7, indent=2).split("\n") == ["  aa bb", "  cc"]
    assert cli("--width", "6", "--indent", "2", stdin="日本語\n").stdout.rstrip("\n").split("\n") == ["  日本", "  語"]
""",
)

DIFFLITE = Task(
    id="difflite",
    package="difflite",
    style="scripts",
    prompt="""Build a Python package `difflite` in this folder: a line diff tool that writes unified diffs the `patch`
program accepts. Do not use difflib.
1. difflite/core.py: lcs(a, b) for two lists of lines, and opcodes(a, b), which turns it into runs of
   ("equal" | "delete" | "insert", lines).
2. unified(a_lines, b_lines, a_name, b_name, context=3) returns the diff text: the headers `--- a_name` and
   `+++ b_name`, then hunks headed `@@ -START,COUNT +START,COUNT @@` with `context` lines around each change;
   changes whose context overlaps or touches share one hunk. It returns an empty string when the inputs are equal.
3. pytest tests in tests/, at least 12.
4. A CLI: `python -m difflite OLD NEW [-U N]` prints the diff and exits with code 1 when the files differ, 0 when
   they are the same, and 2 on an error such as a missing file.
5. The details, as `diff -u` writes them: an empty side has the range `-0,0` or `+0,0`; a count of 1 is left out
   (`@@ -3 +3 @@`); with `-U 0` an insertion after line N of the old file is `@@ -N,0 +M,K @@`; and a file whose last
   line has no newline gets the line `\\ No newline at end of file` right after that line, on whichever side it
   applies to. A script scripts/compare.sh that checks the output against the system's `diff -u` on a few pairs of
   files; run it.
6. `python -m difflite --apply DIFF_FILE TARGET` applies a diff this tool wrote to TARGET in place. When a hunk does
   not match the file it exits with code 1 and changes nothing.
7. `--stat` prints `N insertions(+), M deletions(-)` in place of the diff. Then README.md and CHANGELOG.md.
"""
    + ALL_STEPS,
    checks=r'''
def pair(tmp_path, old, new):
    a, b = tmp_path / "old.txt", tmp_path / "new.txt"
    a.write_bytes(old.encode())
    b.write_bytes(new.encode())
    return a, b


def patched(tmp_path, old, new, *args):
    # The diff, applied by the system's patch program: what comes out should be the new file.
    a, b = pair(tmp_path, old, new)
    diff = cli(a, b, *args)
    (tmp_path / "d.patch").write_text(diff.stdout)
    out = tmp_path / "out.txt"
    done = subprocess.run(["patch", "-s", "-o", str(out), str(a), str(tmp_path / "d.patch")], capture_output=True)
    return done.returncode == 0 and out.read_bytes() == b.read_bytes()


def body(tmp_path, old, new, *args):
    a, b = pair(tmp_path, old, new)
    ours = cli(a, b, *args).stdout.split("\n")[2:]
    real = subprocess.run(["diff", *(args or ["-u"]), str(a), str(b)], capture_output=True, text=True).stdout
    return ours, real.split("\n")[2:]


TEN = "".join(f"line {n}\n" for n in range(1, 11))


def test_patch_accepts_a_change_in_the_middle(tmp_path):
    r"""The diff of a ten-line file and the same file with line 5 changed should apply with `patch` and give the
    new file. `patch` rejects it or produces something else."""
    assert patched(tmp_path, TEN, TEN.replace("line 5\n", "LINE 5\n"))


def test_a_new_last_line_without_a_newline(tmp_path):
    r"""When the new file's last line has no newline, the diff needs `\ No newline at end of file` after that line:
    `python -m difflite old new` for `a\nb\n` against `a\nb\nc` should match what `diff -u` prints, and `patch`
    should rebuild the new file from it."""
    ours, real = body(tmp_path, "a\nb\n", "a\nb\nc")
    assert ours == real and patched(tmp_path, "a\nb\n", "a\nb\nc")


def test_the_old_last_line_gains_its_newline(tmp_path):
    r"""When only the old file lacks the final newline (`a\nb` against `a\nb\n`), the marker belongs after the
    removed line, on the old side. The output should match `diff -u`."""
    ours, real = body(tmp_path, "a\nb", "a\nb\n")
    assert ours == real and patched(tmp_path, "a\nb", "a\nb\n")


def test_both_files_lack_the_final_newline(tmp_path):
    r"""For `a\nb` against `a\nc`, neither with a final newline, the marker comes after both the removed and the
    added line, as in `diff -u`."""
    ours, real = body(tmp_path, "a\nb", "a\nc")
    assert ours == real


def test_an_empty_old_file(tmp_path):
    r"""Against an empty old file the hunk header should be `@@ -0,0 +1,2 @@`, and against an empty new file
    `@@ -1,2 +0,0 @@`, as `diff -u` writes them."""
    ours, real = body(tmp_path, "", "a\nb\n")
    assert ours == real
    ours, real = body(tmp_path, "a\nb\n", "")
    assert ours == real


def test_a_count_of_one_is_left_out(tmp_path):
    r"""`python -m difflite old new -U 0` for a single changed line should print `@@ -5 +5 @@`, with no `,1`, and for
    a line inserted after line 2 `@@ -2,0 +3 @@`, as `diff -U 0` does."""
    ours, real = body(tmp_path, TEN, TEN.replace("line 5\n", "LINE 5\n"), "-U", "0")
    assert ours == real
    ours, real = body(tmp_path, "a\nb\n", "a\nb\nc\n", "-U", "0")
    assert ours == real


def test_hunks_that_touch_are_one_hunk(tmp_path):
    r"""Changes on lines 2 and 9 of a ten-line file are six lines apart, so with three lines of context their hunks
    touch and should be one hunk, as in `diff -u`; with `-U 1` they should be two."""
    new = TEN.replace("line 2\n", "LINE 2\n").replace("line 9\n", "LINE 9\n")
    ours, real = body(tmp_path, TEN, new)
    assert ours == real
    ours, real = body(tmp_path, TEN, new, "-U", "1")
    assert ours == real


def test_exit_codes(tmp_path):
    r"""`python -m difflite a b` should exit with code 0 and print nothing for equal files, 1 for different ones and
    2 when a file is missing."""
    a, b = pair(tmp_path, TEN, TEN)
    same = cli(a, b)
    assert (same.returncode, same.stdout) == (0, "")
    b.write_text("x\n")
    assert cli(a, b).returncode == 1 and cli(a, tmp_path / "nope").returncode == 2


def test_apply_round_trip_and_mismatch(tmp_path):
    r"""`--apply` should turn the old file into the new one from a diff this tool wrote, also when the last line
    has no newline, and should exit with code 1 and leave the target alone when a hunk does not match."""
    new = TEN.replace("line 2\n", "").replace("line 9\n", "nine\n") + "tail"
    a, b = pair(tmp_path, TEN, new)
    (tmp_path / "d.patch").write_text(cli(a, b).stdout)
    assert cli("--apply", tmp_path / "d.patch", a).returncode == 0 and a.read_bytes() == b.read_bytes()
    other = tmp_path / "other.txt"
    other.write_text("nothing\nin\ncommon\n")
    assert cli("--apply", tmp_path / "d.patch", other).returncode == 1
    assert other.read_text() == "nothing\nin\ncommon\n"


def test_stat(tmp_path):
    r"""`--stat` should print `2 insertions(+), 1 deletions(-)` for a file with one line changed and one added."""
    a, b = pair(tmp_path, TEN, TEN.replace("line 5\n", "LINE 5\n") + "more\n")
    assert cli(a, b, "--stat").stdout.strip() == "2 insertions(+), 1 deletions(-)"
''',
    change="Add `-q` to the CLI: when the files differ it prints `Files OLD and NEW differ`, with their names, in "
    "place of the diff, and nothing when they are the same. The exit codes stay as they are. Add a test and run the "
    "tests.",
    change_checks=r"""
def test_brief(tmp_path):
    a, b = tmp_path / "old.txt", tmp_path / "new.txt"
    a.write_text("a\n")
    b.write_text("b\n")
    result = cli(a, b, "-q")
    assert result.stdout.strip() == f"Files {a} and {b} differ" and result.returncode == 1
    b.write_text("a\n")
    assert (cli(a, b, "-q").stdout, cli(a, b, "-q").returncode) == ("", 0)
""",
)

GITIG = Task(
    id="gitig",
    package="gitig",
    style="free",
    prompt="""Build a Python package `gitig` in this folder: it decides which paths a .gitignore file ignores, by
git's own rules.
1. gitig/core.py: Rules(text) parses the lines of a .gitignore; its ignored(path) says whether a path is ignored.
   Paths are relative, with forward slashes, and a trailing slash marks a directory. Start with plain names and
   the wildcards `*`, `?` and `[abc]`, which never match a slash; a pattern without a slash matches at any depth.
2. A CLI: `python -m gitig RULES_FILE PATH...` prints each ignored path, one per line, and exits with code 0 when
   at least one is ignored and 1 otherwise.
3. pytest tests in tests/, at least 12.
4. Slashes: a pattern with a slash at its start or in its middle is anchored to the root; a pattern that ends with
   a slash matches directories only. Whatever lies under an ignored directory is ignored.
5. `**`: a leading `**/` matches in all directories, a trailing `/**` everything inside, and `/**/` zero or more
   directories.
6. Order and negation: the last matching pattern decides, and a pattern that starts with `!` un-ignores. A path
   cannot be re-included when a parent directory of it is ignored. Blank lines are skipped, a line that starts with
   `#` is a comment, `\\#` and `\\!` stand for the literal characters, and trailing spaces are dropped unless
   escaped with a backslash.
7. `python -m gitig RULES_FILE --stdin` reads the paths from stdin, one per line. `-v` prints
   `LINE_NUMBER:PATTERN<TAB>PATH` for each ignored path, naming the pattern that decided. Then README.md and
   CHANGELOG.md.
"""
    + ALL_STEPS,
    checks=r'''
def by_git(tmp_path, rules, paths):
    # What git itself ignores, for the same rules and paths.
    repo = tmp_path / "repo"
    repo.mkdir(exist_ok=True)
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    (repo / ".gitignore").write_text(rules)
    command = ["git", "-C", str(repo), "check-ignore", "--no-index", "--stdin"]
    return subprocess.run(command, input="\n".join(paths) + "\n", capture_output=True, text=True).stdout.splitlines()


def agree(tmp_path, rules, paths):
    file = tmp_path / "rules"
    file.write_text(rules)
    assert lines(cli(file, *paths)) == by_git(tmp_path, rules, paths)


def test_a_trailing_slash_means_directories_only(tmp_path):
    r"""With the rule `build/`, the file `build` is not ignored, while the directory `build/` and everything under
    a directory of that name, such as `src/build/x.o`, is. gitig decides one of these differently from git."""
    agree(tmp_path, "build/\n", ["build", "build/", "src/build/x.o", "a/build", "src/build/"])


def test_a_slash_in_the_middle_anchors_the_pattern(tmp_path):
    r"""`docs/*.md` matches `docs/a.md` only: not `x/docs/a.md`, since the slash anchors it to the root, and not
    `docs/sub/a.md`, since `*` does not match a slash. `/root.txt` matches `root.txt` and not `sub/root.txt`."""
    agree(tmp_path, "docs/*.md\n/root.txt\n", ["docs/a.md", "x/docs/a.md", "docs/sub/a.md", "root.txt", "sub/root.txt"])


def test_double_star(tmp_path):
    r"""`**/tmp` matches `tmp` at any depth, `logs/**` everything inside logs but not `logs` itself, and `a/**/z`
    matches `a/z` as well as `a/b/c/z`. gitig decides one of these differently from git."""
    paths = ["tmp", "q/tmp/f", "q/tmp", "logs/a/b", "logs", "a/z", "a/b/c/z", "b/a/z", "x/logs/a"]
    agree(tmp_path, "**/tmp\nlogs/**\na/**/z\n", paths)


def test_the_last_matching_pattern_decides(tmp_path):
    r"""With `*.log`, `!keep.log` and then `debug/keep.log`, the file `keep.log` is kept at any depth except in
    debug/, where the later pattern ignores it again."""
    paths = ["x.log", "keep.log", "d/keep.log", "debug/keep.log", "debug/x.log"]
    agree(tmp_path, "*.log\n!keep.log\ndebug/keep.log\n", paths)


def test_nothing_under_an_ignored_directory_comes_back(tmp_path):
    r"""With `build/` and `!build/keep.txt`, the file `build/keep.txt` stays ignored: a path cannot be re-included
    when a parent directory of it is ignored. With `build/*` and `!build/keep.txt` it is kept."""
    agree(tmp_path, "build/\n!build/keep.txt\n", ["build/keep.txt", "build/x"])
    agree(tmp_path, "build/*\n!build/keep.txt\n", ["build/keep.txt", "build/x"])


def test_escapes_and_trailing_spaces(tmp_path):
    r"""`\#lit` matches a file called `#lit`, `\!bang` one called `!bang`, a `# line` is a comment, and trailing
    spaces count only when escaped: `trail\ ` followed by a space matches `trail ` with one space, not `trail`."""
    rules = "# comment\n\\#lit\n\\!bang\ntrail\\ \nplain   \n"
    agree(tmp_path, rules, ["#lit", "!bang", "comment", "trail ", "trail", "plain", "plain   "])


def test_character_classes_and_question_marks(tmp_path):
    r"""`file?.txt` matches `file1.txt` and not `file10.txt` or `file/.txt`; `[a-c]x` matches `bx` and not `dx`;
    `[!a-c]y` matches `dy` and not `ay`."""
    paths = ["file1.txt", "file10.txt", "d/fileA.txt", "bx", "dx", "dy", "ay", "sub/cx"]
    agree(tmp_path, "file?.txt\n[a-c]x\n[!a-c]y\n", paths)


def test_a_pattern_for_a_directory_without_the_slash(tmp_path):
    r"""The rule `node_modules` without a slash ignores a file or a directory of that name at any depth, and so
    everything under it: `a/node_modules/x/y.js` is ignored."""
    agree(tmp_path, "node_modules\n", ["node_modules", "a/node_modules/x/y.js", "node_modules2", "a/b/node_modules/"])


def test_verbose_names_the_pattern(tmp_path):
    r"""`-v` should print `LINE_NUMBER:PATTERN<TAB>PATH` for each ignored path, with the pattern that decided:
    for the rules `*.log` and `debug/` on lines 1 and 3, `debug/x.log` is ignored by line 3 and `x.log` by line 1."""
    file = tmp_path / "rules"
    file.write_text("*.log\n\ndebug/\n")
    assert lines(cli(file, "-v", "x.log", "debug/x.log", "y.txt")) == ["1:*.log\tx.log", "3:debug/\tdebug/x.log"]


def test_stdin_and_exit_codes(tmp_path):
    r"""`--stdin` should read the paths from stdin, and the exit code should be 0 when at least one path is ignored
    and 1 when none is."""
    file = tmp_path / "rules"
    file.write_text("*.log\n")
    some = cli(file, "--stdin", stdin="a.log\nb.txt\n")
    none = cli(file, "--stdin", stdin="b.txt\n")
    assert (lines(some), some.returncode, lines(none), none.returncode) == (["a.log"], 0, [], 1)
''',
    change="Add `--count` to the CLI: it prints only the number of ignored paths. The exit code stays as it is. Add a "
    "test and run the tests.",
    change_checks=r"""
def test_count(tmp_path):
    file = tmp_path / "rules"
    file.write_text("*.log\n!keep.log\n")
    result = cli(file, "--count", "a.log", "keep.log", "b.log", "c.txt")
    assert (result.stdout.strip(), result.returncode) == ("2", 0)
    assert (cli(file, "--count", "c.txt").stdout.strip(), cli(file, "--count", "c.txt").returncode) == ("0", 1)
""",
)

SHWORDS = Task(
    id="shwords",
    package="shwords",
    style="scripts",
    prompt="""Build a Python package `shwords` in this folder: it splits a command line into words the way a POSIX shell
does. Do not use shlex.
1. shwords/core.py: split(text) returns the list of words. Whitespace separates words; single quotes keep
   everything literal; inside double quotes a backslash escapes only `"`, `\\`, `$` and a newline; outside quotes
   a backslash makes the next character literal, and a backslash followed by a newline disappears. Quoted and
   unquoted pieces next to each other join into one word, and `''` gives an empty word. A quote that is never closed
   raises ValueError.
2. A CLI: `python -m shwords "LINE"` prints one word per line as JSON strings; a ValueError becomes `error: ...` on
   stderr with exit code 2.
3. pytest tests in tests/, at least 15.
4. Variables: split(text, env) expands `$NAME` and `${NAME}` from the env dict, an unset name to nothing;
   `${NAME:-word}` gives word when the variable is unset or empty, and `${NAME:+word}` gives word when it is set
   and not empty. Nothing expands inside single quotes. A `$` followed by anything else is literal.
5. Field splitting: an expansion outside quotes is split into words at whitespace, and one that is empty
   disappears, while `"$EMPTY"` gives one empty word. Text next to an unquoted expansion joins its first and last
   fields: `a$X b` with X=`1 2` gives `a1`, `2`, `b`.
6. Comments and operators: an unquoted `#` at the start of a word begins a comment that runs to the end of the
   line. tokens(text, env) returns (kind, value) pairs, in which the operators `|`, `||`, `&&`, `;`, `>`, `>>`, `<`
   and `&` are tokens of kind "op" of their own, also without spaces around them, and everything else is of kind
   "word". A script scripts/against_sh.sh that compares split with what `sh` does for a few lines; run it.
7. `python -m shwords [--env NAME=VALUE]... [--tokens] "LINE"`: with --tokens it prints `KIND<TAB>JSON` lines. Then
   README.md and CHANGELOG.md.
"""
    + ALL_STEPS,
    checks=r'''
def by_sh(line, env=None):
    # What a real shell makes of the line: its words, one after the other.
    script = "set -f\nset -- " + line + '\nfor w do printf "%s\\0" "$w"; done'
    done = subprocess.run(["sh", "-c", script], capture_output=True, env={"PATH": os.environ["PATH"], **(env or {})})
    assert done.returncode == 0, done.stderr
    return done.stdout.decode().split("\0")[:-1]


def split(line, env=None):
    return api("shwords.core").split(line, env or {})


def agree(line, env=None):
    assert split(line, env) == by_sh(line, env), line


def test_quotes_join_into_one_word():
    r"""split should treat quoted and unquoted pieces next to each other as one word, as sh does: `a"b c"'d e'f` is
    the single word `ab cd ef`, and `''` and `""` are empty words."""
    for line in ("a\"b c\"'d e'f g", "'' x \"\"", "a''b", "\"it's\" 'say \"hi\"'"):
        agree(line)


def test_backslashes_inside_double_quotes():
    r"""Inside double quotes a backslash escapes only `"`, `\`, `$` and a newline, and stays in front of anything
    else: `"a\nb"` keeps its backslash, `"a\"b"` loses it, and `"a\\b"` has one. Outside quotes `\n` is just `n`."""
    for line in ('"a\\nb"', '"a\\"b"', '"a\\\\b"', '"a\\$b"', "a\\nb", "a\\ b c", "\\'x \\\"y", '"a\\\nb" c\\\nd'):
        agree(line)


def test_expansions():
    r"""`$NAME`, `${NAME}`, `${NAME:-word}` and `${NAME:+word}` should expand as in sh: an unset or empty variable
    takes the `:-` word, a set one the `:+` word, and nothing expands inside single quotes."""
    env = {"X": "1", "E": ""}
    for line in ("$X ${X}y $Xy", "${U:-d} ${E:-d} ${X:-d}", "${U:+o}a ${X:+o} ${E:+o}b", "'$X' \"$X\" \\$X", "a${X}b$"):
        agree(line, env)


def test_unquoted_expansions_are_split_into_fields():
    r"""An expansion outside quotes is split at whitespace and joins the text around it: `a$X b` with X=`1 2` is
    `a1`, `2`, `b`, while `"a$X b"` is one word. `${U:-d e}` gives two words and `"${U:-d e}"` one."""
    env = {"X": "1 2", "S": "  lead and trail  "}
    for line in ("a$X b", '"a$X b"', "${U:-d e} \"${U:-d e}\"", "x$S", "$S$S", "[$S]"):
        agree(line, env)


def test_empty_expansions():
    r"""An empty expansion outside quotes disappears, `"$E"` is one empty word, and `a$E` is just `a`: with E empty,
    `$E "$E" a$E $U ''` has three words."""
    for line in ("$E \"$E\" a$E $U ''", "$E$U", "\"$E$U\"", "x ${E:+y} z"):
        agree(line, {"E": ""})


def test_comments():
    r"""A `#` at the start of a word begins a comment, inside a word it does not: `a #b c` is the one word `a`,
    and `a#b '#c' \#d` has three words."""
    for line in ("a #b c", "a#b '#c' \\#d", "#all", "a \"#b\" # c"):
        agree(line)


def test_an_unclosed_quote_is_an_error():
    r"""split("a 'b") and split('a "b') should raise ValueError, and the CLI should print `error: ...` on stderr and
    exit with code 2."""
    for line in ("a 'b", 'a "b', 'a "b\\"'):
        with pytest.raises(ValueError):
            split(line)
    result = cli("a 'b")
    assert result.returncode == 2 and result.stderr.startswith("error:")


def test_operators_are_tokens_of_their_own():
    r"""tokens("a|b&&c >>out;d", {}) should give the words a, b, c, out and d with the operators `|`, `&&`, `>>` and
    `;` between them, though there are no spaces; a quoted `'|'` is a word."""
    got = [tuple(pair) for pair in api("shwords.core").tokens("a|b&&c >>out;d '|' x||y&", {})]
    assert got == [
        ("word", "a"), ("op", "|"), ("word", "b"), ("op", "&&"), ("word", "c"), ("op", ">>"), ("word", "out"),
        ("op", ";"), ("word", "d"), ("word", "|"), ("word", "x"), ("op", "||"), ("word", "y"), ("op", "&"),
    ]


def test_cli_tokens_and_env():
    r"""`python -m shwords --env X="1 2" --tokens 'a$X>f'` should print `word<TAB>"a1"`, `word<TAB>"2"`,
    `op<TAB>">"` and `word<TAB>"f"`, and without --tokens one JSON string per word."""
    assert lines(cli("--env", "X=1 2", "--tokens", "a$X>f")) == ['word\t"a1"', 'word\t"2"', 'op\t">"', 'word\t"f"']
    assert lines(cli("--env", "X=1 2", 'a$X "b c"')) == ['"a1"', '"2"', '"b c"']
''',
    change="Add join(words) and `python -m shwords --join WORD...`: it gives one line that split turns back into "
    "exactly those words, quoting only the words that need it. Add a test and run the tests.",
    change_checks=r"""
def test_join():
    words = ["plain", "two words", "it's", 'say "hi"', "", "$HOME", "a\\b", "#no", "x|y", "tab\there"]
    joined = api("shwords.core").join(words)
    script = "set -f\nset -- " + joined + '\nfor w do printf "%s\\0" "$w"; done'
    done = subprocess.run(["sh", "-c", script], capture_output=True, env={"PATH": os.environ["PATH"], "HOME": "/h"})
    assert done.stdout.decode().split("\0")[:-1] == words
    assert api("shwords.core").join(["a", "b"]) == "a b"
    assert cli("--join", "a", "b c").stdout.strip() == joined_two(api)


def joined_two(api):
    return api("shwords.core").join(["a", "b c"])
""",
)

TRAPS: tuple[Task, ...] = (CRONZONE, COLWRAP, DIFFLITE, GITIG, SHWORDS)
"""Tasks whose later steps hold work that is easy to get subtly wrong, each checked against something that knows the
answer: the zone database, git, patch, diff and sh."""

KVTX = Task(
    id="kvtx",
    package="kvtx",
    style="free",
    prompt="""Build a Python package `kvtx` in this folder: a key-value store kept in an append-only log file. One rule
holds for every command and method: a command that fails leaves the file byte for byte as it was.
1. kvtx/store.py: Store(path) with set(key, value), get(key, default=None), delete(key) and keys(). Every change
   appends one JSON line to the file, `{"op": "set", "key": ..., "value": ...}` or `{"op": "del", "key": ...}`;
   opening a Store replays the file. Keys and values are strings: anything else raises TypeError. Deleting a key
   that is not there raises KeyError.
2. A CLI: `python -m kvtx FILE set KEY VALUE`; `get KEY` prints the value (exit code 1 and nothing printed when the
   key is missing); `del KEY` (exit code 1 when missing); `keys` prints them sorted, one per line.
3. pytest tests in tests/, at least 12.
4. compact() rewrites the file with one set line per live key, in sorted key order, through a temporary file in the
   same folder that replaces the original with os.replace. `python -m kvtx FILE compact` prints `N -> M lines`.
5. `python -m kvtx FILE import DATA.json` sets every pair of a JSON object, in the order they appear; a pair whose
   value is null deletes that key. It prints `N set, M deleted`. When the data is not an object, a value is neither
   a string nor null, or a null names a key that is not there, it prints `error: ...` on stderr and exits with
   code 2.
6. `python -m kvtx FILE rename OLD NEW` moves a value to another key and replaces NEW when it exists (exit code 1
   when OLD is missing; renaming a key to itself changes nothing and writes nothing). `python -m kvtx FILE batch`
   reads commands from stdin, one per line: `set KEY VALUE`, `del KEY` or `rename OLD NEW`, split into words the way
   a shell does. It applies all of them or, when one of them fails or cannot be parsed, none of them: it then prints
   `error: line N: ...` on stderr and exits with code 2.
7. README.md and CHANGELOG.md.
"""
    + ALL_STEPS,
    checks=r'''
def store(tmp_path, **pairs):
    file = tmp_path / "db.log"
    for key, value in pairs.items():
        assert cli(file, "set", key, value).returncode == 0
    return file


def state(file):
    return {key: cli(file, "get", key).stdout.rstrip("\n") for key in lines(cli(file, "keys"))}


def refused(file, *args, stdin=None, code=2):
    before = file.read_bytes()
    result = cli(file, *args, stdin=stdin)
    return result.returncode == code and file.read_bytes() == before, result


def test_import_with_a_bad_value_changes_nothing(tmp_path):
    r"""`import` of `{"x": "1", "b": 5, "y": "2"}` should exit with code 2 and leave the file byte for byte as it
    was, since 5 is not a string. It exits with another code, or `x` is set although the command failed."""
    file, data = store(tmp_path, a="1", b="2"), tmp_path / "data.json"
    data.write_text('{"x": "1", "b": 5, "y": "2"}')
    ok, result = refused(file, "import", data)
    assert ok and result.stderr.startswith("error:")


def test_import_with_a_null_for_a_missing_key_changes_nothing(tmp_path):
    r"""`import` of `{"x": "1", "nope": null}` should exit with code 2 and change nothing, since there is no key
    `nope` to delete. The file is changed although the command failed, or the exit code is not 2."""
    file, data = store(tmp_path, a="1"), tmp_path / "data.json"
    data.write_text('{"x": "1", "nope": null}')
    ok, _ = refused(file, "import", data)
    assert ok
    data.write_text('["x", "1"]')
    ok, _ = refused(file, "import", data)
    assert ok


def test_import_sets_and_deletes(tmp_path):
    r"""`import` of `{"a": "9", "b": null, "c": "3"}` into a store with a and b should print `2 set, 1 deleted` and
    leave a=9 and c=3."""
    file, data = store(tmp_path, a="1", b="2"), tmp_path / "data.json"
    data.write_text('{"a": "9", "b": null, "c": "3"}')
    result = cli(file, "import", data)
    assert (result.returncode, result.stdout.strip()) == (0, "2 set, 1 deleted")
    assert state(file) == {"a": "9", "c": "3"}


def test_batch_is_all_or_nothing(tmp_path):
    r"""`batch` with the lines `set x 1`, `set y 2`, `del nope`, `set z 3` should print `error: line 3: ...`, exit
    with code 2 and leave the file byte for byte as it was. x and y are set although the third line failed."""
    file = store(tmp_path, a="1")
    ok, result = refused(file, "batch", stdin="set x 1\nset y 2\ndel nope\nset z 3\n")
    assert ok and result.stderr.startswith("error: line 3:")


def test_batch_lines_see_the_lines_before_them(tmp_path):
    r"""`batch` with `set n 1`, `rename n m`, `del m`, `set k v` should succeed and leave only `k` added: each line
    works on what the lines before it did. And `del a` twice should fail on line 2 and change nothing."""
    file = store(tmp_path, a="1")
    result = cli(file, "batch", stdin="set n 1\nrename n m\ndel m\nset k v\n")
    assert result.returncode == 0 and state(file) == {"a": "1", "k": "v"}
    ok, result = refused(file, "batch", stdin="del a\ndel a\n")
    assert ok and result.stderr.startswith("error: line 2:")


def test_batch_splits_like_a_shell(tmp_path):
    r"""`batch` should split its lines the way a shell does: `set "two words" 'a b'` sets the key `two words` to
    `a b`. A line that cannot be parsed, like `set a "unclosed` or `rename a`, is an error with its line number
    that changes nothing."""
    file = store(tmp_path, a="1")
    assert cli(file, "batch", stdin="set \"two words\" 'a b'\n").returncode == 0
    assert state(file) == {"a": "1", "two words": "a b"}
    for text, line in (('set b 2\nset a "unclosed\n', 2), ("rename a\n", 1), ("set c 3\nbogus x\n", 2)):
        ok, result = refused(file, "batch", stdin=text)
        assert ok and result.stderr.startswith(f"error: line {line}:"), text


def test_rename(tmp_path):
    r"""`rename a b` should move a's value to b and replace the b that was there; `rename nope x` should exit with
    code 1 and change nothing; `rename b b` should exit with code 0 and write nothing to the file."""
    file = store(tmp_path, a="1", b="2")
    assert cli(file, "rename", "a", "b").returncode == 0 and state(file) == {"b": "1"}
    ok, _ = refused(file, "rename", "nope", "x", code=1)
    assert ok
    ok, _ = refused(file, "rename", "b", "b", code=0)
    assert ok


def test_a_failed_change_writes_nothing(tmp_path):
    r"""`del nope` should exit with code 1 and leave the file as it was, and Store.set("k", 5) should raise
    TypeError without writing a line."""
    file = store(tmp_path, a="1")
    ok, _ = refused(file, "del", "nope", code=1)
    assert ok
    before = file.read_bytes()
    with pytest.raises(TypeError):
        api("kvtx.store").Store(file).set("k", 5)
    assert file.read_bytes() == before


def test_compact(tmp_path):
    r"""After `set a 1`, `set a 2`, `set b 1` and `del b`, `compact` should print `4 -> 1 lines` and keep a=2."""
    file = store(tmp_path, a="1")
    for args in (("set", "a", "2"), ("set", "b", "1"), ("del", "b")):
        cli(file, *args)
    assert cli(file, "compact").stdout.strip() == "4 -> 1 lines" and state(file) == {"a": "2"}
''',
    change="Add `python -m kvtx FILE count`: it prints the number of live keys. Add a test and run the tests.",
    change_checks=r"""
def test_count(tmp_path):
    file = tmp_path / "db.log"
    for args in (("set", "a", "1"), ("set", "b", "2"), ("set", "a", "3"), ("del", "b")):
        cli(file, *args)
    assert cli(file, "count").stdout.strip() == "1"
""",
)

REPLAN = Task(
    id="replan",
    package="replan",
    style="free",
    prompt="""Build a Python package `replan` in this folder: it renames the files of a folder according to a plan. One
rule holds throughout: a run that fails leaves the folder exactly as it was.
1. replan/plan.py: parse(text) reads lines `OLD -> NEW`, names relative to the folder, into a list of (old, new)
   pairs. Blank lines and lines that start with `#` are skipped; any other line raises PlanError with its 1-based
   line number.
2. A CLI: `python -m replan FOLDER PLAN_FILE` renames the files and prints `renamed N files`. `--dry-run` prints
   one `OLD -> NEW` line for each rename that would happen and changes nothing. A PlanError prints `error: ...` on
   stderr and exits with code 2.
3. pytest tests in tests/, at least 12, on temporary folders.
4. Validation, before anything is renamed: a source that does not exist, the same source twice, two sources with
   the same target, and a target that exists and is not itself renamed away by the plan are each a PlanError that
   names the file. A line whose old and new names are the same is skipped and not counted.
5. Order: the plan describes the result, not a sequence of steps. `a -> b` with `b -> c` moves the old b to c and
   the old a to b, in whatever order the lines come, and a cycle such as `a -> b` with `b -> a` swaps the files.
   Use temporary names inside the folder where that is needed, and leave none behind.
6. When the operating system refuses a rename part-way, everything done so far is undone before the error is
   reported (`error: ...`, exit code 1). `--undo-file PATH` writes a plan in the same format that reverses the run:
   applying it with replan restores the folder. It is written only when the run succeeds, and never with --dry-run.
7. Subfolders: names may contain `/`. Target folders are created as needed, and a source folder that the run
   leaves empty is removed. Then README.md and CHANGELOG.md.
"""
    + ALL_STEPS,
    checks=r'''
def folder(tmp_path, files, name="f"):
    root = tmp_path / name
    for path, text in files.items():
        (root / path).parent.mkdir(parents=True, exist_ok=True)
        (root / path).write_text(text)
    return root


def tree(root):
    return {str(p.relative_to(root)): p.read_text() for p in sorted(root.rglob("*")) if p.is_file()}


def folders(root):
    return sorted(str(p.relative_to(root)) for p in root.rglob("*") if p.is_dir())


def run(tmp_path, root, plan, *args):
    file = tmp_path / "plan.txt"
    file.write_text(plan)
    return cli(root, file, *args)


ABC = {"a": "A", "b": "B", "c": "C"}


def test_a_chain_in_any_order(tmp_path):
    r"""With files a and b, the plan `a -> b`, `b -> c` should leave b with a's old content and c with b's, and
    print `renamed 2 files`, whichever line comes first. The run fails, or a file ends up with the wrong content."""
    for number, plan in enumerate(("a -> b\nb -> c\n", "b -> c\na -> b\n")):
        root = folder(tmp_path, {"a": "A", "b": "B"}, f"f{number}")
        result = run(tmp_path, root, plan)
        assert result.returncode == 0 and tree(root) == {"b": "A", "c": "B"}
        assert result.stdout.strip() == "renamed 2 files"


def test_a_swap_and_a_cycle(tmp_path):
    r"""The plan `a -> b`, `b -> a` should swap the two files, and `a -> b`, `b -> c`, `c -> a` should rotate
    three, with nothing else left in the folder afterwards."""
    root = folder(tmp_path, ABC, "swap")
    assert run(tmp_path, root, "a -> b\nb -> a\n").returncode == 0 and tree(root) == {"a": "B", "b": "A", "c": "C"}
    root = folder(tmp_path, ABC, "cycle")
    assert run(tmp_path, root, "a -> b\nb -> c\nc -> a\n").returncode == 0
    assert tree(root) == {"a": "C", "b": "A", "c": "B"} and folders(root) == []


def test_a_target_that_stays_is_an_error(tmp_path):
    r"""With files a, b and c, the plan `c -> d`, `a -> b` should exit with code 2 and rename nothing, since b
    exists and the plan does not move it away. c is renamed although the run failed, or b is overwritten."""
    root = folder(tmp_path, ABC)
    result = run(tmp_path, root, "c -> d\na -> b\n")
    assert result.returncode == 2 and result.stderr.startswith("error:") and tree(root) == ABC


def test_bad_plans_change_nothing(tmp_path):
    r"""A plan with two sources for one target (`a -> x`, `b -> x`), the same source twice (`a -> x`, `a -> y`) or
    a source that does not exist (`a -> x`, `nope -> y`) should exit with code 2 and rename nothing."""
    root = folder(tmp_path, ABC)
    for plan in ("a -> x\nb -> x\n", "a -> x\na -> y\n", "a -> x\nnope -> y\n", "a -> x\nthis is not a rename\n"):
        result = run(tmp_path, root, plan)
        assert result.returncode == 2 and tree(root) == ABC, plan


def test_equal_names_are_skipped(tmp_path):
    r"""The plan `a -> a`, `b -> x` should rename b only and print `renamed 1 files`: a line whose two names are
    the same is skipped and not counted."""
    root = folder(tmp_path, ABC)
    result = run(tmp_path, root, "a -> a\nb -> x\n")
    assert result.stdout.strip() == "renamed 1 files" and tree(root) == {"a": "A", "x": "B", "c": "C"}


def test_dry_run(tmp_path):
    r"""`--dry-run` should print `a -> b` and `b -> c` for that plan and change nothing in the folder."""
    root = folder(tmp_path, ABC)
    result = run(tmp_path, root, "a -> d\nb -> e\n", "--dry-run")
    assert sorted(lines(result)) == ["a -> d", "b -> e"] and tree(root) == ABC


def test_the_undo_file_restores_the_folder(tmp_path):
    r"""After a run with `--undo-file undo.txt` for the plan `a -> b`, `b -> a`, `c -> e`, `d -> sub/d`, running
    replan with undo.txt as the plan should give back the folder as it was: the same files with the same content
    and no folder `sub`. The undo plan fails, or the folder is not what it was."""
    files = {**ABC, "d": "D"}
    root, undo = folder(tmp_path, files), tmp_path / "undo.txt"
    assert run(tmp_path, root, "a -> b\nb -> a\nc -> e\nd -> sub/d\n", "--undo-file", undo).returncode == 0
    assert tree(root) == {"a": "B", "b": "A", "e": "C", "sub/d": "D"}
    assert cli(root, undo).returncode == 0
    assert tree(root) == files and folders(root) == []


def test_no_undo_file_without_a_successful_run(tmp_path):
    r"""`--undo-file` should write nothing with `--dry-run`, and nothing when the plan is refused."""
    root, undo = folder(tmp_path, ABC), tmp_path / "undo.txt"
    run(tmp_path, root, "a -> x\n", "--dry-run", "--undo-file", undo)
    run(tmp_path, root, "a -> b\n", "--undo-file", undo)
    assert not undo.exists() and tree(root) == ABC


def test_subfolders(tmp_path):
    r"""The plan `old/x -> new/deep/x`, `old/y -> y` should create new/deep and remove the folder old, which the
    run leaves empty. A source folder that still holds a file should stay."""
    root = folder(tmp_path, {"old/x": "X", "old/y": "Y", "z": "Z"})
    assert run(tmp_path, root, "old/x -> new/deep/x\nold/y -> y\n").returncode == 0
    assert tree(root) == {"new/deep/x": "X", "y": "Y", "z": "Z"} and folders(root) == ["new", "new/deep"]
    root = folder(tmp_path, {"old/x": "X", "old/keep": "K"}, "g")
    assert run(tmp_path, root, "old/x -> x\n").returncode == 0 and folders(root) == ["old"]


def test_a_refused_rename_undoes_the_rest(tmp_path):
    r"""When one rename is refused by the operating system, here `b -> locked/b` into a folder without write
    permission, the run should exit with code 1 and leave a, b and c where they were, though `a -> a2` and
    `c -> c2` are fine on their own. A file stays renamed after the failed run, or a temporary name is left behind."""
    root = folder(tmp_path, ABC)
    (root / "locked").mkdir()
    (root / "locked").chmod(0o555)
    try:
        result = run(tmp_path, root, "a -> a2\nb -> locked/b\nc -> c2\n")
        assert result.returncode == 1 and result.stderr.startswith("error:")
        assert tree(root) == ABC and folders(root) == ["locked"]
    finally:
        (root / "locked").chmod(0o755)
''',
    change="Add `--verbose`: a run then prints one `OLD -> NEW` line per renamed file, in the order of the plan's "
    "lines, before the `renamed N files` line. Add a test and run the tests.",
    change_checks=r"""
def test_verbose(tmp_path):
    root = tmp_path / "f"
    root.mkdir()
    for name in ("a", "b"):
        (root / name).write_text(name)
    plan = tmp_path / "plan.txt"
    plan.write_text("b -> c\na -> b\n")
    assert lines(cli(root, plan, "--verbose")) == ["b -> c", "a -> b", "renamed 2 files"]
    assert (root / "b").read_text() == "a" and (root / "c").read_text() == "b"
""",
)

SOFTWRAP = Task(
    id="softwrap",
    package="softwrap",
    style="tools",
    prompt="""Build a Python package `softwrap` in this folder. It re-wraps plain text to a width in terminal columns.
Two rules hold for everything it does: every line holds as much as fits (text goes to the next line only when it does
not fit on this one), and wrapping its own output again at the same width changes nothing.
1. softwrap/core.py: wrap(text, width=80) re-flows the words of each paragraph into lines no wider than `width`.
   Paragraphs are separated by one or more blank lines and come out separated by exactly one. Words are separated
   by spaces, tabs and newlines, and by nothing else. A word wider than the width goes on a line of its own. It
   returns the text without a trailing newline.
2. A CLI: `python -m softwrap [FILE] [--width N]` reads the file, or stdin when none is given, and prints the
   result. A width below 1 is a usage error (exit code 2).
3. pytest tests in tests/, at least 12.
4. Width is counted in terminal columns, not characters: East Asian wide and fullwidth characters
   (unicodedata.east_asian_width of W or F) take two columns; combining marks (unicodedata.combining is not 0) and
   characters of category Cf, such as U+200B and U+00AD, take none.
5. Breaks inside words. A word may be broken after a hyphen `-` that stands between two letters (`well-known` to
   `well-` and `known`), and at a soft hyphen U+00AD, which then becomes a `-` at the end of the line; a soft hyphen
   that is not used for a break stays in the text as it is. A no-break space U+00A0 is one column wide and never
   separates words: it is part of its word. The first rule holds here too: a line takes as much of a word as fits,
   up to the last break that fits.
6. `--justify` pads the spaces of every line except the last of each paragraph until the line is exactly `width`
   columns wide, the extra spaces going to the leftmost gaps first; no-break spaces are never padded, and a line
   with no space in it is left alone. `--check` prints nothing and exits with code 1 when wrapping would change the
   input, and 0 otherwise. `--in-place` writes the result to FILE and prints nothing; it needs FILE, and it cannot
   be combined with --check: both are usage errors (exit code 2).
7. README.md and CHANGELOG.md.
"""
    + ALL_STEPS
    + FILE_TOOLS,
    checks=r'''
SHY, NBSP = "\u00ad", "\u00a0"


def wrap(text, width, **options):
    return api("softwrap.core").wrap(text, width=width, **options).split("\n")


def test_a_break_after_a_hyphen():
    r"""wrap("a well-known fact", width=7) should give `a well-`, `known`, `fact`: `a well-` is exactly seven
    columns, so that much of the word fits on the first line."""
    assert wrap("a well-known fact", 7) == ["a well-", "known", "fact"]


def test_as_much_of_a_word_as_fits():
    r"""wrap("x one-two-three-four", width=10) should give `x one-two-` and `three-four`: the line takes the word
    up to the last break that fits. And wrap("aaa bbb-ccc", width=8) should give `aaa bbb-` and `ccc`."""
    assert wrap("x one-two-three-four", 10) == ["x one-two-", "three-four"]
    assert wrap("aaa bbb-ccc", 8) == ["aaa bbb-", "ccc"]


def test_only_a_hyphen_between_letters_is_a_break():
    r"""wrap("in 10-12 days", width=6) should give `in`, `10-12`, `days`, and wrap("go --fast now", width=5) should
    give `go`, `--fast`, `now`: a hyphen between digits or at the start of a word is not a place to break."""
    assert wrap("in 10-12 days", 6) == ["in", "10-12", "days"]
    assert wrap("go --fast now", 5) == ["go", "--fast", "now"]


def test_a_soft_hyphen_that_is_used():
    r"""wrap("an extra\u00adordinary day", width=9) should give `an extra-`, `ordinary`, `day`: the soft hyphen
    becomes a real hyphen at the end of the line. wrap("extra\u00adordinary", width=6) should give `extra-` and
    `ordinary`."""
    assert wrap(f"an extra{SHY}ordinary day", 9) == ["an extra-", "ordinary", "day"]
    assert wrap(f"extra{SHY}ordinary", 6) == ["extra-", "ordinary"]


def test_a_soft_hyphen_that_is_not_used():
    r"""wrap("co\u00adop is ok", width=7) should give `co\u00adop is` and `ok`, the soft hyphen still in the text:
    it takes no column, so `co\u00adop is` is seven columns wide."""
    assert wrap(f"co{SHY}op is ok", 7) == [f"co{SHY}op is", "ok"]


def test_a_no_break_space_keeps_its_word_together():
    r"""wrap("see Fig.\u00a07 below", width=8) should give `see`, `Fig.\u00a07`, `below`: the no-break space is
    part of the word, so the text is never broken there, and it stays a no-break space."""
    assert wrap(f"see Fig.{NBSP}7 below", 8) == ["see", f"Fig.{NBSP}7", "below"]


def test_wrapping_the_output_again_changes_nothing():
    r"""Wrapping softwrap's own output again at the same width should change nothing. It does for one of:
    "a well-known fact" at 7, "x one-two-three-four" at 10, "an extra\u00adordinary day" at 9, "日本語 well-known
    text" at 9."""
    cases = (("a well-known fact", 7), ("x one-two-three-four", 10), (f"an extra{SHY}ordinary day", 9))
    for text, width in (*cases, ("日本語 well-known text", 9), (f"aa bb{NBSP}cc dd ee-ff", 8)):
        once = api("softwrap.core").wrap(text, width=width)
        assert api("softwrap.core").wrap(once, width=width) == once, (text, width)


def test_justify_never_pads_a_no_break_space():
    r"""`--justify --width 12` on `aa bb\u00a0cc dd ee ff gg` should print `aa  bb\u00a0cc dd` and `ee ff gg`:
    the first line is eleven columns wide, so one space is added, in the leftmost gap, and never at the no-break
    space."""
    result = cli("--width", "12", "--justify", stdin=f"aa bb{NBSP}cc dd ee ff gg\n")
    assert result.stdout.rstrip("\n").split("\n") == [f"aa  bb{NBSP}cc dd", "ee ff gg"]


def test_check_and_in_place(tmp_path):
    r"""`--check --width 7` should exit with code 0 for a file that holds `a well-`, `known`, `fact` on three lines
    and with code 1 for `a well-known fact`. `--in-place --width 7` should rewrite that file to the three lines and
    print nothing. `--in-place` without a file, or together with --check, should exit with code 2 and write
    nothing."""
    good, bad = tmp_path / "good.txt", tmp_path / "bad.txt"
    good.write_text("a well-\nknown\nfact\n")
    bad.write_text("a well-known fact\n")
    assert cli(good, "--width", "7", "--check").returncode == 0
    assert cli(bad, "--width", "7", "--check").returncode == 1
    assert cli(bad, "--width", "7", "--check", "--in-place").returncode == 2
    assert cli("--width", "7", "--in-place", stdin="a b\n").returncode == 2
    assert bad.read_text() == "a well-known fact\n"
    result = cli(bad, "--width", "7", "--in-place")
    assert (result.returncode, result.stdout) == (0, "") and bad.read_text().rstrip("\n") == "a well-\nknown\nfact"
''',
    change="Add `--indent N` to the CLI and wrap(text, width, indent=N): every output line starts with N spaces, which "
    "count toward the width. Add a test and run the tests.",
    change_checks=r"""
def test_indent():
    assert api("softwrap.core").wrap("aa bb cc", width=7, indent=2).split("\n") == ["  aa bb", "  cc"]
    assert cli("--width", "8", "--indent", "2", stdin="a well-known\n").stdout.rstrip("\n").split("\n") == [
        "  a",
        "  well-",
        "  known",
    ]
""",
)

DEPSOLVE = Task(
    id="depsolve",
    package="depsolve",
    style="free",
    prompt="""Build a Python package `depsolve` in this folder: a small dependency resolver.
1. depsolve/core.py: an index is a dict {package: {version: {dependency: spec}}}. Versions are three numbers
   (`1.2.0`) and compare as numbers. A spec is a comma-separated list of comparisons that must all hold
   (`>=1.0.0,<2.0.0`, `==1.4.2`, `!=1.3.0`, also `<=` and `>`), or `*` for any version. parse_version(text) and
   matches(version, spec); malformed input raises ValueError.
2. solve(index, requirements), with requirements a dict {package: spec}, returns {package: version}: the required
   packages and everything they depend on, and no package that nothing needs, such that every spec is met. It
   finds a solution whenever one exists and raises Unsolvable otherwise. Among the solutions it returns the best by
   this order: go through all packages of the index in alphabetical order; at the first one where two solutions
   differ, the one with the higher version wins, and one that has the package wins over one that does not.
3. pytest tests in tests/, at least 12.
4. A CLI: `python -m depsolve INDEX.json REQ...`, with requirements written `name` or `name>=1.0.0,<2.0.0`. It
   prints `name==version` lines sorted by name. Unsolvable prints `error: ...` on stderr and exits with code 1; a
   malformed version or spec exits with code 2.
5. Conflicts: a version's entry may also be written {"requires": {dependency: spec}, "conflicts": {package:
   spec}}; a version cannot be in a solution together with a version of a package that matches one of its
   conflicts. Both forms may be mixed in one index.
6. A lock: solve(index, requirements, lock={package: version}) and `--lock LOCK.json`. Among the solutions it
   returns one that changes as few locked packages as possible: a locked package that comes out at another version,
   or not at all, is one change, and packages that are not in the lock do not count. Among those the order of step 2
   decides. With `--explain` the CLI then also prints one line `changed NAME: OLD -> NEW` or `dropped NAME: OLD`
   for every change, sorted by name.
7. README.md and CHANGELOG.md.
"""
    + ALL_STEPS,
    checks=r'''
CHAIN = {
    "a": {"1.0.0": {"b": "==2.0.0"}, "2.0.0": {}},
    "b": {"1.0.0": {}, "2.0.0": {"c": "==2.0.0"}},
    "c": {"1.0.0": {}, "2.0.0": {}},
    "x": {"1.0.0": {}},
}
ALL = {"a": "*", "b": "*", "c": "*"}


def solve(index, requirements, **options):
    return dict(api("depsolve.core").solve(index, requirements, **options))


def run(tmp_path, index, *args, lock=None):
    (tmp_path / "index.json").write_text(json.dumps(index))
    if lock is not None:
        (tmp_path / "lock.json").write_text(json.dumps(lock))
        args = ("--lock", tmp_path / "lock.json", *args)
    return cli(tmp_path / "index.json", *args)


def test_it_goes_back_on_a_choice_that_cannot_work():
    r"""With a 2.0.0 needing c==1.0.0, a 1.0.0 needing nothing and b 1.0.0 needing c==2.0.0, solve for a and b
    should return a 1.0.0, b 1.0.0, c 2.0.0: the highest a cannot work. It raises Unsolvable or returns something
    else."""
    index = {
        "a": {"2.0.0": {"c": "==1.0.0"}, "1.0.0": {}},
        "b": {"1.0.0": {"c": "==2.0.0"}},
        "c": {"1.0.0": {}, "2.0.0": {}},
    }
    assert solve(index, {"a": "*", "b": "*"}) == {"a": "1.0.0", "b": "1.0.0", "c": "2.0.0"}


def test_only_what_is_needed_and_the_best_by_the_order():
    r"""In an index where a 1.0.0 needs b==2.0.0, b 2.0.0 needs c==2.0.0 and x is needed by nothing, solve for a, b
    and c should return a 2.0.0, b 2.0.0, c 2.0.0 and no x; solve for b alone with `<2.0.0` should return only b
    1.0.0."""
    assert solve(CHAIN, ALL) == {"a": "2.0.0", "b": "2.0.0", "c": "2.0.0"}
    assert solve(CHAIN, {"b": "<2.0.0"}) == {"b": "1.0.0"}


def test_conflicts():
    r"""With a 2.0.0 written {"requires": {}, "conflicts": {"b": ">=2.0.0"}} and a 1.0.0, b 1.0.0 and b 2.0.0 in
    the old form, solve for a and b should return a 2.0.0 with b 1.0.0, and with b required `>=2.0.0` it should
    return a 1.0.0 with b 2.0.0. A conflict declared by the other package counts as well."""
    index = {
        "a": {"2.0.0": {"requires": {}, "conflicts": {"b": ">=2.0.0"}}, "1.0.0": {}},
        "b": {"2.0.0": {}, "1.0.0": {}},
    }
    assert solve(index, {"a": "*", "b": "*"}) == {"a": "2.0.0", "b": "1.0.0"}
    assert solve(index, {"a": "*", "b": ">=2.0.0"}) == {"a": "1.0.0", "b": "2.0.0"}
    index = {"a": {"2.0.0": {}, "1.0.0": {}}, "z": {"1.0.0": {"conflicts": {"a": ">=2.0.0"}}}}
    assert solve(index, {"a": "*", "z": "*"}) == {"a": "1.0.0", "z": "1.0.0"}


def test_a_conflict_with_no_way_out_is_unsolvable():
    r"""When the only version of a conflicts with every b, solve for a and b should raise Unsolvable."""
    index = {"a": {"1.0.0": {"requires": {}, "conflicts": {"b": "*"}}}, "b": {"1.0.0": {}}}
    with pytest.raises(api("depsolve.core").Unsolvable):
        solve(index, {"a": "*", "b": "*"})


def test_the_lock_changes_as_few_packages_as_possible():
    r"""With a 1.0.0 needing b==2.0.0 and b 2.0.0 needing c==2.0.0, and a, b and c all locked at 1.0.0, solve for
    a, b and c should return a 2.0.0, b 1.0.0, c 1.0.0: that changes one locked package, while keeping a at 1.0.0
    would change two (b and c). It returns a solution with more changes."""
    lock = {"a": "1.0.0", "b": "1.0.0", "c": "1.0.0"}
    assert solve(CHAIN, ALL, lock=lock) == {"a": "2.0.0", "b": "1.0.0", "c": "1.0.0"}


def test_packages_outside_the_lock_follow_the_order():
    r"""With only b locked at 1.0.0 in that index, solve for a, b and c should return a 2.0.0, b 1.0.0, c 2.0.0:
    b stays, and the packages that are not locked take the best versions by the usual order."""
    assert solve(CHAIN, ALL, lock={"b": "1.0.0"}) == {"a": "2.0.0", "b": "1.0.0", "c": "2.0.0"}


def test_a_lock_that_cannot_be_kept():
    r"""With a locked at 1.0.0 and b at 1.0.0, where a 1.0.0 needs b==2.0.0, and a required as `<2.0.0`, solve
    should still succeed, with a 1.0.0, b 2.0.0 and c 2.0.0: a lock is a preference, not a requirement."""
    assert solve(CHAIN, {"a": "<2.0.0"}, lock={"a": "1.0.0", "b": "1.0.0"}) == {
        "a": "1.0.0",
        "b": "2.0.0",
        "c": "2.0.0",
    }


def test_cli(tmp_path):
    r"""`python -m depsolve INDEX.json a "b<2.0.0"` should print `a==2.0.0` and `b==1.0.0`; a requirement that
    cannot be met should print `error: ...` and exit with code 1, and a malformed spec like `a>=x` with code 2."""
    assert lines(run(tmp_path, CHAIN, "a", "b<2.0.0")) == ["a==2.0.0", "b==1.0.0"]
    missing = run(tmp_path, CHAIN, "a>=3.0.0")
    assert missing.returncode == 1 and missing.stderr.startswith("error:")
    assert run(tmp_path, CHAIN, "a>=x").returncode == 2


def test_explain(tmp_path):
    r"""With a, b, c and x locked at 1.0.0, `--lock LOCK.json --explain a b c` should print the three `name==`
    lines, then `changed a: 1.0.0 -> 2.0.0` and `dropped x: 1.0.0`: x is locked and no longer needed."""
    lock = {"a": "1.0.0", "b": "1.0.0", "c": "1.0.0", "x": "1.0.0"}
    result = run(tmp_path, CHAIN, "--explain", "a", "b", "c", lock=lock)
    assert lines(result) == ["a==2.0.0", "b==1.0.0", "c==1.0.0", "changed a: 1.0.0 -> 2.0.0", "dropped x: 1.0.0"]
''',
    change="Add `--json` to the CLI: it prints the result as one JSON object {name: version} in place of the "
    "`name==version` lines. Add a test and run the tests.",
    change_checks=r"""
def test_json(tmp_path):
    index = {"a": {"1.0.0": {"b": "*"}}, "b": {"1.0.0": {}, "2.0.0": {}}}
    (tmp_path / "index.json").write_text(json.dumps(index))
    assert json.loads(cli(tmp_path / "index.json", "--json", "a").stdout) == {"a": "1.0.0", "b": "2.0.0"}
""",
)

CASHBOOK = Task(
    id="cashbook",
    package="cashbook",
    style="free",
    prompt="""Build a Python package `cashbook` in this folder: named accounts with balances, kept in a JSON file. Two
rules hold for every command: no cent is ever created or lost (the sum of all balances changes only through a deposit
or a withdrawal, and by exactly its amount), and a command that fails changes nothing in the file.
1. cashbook/core.py: Book(path) with open_account(name), deposit(name, amount), withdraw(name, amount),
   transfer(src, dst, amount) and balance(name). Amounts are strings like `12.34`, `5` or `0.5`: parse them
   exactly, without floats. A negative amount, zero, more than two decimals or anything that is not a number
   raises ValueError; an unknown account raises KeyError; taking more than an account holds raises
   InsufficientFunds. The file is saved after every change.
2. A CLI: `python -m cashbook FILE open NAME`, `deposit NAME AMOUNT`, `withdraw NAME AMOUNT`,
   `transfer SRC DST AMOUNT`, `balance NAME`, which prints like `12.34`, and `balances`, which prints `NAME 12.34`
   lines sorted by name and then `TOTAL 99.99`. Errors print `error: ...` on stderr: exit code 2 for a bad amount or
   an unknown account, 1 for insufficient funds.
3. pytest tests in tests/, at least 12.
4. `split SRC AMOUNT DST... [--weights W,W,...]` moves AMOUNT from SRC to the DST accounts in proportion to the
   weights, which are whole numbers, one per DST (equal shares without the option). Each share is rounded down to
   the cent, and the cents left over go, one each, to the accounts with the largest remainders, the earlier account
   first on a tie.
5. Fees: `transfer SRC DST AMOUNT --fee-percent P` also moves a fee of P percent of AMOUNT (P like `1.5`), rounded
   half up to the cent, from SRC to the account `fees`, which is opened when it does not exist yet. SRC must hold
   the amount and the fee together.
6. `apply ROWS.csv` runs the rows of a CSV file: `open,NAME`, `deposit,NAME,AMOUNT`, `withdraw,NAME,AMOUNT` or
   `transfer,SRC,DST,AMOUNT[,FEE_PERCENT]`. It runs all of them or, when a row fails, none of them: it then prints
   `error: line N: ...` on stderr and exits with code 2. On success it prints `applied N rows`.
7. README.md and CHANGELOG.md.
"""
    + ALL_STEPS,
    checks=r'''
def book(tmp_path, **deposits):
    file = tmp_path / "book.json"
    for name, amount in deposits.items():
        assert cli(file, "open", name).returncode == 0
        if amount:
            assert cli(file, "deposit", name, amount).returncode == 0
    return file


def balances(file):
    rows = [line.rsplit(" ", 1) for line in lines(cli(file, "balances"))]
    return {name: amount for name, amount in rows}


def refused(file, *args, code):
    before = file.read_bytes()
    result = cli(file, *args)
    return result.returncode == code and file.read_bytes() == before


def test_split_gives_the_cents_left_over_to_the_largest_remainders(tmp_path):
    r"""`split src 0.10 a b c --weights 3,3,1` should give a 0.04, b 0.04 and c 0.02: the shares are 4.29, 4.29 and
    1.43 cents, so after rounding down one cent is left, and c has the largest remainder. And `split src 0.10 a b
    --weights 1,2` should give a 0.03 and b 0.07."""
    file = book(tmp_path, src="10", a="", b="", c="")
    assert cli(file, "split", "src", "0.10", "a", "b", "c", "--weights", "3,3,1").returncode == 0
    assert balances(file) == {"a": "0.04", "b": "0.04", "c": "0.02", "src": "9.90", "TOTAL": "10.00"}
    assert cli(file, "split", "src", "0.10", "a", "b", "--weights", "1,2").returncode == 0
    assert balances(file) == {"a": "0.07", "b": "0.11", "c": "0.02", "src": "9.80", "TOTAL": "10.00"}


def test_split_in_equal_shares(tmp_path):
    r"""`split src 1.00 a b c` should give 0.34, 0.33, 0.33, and `split src 0.05 a b c` 0.02, 0.02, 0.01: on a
    tie the earlier account gets the cent."""
    file = book(tmp_path, src="10", a="", b="", c="")
    cli(file, "split", "src", "1.00", "a", "b", "c")
    assert balances(file) == {"a": "0.34", "b": "0.33", "c": "0.33", "src": "9.00", "TOTAL": "10.00"}
    cli(file, "split", "src", "0.05", "a", "b", "c")
    assert balances(file) == {"a": "0.36", "b": "0.35", "c": "0.34", "src": "8.95", "TOTAL": "10.00"}


def test_a_split_that_fails_changes_nothing(tmp_path):
    r"""`split` to an account that does not exist, with the wrong number of weights or for more than SRC holds
    should fail (exit codes 2, 2 and 1) and leave the file as it was."""
    file = book(tmp_path, src="1", a="", b="")
    assert refused(file, "split", "src", "0.50", "a", "nope", code=2)
    assert refused(file, "split", "src", "0.50", "a", "b", "--weights", "1,2,3", code=2)
    assert refused(file, "split", "src", "5", "a", "b", code=1)


def test_the_fee_is_rounded_half_up(tmp_path):
    r"""`transfer src dst 1.00 --fee-percent 0.5` should move a fee of 0.01 to `fees`: half a cent is rounded up.
    Then `transfer src dst 1.00 --fee-percent 2.5` should add 0.03, and `transfer src dst 3.00 --fee-percent 3.5`
    0.11, so `fees` holds 0.15 and src 4.85 of its 10.00."""
    file = book(tmp_path, src="10", dst="")
    for amount, percent in (("1.00", "0.5"), ("1.00", "2.5"), ("3.00", "3.5")):
        assert cli(file, "transfer", "src", "dst", amount, "--fee-percent", percent).returncode == 0
    assert balances(file) == {"dst": "5.00", "fees": "0.15", "src": "4.85", "TOTAL": "10.00"}


def test_a_transfer_that_cannot_pay_its_fee_changes_nothing(tmp_path):
    r"""With 1.00 in src and no account `fees` yet, `transfer src dst 1.00 --fee-percent 1` should exit with code
    1, since src cannot pay 1.01, and leave the file byte for byte as it was. The file changes although the command
    failed, for instance by gaining the account `fees`."""
    file = book(tmp_path, src="1", dst="")
    assert refused(file, "transfer", "src", "dst", "1.00", "--fee-percent", "1", code=1)


def test_apply_is_all_or_nothing(tmp_path):
    r"""`apply` with the rows `deposit,a,5`, `transfer,a,b,2`, `withdraw,b,9` should print `error: line 3: ...`,
    exit with code 2 and leave the file byte for byte as it was. The first rows are applied although the third
    failed."""
    file, rows = book(tmp_path, a="1", b=""), tmp_path / "rows.csv"
    rows.write_text("deposit,a,5\ntransfer,a,b,2\nwithdraw,b,9\n")
    before = file.read_bytes()
    result = cli(file, "apply", rows)
    assert result.returncode == 2 and result.stderr.startswith("error: line 3:") and file.read_bytes() == before
    rows.write_text("deposit,a,5\nbogus,a\n")
    assert refused(file, "apply", rows, code=2)


def test_apply_runs_its_rows_in_order(tmp_path):
    r"""`apply` with `open,c`, `deposit,c,1.50`, `transfer,c,a,0.50,10` should print `applied 3 rows` and leave a
    1.50, c 0.95 and fees 0.05: each row works on what the rows before it did."""
    file, rows = book(tmp_path, a="1"), tmp_path / "rows.csv"
    rows.write_text("open,c\ndeposit,c,1.50\ntransfer,c,a,0.50,10\n")
    result = cli(file, "apply", rows)
    assert (result.returncode, result.stdout.strip()) == (0, "applied 3 rows")
    assert balances(file) == {"a": "1.50", "c": "0.95", "fees": "0.05", "TOTAL": "2.50"}


def test_amounts_are_exact(tmp_path):
    r"""Three deposits of 0.29 and one of 0.13 should give a balance of exactly 1.00, and `5` should print as
    `5.00`. `1.005`, `0`, `abc` and `1e2` are not amounts: exit code 2 and nothing changed."""
    file = book(tmp_path, a="")
    for amount in ("0.29", "0.29", "0.29", "0.13", "5"):
        assert cli(file, "deposit", "a", amount).returncode == 0
    assert cli(file, "balance", "a").stdout.strip() == "6.00"
    for amount in ("1.005", "0", "abc", "1e2"):
        assert refused(file, "deposit", "a", amount, code=2), amount


def test_failed_commands_change_nothing(tmp_path):
    r"""`withdraw a 5` from an account that holds 1.00 should exit with code 1, and `deposit nope 1` and
    `transfer a nope 0.50` with code 2, each leaving the file as it was."""
    file = book(tmp_path, a="1")
    assert refused(file, "withdraw", "a", "5", code=1)
    assert refused(file, "deposit", "nope", "1", code=2)
    assert refused(file, "transfer", "a", "nope", "0.50", code=2)
''',
    change="Add `python -m cashbook FILE close NAME`: it removes an account whose balance is zero. A balance that is "
    "not zero is an error with exit code 1, an unknown account with exit code 2. Add a test and run the tests.",
    change_checks=r"""
def test_close(tmp_path):
    file = tmp_path / "book.json"
    for args in (("open", "a"), ("open", "b"), ("deposit", "b", "1")):
        cli(file, *args)
    assert cli(file, "close", "a").returncode == 0
    assert cli(file, "close", "b").returncode == 1 and cli(file, "close", "nope").returncode == 2
    names = [line.split()[0] for line in cli(file, "balances").stdout.splitlines()]
    assert names == ["b", "TOTAL"]
""",
)

PRINCIPLES: tuple[Task, ...] = (KVTX, REPLAN, SOFTWRAP, DEPSOLVE, CASHBOOK)
"""Tasks that state a rule once, at the top, which the later steps have to keep without being reminded of it."""


TASKS: tuple[Task, ...] = (
    TEXTSTATS,
    LEDGER,
    MDTOC,
    SEMVER,
    KVLOG,
    URLNORM,
    LOGSTAT,
    TODO,
    SPANS,
    TMPL,
    NUMWORDS,
    TABQ,
    CRONNEXT,
    WRAPIT,
    *TRAPS,
    *PRINCIPLES,
)
