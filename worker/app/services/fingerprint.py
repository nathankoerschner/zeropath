"""Finding fingerprint generation for stable cross-scan identity matching.

A fingerprint is a stable hash derived from the normalised file path and
vulnerability type.  This gives us a "same logical issue" identity that
survives line-number drift across commits while still distinguishing
different vulnerability types in the same file.
"""

import hashlib


def generate_fingerprint(file_path: str, vulnerability_type: str) -> str:
    """Return a hex digest fingerprint for a (file_path, vulnerability_type) pair.

    Both inputs are lowered and stripped so cosmetic differences in LLM output
    (e.g. "SQL Injection" vs "sql injection") do not create duplicate identities.
    """
    normalised = f"{file_path.strip().lower()}::{vulnerability_type.strip().lower()}"
    return hashlib.sha256(normalised.encode("utf-8")).hexdigest()
