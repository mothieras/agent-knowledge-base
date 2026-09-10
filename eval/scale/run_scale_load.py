"""规模实测·3 并发只读负载：起服务 → warm → 串行基线 → 并发压测 → 报告。

针对 build_scale_snapshot.py 构建的合成规模快照（约 100 文档 / 1 万 child
chunks），以 3 并发（= AppService MAX_CONCURRENT_QUERIES 设计容量）执行
≥100 个有效只读查询（/search 与 /evidence 混合），记录：

- 串行基线 P50/P95 vs 3 并发 P50/P95（并发/串行延迟比 = 排队效应观测）
- busy（槽位等待超 30s）/ 其他错误计数
- 契约有效性：HTTP 200 且 content_hash 与内容自洽、span 有界
- 服务进程峰值 RSS（ps 采样，1s 间隔）

运行: cd eval && env -u ALL_PROXY -u all_proxy ../.venv/bin/python scale/run_scale_load.py
产出: eval/reports/scale-<date>.md / .json
"""
import asyncio
import hashlib
import json
import os
import platform
import statistics
import subprocess
import sys
import time
from datetime import date
from pathlib import Path

import httpx

EVAL_DIR = Path(__file__).resolve().parent.parent
REPO_ROOT = EVAL_DIR.parent
ARTIFACTS = EVAL_DIR / "scale_artifacts"
REPORTS = EVAL_DIR / "reports"
HOST = "127.0.0.1"
PORT = 8901
BASE_URL = f"http://{HOST}:{PORT}"
SERVER_READY_TIMEOUT_S = 300
CONCURRENCY = 3          # = AppService.MAX_CONCURRENT_QUERIES（设计容量边界）
N_WARMUP = 5
N_SEQ_BASELINE = 30
N_CONCURRENT_QUERIES = 105   # ≥100（PLAN 收尾一步）；3 worker × 35

sys.path.insert(0, str(REPO_ROOT))   # git_commit() 用


def git_commit() -> str:
    try:
        return subprocess.run(["git", "-C", str(REPO_ROOT), "rev-parse", "HEAD"],
                              capture_output=True, text=True, check=True).stdout.strip()
    except Exception:
        return "n/a"


def pct(xs, p):
    s = sorted(xs)
    return s[min(len(s) - 1, max(0, round(len(s) * p) - 1))] if s else 0.0


def rss_kb(pid: int) -> int:
    try:
        out = subprocess.run(["ps", "-o", "rss=", "-p", str(pid)],
                             capture_output=True, text=True, check=True).stdout
        return int(out.strip())
    except Exception:
        return 0


class RssSampler:
    """并发阶段 1s 间隔采样服务进程 RSS（KB）。"""

    def __init__(self, pid: int):
        self.pid = pid
        self.samples: list[int] = []
        self._stop = asyncio.Event()
        self._task = None

    async def _loop(self):
        while not self._stop.is_set():
            kb = await asyncio.to_thread(rss_kb, self.pid)
            if kb:
                self.samples.append(kb)
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=1.0)
            except TimeoutError:
                pass

    async def __aenter__(self):
        self._task = asyncio.create_task(self._loop())
        return self

    async def __aexit__(self, *exc):
        self._stop.set()
        if self._task:
            await self._task


def verify_search(resp_json: dict) -> tuple[bool, str]:
    """契约有效性：返回 (ok, 备注)。content_hash 与内容自洽、span 有界。"""
    if not str(resp_json.get("index_id", "")).startswith("sha256:"):
        return False, "index_id 缺失"
    for h in resp_json.get("hits", []):
        if h.get("content_hash") != "sha256:" + hashlib.sha256(
                h.get("content", "").encode("utf-8")).hexdigest():
            return False, f"hit content_hash 不符 ({h.get('evidence_id')})"
        span = h.get("span") or {}
        if not (0 <= span.get("start", -1) < span.get("end", -1)
                and span["end"] - span["start"] == len(h.get("content", ""))):
            return False, f"hit span 有界性失败 ({h.get('evidence_id')})"
    return True, ""


def verify_evidence(resp_json: dict) -> tuple[bool, str]:
    if resp_json.get("content_hash") != "sha256:" + hashlib.sha256(
            resp_json.get("content", "").encode("utf-8")).hexdigest():
        return False, "evidence content_hash 不符"
    return True, ""


