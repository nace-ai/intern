---
name: active-directory
title: Active Directory — enumeration, Kerberos, ACL paths, ADCS
triggers: Load when a Windows domain is in play — ports 88/389/445/636/3268, a domain-joined foothold, domain credentials (even a low-priv user), NTLM auth on web apps, or an SSO/IdP backed by AD.
attack: discovery, credential-access, lateral-movement, privilege-escalation
owasp: A07
payloads: Methodology and Resources/Active Directory Attack; Methodology and Resources/Windows - Using credentials; Methodology and Resources/Hash Cracking
---

AD compromise is a graph problem: low-priv user → misconfigured edge → higher principal. Enumerate the graph with the least privilege you have, then prove one path at a time read-only. Respect the operator's lockout policy — no password spraying beyond what they allow.

## 1. Unauthenticated
- Find DCs: `dig _ldap._tcp.dc._msdcs.<domain> SRV`, nmap 88/389/445.
- Null/anonymous: LDAP anonymous bind (`ldapsearch -x -H ldap://dc -b "" -s base`), SMB null session (`smbclient -N -L //dc`, `nxc smb dc -u '' -p ''`).
- User discovery without creds: Kerberos pre-auth user enumeration (`kerbrute userenum`, bounded wordlist).
- **AS-REP roasting**: accounts without pre-auth (`GetNPUsers.py -no-pass -usersfile users.txt`); crack offline with a bounded wordlist.
- Coercion and relay (PetitPotam, printer bug → NTLM relay) only with explicit operator approval — they touch production DCs.

## 2. With any domain user
- Dump the directory read-only: `bloodhound-python -c All` / `nxc ldap --bloodhound`, or `ldapdomaindump`. Load into BloodHound and query shortest paths to Domain Admins / high-value targets.
- Password policy first (`nxc smb dc -u u -p p --pass-pol`) — it bounds everything credential-related.
- **Kerberoasting**: SPN accounts (`GetUserSPNs.py -request`); crack offline, bounded.
- Shares: `nxc smb <hosts> -u u -p p --shares`, then spider readable shares for scripts, configs, `Groups.xml` (GPP cpassword), backups, KeePass files.
- LAPS / gMSA readability, `ms-DS-MachineAccountQuota` (lets users add machines), descriptions with passwords.

## 3. ACL and delegation paths
- Edges that matter: `GenericAll`, `GenericWrite`, `WriteDacl`, `WriteOwner`, `ForceChangePassword`, `AddMember`, `AllExtendedRights` on users/groups/computers/GPOs.
- Delegation: unconstrained (any host with it is a ticket sink), constrained (`msDS-AllowedToDelegateTo`), resource-based (`msDS-AllowedToActOnBehalfOfOtherIdentity` writable).
- Prove an edge without breaking things: show you hold the right (BloodHound + an `ldapsearch` of the ACE), and exercise it only on objects the operator approves — changing a real user's password or group membership is a production change.

## 4. ADCS (certificate services)
- `certipy find -vulnerable -u u -p p -dc-ip <dc>`: ESC1 (enrollee supplies subject), ESC2/3, ESC4 (writable template), ESC6, ESC8 (web enrollment relay).
- ESC1 proof: request a certificate for a privileged principal and authenticate with it (`certipy auth`) — record the obtained TGT/hash as the proof, then stop.

## 5. Domain dominance (prove, don't wreck)
- With replication rights or DA: a **targeted** DCSync of one account (`secretsdump.py -just-dc-user krbtgt` or a test account) is the proof — not a full dump.
- No golden tickets, no GPO changes, no new admin accounts unless the operator explicitly asks.

## Record & validate
- Users/hashes/tickets → `state_update action=cred` (source = technique). Each path step → `state_update action=vuln owasp=[A07]` with the edge and the proof; obtained privileges → `action=access`.
- The final path to domain admin is one finding at `depth=chained`, with each hop as evidence.
