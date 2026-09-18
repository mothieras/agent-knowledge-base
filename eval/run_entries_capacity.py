"""条目服务容量/并发/重启实测（PHASE2 §6.4/§7.5，T5）。

种子 1 万 active 条目 × 10 修订（进程内直写，10 万修订行）→ 起真实服务
（禁生成模型）→ 无模型核心流程全链 → 3 并发写（断言 0 busy / 0 错误）→
搜索延迟（门槛 P95 < 500ms）→ 重启数据保留（SIGTERM → 同库重启）→
报告写 eval/reports/，绑定 git commit。

运行: cd eval && env -u ALL_PROXY -u all_proxy ../.venv/bin/python run_entries_capacity.py
产出: eval/reports/entries-capacity-<date>.md / .json；artifacts 不进版本库
"""
from __future__ import annotations

import json
import os
import signal
import statistics
import subprocess
import sys
import threading
import time
from datetime import date
from pathlib import Path

import httpx

EVAL_DIR = Path(__file__).resolve().parent
ROOT = EVAL_DIR.parent
sys.path.insert(0, str(ROOT / "src"))

ARTIFACTS = EVAL_DIR / "entries_capacity_artifacts"
REPORTS = EVAL_DIR / "reports"
HOST, PORT = "127.0.0.1", 8902
BASE_URL = f"http://{HOST}:{PORT}"
SERVER_READY_TIMEOUT_S = 300

N_ENTRIES = 10_000          # §6.4：1 万 active
REVISIONS_PER = 10          # 1 create + 9 revise = 10 万修订行
WRITERS = 3                 # §6.4：3 并发写（= 写槽位数，不应 busy）
WRITER_ROUNDS = 13          # 每轮 create + revise×2；另加首条目 archive/unarchive
SEARCH_P95_LIMIT_MS = 500.0  # §6.4 门槛
TOPICS = ["检索", "分块", "评测", "向量化", "嵌入", "缓存", "并发", "事务", "索引", "备份",
          "鉴权", "路由", "解析", "排序", "过滤", "聚合", "压缩", "校验", "重试", "限流"]


def git_commit() -> str:
    try:
        return subprocess.run(["git", "-C", str(ROOT), "rev-parse", "HEAD"],
                              capture_output=True, text=True, check=True).stdout.strip()
    except Exception:
        return "n/a"


def pct(xs: list[float], p: float) -> float:
    s = sorted(xs)
    return s[min(len(s) - 1, max(0, round(len(s) * p) - 1))] if s else 0.0


def seed_body(i: int, rev: int = 0) -> str:
    topic = TOPICS[i % len(TOPICS)]
    body = (f"容量基准条目 {i}：{topic} 领域共享记忆，{topic} 实践要点 {i % 97}，"
            f"边界讨论 {i % 31}。")
    if rev:
        body += f" 第 {rev} 轮修订补充 {TOPICS[(i + rev) % len(TOPICS)]} 关联。"
    if i % 10 == 0:
        body += f" 稀有标记{i}。"
    return body


def seed() -> dict:
    """进程内直写 1 万条目 × 10 修订；返回样本与计数。"""
    from core.entry_service import EntryService
    from db.entry_store import EntryStore

    db_path = ARTIFACTS / "entries.db"
    if db_path.exists():
        db_path.unlink()
    svc = EntryService(EntryStore(str(db_path)))
    t0 = time.perf_counter()
    samples = {}
    for i in range(N_ENTRIES):
        e = svc.create(type="memory" if i % 2 else "knowledge", body=seed_body(i),
                       scope={"kind": "global"}, author="capacity-seed/1.0")
        for r in range(1, REVISIONS_PER):
            svc.revise(e.id, expected_revision=r, author="capacity-seed/1.0",
                       body=seed_body(i, r))
        if i in (0, N_ENTRIES // 2, N_ENTRIES - 1):
            got = svc.get(e.id)
            samples[e.id] = {"body": got.body, "revision": got.revision,
                             "status": got.status, "history_len": len(svc.history(e.id))}
        if (i + 1) % 2000 == 0:
            print(f"[seed] {i + 1}/{N_ENTRIES} 条目（{time.perf_counter() - t0:.0f}s）")
    svc._store.close()
    print(f"[seed] 完成：{N_ENTRIES} 条目 × {REVISIONS_PER} 修订，{time.perf_counter() - t0:.0f}s")
    return {"samples": samples, "seed_seconds": round(time.perf_counter() - t0, 1)}


def boot_server(log_file) -> subprocess.Popen:
    env = os.environ.copy()
    env.update({
        "ENTRIES_DB_PATH": str(ARTIFACTS / "entries.db"),
        "API_HOST": HOST, "API_PORT": str(PORT),
        "DEEPSEEK_API_KEY": "",  # 禁生成模型：核心流程不依赖模型（§1）
        "HF_HUB_OFFLINE": "1",
    })
    env.pop("DEMO_API_TOKEN", None)
    env.pop("HF_ENDPOINT", None)
    for k in ("ALL_PROXY", "all_proxy", "HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
        env.pop(k, None)
    return subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "api.main:app", "--host", HOST, "--port", str(PORT)],
        cwd=str(ROOT / "src"), env=env, stdout=log_file, stderr=subprocess.STDOUT)


