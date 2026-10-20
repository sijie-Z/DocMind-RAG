"""文档口径 / 配置模板卫生测试（#352 残余项；W0-7 / W0-8 的轻量补充）。

纯静态文件断言（不导入 app、不依赖网络/数据库），可在 CI 轻量 job 中以
``--noconftest`` 运行。各用例在修复前应红、修复后应绿：

1. README 的记忆持久化表述必须与 ``memory_service.py`` 一致：Redis 只持久化
   长期/反思记忆（``_save_to_redis``），短期/工作记忆为进程内；
2. ``/memory`` 行必须包含第四层反思记忆（代码里 ``self.reflective`` 是四层之一）；
3. ``create_db.py`` / ``update_org_table.py`` 的默认库名必须等于
   ``backend/.env.example`` 中 ``DATABASE_URL`` 的库名（不手写数字/名称）；
4. ``frontend/index.html`` 的 favicon 必须指向 ``frontend/public/`` 中真实存在的资产；
5. ``frontend/README.md`` 不得再写过期启动方式/WS 路径/API 前缀；
6. ``backend/.env.example`` / ``.env.docker.example`` 必须覆盖真实消费的配置键；
7. ``backend/.dockerignore`` 必须排除 ``.env.*`` 且只保留两个模板文件
   （``Dockerfile`` 的 ``COPY . .`` 会把构建上下文整体烤进镜像）；
8. ``.gitignore`` 必须覆盖本地工作目录/覆盖率产物。

复跑（backend 目录下）::

    python -m pytest tests/unit/test_doc_config_hygiene.py -q
"""

import fnmatch
import re
from pathlib import Path

_UNIT_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _UNIT_DIR.parents[2]


def _read(rel: str) -> str:
    return (_REPO_ROOT / rel).read_text(encoding="utf-8")


def _assert_absent(rel: str, needles: list[str]) -> None:
    text = _read(rel)
    hits = [n for n in needles if n in text]
    assert not hits, f"{rel} 仍含过期/错误表述: {hits}"


def _assert_present(rel: str, needles: list[str]) -> None:
    text = _read(rel)
    missing = [n for n in needles if n not in text]
    assert not missing, f"{rel} 缺少与代码一致的必要表述: {missing}"


# ── 1. 记忆持久化：仅长期/反思记忆落 Redis ──────────────────────────────────


def test_memory_persistence_statements_match_code():
    """README（中英）的记忆持久化表述必须与 memory_service.py 的行为一致。"""
    service = _read("backend/app/services/memory_service.py")
    # 代码事实：四层记忆；Redis 只序列化 long_term / reflective。
    assert "self.reflective = ReflectiveMemory()" in service, (
        "memory_service.py 不再包含反思记忆层，请复核 README 的四层记忆表述。"
    )
    persist_block = service.split("async def _save_to_redis", 1)[1].split(
        "async def remember", 1
    )[0]
    assert '"long_term"' in persist_block and '"reflective"' in persist_block, (
        "memory_service._save_to_redis 不再持久化长期/反思记忆，请复核 README。"
    )
    assert '"short_term"' not in persist_block and '"working"' not in persist_block, (
        "memory_service._save_to_redis 开始持久化短期/工作记忆；"
        "若属实，请同步更新 README 的持久化边界与本测试。"
    )

    _assert_absent(
        "README.md",
        [
            "Agent 记忆、指标快照与实时计数均已支持 Redis 持久化",
        ],
    )
    _assert_present("README.md", ["长期/反思记忆", "短期/工作记忆为进程内"])
    _assert_absent(
        "README_EN.md",
        [
            "GraphRAG graphs, agent memory, metrics snapshots, and live counters now persist to Redis",
        ],
    )
    _assert_present(
        "README_EN.md",
        ["long-term/reflective", "short-term/working memory", "in-process"],
    )


# ── 2. /memory 行包含第四层反思记忆 ─────────────────────────────────────────


