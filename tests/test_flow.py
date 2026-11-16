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
    return c.post("/api/auth/login", json={"phone": phone, "password": pw}).get_json()["token"]


def register(c, phone="0770111222"):
    r = c.post("/api/auth/register", json={"full_name": "Ma Test", "phone": phone, "password": "Passw0rd!"})
    assert r.status_code == 201
    r = c.post("/api/auth/verify-otp", json={"phone": phone, "otp": r.get_json()["dev_otp"]})
    return r.get_json()["token"]


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
    for t in ("birth", "photo"):
        r = client.post(f"/api/applications/{aid}/documents", headers=hdr(tok),
                        data={"file": (io.BytesIO(b"%PDF-1.4 fake"), t + ".pdf"), "doc_type": t},
                        content_type="multipart/form-data")
        assert r.status_code == 201
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
               json={"data": {"full_name": "Ma Test", "nin": "N123"}})
    client.post(f"/api/applications/{a['id']}/documents", headers=hdr(tok),
                data={"file": (io.BytesIO(b"%PDF-1.4"), "id.pdf")}, content_type="multipart/form-data")
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
    client.put(f"/api/applications/{aid}", headers=hdr(ag), json={"data": {"passport_no": "P123", "dob": "1950-01-01"}})
    client.post(f"/api/applications/{aid}/documents", headers=hdr(ag),
                data={"file": (io.BytesIO(b"%PDF-1.4"), "old.pdf")}, content_type="multipart/form-data")
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
