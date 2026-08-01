"""
Phone number normalization. Deliberately dependency-free.

Split out of app/db/import_csv.py, which imports app/db/client.py, which
imports the Supabase driver - so testing this regex used to require a
database client and live credentials. A pure function guarded by the
fast test tier should import nothing.
"""
import re


class InvalidPhoneError(ValueError):
    """Raised when a number can't be coerced into plausible E.164."""


def normalize_phone(raw: str) -> str:
    """'+1 647-251-8320' -> '+16472518320' (E.164, what Twilio expects).

    The previous version treated ANY leading '+' as proof the number was
    already complete E.164 and returned it untouched. So '+416 822 6186' -
    a NANP number typed with a plus but no country code - became
    '+4168226186', which Twilio rejects with error 21211 because it reads
    the leading 4 as a country code. Silent corruption on CSV import, and
    a 500 at send time.

    Now: strip to digits, then decide by shape rather than by whether a
    '+' happened to be present.
      10 digits            -> NANP, prepend +1
      11 digits w/ lead 1  -> NANP, prepend +
      8-15 digits          -> assume already has a country code
      anything else        -> InvalidPhoneError

    The floor is 8, not 7. A bare 7-digit string is a local NANP number
    with the area code missing ("555-1234"), not a short international
    number - the shortest real E.164 numbers are about 8 digits. At 7 the
    old code produced "+5551234", a plausible-looking value Twilio
    rejects. Found by a test case, not by reading the code.
    """
    if raw is None:
        raise InvalidPhoneError("empty")

    digits = re.sub(r"\D", "", str(raw))
    if not digits:
        raise InvalidPhoneError(str(raw))

    if len(digits) == 10:
        return "+1" + digits
    if len(digits) == 11 and digits.startswith("1"):
        return "+" + digits
    if 8 <= len(digits) <= 15:
        return "+" + digits

    raise InvalidPhoneError(str(raw))
