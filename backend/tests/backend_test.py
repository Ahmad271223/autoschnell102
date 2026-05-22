"""Comprehensive backend regression tests for Autohändler SaaS.

Single-session enforcement on /api/auth/login means every login invalidates
prior tokens for that user. We therefore re-login per test (function-scope
token fixtures) for any user whose state is shared across tests.
"""
import os
import uuid
import pytest
import requests

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "https://vehicle-holder-auto.preview.emergentagent.com").rstrip("/")
API = f"{BASE_URL}/api"

ADMIN_EMAIL = "admin@autohandel.app"
ADMIN_PASS = "Admin123!"
SAMPLE_AD_URL = "https://m.mobile.de/fahrzeuge/details.html?id=448228023"
SAMPLE_AD_URL_2 = "https://m.mobile.de/fahrzeuge/details.html?id=391155421"


def _hdr(token):
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


def _login(email, pwd):
    r = requests.post(f"{API}/auth/login", json={"email": email, "password": pwd})
    assert r.status_code == 200, f"Login failed for {email}: {r.status_code} {r.text}"
    return r.json()["token"]


# ---- Module-scoped credentials ----
@pytest.fixture(scope="module")
def admin_creds():
    return {"email": ADMIN_EMAIL, "password": ADMIN_PASS}


@pytest.fixture(scope="module")
def dealer_a_creds():
    email = f"TEST_dealer_a_{uuid.uuid4().hex[:8]}@autohandel.app"
    r = requests.post(f"{API}/auth/register", json={
        "email": email, "password": "Password123!", "company_name": "TEST AutoHaus A",
        "contact_person": "Hans", "phone": "+491234567890",
    })
    assert r.status_code == 200, r.text
    user_id = r.json()["user"]["id"]
    dealer_id = r.json()["user"]["dealer_id"]
    # Grant lifetime via admin so dealer_a can use protected endpoints
    admin_t = _login(ADMIN_EMAIL, ADMIN_PASS)
    pr = requests.put(f"{API}/admin/users/{user_id}", json={"plan_type": "lifetime"}, headers=_hdr(admin_t))
    assert pr.status_code == 200
    return {"email": email, "password": "Password123!", "user_id": user_id, "dealer_id": dealer_id}


@pytest.fixture(scope="module")
def dealer_b_creds():
    email = f"TEST_dealer_b_{uuid.uuid4().hex[:8]}@autohandel.app"
    r = requests.post(f"{API}/auth/register", json={
        "email": email, "password": "Password123!", "company_name": "TEST AutoHaus B",
    })
    assert r.status_code == 200
    return {"email": email, "password": "Password123!", "user_id": r.json()["user"]["id"], "dealer_id": r.json()["user"]["dealer_id"]}


# ---- Function-scoped fresh tokens (avoid single-session invalidation) ----
@pytest.fixture
def admin_token(admin_creds):
    return _login(admin_creds["email"], admin_creds["password"])


@pytest.fixture
def dealer_a_token(dealer_a_creds):
    return _login(dealer_a_creds["email"], dealer_a_creds["password"])


@pytest.fixture
def dealer_b_token(dealer_b_creds):
    return _login(dealer_b_creds["email"], dealer_b_creds["password"])


