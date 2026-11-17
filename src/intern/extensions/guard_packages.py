"""What the public registries say about the packages a call installs, for System 1 to judge an install by.

Models invent package names, and attackers register them on the real registries ("slopsquatting"), so a
`pip install <name>` from PyPI is routine only when the package is established there or the project already uses
it. The guard takes the names from the install commands it parses (pip, uv, poetry, pipx, npm, pnpm, yarn, bun,
cargo and gem; not apt or brew, whose packages are curated, nor Go, whose module path is its repository and is in
the call), leaves out what the project's manifests and lockfiles list, read where the call runs, and looks the rest
up from this host: when each was first published, how many releases it has and, where the registry counts them, its
downloads. System 1 is shown these facts with the call; nothing here blocks a call by itself.

No lookup when the call or its environment points the installer at another index or registry (`--index-url`,
`--registry`, `PIP_INDEX_URL`, an `.npmrc` with a `registry`), which keeps a private package's name off the public
registries. A lookup that fails or takes longer than `LOOKUP_TIMEOUT_S` gives no facts, and the call is judged as
it would be without them.
"""

from __future__ import annotations

import asyncio
import re
import shlex
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Literal
from urllib.parse import quote

import httpx
from pydantic import BaseModel, Field, TypeAdapter

from intern import __version__
from intern.executor import Executor

if TYPE_CHECKING:
    from intern.extensions.guard import Command

Ecosystem = Literal["pypi", "npm", "crates", "rubygems"]

LOOKUP_TIMEOUT_S = 3.0  # per package; the lookups of one call run together
PROBE_TIMEOUT_S = 10.0
PROBE_OUTPUT_BYTES = 16_384
PROBE_LINES = 200  # manifest lines that match one ecosystem's names, which is plenty to tell which names matched
USER_AGENT = f"intern/{__version__} (tool-call guard; package facts)"  # crates.io refuses requests without one


class PackageFacts(BaseModel):
    """What System 1 is told about one package a call installs."""

    ecosystem: Ecosystem
    name: str
    listed: bool = Field(default=False, description="The project's manifests or lockfiles list it; not looked up.")
    exists: bool = True
    age_days: int | None = Field(default=None, description="Days since its first upload.")
    releases: int | None = None
    downloads: int | None = Field(default=None, description="Over the span the registry counts (`DOWNLOAD_SPANS`).")

    def describe(self) -> str:
        where = REGISTRY_NAMES[self.ecosystem]
        if self.listed:
            return f"`{self.name}` from {where}: the project's manifests already list it"
        if not self.exists:
            return f"`{self.name}` is not on {where}"
        parts = [f"first published {_ago(self.age_days)}"] if self.age_days is not None else []
        if self.releases is not None:
            parts.append(_count(self.releases, "release"))
        if self.downloads is not None:
            parts.append(f"{_count(self.downloads, 'download')} {DOWNLOAD_SPANS.get(self.ecosystem, '')}".rstrip())
        return f"`{self.name}` on {where}: " + ", ".join([*parts, "not in the project's manifests"])


REGISTRY_NAMES: dict[Ecosystem, str] = {"pypi": "PyPI", "npm": "npm", "crates": "crates.io", "rubygems": "RubyGems"}
_ECOSYSTEMS: dict[str, Ecosystem] = {e: e for e in REGISTRY_NAMES}
# What a registry's download count covers; PyPI's API counts none.
DOWNLOAD_SPANS: dict[Ecosystem, str] = {"npm": "last week", "crates": "in the last 90 days", "rubygems": "in all"}


def describe(packages: Iterable[PackageFacts]) -> str:
    """The facts as System 1 reads them after the call; empty when there are none."""
    lines = [f"- {p.describe()}" for p in packages]
    return "Package facts, for the packages the call installs:\n" + "\n".join(lines) if lines else ""


def _count(n: int, noun: str) -> str:
    return f"{n:,} {noun}" + ("" if n == 1 else "s")


