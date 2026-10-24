"""HITL 审批门 —— 工具执行前的人工审批判定表与决策协议。

背景（对应 `_decisions-need-you.md` 第 8 条的最终处置）：
- 旧 `escalation.py` 是"构造即丢"的死配置（定义完整、全仓零读取），第 8 条
  默认建议是删。本模块把"人在环"补成真：**审批门是活实现**，旧文件的语义
  里仍然成立的部分（人类的决定是正常结局、超时不是拒绝）在下方协议中显式
  承接，文件本身删除（`git show 81f6509` 可完整取回，设计文档 §4/§7.4 契约
  仍在 `backend/docs/agent-reliability-design.md`）。
- 本模块**不引入**旧文件的失败计数阈值（consecutive_tool_failures 等）：
  那些阈值对应的"失败→升级人工"通道没有被接线，复活它们等于再造一批死旋钮。

协议（与 loop/executor/checkpoint/端点的接线见各处 docstring）：

- ``approval_mode`` ∈ {never, risky, always}（`AgentConfig.approval_mode`）：
    never  — 不审批（完全旧行为）；
    risky  — **high** 风险工具需审批（默认值）；
    always — 所有工具调用需审批。
- 风险判定 = ``tool_risk_level()``：工具在注册表**声明**的 ``risk_level``
  优先（装饰元数据真实化），未声明回落到本模块的集中判定表
  ``TOOL_RISK_LEVELS``；mutating / long_running 语义兜底 medium。
- 审批**绑定到具体行动**：``(tool_name, args_fingerprint)``。人类批准的/
  拒绝的不是"某工具"，而是"这次工具+这组参数"。同一 run 内对同一
  (tool, args) 的决定**粘滞**（批准后重跑步骤再发同一调用不再打扰、
  拒绝后同类调用直接结构化失败），既不重复打扰、也不放行换参数的调用。
- 人类决定是正常结局：拒绝（deny）**不是**工具错误语义上的"执行失败"，
  而是以 ``approval_denied`` 结构化失败喂给反思器（reflector 让它继续/收敛）；
  审批超时**绝不**伪造成拒绝（见 `AgentConfig.approval_timeout_seconds`：
  默认 0 = 无限等待；>0 时在读取/应答点惰性判定，如实置 failed + 原因
  ``approval_timeout``，不落任何假"用户拒绝了"的记录）。

控制流异常（executor 抛、loop 捕获后收尾）：
- ``ApprovalRequired``：审批回调已把暂停状态**持久化完成**（快照 + 任务态
  ``waiting_approval``）之后才抛出。SDK/loop 见到它 = 平静结束本 run，等待
  人类应答——绝不继续执行、绝不置终态。
- ``RunCancelled``：协作式取消的收敛信号（步间/工具前检查点观察到取消旗）。
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, field
from typing import Any, Literal

ApprovalMode = Literal["never", "risky", "always"]
ApprovalDecision = Literal["approve", "deny"]

VALID_RISK_LEVELS = ("low", "medium", "high")

# ── 集中判定表（单一事实源）──────────────────────────────────────────────────
# 判定原则：**host 侧副作用 / 越过沙箱边界 / 执行任意代码** 的工具才配 high。
# 与 config.DEFAULT_DISABLED_TOOLS 的"高危"标注同源（那三个工具本就默认禁单
# 挡住——审批门在它们被显式启用之后生效；默认配置下 risky 模式的审批量 = 0，
# 既有演示/测试流零扰动，见测试 test_approval_default_risky_gates_only_high）。
TOOL_RISK_LEVELS: dict[str, str] = {
    # ── high：执行任意代码 / 任意 SQL / 子进程 MCP（宿主/外联副作用面）──
    "execute_python": "high",
    "execute_sql": "high",
    "mcp_call": "high",
    # ── medium：跨信任边界或昂贵（只读，但外部网络 / 长任务 / 扇出）──
    # risky 模式不拦（读操作，且为常见演示流）；always 模式拦；供 UI 徽标与
    # 后续分级策略（allow 规则）消费。
    "fetch_webpage": "medium",
    "web_search": "medium",
    "deep_research": "medium",
    "delegate_subtask": "medium",
}
# 未列出 + 未声明 → low（知识库只读面是绝大多数工具）。


def tool_risk_level(name: str) -> str:
    """工具的风险等级：注册表声明 > 集中判定表 > 语义兜底 > low。

    - 注册表声明（``ToolEntry.risk_level``，v2.2 字段）：工具作者在 ``@register_tool``
      上显式声明的等级（"low"/"medium"/"high"），最高优先——这是"把装饰元数据
      接成判定表"的接线点；
    - 集中判定表（``TOOL_RISK_LEVELS``）：内置工具的显式分级；
    - semantic 兜底：``type`` 为 mutating / long_running 的工具至少 medium
      （判定表漏登记时不会静默放行一个写操作）；
    - 其余 → low。

    issue #509：入口先 canonicalize 别名——旧名 ``calculate`` 若指向 high 的规范
    工具，这里必须按规范名返回 high（否则审批门会被别名绕过）。
    """
    from app.agent.registry import canonical_tool_name, tool_registry

    name = canonical_tool_name(name)
    entry = None
    try:
        entry = tool_registry.get(name)
    except Exception:  # pragma: no cover — 注册表不可用不是执行路径
        entry = None

    if entry is not None:
        declared = getattr(entry, "risk_level", "") or ""
        if declared in VALID_RISK_LEVELS:
            return declared

    if name in TOOL_RISK_LEVELS:
        return TOOL_RISK_LEVELS[name]

    semantic = getattr(entry, "semantic", None) if entry is not None else None
    if isinstance(semantic, dict) and semantic.get("type") in ("mutating", "long_running"):
        return "medium"

    return "low"


def is_terminal_tool(name: str) -> bool:
    """是否 terminal 族工具（按注册表 tag/category 判定，不靠名字前缀）。

    issue #509：旧实现用 ``startswith("terminal_")``，伞形名（如 ``terminal`` 或
    任何未来别名/包装名）会漏掉强制审批。判定改为：规范名对应的注册条目
    ``category == "terminal"`` 或 tags 含 ``terminal``。未注册的名字（如直接
    构造的调用）回退到前缀判定，保持 #477 的 fail-closed 语义。
    """
    from app.agent.registry import canonical_tool_name, tool_registry

    canonical = canonical_tool_name(name)
    try:
        entry = tool_registry.get(canonical)
    except Exception:  # pragma: no cover
        entry = None
    if entry is not None:
        return getattr(entry, "category", "") == "terminal" or "terminal" in (
            getattr(entry, "tags", None) or []
        )
    return canonical.startswith("terminal_") or canonical == "terminal"


def requires_approval(mode: str, tool_name: str) -> bool:
    """该模式 × 该工具是否需要执行前审批（别名先 canonicalize）。"""
    if mode == "never":
        return False
    if mode == "always":
        return True
    # risky：只拦 high。medium 是"值得展示的等级"，不是判决。
    return tool_risk_level(tool_name) == "high"


def args_fingerprint(args: dict[str, Any] | None) -> str:
    """参数指纹：规范化 JSON 的 sha256 前 16 位。

    人类审批的对象是"这次调用"= 工具名 + 这组参数；重跑时模型若发出**不同**
    参数（哪怕同名工具），指纹不同 → 需要新的决定（绝不放行未审批过的行动）。
    """
    canonical = json.dumps(args or {}, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]


def find_decision(
    decisions: list[dict[str, Any]] | None,
    tool_name: str,
    args: dict[str, Any] | None,
) -> dict[str, Any] | None:
    """在决定账本里找 (tool, args) 的粘滞决定；后写覆盖先写。"""
    fp = args_fingerprint(args)
    found: dict[str, Any] | None = None
    for d in decisions or []:
        if not isinstance(d, dict):
            continue
        if d.get("tool_name") == tool_name and d.get("args_hash") == fp:
            found = d
    return found


# ── 审批请求 / 决定记录（快照 run_state 的序列化形状）──────────────────────


@dataclass
class ApprovalRequest:
    """一个待审批的具体行动（暂停时冻结进快照 run_state.pending_approval）。"""

    tool_name: str
    tool_args: dict[str, Any] = field(default_factory=dict)
    tool_call_id: str = ""
    risk_level: str = "high"
    reason: str = ""
    step_id: str = ""
    plan_id: str = ""
    requested_at: float = field(default_factory=time.time)
    # 审批时限（`AgentConfig.approval_timeout_seconds` > 0 时非空；epoch 秒）。
    # 超时的诚实语义见模块头：绝不伪装成"拒绝"。
    expires_at: float | None = None
    # 同一轮里还有几个同时待审批的调用（只为提示人类"还有后手"，不拆分单个请求）。
    more_pending: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "tool_name": self.tool_name,
            "tool_args": self.tool_args,
            "tool_call_id": self.tool_call_id,
            "args_hash": args_fingerprint(self.tool_args),
            "risk_level": self.risk_level,
            "reason": self.reason,
            "step_id": self.step_id,
            "plan_id": self.plan_id,
            "requested_at": self.requested_at,
            "expires_at": self.expires_at,
            "more_pending": self.more_pending,
        }


def build_approval_reason(mode: str, tool_name: str, risk_level: str) -> str:
    """人类可读的审批理由（进 SSE content 与任务状态视图）。"""
    if mode == "always":
        return f"审批模式 always：调用工具 {tool_name}（风险 {risk_level}）需人工确认后执行"
    return f"高风险工具 {tool_name}（风险等级 {risk_level}）执行前需人工审批"


def approval_expired(pending: dict[str, Any] | None, *, now: float | None = None) -> bool:
    """待审批是否已过时限（expires_at 为空 = 无限等待，永不超时）。"""
    if not isinstance(pending, dict):
        return False
    expires_at = pending.get("expires_at")
    if not isinstance(expires_at, (int, float)) or isinstance(expires_at, bool):
        return False
    return (now or time.time()) > float(expires_at)


# ── 控制流异常（executor → loop）────────────────────────────────────────────


class ApprovalRequired(Exception):
    """执行器已停在审批点上：暂停状态（快照 + 任务态）已持久化，等待人类应答。

    只在审批回调返回 "paused"（持久化成功）后抛出——回调失败时执行器走
    结构化降级（approval_unavailable），绝不抛本异常让 run 悬空。
    """

    def __init__(self, request: ApprovalRequest):
        self.request = request
        super().__init__(f"approval required: {request.tool_name}")


class RunCancelled(Exception):
    """协作式取消在检查点被观察到：本 run 收敛为 cancelled（诚实终态）。"""

    def __init__(self, reason: str = "user_cancelled"):
        self.reason = reason
        super().__init__(reason)