# ============== AUTH ==============
class TestAuth:
    def test_register(self):
        email = f"TEST_reg_{uuid.uuid4().hex[:8]}@autohandel.app"
        r = requests.post(f"{API}/auth/register", json={
            "email": email, "password": "Password123!", "company_name": "TEST Reg Inc",
        })
        assert r.status_code == 200, r.text
        body = r.json()
        assert "token" in body and len(body["token"]) > 10
        assert body["user"]["email"] == email
        assert body["user"]["role"] == "dealer"

    def test_register_duplicate(self):
        email = f"TEST_dup_{uuid.uuid4().hex[:8]}@autohandel.app"
        requests.post(f"{API}/auth/register", json={"email": email, "password": "Password123!", "company_name": "x"})
        r = requests.post(f"{API}/auth/register", json={"email": email, "password": "Password123!", "company_name": "y"})
        assert r.status_code == 409

    def test_login_invalid(self):
        r = requests.post(f"{API}/auth/login", json={"email": ADMIN_EMAIL, "password": "wrong"})
        assert r.status_code == 401

    def test_me(self, admin_token):
        r = requests.get(f"{API}/auth/me", headers=_hdr(admin_token))
        assert r.status_code == 200
        d = r.json()
        assert d["user"]["email"] == ADMIN_EMAIL
        assert d["subscription"]["active"] is True
        assert d["dealer"] is not None

    def test_single_session_invalidates_old(self):
        email = f"TEST_sess_{uuid.uuid4().hex[:8]}@autohandel.app"
        requests.post(f"{API}/auth/register", json={"email": email, "password": "Password123!", "company_name": "TEST Sess"})
        t1 = _login(email, "Password123!")
        t2 = _login(email, "Password123!")
        assert t1 != t2
        assert requests.get(f"{API}/auth/me", headers=_hdr(t1)).status_code == 401
        assert requests.get(f"{API}/auth/me", headers=_hdr(t2)).status_code == 200


# ============== SUBSCRIPTION GATING ==============
class TestSubscription:
    def test_compare_without_sub_402(self, dealer_b_token):
        r = requests.post(f"{API}/mobile/compare", json={"url": SAMPLE_AD_URL}, headers=_hdr(dealer_b_token))
        assert r.status_code == 402

    def test_dealer_a_has_lifetime(self, dealer_a_token):
        m = requests.get(f"{API}/auth/me", headers=_hdr(dealer_a_token))
        assert m.status_code == 200
        assert m.json()["subscription"]["active"] is True
        assert m.json()["subscription"]["plan"] == "lifetime"


# ============== MOBILE COMPARE ==============
class TestMobileCompare:
    def test_compare_returns_vehicle(self, dealer_a_token):
        r = requests.post(f"{API}/mobile/compare", json={"url": SAMPLE_AD_URL}, headers=_hdr(dealer_a_token))
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["ad_id"] == "448228023"
        assert "search_url" in body and "mobile.de" in body["search_url"]
        assert body["vehicle_id"] == "v_448228023"
        assert body["source"] in ("api", "mock", "cache")

    def test_compare_cache(self, dealer_a_token):
        requests.post(f"{API}/mobile/compare", json={"url": SAMPLE_AD_URL}, headers=_hdr(dealer_a_token))
        r2 = requests.post(f"{API}/mobile/compare", json={"url": SAMPLE_AD_URL}, headers=_hdr(dealer_a_token))
        assert r2.status_code == 200
        assert r2.json()["source"] == "cache"

    def test_compare_invalid_url(self, dealer_a_token):
        r = requests.post(f"{API}/mobile/compare", json={"url": "https://example.com/x"}, headers=_hdr(dealer_a_token))
        assert r.status_code == 400

    def test_live_counter(self, dealer_a_token):
        requests.post(f"{API}/mobile/compare", json={"url": SAMPLE_AD_URL}, headers=_hdr(dealer_a_token))
        r = requests.get(f"{API}/mobile/live-counter/448228023", headers=_hdr(dealer_a_token))
        assert r.status_code == 200
        body = r.json()
        assert "active_now" in body and "today" in body
        assert body["today"] >= 1


# ============== CONTRACTS ==============
def _ensure_vehicle(token, url=SAMPLE_AD_URL):
    r = requests.post(f"{API}/mobile/compare", json={"url": url}, headers=_hdr(token))
    assert r.status_code == 200, r.text
    return r.json()["vehicle_id"]