def _ago(days: int) -> str:
    if days < 1:
        return "today"
    if days < 60:
        return f"{_count(days, 'day')} ago"
    if days < 730:
        return f"{_count(days // 30, 'month')} ago"
    return f"{_count(days // 365, 'year')} ago"


# -- install commands --------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Installer:
    """One install command form: its ecosystem, the options whose value is the next word, the options that install
    from somewhere other than the public registry (which also take a value when listed in `values`), and those whose
    value is another package to install (`uv tool install --with x`)."""

    ecosystem: Ecosystem
    values: frozenset[str] = frozenset()
    elsewhere: frozenset[str] = frozenset()
    packages: frozenset[str] = frozenset()


_PIP_INDEX = frozenset({"-i", "--index-url", "--extra-index-url", "--index", "--default-index", "-f", "--find-links"})
_PIP = Installer(
    "pypi",
    _PIP_INDEX
    | {"-r", "--requirement", "-c", "--constraint", "-e", "--editable", "-t", "--target", "--prefix", "--root"}
    | {"--platform", "--python-version", "--implementation", "--abi", "--src", "--upgrade-strategy", "--python", "-p"}
    | {"--cache-dir", "--log", "--report", "--config-settings", "-C", "--no-binary", "--only-binary", "--group"}
    | {"--trusted-host", "--cert", "--client-cert", "--proxy", "--timeout", "--retries", "--exists-action"}
    | {"--keyring-provider", "--progress-bar", "--index-strategy", "--resolution", "--prerelease", "--extra"},
    _PIP_INDEX | {"--trusted-host"},
)
_UV_ADD = Installer(
    "pypi",
    _PIP_INDEX
    | {"--group", "--optional", "--extra", "--package", "--project", "--directory", "-p", "--python", "-r"}
    | {"--requirements", "-c", "--constraints", "--tag", "--branch", "--rev", "--bounds", "-m", "--marker"}
    | {"--script", "--index-strategy", "--resolution", "--prerelease", "--cache-dir", "--config-file"},
    _PIP_INDEX,
)
_POETRY = Installer(
    "pypi",
    frozenset({"-G", "--group", "-E", "--extras", "--python", "--platform", "-C", "--directory", "--markers", "-P"})
    | {"--project", "--source"},
    frozenset({"--source"}),
)
_UV_TOOL = Installer(
    "pypi",
    _UV_ADD.values | {"--with", "--from", "--with-editable", "--with-requirements"},
    _PIP_INDEX,
    frozenset({"--with", "--from"}),
)
_PIPX = Installer(
    "pypi",
    frozenset({"--python", "--suffix", "--pip-args", "--index-url", "--preinstall"}),
    frozenset({"--index-url", "--pip-args"}),  # pip arguments may name an index
    frozenset({"--preinstall"}),
)
_NPM_REGISTRY = frozenset({"--registry"})
_NPM = Installer(
    "npm",
    _NPM_REGISTRY
    | {"--prefix", "-w", "--workspace", "--tag", "--omit", "--include", "--install-strategy", "--cache"}
    | {"--userconfig", "--globalconfig", "--loglevel", "--before", "--save-prefix", "--script-shell"},
    _NPM_REGISTRY,
)
_PNPM = Installer(
    "npm",
    _NPM_REGISTRY
    | {"-C", "--dir", "-F", "--filter", "--reporter", "--store-dir", "--virtual-store-dir", "--modules-dir"}
    | {"--lockfile-dir", "--config"},
    _NPM_REGISTRY,
)
_YARN = Installer(
    "npm",
    _NPM_REGISTRY | {"--cwd", "--network-timeout", "--modules-folder", "--cache-folder", "--mutex"},
    _NPM_REGISTRY,
)
_BUN = Installer(
    "npm",
    _NPM_REGISTRY
    | {"--cwd", "--cache-dir", "--backend", "--concurrent-scripts", "--network-concurrency"}
    | {"-c", "--config"},
    _NPM_REGISTRY,
)
_CARGO_ELSEWHERE = frozenset({"--registry", "--index", "--git", "--path"})
_CARGO_ADD = Installer(
    "crates",
    _CARGO_ELSEWHERE
    | {"-F", "--features", "-p", "--package", "--manifest-path", "--rename", "--target", "--branch", "--tag"}
    | {"--rev", "--lockfile-path", "--config", "-Z"},
    _CARGO_ELSEWHERE,
)
_CARGO_INSTALL = Installer(
    "crates",
    _CARGO_ELSEWHERE
    | {"--version", "--vers", "-F", "--features", "--root", "--target", "--target-dir", "--profile", "--bin"}
    | {"--example", "-j", "--jobs", "--config", "-Z", "--branch", "--tag", "--rev"},
    _CARGO_ELSEWHERE,
)
_GEM = Installer(
    "rubygems",
    frozenset({"-v", "--version", "-i", "--install-dir", "-n", "--bindir", "--platform", "-g", "--file", "-s"})
    | {"--source", "--document", "--build-root", "-P", "--trust-policy", "--target-rbconfig"},
    frozenset({"-s", "--source", "--clear-sources"}),
)
# The words that start each install command, after the program's name.
INSTALLERS: dict[tuple[str, ...], Installer] = {
    ("pip", "install"): _PIP,
    ("uv", "pip", "install"): _PIP,
    ("uv", "add"): _UV_ADD,
    ("uv", "tool", "install"): _UV_TOOL,
    ("poetry", "add"): _POETRY,
    ("pipx", "install"): _PIPX,
    ("npm", "install"): _NPM,
    ("npm", "i"): _NPM,
    ("npm", "add"): _NPM,
    ("pnpm", "add"): _PNPM,
    ("yarn", "add"): _YARN,
    ("yarn", "global", "add"): _YARN,
    ("bun", "add"): _BUN,
    ("bun", "install"): _BUN,
    ("bun", "i"): _BUN,
    ("cargo", "add"): _CARGO_ADD,
    ("cargo", "install"): _CARGO_INSTALL,
    ("gem", "install"): _GEM,
}
_PIP_PROGRAM = re.compile(r"^pip[\d.]*$")  # pip3, pip3.12
_PYTHON = re.compile(r"^python[\d.]*$")


