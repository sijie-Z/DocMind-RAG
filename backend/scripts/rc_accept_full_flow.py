"""RC 全流程验收：目标栈 = 本进程环境变量指向的 DATABASE_URL + 独立 ES 索引 + 运行中的 backend/worker。

覆盖（全部真实服务，不 seed 文档、不 mock 检索/LLM）：
注册 → 上传 3 份 .txt（真实解析+摘要+嵌入+索引）→ 原文预览/证据
→ REST 检索（O 派生内容）→ Chat SSE（真实 LLM 回答 + 来源 provenance）
→ 原文件下载 → 核验建案→分析→证据→决策→导出/下载→归档。

运行前置（启动期自检会逐项打印/校验）：
- 环境变量 DATABASE_URL 指向被测库；ELASTICSEARCH_INDEX_NAME 未显式设置时按库名派生
  （docmind → docmind_knowledge；docmind_rc → docmind_knowledge_rc），保证多库不共用索引；
- backend 可达（RC_BASE_URL 或 --base-url，默认 http://127.0.0.1:8020），且 /health 为 healthy；
- Kafka worker 与 backend 使用同一 DATABASE_URL/索引/主题并在运行（上传后的处理由 worker 完成）；
- Ollama/ES/MinIO/Redis/DeepSeek 可用。

注意：每次运行都会新建账号/文档/核验案件（固定验收口令），不做自动清理；
RC/测试库专用，禁止指向业务库。preflight 会拒绝索引身份不明的目标栈。

用法::

    RC_BASE_URL=http://127.0.0.1:8020 DATABASE_URL=... python scripts/rc_accept_full_flow.py
    python scripts/rc_accept_full_flow.py --base-url http://127.0.0.1:8020
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

import requests

# 按文档直跑 ``python scripts/rc_accept_full_flow.py`` 时，Python 只把
# ``scripts/`` 放进 sys.path；backend 根需显式加入，否则 ``app`` 导入必失败，
# preflight 会退化为只能依赖 --expect-index（连正确的本地栈也会被拒绝）。
_BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))

DEFAULT_BASE = os.environ.get("RC_BASE_URL", "http://127.0.0.1:8020")
BASE = DEFAULT_BASE
PASSWORD = "Rc-Accept-2026!pass"
FAILURES: list[str] = []


def step(name: str, ok: bool, extra: str = "") -> None:
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f" — {extra}" if extra else ""))
    if not ok:
        FAILURES.append(name)


def preflight(expect_index: str | None) -> bool:
    """启动自检：库/索引身份 + 目标 backend 健康。

    必须能证明「目标栈用的是哪个索引」：本地可导入配置时用当前生效索引名
    （显式优先，否则按库派生）；导入失败时必须显式传 --expect-index。目标
    ``/health`` 暴露的 ``knowledge_base.index_name`` 与期望值不一致或无法
    比对 → 直接拒绝，避免把验收数据写进错栈（脚本会创建账号/文档/案件）。
    """
    expected = expect_index
    try:
        from sqlalchemy.engine import make_url

        from app.core.config import settings
        from app.core.config.loader import _derive_es_index_name

        db = make_url(settings.DATABASE_URL).database
        derived = _derive_es_index_name(settings.DATABASE_URL)
        local_index = settings.ELASTICSEARCH_INDEX_NAME
        print(f"[preflight] 本地配置 db={db} index={local_index} derived={derived}")
        if expected is None:
            expected = local_index
    except Exception as exc:
        print(f"[preflight] 本地配置不可用（{type(exc).__name__}: {exc}）")
        if not expected:
            print(
                "[preflight] 无法确认真实索引身份：请在目标栈 backend 目录运行，或传 --expect-index"
            )
            return False

    try:
        r = requests.get(f"{BASE}/health", timeout=15)
    except Exception as exc:
        print(f"[preflight] backend 不可达 {BASE}: {type(exc).__name__}: {exc}")
        return False
    try:
        data = r.json() if r.status_code == 200 else {}
    except Exception:
        data = {}
    if not isinstance(data, dict):
        print(f"[preflight] /health 返回非对象 JSON（{type(data).__name__}），拒绝运行")
        return False
    kb = data.get("knowledge_base") or {}
    target_index = kb.get("index_name")
    print(
        f"[preflight] /health {r.status_code} status={data.get('status')} "
        f"target_index={target_index} expect_index={expected}"
    )
    if r.status_code != 200 or data.get("status") != "healthy":
        print(
            f"[preflight] 依赖不健康，先修复环境再跑验收：{json.dumps(data, ensure_ascii=False)[:500]}"
        )
        return False
    if not expected or not target_index:
        print(
            "[preflight] 无法比对目标索引名"
            "（缺少 expected 或 /health.knowledge_base.index_name），拒绝运行"
        )
        return False
    if target_index != expected:
        print(f"[preflight] 目标栈索引不匹配：期望 {expected}，实际 {target_index}；拒绝写入")
        return False
    print(
        "[preflight] worker 必须与 backend 使用同一 DATABASE_URL/索引/主题并在运行（上传后处理依赖它）"
    )
    return True


def register() -> dict:
    username = f"rcflow{int(time.time())}"
    r = requests.post(
        f"{BASE}/api/v1/auth/register",
        json={
            "username": username,
            "email": f"{username}@example.com",
            "password": PASSWORD,
            "full_name": "RC Full Flow",
        },
        timeout=30,
    )
    r.raise_for_status()
    data = r.json()["data"]
    return {
        "token": data["access_token"],
        "user_id": data["user_info"]["id"],
        "org_id": data["user_info"]["organization_id"],
    }


def headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def upload(token: str, filename: str, content: str, title: str) -> str:
    r = requests.post(
        f"{BASE}/api/v1/documents/upload",
        headers=headers(token),
        files={"file": (filename, content.encode("utf-8"), "text/plain")},
        data={"title": title, "description": "RC 全流程验收夹具"},
        timeout=60,
    )
    step(f"上传 {filename} -> {r.status_code}", r.status_code == 201, r.text[:200])
    if r.status_code != 201:
        return ""
    return r.json()["data"]["id"]


def wait_indexed(token: str, doc_id: str, filename: str, timeout: int = 240) -> dict:
    deadline = time.time() + timeout
    last = {}
    while time.time() < deadline:
        r = requests.get(f"{BASE}/api/v1/documents/{doc_id}", headers=headers(token), timeout=30)
        last = (r.json().get("data") or {}) if r.status_code == 200 else {}
        status = last.get("status")
        if status == "indexed":
            chunks = int(last.get("chunk_count") or 0)
            step(f"{filename} 解析/索引完成（chunks={chunks}）", chunks > 0)
            return last
        if status == "failed":
            step(f"{filename} 处理失败: {last.get('parse_error')}", False)
            return last
        time.sleep(4)
    step(f"{filename} 240s 内未完成索引（最后状态={last.get('status')}）", False, str(last)[:300])
    return last


def main() -> int:
    who = register()
    token = who["token"]
    org_id = who["org_id"]
    step("注册并取得个人组织", bool(token and org_id), f"org={org_id}")

    policy = (
        "采购管理办法（2026 版）\n"
        "第5条 付款条件：合同付款应经财务与法务复核，原则上验收合格后支付。\n"
        "第8条 例外：涉及预付款的采购须单独审批。\n"
        "第9条 资料归档：合同与验收单应归档保存不少于五年。\n"
    )
    base = (
        "采购合同（V1）\n"
        "第3条 付款期限：甲方应在验收合格后30日内支付货款。\n"
        "第4条 验收：乙方交付后10个工作日内完成验收。\n"
        "第7条 违约：逾期交付的，每延迟一日按合同金额千分之一支付违约金。\n"
    )
    revised = (
        "采购合同（V2）\n"
        "第3条 付款期限：甲方应在验收合格后60日内支付货款。\n"
        "第4条 验收：乙方交付后15个工作日内完成验收。\n"
        "第6条 例外：因不可抗力导致验收延迟的，付款期限相应顺延。\n"
        "第7条 违约：逾期交付的，每延迟一日按合同金额千分之二支付违约金。\n"
    )

    docs = {}
    for side, filename, content in (
        ("policy", "采购管理办法.txt", policy),
        ("base", "采购合同-V1.txt", base),
        ("revised", "采购合同-V2.txt", revised),
    ):
        doc_id = upload(token, filename, content, filename.replace(".txt", ""))
        if not doc_id:
            print("RESULT: FAIL — 上传未成功，后续中止")
            return 1
        docs[side] = {"id": doc_id, "content": content}
        wait_indexed(token, doc_id, filename)

    # 原文预览与证据（真实解析产物）
    for side in docs:
        r = requests.get(
            f"{BASE}/api/v1/documents/{docs[side]['id']}/content",
            headers=headers(token),
            timeout=30,
        )
        data = r.json().get("data") or {}
        step(
            f"{side} 全文预览为 O 且不冒充原文",
            r.status_code == 200
            and data.get("text_provenance") == "parser_original"
            and "【文档背景" not in (data.get("content") or ""),
            f"status={r.status_code} prov={data.get('text_provenance')}",
        )
        ev = requests.get(
            f"{BASE}/api/v1/documents/{docs[side]['id']}/evidence",
            headers=headers(token),
            params={"q": "付款"},
            timeout=30,
        )
        items = (ev.json().get("data") or {}).get("items") or []
        step(f"{side} 证据接口返回 parser 原文", ev.status_code == 200 and bool(items))
        if items:
            docs[side]["chunk_id"] = items[0]["chunk_id"]

    # 检索（REST）
    search = requests.post(
        f"{BASE}/api/v1/knowledge/search",
        headers=headers(token),
        json={"query": "付款期限 60 日", "organization_id": org_id, "top_k": 5},
        timeout=60,
    )
    results = (search.json().get("results") if search.status_code == 200 else []) or []
    hit_v2 = [r for r in results if "采购合同-V2" in (r.get("metadata", {}).get("filename") or "")]
    step(
        "REST 检索命中 V2 且展示内容来自 O",
        search.status_code == 200
        and bool(hit_v2)
        and hit_v2[0].get("text_provenance") == "parser_original"
        and "【文档背景" not in (hit_v2[0].get("content") or ""),
        f"status={search.status_code} hits={len(results)}",
    )

    # Chat SSE（真实 LLM + RAG）
    chat = requests.post(
        f"{BASE}/api/v1/chat/stream",
        headers=headers(token),
        json={"content": "《采购合同-V2》第3条约定的付款期限是多少天？请只依据文档回答。"},
        stream=True,
        timeout=180,
    )
    answer_parts: list[str] = []
    final_sources: list[dict] = []
    if chat.status_code == 200:
        event_name = ""
        for raw in chat.iter_lines(decode_unicode=True):
            if not raw:
                continue
            if raw.startswith("event:"):
                event_name = raw[6:].strip()
                continue
            if not raw.startswith("data:"):
                continue
            try:
                event = json.loads(raw[5:].strip())
            except json.JSONDecodeError:
                continue
            if event.get("sources"):
                final_sources = event["sources"]
            content = event.get("content") or ""
            if content and event_name in ("", "chunk", "message"):
                answer_parts.append(content)
    answer = "".join(answer_parts)
    step("Chat SSE 返回 200", chat.status_code == 200, str(chat.status_code))
    step("回答包含 V2 的 60 天口径", "60" in answer, answer[:200].replace("\n", " "))
    step(
        "回答来源含上传文档且 provenance 为 parser_original",
        any(
            "采购合同-V2" in (s.get("filename") or "")
            and (s.get("textProvenance") or s.get("text_provenance")) == "parser_original"
            for s in final_sources
        ),
        f"sources={len(final_sources)}",
    )

    # 原文件下载（预签名跳转）
    dl = requests.get(
        f"{BASE}/api/v1/documents/{docs['revised']['id']}/download",
        headers=headers(token),
        allow_redirects=False,
        timeout=30,
    )
    location = dl.headers.get("location") or ""
    downloaded_ok = False
    if dl.status_code in (302, 307) and location:
        blob = requests.get(location, timeout=60)
        downloaded_ok = blob.status_code == 200 and blob.content == revised.encode("utf-8")
    step("原文件下载内容与上传一致", downloaded_ok, f"dl={dl.status_code}")

    # 核验身份 + 建案
    metas = {
        "policy": {"doc_role": "policy"},
        "base": {
            "doc_role": "contract",
            "family_key": "rc-flow",
            "version_label": "V1",
            "version_seq": 1,
        },
        "revised": {
            "doc_role": "contract",
            "family_key": "rc-flow",
            "version_label": "V2",
            "version_seq": 2,
        },
    }
    for side, body in metas.items():
        r = requests.put(
            f"{BASE}/api/v1/documents/{docs[side]['id']}/review-meta",
            headers=headers(token),
            json=body,
            timeout=30,
        )
        step(f"{side} 登记核验身份 -> {r.status_code}", r.status_code == 200)
        ready = requests.get(
            f"{BASE}/api/v1/documents/{docs[side]['id']}/readiness",
            headers=headers(token),
            timeout=30,
        )
        step(
            f"{side} readiness=reviewable",
            ready.status_code == 200
            and (ready.json().get("data") or {}).get("status") == "reviewable",
            str((ready.json().get("data") or {}).get("blocking_reasons")),
        )

    created = requests.post(
        f"{BASE}/api/v1/reviews",
        headers=headers(token),
        json={
            "title": "RC 全流程核验：采购合同 V1→V2",
            "policy_document_id": docs["policy"]["id"],
            "base_document_id": docs["base"]["id"],
            "revised_document_id": docs["revised"]["id"],
        },
        timeout=60,
    )
    case_id = (created.json().get("data") or {}).get("id") if created.status_code == 200 else None
    step("真实文档建案成功", created.status_code == 200 and bool(case_id), created.text[:200])

    analyzed = requests.post(
        f"{BASE}/api/v1/reviews/{case_id}/analyze", headers=headers(token), timeout=60
    )
    step("确定性分析成功", analyzed.status_code == 200, analyzed.text[:200])
    detail = requests.get(
        f"{BASE}/api/v1/reviews/{case_id}", headers=headers(token), timeout=30
    ).json()["data"]
    facts_blob = json.dumps([i.get("facts") for i in detail["items"]], ensure_ascii=False)
    step("分析事实含 60/30 天口径", "60" in facts_blob and "30" in facts_blob)
    step("4 个核验项均有 change_type", all(i.get("change_type") for i in detail["items"]))

    items = detail["items"]
    ev = requests.put(
        f"{BASE}/api/v1/reviews/{case_id}/items/{items[0]['id']}/evidence",
        headers=headers(token),
        json={"side": "revised", "chunk_id": docs["revised"]["chunk_id"], "role": "main"},
        timeout=30,
    )
    step("证据选择接受真实 parser 分块", ev.status_code == 200, ev.text[:200])
    decisions_ok = True
    for item in items:
        resp = requests.put(
            f"{BASE}/api/v1/reviews/{case_id}/items/{item['id']}/decision",
            headers=headers(token),
            json={"status": "verified", "note": "RC 全流程验收"},
            timeout=30,
        )
        decisions_ok = decisions_ok and resp.status_code == 200
    step("全部决策写入成功", decisions_ok)
    exported = requests.post(
        f"{BASE}/api/v1/reviews/{case_id}/export", headers=headers(token), timeout=60
    )
    export_id = (
        (exported.json().get("data") or {}).get("id") if exported.status_code == 200 else None
    )
    step("导出成功", exported.status_code == 200 and bool(export_id), exported.text[:200])
    if export_id:
        jr = requests.get(
            f"{BASE}/api/v1/reviews/{case_id}/exports/{export_id}/download?format=json",
            headers=headers(token),
            timeout=30,
        )
        step(
            "导出 JSON 含 V1/V2 与文档哈希",
            jr.status_code == 200 and "V1" in jr.text and "V2" in jr.text,
        )
    archived = requests.post(
        f"{BASE}/api/v1/reviews/{case_id}/archive", headers=headers(token), timeout=30
    )
    step("归档成功", archived.status_code == 200, archived.text[:200])

    print()
    if FAILURES:
        print(f"RESULT: FAIL ({len(FAILURES)}): {FAILURES}")
        return 1
    print("RESULT: ALL PASS — 真实全流程（上传→解析/嵌入/索引→检索→Chat→核验→归档→下载）通过")
    return 0


def _cli() -> int:
    parser = argparse.ArgumentParser(description="DocMind RC 全流程验收（真实栈）")
    parser.add_argument(
        "--base-url", default=DEFAULT_BASE, help="backend 地址（默认 $RC_BASE_URL 或 :8020）"
    )
    parser.add_argument(
        "--expect-index",
        default=os.environ.get("RC_EXPECT_INDEX"),
        help="目标栈必须使用的 ES 索引名（本地配置不可用/多栈并存时必传）",
    )
    args = parser.parse_args()
    global BASE
    BASE = args.base_url.rstrip("/")
    if not preflight(args.expect_index):
        return 2
    return main()


if __name__ == "__main__":
    raise SystemExit(_cli())
