from __future__ import annotations

import argparse
import asyncio
import contextlib
import sqlite3
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from intern import names
from intern.config import Bootstrap, Options
from intern.decisions import DecisionClient
from intern.events import Decision, Event
from intern.memory.drive import MAIN, STEWARD_LOCK, TREE_LOCK, GitError, create, locked, main_commit, tracked_files
from intern.memory.forget import Removal, matching, settle, widened
from intern.memory.forget import apply as apply_removal
from intern.memory.format import page_file
from intern.memory.lint import fix, lint, working_files
from intern.memory.search import refresh
from intern.memory.spend import add_spend
from intern.memory.steward.git import PROPOSED, commit_proposal, publish, recover
from intern.memory.steward.run import Deferred, Keep, Skipped, commit, propose
from intern.memory.steward.write import StewardError
from intern.memory.steward.writer import writer_from_ref
from intern.messages import Usage
from intern.models.registry import ConfigError

EXIT_OK = 0
EXIT_FAILED = 1
EXIT_USAGE = 2


def _init(root: Path) -> int:
    if root.exists() and any(root.iterdir()):
        print(f"{names.NAME}: {root} is not empty", file=sys.stderr)
        return EXIT_USAGE
    try:
        create(root)
    except GitError as e:
        print(f"{names.NAME}: {e}", file=sys.stderr)
        return EXIT_FAILED
    print(f"created a memory drive at {root}")
    return EXIT_OK


def _lint(root: Path, apply_fix: bool) -> int:
    if not root.is_dir():
        print(f"{names.NAME}: {root} is not a directory", file=sys.stderr)
        return EXIT_USAGE
    files = working_files(root)
    if apply_fix:
        for name, text in fix(files).items():
            (root / name).parent.mkdir(parents=True, exist_ok=True)
            (root / name).write_text(text)
            print(f"rewrote {name}")
        files = working_files(root)
    problems = lint(files)
    for problem in problems:
        print(problem)
    print(f"{len(problems)} problems in {len(files)} files", file=sys.stderr)
    return EXIT_FAILED if problems else EXIT_OK


EXIT_DEFERRED = 3
DEFERRED_FILE = "nace-steward-deferred"


def _decision_sink(event: Event) -> None:
    if isinstance(event, Decision) and not event.ok and event.error:
        print(f"{names.NAME}: {event.purpose}: {event.error}", file=sys.stderr)


def _deferred_file(drive: Path) -> Path | None:
    # Journals a run deferred, retried by the next run on this checkout; kept in git's own directory, so it is
    # never part of the drive.
    git = drive / ".git"
    return git / DEFERRED_FILE if git.is_dir() else None


@dataclass(frozen=True)
class OnFree:
    """Which of the steward's models run on the free credit, as the session's systems did."""

    system1: bool = False
    writer: bool = False


async def _steward(
    journals: list[Path],
    drive: Path,
    system1: str | None,
    writer_ref: str | None,
    world: str = "",
    merge: bool = False,
    free: OnFree | None = None,
    keep: Keep | None = None,
) -> int:
    with locked(drive, STEWARD_LOCK):
        return await _steward_locked(
            journals, drive, system1, writer_ref, world, merge, free or OnFree(), keep or Keep()
        )