@dataclass(frozen=True)
class Install:
    """The packages one command installs from a public registry, by the names the registry knows them by."""

    ecosystem: Ecosystem
    names: tuple[str, ...]
    cwd: PurePosixPath
    elsewhere: bool = False
    """It installs from another index or registry, a repository or a path: its names are not looked up."""


def installs(commands: Iterable[Command]) -> list[Install]:
    """The packages each install command names. Not what a command installs from files it names (`-r
    requirements.txt`, `npm install` alone, `-e .`), nor paths, archives or URLs."""
    found: list[Install] = []
    for command in commands:
        words = list(command.argv)
        if words and _PYTHON.match(words[0]) and words[1:3] == ["-m", "pip"]:
            words = ["pip", *words[3:]]  # python -m pip install
        if words and _PIP_PROGRAM.match(words[0]):
            words[0] = "pip"
        for prefix, installer in INSTALLERS.items():
            if tuple(words[: len(prefix)]) == prefix:
                found.extend(_install(installer, words[len(prefix) :], command.cwd))
                break
    return found


def _install(installer: Installer, args: list[str], cwd: PurePosixPath) -> list[Install]:
    specs: list[str] = []
    elsewhere = False
    options = True
    words = iter(args)
    for arg in words:
        option, given, value = arg.partition("=")
        if options and arg == "--":
            options = False
        elif options and arg.startswith("-") and arg != "-":
            attached = not arg.startswith("--") and arg[:2] in installer.elsewhere  # `-ihttps://...`
            elsewhere = elsewhere or option in installer.elsewhere or attached
            if option in installer.values and not given:
                value = next(words, "")
            if option in installer.packages and value:
                specs.append(value)
        else:
            specs.append(arg)
    names = tuple(dict.fromkeys(n for s in specs if (n := SPEC_NAMES[installer.ecosystem](s)) is not None))
    return [Install(installer.ecosystem, names, cwd, elsewhere)] if names else []


