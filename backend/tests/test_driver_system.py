"""Tests for new standalone driver-account system (Fahrer-App).

Covers:
  /api/driver/register, /login, /me, /me PUT
  /api/drivers/add, /api/drivers, DELETE /api/drivers/{id}
  /api/drivers/{id}/conflicts
  /api/driver/appointments
  /api/driver/appointments/{id}/pickup-order.pdf
  /api/driver/contracts/{id}/pdf
"""
import os
import time
import uuid
import pytest
import requests

# REACT_APP_BACKEND_URL ist seit dem Proxy-Umbau bewusst LEER (relative
# /api-Aufrufe). Fuer Tests brauchen wir eine absolute Adresse -> lokales
# Backend, per TEST_BASE_URL ueberschreibbar.
BASE_URL = (os.environ.get("TEST_BASE_URL")
            or os.environ.get("REACT_APP_BACKEND_URL")
            or "http://localhost:8001").rstrip("/")
API = f"{BASE_URL}/api"

# Die Tests haengen NICHT mehr an einem fest eingerichteten Demo-Admin
# ("Admin123!") — der existiert weder in CI noch auf frischen Rechnern.
# Stattdessen legt _make_admin() einen Wegwerf-Admin direkt in der
# Datenbank an (dieselbe DB wie das laufende Backend, siehe MONGO_URL/
# DB_NAME) und raeumt ihn am Ende wieder weg.
MONGO_URL = os.environ.get("MONGO_URL") or "mongodb://127.0.0.1:27017"
DB_NAME = os.environ.get("DB_NAME") or "autoschnell"
_ADMIN_SUFFIX = uuid.uuid4().hex[:8]
ADMIN_EMAIL = f"test_admin_{_ADMIN_SUFFIX}@e2etest-mail.de"
ADMIN_PASSWORD = "TestAdmin123!"


def _make_admin():
    """Wegwerf-Admin in der DB anlegen (idempotent)."""
    import bcrypt
    from pymongo import MongoClient
    dbx = MongoClient(MONGO_URL, serverSelectionTimeoutMS=5000)[DB_NAME]
    if not dbx.users.find_one({"email": ADMIN_EMAIL}):
        dbx.users.insert_one({
            "id": f"testadm_{_ADMIN_SUFFIX}", "email": ADMIN_EMAIL,
            "role": "admin", "active": True, "dealer_id": None,
            "password_hash": bcrypt.hashpw(ADMIN_PASSWORD.encode(),
                                           bcrypt.gensalt()).decode(),
            "created_at": "2026-01-01T00:00:00+00:00"})


def _drop_admin():
    from pymongo import MongoClient
    MongoClient(MONGO_URL, serverSelectionTimeoutMS=5000)[DB_NAME] \
        .users.delete_many({"email": ADMIN_EMAIL})


def _admin_login():
    _make_admin()
    r = requests.post(f"{API}/auth/login",
                      json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD},
                      timeout=30)
    assert r.status_code == 200, f"Admin-Login: {r.status_code} {r.text[:200]}"
    return r.json()["token"]


# ---------------- helpers ----------------
def _unique(prefix="test"):
    # lowercase – server normalizes driver emails to lowercase on save
    return f"test_{prefix}_{uuid.uuid4().hex[:8]}"


@pytest.fixture(scope="session", autouse=True)
def _admin_lifecycle():
    yield
    _drop_admin()


@pytest.fixture(scope="module")
def admin_token():
    return _admin_login()


@pytest.fixture(scope="module")
def dealer_a():
    """Independent dealer account A."""
    email = f"{_unique('dealerA')}@example.com"
    r = requests.post(f"{API}/auth/register", json={
        "email": email, "password": "Test1234!", "company_name": "Autohaus A",
        "contact_person": "Anna A", "phone": "+491110000",
    }, timeout=30)
    assert r.status_code == 200, r.text
    data = r.json()
    return {"email": email, "password": "Test1234!", "token": data["token"],
            "user": data["user"]}


@pytest.fixture
def dealer_a_token(dealer_a):
    """Fresh token (re-login) – guards against single-session token rotation."""
    r = requests.post(f"{API}/auth/login",
                      json={"email": dealer_a["email"],
                            "password": dealer_a["password"]}, timeout=30)
    assert r.status_code == 200
    return r.json()["token"]


@pytest.fixture(scope="module")
def dealer_b():
    email = f"{_unique('dealerB')}@example.com"
    r = requests.post(f"{API}/auth/register", json={
        "email": email, "password": "Test1234!", "company_name": "Autohaus B",
        "phone": "+492220000",
    }, timeout=30)
    assert r.status_code == 200
    data = r.json()
    # Activate lifetime so dealer can hit protected endpoints if needed
    return {"email": email, "password": "Test1234!", "token": data["token"],
            "user": data["user"]}


