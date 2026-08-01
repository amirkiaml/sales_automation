"""
normalize_phone: the E.164 coercion every inbound and outbound number
passes through.

Regression origin: the original implementation treated ANY leading '+' as
proof the number was already complete E.164 and returned it untouched. A
Toronto number typed as "+416 822 6186" was stored as "+4168226186",
which Twilio rejects with error 21211 because it reads the leading 4 as a
country code. It surfaced as a 500 at send time; the real damage was
silent corruption at CSV import.

No API key, no database, no network. Runs in milliseconds.
"""
import pytest

from app.phone import InvalidPhoneError, normalize_phone


class TestNorthAmericanNumbers:
    @pytest.mark.parametrize(
        "raw",
        [
            "+416 822 6186",     # THE REGRESSION: plus, but no country code
            "416 822 6186",
            "(416) 822-6186",
            "4168226186",
            "416-822-6186",
            "1-416-822-6186",
            "+1 416 822 6186",
            "+14168226186",
            "  416.822.6186  ",
        ],
    )
    def test_all_spellings_normalize_identically(self, raw):
        assert normalize_phone(raw) == "+14168226186"


class TestInternationalNumbers:
    def test_uk_number_keeps_its_country_code(self):
        # Must NOT become +1442... - the fix decides by digit count, and a
        # 12-digit number is assumed to already carry a country code.
        assert normalize_phone("+44 20 7946 0958") == "+442079460958"

    def test_australian_number(self):
        assert normalize_phone("+61 2 5550 1234") == "+61255501234"


class TestRejection:
    @pytest.mark.parametrize(
        "raw", ["", "   ", "12", "abc", "555-1234", "0" * 20, None]
    )
    def test_unusable_input_raises_rather_than_storing_garbage(self, raw):
        # Raising matters more than the specific message: the old version
        # silently stored whatever it produced, so a malformed CSV row
        # became a broken prospect that only failed weeks later at send.
        with pytest.raises(InvalidPhoneError):
            normalize_phone(raw)


class TestIdempotence:
    @pytest.mark.parametrize(
        "raw", ["+416 822 6186", "4168226186", "+44 20 7946 0958"]
    )
    def test_normalizing_twice_changes_nothing(self, raw):
        # Numbers are re-normalized on edit, so a second pass must be a
        # no-op. A version that prepended +1 each time would pass the
        # single-pass tests above and still corrupt on the second edit.
        once = normalize_phone(raw)
        assert normalize_phone(once) == once
