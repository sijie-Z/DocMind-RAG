"""issue #206 —— AST 沙箱超时杀不死线程 / auto 无 Docker 静默回落（先红后绿）。

背景（issue #206 基线实测）：
- 旧实现 `await asyncio.wait_for(loop.run_in_executor(None, _run), timeout=10)`
  只是**放弃等待**：`while True: pass` 的线程继续自旋；`redirect_stdout/stderr`
  是进程级的，超时路径把 StringIO 永久挂在 sys.stdout 上；`asyncio.run()` 还会在
  `shutdown_default_executor()` 里永久等待该线程（复现脚本只能 os._exit 兜底）。
  Python 没有杀线程的 API。
- `SANDBOX_MODE=auto` 在无 Docker 时**静默回落**进程内 AST，且不打任何 warning；
  `docker` 分支的 fallback 也被 `run_in_docker` 自己吞异常，导致几乎不可达。

修复契约（本文件即判别用例）：
1. AST 兜底改为**子进程**执行（与 docker_sandbox 同款
   ``create_subprocess_exec(sys.executable, "-I", "-c", wrapper)``，代码经 stdin
   传入）；超时 ``proc.kill(); await proc.wait()`` —— 返回即不再消耗 CPU、
   无残留线程、不劫持 sys.stdout/stderr。
2. 只有显式 ``SANDBOX_MODE=ast`` 允许进程内（现为子进程）执行；auto/docker 在
   Docker 不可用时 **fail-closed**，返回 ``ToolResult.fail(code="sandbox_unavailable")``。
   必须是结构化失败：``registry.execute_detailed`` 会把普通错误串包成
   ``success=True``（registry.py 的 "Legacy handler returned a string" 分支）。
3. 解析出的 sandbox 写入 ``ToolMeta``，并随 SSE ``tool_result`` 的 ``sandbox``
   字段暴露。

变异点（评审可据此验判别力）：
- 把 ``_run_ast_subprocess`` 换回 ``run_in_executor`` → 死循环用例红
  （残留线程 / 返回后 CPU 继续燃烧 / sys.stdout 被 StringIO 占住）；
- auto 恢复静默回落 ast → fail-closed 用例红（返回 "45" 且 registry 包成功）；
- 去掉 ToolResult.fail、直接返回错误串 → registry 包装用例红；
- 去掉 ToolMeta.sandbox 或 executor 不透传 → SSE 用例红。
"""

import asyncio
import io
import logging
import sys
import threading
import time

import pytest
from pydantic import ValidationError

import app.agent.service  # noqa: F401 — 触发全部工具注册（registry 用例需要）
from app.agent.events import AgentEvent
from app.agent.registry import ToolResult, tool_registry
from app.agent.tools import code_execution, docker_sandbox
from app.agent.tools.code_execution import execute_python
from app.core.config import settings