_ARCHIVES = (".whl", ".tar.gz", ".tgz", ".tar", ".zip", ".gem", ".crate")
_PYPI_NAME = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?")
_PYPI_AFTER = re.compile(r"$|[\s\[<>=!~;,@(]")  # extras, a specifier, markers, `@ version` (poetry)
_NPM_NAME = re.compile(r"(?:@[a-z0-9][a-z0-9._~-]*/)?[a-z0-9][a-z0-9._~-]*", re.IGNORECASE)
_CRATE_NAME = re.compile(r"[A-Za-z][A-Za-z0-9_-]*")
_GEM_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")


def _local(spec: str) -> bool:
    """A path, an archive or a URL: installed from there, not from the registry by name."""
    return "://" in spec or spec.startswith(("git+", ".", "/", "~")) or spec.lower().endswith(_ARCHIVES)


def pypi_name(spec: str) -> str | None:
    """`foo[bar]>=1.2` -> `foo`; None for a path, an archive, a URL or `name @ url`."""
    if _local(spec) or "/" in spec:
        return None
    found = _PYPI_NAME.match(spec)
    if found is None or not _PYPI_AFTER.match(spec, found.end()):
        return None
    return re.sub(r"[-_.]+", "-", found.group().lower())  # PEP 503: the name PyPI files it under


def npm_name(spec: str) -> str | None:
    """`lodash@^4` -> `lodash`, `@types/node@20` -> `@types/node`, `alias@npm:real@1` -> `real`; None for a
    repository (`user/repo`, `github:user/repo`), a path, an archive or a URL."""
    if _local(spec):
        return None
    at = spec.find("@", 1)
    name, version = (spec[:at], spec[at + 1 :]) if at > 0 else (spec, "")
    if version.startswith("npm:"):
        return npm_name(version.removeprefix("npm:"))
    if not _NPM_NAME.fullmatch(name) or ":" in version:
        return None
    return name.lower()


def crate_name(spec: str) -> str | None:
    """`serde@1.0` -> `serde`."""
    name = spec.split("@", 1)[0]
    return name.lower() if not _local(spec) and _CRATE_NAME.fullmatch(name) else None


def gem_name(spec: str) -> str | None:
    """`rails:7.1` -> `rails`; None for a `.gem` file or a path."""
    name = spec.split(":", 1)[0]
    return name if not _local(spec) and _GEM_NAME.fullmatch(name) else None


SPEC_NAMES = {"pypi": pypi_name, "npm": npm_name, "crates": crate_name, "rubygems": gem_name}


# -- another index or registry ---------------------------------------------------------------------------------

