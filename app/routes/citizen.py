"""Citizen conveniences: document locker, family members, appointment slots, delivery choice."""
import hashlib
import io
import os
import uuid
from datetime import date, datetime, time, timedelta

from flask import Blueprint, current_app, g, jsonify, request, send_file

from ..extensions import db
from ..models import Appointment, Application, ApplicationEvent, Document, FamilyMember, LockerItem, now
from ..rules import EXT, MAGIC, check_file, normalize_doc_rules
from ..services import storage, vault
from ..utils import audit, auth_required, err, notify, rate_limit
from .applications import _own
from .catalog import CENTERS

bp = Blueprint("citizen", __name__, url_prefix="/api")

LOCKER_LIMIT = 20
LOCKER_MAX_MB = 8


# ============================ document locker ============================
def save_locker_copy(user_id, label, filename, raw):
    """Store an already-validated file in the user's locker. Returns the item, or an error string."""
    if LockerItem.query.filter_by(user_id=user_id).count() >= LOCKER_LIMIT:
        return f"Your locker is full ({LOCKER_LIMIT} documents). Remove one first."
    kind = EXT.get(os.path.splitext(filename or "")[1].lower())
    stored = f"locker_{uuid.uuid4().hex}"
    storage.put(stored, vault.encrypt_bytes(raw))
    item = LockerItem(user_id=user_id, label=label[:80], filename=os.path.basename(filename)[:200], kind=kind,
                      size=len(raw), stored_name=stored, sha256=hashlib.sha256(raw).hexdigest())
    db.session.add(item)
    return item


@bp.get("/locker")
@auth_required("citizen")
def locker_list():
    rows = LockerItem.query.filter_by(user_id=g.user.id).order_by(LockerItem.id.desc()).all()
    return jsonify([x.to_dict() for x in rows])


@bp.post("/locker")
@auth_required("citizen")
@rate_limit("locker_upload", 40, 3600)
def locker_upload():
    f = request.files.get("file")
    label = (request.form.get("label") or "").strip()
    if not f or not f.filename:
        return err("Choose a file to upload")
    if len(label) < 2:
        return err("Give the document a name, e.g. Birth certificate")
    raw = f.read()
    rule = {"name": label, "formats": ["pdf", "jpg", "png"], "max_mb": LOCKER_MAX_MB}
    problem = check_file(rule, f.filename, raw)
    if problem:
        return err(problem)
    item = save_locker_copy(g.user.id, label, f.filename, raw)
    if isinstance(item, str):
        return err(item, 409)
    audit("locker_upload", label)
    db.session.commit()
    return jsonify(item.to_dict()), 201


@bp.delete("/locker/<int:item_id>")
@auth_required("citizen")
def locker_delete(item_id):
    item = LockerItem.query.filter_by(id=item_id, user_id=g.user.id).first()
    if not item:
        return err("Document not found", 404)
    storage.delete(item.stored_name)
    db.session.delete(item)
    audit("locker_delete", item.label)
    db.session.commit()
    return jsonify({"ok": True})


@bp.get("/locker/<int:item_id>/file")
@auth_required("citizen")
def locker_file(item_id):
    item = LockerItem.query.filter_by(id=item_id, user_id=g.user.id).first()
    blob = storage.get(item.stored_name) if item else None
    if not item or blob is None:
        return err("Document not found", 404)
    return send_file(io.BytesIO(vault.decrypt_bytes(blob)), download_name=item.filename)


