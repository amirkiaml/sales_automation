import logging
from app.tools.twilio_sms import get_twilio_client
from app.db.client import list_prospects_needing_lookup
from twilio.base.exceptions import TwilioRestException


def lookup_line_type(phone: str) -> str | None:
    """Line type, or None if we could not get a real answer.

    None means "do not set line_type_checked_at" - the number stays in
    the queue for a later run.

    Blocked for Canadian numbers: Line Type Intelligence requires NPAC
    approval from the Canadian Local Number Portability Consortium, and
    without it every CA number returns error 60601. The delivery-status
    path in check_delivery.py is the workaround; this stays for US
    numbers and for if that approval comes through.
    """
    client = get_twilio_client()
    try:
        result = client.lookups.v2.phone_numbers(phone).fetch(
            fields="line_type_intelligence")
    except TwilioRestException as e:
        logging.error("Lookup failed for %s: %s", phone, e)
        return None

    if not result.valid:
        return "invalid"

    lti = result.line_type_intelligence or {}
    if lti.get("error_code"):
        # 60601 = no NPAC access for this country. A fact about the
        # account, not the number - storing it would mark the row checked
        # and permanently wrong.
        logging.error("Lookup error %s for %s", lti["error_code"], phone)
        return None

    return lti.get("type") or "unknown"