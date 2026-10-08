"""issue #273：workflow_engine.py 三处同形态反向缺口（getattr 单引号兜底）的回归测试。

背景：`services/workflow_engine.py` 的 get_llm 此前用
`getattr(settings, 'OPENAI_API_KEY', '')`（另两处单引号 `'AIPING_BASE_URL'` /
`'DASHSCOPE_API_KEY'`）兜底读取，但 Settings 无对应字段——extra="ignore" 下 .env
配置被静默吞掉，工作流 LLM 节点的环境默认 key 恒为空串（与 #259 同族缺口；#242 /
#259 两轮全仓扫描因单引号口径漏网）。

裁定（以调用链为证据）：
- OPENAI_API_KEY / DASHSCOPE_API_KEY：provider 为真实可选通道（前端节点面板
  llm_openai / llm_qwen、后端 NodeDefinition、executor 注册表）→ 补 Settings
  字段（config/ai.py），引擎改直读；
- 与 openai 共行的 deepseek 分支改读既有真字段 DEEPSEEK_API_KEY（该分支固定走
  api.deepseek.com 端点，OpenAI key 无效；同 dependencies.py 口径）；
- AIPING_BASE_URL 连同 "aiping" 分支：全仓无任何 llm_aiping 生产路径（前端面板 /
  NodeDefinition / schema / 文档 / 测试均无；llm_config 里的「AI平」标识是
  ai_ping，属另一套体系）→ 判定废弃残留，读取连同死分支删除。

本文件四层锁：
1. 字段锚点：两个新字段存在、默认 ''、类型 str；
2. .env 端到端（子进程探针，同 test_issue259 模式）：.env 三 key 经真实 get_llm
   到达各客户端构造实参；
3. 进程内接线：settings 改值跟随 + 节点级 llm_settings.api_key 优先级保持；
4. 死路径不存在（llm_aiping 抛 Unknown node type、源码无 aiping 残留）+
   全仓扫描口径常驻：app/ 下所有 `getattr(settings, "KEY")` 的字面 KEY 必须是
   Settings 真字段，单双引号两口径同扫（把两轮漏检的形态钉死）。
"""
import json
import os
import pathlib
import re
import subprocess
import sys

import pytest
from pydantic import ValidationError

from app.core.config import Settings
from app.core.config.ai import AISettings
from app.schemas.workflow import WorkflowConfig, WorkflowNode
from app.services import workflow_engine
from app.services.workflow_engine import NodeExecutor, WorkflowEngine

BACKEND_DIR = pathlib.Path(__file__).resolve().parents[2]

# 两个新补字段（类型/默认须与原 getattr 兜底一致）。
_NEW_KEYS = ("OPENAI_API_KEY", "DASHSCOPE_API_KEY")
# #273 顺带 A 的 bounds 目标（#259 补的三字段）。
_SCOPED_KEYS = (
    "SEMANTIC_CACHE_THRESHOLD",
    "SEMANTIC_CACHE_TTL_SECONDS",
    "RAG_QUERY_REWRITE_TIMEOUT_SECONDS",
)


class TestFieldAnchors:
    def test_provider_key_fields_match_getattr_defaults(self):
        for name in _NEW_KEYS:
            assert name in AISettings.model_fields, (
                f"{name} 应在 Settings 补字段（issue #273：getattr 单引号兜底读取但无字段）"
            )
            info = AISettings.model_fields[name]
            assert info.default == "", f"{name} 默认值需与原 getattr 兜底一致（''）"
            assert info.annotation is str, f"{name} 类型需与原 getattr 兜底一致（str）"


_PROBE = """
import json
from app.services import workflow_engine as we
from app.schemas.workflow import WorkflowNode

calls = {"chat": [], "tongyi": {}}

class FakeChatOpenAI:
    def __init__(self, **kw):
        calls["chat"].append(kw)

class FakeTongyi:
    def __init__(self, **kw):
        calls["tongyi"] = kw

we.ChatOpenAI = FakeChatOpenAI
import langchain_community.chat_models as _lccm
_lccm.ChatTongyi = FakeTongyi

for node_type in ("llm_openai", "llm_deepseek", "llm_qwen"):
    node = WorkflowNode(id="n1", type=node_type)
    we.NodeExecutor(node).get_llm(node_type.replace("llm_", ""))

print(json.dumps(calls))
"""


