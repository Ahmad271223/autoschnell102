"""
Backend tests for the new Apple-style Admin Dashboard endpoints.

Covers:
  * POST /api/auth/login (username + email + soft-block 403)
  * POST /api/admin/users/{id}/active (soft-block, super-admin guard, self-block guard)
  * POST /api/admin/users/{id}/password (admin reset, min 8 chars)
  * POST /api/admin/me/password (self change, wrong current → 401)
  * GET  /api/admin/users (subscription / company_name / active)
  * GET  /api/admin/users/{id}/contracts (no _id, no pdf_b64)
  * GET  /api/admin/comparisons
  * GET  /api/admin/url-stats (4 windows + now)
  * GET  /api/admin/stats
  * GET  /api/admin/contracts/{id}/pdf (StreamingResponse, application/pdf)
"""
import os
import uuid
import time
import pytest
import requests

# REACT_APP_BACKEND_URL ist seit dem Proxy-Umbau bewusst LEER (relative
# /api-Aufrufe). Fuer Tests brauchen wir eine absolute Adresse -> lokales
# Backend, per TEST_BASE_URL ueberschreibbar.
BASE_URL = (os.environ.get("TEST_BASE_URL")
            or os.environ.get("REACT_APP_BACKEND_URL")
            or "http://localhost:8001").rstrip("/")

SUPER_USERNAME = os.environ.get("SUPER_ADMIN_USERNAME", "ci-superadmin")
SUPER_PASSWORD = os.environ.get("SUPER_ADMIN_PASSWORD", "ci-only-superadmin-pw-1")
LEGACY_ADMIN_EMAIL = os.environ.get("ADMIN_EMAIL", "ci-admin@ci.invalid")
LEGACY_ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "ci-only-admin-pw-1")


# --------------------- helpers / fixtures ---------------------
def _login(identifier: str, password: str):
    r = requests.post(
        f"{BASE_URL}/api/auth/login",
        json={"email": identifier, "password": password},
        timeout=30,
    )
    return r


@pytest.fixture(scope="module")
def super_admin_token():
    r = _login(SUPER_USERNAME, SUPER_PASSWORD)
    assert r.status_code == 200, f"super admin login failed: {r.status_code} {r.text}"
    data = r.json()
    assert "token" in data and "user" in data
    return data["token"], data["user"]


@pytest.fixture(scope="module")
def legacy_admin_token():
    r = _login(LEGACY_ADMIN_EMAIL, LEGACY_ADMIN_PASSWORD)
    assert r.status_code == 200, f"legacy admin login failed: {r.status_code} {r.text}"
    return r.json()["token"], r.json()["user"]


@pytest.fixture(scope="module")
def admin_headers(super_admin_token):
    token, _ = super_admin_token
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture(scope="module")
def test_dealer():
    """Create a fresh non-admin dealer for soft-block / pwd-reset tests."""
    suffix = uuid.uuid4().hex[:8]
    email = f"test_admin_dash_{suffix}@example.com"
    pw = "InitialPass123"
    r = requests.post(
        f"{BASE_URL}/api/auth/register",
        json={
            "email": email,
            "password": pw,
            "company_name": f"TEST Dash Co {suffix}",
        },
        timeout=30,
    )
    assert r.status_code in (200, 201), f"register failed: {r.status_code} {r.text}"
    data = r.json()
    return {
        "id": data["user"]["id"],
        "email": email,
        "password": pw,
        "token": data["token"],
    }


# --------------------- /auth/login ---------------------
class TestLogin:
    def test_login_username_super_admin(self):
        r = _login(SUPER_USERNAME, SUPER_PASSWORD)
        assert r.status_code == 200, r.text
        body = r.json()
        assert isinstance(body.get("token"), str) and len(body["token"]) > 10
        assert body["user"]["role"] == "admin"
        assert body["user"].get("is_super_admin") is True

    def test_login_legacy_admin_email(self):
        r = _login(LEGACY_ADMIN_EMAIL, LEGACY_ADMIN_PASSWORD)
        assert r.status_code == 200, r.text
        assert r.json()["user"]["role"] == "admin"

    def test_login_wrong_password(self):
        r = _login(SUPER_USERNAME, "definitely-wrong")
        assert r.status_code == 401


