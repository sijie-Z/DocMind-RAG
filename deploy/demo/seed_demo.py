"""Demo 数据种子：用真实链路生成一个“已完成核验”的演示案件。

流程与 `backend/scripts/rc_accept_full_flow.py` 同源：注册/登录 → 上传 3 份
中文采购文档 → 等待 worker 解析/摘要/嵌入/索引 → 登记核验身份 → 建案 →
分析 → 证据 → 决策 → 导出 → 归档。不 seed 任何 chunk、不 mock 检索/LLM。

前置：Demo 栈已启动且 `/health` 为 healthy，worker 在运行。
用法（仓库根目录）：
    DEMO_PASSWORD='...' python deploy/demo/seed_demo.py \
        --base-url http://127.0.0.1:8080
可选环境变量：DEMO_USERNAME（默认 docmind_demo）。

注意：重复运行会再建一套文档与案件；演示库按需重建即可（不做自动清理）。
"""

from __future__ import annotations

import argparse
import os
import time

import requests

PASSWORD = os.environ.get("DEMO_PASSWORD", "")
USERNAME = os.environ.get("DEMO_USERNAME", "docmind_demo")

POLICY = (
    "采购管理办法（演示）\n"
    "第5条 付款条件：合同付款应经财务与法务复核，原则上验收合格后支付。\n"
    "第8条 例外：涉及预付款的采购须单独审批。\n"
    "第9条 资料归档：合同与验收单应归档保存不少于五年。\n"
)
BASE = (
    "采购合同（演示 V1）\n"
    "第3条 付款期限：甲方应在验收合格后30日内支付货款。\n"
    "第4条 验收：乙方交付后10个工作日内完成验收。\n"
    "第7条 违约：逾期交付的，每延迟一日按合同金额千分之一支付违约金。\n"
)
REVISED = (
    "采购合同（演示 V2）\n"
    "第3条 付款期限：甲方应在验收合格后60日内支付货款。\n"
    "第4条 验收：乙方交付后15个工作日内完成验收。\n"
    "第6条 例外：因不可抗力导致验收延迟的，付款期限相应顺延。\n"
    "第7条 违约：逾期交付的，每延迟一日按合同金额千分之二支付违约金。\n"
)


def headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def register_or_login(base: str) -> tuple[str, str]:
    if not PASSWORD:
        raise SystemExit("缺少 DEMO_PASSWORD 环境变量")
    payload = {
        "username": USERNAME,
        "email": f"{USERNAME}@example.com",
        "password": PASSWORD,
        "full_name": "DocMind Demo",
    }
    r = requests.post(f"{base}/api/v1/auth/register", json=payload, timeout=30)
    if r.status_code in (200, 201):
        data = r.json()["data"]
        print(f"[seed] 已注册演示账号 {USERNAME}（org={data['user_info']['organization_id']}）")
        return data["access_token"], str(data["user_info"]["organization_id"])
    login = requests.post(
        f"{base}/api/v1/auth/login/json",
        json={"username": USERNAME, "password": PASSWORD},
        timeout=30,
    )
    if login.status_code != 200:
        raise SystemExit(
            f"注册失败({r.status_code})且登录失败({login.status_code})：{r.text[:200]}"
        )
    data = login.json()["data"]
    print("[seed] 演示账号已存在，登录成功")
    return data["access_token"], str(data["user_info"]["organization_id"])


def upload(token: str, base: str, filename: str, content: str) -> str:
    r = requests.post(
        f"{base}/api/v1/documents/upload",
        headers=headers(token),
        files={"file": (filename, content.encode("utf-8"), "text/plain")},
        data={"title": filename.removesuffix(".txt"), "description": "DocMind 演示数据（合成）"},
        timeout=60,
    )
    if r.status_code != 201:
        raise SystemExit(f"上传失败 {filename}: {r.status_code} {r.text[:200]}")
    doc_id = r.json()["data"]["id"]
    print(f"[seed] 已上传 {filename} -> {doc_id}")
    return doc_id


def wait_indexed(token: str, base: str, doc_id: str, timeout: int = 300) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        r = requests.get(f"{base}/api/v1/documents/{doc_id}", headers=headers(token), timeout=30)
        data = (r.json().get("data") or {}) if r.status_code == 200 else {}
        status = data.get("status")
        if status == "indexed" and int(data.get("chunk_count") or 0) > 0:
            print(f"[seed] 文档 {doc_id} 索引完成（chunks={data['chunk_count']}）")
            return
        if status == "failed":
            raise SystemExit(f"文档处理失败: {data.get('parse_error')}")
        time.sleep(4)
    raise SystemExit(f"文档 {doc_id} 300s 内未完成索引")


