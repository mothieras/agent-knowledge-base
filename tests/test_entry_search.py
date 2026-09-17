"""T3 条目搜索测试（PHASE2 §4.1/§4.2 逐行验证）。

§4.1 范围语义表逐行 + §4.2 恒定过滤/结果字段/修订绑定 + §4.4 同事务新鲜度。
"""
import re
import threading
from datetime import datetime, timedelta, timezone

import pytest

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
def clock():
    return FakeClock()


@pytest.fixture
def store(tmp_path):
    s = EntryStore(str(tmp_path / "entries.db"))
    yield s
    s.close()


@pytest.fixture
def svc(store, clock):
    return EntryService(store, clock=clock)


def add(svc, body, scope=None, type="knowledge", **kw):
    return svc.create(
        type=type, body=body,
        scope=scope or {"kind": "global"}, author="t/1.0", **kw,
    )


def result_ids(result):
    return [e.id for e in result.results]


class TestScopeSemantics:
    """§4.1 表逐行：未指定/显式全局/单项目/多项目/all_projects。"""

    def test_unspecified_scope_global_only(self, svc):
        g = add(svc, "全局条目 alpha 检索")
        add(svc, "项目条目 alpha 检索", {"kind": "projects", "projects": ["a"]})
        r = svc.search(query="alpha 检索")
        assert result_ids(r) == [g.id]

    def test_explicit_global_only(self, svc):
        g = add(svc, "全局条目 beta")
        add(svc, "项目条目 beta", {"kind": "projects", "projects": ["a"]})
        r = svc.search(query="beta", projects=None, all_projects=False)
        assert result_ids(r) == [g.id]

    def test_single_project_includes_global(self, svc):
        g = add(svc, "全局条目 gamma")
        pa = add(svc, "项目A条目 gamma", {"kind": "projects", "projects": ["a"]})
        add(svc, "项目B条目 gamma", {"kind": "projects", "projects": ["b"]})
        r = svc.search(query="gamma", projects=["a"])
        assert set(result_ids(r)) == {g.id, pa.id}

    def test_multi_project_union_dedup(self, svc):
        g = add(svc, "全局条目 delta")
        pa = add(svc, "项目A条目 delta", {"kind": "projects", "projects": ["a"]})
        both = add(svc, "双项目条目 delta", {"kind": "projects", "projects": ["a", "b"]})
        pb = add(svc, "项目B条目 delta", {"kind": "projects", "projects": ["b"]})
        add(svc, "项目C条目 delta", {"kind": "projects", "projects": ["c"]})
        r = svc.search(query="delta", projects=["a", "b"])
        assert set(result_ids(r)) == {g.id, pa.id, both.id, pb.id}
        # 双项目关联保持同一条目身份（不复制）
        assert len([i for i in result_ids(r) if i == both.id]) == 1

    def test_all_projects(self, svc):
        g = add(svc, "全局条目 epsilon")
        pa = add(svc, "项目A epsilon", {"kind": "projects", "projects": ["a"]})
        pz = add(svc, "项目Z epsilon", {"kind": "projects", "projects": ["z"]})
        r = svc.search(query="epsilon", all_projects=True)
        assert set(result_ids(r)) == {g.id, pa.id, pz.id}

    def test_projects_normalized_in_search(self, svc):
        pa = add(svc, "项目A条目 zeta", {"kind": "projects", "projects": ["proj-a"]})
        r = svc.search(query="zeta", projects=[" Proj-A "])
        assert result_ids(r) == [pa.id]
        with pytest.raises(EntryServiceError) as exc:
            svc.search(query="zeta", projects=["A B"])
        assert exc.value.code == "invalid_project"

    def test_projects_and_all_projects_exclusive(self, svc):
        with pytest.raises(EntryServiceError) as exc:
            svc.search(query="x", projects=["a"], all_projects=True)
        assert exc.value.code == "invalid_request"
        with pytest.raises(EntryServiceError) as exc:
            svc.search(query="x", projects=[])
        assert exc.value.code == "invalid_request"


