"""issue #259：三处「反向死配」字段补齐 + 消费接线的回归测试。

背景：`SEMANTIC_CACHE_THRESHOLD` / `SEMANTIC_CACHE_TTL_SECONDS`（rag/cache.py 的
SemanticCache）与 `RAG_QUERY_REWRITE_TIMEOUT_SECONDS`（rag/retriever.py 的
`_keyword_hits`）此前经 `getattr(settings, …, 默认值)` 读取，但 Settings 类里**没有
对应字段**——`extra="ignore"` 下 .env 配置它们被静默忽略（与 #242 同源、方向相反）。
本文件锁三层：

1. 字段存在且默认值/类型与原 getattr 兜底一致（行为保持锚点）；
2. `.env`（子进程实测，同 test_prometheus_enabled_via_dotenv 模式）与真实环境变量
   （进程内重建 Settings，绕开模块单例缓存）均可覆盖；
3. 消费方读取 settings 上的现读值（改值跟随：语义缓存实例 / 改写超时实参）。

变异验证（先提交后执行，逐一还原成「字段缺失」形态）：
1. 删 `config/ai.py` 的 SEMANTIC_CACHE_THRESHOLD 字段 → 语义缓存用例红；
2. 删 SEMANTIC_CACHE_TTL_SECONDS 字段 → ttl 断言红；
3. 删 RAG_QUERY_REWRITE_TIMEOUT_SECONDS 字段 → 消费方直接读取 AttributeError / 超时断言红。
"""
import json
import os
import pathlib
import subprocess
import sys
import types
from unittest.mock import AsyncMock

import pytest

from app.core.config import Settings, settings
from app.core.config.ai import AISettings

BACKEND_DIR = pathlib.Path(__file__).resolve().parents[2]

# 三键「字段默认值 == 原 getattr 兜底值」锚点（issue #259 修复前形态）。
_DEFAULTS = {
    "SEMANTIC_CACHE_THRESHOLD": 0.92,
    "SEMANTIC_CACHE_TTL_SECONDS": 3600,
    "RAG_QUERY_REWRITE_TIMEOUT_SECONDS": 1.2,
}


class TestFieldDefaults:
    def test_fields_exist_with_getattr_compatible_defaults(self):
        for name, default in _DEFAULTS.items():
            assert name in AISettings.model_fields, (
                f"{name} 应在 Settings 补上字段（issue #259：消费方等字段，配置本体缺失）"
            )
            assert AISettings.model_fields[name].default == default, (
                f"{name} 默认值需与原 getattr 兜底一致"
            )
        assert AISettings.model_fields["SEMANTIC_CACHE_THRESHOLD"].annotation is float
        assert AISettings.model_fields["SEMANTIC_CACHE_TTL_SECONDS"].annotation is int
        assert AISettings.model_fields["RAG_QUERY_REWRITE_TIMEOUT_SECONDS"].annotation is float


class TestEnvOverride:
    def test_real_env_vars_reach_rebuilt_settings(self, monkeypatch):
        """真实环境变量 → 重建 Settings（模块单例是 import 时刻的快照）后字段被覆盖。"""
        monkeypatch.setenv("SEMANTIC_CACHE_THRESHOLD", "0.75")
        monkeypatch.setenv("SEMANTIC_CACHE_TTL_SECONDS", "120")
        monkeypatch.setenv("RAG_QUERY_REWRITE_TIMEOUT_SECONDS", "3.5")
        rebuilt = Settings(_env_file=None)
        assert pytest.approx(0.75) == rebuilt.SEMANTIC_CACHE_THRESHOLD
        assert rebuilt.SEMANTIC_CACHE_TTL_SECONDS == 120
        assert pytest.approx(3.5) == rebuilt.RAG_QUERY_REWRITE_TIMEOUT_SECONDS


_PROBE = (
    "import json;"
    "from app.core.config import settings;"
    "from app.rag.cache import SemanticCache;"
    "c = SemanticCache();"
    "print(json.dumps({'threshold': c.threshold, 'ttl': c.ttl,"
    " 'rewrite_timeout': settings.RAG_QUERY_REWRITE_TIMEOUT_SECONDS}))"
)