def test_memory_endpoint_row_includes_reflective_layer():
    _assert_absent("README.md", ["短期/长期/工作记忆 CRUD"])
    _assert_present("README.md", ["短期/长期/工作/反思记忆 CRUD"])
    _assert_absent("README_EN.md", ["Short/long/working memory CRUD"])
    _assert_present("README_EN.md", ["Short/long/working/reflective memory CRUD"])


# ── 3. 建库脚本默认库名与 .env.example 的真源一致 ────────────────────────────


def _env_example_db_name() -> str:
    text = _read("backend/.env.example")
    match = re.search(r"^DATABASE_URL=.*/([A-Za-z0-9_]+)\s*$", text, re.MULTILINE)
    assert match, "backend/.env.example 中未找到形如 .../<db_name> 的 DATABASE_URL"
    return match.group(1)


def test_db_bootstrap_scripts_default_to_current_database_name():
    db_name = _env_example_db_name()
    assert db_name != "paicongming_db", "旧品牌库名仍是 .env.example 的真源？请先核对"
    for rel in ("backend/create_db.py", "backend/update_org_table.py"):
        text = _read(rel)
        match = re.search(r'os\.getenv\("DB_NAME",\s*"([^"]+)"\)', text)
        assert match, f'{rel} 中未找到 os.getenv("DB_NAME", "...") 默认值'
        assert match.group(1) == db_name, (
            f"{rel} 默认库名 {match.group(1)!r} 与 backend/.env.example "
            f"中的库名 {db_name!r} 不一致"
        )
        assert "paicongming" not in text.lower(), f"{rel} 仍含旧品牌库名残留"


# ── 4. favicon 指向真实资产 ──────────────────────────────────────────────────


def test_favicon_points_to_existing_public_asset():
    html = _read("frontend/index.html")
    tags = re.findall(r"<link\b[^>]*>", html)
    icon_tags = [tag for tag in tags if re.search(r'\brel="icon"', tag)]
    assert icon_tags, "frontend/index.html 缺少 <link rel=\"icon\"> 声明"
    for tag in icon_tags:
        href_match = re.search(r'href="([^"]+)"', tag)
        assert href_match, f"favicon link 缺少 href: {tag}"
        href = href_match.group(1)
        assert href != "/vite.svg", "favicon 仍指向不存在于 frontend/public 的 /vite.svg"
        if href.startswith("/"):
            asset = _REPO_ROOT / "frontend" / "public" / href.lstrip("/")
            assert asset.is_file(), f"favicon 指向的资产不存在: frontend/public{href}"


# ── 5. frontend/README.md 与当前栈一致 ──────────────────────────────────────


def test_frontend_readme_matches_current_stack():
    rel = "frontend/README.md"
    _assert_absent(
        rel,
        [
            "Python 3.8",
            "python main.py",
            "file_processor.py",
            "/ws/chat/",
            "POST /api/auth/",
            "Node.js 16",
        ],
    )
    _assert_present(
        rel,
        [
            "Python 3.11+",
            "uvicorn app.main:app",
            "/api/v1",
            "/api/v1/chat/ws",
            "docs/facts.json",
        ],
    )


# ── 6. env 模板覆盖真实消费的配置键 ─────────────────────────────────────────


def _key_declared(text: str, key: str) -> bool:
    """键以 ``KEY=`` 或 ``# KEY=`` 形式出现在行首（模板里可选键常被注释）。"""
    return re.search(rf"^\s*#?\s*{re.escape(key)}=", text, re.MULTILINE) is not None


def _active_value(text: str, key: str) -> str:
    match = re.search(rf"^{re.escape(key)}=(.+)$", text, re.MULTILINE)
    assert match, f"模板缺少未注释的 {key}= 行"
    return match.group(1).strip()