def main() -> int:
    parser = argparse.ArgumentParser(description="DocMind Demo 数据种子")
    parser.add_argument(
        "--base-url", default=os.environ.get("DEMO_BASE_URL", "http://127.0.0.1:8080")
    )
    args = parser.parse_args()
    base = args.base_url.rstrip("/")

    token, org_id = register_or_login(base)
    ids = {
        "policy": upload(token, base, "采购管理办法-演示.txt", POLICY),
        "base": upload(token, base, "采购合同-V1-演示.txt", BASE),
        "revised": upload(token, base, "采购合同-V2-演示.txt", REVISED),
    }
    for doc_id in ids.values():
        wait_indexed(token, base, doc_id)

    metas = {
        "policy": {"doc_role": "policy"},
        "base": {
            "doc_role": "contract",
            "family_key": "demo",
            "version_label": "V1",
            "version_seq": 1,
        },
        "revised": {
            "doc_role": "contract",
            "family_key": "demo",
            "version_label": "V2",
            "version_seq": 2,
        },
    }
    for side, body in metas.items():
        r = requests.put(
            f"{base}/api/v1/documents/{ids[side]}/review-meta",
            headers=headers(token),
            json=body,
            timeout=30,
        )
        if r.status_code != 200:
            raise SystemExit(f"登记核验身份失败 {side}: {r.status_code} {r.text[:200]}")

    created = requests.post(
        f"{base}/api/v1/reviews",
        headers=headers(token),
        json={
            "title": "演示核验：采购合同 V1→V2（合成数据）",
            "policy_document_id": ids["policy"],
            "base_document_id": ids["base"],
            "revised_document_id": ids["revised"],
        },
        timeout=60,
    )
    if created.status_code != 200:
        raise SystemExit(f"建案失败: {created.status_code} {created.text[:300]}")
    case_id = created.json()["data"]["id"]
    print(f"[seed] 案件已创建 -> {case_id}")

    analyzed = requests.post(
        f"{base}/api/v1/reviews/{case_id}/analyze", headers=headers(token), timeout=60
    )
    if analyzed.status_code != 200:
        raise SystemExit(f"分析失败: {analyzed.status_code} {analyzed.text[:300]}")

    detail = requests.get(
        f"{base}/api/v1/reviews/{case_id}", headers=headers(token), timeout=30
    ).json()["data"]
    items = detail["items"]
    print(f"[seed] 核验项 {len(items)} 个")

    ev = requests.get(
        f"{base}/api/v1/documents/{ids['revised']}/evidence",
        headers=headers(token),
        params={"q": "付款"},
        timeout=30,
    )
    evidence_items = (ev.json().get("data") or {}).get("items") or []
    if evidence_items:
        requests.put(
            f"{base}/api/v1/reviews/{case_id}/items/{items[0]['id']}/evidence",
            headers=headers(token),
            json={"side": "revised", "chunk_id": evidence_items[0]["chunk_id"], "role": "main"},
            timeout=30,
        )

    for item in items:
        r = requests.put(
            f"{base}/api/v1/reviews/{case_id}/items/{item['id']}/decision",
            headers=headers(token),
            json={"status": "verified", "note": "演示数据（合成合同）"},
            timeout=30,
        )
        if r.status_code != 200:
            raise SystemExit(f"决策写入失败: {r.status_code} {r.text[:200]}")

    exported = requests.post(
        f"{base}/api/v1/reviews/{case_id}/export", headers=headers(token), timeout=60
    )
    if exported.status_code != 200:
        raise SystemExit(f"导出失败: {exported.status_code} {exported.text[:300]}")

    archived = requests.post(
        f"{base}/api/v1/reviews/{case_id}/archive", headers=headers(token), timeout=30
    )
    if archived.status_code != 200:
        raise SystemExit(f"归档失败: {archived.status_code} {archived.text[:300]}")

    print("[seed] 完成：演示案件已归档，可直接用演示账号登录查看")
    print(f"[seed] 账号={USERNAME} 组织={org_id} 案件={case_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