def _run_probe(tmp_path, extra_env_lines: list[str]) -> dict:
    """给临时目录一份只含必需键 + 三键的 .env，子进程里读消费方现值。"""
    (tmp_path / ".env").write_text(
        "\n".join(
            [
                "DATABASE_URL=mysql+asyncmy://u:p@localhost:3306/whatever",
                "SECRET_KEY=" + "a" * 32,
                "JWT_SECRET_KEY=" + "b" * 32,
                *extra_env_lines,
            ]
        ).strip()
        + "\n",
        encoding="utf-8",
    )
    # 剥掉继承环境里的同名键，逼真复现「.env 是唯一来源」。
    env = {k: v for k, v in os.environ.items() if k not in _DEFAULTS}
    env["PYTHONPATH"] = str(BACKEND_DIR)
    env["PYTHONUTF8"] = "1"
    r = subprocess.run(
        [sys.executable, "-c", _PROBE],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert r.returncode == 0, r.stderr[-1500:]
    return json.loads(r.stdout.strip().splitlines()[-1])


def test_dotenv_overrides_reach_consumer(tmp_path):
    """.env 三键 → 单例 settings → 消费方拿到新值（修复前被 extra="ignore" 吞掉）。"""
    got = _run_probe(
        tmp_path,
        [
            "SEMANTIC_CACHE_THRESHOLD=0.75",
            "SEMANTIC_CACHE_TTL_SECONDS=120",
            "RAG_QUERY_REWRITE_TIMEOUT_SECONDS=3.5",
        ],
    )
    assert got == {"threshold": 0.75, "ttl": 120, "rewrite_timeout": 3.5}, (
        f".env 配置未到达消费方（issue #259），实际 {got}"
    )


class TestConsumerWiring:
    def test_semantic_cache_reads_settings(self, monkeypatch):
        """settings 改值 → 两种 SemanticCache 实例（默认 / 检索专用）都跟随。"""
        from app.rag.cache import SemanticCache

        monkeypatch.setattr(settings, "SEMANTIC_CACHE_THRESHOLD", 0.75)
        monkeypatch.setattr(settings, "SEMANTIC_CACHE_TTL_SECONDS", 120)
        cache = SemanticCache()
        assert cache.threshold == 0.75, "阈值未接线：仍读旧 getattr 兜底或硬编码（issue #259）"
        assert cache.ttl == 120, "TTL 未接线：仍读旧 getattr 兜底或硬编码（issue #259）"
        retrieval = SemanticCache.for_retrieval()
        assert retrieval.threshold == 0.75 and retrieval.ttl == 120

    @pytest.mark.asyncio
    async def test_rewrite_timeout_reads_settings(self, monkeypatch):
        """settings 改值 → `_keyword_hits` 传给 asyncio.wait_for 的超时跟随。"""
        import app.rag.retriever as retriever_mod

        captured: list[float] = []
        real_wait_for = retriever_mod.asyncio.wait_for

        async def capture_wait_for(coro, timeout):  # 与真实实现同签名，转发只加捕获
            captured.append(timeout)
            return await real_wait_for(coro, timeout)

        # 只替换 retriever 模块命名空间里的 asyncio（不动 stdlib 全局对象）。
        monkeypatch.setattr(
            retriever_mod,
            "asyncio",
            types.SimpleNamespace(wait_for=capture_wait_for, gather=retriever_mod.asyncio.gather),
        )
        search = AsyncMock(return_value={"hits": {"hits": []}})
        monkeypatch.setattr(retriever_mod.ElasticsearchTools, "search_documents", search)
        monkeypatch.setattr(retriever_mod.settings, "RAG_ENABLE_QUERY_REWRITE", True)
        monkeypatch.setattr(retriever_mod.settings, "RAG_QUERY_REWRITE_TIMEOUT_SECONDS", 3.5)

        retriever = retriever_mod.HybridRetriever(openai_client=None)
        await retriever._keyword_hits("语义缓存接线测试", [], candidate_size=5)

        assert captured == [3.5], (
            f"改写等待超时未跟随 settings.RAG_QUERY_REWRITE_TIMEOUT_SECONDS（issue #259），实际 {captured}"
        )

    @pytest.mark.asyncio
    async def test_rewrite_timeout_zero_floors_to_point_two(self, monkeypatch):
        """issue #273 顺带 B（评审 Mu-D1）：0/负值 → `max(0.2, ·)` 下限常驻钉住。

        消费跟随用例对「直读 vs getattr + `or 1.2` 回退」不可判别（字段存在时两式
        对一切非 0 值等价），唯一分歧点是 0：新语义是显式钳到 0.2 下限，而不是回退
        1.2。本用例专门锁这个分歧点，防止未来无意回退。
        """
        import app.rag.retriever as retriever_mod

        captured: list[float] = []
        real_wait_for = retriever_mod.asyncio.wait_for

        async def capture_wait_for(coro, timeout):
            captured.append(timeout)
            return await real_wait_for(coro, timeout)

        monkeypatch.setattr(
            retriever_mod,
            "asyncio",
            types.SimpleNamespace(wait_for=capture_wait_for, gather=retriever_mod.asyncio.gather),
        )
        search = AsyncMock(return_value={"hits": {"hits": []}})
        monkeypatch.setattr(retriever_mod.ElasticsearchTools, "search_documents", search)
        monkeypatch.setattr(retriever_mod.settings, "RAG_ENABLE_QUERY_REWRITE", True)

        for configured, expected in ((0, 0.2), (-5, 0.2)):
            captured.clear()
            monkeypatch.setattr(
                retriever_mod.settings, "RAG_QUERY_REWRITE_TIMEOUT_SECONDS", configured
            )
            retriever = retriever_mod.HybridRetriever(openai_client=None)
            await retriever._keyword_hits(f"下限钉住 {configured}", [], candidate_size=5)
            assert captured == [expected], (
                f"配置 {configured} 应钳到下限 {expected}（显式 0 ≠ 回退 1.2，issue #273），"
                f"实际 {captured}"
            )
