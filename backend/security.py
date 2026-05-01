"""Security: sanitize sensitive data before sending to cloud LLM."""
import re

# Patterns to redact
PATTERNS = [
    # API keys
    (re.compile(r'(api[_-]?key|apikey|secret|token|password|passwd|pwd)\s*[=:]\s*["\']?([^\s"\'&,\}]{8,})["\']?', re.IGNORECASE), r'\1=***REDACTED***'),
    # Bearer tokens
    (re.compile(r'(Bearer|Basic|Token)\s+[A-Za-z0-9\-._~+/]+=*', re.IGNORECASE), r'\1 ***REDACTED***'),
    # Internal IPs
    (re.compile(r'\b(10\.\d{1,3}\.\d{1,3}\.\d{1,3})\b'), '***.***.***.***'),
    (re.compile(r'\b(172\.(1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3})\b'), '***.***.***.***'),
    (re.compile(r'\b(192\.168\.\d{1,3}\.\d{1,3})\b'), '***.***.***.***'),
    # Email addresses
    (re.compile(r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b'), '***@***.***'),
    # MAC addresses
    (re.compile(r'\b([0-9A-Fa-f]{2}[:-]){5}([0-9A-Fa-f]{2})\b'), '**:**:**:**:**:**'),
]

def sanitize_for_cloud(text: str) -> str:
    """Remove sensitive information before sending to cloud LLM."""
    for pattern, replacement in PATTERNS:
        text = pattern.sub(replacement, text)
    return text
