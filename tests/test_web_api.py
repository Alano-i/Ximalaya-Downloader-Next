# -*- coding: utf-8 -*-
from dataclasses import fields

from fastapi.testclient import TestClient

from xdl.frontends.web import SettingsUpdate, create_app
from xdl.frontends.web_runtime import OperationBusyError
from xdl.settings import Settings


class FakeRuntime:
    def __init__(self):
        self.calls = []
        self.busy = False

    def shutdown(self):
        self.calls.append(("shutdown",))

    def bootstrap(self):
        return {
            "settings": {"default_quality": "standard"},
            "login": {"authenticated": False},
            "operation": None,
            "tasks": [],
            "counts": {"all": 0},
            "page": {
                "offset": 0, "limit": 100, "total": 0,
                "has_previous": False, "has_next": False,
            },
            "task_error": None,
        }

    def operation_snapshot(self, *, include_result=True):
        self.calls.append(("operation", include_result))
        return None

    def tasks_snapshot(self, **kwargs):
        self.calls.append(("tasks", kwargs))
        return {
            "tasks": [], "counts": {"all": 0},
            "page": {
                "offset": kwargs.get("offset", 0),
                "limit": kwargs.get("limit", 100), "total": 0,
                "has_previous": False, "has_next": False,
            },
        }

    def task_ids(self, **kwargs):
        self.calls.append(("task_ids", kwargs))
        return {"ids": [1, 2], "count": 2, "truncated": False}

    def preview_tasks(self, ids):
        self.calls.append(("preview", ids))
        return {"summary": {"states": {"done": len(ids)}, "running": 0,
                            "with_part": 0, "part_bytes": 0, "missing": 0}}

    def delete_tasks(self, ids):
        self.calls.append(("delete", ids))
        return {"result": {"deleted": len(ids)}, "tasks": [],
                "counts": {"all": 0}, "page": {}}

    def requeue_tasks(self, ids):
        self.calls.append(("requeue", ids))
        return {"requeued": len(ids), "tasks": [], "counts": {"all": 0},
                "page": {}}

    def risk_report(self):
        return {"path": "risk.jsonl", "summary": {"total": 0}}

    def start_download(self, **kwargs):
        if self.busy:
            raise OperationBusyError("已有任务")
        self.calls.append(("download", kwargs))
        return {"id": "1", "status": "running"}

    def start_login(self):
        return {"id": "2", "status": "running"}

    def start_resume(self):
        return {"id": "3", "status": "running"}

    def start_formats(self, target):
        return {"id": "4", "target": target}

    def start_inspect_storage(self):
        return {"id": "5"}

    def start_gen_sign(self, **kwargs):
        return {"id": "6", **kwargs}

    def start_extract_device(self, **kwargs):
        return {"id": "7", **kwargs}

    def start_refresh_cookies(self, **kwargs):
        return {"id": "8", **kwargs}

    def request_stop(self):
        return {"status": "running", "stop_requested": True}

    def update_settings(self, changes):
        self.calls.append(("settings", changes))
        return changes

    def login_status(self):
        self.calls.append(("login_status",))
        return {"authenticated": False, "browser_name": "Chrome"}

    def logout(self):
        self.calls.append(("logout",))
        return {"detail": {"profile_removed": True},
                "login": {"authenticated": False, "browser_name": "Chrome"}}

    def open_downloads(self, task_id=None):
        return {"path": "/tmp/downloads", "task_id": task_id}


def test_web_api_bootstrap_and_download_contract():
    runtime = FakeRuntime()
    with TestClient(create_app(runtime)) as client:
        assert client.get("/api/health").json() == {"ok": True}
        assert client.get("/api/bootstrap").json()["counts"] == {"all": 0}

        response = client.post("/api/operations/download", json={
            "mode": "album",
            "target": "https://www.ximalaya.com/album/22",
            "quality": "high",
            "range": "1-20",
        })

    assert response.status_code == 202
    assert runtime.calls[0][0] == "download"
    assert runtime.calls[0][1]["range_"] == "1-20"
    assert runtime.calls[-1] == ("shutdown",)


def test_webui_static_shell_is_served():
    with TestClient(create_app(FakeRuntime())) as client:
        page = client.get("/")
        script = client.get("/app.js")
        styles = client.get("/styles.css")

    assert page.status_code == 200
    assert "下载任务" in page.text
    assert 'name="experiment_risk_cooldown_seconds"' in page.text
    assert 'name="experiment_rotate_headless"' in page.text
    assert 'name="experiment_require_identity_change"' in page.text
    assert 'name="experiment_rebirth_rounds"' in page.text
    assert 'id="task-pagination"' in page.text
    assert 'id="login-entry-button"' in page.text
    # 登录/登出是同一个按钮（按登录态切换文案），不再有独立登出按钮
    assert 'id="logout-button"' not in page.text
    assert "window.setInterval(refreshRuntime, 850)" not in script.text
    assert "document.hidden" in script.text
    assert "javascript" in script.headers["content-type"]
    assert "text/css" in styles.headers["content-type"]


def test_web_settings_update_contract_matches_settings():
    assert set(SettingsUpdate.model_fields) == {
        field.name for field in fields(Settings)
    }


