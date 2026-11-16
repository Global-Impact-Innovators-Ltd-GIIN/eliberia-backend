import io

import pytest

from app import create_app


@pytest.fixture()
def client(tmp_path):
    app = create_app({"SQLALCHEMY_DATABASE_URI": f"sqlite:///{tmp_path}/t.db", "TESTING": True,
                      "UPLOAD_DIR": str(tmp_path / "up"), "EXPOSE_OTP": True})
    return app.test_client()


def hdr(tok):
    return {"Authorization": f"Bearer {tok}"}


def login(c, phone, pw):
    r = c.post("/api/auth/login", json={"phone": phone, "password": pw}).get_json()
    if r.get("two_factor"):  # staff and agents confirm with a one-time code
        r = c.post("/api/auth/login/verify", json={"phone": phone, "otp": r["dev_otp"]}).get_json()
    return r["token"]


def register(c, phone="0770111222"):
    r = c.post("/api/auth/register", json={"full_name": "Ma Test", "phone": phone, "password": "Passw0rd!"})
    assert r.status_code == 201
    r = c.post("/api/auth/verify-otp", json={"phone": phone, "otp": r.get_json()["dev_otp"]})
    return r.get_json()["token"]


PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 64
JPG = b"\xff\xd8\xff" + b"0" * 64
SAMPLE = {"pdf": b"%PDF-1.4 test file", "png": PNG, "jpg": JPG}


def upload_all(c, tok, aid):
    """Upload a valid file for every document the institution requires."""
    rules = c.get(f"/api/applications/{aid}", headers=hdr(tok)).get_json()["service_detail"]["required_documents"]
    for r in rules:
        ext = r["formats"][0]
        resp = c.post(f"/api/applications/{aid}/documents", headers=hdr(tok), content_type="multipart/form-data",
                      data={"file": (io.BytesIO(SAMPLE[ext]), "f." + ext), "doc_type": r["name"]})
        assert resp.status_code == 201, resp.get_json()


def test_catalog(client):
    r = client.get("/api/services")
    assert r.status_code == 200 and len(r.get_json()) >= 10
    assert client.get("/api/services?q=passport").get_json()


def test_auth_rules(client):
    assert client.post("/api/auth/login", json={"phone": "0770", "password": "x"}).status_code == 401
    register(client)
    assert client.post("/api/auth/register",
                       json={"full_name": "Xavier Doe", "phone": "+231770111222", "password": "Passw0rd!"}).status_code == 409
    assert client.get("/api/applications").status_code == 401


def test_full_digital_flow_with_remote_biometrics(client):
    tok = register(client)
    a = client.post("/api/applications", headers=hdr(tok), json={"service_code": "NATIONAL_ID_NEW"}).get_json()
    aid = a["id"]
    # incomplete form is rejected
    assert client.post(f"/api/applications/{aid}/submit", headers=hdr(tok)).status_code == 400
    client.put(f"/api/applications/{aid}", headers=hdr(tok), json={"data": {
        "full_name": "Ma Test", "dob": "1990-01-01", "place_of_birth": "Monrovia",
        "county": "Montserrado", "mother_name": "Mama"}})
    upload_all(client, tok, aid)
    # payment required
    assert client.post(f"/api/applications/{aid}/submit", headers=hdr(tok)).status_code == 402
    p = client.post(f"/api/applications/{aid}/pay", headers=hdr(tok),
                    json={"method": "orange_money"}).get_json()
    assert client.post(f"/api/applications/payments/{p['txn_ref']}/confirm", headers=hdr(tok),
                       json={}).get_json()["status"] == "success"
    r = client.post(f"/api/applications/{aid}/submit", headers=hdr(tok))
    assert r.status_code == 200 and r.get_json()["status"] == "submitted"

    # public tracking
    assert client.get(f"/api/applications/track/{a['reference']}").get_json()["status"] == "submitted"

    # officer reviews and asks for remote biometrics
    off = login(client, "+231770000002", "Officer@12345")
    assert client.post(f"/api/staff/applications/{aid}/action", headers=hdr(off),
                       json={"action": "start_review"}).status_code == 200
    # cannot approve before face verification
    assert client.post(f"/api/staff/applications/{aid}/action", headers=hdr(off),
                       json={"action": "approve"}).status_code == 409
    client.post(f"/api/staff/applications/{aid}/action", headers=hdr(off), json={"action": "request_biometrics"})

    # citizen captures face remotely
    bad = client.post(f"/api/applications/{aid}/biometrics", headers=hdr(tok), content_type="multipart/form-data",
                      data={"kind": "face", "liveness": "0.2", "image": (io.BytesIO(b"x" * 2000), "f.jpg")})
    assert bad.status_code == 422
    ok = client.post(f"/api/applications/{aid}/biometrics", headers=hdr(tok), content_type="multipart/form-data",
                     data={"kind": "face", "liveness": "0.95", "image": (io.BytesIO(b"y" * 2000), "f.jpg")})
    assert ok.status_code == 201 and ok.get_json()["record"]["status"] == "dispatched"
    # fingerprints cannot be faked remotely
    assert client.post(f"/api/applications/{aid}/biometrics", headers=hdr(tok), content_type="multipart/form-data",
                       data={"kind": "fingerprint"}).status_code in (409, 422)

    for act in ("approve", "ready", "complete"):
        r = client.post(f"/api/staff/applications/{aid}/action", headers=hdr(off), json={"action": act})
        assert r.status_code == 200, r.get_json()
    final = client.get(f"/api/applications/{aid}", headers=hdr(tok)).get_json()
    assert final["status"] == "completed" and len(final["events"]) >= 7