class TestSubprocessTimeout:
    """超时必须真正终止执行，而不是继续占用线程/CPU/stdout。"""

    def test_spinning_code_is_killed_not_abandoned(self, monkeypatch):
        """判别：超时返回后 CPU 不再被用户代码消耗。

        变异点（旧实现）：run_in_executor 的线程无法被杀 → 返回后 0.5s 墙钟内
        进程 CPU 时间继续增长（基线复现实测 0.52s），asyncio_0 线程仍存活，
        且 sys.stdout 被挂起线程的 StringIO 占住。
        """
        monkeypatch.setattr(code_execution, "_AST_EXEC_TIMEOUT_SECONDS", 1.0)
        monkeypatch.setattr(settings, "SANDBOX_MODE", "ast")

        stdout_before, stderr_before = sys.stdout, sys.stderr
        threads_before = set(threading.enumerate())

        t0 = time.monotonic()
        result = asyncio.run(execute_python(code="while True: pass"))
        elapsed = time.monotonic() - t0

        assert elapsed < 8.0, f"超时没有生效，调用耗时 {elapsed:.1f}s"
        assert isinstance(result, ToolResult), (
            "超时必须返回结构化失败（ToolResult），普通错误串会被 registry 包成成功"
        )
        assert result.success is False
        assert result.error is not None and result.error.code == "timeout"
        assert result.meta.sandbox == "ast", "失败结果也要带上是哪个沙箱执行的"

        # 旧实现：超时路径把 redirect_stdout/redirect_stderr 的 StringIO 永久挂上
        assert sys.stdout is stdout_before and not isinstance(sys.stdout, io.StringIO)
        assert sys.stderr is stderr_before and not isinstance(sys.stderr, io.StringIO)

        # 旧实现：run_in_executor 的线程在超时后仍然存活并继续自旋。
        # POSIX 上 asyncio 的子进程 child-watcher 会短暂创建 daemon 线程，给
        # 2s 收敛窗口；旧实现的失控线程永不退出，因此判别力不受影响。
        deadline = time.monotonic() + 2.0
        leaked = [t for t in threading.enumerate() if t not in threads_before and t.is_alive()]
        while leaked and time.monotonic() < deadline:
            time.sleep(0.05)
            leaked = [t for t in threading.enumerate() if t not in threads_before and t.is_alive()]
        assert not leaked, f"超时后仍有残留线程: {[(t.name, t.is_alive()) for t in leaked]}"

        # 旧实现：被放弃的执行线程在返回后继续烧 CPU（0.5s 墙钟 ≈ 0.5s CPU）
        cpu0 = time.process_time()
        time.sleep(0.5)
        burned = time.process_time() - cpu0
        assert burned < 0.3, f"超时返回后 CPU 仍在被消耗: {burned:.2f}s / 0.5s 墙钟"

    def test_timeout_kills_and_reaps_child_process(self, monkeypatch):
        """判别子进程本身被杀死（父进程 CPU 看不到泄漏的**子进程**的消耗）。

        变异点：超时后只放弃等待、不 `proc.kill(); await proc.wait()` →
        子进程仍存活，returncode 保持 None → 本用例红。
        """
        created = []
        real_create = asyncio.create_subprocess_exec

        async def _capture(*args, **kwargs):
            proc = await real_create(*args, **kwargs)
            created.append(proc)
            return proc

        monkeypatch.setattr(code_execution.asyncio, "create_subprocess_exec", _capture)

        with pytest.raises(TimeoutError):
            asyncio.run(code_execution._run_ast_subprocess("while True: pass", timeout=0.5))

        assert created, "应启动子进程执行"
        assert created[0].returncode is not None, (
            "超时后子进程必须被 kill + wait 回收（returncode 已落定），而不是被遗弃"
        )

    def test_cancellation_kills_child_process(self, monkeypatch):
        """取消（客户端断连 / 服务端关停）后也必须回收子进程（评审 M1）。

        变异点：只在 `except TimeoutError` 里 kill+wait → 取消路径绕过清理，
        子进程 returncode 保持 None、死循环子进程永久燃烧 CPU → 本用例红。
        """
        created = []
        real_create = asyncio.create_subprocess_exec

        async def _capture(*args, **kwargs):
            proc = await real_create(*args, **kwargs)
            created.append(proc)
            return proc

        monkeypatch.setattr(code_execution.asyncio, "create_subprocess_exec", _capture)

        async def _scenario():
            task = asyncio.create_task(
                code_execution._run_ast_subprocess("while True: pass", timeout=30)
            )
            for _ in range(100):
                if created:
                    break
                await asyncio.sleep(0.05)
            assert created, "子进程未在预期时间内启动"
            await asyncio.sleep(0.2)  # 让死循环真正跑起来
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            # 取消路径不阻塞等待；reap 在后台完成，给它一个收敛窗口。
            for _ in range(60):
                if created[0].returncode is not None:
                    break
                await asyncio.sleep(0.05)
            assert created[0].returncode is not None, "取消后子进程必须被 kill + reap，而不是被遗弃"

        asyncio.run(_scenario())


