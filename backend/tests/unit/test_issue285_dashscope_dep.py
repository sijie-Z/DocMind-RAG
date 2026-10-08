"""issue #285：`llm_qwen` 节点运行时依赖 `dashscope` 缺失的回归。

背景：`llm_qwen` 在节点面板 / NodeDefinition / executor 注册表中齐备（对外可见、
用户可选中），但其底层 `ChatTongyi.validate_environment`（@pre_init）在**构造期**
即 `import dashscope`——此前 requirements.txt 未声明该包、部署环境也没有，
任何真实构造都必 ImportError（"面板承诺 vs 运行时能力"缺口；#273 评审核实时发现，
按依赖/镜像范畴独立立单）。

本文件两层锁：
1. 静态层：requirements.txt 必须声明 `dashscope` 且带版本区间——下界不高过已实测的
   1.14.0（该版本 wheel 实测同时导出 ChatTongyi 构造所需的 `dashscope.Generation` /
   `MultiModalConversation`），上界排除 2.x（ChatTongyi 按 1.x 调用形态编写）；
2. 运行时层：**不 mock ChatTongyi**，走引擎 `get_llm("qwen")` 的生产路径真实构造
   （构造无网络：validate_environment 只 import + 取属性，不发请求）——证明
   「面板可见的节点在依赖齐备时可构造」。依赖缺失的环境本层直接 ImportError 红，
   与 issue #285 症状同构；CI（uv pip install -r requirements.txt）装齐后有判别力。
"""

import importlib.metadata
import pathlib

from packaging.requirements import Requirement

from app.schemas.workflow import WorkflowNode
from app.services import workflow_engine
from app.services.workflow_engine import NodeExecutor

BACKEND_DIR = pathlib.Path(__file__).resolve().parents[2]

# 下界锚点：1.14.0 起两属性齐备（见 requirements.txt 的 dashscope 注释）。
_VERIFIED_FLOOR = "1.14.0"


def _dashscope_requirement() -> Requirement:
    """从 requirements.txt 取出 dashscope 声明行（整行注释跳过）。"""
    text = (BACKEND_DIR / "requirements.txt").read_text(encoding="utf-8")
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        try:
            req = Requirement(line)
        except Exception:  # 非能解析的 requirements 行（续行、选项等）
            continue
        if req.name.lower() == "dashscope":
            return req
    raise AssertionError(
        "requirements.txt 未声明 dashscope（issue #285：llm_qwen 节点构造必 ImportError）"
    )


class TestRequirementsDeclaration:
    def test_declares_dashscope_with_bounds(self):
        spec = _dashscope_requirement().specifier
        assert spec, "dashscope 应带版本区间（无界会漂到未验证的大版本）"
        assert spec.contains(_VERIFIED_FLOOR), (
            f"下界不应排除已实测可用版本 {_VERIFIED_FLOOR}"
            "（其同时导出 Generation / MultiModalConversation，见模块 docstring）"
        )
        assert not spec.contains("2.0.0"), (
            "上界须排除 2.x（ChatTongyi 按 dashscope 1.x API 编写：Generation.call / output.choices）"
        )

    def test_installed_dashscope_satisfies_declared_range(self):
        """requirements 区间必须覆盖本环境实际安装版本（防区间与 venv 漂移）。"""
        req = _dashscope_requirement()
        installed = importlib.metadata.version("dashscope")
        assert req.specifier.contains(installed), (
            f"本环境 dashscope=={installed} 不满足 requirements 区间 {req.specifier}"
        )


class TestQwenNodeConstruction:
    def test_llm_qwen_constructs_real_chattongyi(self, monkeypatch):
        """不 mock：经生产路径 get_llm("qwen") 真实构造 ChatTongyi（无网络）。"""
        import dashscope
        from langchain_community.chat_models import ChatTongyi

        monkeypatch.setattr(workflow_engine.settings, "DASHSCOPE_API_KEY", "sk-dummy")
        llm = NodeExecutor(WorkflowNode(id="n1", type="llm_qwen")).get_llm("qwen")

        assert isinstance(llm, ChatTongyi)
        assert llm.model_name == "qwen-plus"
        # 构造期 validate_environment 已把 dashscope.Generation 绑到 client——包缺失
        # 或版本过旧（无该属性）时，此处之前就已 ImportError / ValueError（#285 症状）。
        assert llm.client is dashscope.Generation

    def test_multimodal_model_construction_uses_other_client_attr(self):
        """ChatTongyi 按模型名在 Generation / MultiModalConversation 二选一取属性；
        区间下界必须两属性齐备（1.14.0 wheel 实测均导出）。"""
        import dashscope
        from langchain_community.chat_models import ChatTongyi

        llm = ChatTongyi(model="qwen-vl-plus", dashscope_api_key="sk-dummy")
        assert llm.client is dashscope.MultiModalConversation