def test_officer_scope_and_citizen_isolation(client):
    t1, t2 = register(client, "0770333444"), register(client, "0770555666")
    a = client.post("/api/applications", headers=hdr(t1), json={"service_code": "TIN_REG"}).get_json()
    assert client.get(f"/api/applications/{a['id']}", headers=hdr(t2)).status_code == 404
    # citizens cannot reach staff endpoints
    assert client.get("/api/staff/applications", headers=hdr(t1)).status_code == 403
    assert client.get("/api/admin/audit", headers=hdr(login(client, "+231770000002", "Officer@12345"))).status_code == 403
    assert client.get("/api/admin/audit", headers=hdr(login(client, "+231770000001", "Admin@12345"))).status_code == 200


def _approved_app(client, tok, service="TIN_REG", beneficiary=None):
    body = {"service_code": service}
    if beneficiary:
        body["beneficiary"] = beneficiary
    a = client.post("/api/applications", headers=hdr(tok), json=body).get_json()
    client.put(f"/api/applications/{a['id']}", headers=hdr(tok),
               json={"data": {"full_name": "Ma Test", "nin": "LR12345678"}})
    upload_all(client, tok, a["id"])
    assert client.post(f"/api/applications/{a['id']}/submit", headers=hdr(tok)).status_code == 200
    adm = login(client, "+231770000001", "Admin@12345")
    for act in ("start_review", "approve"):
        assert client.post(f"/api/staff/applications/{a['id']}/action", headers=hdr(adm),
                           json={"action": act}).status_code == 200
    return a


def test_certificate_issue_and_public_verification(client):
    tok = register(client, "0770777111")
    a = _approved_app(client, tok, beneficiary={"name": "Little Child", "relation": "Child"})
    full = client.get(f"/api/applications/{a['id']}", headers=hdr(tok)).get_json()
    cert = full["certificate"]
    assert cert and cert["holder_name"] == "Little Child"
    v = client.get(f"/api/verify/{cert['number']}").get_json()
    assert v["valid"] is True and v["service_name"].startswith("Tax Identification")
    assert client.get("/api/verify/ELC-2026-DEADBEEF").status_code == 404
    # tampering with the stored certificate breaks the signature
    from app.extensions import db
    from app.models import Certificate
    with client.application.app_context():
        c = Certificate.query.filter_by(number=cert["number"]).first()
        c.holder_name = "Someone Else"
        db.session.commit()
    assert client.get(f"/api/verify/{cert['number']}").get_json()["valid"] is False
    # feedback
    assert client.post(f"/api/applications/{a['id']}/feedback", headers=hdr(tok),
                       json={"rating": 9}).status_code == 400
    assert client.post(f"/api/applications/{a['id']}/feedback", headers=hdr(tok),
                       json={"rating": 5, "comment": "Fast"}).status_code == 201
    assert client.get("/api/public/stats").get_json()["rating"] == 5.0


def test_bill_id_tracking_and_assistant(client):
    tok = register(client, "0770777222")
    a = client.post("/api/applications", headers=hdr(tok), json={"service_code": "PASSPORT_NEW"}).get_json()
    assert a["bill_id"] and len(a["bill_id"]) == 12
    assert client.get(f"/api/applications/track/{a['bill_id']}").get_json()["reference"] == a["reference"]
    r = client.post("/api/assistant", json={"q": "How much is a passport?"}).get_json()
    assert "$50.00" in r["answer"] and r["services"][0]["code"].startswith("PASSPORT")
    assert len(client.get("/api/services/popular").get_json()) == 6


