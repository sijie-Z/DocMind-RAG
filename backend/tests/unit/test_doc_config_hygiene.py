"""文档口径 / 配置模板卫生测试（#352 残余项；W0-7 / W0-8 的轻量补充）。

纯静态文件断言（不导入 app、不依赖网络/数据库），可在 CI 轻量 job 中以
``--noconftest`` 运行。各用例在修复前应红、修复后应绿：

1. README 的记忆持久化表述必须与 ``memory_service.py`` 一致：Redis 只持久化
   长期/反思记忆（``_save_to_redis``），短期/工作记忆为进程内；
2. ``/memory`` 行必须包含第四层反思记忆（代码里 ``self.reflective`` 是四层之一）；
3. 宿主模板、compose 发布端口/库名与 legacy 脚本一致，全 Docker 保持容器内拓扑；
4. ``frontend/index.html`` 的 favicon 必须指向 ``frontend/public/`` 中真实存在的资产；
5. ``frontend/README.md`` 不得再写过期启动方式/WS 路径/API 前缀；
6. ``backend/.env.example`` / ``.env.docker.example`` 必须覆盖真实消费的配置键；
7. 首启文档列全 compose 必填变量，并说明全文件插值与 Grafana 服务选择无关；
8. ``backend/.dockerignore`` 必须排除 ``.env.*`` 且只保留两个模板文件
   （``Dockerfile`` 的 ``COPY . .`` 会把构建上下文整体烤进镜像）；
9. ``.gitignore`` 必须覆盖本地工作目录/覆盖率产物。

复跑（backend 目录下）::

    python -m pytest tests/unit/test_doc_config_hygiene.py -q
"""

import fnmatch
import re
from pathlib import Path

import yaml

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
    persist_block = service.split("async def _save_to_redis", 1)[1].split("async def remember", 1)[
        0
    ]
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


# ── 3. 宿主模板 / 全 Docker / legacy 脚本使用各自正确的拓扑 ─────────────────


def _compose_services() -> dict:
    compose = yaml.safe_load(_read("backend/docker-compose.yml"))
    return compose["services"]


def _compose_environment(service: dict) -> dict[str, str]:
    entries = service.get("environment", [])
    assert isinstance(entries, list), "本测试按 compose 的 list environment 形式解析"
    return dict(entry.split("=", 1) for entry in entries)


def _published_port(service: dict) -> tuple[str, int, int]:
    ports = service["ports"]
    assert len(ports) == 1, "MySQL/Redis 目前各只有一条宿主端口映射"
    host, published, container = str(ports[0]).rsplit(":", 2)
    return host, int(published), int(container)


def _mysql_database_default(mysql_service: dict) -> str:
    expression = _compose_environment(mysql_service)["MYSQL_DATABASE"]
    match = re.fullmatch(r"\$\{MYSQL_DATABASE:-([^}]+)\}", expression)
    assert match, f"无法从 MYSQL_DATABASE 表达式解析默认库名: {expression}"
    return match.group(1)


def _env_example_db_name() -> str:
    text = _read("backend/.env.example")
    match = re.search(r"^DATABASE_URL=.*/([A-Za-z0-9_]+)\s*$", text, re.MULTILINE)
    assert match, "backend/.env.example 中未找到形如 .../<db_name> 的 DATABASE_URL"
    return match.group(1)


def test_host_template_matches_compose_published_topology():
    services = _compose_services()
    mysql_host, mysql_port, _ = _published_port(services["mysql"])
    redis_host, redis_port, _ = _published_port(services["redis"])
    assert mysql_host == redis_host == "127.0.0.1"

    env = _read("backend/.env.example")
    database_url = re.search(
        r"^DATABASE_URL=mysql\+aiomysql://\$\{DB_USER\}:\$\{DB_PASSWORD\}"
        r"@localhost:(\d+)/([A-Za-z0-9_]+)\s*$",
        env,
        re.MULTILINE,
    )
    assert database_url, "宿主 DATABASE_URL 必须使用模板凭据引用和 localhost 拓扑"
    assert int(database_url.group(1)) == mysql_port
    assert database_url.group(2) == _mysql_database_default(services["mysql"])
    assert _active_value(env, "REDIS_HOST") == "localhost"
    assert int(_active_value(env, "REDIS_PORT")) == redis_port