# --------------------- /admin/users/{id}/active ---------------------
class TestSoftBlock:
    def test_block_then_login_403(self, admin_headers, test_dealer):
        # block
        r = requests.post(
            f"{BASE_URL}/api/admin/users/{test_dealer['id']}/active",
            json={"active": False}, headers=admin_headers, timeout=20,
        )
        assert r.status_code == 200, r.text
        assert r.json()["active"] is False

        # login attempt → 403 with "Account ist deaktiviert"
        login = _login(test_dealer["email"], test_dealer["password"])
        assert login.status_code == 403, login.text
        # FastAPI HTTPException returns {"detail": "..."}
        detail = login.json().get("detail", "")
        assert "deaktiviert" in detail.lower()

    def test_reenable_then_login_works(self, admin_headers, test_dealer):
        r = requests.post(
            f"{BASE_URL}/api/admin/users/{test_dealer['id']}/active",
            json={"active": True}, headers=admin_headers, timeout=20,
        )
        assert r.status_code == 200, r.text
        assert r.json()["active"] is True

        login = _login(test_dealer["email"], test_dealer["password"])
        assert login.status_code == 200, login.text

    def test_cannot_block_super_admin(self, admin_headers, super_admin_token):
        _, super_user = super_admin_token
        r = requests.post(
            f"{BASE_URL}/api/admin/users/{super_user['id']}/active",
            json={"active": False}, headers=admin_headers, timeout=20,
        )
        assert r.status_code == 400, r.text

    def test_cannot_block_self(self, legacy_admin_token, super_admin_token):
        # Audit 09/2026: Sperren ist Super-Admin-exklusiv -> normaler Admin 403 ...
        token, user = legacy_admin_token
        h = {"Authorization": f"Bearer {token}"}
        r = requests.post(
            f"{BASE_URL}/api/admin/users/{user['id']}/active",
            json={"active": False}, headers=h, timeout=20,
        )
        assert r.status_code == 403, r.text
        # ... und der Super-Admin kann sich weiterhin nicht selbst sperren (400)
        stoken, suser = super_admin_token
        r = requests.post(
            f"{BASE_URL}/api/admin/users/{suser['id']}/active",
            json={"active": False},
            headers={"Authorization": f"Bearer {stoken}"}, timeout=20,
        )
        assert r.status_code == 400, r.text


# --------------------- /admin/users/{id}/password ---------------------
class TestAdminResetPassword:
    def test_reset_user_password_then_login(self, admin_headers, test_dealer):
        new_pw = "NewPass12345"
        r = requests.post(
            f"{BASE_URL}/api/admin/users/{test_dealer['id']}/password",
            json={"new_password": new_pw}, headers=admin_headers, timeout=20,
        )
        assert r.status_code == 200, r.text

        # login with new password works
        login = _login(test_dealer["email"], new_pw)
        assert login.status_code == 200, login.text
        # update fixture password so subsequent tests don't break
        test_dealer["password"] = new_pw

        # old password should NO LONGER work
        bad = _login(test_dealer["email"], "InitialPass123")
        assert bad.status_code == 401

    def test_password_too_short(self, admin_headers, test_dealer):
        r = requests.post(
            f"{BASE_URL}/api/admin/users/{test_dealer['id']}/password",
            json={"new_password": "abc"}, headers=admin_headers, timeout=20,
        )
        assert r.status_code in (400, 422), r.text   # zentrale Passwortregel (422 aus dem Modell)


# --------------------- /admin/me/password ---------------------
class TestAdminSelfPassword:
    def test_wrong_current_password_returns_401(self, legacy_admin_token):
        token, _ = legacy_admin_token
        h = {"Authorization": f"Bearer {token}"}
        r = requests.post(
            f"{BASE_URL}/api/admin/me/password",
            json={"current_password": "wrong-current", "new_password": "Whatever12345"},
            headers=h, timeout=20,
        )
        assert r.status_code == 401, r.text

    def test_change_then_change_back(self, legacy_admin_token):
        token, _ = legacy_admin_token
        h = {"Authorization": f"Bearer {token}"}
        new_pw = "TempAdminPw12345"
        r = requests.post(
            f"{BASE_URL}/api/admin/me/password",
            json={"current_password": LEGACY_ADMIN_PASSWORD, "new_password": new_pw},
            headers=h, timeout=20,
        )
        assert r.status_code == 200, r.text

        # confirm new pw works
        login = _login(LEGACY_ADMIN_EMAIL, new_pw)
        assert login.status_code == 200

        # restore original so other tests / future runs are stable
        token2 = login.json()["token"]
        h2 = {"Authorization": f"Bearer {token2}"}
        r2 = requests.post(
            f"{BASE_URL}/api/admin/me/password",
            json={"current_password": new_pw, "new_password": LEGACY_ADMIN_PASSWORD},
            headers=h2, timeout=20,
        )
        assert r2.status_code == 200, r2.text

    def test_too_short_password_returns_400(self):
        # Re-login fresh because previous tests in this class changed/restored
        # the password and the single-session guard invalidates the stale token.
        r0 = _login(LEGACY_ADMIN_EMAIL, LEGACY_ADMIN_PASSWORD)
        assert r0.status_code == 200, r0.text
        h = {"Authorization": f"Bearer {r0.json()['token']}"}
        r = requests.post(
            f"{BASE_URL}/api/admin/me/password",
            json={"current_password": LEGACY_ADMIN_PASSWORD, "new_password": "abc"},
            headers=h, timeout=20,
        )
        assert r.status_code == 400, r.text


