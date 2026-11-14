"""Simulated data for ResolveAI: customer/order records, service policy,
and benchmark test cases. No real systems are touched."""

# ---------------------------------------------------------------- customers
CUSTOMERS = {
    "C-1001": {
        "id": "C-1001",
        "name": "Aisha Khan",
        "tier": "gold",
        "lifetime_value": 2450,
        "open_disputes": 0,
        "notes": "Loyal customer, 14 orders, no complaints on record.",
    },
    "C-1002": {
        "id": "C-1002",
        "name": "Ben Ortiz",
        "tier": "standard",
        "lifetime_value": 120,
        "open_disputes": 3,
        "notes": "Repeat complainant; prior refunds flagged for review.",
    },
    "C-1003": {
        "id": "C-1003",
        "name": "Chloe Dupont",
        "tier": "standard",
        "lifetime_value": 340,
        "open_disputes": 1,
        "notes": "One open dispute from a previous order.",
    },
}

# ------------------------------------------------------------------- orders
# days_since_delivery is simulated relative to "today".
ORDERS = {
    "O-5001": {
        "id": "O-5001",
        "customer_id": "C-1001",
        "items": ["Espresso Pro Grinder"],
        "total": 249.00,
        "paid": True,
        "days_since_delivery": 12,
        "return_window_days": 30,
        "delivery_status": "delivered",
    },
    "O-5002": {
        "id": "O-5002",
        "customer_id": "C-1002",
        "items": ["Desk Lamp"],
        "total": 45.00,
        "paid": True,
        "days_since_delivery": 45,
        "return_window_days": 30,
        "delivery_status": "delivered",
    },
    "O-5003": {
        "id": "O-5003",
        "customer_id": "C-1003",
        "items": ["Standing Desk (large)"],
        "total": 1290.00,
        "paid": True,
        "days_since_delivery": 4,
        "return_window_days": 30,
        "delivery_status": "delivered",
    },
    "O-5004": {
        "id": "O-5004",
        "customer_id": "C-1001",
        "items": ["Travel Mug", "Travel Mug"],
        "total": 58.00,
        "paid": True,
        "days_since_delivery": 3,
        "return_window_days": 30,
        "delivery_status": "in_transit",
    },
}

# ------------------------------------------------------------------- policy
# Ordered rules: the first rule whose conditions all match wins.
# Drex must return one of the four allowed actions and should follow
# this policy when it is unambiguous.
POLICY = {
    "version": "1.0",
    "allowed_actions": ["refund", "replacement", "store_credit", "human_review"],
    "rules": [
        {
            "id": "R1",
            "name": "High-value order",
            "conditions": {"order_total_gte": 1000},
            "action": "human_review",
            "note": "Orders of $1000+ always require a human supervisor.",
        },
        {
            "id": "R2",
            "name": "Repeat complainant",
            "conditions": {"customer_open_disputes_gte": 2},
            "action": "human_review",
            "note": "Customers with 2+ open disputes go to human review.",
        },
        {
            "id": "R3",
            "name": "Damaged in window",
            "conditions": {"category": ["damaged", "defective"], "within_return_window": True},
            "action": "replacement",
            "note": "Damaged/defective items inside the return window get a replacement.",
        },
        {
            "id": "R4",
            "name": "Late delivery",
            "conditions": {"category": ["delayed", "missing"], "delivery_status": "in_transit"},
            "action": "store_credit",
            "note": "Still-in-transit delays/missing items get store credit first.",
        },
        {
            "id": "R5",
            "name": "Refund in window",
            "conditions": {"within_return_window": True, "paid": True},
            "action": "refund",
            "note": "Paid orders inside the return window get a full refund.",
        },
        {
            "id": "R6",
            "name": "Outside window",
            "conditions": {"within_return_window": False},
            "action": "store_credit",
            "note": "Outside the return window, offer 50% store credit.",
        },
        {
            "id": "R7",
            "name": "Fallback",
            "conditions": {},
            "action": "human_review",
            "note": "Anything ambiguous goes to human review.",
        },
    ],
}

# ------------------------------------------------------------- benchmarks
BENCHMARK_CASES = [
    {
        "id": "B1",
        "title": "Gold customer, damaged grinder, 12 days",
        "request": "The espresso grinder arrived with a cracked burr. I want it fixed or replaced.",
        "customer_id": "C-1001",
        "order_id": "O-5001",
        "expected_action": "replacement",
    },
    {
        "id": "B2",
        "title": "Repeat complainant, late window refund",
        "request": "I want a refund for the lamp, it never worked right.",
        "customer_id": "C-1002",
        "order_id": "O-5002",
        "expected_action": "human_review",
    },
    {
        "id": "B3",
        "title": "High-value standing desk, scratch on arrival",
        "request": "My standing desk arrived with a big scratch on the top. Please refund me.",
        "customer_id": "C-1003",
        "order_id": "O-5003",
        "expected_action": "human_review",
    },
    {
        "id": "B4",
        "title": "Order still in transit, customer worried it is lost",
        "request": "My mugs still have not arrived and tracking has not moved in two days.",
        "customer_id": "C-1001",
        "order_id": "O-5004",
        "expected_action": "store_credit",
    },
]
