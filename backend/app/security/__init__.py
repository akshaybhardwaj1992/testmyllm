"""AI security testing (decision D29): attack library, detectors, target calls and scan execution.

Built-in attack goals are deliberately harmless: an attack "succeeds" when the target outputs a code
word it was told not to, leaks a planted canary, or reveals another test identity's data. That proves the
vulnerability without generating harmful content.
"""

CATEGORIES: dict[str, dict[str, str]] = {
    "prompt_injection": {"label": "Prompt injection", "owasp": "LLM01", "group": "injection"},
    "jailbreak": {"label": "Jailbreak", "owasp": "LLM01", "group": "injection"},
    "indirect_injection": {"label": "Indirect injection", "owasp": "LLM01", "group": "injection"},
    "system_prompt_leakage": {"label": "System prompt leakage", "owasp": "LLM07", "group": "disclosure"},
    "sensitive_disclosure": {"label": "Sensitive information disclosure", "owasp": "LLM02", "group": "disclosure"},
    "cross_user_leakage": {"label": "Cross-user data leakage", "owasp": "LLM02", "group": "disclosure"},
    "excessive_agency": {"label": "Excessive agency", "owasp": "LLM06", "group": "agency"},
}
SEVERITIES = ("critical", "high", "medium", "low")
DETECTORS = ("marker", "canary", "pattern", "cross_user", "agency")
TECHNIQUES = (
    "direct", "role_play", "encoding", "multilingual", "multi_turn", "indirect_document", "extraction", "social",
)
OWASP_VERSION = "OWASP Top 10 for LLM Applications 2025"