def wait_ready(client: httpx.Client, server: subprocess.Popen) -> dict:
    t0 = time.time()
    while time.time() - t0 < SERVER_READY_TIMEOUT_S:
        if server.poll() is not None:
            sys.exit(f"错误: 服务提前退出 (code={server.returncode})，见 {ARTIFACTS / 'server.log'}")
        try:
            r = client.get(f"{BASE_URL}/health")
            if r.status_code == 200:
                return r.json()
        except httpx.HTTPError:
            pass
        time.sleep(1.0)
    sys.exit(f"错误: 服务 {SERVER_READY_TIMEOUT_S}s 内未 ready")


def stop_server(server: subprocess.Popen) -> None:
    server.send_signal(signal.SIGTERM)
    try:
        server.wait(timeout=30)
    except subprocess.TimeoutExpired:
        server.kill()
        server.wait()


def create_entry(client: httpx.Client, body: str, author: str) -> dict:
    r = client.post(f"{BASE_URL}/entries", json={
        "type": "memory", "body": body, "scope": {"kind": "global"}, "author": author})
    assert r.status_code == 201, f"create {r.status_code}: {r.text[:200]}"
    return r.json()


def core_flow(client: httpx.Client) -> dict:
    """禁模型下核心流程全链：写→搜索→读→修订→409 冲突体→历史→按修订回查→生命周期→显式读。"""
    e = create_entry(client, "核心流程条目：写读修订搜索回查全链验证", "capacity-core/1.0")
    eid = e["id"]
    assert client.post(f"{BASE_URL}/entries/search",
                       json={"query": "核心流程 全链"}).json()["returned_k"] == 1
    assert client.get(f"{BASE_URL}/entries/{eid}").json()["revision"] == 1
    r = client.post(f"{BASE_URL}/entries/{eid}/revisions",
                    json={"expected_revision": 1, "author": "capacity-core/1.0",
                          "body": "核心流程条目：二版正文"})
    assert r.status_code == 200 and r.json()["revision"] == 2
    stale = client.post(f"{BASE_URL}/entries/{eid}/revisions",
                        json={"expected_revision": 1, "author": "capacity-core/1.0",
                              "body": "过期修订"})
    assert stale.status_code == 409
    assert stale.json()["detail"]["code"] == "revision_conflict"
    assert stale.json()["detail"]["current"]["revision"] == 2
    history = client.get(f"{BASE_URL}/entries/{eid}/revisions").json()
    assert [(h["revision"], h["op"]) for h in history] == [(1, "create"), (2, "update")]
    snap = client.get(f"{BASE_URL}/entries/{eid}/revisions/1").json()
    assert snap["body"] == "核心流程条目：写读修订搜索回查全链验证"
    for op in ("archive", "unarchive"):
        r = client.post(f"{BASE_URL}/entries/{eid}/lifecycle",
                        json={"op": op, "expected_revision": 2 if op == "archive" else 3,
                              "author": "capacity-core/1.0"})
        assert r.status_code == 200
    assert client.get(f"{BASE_URL}/entries/{eid}").json()["status"] == "active"
    return {"entry_id": eid, "ops": 4}  # create + revise + archive + unarchive = 4 修订