# Environment variables that point an installer at another index or registry.
INDEX_VARIABLES: dict[Ecosystem, tuple[str, ...]] = {
    "pypi": (
        "PIP_INDEX_URL", "PIP_EXTRA_INDEX_URL", "PIP_FIND_LINKS", "UV_INDEX_URL", "UV_EXTRA_INDEX_URL", "UV_INDEX",
        "UV_DEFAULT_INDEX", "UV_FIND_LINKS", "PIPX_DEFAULT_INDEX_URL",
    ),
    "npm": ("NPM_CONFIG_REGISTRY", "YARN_REGISTRY", "YARN_NPM_REGISTRY_SERVER", "BUN_CONFIG_REGISTRY"),
    "crates": ("CARGO_REGISTRY_DEFAULT", "CARGO_REGISTRIES_"),  # the last a prefix, CARGO_REGISTRIES_<NAME>_INDEX
    "rubygems": ("GEM_SOURCE",),
}  # fmt: skip
# The config files that can point an installer elsewhere, in the project and the home, and what says so in them.
INDEX_CONFIGS: dict[Ecosystem, tuple[tuple[str, ...], str]] = {
    "pypi": (
        ("pip.conf", "uv.toml", "pyproject.toml", "$HOME/.config/pip/pip.conf", "$HOME/.pip/pip.conf",
         "$HOME/Library/Application Support/pip/pip.conf", "$HOME/.config/uv/uv.toml"),
        r"index-url|find-links|^\[\[(tool\.uv\.)?index\]\]|^\[\[tool\.poetry\.source\]\]",
    ),
    "npm": (
        (".npmrc", ".yarnrc", ".yarnrc.yml", "bunfig.toml", "$HOME/.npmrc", "$HOME/.yarnrc", "$HOME/.yarnrc.yml",
         "$HOME/.bunfig.toml"),
        "registry",
    ),
    "crates": ((".cargo/config.toml", ".cargo/config", "$HOME/.cargo/config.toml", "$HOME/.cargo/config"),
               r"^\[(registries|source)"),
    "rubygems": ((".gemrc", "$HOME/.gemrc"), "^:?sources"),
}  # fmt: skip
MANIFESTS: dict[Ecosystem, tuple[str, ...]] = {
    "pypi": ("pyproject.toml", "requirements*.txt", "uv.lock", "poetry.lock", "Pipfile", "Pipfile.lock"),
    "npm": ("package.json", "package-lock.json", "pnpm-lock.yaml", "yarn.lock", "bun.lock"),
    "crates": ("Cargo.toml", "Cargo.lock"),
    "rubygems": ("Gemfile", "Gemfile.lock"),
}


def elsewhere_by_environment(command: str, environ: Mapping[str, str]) -> frozenset[Ecosystem]:
    """The ecosystems whose installer the command, or the environment it runs in, points at another index."""
    names = {m.group(1) for m in re.finditer(r"\b([A-Za-z_]\w*)=", command)} | {k for k, v in environ.items() if v}
    return frozenset(e for e in INDEX_VARIABLES if any(_index_variable(e, n) for n in names))


def _index_variable(ecosystem: Ecosystem, name: str) -> bool:
    name = name.upper()  # npm reads npm_config_* in any case
    return any(name == v or (v.endswith("_") and name.startswith(v)) for v in INDEX_VARIABLES[ecosystem])


@dataclass(frozen=True)
class Project:
    """What the project's files say: the names its manifests list and the ecosystems it installs from elsewhere."""

    listed: frozenset[tuple[Ecosystem, str]]
    elsewhere: frozenset[Ecosystem]


def _name_pattern(ecosystem: Ecosystem, name: str) -> str:
    """An extended regular expression, for grep and Python alike, that finds `name` as a whole package name, under
    the spellings its registry takes as the same (`Foo_Bar` for PyPI's `foo-bar`)."""
    if ecosystem == "pypi":
        spelled = "[-_.]+".join(name.split("-"))
    elif ecosystem == "crates":
        spelled = "[-_]".join(re.split(r"[-_]", name))
    else:
        spelled = name.replace(".", r"\.")
    return rf"(^|[^A-Za-z0-9._-]){spelled}($|[^A-Za-z0-9._-])"