class TestAstSubprocessContract:
    """子进程化后，对调用方可见的输出契约保持不变。"""

    def test_normal_output_unchanged(self, monkeypatch):
        monkeypatch.setattr(settings, "SANDBOX_MODE", "ast")
        result = asyncio.run(execute_python(code="print(sum(range(10)))"))
        assert isinstance(result, ToolResult) and result.success
        assert str(result) == "45", "str(ToolResult) 必须仍是纯 stdout 文本"
        assert result.meta.sandbox == "ast"

    def test_no_output_message(self):
        assert "(Code executed successfully" in str(asyncio.run(execute_python(code="x = 42")))

    def test_runtime_error_reported(self):
        r = str(asyncio.run(execute_python(code="print(undefined_name)")))
        assert r.startswith("Error:") and "NameError" in r

    def test_ast_gate_still_runs_before_execution(self):
        r = str(asyncio.run(execute_python(code="import os")))
        assert r.startswith("Error:") and "not allowed" in r

    def test_unicode_output_roundtrip(self):
        """子进程 stdout 必须强制 UTF-8，否则 Windows 管道按本地编码输出会乱码。"""
        result = asyncio.run(execute_python(code="print('中文输出 ✓')"))
        assert str(result) == "中文输出 ✓"


class TestFailClosed:
    """auto/docker 在 Docker 不可用时必须拒绝执行，绝不静默回落进程内。"""

    def test_auto_without_docker_refuses(self, monkeypatch):
        """变异点：恢复 `mode = "ast" if not docker` 静默回落 → 本用例红。"""
        monkeypatch.setattr(settings, "SANDBOX_MODE", "auto")
        monkeypatch.setattr(docker_sandbox, "_docker_available", lambda: False)

        result = asyncio.run(execute_python(code="print(45)"))

        assert isinstance(result, ToolResult), (
            "fail-closed 必须是结构化失败；返回字符串会被 registry 包成 success=True"
        )
        assert result.success is False
        assert result.error is not None
        assert result.error.code == "sandbox_unavailable"
        assert "SANDBOX_MODE=ast" in result.error.message, "错误信息必须给出可操作的出路"

    def test_explicit_docker_without_docker_refuses(self, monkeypatch):
        monkeypatch.setattr(settings, "SANDBOX_MODE", "docker")
        monkeypatch.setattr(docker_sandbox, "_docker_available", lambda: False)

        result = asyncio.run(execute_python(code="print(45)"))

        assert isinstance(result, ToolResult) and result.success is False
        assert result.error is not None and result.error.code == "sandbox_unavailable"

    def test_registry_does_not_wrap_sandbox_failure_as_success(self, monkeypatch):
        """错误码包装判别：经 registry.execute_detailed 后仍必须是 success=False。

        变异点：直接 `return "Error: ..."` 字符串 → registry 的 Legacy 分支包成
        success=True、SSE 发 tool_result、失败被伪装成成功 → 本用例红。
        """
        monkeypatch.setattr(settings, "SANDBOX_MODE", "auto")
        monkeypatch.setattr(docker_sandbox, "_docker_available", lambda: False)

        result = asyncio.run(
            tool_registry.execute_detailed(
                "execute_python",
                {"code": "print(1)"},
                organization_id=1,
                user_id=1,  # requires_auth 工具需要组织上下文
            )
        )

        assert result.success is False
        assert result.error is not None and result.error.code == "sandbox_unavailable"
        assert str(result).startswith("Error: [sandbox_unavailable]")

    def test_explicit_ast_never_touches_docker(self, monkeypatch):
        """显式 ast 是唯一合法的进程内路径：不探测、不调用 Docker。"""
        monkeypatch.setattr(settings, "SANDBOX_MODE", "ast")

        def _must_not_probe():
            raise AssertionError("SANDBOX_MODE=ast 不得探测 Docker")

        async def _must_not_run(*_a, **_k):
            raise AssertionError("SANDBOX_MODE=ast 不得调用 Docker 沙箱")

        monkeypatch.setattr(docker_sandbox, "_docker_available", _must_not_probe)
        monkeypatch.setattr(docker_sandbox, "run_in_docker", _must_not_run)

        result = asyncio.run(execute_python(code="print(1)"))
        assert result.success and str(result) == "1"

    def test_auto_with_docker_dispatches_to_container(self, monkeypatch):
        monkeypatch.setattr(settings, "SANDBOX_MODE", "auto")
        monkeypatch.setattr(docker_sandbox, "_docker_available", lambda: True)
        seen: dict = {}

        async def fake_run(code, **kwargs):
            seen.update(kwargs)
            return "from-container"

        monkeypatch.setattr(docker_sandbox, "run_in_docker", fake_run)

        result = asyncio.run(execute_python(code="print(1)"))
        assert result.success and str(result) == "from-container"
        assert result.meta.sandbox == "docker"
        assert seen["image"] == settings.SANDBOX_DOCKER_IMAGE
        assert seen["raise_on_error"] is True, "Docker 路径必须取结构化失败（评审 M2）"

    def test_runtime_invalid_mode_fails_closed(self, monkeypatch):
        """防御性兜底：settings 被运行时改成非法值（绕过 pydantic 校验）也拒绝执行。"""
        monkeypatch.setattr(settings, "SANDBOX_MODE", "process")
        monkeypatch.setattr(docker_sandbox, "_resolve_logged", False)

        result = asyncio.run(execute_python(code="print(1)"))
        assert result.success is False
        assert result.error is not None and result.error.code == "sandbox_unavailable"

    def test_selector_event_loop_reports_actionable_failure(self, monkeypatch):
        """评审 M3：Windows SelectorEventLoop（uvicorn --reload/workers>1）不支持
        create_subprocess_exec——必须是可读的 sandbox_unavailable，而不是 unknown。"""
        monkeypatch.setattr(settings, "SANDBOX_MODE", "ast")

        def _no_subprocess(*_a, **_k):
            raise NotImplementedError

        monkeypatch.setattr(code_execution.asyncio, "create_subprocess_exec", _no_subprocess)

        result = asyncio.run(execute_python(code="print(1)"))
        assert result.success is False
        assert result.error is not None and result.error.code == "sandbox_unavailable"
        assert "--reload" in result.error.message
        assert "SelectorEventLoop" in result.error.message