def _create_contract(token, vehicle_id, price=15500.0):
    payload = {
        "vehicle_id": vehicle_id, "seller_name": "Max Mustermann",
        "seller_address": "Teststr 1", "seller_zip": "10115", "seller_city": "Berlin",
        "seller_phone": "+491701234567", "seller_email": "max@example.com",
        "purchase_price": price, "pickup_date": "2026-02-15", "pickup_time": "10:00",
    }
    r = requests.post(f"{API}/contracts", json=payload, headers=_hdr(token))
    assert r.status_code == 200, r.text
    return r.json()


class TestContracts:
    def test_create_and_pdf(self, dealer_a_token):
        vid = _ensure_vehicle(dealer_a_token)
        c = _create_contract(dealer_a_token, vid)
        assert c["id"]
        assert c["seller_name"] == "Max Mustermann"
        assert c["purchase_price"] == 15500.0
        assert c.get("pdf_b64")

        # GET the PDF binary
        r = requests.get(f"{API}/contracts/{c['id']}/pdf", headers=_hdr(dealer_a_token))
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("application/pdf")
        assert r.content[:4] == b"%PDF"
        assert len(r.content) > 1000

    def test_send_whatsapp_and_email(self, dealer_a_token):
        vid = _ensure_vehicle(dealer_a_token)
        c = _create_contract(dealer_a_token, vid)
        wa = requests.post(f"{API}/contracts/{c['id']}/send",
                           json={"channel": "whatsapp", "recipient": "+491701234567", "message": "Hallo Test"},
                           headers=_hdr(dealer_a_token))
        assert wa.status_code == 200
        assert "wa.me" in wa.json()["wa_url"]
        em = requests.post(f"{API}/contracts/{c['id']}/send",
                           json={"channel": "email", "recipient": "test@example.com", "subject": "Vertrag", "message": "Hallo"},
                           headers=_hdr(dealer_a_token))
        assert em.status_code == 200
        assert em.json().get("mocked") is True
        # persistence check
        cg = requests.get(f"{API}/contracts/{c['id']}", headers=_hdr(dealer_a_token)).json()
        channels = {s["channel"] for s in cg.get("send_status", [])}
        assert {"whatsapp", "email"}.issubset(channels)

    def test_list_isolation(self, dealer_a_token, dealer_b_token):
        vid = _ensure_vehicle(dealer_a_token)
        _create_contract(dealer_a_token, vid)
        # Re-login dealer_a since dealer_b login may have happened in fixture? Actually different users so unaffected.
        a_list = requests.get(f"{API}/contracts", headers=_hdr(dealer_a_token)).json()
        b_list = requests.get(f"{API}/contracts", headers=_hdr(dealer_b_token)).json()
        assert isinstance(a_list, list) and isinstance(b_list, list)
        assert len(a_list) >= 1
        a_ids = {c["id"] for c in a_list}
        b_ids = {c["id"] for c in b_list}
        assert a_ids.isdisjoint(b_ids)


# ============== APPOINTMENTS ==============
class TestAppointments:
    def test_create_and_update(self, dealer_a_token):
        vid = _ensure_vehicle(dealer_a_token)
        c = _create_contract(dealer_a_token, vid)
        payload = {
            "vehicle_id": vid, "contract_id": c["id"],
            "seller_name": "Max", "pickup_date": "2026-02-20",
            "pickup_time": "11:00", "pickup_address": "Teststr 1",
        }
        r = requests.post(f"{API}/appointments", json=payload, headers=_hdr(dealer_a_token))
        assert r.status_code == 200, r.text
        appt = r.json()
        lst = requests.get(f"{API}/appointments", headers=_hdr(dealer_a_token)).json()
        assert any(a["id"] == appt["id"] for a in lst)
        u = requests.put(f"{API}/appointments/{appt['id']}", json={"pickup_date": "2026-03-01"}, headers=_hdr(dealer_a_token))
        assert u.status_code == 200
        assert u.json()["pickup_date_changed"] is True

    def test_isolation(self, dealer_a_token, dealer_b_token):
        a_list = requests.get(f"{API}/appointments", headers=_hdr(dealer_a_token)).json()
        b_list = requests.get(f"{API}/appointments", headers=_hdr(dealer_b_token)).json()
        assert isinstance(a_list, list) and isinstance(b_list, list)
        a_ids = {a["id"] for a in a_list}
        b_ids = {a["id"] for a in b_list}
        assert a_ids.isdisjoint(b_ids)