def test_account_security(client):
    tok = register(client, "0770777333")
    # weak password rejected
    assert client.post("/api/auth/register", json={"full_name": "Weak Pw", "phone": "0770777334",
                                                   "password": "abcdefgh"}).status_code == 400
    # password change revokes the old token
    r = client.post("/api/auth/change-password", headers=hdr(tok),
                    json={"current_password": "Passw0rd!", "new_password": "N3wPassword!"})
    assert r.status_code == 200
    assert client.get("/api/auth/me", headers=hdr(tok)).status_code == 401
    assert client.get("/api/auth/me", headers=hdr(r.get_json()["token"])).status_code == 200
    # lockout after 5 bad passwords, even with the right one afterwards
    for _ in range(5):
        assert client.post("/api/auth/login", json={"phone": "0770777333", "password": "nope"}).status_code == 401
    assert client.post("/api/auth/login", json={"phone": "0770777333", "password": "N3wPassword!"}).status_code == 423
    # OTP brute force is capped
    r = client.post("/api/auth/register", json={"full_name": "Otp Test", "phone": "0770777335", "password": "Passw0rd!"})
    for _ in range(5):
        client.post("/api/auth/verify-otp", json={"phone": "0770777335", "otp": "000000"})
    assert client.post("/api/auth/verify-otp", json={"phone": "0770777335",
                                                     "otp": r.get_json()["dev_otp"]}).status_code == 429


def test_agent_assisted_flow_and_password_reset(client):
    adm = login(client, "+231770000001", "Admin@12345")
    for ph in ("0770888001", "0770888002"):
        assert client.post("/api/admin/officers", headers=hdr(adm), json={
            "full_name": "Agent " + ph[-1], "phone": ph, "password": "Agent0pass!", "role": "agent"}).status_code == 201
    ag, ag2 = login(client, "0770888001", "Agent0pass!"), login(client, "0770888002", "Agent0pass!")
    # unknown citizen needs a name; then gets registered and texted a consent code
    assert client.post("/api/agent/citizens/start", headers=hdr(ag), json={"phone": "0770888100"}).status_code == 404
    r = client.post("/api/agent/citizens/start", headers=hdr(ag),
                    json={"phone": "0770888100", "full_name": "Old Pa Flomo"})
    assert r.status_code == 201
    cid, code = r.get_json()["citizen"]["id"], r.get_json()["dev_otp"]
    # no consent yet -> cannot act
    assert client.post("/api/applications", headers=hdr(ag),
                       json={"service_code": "TIN_REG", "citizen_id": cid}).status_code == 403
    # a consent code cannot be used to reset the citizen's password (account takeover guard)
    assert client.post("/api/auth/reset", json={"phone": "0770888100", "otp": code,
                                                "new_password": "Hijack123!"}).status_code == 400
    assert client.post("/api/agent/citizens/consent", headers=hdr(ag),
                       json={"phone": "0770888100", "otp": code}).status_code == 200
    # the other agent has no consent
    assert client.post("/api/applications", headers=hdr(ag2),
                       json={"service_code": "TIN_REG", "citizen_id": cid}).status_code == 403
    a = client.post("/api/applications", headers=hdr(ag), json={"service_code": "PASSPORT_RENEW", "citizen_id": cid})
    assert a.status_code == 201 and a.get_json()["assisted"] is True
    aid = a.get_json()["id"]
    client.put(f"/api/applications/{aid}", headers=hdr(ag), json={"data": {"passport_no": "PP0012345", "dob": "1950-01-01"}})
    upload_all(client, ag, aid)
    p = client.post(f"/api/applications/{aid}/pay", headers=hdr(ag), json={"method": "agent_cash"}).get_json()
    assert client.post(f"/api/applications/payments/{p['txn_ref']}/confirm", headers=hdr(ag), json={}).status_code == 200
    assert client.post(f"/api/applications/{aid}/submit", headers=hdr(ag)).get_json()["status"] == "submitted"
    # isolation: other agent and an unrelated officer cannot open it; citizens cannot use cash
    assert client.get(f"/api/applications/{aid}", headers=hdr(ag2)).status_code == 404
    nir = login(client, "+231770000002", "Officer@12345")  # passport belongs to MFA, not NIR
    assert client.get(f"/api/applications/{aid}", headers=hdr(nir)).status_code == 404
    st = client.get("/api/agent/stats", headers=hdr(ag)).get_json()
    assert st["assisted_total"] == 1 and st["commission_usd"] == 0.5
    assert len(client.get("/api/agent/applications", headers=hdr(ag)).get_json()) == 1
    # the citizen sets their own password later and finds the application in their account
    code = client.post("/api/auth/forgot", json={"phone": "0770888100"}).get_json()["dev_otp"]
    r = client.post("/api/auth/reset", json={"phone": "0770888100", "otp": code, "new_password": "MyOwnPass1"})
    assert r.status_code == 200
    mine = client.get("/api/applications", headers=hdr(r.get_json()["token"])).get_json()
    assert len(mine) == 1 and mine[0]["id"] == aid
    assert client.post(f"/api/applications/{aid}/pay", headers=hdr(r.get_json()["token"]),
                       json={"method": "agent_cash"}).status_code in (400, 409)