def test_env_templates_cover_required_keys():
    env = _read("backend/.env.example")
    docker_env = _read("backend/.env.docker.example")

    for key in ("RERANK_USE_LOCAL", "RERANK_MODEL", "DEMO_PASSWORD"):
        assert _key_declared(env, key), f"backend/.env.example 缺少 {key}"
    assert _key_declared(docker_env, "ELASTICSEARCH_INDEX_NAME"), (
        "backend/.env.docker.example 缺少 ELASTICSEARCH_INDEX_NAME"
    )

    # 模板中声明的键必须有真实消费方（防止文档化不存在的配置）。
    ai_config = _read("backend/app/core/config/ai.py")
    for key in ("RERANK_USE_LOCAL", "RERANK_MODEL"):
        assert re.search(rf"^\s*{key}:\s", ai_config, re.MULTILINE), (
            f"app/core/config/ai.py 中不存在配置项 {key}"
        )
    demo_user = _read("backend/app/core/ensure_demo_user.py")
    assert 'os.environ.get("DEMO_PASSWORD")' in demo_user, (
        "DEMO_PASSWORD 在 ensure_demo_user.py 中不再被消费"
    )

    # 两份模板的 ES 索引名必须一致（同一部署口径）。
    assert _active_value(env, "ELASTICSEARCH_INDEX_NAME") == _active_value(
        docker_env, "ELASTICSEARCH_INDEX_NAME"
    ), "backend/.env.example 与 .env.docker.example 的 ELASTICSEARCH_INDEX_NAME 不一致"


# ── 7. .dockerignore 不得把真实密钥放进构建上下文 ───────────────────────────


def _dockerignored(patterns: list[str], path: str) -> bool:
    """近似 Docker .dockerignore 语义：按行序匹配，最后一条命中生效。"""
    ignored = False
    basename = path.rsplit("/", 1)[-1]
    for raw in patterns:
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        negated = line.startswith("!")
        pattern = line[1:] if negated else line
        if fnmatch.fnmatch(path, pattern) or fnmatch.fnmatch(basename, pattern):
            ignored = not negated
    return ignored


def test_dockerignore_excludes_env_secrets_but_keeps_templates():
    patterns = _read("backend/.dockerignore").splitlines()
    assert any(line.strip() == ".env.*" for line in patterns), (
        "backend/.dockerignore 缺少 .env.* —— .env.docker 等真实密钥文件会进入构建上下文"
    )
    exceptions = {line.strip() for line in patterns if line.strip().startswith("!")}
    assert exceptions == {"!.env.example", "!.env.docker.example"}, (
        "dockerignore 的白名单必须恰为两个模板文件（放行其他 .env.* 会让密钥被 COPY 进镜像）: "
        f"{sorted(exceptions)}"
    )
    # 前提校验：Dockerfile 确实整体 COPY 构建上下文。
    assert "COPY . ." in _read("backend/Dockerfile"), (
        "Dockerfile 不再 COPY . .；若构建上下文输入方式已变化，请复核本测试的前提"
    )

    assert _dockerignored(patterns, ".env") is True
    assert _dockerignored(patterns, ".env.local") is True
    assert _dockerignored(patterns, ".env.docker") is True
    assert _dockerignored(patterns, ".env.example") is False
    assert _dockerignored(patterns, ".env.docker.example") is False


# ── 8. .gitignore 覆盖本地工作目录 ──────────────────────────────────────────

_GITIGNORE_REQUIRED = (
    ".coverage",
    "htmlcov/",
    "/_orchestrate/",
    "/_uisweep/",
    "/logs/",
    "/uploads/",
    "/data/",
    "/.benchmarks/",
    "/backend/temp_uploads/",
)


def test_gitignore_covers_local_workspaces():
    lines = {line.strip() for line in _read(".gitignore").splitlines()}
    missing = [entry for entry in _GITIGNORE_REQUIRED if entry not in lines]
    assert not missing, f".gitignore 缺少本地工作目录/覆盖率产物条目: {missing}"
