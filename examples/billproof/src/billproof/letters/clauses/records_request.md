---
id: records_request
title: Itemized bill and records
letter_types: [itemized_request, dispute, appeal]
ground: records_request
slots: [account_no, provider, claim_id, payer, deadline, lines]
summary: Ask for an itemized bill and the records behind the charges.
---

Please send, for account {account_no} with {provider}, an itemized bill that lists each code, description, date of service, units, and charge.{lines? This request covers {lines}.}{claim_id? Please also send the explanation of benefits for claim {claim_id}{payer? ({payer})}.} Please also send the orders or notes that support the services on that bill. I need those records before I can review the balance.{deadline? Please reply by {deadline}.}
