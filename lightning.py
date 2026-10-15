"""BOLT11 invoice amount validation."""

import re

BECH32_CHARSET = "qpzry9x8gf2tvdw0s3jn54khce6mua7l"



def buyer_invoice_amount_sats(invoice):
    """Return the BOLT11 amount in sats, or None for an invalid invoice."""
    invoice = invoice.strip()
    if invoice.lower().startswith("lightning:"):
        invoice = invoice[len("lightning:"):]

    if not invoice or (invoice.lower() != invoice and invoice.upper() != invoice):
        return None

    invoice = invoice.lower()
    separator = invoice.rfind("1")
    if separator < 1:
        return None

    hrp = invoice[:separator]
    data_part = invoice[separator + 1:]
    amount_match = re.fullmatch(r"ln(?:bcrt|bc|tb)(\d+)([munp]?)", hrp)
    # A BOLT11 payload has a 7-word timestamp, a 104-word signature,
    # and a 6-word Bech32 checksum, even when it has no tagged fields.
    if amount_match is None or len(data_part) < 117:
        return None

    try:
        data = [BECH32_CHARSET.index(character) for character in data_part]
    except ValueError:
        return None

    checksum_values = [ord(character) >> 5 for character in hrp]
    checksum_values += [0]
    checksum_values += [ord(character) & 31 for character in hrp]
    checksum_values += data
    checksum = 1
    for value in checksum_values:
        high = checksum >> 25
        checksum = ((checksum & 0x1FFFFFF) << 5) ^ value
        for index, generator in enumerate(
            (0x3B6A57B2, 0x26508E6D, 0x1EA119FA, 0x3D4233DD, 0x2A1462B3)
        ):
            if (high >> index) & 1:
                checksum ^= generator
    if checksum != 1:
        return None

    amount = int(amount_match.group(1))
    unit = amount_match.group(2)
    if unit == "":
        return amount * 100_000_000
    if unit == "m":
        return amount * 100_000
    if unit == "u":
        return amount * 100
    if unit == "n":
        return amount // 10 if amount % 10 == 0 else None
    return amount // 10_000 if amount % 10_000 == 0 else None

