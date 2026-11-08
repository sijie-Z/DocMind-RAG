"""E2E: personal-LLM upstream failure must yield an error event with code and not persist an answer.

Runs against a locally started backend from the PR branch (port 8011, RC database).
Creates a throwaway user (registration auto-creates a personal org), saves a deliberately
invalid DeepSeek key, chats, and inspects the SSE frames and the stored conversation.
"""

from __future__ import annotations

import json
import sys
import time
import urllib.error
import urllib.request

BASE = "http://127.0.0.1:8011/api/v1"
FAILURE_TEXT = "个人模型调用失败"


def call(method: str, path: str, payload: dict | None = None, token: str | None = None):
    data = json.dumps(payload, ensure_ascii=False).encode() if payload is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", "Bearer " + token)
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return resp.status, json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode()


def main() -> int:
    stamp = int(time.time())
    username = f"e2e_llmerr_{stamp}"
    status, body = call(
        "POST",
        "/auth/register",
        {
            "username": username,
            "email": f"{username}@example.com",
            "password": "E2eTest!2026",
            "full_name": "E2E LLM Error",
        },
    )
    if status != 200:
        print("REGISTER FAILED", status, str(body)[:400])
        return 1
    token = body["data"]["access_token"]
    print("registered", username)

    status, body = call(
        "PUT",
        "/user/settings/providers",
        {
            "provider": "deepseek",
            "model": "deepseek-chat",
            "api_key": "sk-invalid-e2e-0000000000000000",
        },
        token,
    )
    print("save provider:", status, json.dumps(body, ensure_ascii=False)[:300])
    if status != 200:
        return 1

    status, body = call("POST", "/chat/conversations", {"title": f"E2E {stamp}"}, token)
    if status != 200:
        print("CREATE CONV FAILED", status, str(body)[:400])
        return 1
    conv_id = body["data"]["id"]

    payload = json.dumps(
        {"content": "请用一句话说明这份合同的主要风险。", "conversationId": conv_id},
        ensure_ascii=False,
    ).encode()
    req = urllib.request.Request(BASE + "/chat/stream", data=payload, method="POST")
    req.add_header("Content-Type", "application/json")
    req.add_header("Authorization", "Bearer " + token)
    req.add_header("Accept", "text/event-stream")

    events: list[tuple[str, str]] = []
    error_frames: list[dict] = []
    raw_chunks: list[str] = []
    with urllib.request.urlopen(req, timeout=180) as resp:
        event_name = ""
        for raw in resp:
            line = raw.decode("utf-8", "replace").rstrip("\r\n")
            if line.startswith("event:"):
                event_name = line[6:].strip()
            elif line.startswith("data:"):
                data = line[5:].strip()
                events.append((event_name, data))
                if event_name == "error":
                    try:
                        error_frames.append(json.loads(data))
                    except json.JSONDecodeError:
                        error_frames.append({"raw": data})
                if event_name == "chunk":
                    raw_chunks.append(data)
    print("event types:", [e for e, _ in events])
    print("error frames:", json.dumps(error_frames, ensure_ascii=False))
    print("chunk payloads:", json.dumps(raw_chunks, ensure_ascii=False)[:400])

    status, body = call("GET", f"/chat/conversations/{conv_id}", token=token)
    stored = []
    if status == 200 and isinstance(body, dict):
        stored = body.get("data", {}).get("messages") or body.get("messages") or []
    kinds = [m.get("messageType") for m in stored]
    assistant_texts = [
        (m.get("content") or "") for m in stored if m.get("messageType") == "assistant"
    ]
    print("stored kinds:", kinds)
    print("assistant texts:", json.dumps(assistant_texts, ensure_ascii=False)[:300])

    checks = {
        "error frame present": bool(error_frames),
        "code is USER_LLM_REQUEST_FAILED": any(
            f.get("code") == "USER_LLM_REQUEST_FAILED" for f in error_frames
        ),
        "error content is whitelisted zh message": any(
            FAILURE_TEXT in (f.get("content") or "") for f in error_frames
        ),
        "no assistant message persisted": not assistant_texts,
        "failure text not persisted": not any(
            FAILURE_TEXT in t for m in stored for t in [m.get("content") or ""]
        ),
    }
    for name, ok in checks.items():
        print(("PASS " if ok else "FAIL ") + name)
    return 0 if all(checks.values()) else 2


if __name__ == "__main__":
    sys.exit(main())
