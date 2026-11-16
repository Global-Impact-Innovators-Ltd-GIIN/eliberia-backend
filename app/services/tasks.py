"""Daily housekeeping: renewal reminders, expiring stale payments, and data retention."""
from datetime import timedelta

from flask import current_app

from ..extensions import db
from ..models import Application, Certificate, LockerItem, Notification, Payment, now
from . import storage

REMINDER_DAYS = (60, 30, 7, 0)


def renewal_reminders():
    from ..utils import notify
    sent = 0
    today = now()
    horizon = today + timedelta(days=max(REMINDER_DAYS) + 1)
    rows = Certificate.query.filter(Certificate.expires_at.isnot(None), Certificate.revoked.is_(False),
                                    Certificate.expires_at <= horizon).all()
    for c in rows:
        left = (c.expires_at - today).days
        due = [d for d in REMINDER_DAYS if left <= d]
        if not due:
            continue
        threshold = min(due)
        done = list(c.reminders_sent or [])
        if threshold in done:
            continue
        a = db.session.get(Application, c.application_id)
        if left < 0:
            title, body = "Document expired", f"{c.service_name} {c.number} expired on {c.expires_at:%d %b %Y}."
        else:
            title = "Renewal reminder"
            body = (f"{c.service_name} {c.number} expires on {c.expires_at:%d %b %Y} "
                    f"({'today' if left == 0 else f'in {left} days'}). Renew online at eLiberia.")
        notify(a.user_id, title, body)
        c.reminders_sent = done + [threshold]
        sent += 1
    return sent


def expire_stale_payments(hours=24):
    cutoff = now() - timedelta(hours=hours)
    rows = Payment.query.filter(Payment.status == "pending", Payment.created_at < cutoff).all()
    for p in rows:
        p.status, p.settled_at = "failed", now()
    return len(rows)


def purge_old_drafts():
    days = int(current_app.config.get("DRAFT_RETENTION_DAYS") or 90)
    cutoff = now() - timedelta(days=days)
    rows = Application.query.filter(Application.status == "draft", Application.updated_at < cutoff).all()
    for a in rows:
        if Payment.query.filter_by(application_id=a.id, status="success").first():
            continue  # never delete something the citizen paid for
        for d in a.documents:
            storage.delete(d.stored_name)
        Payment.query.filter_by(application_id=a.id).delete()
        db.session.delete(a)
    return len(rows)


def purge_old_notifications():
    days = int(current_app.config.get("NOTIFICATION_RETENTION_DAYS") or 365)
    return Notification.query.filter(Notification.created_at < now() - timedelta(days=days)).delete()


def run_daily():
    result = {"renewal_reminders": renewal_reminders(), "expired_payments": expire_stale_payments(),
              "drafts_deleted": purge_old_drafts(), "notifications_deleted": purge_old_notifications()}
    db.session.commit()
    return result


__all__ = ["run_daily", "LockerItem"]