# ============== DRIVERS ==============
class TestDrivers:
    def test_crud(self, dealer_a_token):
        r = requests.post(f"{API}/drivers", json={"name": "TEST Fahrer", "phone": "+491701112233"}, headers=_hdr(dealer_a_token))
        assert r.status_code == 200
        did = r.json()["id"]
        lst = requests.get(f"{API}/drivers", headers=_hdr(dealer_a_token)).json()
        assert any(d["id"] == did for d in lst)
        u = requests.put(f"{API}/drivers/{did}", json={"name": "TEST Fahrer 2", "phone": "+491702223344"}, headers=_hdr(dealer_a_token))
        assert u.status_code == 200 and u.json()["name"] == "TEST Fahrer 2"
        d = requests.delete(f"{API}/drivers/{did}", headers=_hdr(dealer_a_token))
        assert d.status_code == 200


# ============== DEALER SETTINGS ==============
class TestDealerSettings:
    def test_update(self, dealer_a_token):
        body = {
            "comparison_rules": {"power_pct": 5},
            "email_subject": "Neuer Betreff",
            "whatsapp_template": "Custom WA template",
        }
        r = requests.put(f"{API}/dealer/settings", json=body, headers=_hdr(dealer_a_token))
        assert r.status_code == 200
        d = r.json()
        assert d["email_subject"] == "Neuer Betreff"
        assert d["comparison_rules"] == {"power_pct": 5}


# ============== ADMIN ==============
class TestAdmin:
    def test_admin_create_lifetime_user(self, admin_token):
        email = f"TEST_admin_lt_{uuid.uuid4().hex[:8]}@autohandel.app"
        r = requests.post(f"{API}/admin/users", json={
            "email": email, "password": "Password123!", "company_name": "TEST Admin LT",
            "plan_type": "lifetime",
        }, headers=_hdr(admin_token))
        assert r.status_code == 200
        tok = _login(email, "Password123!")
        c = requests.post(f"{API}/mobile/compare", json={"url": SAMPLE_AD_URL_2}, headers=_hdr(tok))
        assert c.status_code == 200

    def test_list_users_enriched(self, admin_token):
        r = requests.get(f"{API}/admin/users", headers=_hdr(admin_token))
        assert r.status_code == 200
        users = r.json()
        assert len(users) > 0
        sample = users[0]
        assert "company_name" in sample and "subscription" in sample

    def test_list_contracts(self, admin_token):
        r = requests.get(f"{API}/admin/contracts", headers=_hdr(admin_token))
        assert r.status_code == 200
        assert isinstance(r.json(), list)

    def test_admin_only(self, dealer_a_token):
        r = requests.get(f"{API}/admin/users", headers=_hdr(dealer_a_token))
        assert r.status_code == 403


# ============== PAYMENTS ==============
class TestPayments:
    def test_checkout_monthly(self, dealer_a_token):
        r = requests.post(f"{API}/payments/checkout",
                          json={"plan": "monthly", "origin_url": BASE_URL},
                          headers=_hdr(dealer_a_token))
        if r.status_code != 200:
            pytest.skip(f"Stripe checkout unavailable: {r.status_code} {r.text[:200]}")
        body = r.json()
        assert body["url"].startswith("http")
        assert "session_id" in body
        s = requests.get(f"{API}/payments/status/{body['session_id']}", headers=_hdr(dealer_a_token))
        assert s.status_code == 200

    def test_checkout_invalid_plan(self, dealer_a_token):
        r = requests.post(f"{API}/payments/checkout",
                          json={"plan": "weekly", "origin_url": BASE_URL},
                          headers=_hdr(dealer_a_token))
        assert r.status_code == 400
