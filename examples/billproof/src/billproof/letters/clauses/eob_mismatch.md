---
id: eob_mismatch
title: Bill does not match the EOB
letter_types: [dispute, appeal]
ground: EOB_BILL_MISMATCH
slots: [account_no, claim_id, payer, amount, rule_cite, lines]
summary: The bill asks for more than the explanation of benefits allows.
---

The bill for account {account_no} does not match the patient responsibility on the explanation of benefits{claim_id? for claim {claim_id}}{payer? from {payer}}. The amount in dispute is {amount}.{lines? This request covers {lines}.} Please change the bill so the patient balance matches that explanation of benefits, and send a corrected statement.{rule_cite? This request relies on {rule_cite}.}
