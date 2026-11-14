---
id: after_death
title: Service after the date of death
letter_types: [dispute]
ground: SERVICE_AFTER_DEATH
slots: [line_no, code, label, dos, amount, date_of_death, rule_cite]
summary: The date of service is after the date of death on file.
---

Line {line_no} bills {code} ({label}){dos? on {dos}} for {amount}.{date_of_death? The patient file lists a date of death of {date_of_death}, which is before that service date.} Please remove the charge, or show a service date the file supports.{rule_cite? This request relies on {rule_cite}.}