def project_probe(installs: list[Install], dirs: Iterable[PurePosixPath]) -> str:
    """One read-only shell command printing `listed <ecosystem> <line>` for each manifest line naming a package the
    call installs, and `elsewhere <ecosystem>` when a config file points that installer at another index. Config
    files are only matched, never printed: `~/.npmrc` may hold a token."""
    steps: list[str] = []
    ecosystems: list[Ecosystem] = list(dict.fromkeys(i.ecosystem for i in installs))
    for ecosystem in ecosystems:
        names = dict.fromkeys(n for i in installs if i.ecosystem == ecosystem for n in i.names)
        patterns = " ".join(f"-e {shlex.quote(_name_pattern(ecosystem, n))}" for n in names)
        steps.append(
            f"grep -h -o -i -s -E {patterns} -- {_files(MANIFESTS[ecosystem])} | head -n {PROBE_LINES} "
            f"| sed 's/^/listed {ecosystem} /'"
        )
        files, pattern = INDEX_CONFIGS[ecosystem]
        # -q: found in one file is found, though the others are missing
        steps.append(f"grep -q -i -s -E {shlex.quote(pattern)} -- {_files(files)} && echo 'elsewhere {ecosystem}'")
    body = "; ".join(steps)
    return "; ".join(f"(cd {shlex.quote(str(d))} 2>/dev/null && {{ {body}; }})" for d in dict.fromkeys(dirs)) + "; true"


def _files(files: Iterable[str]) -> str:
    """File names as shell words: `$HOME` expanded where the call runs, `*` left to the shell's globbing."""
    words: list[str] = []
    for file in files:
        if file.startswith("$HOME/"):
            words.append('"$HOME"/' + shlex.quote(file.removeprefix("$HOME/")))
        else:
            words.append(file if "*" in file else shlex.quote(file))
    return " ".join(words)


def read_probe(output: str, installs: list[Install]) -> Project:
    lines: dict[str, list[str]] = {}
    elsewhere: set[Ecosystem] = set()
    for line in output.splitlines():
        kind, _, rest = line.partition(" ")
        ecosystem, _, text = rest.partition(" ")
        if kind == "listed":
            lines.setdefault(ecosystem, []).append(text)
        elif kind == "elsewhere" and (known := _ECOSYSTEMS.get(ecosystem)) is not None:
            elsewhere.add(known)
    listed: set[tuple[Ecosystem, str]] = {
        (i.ecosystem, name)
        for i in installs
        for name in i.names
        if any(re.search(_name_pattern(i.ecosystem, name), t, re.IGNORECASE) for t in lines.get(i.ecosystem, []))
    }
    return Project(listed=frozenset(listed), elsewhere=frozenset(elsewhere))


async def read_project(
    executor: Executor, cwd: Path, installs: list[Install], dirs: Iterable[PurePosixPath]
) -> Project | None:
    """The project's side, read where the call runs; None when it cannot be read, so nothing is looked up."""
    try:
        result = await executor.run(
            project_probe(installs, dirs), cwd=cwd, timeout_s=PROBE_TIMEOUT_S, max_output_bytes=PROBE_OUTPUT_BYTES
        )
    except Exception:
        return None
    if result.exit_code != 0 or result.timed_out or result.aborted:
        return None
    return read_probe(result.output, installs)


# -- the registries ----------------------------------------------------------------------------------------------


class _PypiFile(BaseModel):
    upload_time_iso_8601: datetime


class _Pypi(BaseModel):
    releases: dict[str, list[_PypiFile]]


class _Npm(BaseModel):
    time: dict[str, object] = {}
    versions: dict[str, object] = {}


class _NpmDownloads(BaseModel):
    downloads: int


class _Crate(BaseModel):
    created_at: datetime
    recent_downloads: int | None = None


class _Crates(BaseModel):
    crate: _Crate
    versions: list[object] = []


class _GemVersion(BaseModel):
    created_at: datetime
    downloads_count: int = 0


_GEM_VERSIONS = TypeAdapter(list[_GemVersion])


