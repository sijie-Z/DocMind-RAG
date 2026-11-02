"""ES 索引名按库派生（环境隔离，T1）与显式配置优先级。"""

from app.core.config.loader import _apply_es_index_name, _derive_es_index_name


def test_derive_index_name_from_database_name():
    assert _derive_es_index_name("mysql+asyncmy://u:p@h:3306/docmind") == "docmind_knowledge"
    assert _derive_es_index_name("mysql+aiomysql://u:p@h:3306/docmind_rc") == "docmind_knowledge_rc"
    assert _derive_es_index_name("mysql+asyncmy://u:p@h:3306/docmind-it") == "docmind_knowledge_it"
    assert _derive_es_index_name("mysql+asyncmy://u:p@h:3306/test_db") == "test_db_knowledge"


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
