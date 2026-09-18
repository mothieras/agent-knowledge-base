"""T4 生命周期与到期端到端（PHASE2 §7.4 验证口径）。

覆盖：可控时间到期前/时/后三态（HTTP 通道）、重启后已到期不混入、
到期与并发修订竞争不丢更新（失败方重读重试后两变更都在）、
显式历史查询对 archived/deleted/expired 可见。
"""
import threading
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from core.entry_service import EntryService, EntryServiceError
from db.entry_store import EntryStore


class FakeClock:
    def __init__(self):
        self.now = datetime(2026, 9, 17, 12, 0, 0, tzinfo=timezone.utc)

    def __call__(self):
        return self.now

    def advance(self, **kw):
        self.now = self.now + timedelta(**kw)


@pytest.fixture
def entry_app(tmp_path):
    """HTTP 应用 + 可控时钟 + 落盘条目库（重启测试用同一文件）。"""
    import api.main as m
    from conftest import make_app_service

    clock = FakeClock()
    db_path = str(tmp_path / "entries.db")
    svc = make_app_service(generation=False)
    svc.entries = EntryService(EntryStore(db_path), clock=clock)
    m.app.state.app_service = svc
    yield TestClient(m.app), clock, db_path, svc
    m.app.state.app_service = None
    svc.entries._store.close()


def create(client, body="端到端条目", **kw):
    payload = {"type": "memory", "body": body, "scope": {"kind": "global"},
               "author": "e2e/1.0", **kw}
    return client.post("/entries", json=payload).json()


class TestExpiryPhases:
    """可控时钟下到期前/时/后三态。"""

    def test_before_at_after_expiry(self, entry_app):
        client, clock, _, _ = entry_app
        entry = create(client, body="到期三态验证条目",
                       expires_at="2026-09-17T12:30:00Z")

        # 到期前：可搜索、expired=false
        assert client.post("/entries/search", json={"query": "到期三态"}).json()["returned_k"] == 1
        assert client.get(f"/entries/{entry['id']}").json()["expired"] is False

        # 恰好到期时点（含等于即翻转）：退出默认搜索，显式读取可见且 expired=true
        clock.advance(minutes=30)
        assert client.post("/entries/search", json={"query": "到期三态"}).json()["returned_k"] == 0
        got = client.get(f"/entries/{entry['id']}").json()
        assert got["expired"] is True
        assert got["status"] == "active"  # 到期 ≠ 状态变化
        assert got["expires_at"] == "2026-09-17T12:30:00.000Z"  # 到期信息保留

        # 到期之后：仍保持
        clock.advance(minutes=1)
        assert client.get(f"/entries/{entry['id']}").json()["expired"] is True

        # 到期不阻断修订；访问不续期
        r = client.post(f"/entries/{entry['id']}/revisions",
                        json={"expected_revision": 1, "author": "a", "body": "到期后修订正文"})
        assert r.status_code == 200 and r.json()["revision"] == 2
        assert client.post("/entries/search", json={"query": "到期后修订正文"}).json()["returned_k"] == 0
        assert client.get(f"/entries/{entry['id']}").json()["expired"] is True

        # 修订清除有效期后重新可搜索
        client.post(f"/entries/{entry['id']}/revisions",
                    json={"expected_revision": 2, "author": "a", "expires_at": None})
        assert client.post("/entries/search", json={"query": "到期后修订正文"}).json()["returned_k"] == 1


class TestRestart:
    """重启后：数据保留、已到期不混入默认搜索。"""

    def test_restart_preserves_data_and_expiry(self, entry_app):
        client, clock, db_path, svc = entry_app
        live = create(client, body="重启存活条目 alpha")
        expiring = create(client, body="重启到期条目 alpha",
                          expires_at="2026-09-17T18:00:00Z")
        client.post(f"/entries/{live['id']}/revisions",
                    json={"expected_revision": 1, "author": "a", "body": "重启存活条目 beta"})

        svc.entries._store.close()  # 停机
        clock.advance(hours=7)      # 跨过 18:00 到期点
        store2 = EntryStore(db_path)
        svc2 = EntryService(store2, clock=clock)

        got = svc2.get(live["id"])
        assert got.body == "重启存活条目 beta" and got.revision == 2 and got.expired is False
        got = svc2.get(expiring["id"])
        assert got.expired is True  # 到期判定是查询时的，重启不会让已到期条目混入
        ids = [e.id for e in svc2.search(query="重启 alpha").results]
        assert ids == [live["id"]]
        assert [h.op for h in svc2.history(live["id"])] == ["create", "update"]
        store2.close()