@bp.post("/applications/<int:app_id>/documents/from-locker")
@auth_required("citizen")
def attach_from_locker(app_id):
    a = _own(app_id)
    if not a:
        return err("Application not found", 404)
    if a.status not in ("draft", "info_requested"):
        return err("Documents can only be changed before the application is submitted", 409)
    d = request.get_json(silent=True) or {}
    item = LockerItem.query.filter_by(id=d.get("item_id") or 0, user_id=g.user.id).first()
    if not item:
        return err("That document is not in your locker", 404)
    rule = next((r for r in normalize_doc_rules(a.service.required_documents) if r["name"] == d.get("doc_type")), None)
    if not rule:
        return err("Choose which required document this is")
    blob = storage.get(item.stored_name)
    if blob is None:
        return err("The stored copy is no longer available. Upload it again.", 410)
    raw = vault.decrypt_bytes(blob)
    problem = check_file(rule, item.filename, raw)  # the institution's own rules still apply
    if problem:
        return err(problem)
    for old in [x for x in a.documents if x.doc_type == rule["name"]]:
        storage.delete(old.stored_name)
        db.session.delete(old)
    stored = uuid.uuid4().hex + os.path.splitext(item.filename)[1].lower()
    storage.put(stored, vault.encrypt_bytes(raw))
    doc = Document(application=a, doc_type=rule["name"], filename=item.filename, stored_name=stored,
                   sha256=item.sha256)
    db.session.add(doc)
    db.session.commit()
    return jsonify(doc.to_dict()), 201


# ============================ family members ============================
RELATIONS = ("Child", "Parent", "Spouse", "Sibling", "Grandparent", "Ward", "Other")


@bp.get("/family")
@auth_required("citizen")
def family_list():
    rows = FamilyMember.query.filter_by(user_id=g.user.id).order_by(FamilyMember.full_name).all()
    return jsonify([m.to_dict() for m in rows])


@bp.post("/family")
@auth_required("citizen")
def family_add():
    d = request.get_json(silent=True) or {}
    name = str(d.get("full_name") or "").strip()
    relation = d.get("relation")
    if len(name) < 3 or len(name.split()) < 2:
        return err("Enter their full name: first name and family name")
    if relation not in RELATIONS:
        return err("Choose how they are related to you")
    dob = str(d.get("dob") or "").strip() or None
    if dob:
        try:
            if date.fromisoformat(dob) > date.today():
                return err("Date of birth cannot be in the future")
        except ValueError:
            return err("Date of birth must be a valid date")
    if FamilyMember.query.filter_by(user_id=g.user.id).count() >= 15:
        return err("You can save up to 15 family members", 409)
    m = FamilyMember(user_id=g.user.id, full_name=name[:150], relation=relation, dob=dob)
    db.session.add(m)
    audit("family_add", relation)
    db.session.commit()
    return jsonify(m.to_dict()), 201


@bp.delete("/family/<int:member_id>")
@auth_required("citizen")
def family_delete(member_id):
    m = FamilyMember.query.filter_by(id=member_id, user_id=g.user.id).first()
    if not m:
        return err("Family member not found", 404)
    db.session.delete(m)
    db.session.commit()
    return jsonify({"ok": True})


# ============================ appointment slots ============================
OPEN_HOUR, CLOSE_HOUR, SLOT_MINUTES = 9, 16, 30


def slot_times(day):
    if day.weekday() >= 5:  # centers are closed at weekends
        return []
    t, end, out = datetime.combine(day, time(OPEN_HOUR)), datetime.combine(day, time(CLOSE_HOUR)), []
    while t < end:
        out.append(t)
        t += timedelta(minutes=SLOT_MINUTES)
    return out


def capacity():
    return int(current_app.config.get("CENTER_SLOT_CAPACITY") or 5)


def slot_problem(center, slot, ignore_id=None):
    """Why this center/time cannot be booked, or None."""
    if center not in CENTERS:
        return "Unknown service center"
    if slot <= now():
        return "Choose a future time slot"
    if slot.date() > (now() + timedelta(days=60)).date():
        return "Appointments can be booked up to 60 days ahead"
    if slot not in slot_times(slot.date()):
        return "Choose one of the listed times (weekdays, 9:00 to 15:30, every 30 minutes)"
    q = Appointment.query.filter_by(center=center, slot=slot, status="booked")
    if ignore_id:
        q = q.filter(Appointment.id != ignore_id)
    if q.count() >= capacity():
        return "That time is full. Please choose another."
    return None


def parse_slot(value):
    try:
        return datetime.fromisoformat(str(value)).replace(tzinfo=None, second=0, microsecond=0)
    except (TypeError, ValueError):
        return None


