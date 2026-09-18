"""T5 最小备份恢复冒烟（PHASE2 §6.5）：停机拷贝 → 恢复 → 数据一致。

完整备份恢复设计留阶段 3（D7）；本测试只坐实最小口径——SQLite backup
API 停机拷贝出的文件，恢复到运行位后条目/修订历史/幂等记录完整可用，
且恢复后可继续写入。
"""
import shutil
import sqlite3

import pytest

from core.entry_service import EntryService, EntryServiceError
from db.entry_store import EntryStore

AUTHOR = "bk/1.0"


def build_entries(svc: EntryService) -> list[str]:
    """覆盖各形态：active 多修订（含幂等键）、archived、deleted、到期+项目范围。"""
    e1 = svc.create(type="memory", body="备份基线正文", scope={"kind": "global"},
                    author=AUTHOR, idempotency_key="bk-create-1")
    svc.revise(e1.id, expected_revision=1, author=AUTHOR, body="备份基线正文 二版修订")
    e2 = svc.create(type="knowledge", body="归档条目正文", scope={"kind": "global"}, author=AUTHOR)
    svc.revise(e2.id, expected_revision=1, author=AUTHOR, body="归档条目正文 二版")
    svc.lifecycle(e2.id, op="archive", expected_revision=2, author=AUTHOR)
    e3 = svc.create(type="memory", body="删除条目正文", scope={"kind": "global"}, author=AUTHOR)
    svc.lifecycle(e3.id, op="delete", expected_revision=1, author=AUTHOR)
    e4 = svc.create(type="knowledge", body="到期项目条目正文",
                    scope={"kind": "projects", "projects": ["backup"]},
                    author=AUTHOR, expires_at="2020-01-01T00:00:00Z")
    return [e1.id, e2.id, e3.id, e4.id]


def capture(svc: EntryService, ids: list[str]) -> dict:
    return {
        "entries": {i: svc.get(i).model_dump() for i in ids},
        "history": {i: [h.model_dump() for h in svc.history(i)] for i in ids},
    }


def test_backup_restore_roundtrip(tmp_path):
    db_path = str(tmp_path / "entries.db")
    svc = EntryService(EntryStore(db_path))
    ids = build_entries(svc)
    before = capture(svc, ids)
    svc._store.close()  # 停机（§6.5 口径：停机拷贝）

    backup_path = tmp_path / "entries.backup.db"
    src, dst = sqlite3.connect(db_path), sqlite3.connect(backup_path)
    src.backup(dst)
    dst.close()
    src.close()

    shutil.copy(backup_path, db_path)  # 恢复 = 备份文件回到运行位
    restored = EntryService(EntryStore(db_path))
    try:
        after = capture(restored, ids)
        assert after == before  # 全量条目状态与修订历史逐字一致

        # 按修订回查与默认搜索语义不变
        assert restored.revision_snapshot(ids[0], 1).body == "备份基线正文"
        hits = restored.search(query="备份基线 正文").results
        assert [e.id for e in hits] == [ids[0]]  # 仅 active 且未到期的全局条目

        # 幂等记录存活：同键同内容重放原结果，同键异内容拒绝
        replay = restored.create(type="memory", body="备份基线正文",
                                 scope={"kind": "global"}, author=AUTHOR,
                                 idempotency_key="bk-create-1")
        assert replay.id == ids[0] and replay.revision == 1
        with pytest.raises(EntryServiceError) as err:
            restored.create(type="memory", body="不同内容", scope={"kind": "global"},
                            author=AUTHOR, idempotency_key="bk-create-1")
        assert err.value.code == "idempotency_conflict"

        # 恢复后可继续写入
        r = restored.revise(ids[0], expected_revision=2, author=AUTHOR, body="恢复后修订正文")
        assert r.revision == 3
        assert [h.op for h in restored.history(ids[0])] == ["create", "update", "update"]
    finally:
        restored._store.close()


def test_backup_of_backup_source_intact(tmp_path):
    """备份不改动源库：拷贝后源库继续写入，互不影响。"""
    db_path = str(tmp_path / "entries.db")
    svc = EntryService(EntryStore(db_path))
    e = svc.create(type="memory", body="源库条目", scope={"kind": "global"}, author=AUTHOR)
    svc._store.close()

    backup_path = tmp_path / "entries.backup.db"
    src, dst = sqlite3.connect(db_path), sqlite3.connect(backup_path)
    src.backup(dst)
    dst.close()
    src.close()

    live = EntryService(EntryStore(db_path))
    try:
        live.revise(e.id, expected_revision=1, author=AUTHOR, body="源库条目 二版")
    finally:
        live._store.close()

    from_backup = EntryService(EntryStore(str(backup_path)))
    try:
        assert from_backup.get(e.id).body == "源库条目"  # 备份停留在拷贝时点
        assert [h.op for h in from_backup.history(e.id)] == ["create"]
    finally:
        from_backup._store.close()
