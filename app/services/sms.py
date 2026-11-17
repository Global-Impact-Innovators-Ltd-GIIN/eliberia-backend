"""SMS delivery.

SMS_PROVIDER:
  log            (default) messages are only written to the server log - nothing leaves the server
  africastalking Africa's Talking  - SMS_USERNAME, SMS_API_KEY, optional SMS_SENDER_ID
                 (SMS_USERNAME=sandbox uses their free sandbox)
  twilio         Twilio            - SMS_ACCOUNT_SID, SMS_AUTH_TOKEN, SMS_FROM (a phone number, an
                 alphanumeric sender ID, or a Messaging Service SID starting with MG)

Messages are queued during a request and only sent once the response succeeded, so a request that fails
and rolls back never texts anybody.
"""
import base64
import json
import urllib.parse
import urllib.request

from flask import current_app, g, has_request_context


def queue(phone, text):
    if not phone or not text:
        return
    if has_request_context():
        g.setdefault("_sms_outbox", []).append((phone, text[:459]))  # at most 3 SMS segments
    else:
        send(phone, text[:459])


def flush(response):
    """after_request hook: send queued messages when the request succeeded."""
    outbox = g.pop("_sms_outbox", [])
    if response.status_code < 400:
        for phone, text in outbox:
            send(phone, text)
    return response


def send(phone, text):
    cfg = current_app.config
    provider = (cfg.get("SMS_PROVIDER") or "log").lower()
    try:
        if provider == "africastalking":
            sandbox = cfg.get("SMS_USERNAME") == "sandbox"
            url = f"https://api.{'sandbox.' if sandbox else ''}africastalking.com/version1/messaging"
            form = {"username": cfg["SMS_USERNAME"], "to": phone, "message": text}
            if cfg.get("SMS_SENDER_ID"):
                form["from"] = cfg["SMS_SENDER_ID"]
            req = urllib.request.Request(url, data=urllib.parse.urlencode(form).encode(), headers={
                "apiKey": cfg["SMS_API_KEY"], "Accept": "application/json",
                "Content-Type": "application/x-www-form-urlencoded"})
        elif provider == "twilio":
            sid = cfg["SMS_ACCOUNT_SID"]
            url = f"https://api.twilio.com/2010-04-01/Accounts/{sid}/Messages.json"
            auth = base64.b64encode(f"{sid}:{cfg['SMS_AUTH_TOKEN']}".encode()).decode()
            sender = cfg["SMS_FROM"]
            # A Messaging Service SID (MG...) carries a registered alphanumeric sender ID, which Liberia's MTN
            # network requires; otherwise SMS_FROM is a phone number or an alphanumeric ID such as "eLiberia".
            form = {"To": phone, "Body": text,
                    ("MessagingServiceSid" if sender.startswith("MG") and len(sender) == 34 else "From"): sender}
            req = urllib.request.Request(url, data=urllib.parse.urlencode(form).encode(),
                                         headers={"Authorization": f"Basic {auth}"})
        else:
            current_app.logger.info("SMS (not sent, SMS_PROVIDER=log) to %s***: %d chars", phone[:7], len(text))
            return True
        with urllib.request.urlopen(req, timeout=8) as r:
            body = r.read()[:300]
        current_app.logger.info("SMS sent via %s to %s***", provider, phone[:7])
        return bool(body is not None)
    except Exception as exc:  # never break the user's request because SMS failed
        current_app.logger.error("SMS via %s failed: %s", provider, exc)
        return False


def status():
    cfg = current_app.config
    provider = (cfg.get("SMS_PROVIDER") or "log").lower()
    needed = {"africastalking": ["SMS_USERNAME", "SMS_API_KEY"],
              "twilio": ["SMS_ACCOUNT_SID", "SMS_AUTH_TOKEN", "SMS_FROM"]}.get(provider, [])
    missing = [k for k in needed if not cfg.get(k)]
    return {"provider": provider, "live": provider != "log" and not missing, "missing": missing}


def _debug_dump():  # pragma: no cover - helper for local troubleshooting
    return json.dumps(status())