class Registries:
    """The public registries, asked from this host once per package in a session."""

    def __init__(
        self,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        timeout_s: float = LOOKUP_TIMEOUT_S,
        now: datetime | None = None,
    ) -> None:
        self.transport = transport
        self.timeout_s = timeout_s
        self.now = now  # None: the time of each lookup
        self.cache: dict[tuple[Ecosystem, str], PackageFacts | None] = {}

    async def look_up(self, wanted: Iterable[tuple[Ecosystem, str]]) -> list[PackageFacts]:
        """The facts for each package, in order; a package whose lookup failed is left out."""
        wanted = list(dict.fromkeys(wanted))
        missing = [w for w in wanted if w not in self.cache]
        if missing:
            headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
            async with httpx.AsyncClient(
                transport=self.transport, headers=headers, timeout=self.timeout_s, follow_redirects=True
            ) as client:
                found = await asyncio.gather(*(self._one(client, ecosystem, name) for ecosystem, name in missing))
            self.cache.update(zip(missing, found, strict=True))
        return [facts for w in wanted if (facts := self.cache[w]) is not None]

    async def _one(self, client: httpx.AsyncClient, ecosystem: Ecosystem, name: str) -> PackageFacts | None:
        try:
            async with asyncio.timeout(self.timeout_s):
                return await _LOOKUPS[ecosystem](client, name, self.now or datetime.now(UTC))
        except (httpx.HTTPError, ValueError, TimeoutError):
            return None


def _days(now: datetime, first: datetime | None) -> int | None:
    return None if first is None else max(0, (now - first).days)


def _missing(response: httpx.Response) -> bool:
    if response.status_code == 404:
        return True
    response.raise_for_status()
    return False


async def _pypi(client: httpx.AsyncClient, name: str, now: datetime) -> PackageFacts:
    response = await client.get(f"https://pypi.org/pypi/{quote(name, safe='')}/json")
    if _missing(response):
        return PackageFacts(ecosystem="pypi", name=name, exists=False)
    releases = _Pypi.model_validate_json(response.content).releases
    uploads = [f.upload_time_iso_8601 for files in releases.values() for f in files]
    return PackageFacts(
        ecosystem="pypi",
        name=name,
        age_days=_days(now, min(uploads, default=None)),
        releases=sum(1 for files in releases.values() if files),
    )


async def _npm(client: httpx.AsyncClient, name: str, now: datetime) -> PackageFacts:
    document, weekly = await asyncio.gather(
        client.get(f"https://registry.npmjs.org/{quote(name, safe='@')}"),
        client.get(f"https://api.npmjs.org/downloads/point/last-week/{quote(name, safe='@/')}"),
    )
    if _missing(document):
        return PackageFacts(ecosystem="npm", name=name, exists=False)
    package = _Npm.model_validate_json(document.content)
    created = package.time.get("created")
    downloads = None if weekly.status_code != 200 else _NpmDownloads.model_validate_json(weekly.content).downloads
    return PackageFacts(
        ecosystem="npm",
        name=name,
        age_days=_days(now, datetime.fromisoformat(created) if isinstance(created, str) else None),
        releases=len(package.versions),
        downloads=downloads,
    )


async def _crates(client: httpx.AsyncClient, name: str, now: datetime) -> PackageFacts:
    response = await client.get(f"https://crates.io/api/v1/crates/{quote(name, safe='')}")
    if _missing(response):
        return PackageFacts(ecosystem="crates", name=name, exists=False)
    found = _Crates.model_validate_json(response.content)
    return PackageFacts(
        ecosystem="crates",
        name=name,
        age_days=_days(now, found.crate.created_at),
        releases=len(found.versions),
        downloads=found.crate.recent_downloads,
    )


async def _rubygems(client: httpx.AsyncClient, name: str, now: datetime) -> PackageFacts:
    response = await client.get(f"https://rubygems.org/api/v1/versions/{quote(name, safe='')}.json")
    if _missing(response):
        return PackageFacts(ecosystem="rubygems", name=name, exists=False)
    versions = _GEM_VERSIONS.validate_json(response.content)
    return PackageFacts(
        ecosystem="rubygems",
        name=name,
        age_days=_days(now, min((v.created_at for v in versions), default=None)),
        releases=len(versions),
        downloads=sum(v.downloads_count for v in versions),
    )


_LOOKUPS = {"pypi": _pypi, "npm": _npm, "crates": _crates, "rubygems": _rubygems}