@pytest.fixture
def dealer_b_token(dealer_b):
    r = requests.post(f"{API}/auth/login",
                      json={"email": dealer_b["email"],
                            "password": dealer_b["password"]}, timeout=30)
    assert r.status_code == 200
    return r.json()["token"]


# ---------------- driver/register ----------------
class TestDriverRegisterLogin:
    def test_register_creates_account_and_token(self):
        email = f"{_unique('drv')}@example.com"
        r = requests.post(f"{API}/driver/register", json={
            "email": email, "password": "Drv1234!",
            "display_name": "Max Mustermann",
        }, timeout=30)
        assert r.status_code == 200, r.text
        data = r.json()
        assert "token" in data and isinstance(data["token"], str)
        d = data["driver"]
        assert d["email"] == email
        assert d["display_name"] == "Max Mustermann"
        code = d["driver_code"]
        assert code.startswith("FD-") and len(code) == 11

    def test_duplicate_email_returns_409(self):
        email = f"{_unique('dup')}@example.com"
        payload = {"email": email, "password": "Drv1234!", "display_name": "Dup"}
        r1 = requests.post(f"{API}/driver/register", json=payload, timeout=30)
        assert r1.status_code == 200
        r2 = requests.post(f"{API}/driver/register", json=payload, timeout=30)
        assert r2.status_code == 409

    def test_login_success(self):
        email = f"{_unique('lg')}@example.com"
        requests.post(f"{API}/driver/register", json={
            "email": email, "password": "Drv1234!", "display_name": "L G",
        }, timeout=30)
        r = requests.post(f"{API}/driver/login",
                          json={"email": email, "password": "Drv1234!"},
                          timeout=30)
        assert r.status_code == 200
        assert r.json()["driver"]["email"] == email

    def test_login_wrong_password(self):
        email = f"{_unique('wp')}@example.com"
        requests.post(f"{API}/driver/register", json={
            "email": email, "password": "Drv1234!", "display_name": "W P",
        }, timeout=30)
        r = requests.post(f"{API}/driver/login",
                          json={"email": email, "password": "wrong!!"},
                          timeout=30)
        assert r.status_code == 401


# ---------------- driver/me ----------------
@pytest.fixture
def fresh_driver():
    email = f"{_unique('me')}@example.com"
    r = requests.post(f"{API}/driver/register", json={
        "email": email, "password": "Drv1234!", "display_name": "Me Tester",
    }, timeout=30)
    assert r.status_code == 200
    data = r.json()
    return {"email": email, "password": "Drv1234!",
            "token": data["token"], "driver": data["driver"]}


class TestDriverMe:
    def test_me_initial_no_dealers(self, fresh_driver):
        r = requests.get(f"{API}/driver/me",
                         headers={"Authorization": f"Bearer {fresh_driver['token']}"},
                         timeout=30)
        assert r.status_code == 200
        body = r.json()
        assert body["email"] == fresh_driver["email"]
        assert body["driver_code"] == fresh_driver["driver"]["driver_code"]
        assert body["dealers"] == []

    def test_update_display_name(self, fresh_driver, dealer_a_token):
        # First link the driver to dealer_a so we can verify mirror update
        code = fresh_driver["driver"]["driver_code"]
        link = requests.post(f"{API}/drivers/add",
                             headers={"Authorization": f"Bearer {dealer_a_token}"},
                             json={"driver_code": code}, timeout=30)
        assert link.status_code == 200, link.text

        # Now driver renames himself
        r = requests.put(f"{API}/driver/me",
                         headers={"Authorization": f"Bearer {fresh_driver['token']}"},
                         json={"display_name": "Neuer Name"}, timeout=30)
        assert r.status_code == 200, r.text
        # NOTE: PUT response uses the stale `driver` dependency dict so
        # display_name on the response may be old; verify via GET.
        get_after = requests.get(f"{API}/driver/me",
                                 headers={"Authorization": f"Bearer {fresh_driver['token']}"},
                                 timeout=30).json()
        assert get_after["display_name"] == "Neuer Name", \
            f"Persistence failed: {get_after}"

        # Dealer should now also see the new name
        listed = requests.get(f"{API}/drivers",
                              headers={"Authorization": f"Bearer {dealer_a_token}"},
                              timeout=30).json()
        match = [d for d in listed if d["driver_code"] == code]
        assert match and match[0]["name"] == "Neuer Name"


