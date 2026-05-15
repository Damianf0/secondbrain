"""Normalización de teléfonos a E.164 (wrapper de `phonenumbers`)."""

import phonenumbers


def normalizar_telefono(raw: str | None, region_default: str = "AR") -> str | None:
    """`+54 9 223 559 4007` / `223 5594007` -> `+5492235594007`. None si no es válido."""
    if not raw:
        return None
    raw = str(raw).strip()
    if not raw:
        return None
    try:
        parsed = phonenumbers.parse(raw, None if raw.startswith("+") else region_default)
    except phonenumbers.NumberParseException:
        return None
    if not phonenumbers.is_valid_number(parsed):
        # toleramos números "posibles" pero no estrictamente válidos (algunos AR raros)
        if not phonenumbers.is_possible_number(parsed):
            return None
    return phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.E164)
