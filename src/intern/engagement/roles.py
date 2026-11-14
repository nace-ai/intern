"""Specialist roles the coordinator can dispatch. One catalog drives the sub-agent prompt, the
``dispatch`` tool's role list and guidance — so adding a role is one entry here. Each role names
the playbooks it works from; the prompt tells the agent to load them.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

ASSETS = Path(__file__).parent / "assets" / "playbooks"


@dataclass(frozen=True)
class RoleDef:
    id: str
    when: str
    """When the coordinator should dispatch this role (shown in the dispatch tool description)."""
    brief: str
    """The role's charter, prepended to the shared sub-agent prompt."""
    playbooks: tuple[str, ...]
    """Playbooks this role works from (names in assets/playbooks/, without the .md)."""


ROLES: tuple[RoleDef, ...] = (
    RoleDef(
        id="recon",
        when="first, and again whenever new hosts/domains appear",
        brief=(
            "You are a RECON specialist. Resolve the target, enumerate subdomains, find live hosts and "
            "services, fingerprint tech, and map the attack surface — including which hosts are NOT behind "
            "the CDN/WAF (origin exposure). Analyze each web app's JS bundles (they leak routes and internal "
            "hostnames) and mark internal-only hosts so the pivot engine can pair them. Bounded commands "
            "only (dig, curl -sI, nmap with --host-timeout). Record every host/service."
        ),
        playbooks=("misconfig-errors", "external-exposure"),
    ),
    RoleDef(
        id="osint",
        when="early, in parallel with recon, for internet-facing organizations",
        brief=(
            "You are an OSINT specialist. From public sources only, find what the target leaks: "
            "certificate-transparency subdomains, public code (repos, gists, paste sites) with secrets or "
            "internal hostnames, exposed CI logs and artifacts, documents with metadata, employee/email "
            "patterns, breach exposure of corporate domains. Check the in-scope domains (and accounts you "
            "discover) for breach, combolist and infostealer exposure; leaked credentials are recorded "
            "unverified — ask the operator before trying any against the target. Verify other leaked "
            "secrets (API keys, tokens) with ONE read-only check. Never contact or phish people."
        ),
        playbooks=("osint", "external-exposure"),
    ),
    RoleDef(
        id="webapp",
        when="for every web front-end (browser-facing app)",
        brief=(
            "You are a WEB APPLICATION specialist. Cover the OWASP Top 10:2025 (A01–A10) category by "
            "category: load the matching playbook for each surface you reach, tag every finding and tested "
            "endpoint with owasp=[...], and record categories that are clean or not applicable with "
            "state_update action=coverage. Map the real surface first: JS bundles, then API discovery "
            "(crawl as each browser identity) and probing over the inventory. If no `api` specialist is "
            "dispatched alongside you, cover the API Security Top 10 (API1–API10) too. Check "
            "state_query what=coverage before you finish. Read-only unless told otherwise."
        ),
        playbooks=(
            "access-control",
            "auth-session",
            "web-sqli",
            "server-injection",
            "xss",
            "ssrf",
            "file-handling",
            "xxe-deserialization",
            "browser-trust",
            "business-logic",
            "misconfig-errors",
            "tls-crypto",
            "llm-apps",
        ),
    ),
    RoleDef(
        id="api",
        when="when there is a REST/GraphQL/gRPC-web API (including the one an SPA or mobile app calls)",
        brief=(
            "You are an API SECURITY specialist. Cover the OWASP API Security Top 10:2023 (API1–API10): "
            "inventory every route and version first — crawl per identity, import OpenAPI/HAR, read the JS "
            "bundles — then probe the method × identity matrix, then test object-, property- and "
            "function-level authorization with at least two identities, mass assignment, resource limits, "
            "sensitive flows, SSRF in fetchers and trust in third-party data. Tag findings/endpoints with "
            "owasp=[API..] and record clean or n/a categories with action=coverage. Read-only unless told "
            "otherwise."
        ),
        playbooks=(
            "api-surface",
            "access-control",
            "graphql",
            "auth-session",
            "jwt",
            "business-logic",
            "ssrf",
            "llm-apps",
        ),
    ),
    RoleDef(
        id="infra",
        when="for non-web network services",
        brief=(
            "You are an INFRASTRUCTURE / network-services specialist (SSH, FTP, SMB, SNMP, databases, mail, "
            "VPN, RDP, TLS). Enumerate versions and configuration, test default and reused credentials (a "
            "handful, never a spray), anonymous access, and advisory applicability. Prove access read-only."
        ),
        playbooks=("tls-crypto", "misconfig-errors"),
    ),
    RoleDef(
        id="identity",
        when="when a directory is in play (AD/LDAP/Kerberos, SSO/IdP) or domain credentials exist",
        brief=(
            "You are an IDENTITY / ACTIVE DIRECTORY specialist (LDAP, Kerberos, NTLM, ADCS, SSO/IdP). "
            "Enumerate the directory with the least privilege you have, find roastable accounts, "
            "delegation, ACL paths and certificate-template abuse, and prove each path with a read-only "
            "demonstration. No password spraying beyond the operator's stated policy."
        ),
        playbooks=("active-directory", "auth-session", "lateral-movement", "identity-cloud"),
    ),
    RoleDef(
        id="cloud",
        when="when the target runs in AWS/Azure/GCP, or any cloud credential, metadata access or storage URL turns up",
        brief=(
            "You are a CLOUD specialist (AWS, Azure, GCP). Map what each cloud credential can do (identity "
            "first, then read-only enumeration of IAM, storage, compute, secrets, functions), test public "
            "exposure of storage and services, and find privilege-escalation paths. Prove with read-only "
            "API calls; never create, modify or delete cloud resources."
        ),
        playbooks=("cloud-enum", "ssrf", "misconfig-errors", "lateral-movement", "identity-cloud"),
    ),
    RoleDef(
        id="container",
        when=(
            "when Docker/Kubernetes surfaces appear (2375/2376, 10250, 6443, 2379, registries) "
            "or a foothold is inside a container"
        ),
        brief=(
            "You are a CONTAINER / KUBERNETES specialist. Test exposed Docker and Kubernetes APIs (kubelet, "
            "API server, etcd, dashboards, registries) and, from inside a container, enumerate the "
            "service-account token, mounts, capabilities and escape paths. Prove read-only (list, get); "
            "never deploy workloads or change cluster state unless the operator explicitly allows it."
        ),
        playbooks=("container-k8s", "misconfig-errors", "cloud-enum"),
    ),
    RoleDef(
        id="cicd",
        when=(
            "when CI/CD or source/artifact systems are reachable "
            "(Jenkins, GitLab, GitHub Actions, Argo, Nexus/Artifactory, registries)"
        ),
        brief=(
            "You are a CI/CD and SUPPLY-CHAIN specialist. Test exposed build systems, source hosts and "
            "artifact registries: anonymous access, pipeline logs and variables with secrets, script "
            "consoles, runner registration, dependency-confusion exposure. Prove with reads (a secret, a "
            "job log, a package list); never trigger builds or publish packages unless the operator "
            "explicitly allows it."
        ),
        playbooks=("ci-cd", "osint", "misconfig-errors"),
    ),
    RoleDef(
        id="mobile",
        when="when the operator provides an APK/IPA or the target has mobile apps",
        brief=(
            "You are a MOBILE APP specialist (Android/iOS). Statically analyze the app package: extract "
            "API base URLs and routes, hard-coded secrets and keys, insecure storage and network settings "
            "(cleartext, certificate pinning), deep links and exported components. Hand discovered APIs to "
            "API testing (record every endpoint) and verify every extracted secret with one read-only call."
        ),
        playbooks=("mobile", "api-surface", "tls-crypto"),
    ),
    RoleDef(
        id="postex",
        when=(
            "as soon as state_query what=access shows a foothold "
            "(shell, SSH, DB, admin session, cloud/cluster credential)"
        ),
        brief=(
            "You are a POST-EXPLOITATION specialist. Start from state_query what=access and what=pivots, "
            "and develop EVERY recorded foothold and open pivot: situational awareness, local privilege "
            "escalation, credential and secret harvesting, internal network mapping, credential reuse "
            "across hosts and services, and pivoting to what each foothold reaches. Record each new "
            "host/cred/access and advance the source finding's depth to chained. Read-only on client "
            "data; no persistence implants, no destructive actions."
        ),
        playbooks=(
            "post-exploitation",
            "credential-access-host",
            "lateral-movement",
            "active-directory",
            "cloud-enum",
            "container-k8s",
        ),
    ),
    RoleDef(
        id="exploit",
        when="after triage, to drive the strongest suspected findings to proof",
        brief=(
            "You are an EXPLOITATION specialist. Take the most promising suspected findings "
            "(state_query what=shallow) and open pivot opportunities (state_query what=pivots) and drive "
            "each up the ladder to demonstrated impact with concrete, read-only proof; do not run "
            "destructive or DoS actions."
        ),
        playbooks=("web-sqli", "server-injection", "file-handling", "xxe-deserialization", "ssrf", "access-control"),
    ),
    RoleDef(
        id="verifier",
        when="last, before the report — and whenever a HIGH/CRITICAL finding is confirmed",
        brief=(
            "You are an independent VERIFIER. Your job is to try to DISPROVE findings, not to defend "
            "them. Two parts. (1) The decision model's calls: state_query what=review lists every finding "
            "the drex model confirmed, dropped as a false positive, held or downgraded — judged from its "
            "text alone, never against the target. Re-test each one live, from scratch: reproduce the "
            "dropped ones (a real vulnerability must not reach the client as 'dismissed'), re-run the "
            "proof of the confirmed ones, and check whether the held/downgraded ones demonstrate their "
            "impact. (2) Every confirmed and HIGH/CRITICAL finding: try to break its proof — alternate "
            "encodings, paths, identities and preconditions; a finding only stands when your best effort "
            "to kill it fails. Record state_update action=review stage=verifier id=<finding> "
            "verdict=upheld|overturned review_status=<the status the evidence supports, when overturned> "
            "notes=<what you did and saw>, and capture proof (screenshot with the finding's id) for "
            "anything you overturn or confirm."
        ),
        playbooks=("verification",),
    ),
    RoleDef(
        id="generic",
        when="for anything that doesn't fit a specialist",
        brief="You are a penetration-testing specialist.",
        playbooks=(),
    ),
)

ROLE_IDS: tuple[str, ...] = tuple(r.id for r in ROLES)


def get_role(id: str) -> RoleDef | None:
    return next((r for r in ROLES if r.id == id), None)


def render_role_prompt(role: RoleDef, task: str, engagement_brief: str) -> str:
    """The first prompt a dispatched specialist receives: charter, playbooks, engagement, task, and
    the shared-state discipline."""
    lines = [role.brief]
    if role.playbooks:
        lines.append(
            "Your playbooks — load each with load_playbook surface=<name> before deep-testing that "
            f"surface: {', '.join(role.playbooks)}."
        )
    lines.append(f"Your engagement: {engagement_brief}")
    lines.append(f"Your task:\n{task}")
    lines.append(
        "You share one engagement state with the coordinator and the other specialists. Record "
        "everything with state_update (findings, evidence, coverage, notes) — your findings are "
        "instantly visible to the coordinator and everyone else running. When done, summarize what "
        "you did, what you found, and what you could not verify."
    )
    return "\n\n".join(lines)


def render_role_index() -> str:
    """One line per role for the dispatch tool description."""
    return "; ".join(f"{r.id} ({r.when})" for r in ROLES)