class TestDockerRuntimeFailures:
    """Docker 运行时失败也必须结构化（评审 M2），不能被 registry 包成成功。"""

    @staticmethod
    def _use_docker(monkeypatch):
        monkeypatch.setattr(settings, "SANDBOX_MODE", "docker")
        monkeypatch.setattr(docker_sandbox, "_docker_available", lambda: True)

    def test_docker_timeout_is_structured_failure(self, monkeypatch):
        self._use_docker(monkeypatch)

        async def fake_run(code, **kwargs):
            raise docker_sandbox.DockerSandboxTimeout("Docker 沙箱执行超时（30s）")

        monkeypatch.setattr(docker_sandbox, "run_in_docker", fake_run)
        result = asyncio.run(execute_python(code="print(1)"))
        assert result.success is False
        assert result.error is not None and result.error.code == "timeout"
        assert result.meta.sandbox == "docker"

    def test_docker_runtime_failure_is_not_wrapped_as_success(self, monkeypatch):
        """变异点：run_in_docker 返回 "Error: ..." 字符串 → registry 包成 success=True → 红。"""
        self._use_docker(monkeypatch)

        async def fake_run(code, **kwargs):
            raise docker_sandbox.DockerSandboxError(
                "Docker 沙箱执行失败（exit 1）：boom",
                legacy_text="Error (exit 1):\nboom",
            )

        monkeypatch.setattr(docker_sandbox, "run_in_docker", fake_run)
        result = asyncio.run(execute_python(code="print(1)"))
        assert result.success is False
        assert result.error is not None and result.error.code == "unknown"
        assert "boom" in str(result)

    def test_legacy_string_contract_preserved_for_workflow(self, monkeypatch):
        """workflow 代码节点消费的旧字符串契约不得改变（默认 raise_on_error=False）。"""

        async def boom(*_a, **_k):
            raise docker_sandbox.DockerSandboxTimeout(
                "x",
                legacy_text="Error: code execution timed out in Docker sandbox.",
            )

        monkeypatch.setattr(docker_sandbox, "_execute_in_docker", boom)

        legacy = asyncio.run(docker_sandbox.run_in_docker("print(1)"))
        assert legacy == "Error: code execution timed out in Docker sandbox."
        with pytest.raises(docker_sandbox.DockerSandboxTimeout):
            asyncio.run(docker_sandbox.run_in_docker("print(1)", raise_on_error=True))

    def test_selector_event_loop_maps_to_sandbox_unavailable(self, monkeypatch):
        """评审复核 minor ②：Docker spawn 在 Windows SelectorEventLoop 下同样映射为
        sandbox_unavailable + 可操作文案，而不是 unknown/裸 NotImplementedError。"""

        def _no_subprocess(*_a, **_k):
            raise NotImplementedError

        monkeypatch.setattr(docker_sandbox.asyncio, "create_subprocess_exec", _no_subprocess)

        with pytest.raises(docker_sandbox.DockerSandboxUnavailable):
            asyncio.run(docker_sandbox.run_in_docker("print(1)", raise_on_error=True))

        legacy = asyncio.run(docker_sandbox.run_in_docker("print(1)"))
        assert legacy.startswith("Error: Docker sandbox unavailable")


