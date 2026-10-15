"""MCP 配置层单元测试（纯本地，无子进程）。"""

from __future__ import annotations

import json

from app.agent.mcp.config import (
    CONFIG_PATH_ENV,
    MCPServerConfig,
    load_servers_config,
    resolve_config_path,
)


def _write_config(tmp_path, payload) -> str:
    path = tmp_path / "mcp_servers.json"
    if isinstance(payload, str):
        path.write_text(payload, encoding="utf-8")
    else:
        path.write_text(json.dumps(payload), encoding="utf-8")
    return str(path)


class TestLoad:
    def test_missing_file_returns_empty(self, tmp_path):
        assert load_servers_config(tmp_path / "nope.json") == []

    def test_malformed_json_returns_empty(self, tmp_path):
        assert load_servers_config(_write_config(tmp_path, "{not json")) == []

    def test_missing_servers_key_returns_empty(self, tmp_path):
        assert load_servers_config(_write_config(tmp_path, {"other": 1})) == []

    def test_flat_mapping_tolerated(self, tmp_path):
        # 顶层直接是 {name: {...}} 的容错形态
        cfg = load_servers_config(_write_config(tmp_path, {
            "solo": {"transport": "stdio", "command": "python"},
        }))
        assert [c.name for c in cfg] == ["solo"]

    def test_parses_stdio_fields(self, tmp_path):
        path = _write_config(tmp_path, {"servers": {"fs": {
            "transport": "stdio", "command": "npx", "args": ["-y", "pkg"],
            "env": {"K": "V"}, "cwd": "C:/tmp", "enabled": True,
            "description": "d", "timeout": 60,
        }}})
        [cfg] = load_servers_config(path)
        assert cfg == MCPServerConfig(
            name="fs", transport="stdio", enabled=True, description="d", timeout=60.0,
            command="npx", args=["-y", "pkg"], env={"K": "V"}, cwd="C:/tmp",
        )

    def test_default_enabled_is_false(self, tmp_path):
        path = _write_config(tmp_path, {"servers": {"s": {"command": "python"}}})
        [cfg] = load_servers_config(path)
        assert cfg.enabled is False  # 配置存在 ≠ 主动接入

    def test_transport_alias_streamable_http(self, tmp_path):
        path = _write_config(tmp_path, {"servers": {"s": {
            "transport": "streamable-http", "url": "https://x/mcp",
        }}})
        [cfg] = load_servers_config(path)
        assert cfg.transport == "http"

    def test_http_requires_url(self, tmp_path):
        path = _write_config(tmp_path, {"servers": {"s": {"transport": "http", "url": ""}}})
        assert load_servers_config(path) == []

    def test_stdio_requires_command(self, tmp_path):
        path = _write_config(tmp_path, {"servers": {"s": {"transport": "stdio"}}})
        assert load_servers_config(path) == []

    def test_unknown_transport_skipped(self, tmp_path):
        path = _write_config(tmp_path, {"servers": {
            "bad": {"transport": "carrier-pigeon", "command": "x"},
            "good": {"command": "python"},
        }})
        assert [c.name for c in load_servers_config(path)] == ["good"]

    def test_non_dict_entry_skipped(self, tmp_path):
        path = _write_config(tmp_path, {"servers": {"bad": "nope", "good": {"command": "p"}}})
        assert [c.name for c in load_servers_config(path)] == ["good"]

    def test_bad_timeout_falls_back(self, tmp_path):
        path = _write_config(tmp_path, {"servers": {"s": {"command": "p", "timeout": -3}}})
        [cfg] = load_servers_config(path)
        assert cfg.timeout == 30.0

    def test_env_expansion(self, tmp_path, monkeypatch):
        monkeypatch.setenv("MCP_TEST_TOKEN", "sekrit")
        path = _write_config(tmp_path, {"servers": {"s": {
            "transport": "http", "url": "https://x/${MCP_TEST_TOKEN}",
            "headers": {"Authorization": "Bearer ${MCP_TEST_TOKEN}"},
        }}})
        [cfg] = load_servers_config(path)
        assert cfg.url == "https://x/sekrit"
        assert cfg.headers == {"Authorization": "Bearer sekrit"}

    def test_env_expansion_missing_var_becomes_empty(self, tmp_path, monkeypatch, caplog):
        monkeypatch.delenv("MCP_TEST_ABSENT", raising=False)
        path = _write_config(tmp_path, {"servers": {"s": {
            "command": "python", "args": ["${MCP_TEST_ABSENT}"],
        }}})
        with caplog.at_level("WARNING"):
            [cfg] = load_servers_config(path)
        assert cfg.args == [""]
        assert any("MCP_TEST_ABSENT" in r.message for r in caplog.records)


class TestResolvePath:
    def test_env_override(self, tmp_path, monkeypatch):
        target = tmp_path / "custom.json"
        monkeypatch.setenv(CONFIG_PATH_ENV, str(target))
        assert resolve_config_path() == target

    def test_default_is_backend_root(self):
        path = resolve_config_path()
        assert path.name == "mcp_servers.json"
        assert path.parent.name == "backend"
