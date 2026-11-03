"""Mask anything that looks like a bank detail before an admin page shows stored JSON.

A second layer, not the control: tools run as the acting person, who cannot read bank numbers, so a stored tool result
should never hold one. This catches a future tool, or a model's free text, that copies one in anyway.
"""

import re

SENSITIVE_KEY = re.compile(r"account|iban|bank_?number|sort_?code|swift|bic", re.IGNORECASE)
# An IBAN-like run inside free text: the only bank-number format the generator produces. Long digit runs are not masked,
# because invoice and order numbers are long digit runs too, and masking them would hide what the admin is reviewing.
SENSITIVE_TEXT = re.compile(r"\b[A-Z]{2}\d{2}[A-Z0-9]{10,30}\b")


def mask_value(value: object) -> str:
    text = str(value)
    return "*" * max(len(text) - 4, 0) + text[-4:]


def _mask_text(text: str) -> str:
    return SENSITIVE_TEXT.sub(lambda match: mask_value(match.group()), text)


def mask(value: object, key: str = "") -> object:
    """Masks whole values under sensitive keys, and sensitive runs inside any other string."""
    if isinstance(value, dict):
        return {k: mask(v, k) for k, v in value.items()}
    if isinstance(value, list):
        return [mask(item, key) for item in value]
    if value is None or isinstance(value, bool):
        return value
    if SENSITIVE_KEY.search(key):
        return mask_value(value)
    if isinstance(value, str):
        return _mask_text(value)
    return value
