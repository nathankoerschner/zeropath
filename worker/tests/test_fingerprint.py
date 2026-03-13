"""Tests for finding fingerprint generation."""

from app.services.fingerprint import generate_fingerprint


class TestGenerateFingerprint:
    def test_deterministic(self):
        fp1 = generate_fingerprint("app/views.py", "SQL Injection")
        fp2 = generate_fingerprint("app/views.py", "SQL Injection")
        assert fp1 == fp2

    def test_case_insensitive(self):
        fp1 = generate_fingerprint("app/views.py", "SQL Injection")
        fp2 = generate_fingerprint("app/views.py", "sql injection")
        assert fp1 == fp2

    def test_whitespace_insensitive(self):
        fp1 = generate_fingerprint("  app/views.py  ", "  SQL Injection  ")
        fp2 = generate_fingerprint("app/views.py", "SQL Injection")
        assert fp1 == fp2

    def test_different_vuln_type_different_fingerprint(self):
        fp1 = generate_fingerprint("app/views.py", "SQL Injection")
        fp2 = generate_fingerprint("app/views.py", "XSS")
        assert fp1 != fp2

    def test_different_file_different_fingerprint(self):
        fp1 = generate_fingerprint("app/views.py", "SQL Injection")
        fp2 = generate_fingerprint("app/models.py", "SQL Injection")
        assert fp1 != fp2

    def test_returns_hex_string(self):
        fp = generate_fingerprint("foo.py", "bar")
        assert isinstance(fp, str)
        assert len(fp) == 64  # SHA-256 hex digest
        int(fp, 16)  # Should not raise