async def run_queries(client: httpx.AsyncClient, queries: list[dict], *,
                      record_evidence: bool, results: list, worker_hint: int = 1):
    """串行/并发共用的执行体：每第 5 个命中型 search 追加一次 /evidence 回查。"""
    for i, q in enumerate(queries):
        t0 = time.perf_counter()
        try:
            r = await client.post(f"{BASE_URL}/search",
                                  json={"query": q["query"], "k": 7})
            latency = time.perf_counter() - t0
            if r.status_code != 200:
                results.append({"id": q["id"], "kind": q["kind"], "endpoint": "search",
                                "status": r.status_code, "latency_s": round(latency, 3),
                                "valid": False, "note": r.text[:120]})
                continue
            ok, note = verify_search(r.json())
            hits = r.json().get("hits", [])
            results.append({"id": q["id"], "kind": q["kind"], "endpoint": "search",
                            "status": 200, "latency_s": round(latency, 3),
                            "returned_k": r.json()["returned_k"],
                            "truncated": r.json()["truncated"], "valid": ok, "note": note})
            if record_evidence and ok and hits and i % 5 == 0:
                ev = hits[0]["evidence_id"]
                t1 = time.perf_counter()
                er = await client.get(f"{BASE_URL}/evidence/{ev}",
                                      params={"offset": 0, "limit": 4000})
                e_latency = time.perf_counter() - t1
                if er.status_code == 200:
                    e_ok, e_note = verify_evidence(er.json())
                else:
                    e_ok, e_note = False, er.text[:120]
                results.append({"id": f"{q['id']}-ev", "kind": q["kind"], "endpoint": "evidence",
                                "status": er.status_code, "latency_s": round(e_latency, 3),
                                "valid": e_ok, "note": e_note})
        except Exception as exc:
            latency = time.perf_counter() - t0
            results.append({"id": q["id"], "kind": q["kind"], "endpoint": "search",
                            "status": -1, "latency_s": round(latency, 3),
                            "valid": False, "note": f"{type(exc).__name__}: {exc}"})


