---
id: nsa_balance_bill
title: No Surprises Act balance
letter_types: [dispute, appeal]
ground: NO_SURPRISES_ACT
slots: [account_no, provider, claim_id, payer, amount, rule_cite, lines]
summary: The balance is above what the No Surprises Act allows the patient to be billed.
---

Account {account_no} with {provider} shows a patient balance above the protected amount{claim_id? for claim {claim_id}}{payer? ({payer})}. The amount in dispute is {amount}.{lines? This request covers {lines}.} Please reduce any patient balance above what the No Surprises Act allows for this service. If you believe a signed notice-and-consent form creates an exception, please include that form.{rule_cite? This request relies on {rule_cite}.} Surprise-bill questions can go to the No Surprises Help Desk at 1-800-985-3059.
