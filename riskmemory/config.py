"""Assumed operating constants.

Every number here is ASSUMED, not measured -- see section 6 of the problem
statement. They are gathered in one module so a real figure can replace an
assumed one without hunting through the codebase.
"""
from __future__ import annotations

# -- Section 6.1: assumed operating baseline -------------------------------
LIFETIME_APPLICATIONS = 4000
APPROVAL_RATE = 0.65
ACTIVE_MERCHANTS = 1900
CONFIRMED_BAD_RATE = 0.017          # of approvals
ANNUAL_VOLUME_USD = 150_000_000
HUMAN_TOUCH_RATE = 0.40
MEDIAN_DECISION_HOURS = 14
ANNUAL_ADMISSION_LOSS_USD = 350_000

# -- Section 6.2: the operating point --------------------------------------
COST_FALSE_APPROVE_USD = 25_000.0
COST_FALSE_DECLINE_USD = 4_000.0

#: Expected cost is equal when p*C_fa == (1-p)*C_fd, so the indifference
#: point is C_fd / (C_fa + C_fd). With the assumed costs that is ~0.1379.
DECLINE_THRESHOLD = COST_FALSE_DECLINE_USD / (
    COST_FALSE_APPROVE_USD + COST_FALSE_DECLINE_USD
)
#: Below this we auto-approve without a human; between the two a human decides.
AUTO_APPROVE_THRESHOLD = 0.04

# -- Card network monitoring (Visa VAMP, effective 1 Apr 2026) -------------
VAMP_ACQUIRER_ABOVE_STANDARD = 0.005   # 0.50%
VAMP_ACQUIRER_EXCESSIVE = 0.007        # 0.70%
VAMP_MERCHANT_EXCESSIVE = 0.015        # 1.50%
VAMP_MIN_EVENTS = 1500
ASSUMED_PORTFOLIO_VAMP = 0.0040        # 0.40%, thin headroom

# -- Section 2.1: prepaid / insolvency exposure ----------------------------
PREPAID_EXPOSURE_RATIO = 0.15
PREPAID_EXPOSURE_USD = ANNUAL_VOLUME_USD * PREPAID_EXPOSURE_RATIO  # ~$22.5M

# -- Assumed loss distribution by category (section 6.3, question 11) ------
LOSS_DISTRIBUTION = {
    "deceptive_billing": 0.35,
    "product_drift": 0.20,
    "undisclosed_illegality": 0.15,
    "insolvency": 0.12,
    "recidivist_ring": 0.08,
    "transaction_laundering": 0.05,
    "account_takeover": 0.03,
    "card_testing": 0.02,
}

# -- Risk postures (section 2.1) -------------------------------------------
POSTURES = {
    "deceiving": "Deceiving us",
    "drifting": "Drifting from us",
    "failing": "Failing",
    "attacked": "Being attacked",
    "memory": "Memory",
}

CATEGORY_POSTURE = {
    "undisclosed_illegality": "deceiving",
    "recidivist_ring": "deceiving",
    "transaction_laundering": "deceiving",
    "merchant_identity_fraud": "deceiving",
    "bust_out": "deceiving",
    "product_drift": "drifting",
    "deceptive_billing": "drifting",
    "ai_abuse_controls": "drifting",
    "insolvency": "failing",
    "concentration": "failing",
    "account_takeover": "attacked",
    "card_testing": "attacked",
}

#: Postures that say something about the *merchant's own conduct*. Precedent
#: retrieval matches on product and pitch, so it may only reason from these.
#: A merchant whose checkout was used for card testing, or whose account was
#: taken over, tells us nothing about the integrity of a merchant selling a
#: similar product -- they were the victim, not the actor.
PRECEDENT_RELEVANT_POSTURES = {"deceiving", "drifting"}

#: Laplace-style smoothing for precedent. Without it, one bad match in six
#: swings the likelihood ratio by an order of magnitude.
PRECEDENT_SMOOTHING = 4.0

#: Cosine floor for a retrieved neighbour to count as precedent. Measured, not
#: guessed: in this corpus every neighbour that genuinely went bad matches at
#: 0.24 or above, while the noise floor of shared boilerplate vocabulary sits
#: between 0.09 and 0.18. See App._precedent.
PRECEDENT_MIN_SIMILARITY = 0.20

# -- Dodo merchant acceptance policy taxonomy ------------------------------
POLICY_ACCEPTED = {"saas", "digital_goods", "templates_plugins_apps", "ai_product"}
POLICY_RESTRICTED = {
    "ai_content_generation", "marketing_outreach", "resume_hiring_exam",
    "spiritual_astrology", "audio_music_chatbot", "ebooks_publications",
    "productized_services",
}
POLICY_PROHIBITED = {
    "adult_nsfw", "manual_digital_services", "low_value_digital",
    "physical_goods", "in_person_services", "unlicensed_financial",
    "company_registration", "licensed_professional", "travel_booking",
    "gambling", "crypto_nft", "gaming_virtual_goods", "piracy_ip_violation",
    "streaming_iptv", "hosting_vpn_telecom", "surveillance_tools",
    "spam_scraping", "proxy_anti_tos", "cheating_tools", "weapons_violence",
    "health_diagnostics", "miracle_claims", "dating_social_matching",
    "religious_guidance", "donations_no_deliverable", "marketplace_resale",
}

# -- Dodo Payments platform facts (dodopayments.com, 20 Aug 2026) ----------
PLATFORM_NAME = "Dodo Payments"
PLATFORM_TAGLINE = "Billing and payments for AI-first companies"
COUNTRIES_SUPPORTED = 220
BUILDERS_ON_PLATFORM = 50_000
BUSINESSES_ON_PLATFORM = 25_000

#: The product surface Dodo actually sells. Merchants use these, so risk
#: signals differ by product: usage-based billing produces different dispute
#: patterns from a one-time licence.
PLATFORM_FEATURES = [
    "Merchant of Record", "Subscriptions", "Credit-Based Billing",
    "Usage-Based Billing", "Adaptive Currency", "Local Payment Methods",
    "Reporting & Analytics", "In-App Purchases", "Fraud Protection",
    "No Code Checkout", "Purchasing Power Parity", "Discount Codes",
    "Multi Brand Support", "Storefront", "Affiliate Program",
    "Digital Product Delivery", "License Keys",
]

#: Dodo's own brand palette, from dodopayments.com/brand. The UI reads these
#: so the console and the marketing site cannot drift apart.
BRAND = {
    "lime":   "#C6FE1E",   # signature
    "forest": "#004F32",   # dark ground
    "green":  "#00D87D",
    "blue":   "#1264FF",
    "pink":   "#EE46BC",
    "purple": "#7A5AF8",
    "yellow": "#FFD84B",
    "orange": "#FF8B37",
    "red":    "#E83439",
    "ink":    "#00160D",   # body text -- a green-black, not a grey
    "muted":  "#666666",
    "rule":   "#E7E7E7",
}

DEMO_TODAY = "2026-08-20"
