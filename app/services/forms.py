"""Checks an applicant's answers against the questions the institution configured for a service."""
import re
from datetime import date

from . import identity

FIELD_TYPES = ("text", "date", "number", "select", "email", "phone", "country", "identity")
EMAIL = re.compile(r"[^@\s]+@[^@\s]+\.[A-Za-z]{2,}")
PHONE = re.compile(r"\+?\d{7,15}")


def identity_parts(field, data):
    """(country, doc_type, number) for an identity field, applying the field's fixed choices."""
    countries = field.get("countries") or []
    docs = field.get("doc_types") or list(identity.DOC_TYPES)
    country = countries[0] if len(countries) == 1 else str(data.get(field["name"] + "__country") or "")
    doc = docs[0] if len(docs) == 1 else str(data.get(field["name"] + "__doc") or "")
    return country, doc, data.get(field["name"])


def validate(service, data):
    """Return (clean_data, errors). clean_data only contains keys the form defines."""
    clean, errors = {}, []
    for f in service.form_fields or []:
        name, label, ftype = f["name"], f["label"], f.get("type", "text")
        raw = data.get(name)
        value = str(raw).strip() if raw is not None else ""
        if ftype == "identity":
            country, doc, number = identity_parts(f, data)
            if not str(number or "").strip() and not f.get("required"):
                continue
            if f.get("countries") and country not in f["countries"]:
                errors.append(f"{label}: this service only accepts documents from "
                              + ", ".join(identity.COUNTRIES.get(c, c) for c in f["countries"]) + ".")
                continue
            if doc and doc not in (f.get("doc_types") or identity.DOC_TYPES):
                errors.append(f"{label}: this service does not accept a {doc.lower()}.")
                continue
            number, problem = identity.validate(country, doc, number)
            if problem:
                errors.append(f"{label}: {problem}")
                continue
            clean[name], clean[name + "__country"], clean[name + "__doc"] = number, country, doc
            continue
        if not value:
            if f.get("required"):
                errors.append(f"{label} is required.")
            continue
        max_len = int(f.get("max_length") or 300)
        if len(value) > max_len:
            errors.append(f"{label} must be {max_len} characters or fewer.")
        elif ftype == "number":
            try:
                float(value)
            except ValueError:
                errors.append(f"{label} must be a number.")
        elif ftype == "date":
            try:
                date.fromisoformat(value)
            except ValueError:
                errors.append(f"{label} must be a valid date.")
        elif ftype == "select" and value not in (f.get("options") or []):
            errors.append(f"{label}: choose one of the listed options.")
        elif ftype == "email" and not EMAIL.fullmatch(value):
            errors.append(f"{label} must be a valid email address.")
        elif ftype == "phone" and not PHONE.fullmatch(re.sub(r"[\s\-()]", "", value)):
            errors.append(f"{label} must be a phone number with 7 to 15 digits.")
        elif ftype == "country" and value not in identity.COUNTRIES:
            errors.append(f"{label}: choose a country from the list.")
        clean[name] = value
    return clean, errors