async def _steward_locked(
    journals: list[Path],
    drive: Path,
    system1: str | None,
    writer_ref: str | None,
    world: str,
    merge: bool,
    free: OnFree,
    keep: Keep,
) -> int:
    bootstrap = Bootstrap(Options(cwd=Path.cwd(), system1=system1))
    writer = writer_from_ref(writer_ref, bootstrap.env, free=free.writer) if writer_ref else None
    try:
        backend = bootstrap.free_system1(system1) if free.system1 and system1 else bootstrap._system1()
    except ConfigError as e:
        print(f"{names.NAME}: decision model unavailable: {e}", file=sys.stderr)
        return EXIT_FAILED
    cfg = bootstrap.config.system1
    decisions = DecisionClient(
        backend,
        _decision_sink,
        timeout_s=cfg.timeout_s,
        max_state_tokens=cfg.max_state_tokens,
        max_concurrency=cfg.max_concurrency,
        retry_window_s=cfg.retry_window_s,
    )
    await decisions.fit()
    if merge:
        try:
            with locked(drive, TREE_LOCK):
                recover(drive)
        except GitError as e:
            print(f"{names.NAME}: {e}", file=sys.stderr)
            return EXIT_FAILED
    queue = _deferred_file(drive)
    given = [j.resolve() for j in journals]
    waiting = [Path(line) for line in queue.read_text().splitlines() if line] if queue and queue.exists() else []
    retried = [j for j in dict.fromkeys(waiting) if j not in given and j.exists()]
    for journal in retried:
        print(f"retrying deferred {journal}")
    still: list[Path] = []
    deferred = failed = False
    for journal in [*retried, *given]:
        before = decisions.usage.model_copy()
        written = writer.usage if writer is not None else Usage()
        try:
            proposed = await propose(journal, drive, decisions, writer, world, keep)
            with locked(drive, TREE_LOCK):
                proposal = commit(drive, proposed)
                if merge:
                    publish(drive, proposal.branch)
            if merge:
                with contextlib.suppress(sqlite3.Error, GitError):
                    refresh(drive, proposal.commit)
        except Deferred as e:
            print(f"deferred {journal}: {e}")
            still.append(journal)
            deferred = deferred or journal in given
        except Skipped as e:
            print(f"skipped {journal}: {e}")
        except (StewardError, GitError) as e:
            print(f"{names.NAME}: {journal}: {e}", file=sys.stderr)
            failed = True
        else:
            where = MAIN if merge else proposal.branch
            print(f"{where}: {len(proposal.files)} files at {proposal.commit[:12]}")
            for name in sorted(proposal.files):
                print(f"  {name}")
            for gap in proposal.gaps:
                print(f"  not kept [{gap.pointer}]: {gap.text}")
        finally:
            with contextlib.suppress(OSError):
                after = writer.usage if writer is not None else Usage()
                add_spend(bootstrap.home, journal, decisions.usage.since(before), after - written)
    if queue is not None:
        if still:
            queue.write_text("".join(f"{j}\n" for j in still))
        else:
            queue.unlink(missing_ok=True)
    if failed:
        return EXIT_FAILED
    return EXIT_DEFERRED if deferred else EXIT_OK


def steward_main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog=f"{names.NAME} steward",
        description="Propose memory pages from finished session journals, one proposed/<session> branch each. "
        "Decision model only: a session the decision model could not answer for is deferred (exit 3) and retried by "
        "the next steward run on the same drive checkout.",
    )
    parser.add_argument("journals", nargs="+", type=Path, help="Session journal files (JSONL).")
    parser.add_argument("--drive", type=Path, required=True, help="The memory drive: a clean git checkout of main.")
    parser.add_argument(
        "--system1", help="The decision model: decisions:<model>[,...] or drex:<model>[,...]; default from config.toml."
    )
    parser.add_argument(
        "--writer",
        help="provider/model of an OpenAI-compatible LLM that phrases notes, or replay:<dir> of saved drafts; "
        "the decision model still decides.",
    )
    parser.add_argument(
        "--world",
        default="",
        help="Label of the world the sessions ran in, recorded once in .wiki/world.md. A drive holds one world: a "
        "session of another world is refused and needs its own drive.",
    )
    parser.add_argument(
        "--merge", action="store_true", help="Fast-forward main to each proposal and delete its branch."
    )
    parser.add_argument("--free-system1", action="store_true", help="The decision model runs on the free credit.")
    parser.add_argument("--free-writer", action="store_true", help="The writer runs on the free credit.")
    parser.add_argument(
        "--keep-graveyard",
        type=int,
        default=0,
        metavar="N",
        help="Delete graveyard pages archived more than N steward sessions ago; 0 keeps them.",
    )
    parser.add_argument(
        "--keep-transcripts",
        type=int,
        default=0,
        metavar="N",
        help="Delete session transcripts last written more than N steward sessions ago; 0 keeps them.",
    )
    args = parser.parse_args(argv)
    if args.keep_graveyard < 0 or args.keep_transcripts < 0:
        parser.error("--keep-graveyard and --keep-transcripts take 0 or more sessions")
    try:
        drive = args.drive.expanduser().resolve()
        free = OnFree(system1=args.free_system1, writer=args.free_writer)
        keep = Keep(graveyard=args.keep_graveyard, transcripts=args.keep_transcripts)
        return asyncio.run(
            _steward(args.journals, drive, args.system1, args.writer, args.world, args.merge, free, keep)
        )
    except ConfigError as e:
        print(f"{names.NAME}: {e}", file=sys.stderr)
        return EXIT_USAGE


