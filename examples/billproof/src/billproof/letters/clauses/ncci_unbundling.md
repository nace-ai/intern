---
id: ncci_unbundling
title: NCCI unbundling
letter_types: [dispute, appeal]
ground: NCCI_UNBUNDLED
slots: [line_no, code, label, dos, amount, rule_cite]
summary: A published NCCI edit bundles this code into another service on the same date.
---

Line {line_no} bills {code} ({label}){dos? on {dos}} for {amount}. A National Correct Coding Initiative procedure-to-procedure edit treats this code as part of another service on the same date, unless a modifier the edit allows is documented. Please remove the unbundled charge or send that documentation.{rule_cite? This request relies on {rule_cite}.}
