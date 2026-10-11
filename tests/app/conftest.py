"""App test fixtures: a fresh data folder, the sample GGS seeded through the importer, and one
validation run plus one twin run executed through the API (shared by the whole session)."""
from __future__ import annotations

import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.backend.settings import Settings


def wait_job(client, job_id, timeout=180):
    t0 = time.time()
    while time.time() - t0 < timeout:
        j = client.get(f"/api/v1/jobs/{job_id}").json()
        if j["status"] in ("done", "failed", "cancelled"):
            return j
        time.sleep(0.3)
    raise TimeoutError(f"job {job_id} still running")


class Api:
    """TestClient wrapper that sends the CSRF header on every mutating request."""

    def __init__(self, client: TestClient):
        self.c = client

    def _h(self):
        return {"X-CSRF-Token": self.c.cookies.get("ft_csrf", "")}

    def get(self, url, **kw):
        return self.c.get(url, **kw)

    def post(self, url, **kw):
        return self.c.post(url, headers=self._h(), **kw)

    def patch(self, url, **kw):
        return self.c.patch(url, headers=self._h(), **kw)

    def put(self, url, **kw):
        return self.c.put(url, headers=self._h(), **kw)

    def delete(self, url, **kw):
        return self.c.request("DELETE", url, headers=self._h(), **kw)

    def login(self, user, pw):
        r = self.c.post("/api/v1/auth/login", json={"username": user, "password": pw})
        assert r.status_code == 200, r.text
        return r.json()


@pytest.fixture(scope="session")
def data_dir(tmp_path_factory) -> Path:
    return tmp_path_factory.mktemp("flowtwin")


@pytest.fixture(scope="session")
def client(data_dir):
    from app.backend.main import create_app
    s = Settings(data_dir=data_dir, scheduler_enabled=False, watch_enabled=False, workers=2)
    app = create_app(s, start_scheduler=False)
    with TestClient(app) as c:
        yield c


@pytest.fixture(scope="session")
def admin_pw(client, data_dir):
    txt = (data_dir / "initial_admin_password.txt").read_text()
    return txt.split("password: ")[1].split()[0]


@pytest.fixture(scope="session")
def api(client, admin_pw) -> Api:
    a = Api(client)
    a.login("admin", admin_pw)
    r = a.post("/api/v1/auth/password", json={"old_password": admin_pw, "new_password": "test-admin-pass-1"})
    assert r.status_code == 200, r.text
    return a


@pytest.fixture(scope="session")
def seeded(api):
    r = api.post("/api/v1/admin/seed-sample")
    assert r.status_code == 200, r.text
    return r.json()


@pytest.fixture(scope="session")
def twin_run(api, seeded):
    r = api.post("/api/v1/runs", json={"kind": "twin"})
    assert r.status_code == 200, r.text
    j = wait_job(api, r.json()["job_id"], 300)
    assert j["status"] == "done", j
    return api.get(f"/api/v1/runs/{r.json()['id']}").json()


@pytest.fixture(scope="session")
def validation_run(api, twin_run):
    r = api.post("/api/v1/runs", json={"kind": "validation"})
    j = wait_job(api, r.json()["job_id"])
    assert j["status"] == "done", j
    return api.get(f"/api/v1/runs/{r.json()['id']}").json()