def _forget(root: Path, text: str, pages: list[str], apply: bool) -> int:
    try:
        with locked(root, STEWARD_LOCK):
            return _forget_locked(root, text, [p.removesuffix(".md") for p in pages], apply)
    except (GitError, StewardError, sqlite3.Error) as e:
        print(f"{names.NAME}: {e}", file=sys.stderr)
        return EXIT_FAILED


def _forget_locked(root: Path, text: str, pages: list[str], apply: bool) -> int:
    with locked(root, TREE_LOCK):
        recover(root)
    commit = main_commit(root)
    files = tracked_files(root, commit)
    transcripts = tracked_files(root, commit, ".txt")
    if missing := [p for p in pages if page_file(p) not in files]:
        print(f"{names.NAME}: no page {', '.join(missing)} on {MAIN}", file=sys.stderr)
        return EXIT_USAGE
    found = Removal(lines=matching(files, transcripts, text) if text else [], pages=pages)
    removal = widened(files, transcripts, found)
    spots, pages = removal.lines, removal.pages
    for page in pages:
        print(f"{page if page in transcripts else page_file(page)}: the whole file")
    for spot in spots:
        where = f"{spot.file} {spot.block}".strip()
        print(f"{where}: {spot.line.strip()[:200]}")
    if not spots and not pages:
        print(f"nothing on {MAIN} matches {text!r}")
        return EXIT_OK
    if not apply:
        print(f"{len(spots)} lines and {len(pages)} files would go; pass --apply to remove them", file=sys.stderr)
        return EXIT_OK
    changed, removed = apply_removal(files, transcripts, removal)
    changed, removed = settle(files, changed, removed)
    pages_after = {n: t for n, t in (files | changed).items() if n.endswith(".md") and n not in removed}
    if problems := lint(pages_after):
        raise StewardError("the removal does not pass lint:\n" + "\n".join(problems))
    branch = f"{PROPOSED}forget-{time.strftime('%Y%m%d-%H%M%S')}"
    with locked(root, TREE_LOCK):
        done = commit_proposal(root, branch, changed, "Steward: forget", {"Intern-Forget": "command"}, removed)
        publish(root, branch)
    refresh(root, done)
    print(f"{MAIN}: {len(changed)} files rewritten, {len(removed)} removed at {done[:12]}")
    return EXIT_OK


def memory_main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog=f"{names.NAME} memory", description="Create and check memory drives, and remove from them."
    )
    commands = parser.add_subparsers(dest="command", required=True)
    init = commands.add_parser("init", help="Create a drive from the template, as a git repository on main.")
    init.add_argument("drive", type=Path)
    check = commands.add_parser("lint", help="Check a drive's working tree against the format.")
    check.add_argument("drive", type=Path)
    check.add_argument("--fix", action="store_true", help="Rewrite Contents token counts and tables of contents.")
    forget = commands.add_parser(
        "forget",
        help="Remove every line holding all the given words from the drive's pages, graveyard/ and transcripts. "
        "Lists them unless --apply; older commits still hold them.",
    )
    forget.add_argument("drive", type=Path)
    forget.add_argument("text", nargs="?", default="", help="Words a line must all hold to be removed.")
    forget.add_argument("--page", action="append", default=[], help="Remove a whole page, e.g. user/people/sam.")
    forget.add_argument("--apply", action="store_true", help="Remove them, in one commit on main.")
    args = parser.parse_args(argv)
    root = args.drive.expanduser().resolve()
    if args.command == "init":
        return _init(root)
    if args.command == "forget":
        if not args.text.strip() and not args.page:
            parser.error("forget needs words to match or --page")
        return _forget(root, args.text, args.page, args.apply)
    return _lint(root, args.fix)