def _run_probe(tmp_path, extra_env_lines: list[str]) -> dict:
    """给临时目录一份只含必需键 + 三 provider key 的 .env，子进程里走真实 get_llm。"""
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
    # 剥掉继承环境里的同名键（conftest 会注入 DEEPSEEK_API_KEY 等），逼真复现
    # 「.env 是唯一来源」。
    strip = {*_NEW_KEYS, "DEEPSEEK_API_KEY"}
    env = {k: v for k, v in os.environ.items() if k not in strip}
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


def test_dotenv_provider_keys_reach_llm_clients(tmp_path):
    """.env 三 key → 单例 settings → get_llm 实参（修复前字段缺失，全落 ''）。"""
    got = _run_probe(
        tmp_path,
        [
            "OPENAI_API_KEY=sk-env-openai",
            "DEEPSEEK_API_KEY=sk-env-deepseek",
            "DASHSCOPE_API_KEY=sk-env-dashscope",
        ],
    )
    chat = got["chat"]
    assert len(chat) == 2, f"openai/deepseek 各构造一次，实际 {chat}"
    assert chat[0]["api_key"] == "sk-env-openai", ".env OPENAI_API_KEY 未到达 openai 节点"
    assert chat[0]["base_url"] is None, "openai 节点不应有默认 base_url"
    assert chat[1]["api_key"] == "sk-env-deepseek", (
        "deepseek 节点应读 DEEPSEEK_API_KEY（该分支固定走 api.deepseek.com 端点）"
    )
    assert chat[1]["base_url"] == "https://api.deepseek.com/v1"
    assert got["tongyi"]["dashscope_api_key"] == "sk-env-dashscope", (
        ".env DASHSCOPE_API_KEY 未到达 qwen 节点"
    )
    assert got["tongyi"]["model"] == "qwen-plus"


class TestGetLlmWiring:
    def test_settings_values_reach_chat_clients(self, monkeypatch):
        calls: list[dict] = []

        class FakeChatOpenAI:
            def __init__(self, **kw):
                calls.append(kw)

        monkeypatch.setattr(workflow_engine, "ChatOpenAI", FakeChatOpenAI)
        monkeypatch.setattr(workflow_engine.settings, "OPENAI_API_KEY", "ok-1")
        monkeypatch.setattr(workflow_engine.settings, "DEEPSEEK_API_KEY", "dk-1")

        NodeExecutor(WorkflowNode(id="n1", type="llm_openai")).get_llm("openai")
        NodeExecutor(WorkflowNode(id="n2", type="llm_deepseek")).get_llm("deepseek")

        assert calls[0]["api_key"] == "ok-1", "openai 节点未跟随 settings.OPENAI_API_KEY"
        assert calls[1]["api_key"] == "dk-1", "deepseek 节点未跟随 settings.DEEPSEEK_API_KEY"
        assert calls[1]["base_url"] == "https://api.deepseek.com/v1"

    def test_node_level_api_key_takes_precedence(self, monkeypatch):
        """llm_settings（引擎级配置）优先级保持：非空时压过环境字段。"""
        calls: list[dict] = []

        class FakeChatOpenAI:
            def __init__(self, **kw):
                calls.append(kw)

        monkeypatch.setattr(workflow_engine, "ChatOpenAI", FakeChatOpenAI)
        monkeypatch.setattr(workflow_engine.settings, "OPENAI_API_KEY", "env-key")
        # NodeExecutor.__init__ 从 config["llm"] 取各 model_type 的节点级配置。
        cfg = {
            "llm": {
                "openai": {
                    "api_key": "node-key",
                    "base_url": "https://proxy.example/v1",
                    "temperature": 0.1,
                }
            }
        }
        NodeExecutor(WorkflowNode(id="n1", type="llm_openai"), cfg).get_llm("openai")

        assert calls[0]["api_key"] == "node-key"
        assert calls[0]["base_url"] == "https://proxy.example/v1"
        assert calls[0]["temperature"] == 0.1

    def test_qwen_reads_dashscope_field(self, monkeypatch):
        calls: dict = {}

        class FakeTongyi:
            def __init__(self, **kw):
                calls.update(kw)

        import langchain_community.chat_models as lccm

        monkeypatch.setattr(lccm, "ChatTongyi", FakeTongyi)
        monkeypatch.setattr(workflow_engine.settings, "DASHSCOPE_API_KEY", "ds-1")

        NodeExecutor(WorkflowNode(id="n1", type="llm_qwen")).get_llm("qwen")

        assert calls["dashscope_api_key"] == "ds-1"
        assert calls["model"] == "qwen-plus"