def test_web_api_rejects_invalid_download_shape():
    with TestClient(create_app(FakeRuntime())) as client:
        response = client.post("/api/operations/download", json={
            "mode": "playlist", "target": "22",
        })

    assert response.status_code == 422


def test_web_api_forwards_bounded_task_query_and_light_operation_snapshot():
    runtime = FakeRuntime()
    with TestClient(create_app(runtime)) as client:
        tasks = client.get(
            "/api/tasks?state=done&search=needle&limit=50&offset=100"
        )
        operation = client.get("/api/operation")
        detailed = client.get("/api/operation?include_result=true")

    assert tasks.status_code == 200
    assert ("tasks", {
        "state": "done", "search": "needle", "album_id": "",
        "limit": 50, "offset": 100,
    }) in runtime.calls
    assert operation.status_code == 200
    assert detailed.status_code == 200
    assert ("operation", False) in runtime.calls
    assert ("operation", True) in runtime.calls


def test_web_api_rejects_unbounded_task_queries():
    with TestClient(create_app(FakeRuntime())) as client:
        oversized = client.get("/api/tasks?limit=500")
        negative = client.get("/api/tasks?offset=-1")
        unknown = client.get("/api/tasks?state=unknown")

    assert oversized.status_code == 422
    assert negative.status_code == 422
    assert unknown.status_code == 422


def test_web_api_returns_conflict_for_busy_runtime():
    runtime = FakeRuntime()
    runtime.busy = True
    with TestClient(create_app(runtime)) as client:
        response = client.post("/api/operations/download", json={
            "mode": "track", "target": "11",
        })

    assert response.status_code == 409
    assert response.json() == {"detail": "已有任务"}


def test_web_api_updates_settings_and_opens_task_directory():
    runtime = FakeRuntime()
    with TestClient(create_app(runtime)) as client:
        settings = client.put("/api/settings", json={
            "download_dir": "/tmp/audio",
            "max_concurrency": 2,
            "experiment_risk_cooldown_seconds": 12.5,
            "experiment_rotate_headless": True,
        })
        opened = client.post("/api/open-downloads", json={"task_id": 9})

    assert settings.status_code == 200
    assert settings.json()["settings"]["max_concurrency"] == 2
    assert settings.json()["settings"]["experiment_risk_cooldown_seconds"] == 12.5
    assert settings.json()["settings"]["experiment_rotate_headless"] is True
    assert opened.json()["task_id"] == 9


def test_web_api_accepts_pc_source_backend():
    runtime = FakeRuntime()
    with TestClient(create_app(runtime)) as client:
        response = client.put("/api/settings", json={"source_backend": "pc"})

    assert response.status_code == 200
    assert response.json()["settings"]["source_backend"] == "pc"
    # 换后端/换浏览器要顺带回传新登录态，否则前端会继续显示上一套凭据的状态
    assert response.json()["login"] == {"authenticated": False,
                                        "browser_name": "Chrome"}


def test_web_api_logout_clears_credentials_for_any_backend():
    runtime = FakeRuntime()
    with TestClient(create_app(runtime)) as client:
        response = client.post("/api/auth/logout")

    assert response.status_code == 200
    assert response.json()["login"]["authenticated"] is False
    assert response.json()["detail"]["profile_removed"] is True
    assert ("logout",) in runtime.calls


def test_web_api_rejects_unknown_source_backend():
    runtime = FakeRuntime()
    with TestClient(create_app(runtime)) as client:
        response = client.put("/api/settings",
                              json={"source_backend": "weird"})

    assert response.status_code == 422


def test_task_routes_pass_scope_and_selection_through():
    runtime = FakeRuntime()
    client = TestClient(create_app(runtime))

    client.get("/api/tasks?state=done&album_id=1234&search=abc")
    client.get("/api/tasks/ids?state=failed&album_id=1234")
    preview = client.post("/api/tasks/preview", json={"ids": [1, 2]})
    deleted = client.post("/api/tasks/delete", json={"ids": [1, 2, 3]})
    requeued = client.post("/api/tasks/requeue", json={"ids": [7]})

    scope = dict(runtime.calls[0][1])
    assert scope["state"] == "done"
    assert scope["album_id"] == "1234"
    assert ("task_ids", {"state": "failed", "search": "", "album_id": "1234"}) \
        in runtime.calls
    assert preview.json()["summary"]["states"] == {"done": 2}
    assert deleted.json()["result"]["deleted"] == 3
    assert requeued.json()["requeued"] == 1


def test_task_selection_routes_validate_payload():
    client = TestClient(create_app(FakeRuntime()))

    assert client.post("/api/tasks/delete", json={"ids": []}).status_code == 422
    assert client.post("/api/tasks/delete", json={}).status_code == 422
    # 超上限直接拒绝，不悄悄截断
    huge = {"ids": list(range(5001))}
    assert client.post("/api/tasks/delete", json=huge).status_code == 422
    # 不认识的字段一律拒绝（StrictModel）
    bad = {"ids": [1], "force": True}
    assert client.post("/api/tasks/delete", json=bad).status_code == 422
