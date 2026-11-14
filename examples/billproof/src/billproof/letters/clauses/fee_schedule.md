---
id: fee_schedule
title: Fee-schedule overcharge
letter_types: [dispute, appeal]
ground: OVER_FEE_SCHEDULE
slots: [line_no, code, label, dos, amount, rule_cite]
summary: The charge is above the fee schedule, and the excess should not move to the patient.
---

Line {line_no} bills {code} ({label}){dos? on {dos}} for {amount}. The charge is above the fee-schedule amount for this code. Please reduce the line to the allowed amount on the explanation of benefits. An insurer contractual adjustment should stay off the patient balance.{rule_cite? This request relies on {rule_cite}.}