def test_messages_wallet_revocation_and_two_step_login(client):
    # staff password alone is not enough; a wrong code is refused
    r = client.post("/api/auth/login", json={"phone": "0770000001", "password": "Admin@12345"}).get_json()
    assert r.get("two_factor") and "token" not in r
    assert client.post("/api/auth/login/verify", json={"phone": "0770000001", "otp": "000000"}).status_code == 400
    tok, other = register(client, "0770999001"), register(client, "0770999002")
    a = _approved_app(client, tok)
    adm = login(client, "+231770000001", "Admin@12345")
    # conversation
    assert client.post(f"/api/applications/{a['id']}/messages", headers=hdr(tok), json={"body": "x"}).status_code == 400
    assert client.post(f"/api/applications/{a['id']}/messages", headers=hdr(tok),
                       json={"body": "When can I collect it?"}).status_code == 201
    assert client.post(f"/api/applications/{a['id']}/messages", headers=hdr(adm),
                       json={"body": "It is ready on Monday."}).status_code == 201
    msgs = client.get(f"/api/applications/{a['id']}/messages", headers=hdr(tok)).get_json()
    assert [m["mine"] for m in msgs] == [True, False]
    assert client.get(f"/api/applications/{a['id']}/messages", headers=hdr(other)).status_code == 404
    assert client.get("/api/notifications/count", headers=hdr(tok)).get_json()["unread"] >= 1
    # wallet and revocation
    wallet = client.get("/api/certificates", headers=hdr(tok)).get_json()
    assert len(wallet) == 1 and client.get("/api/certificates", headers=hdr(other)).get_json() == []
    num = wallet[0]["number"]
    assert client.post(f"/api/staff/certificates/{num}/revoke", headers=hdr(tok), json={"reason": "nope nope"}).status_code == 403
    assert client.post(f"/api/staff/certificates/{num}/revoke", headers=hdr(adm), json={"reason": "Issued in error"}).status_code == 200
    v = client.get(f"/api/verify/{num}").get_json()
    assert v["valid"] is False and "revoked" in v["reason"]


def test_admin_service_management_and_catalog_size(client):
    assert len(client.get("/api/services").get_json()) >= 30
    adm = login(client, "+231770000001", "Admin@12345")
    inst = client.get("/api/institutions").get_json()[0]["id"]
    body = {"code": "fishing license", "name": "Fishing License", "category": "Business", "institution_id": inst,
            "fee_usd": 12.5, "sla_days": 4, "required_documents": ["National ID"],
            "form_fields": [{"label": "Boat name", "type": "text"},
                            {"label": "Zone", "type": "select", "options": ["Coastal", "Inland"]}]}
    r = client.post("/api/admin/services", headers=hdr(adm), json=body)
    assert r.status_code == 201 and r.get_json()["code"] == "FISHING_LICENSE"
    assert r.get_json()["form_fields"][0]["name"] == "boat_name"
    assert client.post("/api/admin/services", headers=hdr(adm), json=body).status_code == 409
    assert client.get("/api/services/FISHING_LICENSE").status_code == 200
    bad = {**body, "form_fields": [{"label": "Zone", "type": "select", "options": ["Only one"]}]}
    assert client.put("/api/admin/services/FISHING_LICENSE", headers=hdr(adm), json=bad).status_code == 400
    assert client.put("/api/admin/services/FISHING_LICENSE", headers=hdr(adm),
                      json={"fee_usd": 20, "active": False}).get_json()["fee_usd"] == 20
    assert client.get("/api/services/FISHING_LICENSE").status_code == 404  # hidden from citizens
    tok = register(client, "0770999003")
    assert client.post("/api/admin/services", headers=hdr(tok), json=body).status_code == 403


