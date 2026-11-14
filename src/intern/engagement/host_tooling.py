"""Host tooling: what is installed where the agent's commands run (OS/distro, package managers,
catalogued tools), how to install what is missing, and what to use meanwhile. Install recipes come
ONLY from the catalog — a tool can be asked for by name, never an arbitrary package. Installs run
through the executor; system packages that would need sudo are never run interactively: the exact
command is handed back for the operator instead.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import ClassVar, Literal, Protocol

from pydantic import BaseModel, Field

from intern.engagement.store import EngagementStore
from intern.executor import ExecResult
from intern.tools.base import Effect, Tool, ToolContext, ToolOutput, fail

Manager = Literal["apt", "dnf", "zypper", "pacman", "apk", "brew", "port", "pkg"]
#: Detected but without recipes (reported; tools fall back to pipx/go or manual commands).
ExtraManager = Literal["xbps", "nix", "emerge"]


class ToolSpec(BaseModel):
    """One catalogued tool: what it is for, how it installs per package manager, and the fallback."""

    cmd: str
    use: str
    pkg: dict[str, str] = Field(default_factory=dict)
    go: str | None = None
    pipx: str | None = None
    fallback: str
    alts: list[str] = Field(default_factory=list)
    roles: list[str] = Field(default_factory=list)


# Curated, pragmatic set — the tools the methodology actually leans on, each with its Python/
# builtin fallback so a miss is a redirect, not a dead end. curl/python3 are the fallback engine.
# Repo atoms cover apt/dnf/zypper/pacman/apk/brew/port/pkg; ProjectDiscovery tools (nuclei/ffuf/
# httpx) and sqlmap carry go/pipx so they install on managers whose repos lack them.
CORE_TOOLS: list[ToolSpec] = [
    ToolSpec(
        cmd="python3",
        use="fallback engine for everything below",
        pkg={
            "apt": "python3",
            "dnf": "python3",
            "zypper": "python3",
            "pacman": "python",
            "apk": "python3",
            "brew": "python3",
            "port": "python3",
            "pkg": "python3",
        },
        fallback="(this IS the fallback — without it, only curl/bash /dev/tcp remain)",
    ),
    ToolSpec(
        cmd="curl",
        use="HTTP requests, DoH",
        pkg={
            "apt": "curl",
            "dnf": "curl",
            "zypper": "curl",
            "pacman": "curl",
            "apk": "curl",
            "brew": "curl",
            "port": "curl",
            "pkg": "curl",
        },
        fallback="python urllib/socket",
    ),
    ToolSpec(
        cmd="nmap",
        use="port/service scan",
        pkg={
            "apt": "nmap",
            "dnf": "nmap",
            "zypper": "nmap",
            "pacman": "nmap",
            "apk": "nmap",
            "brew": "nmap",
            "port": "nmap",
            "pkg": "nmap",
        },
        fallback="python socket connect-scan, or `nc -zv` / bash /dev/tcp for a few ports",
    ),
    ToolSpec(
        cmd="dig",
        use="DNS lookups",
        pkg={
            "apt": "dnsutils",
            "dnf": "bind-utils",
            "zypper": "bind-utils",
            "pacman": "bind",
            "apk": "bind-tools",
            "brew": "bind",
            "port": "bind9",
            "pkg": "bind-tools",
        },
        fallback="python socket.getaddrinfo, or curl DoH (https://cloudflare-dns.com/dns-query)",
    ),
    ToolSpec(
        cmd="openssl",
        use="TLS/cert inspection",
        pkg={
            "apt": "openssl",
            "dnf": "openssl",
            "zypper": "openssl",
            "pacman": "openssl",
            "apk": "openssl",
            "brew": "openssl",
            "port": "openssl",
            "pkg": "openssl",
        },
        fallback="python ssl.getpeercert, or curl -vI https://host",
    ),
    ToolSpec(
        cmd="jq",
        use="JSON parsing",
        pkg={
            "apt": "jq",
            "dnf": "jq",
            "zypper": "jq",
            "pacman": "jq",
            "apk": "jq",
            "brew": "jq",
            "port": "jq",
            "pkg": "jq",
        },
        fallback="python -m json.tool / python -c",
    ),
    ToolSpec(
        cmd="nc",
        use="TCP connect, banner grab",
        pkg={
            "apt": "netcat-openbsd",
            "dnf": "nmap-ncat",
            "zypper": "netcat-openbsd",
            "pacman": "openbsd-netcat",
            "apk": "netcat-openbsd",
            "brew": "netcat",
            "port": "netcat",
            "pkg": "netcat",
        },
        fallback="bash /dev/tcp, or python socket",
    ),
    ToolSpec(
        cmd="httpx",
        use="fast HTTP probe / tech detect",
        go="github.com/projectdiscovery/httpx/cmd/httpx@latest",
        pkg={"brew": "httpx"},
        fallback="curl -sI / -sv for headers and status",
    ),
    ToolSpec(
        cmd="node",
        use="standalone helper scripts",
        pkg={
            "apt": "nodejs",
            "dnf": "nodejs",
            "zypper": "nodejs",
            "pacman": "nodejs",
            "apk": "nodejs",
            "brew": "node",
            "port": "node",
            "pkg": "node",
        },
        fallback="python for the same one-off scripting",
    ),
    ToolSpec(
        cmd="nuclei",
        use="templated vuln checks",
        go="github.com/projectdiscovery/nuclei/v3/cmd/nuclei@latest",
        pkg={"brew": "nuclei"},
        fallback="targeted curl probes by hand for the specific CVE/misconfig",
    ),
    ToolSpec(
        cmd="ffuf",
        use="content/dir/vhost fuzzing",
        go="github.com/ffuf/ffuf/v2@latest",
        pkg={"apt": "ffuf", "brew": "ffuf"},
        fallback="curl loop over a wordlist (bash/python)",
    ),
    ToolSpec(
        cmd="sqlmap",
        use="SQL injection",
        pipx="sqlmap",
        pkg={"apt": "sqlmap", "dnf": "sqlmap", "zypper": "sqlmap", "pacman": "sqlmap", "brew": "sqlmap"},
        fallback="manual boolean/time-based payloads via curl, diff the responses",
    ),
]

#: Specialist tier: tools only some roles need. They appear in `host_tools` output but stay out of
#: the core install line so a plain web test isn't told to install AD tooling.
SPECIALIST_TOOLS: list[ToolSpec] = [
    ToolSpec(
        cmd="nxc",
        alts=["netexec"],
        roles=["identity", "infra", "postex"],
        use="NetExec: SMB/LDAP/WinRM enumeration, policy, shares",
        pkg={"apt": "netexec"},
        pipx="git+https://github.com/Pennyw0rth/NetExec",
        fallback="smbclient / ldapsearch / impacket for the SMB and LDAP checks",
    ),
    ToolSpec(
        cmd="ldapsearch",
        roles=["identity"],
        use="LDAP queries, anonymous bind check",
        pkg={
            "apt": "ldap-utils",
            "dnf": "openldap-clients",
            "zypper": "openldap2-client",
            "pacman": "openldap",
            "apk": "openldap-clients",
            "brew": "openldap",
        },
        fallback="python ldap3 (pip) bind + search",
    ),
    ToolSpec(
        cmd="smbclient",
        roles=["identity", "infra"],
        use="SMB share listing, null session",
        pkg={
            "apt": "smbclient",
            "dnf": "samba-client",
            "zypper": "samba-client",
            "pacman": "smbclient",
            "apk": "samba-client",
            "brew": "samba",
        },
        fallback="`nxc smb --shares`, or impacket smbclient.py",
    ),
    ToolSpec(
        cmd="kubectl",
        roles=["container"],
        use="Kubernetes permission checks and reads",
        pkg={"pacman": "kubectl", "apk": "kubectl", "brew": "kubectl"},
        fallback="curl the API server REST endpoints with the bearer token (SelfSubjectRulesReview for can-i)",
    ),
    ToolSpec(
        cmd="jadx",
        roles=["mobile"],
        use="Android decompiler (Java/Kotlin source)",
        pkg={"brew": "jadx"},
        fallback="apktool smali output plus strings/grep over the dex files",
    ),
    ToolSpec(
        cmd="apktool",
        roles=["mobile"],
        use="APK resources, manifest, smali",
        pkg={"apt": "apktool", "brew": "apktool"},
        fallback="unzip the APK; decode the binary manifest with aapt or androguard",
    ),
    ToolSpec(
        cmd="exiftool",
        roles=["osint"],
        use="document and image metadata",
        pkg={
            "apt": "libimage-exiftool-perl",
            "dnf": "perl-Image-ExifTool",
            "pacman": "perl-image-exiftool",
            "apk": "exiftool",
            "brew": "exiftool",
        },
        fallback="unzip Office files and read docProps/core.xml; strings on PDFs",
    ),
    ToolSpec(
        cmd="ldapdomaindump",
        roles=["identity"],
        use="AD directory dump",
        pipx="ldapdomaindump",
        fallback="ldapsearch the users/groups/computers containers to files",
    ),
    ToolSpec(
        cmd="bloodhound-python",
        alts=["bloodhound-ce-python"],
        roles=["identity"],
        use="AD attack-path collection",
        pipx="bloodhound",
        fallback="`nxc ldap --bloodhound` if nxc is present; otherwise enumerate ACLs with ldapsearch and "
        "state that graph analysis was not available",
    ),
    ToolSpec(
        cmd="kerbrute",
        roles=["identity"],
        use="Kerberos user enumeration",
        go="github.com/ropnop/kerbrute@latest",
        fallback="skip unauthenticated user enumeration and record it as untested (tool missing)",
    ),
    ToolSpec(
        cmd="certipy",
        roles=["identity"],
        use="ADCS template checks",
        pipx="certipy-ad",
        fallback="no reliable fallback — record ADCS as untested (tool missing) and stop",
    ),
    ToolSpec(
        cmd="GetNPUsers.py",
        alts=["impacket-GetNPUsers"],
        roles=["identity", "postex"],
        use="impacket suite (roasting, targeted DCSync)",
        pipx="impacket",
        fallback="`nxc` covers roasting where present; otherwise record these checks as untested (tool missing)",
    ),
    ToolSpec(
        cmd="etcdctl",
        roles=["container"],
        use="etcd key listing",
        pkg={"apt": "etcd-client", "brew": "etcd"},
        fallback="curl the etcd v3 HTTP gateway (POST /v3/kv/range with base64 keys)",
    ),
    ToolSpec(
        cmd="bundletool",
        roles=["mobile"],
        use="AAB → APK conversion",
        pkg={"brew": "bundletool"},
        fallback="unzip the AAB and analyze base/ with jadx/apktool directly",
    ),
    ToolSpec(
        cmd="subfinder",
        roles=["recon", "osint"],
        use="passive subdomain discovery",
        go="github.com/projectdiscovery/subfinder/v2/cmd/subfinder@latest",
        pkg={"brew": "subfinder"},
        fallback="crt.sh certificate-transparency JSON via curl",
    ),
    ToolSpec(
        cmd="amass",
        roles=["osint"],
        use="passive subdomain discovery",
        pkg={"brew": "amass"},
        fallback="crt.sh via curl, plus subfinder if present",
    ),
    ToolSpec(
        cmd="trufflehog",
        roles=["osint", "cicd"],
        use="secret scanning in repos and history",
        pkg={"brew": "trufflehog"},
        fallback="gitleaks if present; otherwise `git log -p` piped through grep for key patterns",
    ),
    ToolSpec(
        cmd="gitleaks",
        roles=["osint", "cicd"],
        use="secret scanning in repos and history",
        go="github.com/zricethezav/gitleaks/v8@latest",
        pkg={"brew": "gitleaks"},
        fallback="trufflehog if present; otherwise `git log -p` piped through grep for key patterns",
    ),
    ToolSpec(
        cmd="az",
        roles=["cloud"],
        use="Azure CLI (read-only calls)",
        pkg={"brew": "azure-cli"},
        fallback="Azure REST API via curl with a bearer token",
    ),
    ToolSpec(
        cmd="chisel",
        roles=["postex"],
        use="pivot tunnels (operator-approved)",
        go="github.com/jpillora/chisel@latest",
        pkg={"brew": "chisel"},
        fallback="ssh -L / -D when SSH is available; otherwise record that pivoting was not possible",
    ),
]

#: Prerequisite installers: language package managers some recipes need.
INSTALLER_TOOLS: dict[str, ToolSpec] = {
    "pipx": ToolSpec(
        cmd="pipx",
        use="installs Python CLI tools in isolated environments",
        pkg={
            "apt": "pipx",
            "dnf": "pipx",
            "zypper": "python3-pipx",
            "pacman": "python-pipx",
            "apk": "pipx",
            "brew": "pipx",
            "port": "pipx",
            "pkg": "py311-pipx",
        },
        fallback="python3 -m pip install --user <tool>",
    ),
    "go": ToolSpec(
        cmd="go",
        use="builds Go-based tools (go install)",
        pkg={
            "apt": "golang-go",
            "dnf": "golang",
            "zypper": "go",
            "pacman": "go",
            "apk": "go",
            "brew": "go",
            "port": "go",
            "pkg": "go",
        },
        fallback="download the tool's release binary",
    ),
}


def find_tool(name: str) -> ToolSpec | None:
    lowered = name.strip().lower()
    for tool in [*CORE_TOOLS, *SPECIALIST_TOOLS]:
        if tool.cmd.lower() == lowered or lowered in (alt.lower() for alt in tool.alts):
            return tool
    return None


# -- platform detection ------------------------------------------------------------------------


@dataclass(frozen=True)
class Platform:
    os: str  # macos | linux | freebsd | other
    distro: str | None = None  # os-release ID (ubuntu, debian, fedora, arch, alpine …)
    distro_name: str | None = None
    arch: str = ""
    wsl: bool = False
    termux: bool = False
    root: bool = False
    sudo: bool = False
    managers: tuple[Manager, ...] = ()  # preference order, Homebrew first
    extra_managers: tuple[ExtraManager, ...] = ()
    pipx: bool = False
    go: bool = False


class Probe(Protocol):
    """How the host is inspected; every check is injectable for tests."""

    def which(self, cmd: str) -> bool: ...
    def read_file(self, path: str) -> str | None: ...
    @property
    def env(self) -> dict[str, str]: ...

    @property
    def system(self) -> str: ...

    @property
    def uid(self) -> int | None: ...

    @property
    def machine(self) -> str: ...


@dataclass
class RealProbe:
    env: dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        import os
        import shutil

        self._os, self._shutil = os, shutil
        if not self.env:
            self.env = dict(os.environ)

    def which(self, cmd: str) -> bool:
        return self._shutil.which(cmd) is not None

    def read_file(self, path: str) -> str | None:
        try:
            return Path(path).read_text(encoding="utf-8")
        except OSError:
            return None

    @property
    def system(self) -> str:
        return self._os.uname().sysname.lower()

    @property
    def uid(self) -> int | None:
        return getattr(self._os, "getuid", lambda: None)()

    @property
    def machine(self) -> str:
        return self._os.uname().machine


def _os_release(text: str | None) -> dict[str, str | None]:
    def get(key: str) -> str | None:
        if text is None:
            return None
        match = re.search(rf'^{key}="?([^"\n]*)"?', text, re.MULTILINE)
        return match.group(1) if match else None

    return {"id": get("ID"), "like": get("ID_LIKE"), "name": get("PRETTY_NAME")}


def detect_platform(probe: Probe) -> Platform:
    os_name = {"darwin": "macos", "linux": "linux", "freebsd": "freebsd"}.get(probe.system, "other")
    release = _os_release(probe.read_file("/etc/os-release")) if os_name == "linux" else {}
    termux = "com.termux" in (probe.env.get("PREFIX") or "")
    wsl = os_name == "linux" and bool(re.search(r"microsoft|wsl", probe.read_file("/proc/version") or "", re.I))
    # preference: Homebrew/Linuxbrew first everywhere (user-space, no root, current versions), then
    # the OS's native manager, then MacPorts
    linux_native: list[tuple[Manager, str]] = [
        ("apt", "apt-get"),
        ("dnf", "dnf"),
        ("dnf", "yum"),
        ("zypper", "zypper"),
        ("pacman", "pacman"),
        ("apk", "apk"),
    ]
    order: list[tuple[Manager, str]]
    if os_name == "macos":
        order = [("brew", "brew"), ("port", "port")]
    elif os_name == "freebsd" or termux:
        order = [("brew", "brew"), ("pkg", "pkg")]
    else:
        order = [("brew", "brew"), *linux_native]
    managers: list[Manager] = []
    for manager, binary in order:
        if probe.which(binary) and manager not in managers:
            managers.append(manager)
    extra: list[tuple[ExtraManager, str]] = [("xbps", "xbps-install"), ("nix", "nix-env"), ("emerge", "emerge")]
    root = probe.uid == 0
    return Platform(
        os=os_name,
        distro=release.get("id"),
        distro_name=release.get("name"),
        arch=probe.machine,
        wsl=wsl,
        termux=termux,
        root=root,
        sudo=not root and probe.which("sudo"),
        managers=tuple(managers),
        extra_managers=tuple(m for m, binary in extra if probe.which(binary)),
        pipx=probe.which("pipx"),
        go=probe.which("go"),
    )


def needs_root(manager: Manager, plat: Platform) -> bool:
    """Managers that install system-wide need root (Homebrew must NOT run as root)."""
    if manager == "brew":
        return False
    if manager == "pkg" and plat.termux:
        return False
    return not plat.root


def describe_platform(plat: Platform) -> str:
    os_name = {"macos": "macOS"}.get(
        plat.os, plat.distro_name or {"linux": "Linux", "freebsd": "FreeBSD"}.get(plat.os, plat.os)
    )
    flags = ", ".join(
        x
        for x in [plat.arch, "WSL" if plat.wsl else "", "Termux" if plat.termux else "", "root" if plat.root else ""]
        if x
    )
    pms = ", ".join([*plat.managers, *(f"{m} (no recipes)" for m in plat.extra_managers)]) or "no known package manager"
    return f"{os_name} ({flags}) · {pms}"


@dataclass(frozen=True)
class HostTooling:
    platform: Platform
    present: tuple[ToolSpec, ...]
    missing: tuple[ToolSpec, ...]
    specialist_present: tuple[ToolSpec, ...]
    specialist_missing: tuple[ToolSpec, ...]


def detect_tooling(probe: Probe) -> HostTooling:
    platform = detect_platform(probe)

    def installed(tool: ToolSpec) -> bool:
        return probe.which(tool.cmd) or any(probe.which(alt) for alt in tool.alts)

    return HostTooling(
        platform=platform,
        present=tuple(t for t in CORE_TOOLS if installed(t)),
        missing=tuple(t for t in CORE_TOOLS if not installed(t)),
        specialist_present=tuple(t for t in SPECIALIST_TOOLS if installed(t)),
        specialist_missing=tuple(t for t in SPECIALIST_TOOLS if not installed(t)),
    )


# -- install plan -------------------------------------------------------------------------------


@dataclass(frozen=True)
class Step:
    via: Manager | Literal["pipx", "go"]
    argv: tuple[str, ...]  # without sudo
    root: bool  # needs root (sudo unless already root)
    tools: tuple[str, ...] = ()


@dataclass(frozen=True)
class Plan:
    steps: tuple[Step, ...] = ()
    manual: tuple[str, ...] = ()


INSTALL_ARGV: dict[Manager, tuple[str, ...]] = {
    "apt": ("apt-get", "install", "-y"),
    "dnf": ("dnf", "install", "-y"),
    "zypper": ("zypper", "--non-interactive", "install"),
    "pacman": ("pacman", "-S", "--needed", "--noconfirm"),
    "apk": ("apk", "add"),
    "brew": ("brew", "install"),
    "port": ("port", "-N", "install"),
    "pkg": ("pkg", "install", "-y"),
}


def _manager_for(tool: ToolSpec, plat: Platform) -> Manager | None:
    return next((m for m in plat.managers if tool.pkg.get(m)), None)


def build_plan(tools: list[ToolSpec], plat: Platform) -> Plan:
    """For each tool the best installer on THIS host; nothing is dropped."""
    by_manager: dict[Manager, tuple[list[str], list[str]]] = {}
    separate: list[Step] = []  # atoms carrying flags (e.g. brew --cask) run as their own command
    pipx: list[tuple[str, str]] = []
    go: list[tuple[str, str]] = []
    manual: list[str] = []

    def add(manager: Manager, atom: str, tool: str) -> None:
        if atom.startswith("--"):
            separate.append(
                Step(
                    via=manager,
                    argv=(*INSTALL_ARGV[manager], *atom.split()),
                    root=needs_root(manager, plat),
                    tools=(tool,),
                )
            )
            return
        atoms, names = by_manager.get(manager, ([], []))
        if atom not in atoms:
            atoms.append(atom)
        by_manager[manager] = (atoms, [*names, tool])

    for tool in tools:
        manager = _manager_for(tool, plat)
        if manager is not None:
            add(manager, tool.pkg[manager], tool.cmd)
        elif tool.pipx:
            pipx.append((tool.pipx, tool.cmd))
        elif tool.go:
            go.append((tool.go, tool.cmd))
        else:
            manual.append(tool.cmd)
    # prerequisites: pipx / Go themselves, through the host's package manager
    for needed, present, spec, queue in (
        (bool(pipx), plat.pipx, INSTALLER_TOOLS["pipx"], pipx),
        (bool(go), plat.go, INSTALLER_TOOLS["go"], go),
    ):
        if not needed or present:
            continue
        manager = _manager_for(spec, plat)
        if manager is not None:
            add(manager, spec.pkg[manager], spec.cmd)
        else:
            manual.append(f"{spec.cmd} (needed for: {', '.join(tool for _, tool in queue)})")
    steps: list[Step] = []
    if "apt" in by_manager:
        steps.append(Step(via="apt", argv=("apt-get", "update"), root=needs_root("apt", plat)))
    for manager, (atoms, names) in by_manager.items():
        steps.append(
            Step(via=manager, argv=(*INSTALL_ARGV[manager], *atoms), root=needs_root(manager, plat), tools=tuple(names))
        )
    steps.extend(separate)
    steps.extend(Step(via="pipx", argv=("pipx", "install", atom), root=False, tools=(tool,)) for atom, tool in pipx)
    steps.extend(Step(via="go", argv=("go", "install", atom), root=False, tools=(tool,)) for atom, tool in go)
    return Plan(steps=tuple(steps), manual=tuple(manual))


def command_line(step: Step, plat: Platform) -> str:
    """The command line an operator would type for a step."""
    quoted = " ".join(a if re.match(r"^[\w@./:=+-]+$", a) else "'" + a.replace("'", "'\\''") + "'" for a in step.argv)
    return f"sudo {quoted}" if step.root and not plat.root else quoted


def plan_lines(plan: Plan, plat: Platform) -> list[str]:
    lines = [command_line(step, plat) for step in plan.steps]
    if plan.manual:
        lines.append(f"# install manually (no recipe for this host): {', '.join(plan.manual)}")
    return lines


# -- the tools ----------------------------------------------------------------------------------


def render_tooling(host: HostTooling) -> str:
    """The agent-facing status: what to prefer, what to fall back from, what is missing by role."""
    plat = host.platform
    lines = [
        f"# Host tooling (where commands run: {describe_platform(plat)})",
        f"Installed — PREFER these: {', '.join(t.cmd for t in host.present) or '(none)'}.",
    ]
    if host.missing:
        lines.append(
            "NOT installed — do NOT run these; fall back immediately (as noted), "
            "don't burn a turn discovering they are absent:"
        )
        lines.extend(f"- {t.cmd} ({t.use}) → {t.fallback}" for t in host.missing)
    else:
        lines.append("All core tools are present — use them directly.")
    if host.specialist_present:
        lines.append(f"Specialist tools installed: {', '.join(t.cmd for t in host.specialist_present)}.")
    if host.specialist_missing:
        lines.append(
            "Specialist tools NOT installed (by role) — use the fallback, and never claim a check you couldn't run:"
        )
        lines.extend(f"- {t.cmd} [{', '.join(t.roles)}] ({t.use}) → {t.fallback}" for t in host.specialist_missing)
    lines.append(
        "To get a missing tool, call `install_tools` with names and confirmed=true (the operator's "
        "approval; only catalogued tools can be installed). If it can't install, it returns the exact "
        "commands — tell the operator, then `host_tools` to re-check."
    )
    return "\n".join(lines)


class HostToolsArgs(BaseModel):
    pass


class HostToolsTool(Tool):
    """Re-probe the host: OS/distro, package managers, which catalogued tools are installed."""

    name = "host_tools"
    description = (
        "Re-probe the host where your commands run: OS/distro, package managers, which recommended tools are "
        "installed (PREFER them), which are missing (use the listed fallback — don't run a missing tool), and "
        "the copy-paste install commands for the missing ones on this host. Call it after the operator installs "
        "something, or when unsure whether a tool exists."
    )
    Args = HostToolsArgs
    effects: ClassVar[frozenset[Effect]] = frozenset({"read"})

    def __init__(self, probe: Probe | None = None) -> None:
        self._probe = probe

    async def run(self, args: HostToolsArgs, ctx: ToolContext) -> ToolOutput:
        host = detect_tooling(self._probe or RealProbe())
        lines = plan_lines(build_plan(list(host.missing), host.platform), host.platform)
        body = render_tooling(host)
        if lines:
            body += "\n\nInstall commands for this host:\n" + "\n".join(lines)
        return ToolOutput(content=body)


class InstallToolsArgs(BaseModel):
    names: list[str] = Field(default_factory=list, description="catalogued tool names, e.g. ['nuclei', 'sqlmap']")
    group: Literal["core", "all"] | None = Field(
        default=None, description="core = every missing core tool; all = include the specialist tier"
    )
    reason: str | None = Field(default=None, description="why the tool is needed now (shown with the report)")
    confirmed: bool = Field(
        default=False,
        description="the operator's approval to install — this port's confirmation is the explicit flag",
    )


class InstallToolsTool(Tool):
    """Install missing tools from the catalog, only once the operator has approved."""

    name = "install_tools"
    description = (
        "Install missing tools from the catalog on the host — only after the operator confirms; the "
        "confirmation in this harness is passing confirmed=true (without it the call refuses and prints the "
        'commands instead). Pass names (e.g. ["nuclei", "sqlmap"]) or group=core|all. Uses the host\'s '
        "package manager (Homebrew first) / pipx / go install; system packages need sudo and are NEVER run "
        "interactively: when a password would be required, the exact command is returned for the operator "
        "instead. Names outside the catalog are refused."
    )
    Args = InstallToolsArgs
    effects: ClassVar[frozenset[Effect]] = frozenset({"exec", "network", "write"})

    def __init__(self, store: EngagementStore | None = None, probe: Probe | None = None) -> None:
        self._store = store
        self._probe = probe

    async def run(self, args: InstallToolsArgs, ctx: ToolContext) -> ToolOutput:
        if not args.confirmed:
            return fail(
                "install_tools refused: installing software needs the operator's approval",
                "Ask the operator; once they approve, call install_tools again with confirmed=true "
                "(and the same names).",
            )
        probe = self._probe or RealProbe()
        host = detect_tooling(probe)
        installable = [*host.missing, *host.specialist_missing]
        tools: list[ToolSpec] = []
        unknown: list[str] = []
        if args.group == "core":
            tools = list(host.missing)
        elif args.group == "all":
            tools = installable
        for name in args.names:
            found = find_tool(name)
            if found is None:
                unknown.append(name)
            elif found in installable and found not in tools:
                tools.append(found)
        if unknown:
            return fail(
                f"refused — not in the tool catalog: {', '.join(unknown)}",
                "Only catalogued names install (host_tools lists them); install others by hand.",
            )
        if not tools:
            return ToolOutput(content="nothing to install — already present ✓")
        plan = build_plan(tools, host.platform)
        results: list[tuple[str, bool, str]] = []
        for step in plan.steps:
            command = command_line(step, host.platform)
            if step.root and not host.platform.root:
                results.append((command, False, "needs sudo — run this command yourself"))
                continue
            exec_result = await ctx.executor.run(
                command, cwd=ctx.cwd, timeout_s=1200, max_output_bytes=200_000, abort=ctx.abort
            )
            ok = exec_result.exit_code == 0
            results.append((command, ok, "" if ok else _tail(exec_result)))
        host_after = detect_tooling(probe)
        still_missing = [t.cmd for t in tools if t not in [*host_after.present, *host_after.specialist_present]]
        report_lines = [
            line
            for command, ok, detail in results
            for line in (f"{'✓' if ok else '✗'} {command}", f"    {detail}" if detail else "")
            if line
        ]
        if still_missing:
            report_lines.append("")
            report_lines.append("still missing: " + ", ".join(still_missing))
            report_lines.append("Run the ✗ commands yourself, then re-check with host_tools.")
        total = len(host_after.present)
        note = f"now: {total}/{total + len(host_after.missing)} core tools present"
        if self._store is not None:
            self._store.log(
                "tools",
                f"install {', '.join(t.cmd for t in tools)}: "
                f"{len(results) - len(still_missing)} step(s) ok, {len(still_missing)} still missing",
            )
        prefix = f"{args.reason}\n\n" if args.reason else ""
        return ToolOutput(content=prefix + "\n".join(report_lines) + f"\n\n{note}")


def _tail(result: ExecResult, lines: int = 6) -> str:
    kept = [line for line in result.output.splitlines() if line.strip()]
    return "\n".join(kept[-lines:]) or f"exit {result.exit_code}"
