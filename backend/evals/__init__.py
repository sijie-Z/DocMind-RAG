"""DocMind RAG 检索评估（#208 评估门禁 Phase 1 起步版）。

本包只做一件事：把「检索质量」变成可测量、可回归的数字。
- 指标核心：条号级命中 `ClauseHit@k`（定义位口径，见 clauses.py）
- 数据集：`golden_set/clause_set_v1.json` + 固定语料 `corpus/`
- 入口：`python -m evals {check,seed,run}`（Phase 1 只测量，不设门槛；
  CI 门禁与 baseline 回归是 Phase 2）

设计纪律：本包纯标准库，不 import app.*；与真实检索链的接线只在
runner.py（CLI 层）发生，保证核心逻辑可在无 ES/无网络的单元测试里判别。
"""

__all__ = ["clauses", "chunking", "golden", "metrics", "harness", "report"]
