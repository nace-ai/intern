---
id: duplicate_charge
title: Duplicate charge
letter_types: [dispute, appeal]
ground: DUPLICATE
slots: [line_no, code, label, dos, amount, account_no, rule_cite]
summary: The same service appears more than once on this account.
---

Line {line_no} bills {code} ({label}){dos? on {dos}} for {amount}. The same service is already on account {account_no}. Please remove the duplicate and confirm the corrected balance in writing.{rule_cite? This request relies on {rule_cite}.}