def concurrent_writes(client_factory, counters: dict) -> dict:
    """3 线程并发写（各持独立条目，无冲突）；断言 0 busy / 0 错误。"""
    barrier = threading.Barrier(WRITERS)
    per_thread: list[dict] = [{} for _ in range(WRITERS)]

    def worker(w: int):
        c = client_factory()
        latencies, busy, errors = [], 0, 0
        barrier.wait()
        first_id = None
        for i in range(WRITER_ROUNDS):
            body = f"并发写入条目 w{w}-{i}：并发写验证正文 {w}{i}"
            t0 = time.perf_counter()
            e = create_entry(c, body, f"capacity-w{w}/1.0")
            latencies.append(time.perf_counter() - t0)
            if first_id is None:
                first_id = e["id"]
            for rev, new_body in ((1, body + " 二版"), (2, body + " 三版")):
                t0 = time.perf_counter()
                r = c.post(f"{BASE_URL}/entries/{e['id']}/revisions",
                           json={"expected_revision": rev, "author": f"capacity-w{w}/1.0",
                                 "body": new_body})
                latencies.append(time.perf_counter() - t0)
                if r.status_code == 429:
                    busy += 1
                elif r.status_code != 200:
                    errors += 1
        for op, rev in (("archive", 3), ("unarchive", 4)):
            t0 = time.perf_counter()
            r = c.post(f"{BASE_URL}/entries/{first_id}/lifecycle",
                       json={"op": op, "expected_revision": rev, "author": f"capacity-w{w}/1.0"})
            latencies.append(time.perf_counter() - t0)
            if r.status_code == 429:
                busy += 1
            elif r.status_code != 200:
                errors += 1
        c.close()
        per_thread[w] = {"latencies": latencies, "busy": busy, "errors": errors}

    threads = [threading.Thread(target=worker, args=(w,)) for w in range(WRITERS)]
    t0 = time.perf_counter()
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    wall = time.perf_counter() - t0

    lat = [x for d in per_thread for x in d["latencies"]]
    busy = sum(d["busy"] for d in per_thread)
    errors = sum(d["errors"] for d in per_thread)
    counters["entries"] += WRITERS * WRITER_ROUNDS
    counters["revisions"] += WRITERS * (WRITER_ROUNDS * 3 + 2)
    return {"n": len(lat), "busy": busy, "errors": errors, "wall_s": round(wall, 2),
            "p50_ms": round(pct(lat, 0.50) * 1000, 1), "p95_ms": round(pct(lat, 0.95) * 1000, 1),
            "mean_ms": round(statistics.fmean(lat) * 1000, 1)}


def search_latency(client: httpx.Client) -> tuple[dict, list[dict], list[dict]]:
    """90 题：30 稀有精确命中 + 30 常用宽命中 + 30 未命中；门槛 P95 < 500ms。"""
    queries = (
        [{"kind": "rare", "query": f"稀有标记{i}", "expect_top": seed_body(i, REVISIONS_PER - 1)}
         for i in range(0, 30 * 330, 330)]
        + [{"kind": "common", "query": f"{t} 领域共享记忆"}
           for t in TOPICS[:20]]
        + [{"kind": "common", "query": f"{t} 实践要点"}
           for t in TOPICS[:10]]
        + [{"kind": "miss", "query": f"未命中干扰词 {k} qqzz"}
           for k in range(30)]
    )
    rows = []
    for q in queries:
        t0 = time.perf_counter()
        r = client.post(f"{BASE_URL}/entries/search", json={"query": q["query"]})
        ms = (time.perf_counter() - t0) * 1000
        assert r.status_code == 200, r.text[:200]
        body = r.json()
        rows.append({"kind": q["kind"], "query": q["query"], "latency_ms": round(ms, 1),
                     "returned_k": body["returned_k"],
                     "top_body": body["results"][0]["body"] if body["results"] else None})
    by = {}
    for kind in ("rare", "common", "miss"):
        lat = [r["latency_ms"] for r in rows if r["kind"] == kind]
        by[kind] = {"n": len(lat), "p50_ms": round(pct(lat, 0.50), 1),
                    "p95_ms": round(pct(lat, 0.95), 1),
                    "mean_ms": round(statistics.fmean(lat), 1)}
    lat_all = [r["latency_ms"] for r in rows]
    summary = {"by_kind": by, "p95_ms": round(pct(lat_all, 0.95), 1),
               "p50_ms": round(pct(lat_all, 0.50), 1),
               "mean_ms": round(statistics.fmean(lat_all), 1)}
    # 语义 sanity（OR+bm25：候选集含同 token 条目，精确命中看首位）：稀有题首位
    # 恰为目标条目、未命中题 0 命中、常用题 ≥1 命中
    bad = [r for r, q in zip(rows, queries)
           if r["kind"] == "rare" and r["top_body"] != q["expect_top"]]
    bad += [r for r in rows if r["kind"] == "miss" and r["returned_k"] != 0]
    bad += [r for r in rows if r["kind"] == "common" and r["returned_k"] < 1]
    return summary, rows, bad