def test_ussd_channel(client):
    def dial(text, phone="+231770999004", key=None):
        url = "/api/ussd" + (f"?key={key}" if key else "")
        return client.post(url, data={"sessionId": "s1", "phoneNumber": phone, "text": text}).get_data(as_text=True)
    assert dial("").startswith("CON Welcome to eLiberia")
    tok = register(client, "0770999004")
    a = client.post("/api/applications", headers=hdr(tok), json={"service_code": "PASSPORT_NEW"}).get_json()
    assert dial("1").startswith("CON")
    out = dial(f"1*{a['bill_id']}")
    assert out.startswith("END Passport") and "Unpaid: $50.00" in out
    assert "Status" in dial("1*" + a["reference"].replace("-", "").lower())
    assert dial("1*999").startswith("END No application")
    assert "$50.00" in dial("3*passport")
    assert dial("4*ELC2026DEADBEEF").startswith("END NOT VERIFIED")
    # the caller's own list is only shown when the gateway proves itself
    assert "only works" in dial("2")
    client.application.config["USSD_SECRET"] = "gw-secret"
    assert "Passport" in dial("2", key="gw-secret")
    assert "only works" in dial("2", key="wrong")


def _draft(client, tok, code, data):
    a = client.post("/api/applications", headers=hdr(tok), json={"service_code": code}).get_json()
    client.put(f"/api/applications/{a['id']}", headers=hdr(tok), json={"data": data})
    upload_all(client, tok, a["id"])
    if a["fee_usd"]:
        p = client.post(f"/api/applications/{a['id']}/pay", headers=hdr(tok), json={"method": "mtn_momo"}).get_json()
        client.post(f"/api/applications/payments/{p['txn_ref']}/confirm", headers=hdr(tok), json={})
    return a["id"]


def test_identity_document_rules_by_nationality(client):
    tok = register(client, "0770444001")
    base = {"full_name": "John Smith", "purpose": "Work"}
    submit = lambda aid: client.post(f"/api/applications/{aid}/submit", headers=hdr(tok))
    put = lambda aid, extra: client.put(f"/api/applications/{aid}", headers=hdr(tok), json={"data": {**base, **extra}})
    aid = _draft(client, tok, "RESIDENT_PERMIT", {**base, "passport_no": "AB12", "passport_no__country": "US"})
    r = submit(aid)  # a US passport number is 9 digits or a letter and 8 digits
    assert r.status_code == 400 and "United States passport number" in r.get_json()["error"]
    put(aid, {"passport_no": "G1234567", "passport_no__country": "US"})  # a valid Ghanaian number is not a US one
    assert submit(aid).status_code == 400
    put(aid, {"passport_no": "g1234567", "passport_no__country": "GH"})
    r = submit(aid)
    assert r.status_code == 200
    saved = client.get(f"/api/applications/{aid}", headers=hdr(tok)).get_json()["data"]
    assert saved["passport_no"] == "G1234567" and saved["passport_no__doc"] == "Passport"
    # a Liberian National ID cannot be claimed by another nationality
    from app.services import identity
    assert "Liberian nationals" in identity.validate("US", "National ID", "LR12345678")[1]
    assert identity.validate("US", "Passport", "123 456 789") == ("123456789", None)
    assert identity.validate("LR", "National ID", "LR12345678")[1] is None
    meta = client.get("/api/meta/identity").get_json()
    assert meta["passport"]["US"]["example"] and len(meta["countries"]) > 150
    # the citizen's own profile is held to the same rule
    assert client.patch("/api/auth/me", headers=hdr(tok), json={"nationality": "US", "id_doc_type": "National ID",
                                                               "id_number": "123456789"}).status_code == 400
    assert client.patch("/api/auth/me", headers=hdr(tok), json={"nationality": "US", "id_doc_type": "Passport",
                                                               "id_number": "123456789"}).get_json()["id_number"] == "123456789"