async def main() -> int:
    meta = json.loads((ARTIFACTS / "build_meta.json").read_text(encoding="utf-8"))
    queries = json.loads((ARTIFACTS / "queries.json").read_text(encoding="utf-8"))
    today = date.today().isoformat()
    server_log_path = ARTIFACTS / "server.log"

    env = os.environ.copy()
    env.update({
        "QDRANT_DB_PATH": str(ARTIFACTS / "qdrant_db"),
        "PARENT_STORE_PATH": str(ARTIFACTS / "parent_store"),
        "API_HOST": HOST, "API_PORT": str(PORT),
        # 模型已缓存；离线模式跳过 HF 在线校验（无代理环境下 HEAD 重试会挂住启动）
        "HF_HUB_OFFLINE": "1",
    })
    env.pop("DEMO_API_TOKEN", None)  # 压测聚焦容量，鉴权由 Pi 端到端覆盖
    env.pop("HF_ENDPOINT", None)
    for k in ("ALL_PROXY", "all_proxy", "HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
        env.pop(k, None)

    print(f"[server] 启动 uvicorn @ {BASE_URL}（合成快照 index={meta['index_id'][:16]}…）")
    log_file = open(server_log_path, "w", encoding="utf-8")
    server = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "api.main:app", "--host", HOST, "--port", str(PORT)],
        cwd=str(REPO_ROOT / "src"), env=env, stdout=log_file, stderr=subprocess.STDOUT)
    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            t0 = time.time()
            ready = False
            while time.time() - t0 < SERVER_READY_TIMEOUT_S:
                if server.poll() is not None:
                    print(f"错误: 服务进程提前退出 (code={server.returncode})，见 {server_log_path}",
                          file=sys.stderr)
                    return 1
                try:
                    if (await client.get(f"{BASE_URL}/health")).status_code == 200:
                        ready = True
                        break
                    await asyncio.sleep(1.0)
                except httpx.HTTPError:
                    await asyncio.sleep(1.0)
            if not ready:
                print(f"错误: 服务 {SERVER_READY_TIMEOUT_S}s 内未 ready，见 {server_log_path}",
                      file=sys.stderr)
                return 1
            info = (await client.get(f"{BASE_URL}/info")).json()
            print(f"[server] ready in {time.time() - t0:.1f}s index={info['index_id'][:16]}… "
                  f"points={meta['child_count']}")
            info_index = info["index_id"]
            rss_after_ready = rss_kb(server.pid)

            # warm：前 N 条命中型查询，预热嵌入与检索路径（不计入结果）
            warm = queries[:N_WARMUP]
            await run_queries(client, warm, record_evidence=False, results=[])

            # 串行基线（排队效应对照）
            seq_queries = [q for q in queries if q["kind"] == "hit"][:N_SEQ_BASELINE]
            seq_results: list[dict] = []
            t_seq = time.perf_counter()
            await run_queries(client, seq_queries, record_evidence=True, results=seq_results)
            seq_wall = time.perf_counter() - t_seq

            # 3 并发压测（设计容量边界）：105 个查询，3 worker 均分
            conc_queries = queries[:N_CONCURRENT_QUERIES]
            chunks = [conc_queries[i::CONCURRENCY] for i in range(CONCURRENCY)]
            conc_results: list[dict] = []
            async with RssSampler(server.pid) as sampler:
                t_conc = time.perf_counter()
                await asyncio.gather(*[
                    run_queries(client, chunk, record_evidence=True, results=conc_results)
                    for chunk in chunks])
                conc_wall = time.perf_counter() - t_conc
            peak_rss_kb = max(sampler.samples) if sampler.samples else 0
            conc_rss_kb = rss_kb(server.pid)
    finally:
        server.terminate()
        try:
            server.wait(timeout=15)
        except subprocess.TimeoutExpired:
            server.kill()
        log_file.close()

    def phase_stats(results: list[dict]) -> dict:
        lat = [r["latency_s"] for r in results]
        valid = [r for r in results if r["valid"]]
        busy = [r for r in results if r.get("note") and "busy" in r.get("note", "")]
        return {
            "n": len(results), "n_valid": len(valid),
            "valid_rate": round(len(valid) / len(results), 4) if results else None,
            "latency_p50_s": round(pct(lat, 0.5), 3) if lat else None,
            "latency_p95_s": round(pct(lat, 0.95), 3) if lat else None,
            "latency_mean_s": round(statistics.mean(lat), 3) if lat else None,
            "errors": sum(1 for r in results if r["status"] != 200 or not r["valid"]),
            "busy_count": len(busy),
        }

    seq_stats = phase_stats(seq_results)
    conc_stats = phase_stats(conc_results)
    endpoint_split = {e: sum(1 for r in conc_results if r["endpoint"] == e)
                      for e in ("search", "evidence")}
    machine = f"{platform.system()} {platform.release()} / {os.cpu_count()} cores"
    doc = {
        "date": today,
        "git_commit": git_commit(),
        "machine": machine,
        "corpus": {"docs": meta["n_docs"], "parent_chunks": meta["parent_count"],
                   "child_chunks": meta["child_count"], "corpus_chars": meta["corpus_chars"],
                   "seed": meta["seed"], "ingest_seconds": meta["ingest_seconds"],
                   "dense_model": meta["dense_model"], "sparse_model": meta["sparse_model"],
                   "index_id": meta["index_id"]},
        "server": {"index_id": info_index, "rss_after_ready_kb": rss_after_ready,
                   "rss_after_conc_kb": conc_rss_kb, "peak_rss_kb": peak_rss_kb,
                   "concurrency_slots": 3},
        "sequential_baseline": {**seq_stats, "wall_s": round(seq_wall, 1)},
        "concurrent": {**conc_stats, "workers": CONCURRENCY, "wall_s": round(conc_wall, 1),
                       "endpoint_split": endpoint_split},
        "queueing": {
            "p50_ratio": (round(conc_stats["latency_p50_s"] / seq_stats["latency_p50_s"], 2)
                          if seq_stats["latency_p50_s"] else None),
            "p95_ratio": (round(conc_stats["latency_p95_s"] / seq_stats["latency_p95_s"], 2)
                          if seq_stats["latency_p95_s"] else None),
            "busy_count": conc_stats["busy_count"],
        },
    }

    capacity_targets = {"docs ~100": meta["n_docs"], "chunks ~10000": meta["child_count"],
                       "concurrency 3": CONCURRENCY,
                       "valid queries >= 100": conc_stats["n_valid"]}
    doc["server"]["rss_samples"] = len(sampler.samples)
    json_path = REPORTS / f"scale-{today}.json"
    json_path.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")
    md_path = REPORTS / f"scale-{today}.md"
    md_path.write_text(render_md(doc), encoding="utf-8")
    print(f"\n[report] {md_path}")
    print(f"[json] {json_path}")
    print(json.dumps({"capacity": capacity_targets, "sequential_baseline": seq_stats,
                      "concurrent": conc_stats, "queueing": doc["queueing"],
                      "peak_rss_kb": peak_rss_kb}, ensure_ascii=False, indent=2))
    return 0