# --------------------- list / contracts / comparisons / url-stats / stats ---------------------
class TestAdminListsAndStats:
    def test_list_users_shape(self, admin_headers, test_dealer):
        r = requests.get(f"{BASE_URL}/api/admin/users", headers=admin_headers, timeout=20)
        assert r.status_code == 200, r.text
        users = r.json()
        assert isinstance(users, list) and len(users) >= 1
        # find our test dealer
        tu = next((u for u in users if u.get("id") == test_dealer["id"]), None)
        assert tu is not None, "newly created test dealer not in /admin/users"
        assert "subscription" in tu and isinstance(tu["subscription"], dict)
        for k in ("plan", "status", "active"):
            assert k in tu["subscription"], f"subscription missing key {k}"
        assert "company_name" in tu
        assert "role" in tu
        assert "active" in tu
        # no _id leaks
        assert "_id" not in tu

    def test_user_contracts_shape(self, admin_headers, test_dealer):
        r = requests.get(
            f"{BASE_URL}/api/admin/users/{test_dealer['id']}/contracts",
            headers=admin_headers, timeout=20,
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert "user" in body and "contracts" in body
        assert isinstance(body["contracts"], list)
        for c in body["contracts"]:
            assert "_id" not in c
            assert "pdf_b64" not in c
        # user dict must not leak sensitive fields
        assert "password_hash" not in body["user"]
        assert "_id" not in body["user"]

    def test_comparisons_shape(self, admin_headers):
        r = requests.get(f"{BASE_URL}/api/admin/comparisons", headers=admin_headers, timeout=30)
        assert r.status_code == 200, r.text
        body = r.json()
        assert "items" in body and "total" in body
        assert isinstance(body["items"], list)
        assert body["total"] == len(body["items"])
        for it in body["items"][:5]:
            assert "count" in it
            assert "sources" in it and isinstance(it["sources"], list)
            assert "users" in it and isinstance(it["users"], list)

    def test_url_stats_shape(self, admin_headers):
        r = requests.get(f"{BASE_URL}/api/admin/url-stats", headers=admin_headers, timeout=20)
        assert r.status_code == 200, r.text
        body = r.json()
        for window in ("all_time", "last_7d", "last_24h", "today"):
            assert window in body, f"missing window {window}"
            w = body[window]
            for k in ("mobile", "kleinanzeigen", "autoscout", "other", "total"):
                assert k in w, f"{window} missing key {k}"
                assert isinstance(w[k], int)
        assert "now" in body and isinstance(body["now"], str)

    def test_stats_shape(self, admin_headers):
        r = requests.get(f"{BASE_URL}/api/admin/stats", headers=admin_headers, timeout=20)
        assert r.status_code == 200, r.text
        body = r.json()
        for k in ("users", "active_subs", "contracts", "appointments", "comparisons_today"):
            assert k in body, f"missing key {k}"
            assert isinstance(body[k], int)


# --------------------- /admin/contracts/{id}/pdf ---------------------
class TestAdminContractPdf:
    def test_pdf_streaming(self, admin_headers):
        # Find first contract with a pdf_b64 via /admin/contracts (which strips pdf_b64,
        # but /admin/users/{id}/contracts is similar). Just take the first contract id.
        r = requests.get(f"{BASE_URL}/api/admin/contracts", headers=admin_headers, timeout=20)
        assert r.status_code == 200, r.text
        contracts = r.json()
        if not contracts:
            pytest.skip("no contracts in DB to test PDF streaming")
        contract_id = contracts[0]["id"]

        pr = requests.get(
            f"{BASE_URL}/api/admin/contracts/{contract_id}/pdf",
            headers=admin_headers, timeout=30,
        )
        # Could be 404 if the doc has no pdf_b64 — try a few
        if pr.status_code == 404:
            for c in contracts[1:6]:
                pr = requests.get(
                    f"{BASE_URL}/api/admin/contracts/{c['id']}/pdf",
                    headers=admin_headers, timeout=30,
                )
                if pr.status_code == 200:
                    contract_id = c["id"]
                    break
        assert pr.status_code == 200, f"expected pdf 200 — last status {pr.status_code} {pr.text[:200]}"
        assert pr.headers.get("content-type", "").startswith("application/pdf"), pr.headers
        assert pr.content[:4] == b"%PDF", "response is not a PDF"


# --------------------- non-admin denied ---------------------
class TestAuthorization:
    def test_non_admin_cannot_call_admin_endpoints(self, test_dealer):
        # ensure dealer is active first
        login = _login(test_dealer["email"], test_dealer["password"])
        assert login.status_code == 200
        h = {"Authorization": f"Bearer {login.json()['token']}"}
        for path in (
            "/api/admin/users",
            "/api/admin/stats",
            "/api/admin/comparisons",
            "/api/admin/url-stats",
        ):
            r = requests.get(f"{BASE_URL}{path}", headers=h, timeout=20)
            assert r.status_code in (401, 403), f"{path} expected 401/403 got {r.status_code}"