class TestExpiryConcurrency:
    """到期与并发修订竞争不丢更新：单胜一败 + 失败方重读重试后变更都在。"""

    def test_expired_entry_concurrent_revise_no_lost_update(self, entry_app):
        client, clock, _, svc = entry_app
        entry = create(client, body="竞争基线正文",
                       expires_at="2026-09-17T12:10:00Z")
        clock.advance(minutes=20)  # 条目已到期，仍可修订

        results = []
        barrier = threading.Barrier(2)

        def worker(i):
            barrier.wait()
            change = {"body": f"并发变更-{i}"} if i == 0 else {"expires_at": None}
            try:
                results.append(("ok", svc.entries.revise(
                    entry["id"], expected_revision=1, author=f"w{i}", **change)))
            except EntryServiceError as err:
                results.append(("err", err))

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        oks = [r for r in results if r[0] == "ok"]
        errs = [r for r in results if r[0] == "err"]
        assert len(oks) == 1 and len(errs) == 1
        assert errs[0][1].code == "revision_conflict"
        assert errs[0][1].data["current"]["revision"] == 2

        # 失败方按冲突规则重读整合后重试，其变更不丢失
        current = errs[0][1].data["current"]
        change = {"body": "并发变更-1"} if errs[0][1] is results[1][1] else {"expires_at": None}
        final = svc.entries.revise(entry["id"], expected_revision=current["revision"],
                                   author="w-retry", **change)
        assert final.revision == 3
        assert "并发变更-" in final.body
        assert final.expires_at is None  # 两个变更都生效，无丢失
        assert [h.op for h in svc.entries.history(entry["id"])] == ["create", "update", "update"]

    def test_expiry_never_overwrites_newer_revision(self, entry_app):
        """查询时判定 ⇒ 无后台改写：旧有效期不可能覆盖新修订。"""
        client, clock, _, svc = entry_app
        entry = create(client, body="旧有效期条目", expires_at="2026-09-17T12:05:00Z")
        clock.advance(minutes=10)
        assert svc.entries.get(entry["id"]).expired is True
        r = svc.entries.revise(entry["id"], expected_revision=1, author="a", body="新修订正文")
        # 修订未触碰 expires_at（缺省不变），到期状态仍由查询时判定给出
        assert r.body == "新修订正文" and r.expires_at is not None
        assert svc.entries.get(entry["id"]).body == "新修订正文"
        assert svc.entries.revision_snapshot(entry["id"], 1).body == "旧有效期条目"


class TestExplicitHistoryVisibility:
    """显式回查对 archived/deleted/expired 全可见。"""

    def test_full_lifecycle_history_visible(self, entry_app):
        client, clock, _, _ = entry_app
        entry = create(client, body="初版正文", expires_at="2026-09-17T13:00:00Z")
        eid = entry["id"]
        client.post(f"/entries/{eid}/revisions",
                    json={"expected_revision": 1, "author": "b", "body": "二版正文"})
        client.post(f"/entries/{eid}/lifecycle",
                    json={"op": "archive", "expected_revision": 2, "author": "b"})
        client.post(f"/entries/{eid}/lifecycle",
                    json={"op": "delete", "expected_revision": 3, "author": "b"})
        clock.advance(hours=2)  # 且已到期

        history = client.get(f"/entries/{eid}/revisions").json()
        assert [(h["revision"], h["op"]) for h in history] == [
            (1, "create"), (2, "update"), (3, "archive"), (4, "delete"),
        ]
        snap = client.get(f"/entries/{eid}/revisions/1").json()
        assert snap["body"] == "初版正文" and snap["expires_at"] is not None
        # deleted + expired：按 ID 显式读取仍可见全状态
        got = client.get(f"/entries/{eid}").json()
        assert got["status"] == "deleted" and got["expired"] is True
        # 默认搜索零命中
        assert client.post("/entries/search", json={"query": "正文"}).json()["returned_k"] == 0