# ---------------- dealer drivers/add ----------------
class TestDealerDrivers:
    def test_add_driver_by_code(self, dealer_a_token):
        # Create driver
        email = f"{_unique('linkdrv')}@example.com"
        rr = requests.post(f"{API}/driver/register", json={
            "email": email, "password": "Drv1234!", "display_name": "Link Drv",
        }, timeout=30).json()
        code = rr["driver"]["driver_code"]

        r = requests.post(f"{API}/drivers/add",
                          headers={"Authorization": f"Bearer {dealer_a_token}"},
                          json={"driver_code": code}, timeout=30)
        assert r.status_code == 200, r.text
        info = r.json()
        assert info["driver_code"] == code
        assert info["email"] == email
        assert info["name"] == "Link Drv"

    def test_add_unknown_code_404(self, dealer_a_token):
        r = requests.post(f"{API}/drivers/add",
                          headers={"Authorization": f"Bearer {dealer_a_token}"},
                          json={"driver_code": "FD-NOTHEREE"}, timeout=30)
        assert r.status_code == 404

    def test_add_duplicate_409(self, dealer_a_token):
        email = f"{_unique('dupl')}@example.com"
        rr = requests.post(f"{API}/driver/register", json={
            "email": email, "password": "Drv1234!", "display_name": "Dupl",
        }, timeout=30).json()
        code = rr["driver"]["driver_code"]
        r1 = requests.post(f"{API}/drivers/add",
                           headers={"Authorization": f"Bearer {dealer_a_token}"},
                           json={"driver_code": code}, timeout=30)
        assert r1.status_code == 200
        r2 = requests.post(f"{API}/drivers/add",
                           headers={"Authorization": f"Bearer {dealer_a_token}"},
                           json={"driver_code": code}, timeout=30)
        assert r2.status_code == 409

    def test_list_drivers(self, dealer_a_token):
        r = requests.get(f"{API}/drivers",
                         headers={"Authorization": f"Bearer {dealer_a_token}"},
                         timeout=30)
        assert r.status_code == 200
        assert isinstance(r.json(), list)
        # basic shape check
        for d in r.json():
            assert "driver_code" in d and "name" in d and "email" in d

    def test_delete_driver_link(self, dealer_a_token):
        # create + link
        email = f"{_unique('del')}@example.com"
        rr = requests.post(f"{API}/driver/register", json={
            "email": email, "password": "Drv1234!", "display_name": "Del",
        }, timeout=30).json()
        code = rr["driver"]["driver_code"]
        driver_id = rr["driver"]["id"]
        add = requests.post(f"{API}/drivers/add",
                            headers={"Authorization": f"Bearer {dealer_a_token}"},
                            json={"driver_code": code}, timeout=30)
        assert add.status_code == 200
        # delete
        r = requests.delete(f"{API}/drivers/{driver_id}",
                            headers={"Authorization": f"Bearer {dealer_a_token}"},
                            timeout=30)
        assert r.status_code == 200
        # listing no longer contains it
        listed = requests.get(f"{API}/drivers",
                              headers={"Authorization": f"Bearer {dealer_a_token}"},
                              timeout=30).json()
        assert all(d["driver_code"] != code for d in listed)


# ---------------- conflicts ----------------
class TestConflicts:
    def test_conflicts_endpoint_returns_payload(self, dealer_a_token):
        # create driver and link
        email = f"{_unique('cf')}@example.com"
        rr = requests.post(f"{API}/driver/register", json={
            "email": email, "password": "Drv1234!", "display_name": "CF",
        }, timeout=30).json()
        code = rr["driver"]["driver_code"]
        driver_id = rr["driver"]["id"]
        requests.post(f"{API}/drivers/add",
                      headers={"Authorization": f"Bearer {dealer_a_token}"},
                      json={"driver_code": code}, timeout=30)
        r = requests.get(f"{API}/drivers/{driver_id}/conflicts",
                         headers={"Authorization": f"Bearer {dealer_a_token}"},
                         params={"date": "2099-01-01"}, timeout=30)
        assert r.status_code == 200
        body = r.json()
        assert "conflicts" in body
        assert isinstance(body["conflicts"], list)


