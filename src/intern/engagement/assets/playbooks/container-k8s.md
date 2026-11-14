---
name: container-k8s
title: Containers and Kubernetes
triggers: Load when you see Docker or Kubernetes surfaces — ports 2375/2376 (Docker API), 10250/10255 (kubelet), 6443/8443 (API server), 2379 (etcd), dashboards, container registries — or when a foothold turns out to be inside a container (`/.dockerenv`, service-account token mounted).
attack: discovery, privilege-escalation
owasp: A02, API8
payloads: Methodology and Resources/Container - Docker Pentest; Methodology and Resources/Container - Kubernetes Pentest; Methodology and Resources/Escape Breakout
---

Container platforms fail through exposed control planes and over-privileged workloads. From outside, look for APIs that answer anonymously; from inside, look for what the pod can reach and what its identity can do. Prove with reads (list/get); don't deploy, exec into other workloads, or change cluster state unless the operator explicitly allows it.

## 1. Exposed control planes (from outside)
- **Docker API** (2375 plain / 2376 TLS): `curl -s http://host:2375/version`, `/containers/json`, `/images/json`. Anonymous access to the Docker API is root on the host — prove it with the read-only listing, and stop there unless the operator approves creating a container.
- **Kubelet** (10250): `curl -sk https://host:10250/pods` (anonymous auth?), `/runningpods/`; read-only port 10255 `/pods`. Pod specs often carry env secrets.
- **API server** (6443): `curl -sk https://host:6443/version`, then `/api/v1/namespaces`, `/api/v1/secrets` anonymously or with any token you've found.
- **etcd** (2379): `etcdctl --endpoints=http://host:2379 get / --prefix --keys-only` — readable etcd = every cluster secret.
- Dashboards (Kubernetes dashboard, Rancher, Portainer), registries (`/v2/_catalog`) with anonymous read → image layers may contain secrets.

## 2. Inside a container (after a foothold)
- Identity: service-account token at `/var/run/secrets/kubernetes.io/serviceaccount/token`; test it with `kubectl auth can-i --list --token=$(cat …)` (or the REST equivalent `SelfSubjectRulesReview`).
- Environment and mounts: `env`, `mount`, `/proc/1/status` capabilities (`capsh --decode`), host paths mounted (`/var/run/docker.sock`, `/`, `/etc`, `/var/lib/kubelet`).
- Escape conditions (record them; exercise only with approval): privileged container, `CAP_SYS_ADMIN`, mounted Docker socket, `hostPID`/`hostNetwork`, writable host paths.
- Network: what other pods/services resolve (`/etc/resolv.conf` search domains, `*.svc.cluster.local`), cloud metadata from the node (→ `cloud-enum`).

## 3. Cluster RBAC and secrets
- With a token: what can it do (`can-i --list`)? Dangerous verbs: `create pods`, `get secrets`, `create pods/exec`, `bind`/`escalate` on roles, `impersonate`.
- Read secrets you're allowed to read (names first, then one value to prove access); config maps often hold credentials too.
- Map the escalation path (e.g. `create pods` in a namespace with a privileged service account → node access) and demonstrate only the read-side steps without approval.

## Record & validate
- Exposed control plane → `state_update action=vuln owasp=[A02, API8]` with the anonymous request/response; tokens and secrets → `action=cred`; host access via Docker socket → `action=access`.
- Container foothold findings chain into `post-exploitation` (and `cloud-enum` for node credentials).
