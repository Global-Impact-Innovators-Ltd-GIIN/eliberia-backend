"""Document rules an institution sets for each file it asks for: accepted formats, size limit, guidance.

Kept dependency-free so both the models and the route layer can use it.
"""
FORMATS = ("pdf", "jpg", "png")
MAGIC = {"pdf": (b"%PDF",), "png": (b"\x89PNG\r\n\x1a\n",), "jpg": (b"\xff\xd8\xff",)}
EXT = {".pdf": "pdf", ".png": "png", ".jpg": "jpg", ".jpeg": "jpg"}
MAX_MB_CEILING = 8


def _defaults(name):
    photo = "photo" in name.lower()
    return {
        "formats": ["jpg", "png"] if photo else ["pdf", "jpg", "png"],
        "max_mb": 2 if photo else 5,
        "note": ("Recent colour photo, plain white background, face fully visible, no glasses or hat."
                 if photo else "Clear scan or photo of the full page. All four corners must be visible."),
    }


def normalize_doc_rules(items):
    """Accept legacy plain names or rule dicts; always return complete rule dicts."""
    out = []
    for it in items or []:
        if isinstance(it, str):
            it = {"name": it}
        name = str(it.get("name") or "").strip()[:80]
        if not name:
            continue
        d = _defaults(name)
        fmts = [f for f in (it.get("formats") or d["formats"]) if f in FORMATS] or d["formats"]
        try:
            max_mb = float(it.get("max_mb", d["max_mb"]))
        except (TypeError, ValueError):
            max_mb = d["max_mb"]
        out.append({"name": name, "formats": fmts, "max_mb": min(max(max_mb, 0.1), MAX_MB_CEILING),
                    "required": bool(it.get("required", True)),
                    "note": str(it.get("note", d["note"]) or "")[:240]})
    return out


def check_file(rule, filename, raw):
    """Return an error message if the file breaks the institution's rule, else None."""
    import os
    kind = EXT.get(os.path.splitext(filename or "")[1].lower())
    allowed = ", ".join(f.upper() for f in rule["formats"])
    if kind not in rule["formats"]:
        return f"'{rule['name']}' must be a {allowed} file."
    if not any(raw.startswith(m) for m in MAGIC[kind]):
        return f"This file is not a real {kind.upper()} file. Export or scan it again as {allowed}."
    if len(raw) > rule["max_mb"] * 1024 * 1024:
        return f"'{rule['name']}' must be {rule['max_mb']:g} MB or smaller (yours is {len(raw) / 1048576:.1f} MB)."
    if len(raw) < 8:
        return "The file is empty."
    return None