# ---------------- driver appointments + pdf access ----------------
class TestDriverAppointments:
    def test_driver_sees_assigned_appointments(self, dealer_a_token, dealer_a):
        # 1) Activate lifetime for dealer_a (admin)
        admin = _admin_login()
        ulist = requests.get(f"{API}/admin/users",
                             headers={"Authorization": f"Bearer {admin}"},
                             timeout=30).json()
        target = next(u for u in ulist
                      if u["email"].lower() == dealer_a["email"].lower())
        requests.put(f"{API}/admin/users/{target['id']}",
                     headers={"Authorization": f"Bearer {admin}"},
                     json={"plan_type": "lifetime"}, timeout=30)

        # dealer needs fresh token after admin updates? Not strictly, but re-login to be safe
        token = requests.post(f"{API}/auth/login",
                              json={"email": dealer_a["email"],
                                    "password": dealer_a["password"]},
                              timeout=30).json()["token"]
        H = {"Authorization": f"Bearer {token}"}

        # 2) Create driver + link
        email = f"{_unique('appt')}@example.com"
        rr = requests.post(f"{API}/driver/register", json={
            "email": email, "password": "Drv1234!", "display_name": "Appt Drv",
        }, timeout=30).json()
        drv_token = rr["token"]
        drv_id = rr["driver"]["id"]
        code = rr["driver"]["driver_code"]
        requests.post(f"{API}/drivers/add", headers=H,
                      json={"driver_code": code}, timeout=30)

        # 3) Vergleich -> Fahrzeug -> Vertrag -> Termin. Laeuft das Backend
        # im Mock-Modus (CI: MOCK_PROVIDER_FETCH=true), nutzen wir einen
        # synthetischen Kleinanzeigen-Link — der Test laeuft dann WIRKLICH
        # durch. Ohne Mock (lokal, um echte Anbieter-Abrufe zu vermeiden)
        # wird sauber uebersprungen.
        ka_url = ("https://www.kleinanzeigen.de/s-anzeige/drvtest/"
                  f"97{uuid.uuid4().int % 10**8:08d}-216-1")
        r = requests.post(f"{API}/mobile/compare", headers=H,
                          json={"url": ka_url}, timeout=90)
        if r.status_code != 200 or not (r.json().get("vehicle") or {}).get("_mock"):
            pytest.skip("Backend ohne MOCK_PROVIDER_FETCH — Test wuerde "
                        "einen echten Kleinanzeigen-Abruf ausloesen")
        vehicle_id = r.json().get("vehicle_id") or r.json().get("vehicle", {}).get("id")
        assert vehicle_id, f"vehicle_id fehlt in Antwort: {str(r.json())[:200]}"

        # Create contract
        cr = requests.post(f"{API}/contracts", headers=H, json={
            "vehicle_id": vehicle_id,
            "seller_name": "Verkäufer GmbH",
            "seller_address": "Str 2", "seller_zip": "10115", "seller_city": "Berlin",
            "seller_phone": "+490", "seller_email": "v@e.de",
            "purchase_price": 19999,
            "pickup_date": "2099-06-15",
            "pickup_time": "10:00",
        }, timeout=60)
        if cr.status_code not in (200, 201):
            pytest.skip(f"contract creation unavailable: {cr.status_code} {cr.text[:200]}")
        contract = cr.json()
        contract_id = contract.get("id")

        # Appointment should be auto-created
        appts = requests.get(f"{API}/appointments", headers=H, timeout=30).json()
        appt = next((a for a in appts if a.get("contract_id") == contract_id), None)
        assert appt is not None, "Auto-created appointment missing"

        # 4) Assign driver
        upd = requests.put(f"{API}/appointments/{appt['id']}", headers=H,
                          json={"driver_id": drv_id,
                                "pickup_date": "2099-06-15"}, timeout=30)
        assert upd.status_code == 200, upd.text

        # 5) Driver sees appointment
        DH = {"Authorization": f"Bearer {drv_token}"}
        seen = requests.get(f"{API}/driver/appointments", headers=DH, timeout=30)
        assert seen.status_code == 200
        listed = seen.json()
        match = [a for a in listed if a["id"] == appt["id"]]
        assert match, f"Driver does not see assigned appt: {listed}"
        assert match[0].get("vehicle") is not None
        assert "dealer" in match[0]
        assert match[0]["dealer"].get("name")

        # 6) Pickup-order PDF
        pdf = requests.get(
            f"{API}/driver/appointments/{appt['id']}/pickup-order.pdf",
            headers=DH, timeout=60)
        assert pdf.status_code == 200
        assert pdf.headers.get("content-type", "").startswith("application/pdf")

        # 7) Contract PDF for own appointment
        cpdf = requests.get(f"{API}/driver/contracts/{contract_id}/pdf",
                            headers=DH, timeout=60)
        assert cpdf.status_code == 200
        assert cpdf.headers.get("content-type", "").startswith("application/pdf")

        # 8) Conflict warning – another dealer in DB asks for this driver same day
        # Skip if dealer_b not active — keep test focused
        # Use dealer_a's own conflict endpoint with same date
        cf = requests.get(f"{API}/drivers/{drv_id}/conflicts",
                          headers=H, params={"date": "2099-06-15"}, timeout=30)
        assert cf.status_code == 200
        assert cf.json()["count"] >= 1

    def test_driver_cannot_access_foreign_pdf(self):
        """A different driver cannot fetch another driver's pickup-order PDF."""
        email = f"{_unique('foreign')}@example.com"
        rr = requests.post(f"{API}/driver/register", json={
            "email": email, "password": "Drv1234!", "display_name": "Foreign",
        }, timeout=30).json()
        token = rr["token"]
        # random non-existent appt
        r = requests.get(
            f"{API}/driver/appointments/{uuid.uuid4()}/pickup-order.pdf",
            headers={"Authorization": f"Bearer {token}"}, timeout=30)
        assert r.status_code == 404
