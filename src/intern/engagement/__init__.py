"""The engagement harness: intern's pentest layer — a shared, file-backed
engagement record (``store``), drex decision gates over findings (``drex``), the drill-down
ladder and OWASP coverage accounting, evidence and report pipelines, research tooling, specialist
dispatch, and the unattended runner. Everything hangs off ``EngagementExtension``
(``extension.py``), a capability-backed extension that owns one ``EngagementStore`` per session
under ``out/<slug>/`` in the working directory.
"""