def test_institution_document_rules_and_encryption(client):
    tok = register(client, "0770444002")
    a = client.post("/api/applications", headers=hdr(tok), json={"service_code": "NATIONAL_ID_NEW"}).get_json()
    aid = a["id"]
    rules = {r["name"]: r for r in a["service_detail"]["required_documents"]}
    photo = rules["Passport-size photo"]
    assert photo["formats"] == ["jpg", "png"] and photo["max_mb"] == 2 and photo["note"]
    up = lambda name, raw, doc: client.post(f"/api/applications/{aid}/documents", headers=hdr(tok),
                                           content_type="multipart/form-data",
                                           data={"file": (io.BytesIO(raw), name), "doc_type": doc})
    assert "JPG, PNG" in up("me.pdf", SAMPLE["pdf"], "Passport-size photo").get_json()["error"]   # wrong format
    assert "not a real PNG" in up("me.png", b"%PDF-1.4 renamed", "Passport-size photo").get_json()["error"]
    assert "2 MB or smaller" in up("me.png", PNG + b"0" * (3 * 1024 * 1024), "Passport-size photo").get_json()["error"]
    assert up("me.png", PNG, "Something else").status_code == 400                                 # not a listed document
    assert up("me.png", PNG, "Passport-size photo").status_code == 201
    assert up("me2.jpg", JPG, "Passport-size photo").status_code == 201                           # replaces the first
    docs = client.get(f"/api/applications/{aid}", headers=hdr(tok)).get_json()["documents"]
    assert [d["filename"] for d in docs] == ["me2.jpg"]
    # at rest: the file and the answers are encrypted, yet read back intact
    import os
    from app.extensions import db
    from sqlalchemy import text
    up_dir = client.application.config["UPLOAD_DIR"]
    blob = open(os.path.join(up_dir, os.listdir(up_dir)[0]), "rb").read()
    assert blob.startswith(b"ELV1") and JPG not in blob
    assert client.get(f"/api/applications/{aid}/documents/{docs[0]['id']}", headers=hdr(tok)).data == JPG
    client.put(f"/api/applications/{aid}", headers=hdr(tok), json={"data": {"full_name": "Secret Person"}})
    with client.application.app_context():
        stored = db.session.execute(text("SELECT data FROM application WHERE id = :i"), {"i": aid}).scalar()
    assert stored.startswith("enc1:") and "Secret Person" not in stored
    assert client.get(f"/api/applications/{aid}", headers=hdr(tok)).get_json()["data"]["full_name"] == "Secret Person"


def test_institution_portal_and_platform_permissions(client):
    adm = login(client, "+231770000001", "Admin@12345")
    insts = {i["code"]: i["id"] for i in client.get("/api/institutions").get_json()}
    mk = lambda body: client.post("/api/admin/officers", headers=hdr(adm), json={"password": "Staff0pass!", **body})
    assert mk({"full_name": "LRA Boss", "phone": "0770444010", "role": "inst_admin", "institution_id": insts["LRA"]}).status_code == 201
    assert mk({"full_name": "Catalog Clerk", "phone": "0770444011", "role": "admin", "perms": ["services"]}).status_code == 201
    assert mk({"full_name": "No Perms", "phone": "0770444012", "role": "admin", "perms": []}).status_code == 400
    boss, clerk = login(client, "0770444010", "Staff0pass!"), login(client, "0770444011", "Staff0pass!")
    # --- the institution runs its own catalog, with its own rules ---
    mine = client.get("/api/institution/services", headers=hdr(boss)).get_json()
    assert mine and all(s["institution"]["code"] == "LRA" for s in mine)
    body = {"code": "tax waiver", "name": "Tax Waiver", "category": "Tax", "fee_usd": 5, "sla_days": 4,
            "institution_id": insts["NIR"],  # ignored: an institution cannot publish under another's name
            "required_documents": [{"name": "Waiver letter", "formats": ["pdf"], "max_mb": 1, "note": "Signed and stamped"}],
            "form_fields": [{"label": "Applicant ID", "type": "identity"}, {"label": "Email", "type": "email", "required": False}]}
    r = client.post("/api/institution/services", headers=hdr(boss), json=body)
    assert r.status_code == 201 and r.get_json()["institution"]["code"] == "LRA"
    assert r.get_json()["required_documents"][0] == {"name": "Waiver letter", "formats": ["pdf"], "max_mb": 1.0,
                                                     "required": True, "note": "Signed and stamped"}
    assert client.put("/api/institution/services/NATIONAL_ID_NEW", headers=hdr(boss), json={"fee_usd": 0}).status_code == 404
    bad = {**body, "code": "x2x", "required_documents": [{"name": "Thing", "formats": ["exe"]}]}
    assert client.post("/api/institution/services", headers=hdr(boss), json=bad).status_code == 400
    assert client.put("/api/institution/profile", headers=hdr(boss), json={
        "guidelines": "Bring originals to collection.", "contact_email": "help@lra.example", "name": "Hacked"}).status_code == 200
    lra = next(i for i in client.get("/api/institutions").get_json() if i["code"] == "LRA")
    assert lra["guidelines"].startswith("Bring originals") and lra["name"] == "Liberia Revenue Authority"
    assert client.post("/api/institution/team", headers=hdr(boss), json={
        "full_name": "LRA Officer", "phone": "0770444013", "password": "Staff0pass!"}).status_code == 201
    assert {u["role"] for u in client.get("/api/institution/team", headers=hdr(boss)).get_json()} == {"inst_admin", "officer"}
    # the institution admin reviews only its own applications
    tok = register(client, "0770444020")
    aid = _draft(client, tok, "TAX_WAIVER", {"applicant_id": "LR12345678", "applicant_id__country": "LR",
                                             "applicant_id__doc": "National ID", "email": "not-an-email"})
    assert "valid email" in client.post(f"/api/applications/{aid}/submit", headers=hdr(tok)).get_json()["error"]
    client.put(f"/api/applications/{aid}", headers=hdr(tok), json={"data": {
        "applicant_id": "LR12345678", "applicant_id__country": "LR", "applicant_id__doc": "National ID"}})
    assert client.post(f"/api/applications/{aid}/submit", headers=hdr(tok)).status_code == 200
    assert [x["id"] for x in client.get("/api/staff/applications", headers=hdr(boss)).get_json()] == [aid]
    other = _draft(client, tok, "NATIONAL_ID_NEW", {})
    assert client.get(f"/api/staff/applications/{other}", headers=hdr(boss)).status_code == 404
    # --- a limited platform admin can manage the catalog but nothing else ---
    assert client.get("/api/admin/services", headers=hdr(clerk)).status_code == 200
    for path in ("/api/admin/audit", "/api/admin/team", "/api/staff/applications"):
        assert client.get(path, headers=hdr(clerk)).status_code == 403, path
    assert client.post("/api/admin/institutions", headers=hdr(clerk), json={"code": "MOA", "name": "Ministry of Agriculture"}).status_code == 403
    assert client.post("/api/admin/officers", headers=hdr(clerk), json={"full_name": "Sneaky", "phone": "0770444014",
                                                                        "password": "Staff0pass!", "role": "agent"}).status_code == 403
    assert client.post("/api/admin/institutions", headers=hdr(adm), json={"code": "MOA", "name": "Ministry of Agriculture"}).status_code == 201
    assert len(client.get("/api/admin/team", headers=hdr(adm)).get_json()) >= 6