class TestResolvedModeVisibility:
    """启动/首用必须记录一次 resolved mode（warning），便于部署侧发现 fail-closed。"""

    def test_resolved_mode_logged_once(self, monkeypatch, caplog):
        monkeypatch.setattr(settings, "SANDBOX_MODE", "auto")
        monkeypatch.setattr(docker_sandbox, "_docker_available", lambda: True)
        monkeypatch.setattr(docker_sandbox, "_resolve_logged", False)

        with caplog.at_level(logging.WARNING, logger="app.agent.tools.docker_sandbox"):
            assert docker_sandbox.resolve_sandbox_mode() == "docker"
            assert docker_sandbox.resolve_sandbox_mode() == "docker"

        hits = [r for r in caplog.records if "resolved" in r.getMessage().lower()]
        assert len(hits) == 1, f"resolved mode 只应记录一次，实际 {len(hits)} 次"

    def test_unavailable_mode_logged_once(self, monkeypatch, caplog):
        monkeypatch.setattr(settings, "SANDBOX_MODE", "auto")
        monkeypatch.setattr(docker_sandbox, "_docker_available", lambda: False)
        monkeypatch.setattr(docker_sandbox, "_resolve_logged", False)

        with caplog.at_level(logging.WARNING, logger="app.agent.tools.docker_sandbox"):
            with pytest.raises(docker_sandbox.SandboxUnavailableError):
                docker_sandbox.resolve_sandbox_mode()
            with pytest.raises(docker_sandbox.SandboxUnavailableError):
                docker_sandbox.resolve_sandbox_mode()

        hits = [r for r in caplog.records if "resolved" in r.getMessage().lower()]
        assert len(hits) == 1


class TestSandboxConfigAndExposure:
    def test_unknown_sandbox_mode_rejected_at_settings_load(self):
        """Literal 校验：未知模式必须在配置加载期失败（而不是静默当 ast）。"""
        from app.core.config.ai import AISettings

        with pytest.raises(ValidationError, match="SANDBOX_MODE"):
            AISettings(SANDBOX_MODE="process")

    def test_known_sandbox_modes_accepted(self):
        from app.core.config.ai import AISettings

        loaded = {}
        for mode in ("auto", "docker", "ast"):
            loaded[mode] = AISettings(SANDBOX_MODE=mode).SANDBOX_MODE
        assert loaded == {"auto": "auto", "docker": "docker", "ast": "ast"}

    def test_tool_result_event_carries_sandbox(self):
        event = AgentEvent(type="tool_result", content="45", sandbox="ast")
        assert event.to_sse_dict()["sandbox"] == "ast"

    def test_sandbox_field_absent_when_unknown(self):
        assert "sandbox" not in AgentEvent(type="tool_result", content="x").to_sse_dict()