def test_full_docker_services_keep_container_topology():
    services = _compose_services()
    _, _, mysql_container_port = _published_port(services["mysql"])
    _, _, redis_container_port = _published_port(services["redis"])
    mysql_database = _mysql_database_default(services["mysql"])
    assert mysql_container_port == 3306
    assert redis_container_port == 6379

    for name in ("migrate", "backend", "worker"):
        database_url = _compose_environment(services[name])["DATABASE_URL"]
        match = re.search(r"@docmind-mysql:(\d+)/\$\{MYSQL_DATABASE:-([^}]+)\}$", database_url)
        assert match, f"{name} 的 DATABASE_URL 未使用 Compose 内网数据库拓扑"
        assert int(match.group(1)) == mysql_container_port
        assert match.group(2) == mysql_database

    for name in ("backend", "worker"):
        environment = _compose_environment(services[name])
        assert environment["REDIS_HOST"] == "docmind-redis"
        assert int(environment["REDIS_PORT"]) == redis_container_port


def _script_env_default(text: str, key: str) -> str:
    match = re.search(rf'os\.getenv\("{key}",\s*"([^"]+)"\)', text)
    assert match, f'未找到 os.getenv("{key}", "...") 默认值'
    return match.group(1)


def test_db_bootstrap_scripts_match_compose_host_topology():
    services = _compose_services()
    _, mysql_port, _ = _published_port(services["mysql"])
    db_name = _mysql_database_default(services["mysql"])
    assert db_name == _env_example_db_name()
    assert db_name != "paicongming_db", "旧品牌库名仍是模板真源？请先核对"

    for rel in ("backend/create_db.py", "backend/update_org_table.py"):
        text = _read(rel)
        assert _script_env_default(text, "DB_HOST") == "localhost"
        assert int(_script_env_default(text, "DB_PORT")) == mysql_port
        assert _script_env_default(text, "DB_NAME") == db_name
        assert "paicongming" not in text.lower(), f"{rel} 仍含旧品牌库名残留"


# ── 4. favicon 指向真实资产 ──────────────────────────────────────────────────


def test_favicon_points_to_existing_public_asset():
    html = _read("frontend/index.html")
    tags = re.findall(r"<link\b[^>]*>", html)
    icon_tags = [tag for tag in tags if re.search(r'\brel="icon"', tag)]
    assert icon_tags, 'frontend/index.html 缺少 <link rel="icon"> 声明'
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


# ── 7. 首启文档列全 Compose 必填项并说明全文件插值 ───────────────────────────


def _markdown_section(text: str, start: str, end: str) -> str:
    assert start in text and end in text
    return text.split(start, 1)[1].split(end, 1)[0]


def test_first_run_docs_list_all_compose_required_variables():
    compose = _read("backend/docker-compose.yml")
    required = set(re.findall(r"\$\{([A-Z][A-Z0-9_]*):\?", compose))
    assert required, "未从 compose 解析到任何 :? 必填变量"
    sections = {
        "README.md": ("### 2. 配置", "### 3. 启动基础设施"),
        "README_EN.md": ("### 2. Configure", "### 3. Start Infrastructure"),
        "docs/USER_GUIDE.md": ("### 1.3 先准备配置文件", "### 1.4 启动基础设施"),
        "docs/USER_GUIDE_EN.md": (
            "### 1.3 Prepare configuration files",
            "### 1.4 Start infrastructure",
        ),
    }
    for rel, (start, end) in sections.items():
        section = _markdown_section(_read(rel), start, end)
        normalized = " ".join(section.split())
        missing = sorted(key for key in required if f"`{key}`" not in section)
        assert not missing, f"{rel} 首启配置段未列全 compose 必填变量: {missing}"
        if rel.endswith("_EN.md"):
            assert "whole-file interpolation before service selection" in normalized
            assert "without selecting Grafana" in normalized
        else:
            assert "全文件插值" in section
            assert "不选择 Grafana" in section


# ── 8. .dockerignore 不得把真实密钥放进构建上下文 ───────────────────────────


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


# ── 9. .gitignore 覆盖本地工作目录 ──────────────────────────────────────────

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
