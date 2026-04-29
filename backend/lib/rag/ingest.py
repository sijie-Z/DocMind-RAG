import os
import json
from typing import List, Dict


def load_txt_folder(folder: str) -> List[Dict[str, str]]:
    """
    功能：读取指定文件夹下所有 .txt 文本，构造成文档列表。
    小白解释：就像批量打开很多记事本文件，把内容拿出来准备入库。

    返回：[{"id": 文件名, "text": 文本内容, "source": 文件路径} ...]
    """
    docs: List[Dict[str, str]] = []
    if not os.path.isdir(folder):
        return docs

    for name in os.listdir(folder):
        if not name.lower().endswith(".txt"):
            continue
        path = os.path.join(folder, name)
        try:
            with open(path, "r", encoding="utf-8") as f:
                text = f.read()
            # 读取同名 meta（如存在）
            meta_dir = os.path.join("data", "source_meta")
            meta_path = os.path.join(meta_dir, f"{name}.json")
            meta = None
            if os.path.isfile(meta_path):
                try:
                    with open(meta_path, "r", encoding="utf-8") as mf:
                        meta = json.load(mf)
                except Exception:
                    meta = None

            docs.append({
                "id": name,
                "text": text,
                "source": path,
                "meta": meta,
            })
        except Exception:
            # 为了演示简单，遇到不可读文件直接跳过
            continue

    return docs