def test_data_reuse_access_transparency_and_export(client):
    tok = register(client, "0770444030")
    client.patch("/api/auth/me", headers=hdr(tok), json={"county": "Bong", "dob": "1988-05-05", "nationality": "LR",
                                                         "id_doc_type": "National ID", "id_number": "LR99887766"})
    first = client.post("/api/applications", headers=hdr(tok), json={"service_code": "TIN_REG"}).get_json()
    pre = client.get(f"/api/applications/{first['id']}/prefill", headers=hdr(tok)).get_json()["values"]
    assert pre["full_name"] == "Ma Test" and pre["nin"] == "LR99887766" and pre["nin__doc"] == "National ID"
    client.put(f"/api/applications/{first['id']}", headers=hdr(tok), json={"data": pre})
    upload_all(client, tok, first["id"])
    assert client.post(f"/api/applications/{first['id']}/submit", headers=hdr(tok)).status_code == 200
    # answers given once are offered again on the next application
    second = client.post("/api/applications", headers=hdr(tok), json={"service_code": "NATIONAL_ID_NEW"}).get_json()
    pre2 = client.get(f"/api/applications/{second['id']}/prefill", headers=hdr(tok)).get_json()["values"]
    assert pre2["full_name"] == "Ma Test" and pre2["dob"] == "1988-05-05" and pre2["county"] == "Bong"
    # ...but never when applying for someone else
    kid = client.post("/api/applications", headers=hdr(tok), json={
        "service_code": "NATIONAL_ID_NEW", "beneficiary": {"name": "Small Child", "relation": "Child"}}).get_json()
    assert client.get(f"/api/applications/{kid['id']}/prefill", headers=hdr(tok)).get_json()["values"] == {}
    # the citizen sees which official opened their file
    assert client.get(f"/api/applications/{first['id']}/access", headers=hdr(tok)).get_json() == []
    adm = login(client, "+231770000001", "Admin@12345")
    for _ in range(2):
        client.get(f"/api/staff/applications/{first['id']}", headers=hdr(adm))
    seen = client.get(f"/api/applications/{first['id']}/access", headers=hdr(tok)).get_json()
    assert len(seen) == 1 and seen[0]["viewer_name"] == "System Admin" and seen[0]["organisation"] == "eLiberia platform team"
    other = register(client, "0770444031")
    assert client.get(f"/api/applications/{first['id']}/access", headers=hdr(other)).status_code == 404
    export = client.get("/api/me/export", headers=hdr(tok)).get_json()
    assert len(export["applications"]) == 3 and export["staff_access"] and export["profile"]["id_number"] == "LR99887766"


