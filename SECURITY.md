# 安全政策（Security Policy）

DocMind（仓库 DocMind-RAG）是公开仓库。本文说明漏洞报告渠道、支持的版本、密钥管理原则，
以及历史凭据泄露事件（issue #507）的处置记录。

## 报告漏洞

- **不要**在公开 issue / PR / 讨论区披露安全问题（包括任何密钥、可利用细节或复现脚本）。
- 请使用 GitHub 私密漏洞报告（Security → Report a vulnerability）：
  <https://github.com/sijie-Z/DocMind-RAG/security/advisories/new>
- 报告中请包含：受影响版本 / 提交、复现步骤、影响评估、是否已公开。
- 我们会在 **3 个工作日内**确认收到，并在评估后同步修复计划；修复发布前请勿公开细节。

## 支持的版本

| 版本 | 支持状态 |
| --- | --- |
| `main` 及最新发布（当前 `1.22.x`） | 接收安全修复 |
| `develop`（下一版本开发线） | 接收安全修复 |
| 更早版本 / 已删除分支 | 不保证；建议升级到最新发布 |

安全修复优先合入 `develop` 并随最近一次发布交付。

## 密钥管理原则

1. **密钥不进仓库**：任何真实凭据（第三方 API Key、JWT / 加密密钥、数据库与中间件口令、
   SMTP / 飞书 / 钉钉 / Webhook 凭据）只通过部署环境变量或 Secret 注入；仓库内只允许
   `.env.example` 之类的占位模板。
2. **源码禁止硬编码**：包括演示脚本与测试辅助脚本。示例值必须是显式假值
   （`REPLACE_ME_*`、固定假串），不得使用真实值。
3. **门禁**：
   - pre-commit：`gitleaks`（v8.30.1，见 `.pre-commit-config.yaml`）；
   - CI：`ci-hygiene` 的 `secret-scan` job 做全历史 + 工作树扫描，新增命中即失败；
   - 放行只允许两类：`.gitleaks.toml` 中逐条核实的测试夹具假值、`.gitleaksignore`
     中逐条登记的历史指纹（只含指纹，不含值）。
4. **按环境独立**：`SECRET_KEY` / `JWT_SECRET_KEY` 各环境独立生成
   （`python -c "import secrets; print(secrets.token_hex(32))"`），不复用示例或历史值。
5. **轮换优先于改写历史**：凭据一旦进入公开历史即视为泄露。轮换（吊销并重建）是唯一
   止损手段；不采用 `git filter-repo` / `filter-branch` / force-push 改写历史——公开
   仓库可能已被 fork 或缓存，改写无法消除既有暴露，且会破坏协作与审计链。
6. **泄露处置流程**：见 [`docs/security/CREDENTIAL_ROTATION.md`](docs/security/CREDENTIAL_ROTATION.md)
   （凭据清单、轮换步骤、验证命令、日期回填）。

## 历史安全事件

### 2026-10：公开 git 历史中的凭据泄露（issue #507）

- **发现**：安全审计发现公开 git 历史包含真实凭据：
  - `fd00b3c:backend/.env.docker`：`SECRET_KEY`、`JWT_SECRET_KEY`（与当时本地
    `backend/.env` 同值）、第三方 API Key、MySQL / Redis / MinIO / Grafana 口令；
  - `b0324c1:backend/docker-compose.yml`：内联第三方 API Key；
  - `b0324c1:backend/.env.notification`：SMTP / 飞书 / 钉钉 / Webhook 通知凭据
    （占位符形态；若曾填入真实值同样需要轮换）；
  - `backend/demo.py`（本 PR 前仍在工作树）、`backend/app/agent_api.py`（已删除）：
    硬编码第三方 API Key。
- **影响**：读过历史的人可伪造任意用户 JWT（含管理员）、解密依赖 `SECRET_KEY` 的产物、
  使用泄露的第三方 Key 产生费用。
- **处置**：
  1. 轮换全部暴露凭据（在部署环境执行，清单、状态与日期见
     [`docs/security/CREDENTIAL_ROTATION.md`](docs/security/CREDENTIAL_ROTATION.md)）；
  2. 移除工作树中的硬编码 Key：`backend/demo.py` 改为只从环境变量读取；
  3. 增加 gitleaks pre-commit 与 CI 门禁；历史命中逐条登记于 `.gitleaksignore`
     （只含指纹与说明，不含任何密钥值）；
  4. **不改写 git 历史**：相关提交是公开历史的一部分，改写无法消除已有暴露。
- **状态**：轮换完成情况以 `docs/security/CREDENTIAL_ROTATION.md` 中逐项日期为准。
  在该清单全部完成之前，历史中出现过的凭据应视为**仍然有效**。