@bp.get("/slots")
def slots():
    center = request.args.get("center", "")
    try:
        day = date.fromisoformat(request.args.get("date", ""))
    except ValueError:
        return err("Give a date as YYYY-MM-DD")
    if center not in CENTERS:
        return err("Unknown service center", 404)
    times = slot_times(day)
    booked = {}
    if times:
        rows = Appointment.query.filter(Appointment.center == center, Appointment.status == "booked",
                                        Appointment.slot >= times[0], Appointment.slot <= times[-1]).all()
        for r in rows:
            booked[r.slot] = booked.get(r.slot, 0) + 1
    out = [{"time": t.strftime("%H:%M"), "slot": t.isoformat(), "left": max(0, capacity() - booked.get(t, 0)),
            "past": t <= now()} for t in times]
    return jsonify({"center": center, "date": day.isoformat(), "closed": not times, "slots": out})


def _my_appointment(ap_id):
    ap = db.session.get(Appointment, ap_id)
    if not ap:
        return None
    a = _own(ap.application_id)
    return ap if a else None


@bp.put("/appointments/<int:ap_id>")
@auth_required("citizen", "agent")
def reschedule(ap_id):
    ap = _my_appointment(ap_id)
    if not ap:
        return err("Appointment not found", 404)
    if ap.status != "booked":
        return err("Only a booked appointment can be moved", 409)
    d = request.get_json(silent=True) or {}
    center = d.get("center") or ap.center
    slot = parse_slot(d.get("slot"))
    if not slot:
        return err("Choose a new time")
    problem = slot_problem(center, slot, ignore_id=ap.id)
    if problem:
        return err(problem, 409 if "full" in problem else 400)
    ap.center, ap.slot = center, slot
    notify(ap.user_id, "Appointment moved", f"{center} on {slot:%d %b %Y at %H:%M}")
    audit("appointment_reschedule", str(ap.id))
    db.session.commit()
    return jsonify(ap.to_dict())


@bp.post("/appointments/<int:ap_id>/cancel")
@auth_required("citizen", "agent")
def cancel(ap_id):
    ap = _my_appointment(ap_id)
    if not ap:
        return err("Appointment not found", 404)
    if ap.status != "booked":
        return err("This appointment is not active", 409)
    ap.status = "cancelled"
    notify(ap.user_id, "Appointment cancelled", f"{ap.center} on {ap.slot:%d %b %Y at %H:%M} was cancelled.")
    audit("appointment_cancel", str(ap.id))
    db.session.commit()
    return jsonify(ap.to_dict())


# ============================ delivery choice ============================
@bp.put("/applications/<int:app_id>/delivery")
@auth_required("citizen", "agent")
def choose_delivery(app_id):
    a = _own(app_id)
    if not a:
        return err("Application not found", 404)
    if a.status in ("completed", "rejected"):
        return err("This application is closed", 409)
    d = request.get_json(silent=True) or {}
    method = d.get("method")
    if method == "collect":
        if d.get("center") not in CENTERS:
            return err("Choose the service center where you will collect it")
        choice = {"method": "collect", "center": d["center"]}
    elif method == "courier":
        address = str(d.get("address") or "").strip()
        phone = str(d.get("phone") or "").strip() or a.user.phone
        if len(address) < 10:
            return err("Enter a full delivery address with a landmark")
        choice = {"method": "courier", "address": address[:300], "phone": phone[:30],
                  "fee_usd": float(current_app.config.get("COURIER_FEE_USD") or 0)}
    else:
        return err("Choose collection or delivery")
    a.delivery = choice
    db.session.add(ApplicationEvent(application=a, status=a.status, actor=g.user.full_name,
                                    message="Delivery preference: " + (f"collect at {choice['center']}"
                                                                       if method == "collect" else "courier delivery")))
    audit("delivery_choice", a.reference)
    db.session.commit()
    return jsonify(a.to_dict(full=True))


@bp.get("/meta/delivery")
def delivery_meta():
    return jsonify({"centers": CENTERS, "courier_fee_usd": float(current_app.config.get("COURIER_FEE_USD") or 0)})


__all__ = ["slot_problem", "parse_slot", "save_locker_copy"]
_ = Application  # imported for type clarity in callers
