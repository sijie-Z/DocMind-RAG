"""ES 索引名按库派生（环境隔离，T1）与显式配置优先级。"""

import pytest

from app.core.config.loader import _apply_es_index_name, _derive_es_index_name


def test_derive_index_name_from_database_name():
    assert _derive_es_index_name("mysql+asyncmy://u:p@h:3306/docmind") == "docmind_knowledge"
    assert _derive_es_index_name("mysql+aiomysql://u:p@h:3306/docmind_rc") == "docmind_knowledge_rc"
    assert (
        _derive_es_index_name("mysql+asyncmy://u:p@h:3306/docmind_iso") == "docmind_knowledge_iso"
    )
    assert _derive_es_index_name("mysql+asyncmy://u:p@h:3306/test_db") == "test_db_knowledge"


def test_derive_index_name_disambiguates_normalized_names():
    """大小写/连字符/非法字符归一后必须追加哈希，避免两个库映射到同一索引。"""
    lower = _derive_es_index_name("mysql+asyncmy://u:p@h:3306/docmind_rc")
    hyphen = _derive_es_index_name("mysql+asyncmy://u:p@h:3306/docmind-rc")
    upper = _derive_es_index_name("mysql+asyncmy://u:p@h:3306/DocMind_RC")
    assert hyphen.startswith("docmind_knowledge_rc_")
    assert upper.startswith("docmind_knowledge_rc_")
    assert len({lower, hyphen, upper}) == 3, "归一冲突的库名不得共用索引"


def test_derive_index_name_non_ascii_and_empty_never_collide_with_main():
    main = _derive_es_index_name("mysql+asyncmy://u:p@h:3306/docmind")
    assert _derive_es_index_name("mysql+asyncmy://u:p@h:3306/docmind知识库") != main
    assert _derive_es_index_name("mysql+asyncmy://u:p@h:3306/---") != main


def test_derive_index_name_fallbacks():
    assert _derive_es_index_name("sqlite+aiosqlite:///:memory:") == "docmind_knowledge"
    assert _derive_es_index_name("not-a-url") == "docmind_knowledge"
    # sqlite 文件路径不作为索引名
    assert _derive_es_index_name("sqlite+aiosqlite:///tmp/x.db") == "docmind_knowledge"


def test_explicit_index_name_wins_empty_or_absent_derives():
    from app.core.config import Settings

    explicit = Settings(
        DATABASE_URL="mysql+asyncmy://u:p@h:3306/docmind_rc",
        ELASTICSEARCH_INDEX_NAME="custom_idx",
    )
    _apply_es_index_name(explicit)
    assert explicit.ELASTICSEARCH_INDEX_NAME == "custom_idx"

    empty = Settings(
        DATABASE_URL="mysql+asyncmy://u:p@h:3306/docmind_rc", ELASTICSEARCH_INDEX_NAME=""
    )
    _apply_es_index_name(empty)
    assert empty.ELASTICSEARCH_INDEX_NAME == "docmind_knowledge_rc"

    absent = Settings(DATABASE_URL="mysql+asyncmy://u:p@h:3306/docmind")
    _apply_es_index_name(absent)
    assert absent.ELASTICSEARCH_INDEX_NAME == "docmind_knowledge"


@pytest.fixture
def config_tree(tmp_path, monkeypatch):
    """最小 config 树 + 隔离 env/.env（与 test_config_merge 同款）。"""
    from app.core.config import loader

    config_dir = tmp_path / "config"
    for bundle in ("core", "rag", "agent", "observability"):
        path = config_dir / "bundles" / f"{bundle}.yaml"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("settings: {}\n", encoding="utf-8")
    (config_dir / "profiles").mkdir(parents=True, exist_ok=True)
    (config_dir / "profiles" / "web.yaml").write_text(
        "entrypoint: web\nbundles: [core, rag, agent, observability]\nsettings: {}\n",
        encoding="utf-8",
    )
    (config_dir / "base.yaml").write_text("settings: {}\n", encoding="utf-8")
    monkeypatch.setattr(loader, "CONFIG_DIR", config_dir)
    monkeypatch.setenv("SECRET_KEY", "a" * 32)
    monkeypatch.setenv("JWT_SECRET_KEY", "b" * 32)
    monkeypatch.setenv("DATABASE_URL", "mysql+asyncmy://u:p@localhost:3306/docmind_rc")
    monkeypatch.setenv("ENABLE_LOCAL_EMBEDDING", "true")
    monkeypatch.delenv("ELASTICSEARCH_INDEX_NAME", raising=False)
    monkeypatch.chdir(tmp_path)  # 隔离仓库 backend/.env
    loader.reset_settings()
    yield config_dir
    loader.reset_settings()


def test_load_settings_derives_when_env_absent(config_tree):
    from app.core.config import loader

    assert loader.load_settings("web").ELASTICSEARCH_INDEX_NAME == "docmind_knowledge_rc"


def test_load_settings_derives_when_env_empty(config_tree, monkeypatch):
    from app.core.config import loader

    monkeypatch.setenv("ELASTICSEARCH_INDEX_NAME", "")
    loader.reset_settings()
    assert loader.load_settings("web").ELASTICSEARCH_INDEX_NAME == "docmind_knowledge_rc"


def test_load_settings_keeps_explicit_env(config_tree, monkeypatch):
    from app.core.config import loader

    monkeypatch.setenv("ELASTICSEARCH_INDEX_NAME", "custom_idx")
    loader.reset_settings()
    assert loader.load_settings("web").ELASTICSEARCH_INDEX_NAME == "custom_idx"


def test_load_settings_reads_dotenv_value(config_tree, tmp_path):
    from app.core.config import loader

    (tmp_path / ".env").write_text("ELASTICSEARCH_INDEX_NAME=from_dotenv\n", encoding="utf-8")
    loader.reset_settings()
    assert loader.load_settings("web").ELASTICSEARCH_INDEX_NAME == "from_dotenv"


def test_load_settings_profile_value_wins_over_derivation(config_tree):
    from app.core.config import loader

    (config_tree / "profiles" / "web.yaml").write_text(
        "entrypoint: web\nbundles: [core, rag, agent, observability]\n"
        "settings:\n  ELASTICSEARCH_INDEX_NAME: from_profile\n",
        encoding="utf-8",
    )
    loader.reset_settings()
    assert loader.load_settings("web").ELASTICSEARCH_INDEX_NAME == "from_profile"
