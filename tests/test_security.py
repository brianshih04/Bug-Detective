"""Tests for backend/security.py — sanitize_for_cloud()."""

from backend.security import sanitize_for_cloud


class TestSanitizeForCloud:
    """Verify sensitive data is redacted before sending to cloud LLM."""

    # --- API keys ---
    def test_api_key_equals(self):
        assert sanitize_for_cloud("api_key=sk-12345678abcdef") == "api_key=***REDACTED***"

    def test_api_key_colon_with_quotes(self):
        """Colon separator + quotes — regex still matches long values inside quotes."""
        result = sanitize_for_cloud('apikey: "sk-12345678abcdef"')
        assert result == "apikey=***REDACTED***"

    def test_api_key_colon_no_quotes(self):
        """Colon without quotes matches the regex (replacement uses = sign)."""
        assert sanitize_for_cloud("apikey:sk-12345678abcdef") == "apikey=***REDACTED***"

    def test_secret_equals(self):
        assert sanitize_for_cloud("secret=mysecretvalue") == "secret=***REDACTED***"

    def test_password_equals(self):
        assert sanitize_for_cloud("password=hunter22longer") == "password=***REDACTED***"

    def test_short_password_not_redacted(self):
        """Values shorter than 8 chars should not be redacted."""
        assert "REDACTED" not in sanitize_for_cloud("password=hunter2")

    def test_short_value_not_redacted(self):
        """Values shorter than 8 chars should not be redacted."""
        assert "REDACTED" not in sanitize_for_cloud("api_key=abc")

    def test_bearer_token(self):
        assert (
            sanitize_for_cloud("Authorization: Bearer eyJhbGciOiJIUzI1NiJ9.abc")
            == "Authorization: Bearer ***REDACTED***"
        )

    def test_basic_token(self):
        assert sanitize_for_cloud("Authorization: Basic dXNlcjpwYXNz") == "Authorization: Basic ***REDACTED***"

    # --- Internal IPs ---
    def test_10_x_x_x(self):
        assert sanitize_for_cloud("server at 10.0.1.23 is down") == "server at ***.***.***.*** is down"

    def test_172_16_x_x(self):
        assert sanitize_for_cloud("host 172.16.0.1") == "host ***.***.***.***"

    def test_172_31_x_x(self):
        assert sanitize_for_cloud("host 172.31.255.255") == "host ***.***.***.***"

    def test_192_168_x_x(self):
        assert sanitize_for_cloud("connect 192.168.0.134") == "connect ***.***.***.***"

    def test_172_15_x_not_redacted(self):
        """172.15.x.x is NOT private — should not be redacted."""
        assert "REDACTED" not in sanitize_for_cloud("host 172.15.0.1")
        assert "***.***" not in sanitize_for_cloud("host 172.15.0.1")

    def test_public_ip_not_redacted(self):
        assert "REDACTED" not in sanitize_for_cloud("8.8.8.8")
        assert "***.***" not in sanitize_for_cloud("8.8.8.8")

    # --- Emails ---
    def test_email_redacted(self):
        assert sanitize_for_cloud("user@example.com") == "***@***.***"

    # --- MAC addresses ---
    def test_mac_colon(self):
        assert sanitize_for_cloud("AA:BB:CC:DD:EE:FF") == "**:**:**:**:**:**"

    def test_mac_dash(self):
        assert sanitize_for_cloud("AA-BB-CC-DD-EE-FF") == "**:**:**:**:**:**"

    # --- Multiple patterns ---
    def test_multiple_patterns(self):
        text = "api_key=sk-abcdefgh connect 192.168.1.1 email@test.com"
        result = sanitize_for_cloud(text)
        assert "REDACTED" in result
        assert "***.***" in result
        assert "***@***" in result
        assert "sk-abcdefgh" not in result
        assert "192.168.1.1" not in result

    # --- Edge cases ---
    def test_empty_string(self):
        assert sanitize_for_cloud("") == ""

    def test_no_sensitive_data(self):
        text = "Hello world, nothing to see here"
        assert sanitize_for_cloud(text) == text