def sqlite_counts() -> dict:
    import sqlite3
    conn = sqlite3.connect(str(ARTIFACTS / "entries.db"))
    try:
        entries = conn.execute("SELECT COUNT(*) FROM entries").fetchone()[0]
        revisions = conn.execute("SELECT COUNT(*) FROM revisions").fetchone()[0]
        active = conn.execute("SELECT COUNT(*) FROM entries WHERE status = 'active'").fetchone()[0]
        return {"entries": entries, "revisions": revisions, "active": active}
    finally:
        conn.close()


def restart_check(seed_meta: dict, core_meta: dict, client_factory,
                  server_log) -> dict:
    """SIGTERM 停机 → 同库重启 → 数据保留验证。"""
    counts = sqlite_counts()  # 停机后、重启前：行数直接对账
    with open(server_log, "a", encoding="utf-8") as log_file:
        server = boot_server(log_file)
    try:
        with client_factory() as client:
            health = wait_ready(client, server)
            assert health["entries"] == "ready", health
            ok = True
            for eid, expect in seed_meta["samples"].items():
                got = client.get(f"{BASE_URL}/entries/{eid}").json()
                if (got["body"], got["revision"], got["status"]) != (
                        expect["body"], expect["revision"], expect["status"]):
                    ok = False
                hist = client.get(f"{BASE_URL}/entries/{eid}/revisions").json()
                if len(hist) != expect["history_len"]:
                    ok = False
            core = client.get(f"{BASE_URL}/entries/{core_meta['entry_id']}").json()
            rare = client.post(f"{BASE_URL}/entries/search",
                               json={"query": "稀有标记3300"}).json()
            rare_ok = (rare["returned_k"] >= 1
                       and rare["results"][0]["body"] == seed_body(3300, REVISIONS_PER - 1))
            return {"rows": counts, "samples_intact": ok, "core_intact": core["status"] == "active",
                    "rare_search_intact": rare_ok}
    finally:
        stop_server(server)


def write_report(metrics: dict) -> None:
    today = date.today().isoformat()
    md = f"""# 条目服务容量/并发/重启实测 {today}（PHASE2 §6.4，T5）

## 可复现绑定

| 项 | 值 |
|---|---|
| git commit | `{metrics['commit']}` |
| 种子 | {N_ENTRIES} active 条目 × {REVISIONS_PER} 修订（{N_ENTRIES * REVISIONS_PER:,} 修订行），进程内 EntryService 直写 |
| 服务 | 单进程 uvicorn（api.main:app），DEEPSEEK_API_KEY 置空（生成禁用）；检索栈仅随 lifespan 引导加载，未参与测量 |
| 机器 | {metrics['machine']}（本机实测，非受控环境，数字如实记录） |

## §6.4 锚点对照

| 锚点 | 目标 | 实测 |
|---|---|---|
| active 条目 | 1 万 | {metrics['rows']['active']:,} |
| 修订行 | 10 万 | {metrics['rows']['revisions']:,} |
| 3 并发写 busy | 0 | {metrics['writes']['busy']}（{metrics['writes']['n']} 写全 2xx，错误 {metrics['writes']['errors']}）|
| 搜索 P95 | < {SEARCH_P95_LIMIT_MS:.0f}ms | {metrics['search']['p95_ms']}ms |

## 写入延迟（3 并发，HTTP 客户端观测）

| n | P50 (ms) | P95 (ms) | mean (ms) | wall (s) |
|---:|---:|---:|---:|---:|
| {metrics['writes']['n']} | {metrics['writes']['p50_ms']} | {metrics['writes']['p95_ms']} | {metrics['writes']['mean_ms']} | {metrics['writes']['wall_s']} |

## 搜索延迟（90 题：30 稀有精确 / 30 常用宽命中 / 30 未命中）

| 查询类 | n | P50 (ms) | P95 (ms) | mean (ms) |
|---|---:|---:|---:|---:|
| 稀有精确 | {metrics['search']['by_kind']['rare']['n']} | {metrics['search']['by_kind']['rare']['p50_ms']} | {metrics['search']['by_kind']['rare']['p95_ms']} | {metrics['search']['by_kind']['rare']['mean_ms']} |
| 常用宽命中 | {metrics['search']['by_kind']['common']['n']} | {metrics['search']['by_kind']['common']['p50_ms']} | {metrics['search']['by_kind']['common']['p95_ms']} | {metrics['search']['by_kind']['common']['mean_ms']} |
| 未命中 | {metrics['search']['by_kind']['miss']['n']} | {metrics['search']['by_kind']['miss']['p50_ms']} | {metrics['search']['by_kind']['miss']['p95_ms']} | {metrics['search']['by_kind']['miss']['mean_ms']} |
| 合计 | 90 | {metrics['search']['p50_ms']} | {metrics['search']['p95_ms']} | {metrics['search']['mean_ms']} |

语义 sanity（OR+bm25，首位=精确命中）：稀有题首位恰为目标条目、未命中题 0 命中、常用题 ≥1 命中——异常 {len(metrics['search_bad'])} 题。

## 无模型核心流程

/health = `{metrics['health']}`；写 → 搜索 → 读 → 修订 → 409 冲突体（附当前条目）→ 历史 → 按修订回查 → archive/unarchive → 显式读，全链通过（核心过程零模型调用）。

## 重启数据保留

SIGTERM 停机 → 同库重启：样本条目（首/中/尾）状态与修订历史逐字一致 {metrics['restart']['samples_intact']}；核心流程条目保留 {metrics['restart']['core_intact']}；稀有搜索精确命中 {metrics['restart']['rare_search_intact']}；停机时点行数 entries={metrics['rows']['entries']:,} / revisions={metrics['rows']['revisions']:,}。

## 口径与局限

- 种子经进程内 EntryService 直写（排除网络因素）；并发写与搜索为 HTTP 客户端观测（含回环网络与 JSON 序列化），非纯服务端处理时间
- 条目存储为单连接 SQLite（WAL + busy_timeout 5s，RLock 进程内串行写）；并发写延迟含槽位获取
- 未测多 worker/远程部署；写吞吐未设锚点（§6.4 仅约束 busy 与搜索 P95），种子速度仅记录：{N_ENTRIES * REVISIONS_PER:,} 修订 {metrics['seed_seconds']}s
"""
    (REPORTS / f"entries-capacity-{today}.md").write_text(md, encoding="utf-8")
    metrics["report"] = f"eval/reports/entries-capacity-{today}.md"
    (REPORTS / f"entries-capacity-{today}.json").write_text(
        json.dumps(metrics, ensure_ascii=False, indent=1), encoding="utf-8")