def render_md(doc: dict) -> str:
    """规模实测报告 markdown；doc 即 <reports>/scale-<date>.json 的内容（可重放渲染）。"""
    c, s = doc["corpus"], doc["server"]
    seq, conc = doc["sequential_baseline"], doc["concurrent"]
    q = doc["queueing"]
    split = conc["endpoint_split"]
    lines = [
        f"# 规模与并发实测 {doc['date']}（合成快照，3 并发只读）",
        "",
        "## 可复现绑定",
        "",
        "| 项 | 值 |",
        "|---|---|",
        f"| git commit | `{doc['git_commit']}` |",
        f"| 合成语料 | {c['docs']} 文档 / {c['corpus_chars'] / 1e6:.2f} MB（seed={c['seed']}，生成器 scale/build_scale_snapshot.py）|",
        f"| 索引 | parent {c['parent_chunks']} / child {c['child_chunks']} chunks，index_id `{c['index_id'][:24]}…` |",
        f"| 模型 | dense {c['dense_model']} + sparse {c['sparse_model']}（入库耗时 {c['ingest_seconds']}s）|",
        f"| 服务 | 单进程 uvicorn（FastAPI），并发槽位 {s['concurrency_slots']}（AppService.MAX_CONCURRENT_QUERIES）|",
        f"| 机器 | {doc['machine']}（本机实测，非受控环境，数字如实记录）|",
        "",
        "## 容量目标对照（acceptance.yaml capacity）",
        "",
        "| 目标 | 实测 |",
        "|---|---|",
        f"| 文档约 100 | {c['docs']} |",
        f"| child chunks 约 1 万 | {c['child_chunks']} |",
        f"| 并发 3 | {conc['workers']} worker |",
        f"| 有效只读查询 ≥100 | {conc['n_valid']}/{conc['n']}（{split['search']} search + {split['evidence']} evidence）|",
        "",
        "## 延迟（客户端观测，含网络往返）",
        "",
        "| 阶段 | n | P50 (s) | P95 (s) | mean (s) | wall (s) | 错误 | busy |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
        f"| 串行基线 | {seq['n']} | {seq['latency_p50_s']} | {seq['latency_p95_s']} "
        f"| {seq['latency_mean_s']} | {seq['wall_s']} | {seq['errors']} | {seq['busy_count']} |",
        f"| 3 并发 | {conc['n']} | {conc['latency_p50_s']} | {conc['latency_p95_s']} "
        f"| {conc['latency_mean_s']} | {conc['wall_s']} | {conc['errors']} | {conc['busy_count']} |",
        "",
        "## 排队与资源",
        "",
        f"- 并发/串行 P50 延迟比 {q['p50_ratio']}，P95 比 {q['p95_ratio']}；"
        f"busy（槽位等待超 30s）{q['busy_count']} 次",
        f"- 服务进程 RSS：ready 后 {s['rss_after_ready_kb'] / 1024:.0f} MB → 并发后 {s['rss_after_conc_kb'] / 1024:.0f} MB，"
        f"并发阶段峰值 {s['peak_rss_kb'] / 1024:.0f} MB（ps 采样 1s 间隔，{s.get('rss_samples', 'n/a')} 样本）",
        f"- 契约有效性：并发阶段 {conc['n_valid']}/{conc['n']} 通过"
        f"（HTTP 200 + content_hash 自洽 + span 有界，逐条客户端复算）",
        "",
        "## 口径与局限",
        "",
        "- 延迟为客户端测量（含本机回环网络与 JSON 序列化），非纯服务端处理时间",
        "- 合成语料为模板句，仅测规模/并发行为，不代表质量语料的检索效果",
        "- 单进程 uvicorn、本机磁盘 Qdrant embedded 模式；未测多 worker 与远程部署",
        "- 查询集构成：命中型（语料真实句子采样）与未命中型（站外干扰句）混合",
        f"- invalid 逐条备注见 scale-{doc['date']}.json",
        "",
    ]
    return "\n".join(lines)


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser()
    ap.add_argument("--from-json", dest="from_json", default=None,
                    help="不重跑压测，从既有 scale-<date>.json 重放渲染同名 .md")
    args = ap.parse_args()
    if args.from_json:
        doc = json.loads(Path(args.from_json).read_text(encoding="utf-8"))
        out = Path(args.from_json).with_suffix(".md")
        out.write_text(render_md(doc), encoding="utf-8")
        print(f"[report] {out}")
        sys.exit(0)
    sys.exit(asyncio.run(main()))