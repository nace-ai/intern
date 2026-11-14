/* Twelve Angry Drexes: shared data, simulated run and UI helpers for the three mocks.

   EVERY PROBABILITY HERE IS SIMULATED (seeded, deterministic). No Drex, NDI, Intern or
   Keenable call is made. Statute citations, titles and links come from LawWiki
   API (lawwiki-data.js, built from data/statutes). Cases, people and documents are invented.
   The engine (engine/) will write the same event shape, so these screens can show a real run. */
(function () {
  const root = typeof window !== "undefined" ? window : globalThis;
  const LAW = root.TAD_STATUTES || {};
  const RULINGS = root.TAD_RULINGS || {};
  const sig = (x) => 1 / (1 + Math.exp(-x));
  const clamp = (x, a, b) => Math.max(a, Math.min(b, x));
  function rng(seed) {
    let a = seed >>> 0;
    return () => {
      a = (a + 0x6d2b79f5) | 0;
      let t = Math.imul(a ^ (a >>> 15), 1 | a);
      t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
      return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
    };
  }
  function hash(s) {
    let h = 2166136261;
    for (let i = 0; i < s.length; i++) { h ^= s.charCodeAt(i); h = Math.imul(h, 16777619); }
    return h >>> 0;
  }

  // Drex is billed per input token: $0.12 per million (Drex Max console price). Cost of a decision = its input tokens x this rate.
  // The frontier comparison stays the published one: $3.33 per 1,000 decisions, 2.0 s each (Drex technical report, Table 8, October 2026).
  const USD_PER_M_INPUT = 0.12;
  const FRONTIER = { usdPer1k: 3.33, secEach: 2.0 };
  const ROLES = root.TAD_ROLES || { groups: [], roles: {} };
  const DISCLAIMER = "Legal information, not legal advice. A simulation, not a prediction.";

  // 12 juror personas. lean shifts toward "liable"; rule/doc/equity scale how much each kind of fact moves them.
  const JURORS = [
    ["J01", "Retired bookkeeper", "Wants a receipt for every dollar", 0.1, 1.0, 1.4, 0.6],
    ["J02", "Small landlord", "Has dealt with damaged units", -0.7, 0.9, 1.0, 0.5],
    ["J03", "Night-shift nurse", "Trusts deadlines", 0.2, 1.3, 0.8, 1.0],
    ["J04", "Paralegal student", "Reads the statute literally", 0.1, 1.6, 1.0, 0.4],
    ["J05", "Restaurant owner", "Sides with whoever kept records", -0.4, 0.8, 1.3, 0.6],
    ["J06", "Union electrician", "Suspicious of companies", 0.5, 1.0, 0.7, 1.2],
    ["J07", "Software tester", "Looks for the missing step", 0.0, 1.2, 1.2, 0.5],
    ["J08", "Property manager", "Knows how notices go wrong", -0.5, 1.2, 0.9, 0.4],
    ["J09", "Graduate student", "Has lost a deposit before", 0.7, 0.8, 0.7, 1.3],
    ["J10", "Insurance adjuster", "Discounts every claim a little", -0.3, 1.0, 1.3, 0.5],
    ["J11", "School bus driver", "Goes with what seems fair", 0.1, 0.6, 0.6, 1.5],
    ["J12", "Accountant", "Follows the numbers, not the story", -0.1, 1.1, 1.4, 0.3],
  ].map(([id, name, note, lean, rule, doc, equity]) => ({ id, name, note, lean, rule, doc, equity }));

  // A ruling the asker uploads. `won` is the side that prevailed (index into the case's sides); the citation and
  // passages come from data/rulings through lawwiki-data.js, cut from the ruling's own text.
  const ruling = (name, won) => ({ name, won, cite: name, excerpt: "", holding: "", disposition: "", source: "", real: false, ...(RULINGS[name] || {}) });
  const law = (key, applies, excerpt) => ({ key, applies, excerpt: excerpt || null, ...(LAW[key] || { citation: key, title: "", url: "#" }) });

  const CASES = [
    {
      id: "ca-deposit", short: "Deposit returned late", jurisdiction: "CA", forum: "Small claims (simulated jury)", practice: "real_estate", asker_side: 0,
      title: "Whitfield v. Harlow Property Group", claim: "Return of $2,400 withheld from a $3,000 deposit",
      asker: { kind: "Pro se", who: "Tenant, representing herself", initials: "DW", name: "Dana W." },
      ask: "My landlord kept $2,400 of my $3,000 deposit for \"cleaning and repainting\" and sent the itemized list 34 days after I moved out. No receipts. Do I have a case in small claims?",
      upload: { name: "lease_and_statement.pdf", kind: "pdf", pages: 6, via: "NDI parse, extract, ground" },
      fields: [["deposit_amount", "$3,000", 2, 0.99], ["move_out_date", "2026-08-31", 5, 0.98], ["statement_date", "2026-10-04", 5, 0.97],
        ["days_to_statement", "34", 5, 0.97], ["amount_withheld", "$2,400", 5, 0.99], ["receipts_attached", "none", 6, 0.91], ["tenancy_months", "26", 1, 0.96]],
      statutes: [
        law("CA/CIV/1950.5", 0.97, "No later than 21 calendar days after the tenant has vacated the premises … statutory damages of up to twice the amount of the security, in addition to actual damages."),
        law("CA/CCP/116.221", 0.88, "… the small claims court has jurisdiction in an action brought by a natural person, if the amount of the demand does not exceed twelve thousand five hundred dollars ($12,500) …"),
        law("CA/CIV/1941.1", 0.09), law("CA/CIV/1942", 0.07), law("CA/CIV/1950.7", 0.04),
      ],
      base: -0.2,
      facts: [
        { id: "f1", tag: "rule", on: false, label: "Itemized statement sent within 21 days", w: [1.3, -0.9] },
        { id: "f2", tag: "doc", on: false, label: "Receipts attached for deductions over $125", w: [0.7, -0.8] },
        { id: "f3", tag: "doc", on: true, label: "Move-out photos show a clean unit", w: [-0.7, 0.6] },
        { id: "f4", tag: "equity", on: true, label: "Tenant lived there over two years", w: [-0.3, 0.4] },
        { id: "f5", tag: "rule", on: false, label: "Landlord offered a pre-move-out inspection", w: [0.3, -0.4] },
      ],
      tiers: ["Nothing", "Part of the $2,400", "Full $2,400", "$2,400 plus bad-faith damages"],
      sides: ["Tenant", "Landlord"],
      speeches: [
        [["Clerk", "Case called. Claim: $2,400 withheld from a $3,000 residential deposit. Statement dated 34 days after move-out."], ["Tenant", "The statute gives 21 days. The list arrived on day 34 and carries no receipts."]],
        [["Tenant", "Exhibit 2, page 5: move-out August 31, statement October 4. Exhibit 3: photos taken at move-out."], ["Judge", "Objection overruled. The photos are dated and relevant."]],
        [["Landlord", "Repainting after 26 months was needed, and the contractor was booked out. The delay was not bad faith."], ["Landlord", "The lease allows cleaning charges."]],
        [["Judge", "Decide two things: is the landlord liable for the withheld amount, and what should be awarded."], ["Foreperson", "The jury has voted."]],
      ],
      args: [["Lead with the 21-day deadline", "Lead with the missing receipts", "Lead with the photos"], ["Show the dated statement", "Show the photos", "Read the lease clause"],
        ["Argue ordinary wear", "Argue contractor delay", "Argue lease terms"], ["Ask for bad-faith damages", "Ask for the $2,400 only", "Offer to split"]],
    },
    {
      id: "fl-notice", short: "Claim notice by regular mail", jurisdiction: "FL", forum: "County court (simulated jury)", practice: "real_estate", asker_side: 1,
      title: "Okafor v. Bayline Residential", claim: "Return of a $1,850 deposit after a late claim notice",
      asker: { kind: "Paralegal", who: "Paralegal, property manager's legal department", initials: "PL", name: "Paralegal" },
      ask: "One of our Florida properties sent the deposit claim notice on day 35 by regular mail. The tenant wants the full $1,850 back plus fees. What is our exposure if this goes to county court? Photo of the notice attached.",
      upload: { name: "notice_letter.jpg", kind: "image", pages: 1, via: "Drex Max reads the photo, NDI parse and extract" },
      fields: [["lease_end_date", "2026-08-01", 1, 0.97], ["notice_date", "2026-09-05", 1, 0.95], ["days_to_notice", "35", 1, 0.95],
        ["delivery_method", "USPS first class", 1, 0.88], ["claim_amount", "$1,850", 1, 0.98], ["claim_reason", "carpet replacement", 1, 0.93], ["tenant_objection", "2026-09-12", 1, 0.84]],
      statutes: [
        law("FL/FS/83.49", 0.98, "… the landlord must, within 30 days after the termination of the rental agreement, provide the tenant written notice by certified mail … If the landlord fails to give the required written notice within the 30-day period, he or she forfeits the right to impose a claim upon the security deposit …"),
        law("FL/FS/83.48", 0.86, "… the party in whose favor a judgment or decree has been rendered may recover reasonable attorney fees and court costs from the nonprevailing party."),
        law("FL/FS/83.51", 0.12), law("FL/FS/83.56", 0.06),
      ],
      base: -0.1,
      facts: [
        { id: "f1", tag: "rule", on: false, label: "Claim notice sent within 30 days", w: [1.5, -1.0] },
        { id: "f2", tag: "rule", on: false, label: "Notice sent by certified mail or agreed e-mail", w: [0.8, -0.6] },
        { id: "f3", tag: "rule", on: true, label: "Tenant objected within 15 days", w: [-0.6, 0.5] },
        { id: "f4", tag: "doc", on: true, label: "Carpet damage documented with dated photos", w: [0.2, -0.3] },
        { id: "f5", tag: "doc", on: true, label: "Lease carries the deposit disclosure", w: [0.4, -0.2] },
      ],
      tiers: ["Nothing", "Part of the deposit", "Full $1,850", "$1,850 plus attorney fees"],
      sides: ["Tenant", "Landlord"],
      speeches: [
        [["Clerk", "Case called. Claim: return of a $1,850 deposit. Notice of claim dated 35 days after the lease ended, sent by first-class mail."], ["Tenant", "The notice was late and was not sent by certified mail. The right to claim the deposit is forfeited."]],
        [["Tenant", "Exhibit 1: the notice letter, dated September 5. The lease ended August 1."], ["Judge", "Objection sustained. Counsel will not characterize the landlord's motives."]],
        [["Landlord", "The carpet was destroyed and we have dated photos. The tenant received the notice and objected, so no harm followed."], ["Landlord", "We may still bring a separate action for the damage."]],
        [["Judge", "Decide whether the landlord must return the deposit, and what should be awarded."], ["Foreperson", "The jury has voted."]],
      ],
      args: [["Lead with the 30-day deadline", "Lead with the mailing method", "Lead with the objection"], ["Show the notice date", "Show the envelope", "Read the statute notice text"],
        ["Argue actual receipt", "Argue the carpet damage", "Argue substantial compliance"], ["Ask for deposit and fees", "Ask for the deposit only", "Offer to split"]],
    },
    {
      id: "ca-wages", short: "Final paycheck nine days late", jurisdiction: "CA", forum: "Superior court (simulated jury)", practice: "employment", asker_side: 0,
      title: "Reyes v. Northgate Fabrication", claim: "Waiting-time penalty for final wages paid 9 days after discharge",
      asker: { kind: "Attorney", who: "Employment attorney, plaintiff side", initials: "AT", name: "A. Tran, Esq." },
      ask: "Client was discharged September 3; final wages of $3,150 arrived by mail 9 days later. Employer says payroll runs biweekly. Attaching Naranjo v. Spectrum Security (Cal. 2024) on good-faith disputes. How would a jury see willfulness?",
      upload: { name: "naranjo_v_spectrum_s279397.pdf", kind: "pdf", pages: 29, via: "NDI parse, split, extract, ground" },
      fields: [["court", "Supreme Court of California", 1, 0.98], ["docket_number", "S279397", 1, 0.99], ["decision_date", "May 6, 2024", 1, 0.98],
        ["opinion_by", "Kruger, J.", 1, 0.96], ["statute_at_issue", "Lab. Code, § 226", 1, 0.93], ["also_addresses", "willful nonpayment of wages", 1, 0.88], ["disposition", "Court of Appeal affirmed", 29, 0.97]],
      ruling: ruling("naranjo_v_spectrum_s279397", 1),
      statutes: [
        law("CA/LAB/201", 0.97, "If an employer discharges an employee, the wages earned and unpaid at the time of discharge are due and payable immediately."),
        law("CA/LAB/203", 0.95, "If an employer willfully fails to pay … the wages of the employee shall continue as a penalty … but the wages shall not continue for more than 30 days."),
        law("CA/LAB/218.5", 0.55), law("CA/LAB/202", 0.08, "If an employee … quits his or her employment, his or her wages shall become due and payable not later than 72 hours thereafter …"),
      ],
      base: 0.0,
      facts: [
        { id: "f1", tag: "rule", on: true, label: "Employee was discharged, not a resignation", w: [-0.8, 0.9] },
        { id: "f2", tag: "rule", on: false, label: "Wages paid on the day of discharge", w: [1.4, -2.0] },
        { id: "f3", tag: "equity", on: false, label: "Employer had a good-faith dispute over wages owed", w: [0.6, -1.1] },
        { id: "f4", tag: "doc", on: true, label: "Employer knew the termination date in advance", w: [-0.3, 0.5] },
        { id: "f5", tag: "equity", on: false, label: "Employee avoided or refused payment", w: [0.2, -1.2] },
      ],
      tiers: ["Nothing", "Interest only", "Penalty for 9 days ($2,520)", "Penalty plus attorney fees"],
      sides: ["Employee", "Employer"],
      speeches: [
        [["Clerk", "Case called. Claim: waiting-time penalty. Discharge September 3, final wages mailed September 12."], ["Employee", "Wages are due immediately on discharge. They came nine days later. Nine days at $280 is $2,520."]],
        [["Employee", "Exhibit 1: the termination letter, prepared two days before the discharge. The employer knew the date."], ["Judge", "Objection overruled. The letter goes to knowledge."]],
        [["Employer", "Payroll runs every two weeks through an outside processor. Nobody intended to withhold anything."], ["Employer", "The full amount was paid without demand."]],
        [["Judge", "Decide whether the failure to pay on time was willful, and what should be awarded."], ["Foreperson", "The jury has voted."]],
      ],
      args: [["Lead with 'due immediately'", "Lead with the nine days", "Lead with the advance letter"], ["Show the termination letter", "Show the pay stub", "Read the penalty section"],
        ["Argue no intent", "Argue the payroll cycle", "Argue prompt full payment"], ["Ask for penalty and fees", "Ask for the penalty only", "Offer to settle"]],
    },
    {
      id: "tx-theft", short: "Charge the tool theft?", jurisdiction: "TX", forum: "District court (simulated jury)", practice: "prosecutor", asker_side: 0,
      title: "State v. Dalton", claim: "Theft of two tool kits with a shelf price of $2,740, charged as a state jail felony",
      asker: { kind: "Attorney", who: "Assistant district attorney, intake unit", initials: "DA", name: "ADA R. Okafor" },
      ask: "Offense report attached. The suspect left a hardware store with two boxed tool kits, shelf price $2,740 in total, and paid nothing. He says a clerk told him his employer had prepaid the order. Can we prove intent and a value of $2,500 or more, or should this be filed as a misdemeanor?",
      upload: { name: "offense_report.pdf", kind: "pdf", pages: 2, via: "NDI parse, extract, ground" },
      fields: [["offense_date", "2026-09-19", 1, 0.98], ["store", "Brazos Hardware, Store 14", 1, 0.97], ["items_taken", "2 boxed tool kits", 1, 0.95], ["shelf_price_total", "$2,740", 1, 0.96],
        ["video_available", "yes, exit camera", 2, 0.93], ["receipt_found", "none", 2, 0.9], ["suspect_statement", "said his employer had prepaid the order", 2, 0.88]],
      statutes: [law("TX/PE/31.03", 0.96), law("TX/PE/31.08", 0.9), law("TX/PE/8.02", 0.62), law("TX/PE/31.04", 0.05)],
      base: 0.1,
      facts: [
        { id: "f1", tag: "doc", on: true, label: "Exit video shows the defendant leaving without paying", w: [-1.3, 0.9] },
        { id: "f2", tag: "rule", on: true, label: "Fair market value of the items was $2,500 or more", w: [-0.9, 0.5] },
        { id: "f3", tag: "equity", on: false, label: "Defendant reasonably believed his employer had prepaid the order", w: [0.5, -1.6] },
        { id: "f4", tag: "doc", on: false, label: "Store records show a prepaid order on the employer's account", w: [0.3, -1.2] },
        { id: "f5", tag: "equity", on: true, label: "Defendant removed the security tags before leaving", w: [-0.6, 0.8] },
      ],
      tiers: ["Not guilty", "Guilty of a Class B misdemeanor (under $750)", "Guilty of a Class A misdemeanor ($750 to $2,500)", "Guilty of a state jail felony ($2,500 to $30,000)"],
      sides: ["State", "Defendant"],
      speeches: [
        [["Clerk", "Cause called. The State charges theft of property valued at $2,500 or more but less than $30,000."], ["State", "He walked out with two tool kits priced at $2,740 and paid nothing. The exit camera shows it."]],
        [["State", "Exhibit 1: the exit recording and the shelf tags. The security tags were found in aisle nine."], ["Judge", "Objection overruled. The tags go to intent."]],
        [["Defendant", "A clerk told him the order was prepaid by his employer. He believed he was collecting a paid order."], ["Defendant", "Shelf price is not fair market value. One kit was an open-box return."]],
        [["Judge", "The State must prove each element beyond a reasonable doubt, including intent to deprive and value."], ["Foreperson", "The jury has voted."]],
      ],
      args: [["Lead with the video", "Lead with the value", "Lead with the removed tags"], ["Show the exit recording", "Show the shelf tags", "Call the loss-prevention officer"],
        ["Argue mistake of fact", "Argue value under $2,500", "Argue no intent to deprive"], ["Ask for the felony verdict", "Ask for the lesser offense", "Offer a plea"]],
    },
    {
      id: "ca-injury", short: "Rear-ended at a red light", jurisdiction: "CA", forum: "Superior court (simulated jury)", practice: "personal_injury", asker_side: 0,
      title: "Marquez v. Linden Courier Co.", claim: "Negligence in a rear-end collision: $18,400 in medical bills, lost wages, pain and suffering",
      asker: { kind: "Paralegal", who: "Paralegal, personal injury firm, plaintiff side", initials: "PI", name: "Paralegal" },
      ask: "Our client was stopped at a red light and was hit from behind by a delivery van. Collision report attached. Her auto insurance had lapsed 11 days earlier. How would a jury see fault, and can she still recover pain and suffering?",
      upload: { name: "collision_report.pdf", kind: "pdf", pages: 2, via: "NDI parse, extract, ground" },
      fields: [["collision_date", "2026-03-14", 1, 0.98], ["location", "Fulton Avenue at 9th Street", 1, 0.96], ["party_1", "delivery van, Linden Courier Co.", 1, 0.95], ["party_2", "sedan, stopped at a red signal", 1, 0.95],
        ["primary_collision_factor", "following too closely", 1, 0.92], ["injuries", "neck and shoulder strain, transported", 2, 0.9], ["party_2_insurance", "policy lapsed 2026-03-03", 2, 0.89], ["medical_bills", "$18,400", 2, 0.94]],
      statutes: [law("CA/CIV/1714", 0.96), law("CA/VEH/21703", 0.93), law("CA/EVID/669", 0.8), law("CA/CIV/3333.4", 0.72), law("CA/CCP/335.1", 0.3)],
      base: 0.3,
      facts: [
        { id: "f1", tag: "rule", on: true, label: "Plaintiff was stopped at a red light when struck from behind", w: [-1.2, 0.9] },
        { id: "f2", tag: "doc", on: true, label: "Collision report lists following too closely as the primary factor", w: [-0.7, 0.8] },
        { id: "f3", tag: "equity", on: false, label: "Plaintiff braked suddenly without reason", w: [0.3, -1.3] },
        { id: "f4", tag: "rule", on: false, label: "Plaintiff's car was insured on the day of the collision", w: [-0.3, 0.3] },
        { id: "f5", tag: "doc", on: true, label: "Medical records tie the neck injury to the collision", w: [-0.9, 0.5] },
      ],
      tiers: ["Nothing", "Medical bills only ($18,400)", "Medical bills plus lost wages", "Medical bills, lost wages, pain and suffering"],
      sides: ["Plaintiff", "Courier company"],
      speeches: [
        [["Clerk", "Case called. Claim: negligence in a rear-end collision on March 14."], ["Plaintiff", "She was stopped at a red light. The van hit her from behind. Her medical bills are $18,400."]],
        [["Plaintiff", "Exhibit 1: the collision report. The officer listed following too closely as the primary factor."], ["Judge", "Objection sustained as to the officer's opinion on fault. The observations stay in."]],
        [["Courier company", "Traffic was stop and go. She braked hard with no warning."], ["Courier company", "She had no insurance that day. The law bars pain and suffering for an uninsured owner."]],
        [["Judge", "Decide whether the van driver failed to use ordinary care, and what damages the law allows."], ["Foreperson", "The jury has voted."]],
      ],
      args: [["Lead with the red light", "Lead with the report", "Lead with the medical bills"], ["Show the collision report", "Show the medical records", "Call the treating doctor"],
        ["Argue a sudden stop", "Argue the insurance lapse", "Argue a prior injury"], ["Ask for all damages", "Ask for bills and wages", "Offer to settle"]],
    },
    {
      id: "ny-goods", short: "Rejected shipment of valves", jurisdiction: "NY", forum: "Supreme Court, Commercial Division (simulated jury)", practice: "corporate", asker_side: 0,
      title: "Hartwell Brewing Co. v. Castellan Valve Corp.", claim: "Refund of $86,000 paid for 400 valves rejected as nonconforming",
      asker: { kind: "Attorney", who: "In-house counsel, buyer's legal department", initials: "GC", name: "In-house counsel" },
      ask: "We paid $86,000 for 400 stainless valves rated 150 psi. 112 failed our pressure test at 120 psi. We emailed a rejection 9 days after delivery. The seller offers replacements in 6 weeks, after our line start date. Purchase order and inspection report attached. Refund, or must we accept the cure?",
      upload: { name: "purchase_order_and_inspection.pdf", kind: "pdf", pages: 3, via: "NDI parse, extract, ground" },
      fields: [["po_number", "HB-2291", 1, 0.99], ["quantity", "400 valves", 1, 0.98], ["price_paid", "$86,000", 1, 0.99], ["pressure_rating_required", "150 psi", 1, 0.95],
        ["contract_delivery_deadline", "2026-08-15", 1, 0.93], ["delivery_date", "2026-08-03", 2, 0.97], ["units_failed", "112 of 400", 2, 0.94], ["rejection_notice_date", "2026-08-12", 3, 0.96]],
      statutes: [law("NY/UCC/2-601", 0.95), law("NY/UCC/2-602", 0.93), law("NY/UCC/2-508", 0.7), law("NY/UCC/2-725", 0.08)],
      base: 0.2,
      facts: [
        { id: "f1", tag: "doc", on: true, label: "Inspection report shows 112 of 400 valves failed below the rated pressure", w: [-1.4, 0.9] },
        { id: "f2", tag: "rule", on: true, label: "Buyer notified the seller of rejection within 9 days of delivery", w: [-1.1, 0.7] },
        { id: "f3", tag: "rule", on: false, label: "Seller offered conforming replacements before the contract delivery deadline", w: [0.4, -1.3] },
        { id: "f4", tag: "equity", on: false, label: "Buyer installed and used some of the valves after rejecting them", w: [0.3, -1.2] },
        { id: "f5", tag: "doc", on: true, label: "Purchase order states the 150 psi rating as a requirement", w: [-0.8, 0.5] },
      ],
      tiers: ["Nothing", "Price of the 112 failed valves ($24,080)", "Full refund ($86,000)", "Full refund plus incidental damages"],
      sides: ["Buyer", "Seller"],
      speeches: [
        [["Clerk", "Case called. Claim: refund of the price paid for goods rejected as nonconforming."], ["Buyer", "The order required valves rated 150 psi. 112 of 400 failed at 120. We rejected the shipment in nine days."]],
        [["Buyer", "Exhibit 1: the purchase order and the inspection report, unit by unit."], ["Judge", "Objection overruled. The report is a business record."]],
        [["Seller", "We offered replacement valves at once. A buyer cannot refuse a cure and demand a refund."], ["Seller", "Seventy-two percent of the shipment passed. They can keep those."]],
        [["Judge", "Decide whether the goods failed to conform, whether the rejection was timely, and whether the seller had a right to cure."], ["Foreperson", "The jury has voted."]],
      ],
      args: [["Lead with the failed test", "Lead with the timely notice", "Lead with the written rating"], ["Show the inspection report", "Show the purchase order", "Show the rejection email"],
        ["Argue the right to cure", "Argue partial acceptance", "Argue the test was flawed"], ["Ask for a full refund", "Ask for the failed units only", "Offer to settle"]],
    },
    {
      id: "us-photo", short: "Photo used without a license", jurisdiction: "US", forum: "U.S. District Court (simulated jury)", practice: "ip", asker_side: 0,
      title: "Adeyemi v. Brightwater Realty LLC", claim: "Copyright infringement: a skyline photograph used in 14 paid listings without a license",
      asker: { kind: "Pro se", who: "Freelance photographer, representing herself", initials: "TA", name: "Tolu A." },
      ask: "A realty company used my skyline photo in 14 paid listings. I registered the copyright, but only after I found the listings. Registration certificate attached. They say it was fair use and that they took it down. Can I get statutory damages?",
      upload: { name: "registration_certificate.pdf", kind: "pdf", pages: 2, via: "NDI parse, extract, ground" },
      fields: [["registration_number", "VA 2-418-553", 1, 0.98], ["title_of_work", "Harbor Skyline at Dusk", 1, 0.97], ["author", "Tolu Adeyemi", 1, 0.98], ["date_of_first_publication", "2025-05-20", 1, 0.95],
        ["effective_date_of_registration", "2026-07-08", 1, 0.97], ["infringement_first_seen", "2026-06-02", 2, 0.86], ["listings_using_photo", "14", 2, 0.9]],
      statutes: [law("US/USC-17/501", 0.96), law("US/USC-17/106", 0.94), law("US/USC-17/412", 0.9), law("US/USC-17/107", 0.55), law("US/USC-17/512", 0.1)],
      base: 0.4,
      facts: [
        { id: "f1", tag: "doc", on: true, label: "Photographer holds a registration certificate for the photograph", w: [-1.2, 0.6] },
        { id: "f2", tag: "doc", on: true, label: "Listings show the same photograph with her watermark cropped out", w: [-1.2, 1.0] },
        { id: "f3", tag: "rule", on: false, label: "Registration took effect before the infringement began", w: [-0.2, 0.4] },
        { id: "f4", tag: "equity", on: false, label: "Company had a license or permission to use the photograph", w: [0.4, -2.0] },
        { id: "f5", tag: "equity", on: true, label: "Use was commercial advertising, not comment or news", w: [-0.9, 0.6] },
      ],
      tiers: ["Nothing", "Actual damages: a license fee ($1,200)", "Actual damages plus the company's profits", "Statutory damages and attorney fees"],
      sides: ["Photographer", "Realty company"],
      speeches: [
        [["Clerk", "Case called. Claim: infringement of copyright in one photograph."], ["Photographer", "They used my photograph in fourteen paid listings. Nobody asked and nobody paid."]],
        [["Photographer", "Exhibit 1: the registration certificate and screenshots of the listings. My watermark was cropped out."], ["Judge", "Objection overruled. The screenshots are authenticated."]],
        [["Realty company", "The photo was online for anyone to see. We removed it the day we were told."], ["Realty company", "She registered after the listings ran. The statute limits what she can recover."]],
        [["Judge", "Decide whether the company copied the photograph without permission, and what remedy the statute allows."], ["Foreperson", "The jury has voted."]],
      ],
      args: [["Lead with the cropped watermark", "Lead with the fourteen listings", "Lead with the registration"], ["Show the certificate", "Show the listing screenshots", "Show her license price list"],
        ["Argue fair use", "Argue late registration", "Argue prompt removal"], ["Ask for statutory damages", "Ask for the license fee and profits", "Offer to settle"]],
    },
  ];

  const PHASES = ["Opening", "Claimant's evidence", "Defense", "Instructions and vote"];
  const FRAC = [0.35, 0.85, 0.6, 1.0];

  function dist(n, mu, r, spread) { // discrete bell around mu on 0..n-1
    const w = []; let s = 0;
    for (let i = 0; i < n; i++) { const v = Math.exp(-((i - mu) ** 2) / (2 * spread * spread)) + r() * 0.03; w.push(v); s += v; }
    return w.map((v) => v / s);
  }
  function pick(n, top, r) { // choice distribution with option 0..n-1, winner index 0 by construction then shuffled
    const rest = []; let s = 0;
    for (let i = 1; i < n; i++) { const v = 0.2 + r(); rest.push(v); s += v; }
    const d = [top, ...rest.map((v) => (v / s) * (1 - top))];
    const win = Math.floor(r() * n); [d[0], d[win]] = [d[win], d[0]];
    return d;
  }
  const confNoul = (p) => Math.abs(2 * p - 1);
  const confChoice = (d) => (Math.max(...d) - 1 / d.length) / (1 - 1 / d.length);
  function confScore(d) { const mu = d.reduce((a, v, i) => a + v * i, 0); return 1 - d.reduce((a, v, i) => a + v * Math.abs(i - mu), 0) / (d.length - 1); }

  /* Simulated run. state = {factId: true|false} overrides the case defaults. */
  function run(caseId, state) {
    const c = CASES.find((x) => x.id === caseId) || CASES[0];
    const facts = c.facts.map((f) => ({ ...f, on: state && f.id in state ? !!state[f.id] : f.on }));
    const r = rng(hash(c.id + "|" + facts.map((f) => +f.on).join("")));
    const r2 = rng(hash(c.id + "|role|" + facts.map((f) => +f.on).join("")));
    const ev = []; let t = 0, req = 0;
    const add = (o) => { t += o.ms; ev.push({ i: ev.length, t, ...o }); return ev[ev.length - 1]; };
    const lat = (base) => Math.round(base * (0.78 + r() * 0.5));
    function drex(o) { // one decision; o.first marks the question that carries the state tokens of its request; o.rg = a separate random stream
      const model = o.model || "drex-v2.0", g = o.rg || r;
      const tok = o.first === false ? Math.round(40 + g() * 50) : Math.round((o.state || 1500) * (0.9 + g() * 0.2));
      if (o.first !== false) req++;
      const d = o.dist, conf = o.type === "noul" ? confNoul(o.p) : o.type === "score" ? confScore(d) : confChoice(d);
      const stable = o.type !== "choice" ? true : g() > (d.length >= 6 ? 0.13 : Math.max(...d) < 0.6 ? 0.22 : 0.03);
      return add({ src: "drex", model, req: "req_" + String(req).padStart(3, "0"), ms: o.first === false ? Math.round(18 + g() * 14) : Math.round((model === "drex-max-v1.1" ? 183 : 151) * (0.78 + g() * 0.5)),
        tok, usd: (tok * USD_PER_M_INPUT) / 1e6, conf, stable, route: conf < 0.2 || !stable ? "person" : "auto", ...o, first: undefined, rg: undefined });
    }
    const step = (src, id, text, ms, extra) => add({ src, id, text, ms, ...(extra || {}) });

    // 1. intake
    step("intern", "slack.message", `Message from ${c.asker.name} in #ask-the-jury`, 60);
    if (c.upload.kind === "image") {
      drex({ agent: "Clerk", id: "image_legible", type: "noul", q: "Is the photographed document legible enough to extract dates and amounts?", p: 0.93, model: "drex-max-v1.1", state: 2600 });
      drex({ agent: "Clerk", id: "image_has_instructions", type: "noul", q: "Does the image contain text addressed to the reader rather than document content?", p: 0.03, model: "drex-max-v1.1", state: 2600 });
    }
    step("ndi", "parse", `Parsed ${c.upload.name}: ${c.upload.pages} page${c.upload.pages > 1 ? "s" : ""}`, 900 + Math.round(r() * 500));
    step("ndi", "extract", `Extracted ${c.fields.length} fields with page citations`, 700 + Math.round(r() * 400));
    step("ndi", "ground", `Located ${c.fields.length} quotes on the page`, 300 + Math.round(r() * 200));
    const docTypes = ["Lease or rental record", "Notice letter", "Court order or ruling", "Pay record", "Something else"];
    const dt = pick(5, 0.86, r); const want = c.upload.kind === "image" ? 1 : c.id === "ca-wages" ? 2 : 0; const m = dt.indexOf(Math.max(...dt)); [dt[m], dt[want]] = [dt[want], dt[m]];
    drex({ agent: "Clerk", id: "document_type", type: "choice", q: "What kind of document was uploaded?", options: docTypes, dist: dt, state: 3200 });
    const roles = ["Pro se", "Attorney", "Paralegal"]; const rd = pick(3, 0.9, r); const ri = roles.indexOf(c.asker.kind); const rm = rd.indexOf(Math.max(...rd)); [rd[rm], rd[ri]] = [rd[ri], rd[rm]];
    drex({ agent: "Clerk", id: "asker_role", type: "choice", q: "Who is asking?", options: roles, dist: rd, first: false });
    const jur = ["CA", "FL", "NY", "TX", "US"]; const jd = pick(5, 0.94, r); const ji = jur.indexOf(c.jurisdiction); const jm = jd.indexOf(Math.max(...jd)); [jd[jm], jd[ji]] = [jd[ji], jd[jm]];
    drex({ agent: "Clerk", id: "jurisdiction", type: "choice", q: "Which jurisdiction's law governs?", options: jur, dist: jd, first: false });
    // Practice-area role: Drex picks a group, then an area inside each group, in the same request. Own random stream (r2) so the trial below is unchanged.
    const role = ROLES.roles[c.practice] || null, groups = ROLES.groups || [];
    const inGroup = (gi) => Object.keys(ROLES.roles).filter((id) => ROLES.roles[id].group === groups[gi]);
    const topAt = (n, top, want, g) => { const d = pick(n, top, g); const m = d.indexOf(Math.max(...d)); [d[m], d[want]] = [d[want], d[m]]; return d; };
    let detected = null;
    if (role) {
      const own = groups.indexOf(role.group), gdist = topAt(groups.length, 0.9 + r2() * 0.06, own, r2);
      const g1 = drex({ agent: "Clerk", id: "practice_group", qid: "practice_group", type: "choice", q: "Which area of law is this question about?", options: groups.slice(), dist: gdist, first: false, rg: r2 });
      let a1 = null;
      groups.forEach((gname, gi) => {
        const ids = inGroup(gi), isOwn = gi === own, want = isOwn ? ids.indexOf(c.practice) : Math.floor(r2() * ids.length);
        const ad = topAt(ids.length, isOwn ? 0.86 + r2() * 0.08 : 0.5 + r2() * 0.2, want, r2);
        const ev2 = drex({ agent: "Clerk", id: "practice_area", qid: "area_" + gname.split(" ")[0].toLowerCase(), type: "choice", options: ids.map((id) => ROLES.roles[id].label), dist: ad, first: false, rg: r2,
          q: gname === "Criminal" ? "Is the asker defending the accused or bringing the charge?" : `If the question is about ${gname.toLowerCase()} law, which practice area fits it best?` });
        if (isOwn) a1 = ev2;
      });
      detected = { id: c.practice, label: role.label, p_group: Math.max(...gdist), p_area: Math.max(...a1.dist), needs_person: g1.route === "person" || a1.route === "person" };
    }
    if (c.ruling) { // an uploaded ruling is read as precedent: who won, and does it decide the same question
      const wd = pick(3, 0.84, r); const wm = wd.indexOf(Math.max(...wd)); [wd[wm], wd[c.ruling.won]] = [wd[c.ruling.won], wd[wm]];
      drex({ agent: "Clerk", id: "ruling_disposition", type: "choice", q: "Who won in the uploaded ruling?", options: [`${c.sides[0]} won`, `${c.sides[1]} won`, "Mixed result"], dist: wd, first: false });
      drex({ agent: "Clerk", id: "ruling_on_point", type: "noul", q: "Does the uploaded ruling decide the same legal question as this dispute?", p: 0.74 + r() * 0.06, first: false });
    }

    const scope = role ? (role.scope[c.jurisdiction] || []) : [];
    const inScope = (key) => { const [j, code, num] = key.split("/"); return j === c.jurisdiction && scope.some((sp) => { const [cd, ch] = sp.split(":"); return cd === code && (!ch || String(num).split(".")[0] === ch); }); };
    const scopeKeys = Object.keys(LAW).filter(inScope);
    if (role) step("intern", "role", `Legal team: ${role.label} (case). Jurors decide: ${role.finding}, ${role.burden}.`, 20, { result: {
      id: c.practice, label: role.label, group: role.group, team: role.team, lens: role.lens, finding: role.finding, burden: role.burden, source: "case", side: c.sides[c.asker_side],
      detected: { ...detected, label: role.label }, scope, scope_sections: scopeKeys.length, anchors: c.statutes.filter((x) => x.applies >= 0.5).map((x) => x.key) } });
    const finding = role ? role.finding : "liable", crime = finding === "guilty";

    // 2. statutes
    step("lawwiki", "search", `LawWiki search returned ${c.statutes.length} sections (${c.jurisdiction})${scope.length ? `, limited to ${scope.join(", ")}: ${scopeKeys.length} bundled sections in scope` : ""}`, 240 + Math.round(r() * 120));
    c.statutes.forEach((s, k) => drex({ agent: "Clerk", id: "statute_applies", type: "noul", q: `Does ${s.citation} apply to this dispute?`, cite: s.key,
      p: clamp(s.applies + (r() - 0.5) * 0.04, 0.01, 0.99), first: k === 0, state: 5200 }));
    step("keenable", "fetch", "Fetched the court's public self-help page for this claim type", 620 + Math.round(r() * 300));

    // 3. trial
    const total = c.base + facts.reduce((a, f) => a + f.w[f.on ? 1 : 0], 0);
    const jnoise = JURORS.map(() => PHASES.map(() => (r() - 0.5) * 0.5));
    const logitF = (fs, j, ji2, k) => c.base * FRAC[k] + fs.reduce((a, f) => a + f.w[f.on ? 1 : 0] * j[f.tag], 0) * FRAC[k] + j.lean + jnoise[ji2][k];
    const logit = (j, ji2, k) => logitF(facts, j, ji2, k);
    const phases = [];
    PHASES.forEach((name, k) => {
      const who = k === 2 ? c.sides[1] : c.sides[0];
      const ad = pick(3, 0.5 + r() * 0.3, r);
      drex({ agent: who + " counsel", id: "next_argument", type: "choice", q: "Which argument should counsel make next?", options: c.args[k], dist: ad, phase: k, state: 3800 });
      c.speeches[k].forEach(([speaker, text]) => step("s2", "speech", text, 700 + Math.round(r() * 500), { speaker, phase: k }));
      if (k === 1) drex({ agent: "Judge", id: "objection_sustained", type: "noul", q: "Is the objection sustained?", p: c.speeches[1][1][1].includes("sustained") ? 0.81 : 0.17, phase: k, state: 3400 });
      const strength = clamp(2 + (k === 2 ? -total : total) * 0.7, 0.2, 3.8);
      drex({ agent: "Judge", id: "argument_strength", type: "score", q: `How strong was the ${who.toLowerCase()}'s argument?`, levels: ["Very weak", "Weak", "Mixed", "Strong", "Very strong"],
        dist: dist(5, strength, r, 0.8), phase: k, state: 3400 });
      const votes = JURORS.map((j, ji2) => {
        const p = clamp(sig(logit(j, ji2, k)), 0.02, 0.98);
        const td = dist(c.tiers.length, clamp(p * (c.tiers.length - 1) * (0.75 + 0.35 * sig(total)), 0, c.tiers.length - 1), r, 0.75);
        // one request per phase: the first juror question carries the state tokens, the other 23 reuse them
        drex({ agent: j.id, id: "liable", type: "noul", q: `Juror ${j.id} (${j.name}): is the ${c.sides[1].toLowerCase()} ${finding}?`, p, phase: k, state: 4300, first: ji2 === 0 ? undefined : false });
        drex({ agent: j.id, id: "award_level", type: "score", q: `Juror ${j.id}: what should ${crime ? "the verdict be" : "be awarded"}?`, levels: c.tiers, dist: td, phase: k, first: false });
        return { id: j.id, p, tier: td, tierExp: td.reduce((a, v, i) => a + v * i, 0) };
      });
      phases.push({ name, votes });
    });
    const fin = phases[phases.length - 1].votes;
    const liable = fin.filter((v) => v.p >= 0.5).length, mean = fin.reduce((a, v) => a + v.p, 0) / 12;
    const sd = Math.sqrt(fin.reduce((a, v) => a + (v.p - mean) ** 2, 0) / 12);
    const yes = fin.filter((v) => v.p >= 0.5);
    const tierIdx = yes.length ? Math.round(yes.reduce((a, v) => a + v.tierExp, 0) / yes.length) : 0;
    const verdict = { finding, liable, notLiable: 12 - liable, mean, sd, undecided: fin.filter((v) => Math.abs(v.p - 0.5) < 0.1).length, tierIdx, tier: c.tiers[liable >= 7 ? tierIdx : 0],
      label: liable >= 9 ? `${c.sides[1]} ${finding}` : liable <= 3 ? `${c.sides[1]} not ${finding}` : "Split jury" };
    step("intern", "verdict", `Vote ${liable} to ${12 - liable}. ${verdict.label}. Mean probability ${mean.toFixed(2)}.`, 40, { result: verdict });

    // 4. what moves the vote: flip one fact at a time, all 12 jurors vote again on the same record (one request per fact, simulated on stream r2)
    const what_if = [];
    facts.forEach((f) => {
      const alt = facts.map((g) => (g.id === f.id ? { ...g, on: !g.on } : g)), want = f.on ? "FALSE" : "TRUE";
      const ps = JURORS.map((j, ji2) => clamp(sig(logitF(alt, j, ji2, 3)), 0.02, 0.98));
      JURORS.forEach((j, ji2) => drex({ agent: j.id, id: "whatif_liable", qid: `${f.id}_${j.id}`, type: "noul", fact: f.id, p: ps[ji2], rg: r2, state: 4500, first: ji2 === 0 ? undefined : false,
        q: `Juror ${j.id} (${j.name}): is the ${c.sides[1].toLowerCase()} ${finding}, assuming '${f.label}' is ${want}?` }));
      const m2 = ps.reduce((a, p) => a + p, 0) / 12;
      what_if.push({ id: f.id, label: f.label, on_now: f.on, mean_if: m2, liable_if: ps.filter((p) => p >= 0.5).length, delta: m2 - mean });
    });
    what_if.sort((a, b) => Math.abs(b.delta) - Math.abs(a.delta));
    step("intern", "what_if", `Fact that moves the vote most: ${what_if[0].label} (${what_if[0].delta >= 0 ? "+" : ""}${what_if[0].delta.toFixed(2)} if it were the other way)`, 30, { result: what_if });

    // 5. next step: Drex picks one of the role's 5 options; the proof gap is a second question in the same request
    const steps = role ? role.next_steps : ["Send a written demand that cites the statute", "File the claim now", "Gather the missing proof first", "Negotiate a settlement", "Get a lawyer's review before acting"];
    const side = c.sides[c.asker_side], odds = c.asker_side === 0 ? mean : 1 - mean, win = odds >= 0.8 ? 0 : odds >= 0.5 ? 2 : 3;
    const sd5 = topAt(5, 0.55 + r2() * 0.2, win, r2);
    const nsEv = drex({ agent: "Clerk", id: "next_step", qid: "next_step", type: "choice", options: steps.slice(), dist: sd5, rg: r2, state: 6200,
      q: `Which next step best serves the ${side.toLowerCase()} (${(role ? role.label : "legal").toLowerCase()} team), given this record and jury result?` });
    const pgEv = drex({ agent: "Clerk", id: "proof_gap", qid: "proof_gap", type: "noul", p: (mean >= 0.8 ? 0.15 : 0.6) + r2() * 0.2, rg: r2, first: false, q: "Is a document that the claim depends on missing from the record?" });
    const next_steps = { step: steps[sd5.indexOf(Math.max(...sd5))], p_step: Math.max(...sd5), options: steps.slice(), dist: sd5, for_side: side, role: role ? c.practice : null, needs_person: nsEv.route === "person",
      proof_gap: pgEv.p, moves_most: what_if[0], odds: { liable, of: 12, mean, sd }, law: ev.filter((e) => e.id === "statute_applies" && e.p >= 0.5).map((e) => e.cite), disclaimer: DISCLAIMER };
    step("intern", "next_steps", `Suggested next step: ${next_steps.step} (${Math.round(next_steps.p_step * 100)}%)`, 40, { result: next_steps });
    step("intern", "slack.reply", "Posted the result and a link to the live courtroom in the thread", 120);

    const dx = ev.filter((e) => e.src === "drex"); const ms = dx.map((e) => e.ms).sort((a, b) => a - b);
    const totals = { events: ev.length, decisions: dx.length, requests: req, tokens: dx.reduce((a, e) => a + e.tok, 0), usd: dx.reduce((a, e) => a + e.usd, 0),
      p50: ms[Math.floor(ms.length / 2)], drexMs: ms.reduce((a, v) => a + v, 0), wallMs: t, flagged: dx.filter((e) => e.route === "person").length };
    return { case: c, facts, events: ev, phases, verdict, role: (ev.find((e) => e.id === "role") || {}).result || null, what_if, next_steps, totals, frontier: { usd: (dx.length * FRONTIER.usdPer1k) / 1000, sec: dx.length * FRONTIER.secEach } };
  }

  /* Plays a run event by event. Returns {stop()}. Gaps are compressed so a run takes about 12 seconds. */
  function play(runObj, opts) {
    const o = opts || {}; let i = 0, dead = false, timer = null;
    const still = o.instant || (root.matchMedia && root.matchMedia("(prefers-reduced-motion: reduce)").matches);
    (function next() {
      if (dead) return;
      if (i >= runObj.events.length) { if (o.onDone) o.onDone(runObj); return; }
      const e = runObj.events[i++]; if (o.onEvent) o.onEvent(e, i, runObj.events.length);
      if (still) next(); else timer = setTimeout(next, clamp(e.ms * 0.3, 30, 380) / (o.speed || 1));
    })();
    return { stop() { dead = true; clearTimeout(timer); } };
  }

  // ---------- formatting ----------
  const fmt = {
    p: (x) => x.toFixed(2), pct: (x) => Math.round(x * 100) + "%", ms: (x) => (x >= 1000 ? (x / 1000).toFixed(1) + " s" : Math.round(x) + " ms"),
    usd: (x) => "$" + x.toFixed(x < 0.001 ? 6 : x < 0.1 ? 4 : 2), d: (x) => (x >= 0 ? "+" : "-") + Math.abs(x).toFixed(2), int: (x) => Math.round(x).toLocaleString("en-US"), t: (x) => "+" + (x / 1000).toFixed(2) + "s",
  };

  /* How the legal team was chosen, from the `role` event result. Empty until the intake has run. */
  function roleSource(r) {
    if (!r || !r.source) return "";
    const d = r.detected, p = d ? fmt.p(d.p_area) : "";
    if (!d) return r.source === "override" ? "set by the asker" : "set by the case";
    if (r.source === "override") return `set by the asker; Drex picked ${d.label} ${p}`;
    if (d.id !== r.id) return `set by the case; Drex picked ${d.label} ${p}`;
    return `picked by Drex ${p}`;
  }

  // ---------- DOM helpers (browser only) ----------
  function el(tag, cls, html) { const n = document.createElement(tag); if (cls) n.className = cls; if (html != null) n.innerHTML = html; return n; }
  const esc = (s) => String(s).replace(/[&<>"]/g, (ch) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[ch]));
  function pbar(p, small) { const n = el("div", "pbar" + (small ? " sm" : ""), '<div class="track"><div class="fill"></div></div><div class="val">–</div>'); if (p != null) setPbar(n, p); return n; }
  function setPbar(n, p) { n.classList.toggle("yes", p >= 0.5); n.querySelector(".fill").style.width = p * 100 + "%"; n.querySelector(".val").textContent = fmt.p(p); }
  function seat(j) { const n = el("div", "seat", `<div class="id">${j.id}</div><div class="col"><i></i></div><div class="p">–</div><div class="who">${esc(j.name)}</div>`); n.dataset.id = j.id; n.title = j.note; return n; }
  function setSeat(n, p) { n.classList.toggle("yes", p >= 0.5); n.classList.toggle("undecided", Math.abs(p - 0.5) < 0.1); n.querySelector(".col i").style.height = p * 100 + "%"; n.querySelector(".p").textContent = fmt.p(p); }
  /* Segmented bar for a choice or score distribution; the top option is brightest. */
  function distBar(d, labels) {
    const top = d.indexOf(Math.max(...d)); const n = el("span"); n.style.cssText = "display:inline-flex;width:120px;height:8px;border-radius:2px;overflow:hidden;background:var(--raised);vertical-align:middle";
    d.forEach((v, i) => { const s = el("i"); s.style.cssText = `width:${v * 100}%;background:${i === top ? "var(--yes)" : "var(--dim)"};opacity:${i === top ? 1 : 0.35 + 0.4 * v};border-right:1px solid var(--surface)`; if (labels) s.title = `${labels[i]}: ${fmt.p(v)}`; n.appendChild(s); });
    return n;
  }
  function answer(e) { // short text of what the model returned
    if (e.type === "noul") return "p(yes) " + fmt.p(e.p);
    const top = e.dist.indexOf(Math.max(...e.dist)); return (e.options || e.levels)[top] + " " + fmt.p(e.dist[top]);
  }
  /* One trace row. Columns: #, time, source, agent, decision id, type, question, result, confidence, ms, tokens, cost, route. */
  function traceRow(e) {
    const tr = el("tr", "new"); const isD = e.src === "drex";
    const cells = [String(e.i + 1).padStart(3, "0"), fmt.t(e.t), `<span class="src ${e.src}">${e.src}</span>`, esc(e.agent || e.speaker || ""), esc(e.id), isD ? e.type : "", esc(e.q || e.text || ""), "",
      isD ? fmt.p(e.conf) : "", fmt.ms(e.ms), isD ? fmt.int(e.tok) : "", isD ? fmt.usd(e.usd) : "", isD ? (e.route === "person" ? '<span class="chip warn">needs a person</span>' : '<span class="chip">auto</span>') : ""];
    cells.forEach((h, k) => { const td = el("td", k === 6 ? "q" : k >= 8 && k <= 11 ? "num" : "", h); tr.appendChild(td); });
    if (isD) { const td = tr.children[7]; if (e.type === "noul") td.appendChild(pbar(e.p, true)); else { td.appendChild(distBar(e.dist, e.options || e.levels)); td.appendChild(el("span", "muted", " " + esc(answer(e)))); } td.style.minWidth = "190px"; }
    tr.dataset.i = e.i; return tr;
  }
  const TRACE_HEAD = ["#", "Time", "Source", "Agent", "Decision", "Type", "Question or step", "Result", "Conf.", "Latency", "Tokens", "Cost", "Route"];

  const PAGES = [["01-slack-intake.html", "Intake", "Slack"], ["02-live-courtroom.html", "Courtroom", "live"], ["03-trace-ledger.html", "Trace ledger", "audit"]];
  function getCase() { try { const h = new URLSearchParams(location.hash.slice(1)).get("case") || localStorage.getItem("tad.case"); if (CASES.some((c) => c.id === h)) return h; } catch (e) { /* storage blocked */ } return CASES[0].id; }
  function setCase(id) { try { localStorage.setItem("tad.case", id); } catch (e) { /* storage blocked */ } history.replaceState(null, "", "#case=" + id); }

  /* Builds rail + top bar inside #app. Returns {content, title(text, sub), stats(list of [label, value]), live(on), onCase(fn), caseId()}. */
  function shell(active) {
    const app = document.getElementById("app"); app.className = "app"; app.innerHTML = "";
    const css = document.createElement("style"); // the rail holds 7 cases: two columns when the rail runs across the top
    css.textContent = "@media (max-width: 900px) { .caselist { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 2px 8px; } .casebox { flex: 1 1 320px; } }";
    document.head.appendChild(css);
    const rail = el("aside", "rail"); app.appendChild(rail);
    rail.appendChild(el("div", "brand", "Twelve Angry Drexes<small>Mock jury for a legal question</small>"));
    const nav = el("nav", "nav"); nav.setAttribute("aria-label", "Screens"); rail.appendChild(nav);
    PAGES.forEach(([href, name, tag]) => { const a = el("a", "", `${name}<span>${tag}</span>`); a.href = href + location.hash; if (href === active) a.setAttribute("aria-current", "page"); nav.appendChild(a); });
    const box = el("div", "casebox"); box.appendChild(el("div", "label", "Simulated cases")); const list = el("div", "caselist"); box.appendChild(list); rail.appendChild(box);
    rail.appendChild(el("div", "rail-foot", "Statutes from LawWiki (local copy of its own snapshots). Decisions by Drex, documents by NDI, agents on Intern, speeches by Qwen on DeepInfra, web pages by Keenable.<br><br>Legal information, not legal advice. A simulation, not a prediction."));
    const main = el("div", "main"); app.appendChild(main);
    const top = el("header", "topbar"); main.appendChild(top);
    const head = el("div"); const h1 = el("h1"); const sub = el("div", "sub"); head.append(h1, sub);
    const live = el("span", "live", "<i></i><span>idle</span>"); const grow = el("div", "grow"); const stats = el("div", "stats");
    const theme = el("button", "btn", "Light"); theme.type = "button";
    theme.onclick = () => { const l = document.documentElement.dataset.theme === "light"; document.documentElement.dataset.theme = l ? "dark" : "light"; theme.textContent = l ? "Light" : "Dark"; };
    top.append(head, live, grow, stats, theme);
    const content = el("div", "content"); main.appendChild(content);
    main.appendChild(el("div", "foot", "Every number on this screen is simulated. Cost is input tokens at $0.12 per million (Drex Max console price). The frontier chat model comparison is the published $3.33 per 1,000 decisions (Drex technical report, Table 8)."));
    document.body.appendChild(el("div", "sim-badge", ""));
    let cb = null, cur = getCase();
    function paint() { list.querySelectorAll("button").forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.id === cur))); nav.querySelectorAll("a").forEach((a, k) => { a.href = PAGES[k][0] + "#case=" + cur; }); }
    CASES.forEach((c) => { const b = el("button", "", `${esc(c.short)}<small>${c.asker.kind} · ${c.jurisdiction}</small>`); b.type = "button"; b.dataset.id = c.id; b.onclick = () => { cur = c.id; setCase(cur); paint(); if (cb) cb(cur); }; list.appendChild(b); });
    setCase(cur); paint();
    return {
      content, caseId: () => cur, onCase(fn) { cb = fn; },
      title(text, s) { h1.textContent = text; sub.textContent = s || ""; },
      live(on, text) { live.classList.toggle("on", !!on); live.lastChild.textContent = text || (on ? "live" : "idle"); },
      stats(rows) { stats.innerHTML = ""; rows.forEach(([k, v]) => stats.appendChild(el("div", "stat", `<b>${v}</b><span>${k}</span>`))); },
    };
  }

  root.TAD = { JURORS, CASES, PHASES, FRONTIER, USD_PER_M_INPUT, ROLES, roleSource, run, play, fmt, el, esc, pbar, setPbar, seat, setSeat, distBar, answer, traceRow, TRACE_HEAD, shell, getCase };
  if (typeof module !== "undefined") module.exports = root.TAD;
})();