class TestConstantFilters:
    """§4.2：一切搜索恒排除 archived/deleted/expired（与范围模式无关）。"""

    def test_archived_deleted_expired_excluded(self, svc, clock):
        live = add(svc, "存活条目 eta")
        archived = add(svc, "归档条目 eta")
        svc.lifecycle(archived.id, op="archive", expected_revision=1, author="a")
        deleted = add(svc, "删除条目 eta")
        svc.lifecycle(deleted.id, op="delete", expected_revision=1, author="a")
        expired = add(svc, "到期条目 eta", expires_at="2026-09-17T12:30:00Z")

        # 未到期时仅排除 archived/deleted
        assert result_ids(svc.search(query="eta")) == [live.id, expired.id]
        assert result_ids(svc.search(query="eta", all_projects=True)) == [live.id, expired.id]

        clock.advance(hours=2)  # 跨过到期点：live 不受影响，expired 退出
        assert result_ids(svc.search(query="eta")) == [live.id]

    def test_expiry_boundary_with_clock(self, svc, clock):
        e = add(svc, "临界条目 theta", expires_at="2026-09-17T14:00:00Z")
        clock.advance(hours=1, minutes=59)
        assert result_ids(svc.search(query="theta")) == [e.id]
        clock.advance(minutes=2)
        assert result_ids(svc.search(query="theta")) == []
        assert svc.get(e.id).expired is True  # 显式读取保留到期信息

    def test_type_filter(self, svc):
        m = add(svc, "记忆条目 iota", type="memory")
        k = add(svc, "知识条目 iota", type="knowledge")
        assert set(result_ids(svc.search(query="iota"))) == {m.id, k.id}
        assert result_ids(svc.search(query="iota", type="memory")) == [m.id]
        with pytest.raises(EntryServiceError) as exc:
            svc.search(query="iota", type="note")
        assert exc.value.code == "invalid_request"

    def test_limit_validation_and_bounds(self, svc):
        add(svc, "条目 kappa")
        assert svc.search(query="kappa").returned_k == 1  # 默认 20
        for bad in (0, 51, "10", None):
            with pytest.raises(EntryServiceError) as exc:
                svc.search(query="kappa", limit=bad)
            assert exc.value.code == "invalid_request"

    def test_scope_filter_precedes_truncation(self, svc):
        # 范围过滤先于 LIMIT：非候选不占名额，候选按相关性截断
        for i in range(3):
            add(svc, f"全局条目 lambda {i}")
        add(svc, "项目条目 lambda", {"kind": "projects", "projects": ["a"]})
        r = svc.search(query="lambda lambda", limit=2)
        assert len(r.results) == 2
        assert all(e.scope.kind == "global" for e in r.results)

    def test_result_fields_and_revision_binding(self, svc):
        e = add(svc, "字段条目 mu", {"kind": "projects", "projects": ["p"]},
                expires_at="2030-01-01T00:00:00Z", source={"note": "来源注"})
        svc.revise(e.id, expected_revision=1, author="a", body="字段条目 mu 修订后正文")
        hit = svc.search(query="mu 修订后", projects=["p"]).results[0]
        assert hit.body == "字段条目 mu 修订后正文"
        assert hit.revision == 2  # 命中绑定当前修订号
        assert hit.expires_at == "2030-01-01T00:00:00.000Z" and hit.expired is False
        assert hit.source.model_dump(exclude_none=True) == {"note": "来源注"}
        assert hit.scope.projects == ["p"]

    def test_query_validation(self, svc):
        for bad in ("", "   ", "x" * 2001, 123):
            with pytest.raises(EntryServiceError) as exc:
                svc.search(query=bad)
            assert exc.value.code == "invalid_request"
        assert svc.search(query="？？？。").returned_k == 0  # 无可检索词元 → 空结果


class TestFreshness:
    """§4.4：写成功即可被搜索命中（同事务索引）。"""

    def test_write_then_search_immediately(self, svc):
        e = add(svc, "新鲜度条目 nu")
        assert result_ids(svc.search(query="新鲜度")) == [e.id]
        svc.revise(e.id, expected_revision=1, author="a", body="新鲜度条目改为 xi")
        assert result_ids(svc.search(query="xi")) == [e.id]
        assert svc.search(query="nu").returned_k == 0  # 旧正文不再命中

    def test_chinese_word_segmentation_recall(self, svc):
        # jieba 分词下的中文词命中（采定行为回归锚）
        e = add(svc, "重建索引前必须停服务，否则文件锁冲突")
        assert result_ids(svc.search(query="重建索引 停服务")) == [e.id]
        assert result_ids(svc.search(query="文件锁")) == [e.id]

    def test_fts_special_chars_safe(self, svc):
        e = add(svc, "含引号与括号的正文 (beta) \"quoted\"")
        assert result_ids(svc.search(query='(beta) "quoted"')) == [e.id]

    def test_concurrent_write_search_no_busy(self, svc):
        add(svc, "并发锚点 omega")

        def writer(i):
            svc.create(type="memory", body=f"并发写入条目 omega {i}",
                       scope={"kind": "global"}, author=f"w{i}")

        threads = [threading.Thread(target=writer, args=(i,)) for i in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert svc.search(query="omega 并发", limit=10).returned_k >= 4


ID_RE = re.compile(r"^entry_[0-9ABCDEFGHJKMNPQRSTVWXYZ]{26}$")


def test_tokenizer_jieba_adopted(store):
    tokens = store._tokenize("SQLite WAL 模式下读写并发，busy_timeout 兜底")
    assert "sqlite" in tokens and "wal" in tokens and "模式" in tokens and "并发" in tokens
    assert "，" not in tokens.split()
