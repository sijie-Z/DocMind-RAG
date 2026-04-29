import os
import json
from hashlib import md5
from typing import Dict, Any


def _session_dir(upload_id: str) -> str:
    return os.path.join("data", "uploads", upload_id)


def init_upload(filename: str, total_chunks: int, owner: str,
                org_tag: str | None, visibility: str = "org") -> Dict[str, Any]:
    """
    功能：初始化一个分片上传会话，创建会话文件与 chunk 目录。
    小白解释：先登记要上传的文件，告诉服务器“会有多少片”，以及归属与权限。
    """
    upload_id = md5(f"{filename}-{owner}".encode("utf-8")).hexdigest()
    sdir = _session_dir(upload_id)
    os.makedirs(os.path.join(sdir, "chunks"), exist_ok=True)
    session = {
        "upload_id": upload_id,
        "filename": filename,
        "total_chunks": int(total_chunks),
        "bitmap": [0] * int(total_chunks),
        "owner": owner,
        "org_tag": org_tag,
        "visibility": visibility.lower(),
    }
    with open(os.path.join(sdir, "session.json"), "w", encoding="utf-8") as f:
        json.dump(session, f, ensure_ascii=False, indent=2)
    return session


def save_chunk(upload_id: str, index: int, data: bytes) -> Dict[str, Any]:
    """
    功能：保存某一分片，并在位图中标记该分片已到达。
    小白解释：每来一片，就存成一个 .part 文件，并记住这个片“收到了”。
    """
    sdir = _session_dir(upload_id)
    sj = os.path.join(sdir, "session.json")
    if not os.path.isfile(sj):
        return {"ok": False, "error": "session not found"}
    with open(sj, "r", encoding="utf-8") as f:
        session = json.load(f)

    total = int(session["total_chunks"])
    if index < 0 or index >= total:
        return {"ok": False, "error": "index out of range"}

    cpath = os.path.join(sdir, "chunks", f"{index}.part")
    with open(cpath, "wb") as f:
        f.write(data)

    session["bitmap"][index] = 1
    with open(sj, "w", encoding="utf-8") as f:
        json.dump(session, f, ensure_ascii=False, indent=2)
    return {"ok": True, "received": index, "total": total, "complete": sum(session["bitmap"]) == total}


def complete_upload(upload_id: str) -> Dict[str, Any]:
    """
    功能：检测所有分片是否到齐，合并为完整文件并落入 data/source；同时写入 meta。
    小白解释：把所有 .part 按顺序拼起来，生成最终的 .txt 文档，并记录权限信息。
    """
    sdir = _session_dir(upload_id)
    sj = os.path.join(sdir, "session.json")
    if not os.path.isfile(sj):
        return {"ok": False, "error": "session not found"}
    with open(sj, "r", encoding="utf-8") as f:
        session = json.load(f)

    total = int(session["total_chunks"])
    if sum(session["bitmap"]) != total:
        return {"ok": False, "error": "not complete"}

    # 合并
    out_name = f"{upload_id}.txt"
    out_path = os.path.join("data", "source", out_name)
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "wb") as out:
        for i in range(total):
            cpath = os.path.join(sdir, "chunks", f"{i}.part")
            with open(cpath, "rb") as f:
                out.write(f.read())

    # 写 meta
    meta_dir = os.path.join("data", "source_meta")
    os.makedirs(meta_dir, exist_ok=True)
    meta_path = os.path.join(meta_dir, f"{out_name}.json")
    meta = {
        "owner": session.get("owner"),
        "org_tag": session.get("org_tag"),
        "visibility": session.get("visibility") or "org",
        "original_filename": session.get("filename"),
    }
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)

    return {"ok": True, "path": out_path, "meta": meta}