class TestAipingDeadPath:
    def test_llm_aiping_node_is_unknown_type(self):
        """死分支删除后，llm_aiping 节点在注册表层面即被拒绝（不再有僵尸执行器）。"""
        node = WorkflowNode(id="n1", type="llm_aiping")
        engine = WorkflowEngine(WorkflowConfig(nodes=[node], edges=[]))
        with pytest.raises(ValueError, match="Unknown node type"):
            engine.get_executor(node)

    def test_engine_source_has_no_aiping_leftovers(self):
        src = (BACKEND_DIR / "app" / "services" / "workflow_engine.py").read_text(
            encoding="utf-8"
        )
        assert "aiping" not in src.lower(), "aiping 死分支残留（issue #273 已裁定删除）"
        assert "AIPING_BASE_URL" not in AISettings.model_fields, (
            "AIPING_BASE_URL 不应作为字段补入（无真实消费通道）"
        )


_GETATTR_KEY_RE = re.compile(r"getattr\(\s*settings\s*,\s*(['\"])([A-Za-z0-9_]+)\1")


def test_app_has_no_getattr_settings_reverse_gaps():
    """全仓扫描口径常驻（issue #273）：app/ 全量 .py 中所有 `getattr(settings, "KEY")`
    / `getattr(settings, 'KEY')` 的字面 KEY 必须是 Settings 真字段——否则 extra="ignore"
    下 .env 静默无效。单双引号两口径同扫（#242 / #259 两轮分别漏在既定口径上）；
    整行注释跳过（注释里引用历史写法不是真实读取点）。"""
    fields = set(Settings.model_fields)
    holes: list[str] = []
    for path in sorted((BACKEND_DIR / "app").rglob("*.py")):
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if line.lstrip().startswith("#"):
                continue
            for m in _GETATTR_KEY_RE.finditer(line):
                if m.group(2) not in fields:
                    holes.append(f"{path.relative_to(BACKEND_DIR)}:{lineno} {m.group(2)}")
    assert not holes, "getattr(settings, …) 反向缺口（键不在 Settings 字段表中）: " + "; ".join(holes)


class TestBoundsForIssue259Fields:
    """issue #273 顺带 A：#259 补的三字段补合理性校验（阈值 0–1、TTL ≥0、超时 ≥0）。"""

    @pytest.mark.parametrize(
        "kwargs",
        [
            {"SEMANTIC_CACHE_THRESHOLD": -0.5},
            {"SEMANTIC_CACHE_THRESHOLD": 1.01},
            {"SEMANTIC_CACHE_TTL_SECONDS": -1},
            {"RAG_QUERY_REWRITE_TIMEOUT_SECONDS": -0.1},
        ],
    )
    def test_out_of_range_rejected(self, monkeypatch, kwargs):
        for key in (*_SCOPED_KEYS, *kwargs):
            monkeypatch.delenv(key, raising=False)
        with pytest.raises(ValidationError):
            AISettings(_env_file=None, **kwargs)

    def test_boundary_values_accepted_and_default_anchor_kept(self, monkeypatch):
        for key in _SCOPED_KEYS:
            monkeypatch.delenv(key, raising=False)
        s = AISettings(
            _env_file=None,
            SEMANTIC_CACHE_THRESHOLD=1.0,
            SEMANTIC_CACHE_TTL_SECONDS=0,
            RAG_QUERY_REWRITE_TIMEOUT_SECONDS=0.0,
        )
        assert s.SEMANTIC_CACHE_THRESHOLD == 1.0
        assert s.SEMANTIC_CACHE_TTL_SECONDS == 0
        assert s.RAG_QUERY_REWRITE_TIMEOUT_SECONDS == 0.0
        # 默认值锚点不受 bounds 改造影响（#259 行为保持）。
        assert AISettings(_env_file=None).SEMANTIC_CACHE_THRESHOLD == 0.92
        assert AISettings(_env_file=None).SEMANTIC_CACHE_TTL_SECONDS == 3600
        assert AISettings(_env_file=None).RAG_QUERY_REWRITE_TIMEOUT_SECONDS == 1.2
