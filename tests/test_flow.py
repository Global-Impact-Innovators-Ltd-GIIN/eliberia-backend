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
                       json={"full_name": "X", "phone": "+231770111222", "password": "Passw0rd!"}).status_code == 409
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