def main() -> int:
    ARTIFACTS.mkdir(exist_ok=True)
    counters = {"entries": N_ENTRIES, "revisions": N_ENTRIES * REVISIONS_PER}
    seed_meta = seed()

    server_log_path = ARTIFACTS / "server.log"
    with open(server_log_path, "w", encoding="utf-8") as log_file:
        server = boot_server(log_file)
    try:
        client_factory = lambda: httpx.Client(timeout=60.0)  # noqa: E731
        with client_factory() as client:
            health = wait_ready(client, server)
            assert health["generation"] == "disabled" and health["entries"] == "ready", health
            print(f"[boot] ready: {health}")

            core_meta = core_flow(client)
            counters["entries"] += 1
            counters["revisions"] += core_meta["ops"]
            print(f"[core] 无模型核心流程全链通过 ({core_meta['entry_id'][:20]}…)")

            writes = concurrent_writes(client_factory, counters)
            print(f"[write] 3 并发 {writes['n']} 写：busy={writes['busy']} errors={writes['errors']}"
                  f" P95={writes['p95_ms']}ms")
            assert writes["busy"] == 0 and writes["errors"] == 0, writes

            search, rows, bad = search_latency(client)
            print(f"[search] P95={search['p95_ms']}ms (门槛 {SEARCH_P95_LIMIT_MS:.0f}ms)，"
                  f"语义异常 {len(bad)} 题")
            assert search["p95_ms"] < SEARCH_P95_LIMIT_MS, search
    finally:
        stop_server(server)

    restart = restart_check(seed_meta, core_meta, client_factory, server_log_path)
    rows_now = sqlite_counts()
    counters["active"] = counters["entries"]  # 种子/核心/并发条目终态全为 active
    print(f"[restart] 行数 entries={rows_now['entries']:,} revisions={rows_now['revisions']:,}"
          f"（期望 {counters['entries']:,}/{counters['revisions']:,}）")
    assert rows_now == counters, (rows_now, counters)
    assert restart["samples_intact"] and restart["core_intact"] and restart["rare_search_intact"]

    write_report({
        "commit": git_commit(), "seed_seconds": seed_meta["seed_seconds"],
        "machine": f"{sys.platform}/{os.uname().machine}", "health": health,
        "writes": writes, "search": search, "search_rows": rows, "search_bad": bad,
        "rows": rows_now, "restart": restart, "counters": counters,
    })
    print(f"[done] 报告: {REPORTS / f'entries-capacity-{date.today().isoformat()}.md'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