def test_institution_review_stages(client):
    adm = login(client, "+231770000001", "Admin@12345")
    insts = {i["code"]: i["id"] for i in client.get("/api/institutions").get_json()}
    mk = lambda name, phone, role: client.post("/api/admin/officers", headers=hdr(adm), json={
        "full_name": name, "phone": phone, "role": role, "institution_id": insts["LRA"], "password": "Staff0pass!"})
    assert mk("LRA Head", "0770555010", "inst_admin").status_code == 201
    assert mk("Clerk One", "0770555011", "officer").status_code == 201
    assert mk("Clerk Two", "0770555012", "officer").status_code == 201
    head, one, two = (login(client, p, "Staff0pass!") for p in ("0770555010", "0770555011", "0770555012"))
    # the institution defines how its own office works
    stages = [{"name": "Desk check", "instructions": "Check the letter is signed."},
              {"name": "Assessment", "separate_officer": True},
              {"name": "Commissioner sign-off", "requires_admin": True}]
    body = {"code": "duty waiver", "name": "Duty Waiver", "category": "Tax", "fee_usd": 0, "sla_days": 6,
            "required_documents": [], "form_fields": [{"label": "Reason", "type": "text"}], "workflow": stages}
    r = client.post("/api/institution/services", headers=hdr(head), json=body)
    assert r.status_code == 201 and [s["name"] for s in r.get_json()["workflow"]] == ["Desk check", "Assessment", "Commissioner sign-off"]
    dup = {**body, "code": "dw2", "workflow": [{"name": "A stage"}, {"name": "a stage"}]}
    assert client.post("/api/institution/services", headers=hdr(head), json=dup).status_code == 400
    first_sep = {**body, "code": "dw3", "workflow": [{"name": "Only", "separate_officer": True}]}
    assert client.post("/api/institution/services", headers=hdr(head), json=first_sep).status_code == 400
    assert client.get("/api/services/TIN_REG").get_json()["workflow"][0]["name"] == "Review"  # default: one stage

    tok = register(client, "0770555020")
    aid = _draft(client, tok, "DUTY_WAIVER", {"reason": "Relief goods"})
    assert client.post(f"/api/applications/{aid}/submit", headers=hdr(tok)).status_code == 200
    act = lambda who, action, note="": client.post(f"/api/staff/applications/{aid}/action", headers=hdr(who),
                                                    json={"action": action, "note": note})
    stage = lambda: client.get(f"/api/applications/{aid}", headers=hdr(tok)).get_json()["stage"]
    assert act(one, "start_review").status_code == 200 and stage()["name"] == "Desk check" and stage()["total"] == 3
    assert "final stage" in act(one, "approve").get_json()["error"]            # cannot skip ahead
    assert act(one, "send_back", "x").status_code == 409                       # nothing before the first stage
    assert act(one, "advance", "Letter is signed").status_code == 200 and stage()["index"] == 1
    assert "different officer" in act(one, "advance").get_json()["error"]      # separation of duties
    assert act(two, "send_back").status_code == 400                            # needs a reason
    assert act(two, "send_back", "Stamp is missing").status_code == 200 and stage()["name"] == "Desk check"
    assert act(two, "advance").status_code == 200                              # a different clerk redoes stage 1
    assert "different officer" in act(two, "advance").get_json()["error"]
    assert act(one, "advance").status_code == 200 and stage()["name"] == "Commissioner sign-off"
    assert "institution administrator" in act(one, "approve").get_json()["error"]
    assert "final stage" in act(head, "advance").get_json()["error"]
    assert act(head, "approve").status_code == 200
    final = client.get(f"/api/applications/{aid}", headers=hdr(tok)).get_json()
    assert final["status"] == "approved" and final["certificate"]
    assert any("Sent back to stage" in e["message"] for e in final["events"])
    assert client.get(f"/api/applications/track/{final['reference']}").get_json()["stage"]["total"] == 3
    # seeded example
    assert len(client.get("/api/services/PASSPORT_NEW").get_json()["workflow"]) == 3
