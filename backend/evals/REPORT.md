# DocMind RAG 检索评估报告 v1.5（issue #512）

- 数据集：`docmind_clause_v1_5` v1（80 条 = 正例 60 + 负例 20）
- 语料：8 个文件 / 362 个 chunk / 354 条 clause-level 定义（确定性分块，一条款一 chunk）
- 运行模式：`fake`（确定性伪嵌入，无 LLM/rerank）　检索器：`hybrid`
- 日期：2026-10-24T05:17:30+00:00　索引：`docmind_knowledge_eval_public_v1`　ES：8.11.0
- 语料指纹（sha256）：`838fd02f107adc288b2a8884515ce674ad342e86edf4563c5941d1fb73097d9e`　代码/语料 commit：`ffe0a398f10bbbfbdb236e5cad90676d3848a270`
- dense 报告：日期 2026-10-24T05:17:36+00:00　语料指纹 `838fd02f107adc288b2a8884515ce674ad342e86edf4563c5941d1fb73097d9e`

> 口径：ClauseHit@k = top-k 中存在「文件名匹配 + 条号位于 chunk 行首定义位」的块；
> NearMiss@k = 声明 near-miss 的正例中 top-k 出现相邻条定义位的比例（越低越好）；Wilson 95% CI；不引入 bootstrap。

## 总体指标（含 Wilson 95% CI）

| 指标 | hybrid | dense |
|---|---|---|
| ClauseHit@1 | 70.0% | 61.7% |
| ClauseHit@3 | 76.7% | 68.3% |
| ClauseHit@5 | 80.0% | 73.3% |
| NearMiss@5 | 25.0% | 30.0% |
| MRR | 0.7400 | 0.6542 |
| EmptyAcc | 100.0% | 0.0% |
| ClauseHit@5 95% CI | [68.2%, 88.2%] | [61.0%, 82.9%] |
| EmptyAcc 95% CI | [83.9%, 100.0%] | [0.0%, 16.1%] |
| errors | 0 | 0 |

## Per-slice 对比（按题型）

| 题型 | 正例 n | 负例 n | hybrid ClauseHit@5 | dense ClauseHit@5 | hybrid MRR | dense MRR | hybrid EmptyAcc | dense EmptyAcc |
|---|---|---|---|---|---|---|---|---|
| clause_lookup | 28 | 0 | 67.9% | 57.1% | 0.5500 | 0.4702 | n/a | n/a |
| semantic | 18 | 0 | 100.0% | 88.9% | 1.0000 | 0.8102 | n/a | n/a |
| numerical | 8 | 0 | 87.5% | 100.0% | 0.8750 | 0.9375 | n/a | n/a |
| cross_doc | 4 | 0 | 50.0% | 50.0% | 0.5000 | 0.5000 | n/a | n/a |
| conflict | 2 | 0 | 100.0% | 100.0% | 1.0000 | 1.0000 | n/a | n/a |
| unanswerable | 0 | 20 | n/a | n/a | n/a | n/a | 100.0% | 0.0% |

## 失败分类与例子（每类最多 2 个，来自 hybrid / dense 实际结果）

### title-token 淹没（2 例展示）

- `clause_arabic_3`（hybrid，clause_lookup）　query：《知识库服务管理办法》第3条规定了什么？
  - gold：01-knowledge-base-policy.md:第3条　gold_rank：None
  - near-miss：01-knowledge-base-policy.md:第4条　near_miss_rank：None
  - top@1 `01-knowledge-base-policy.md` defs=[] score=1.0：# 知识库服务管理办法

> 版本：v3.0　生效日期：2026-01-01　文号：DM-POL-2026-001

本办法规定了 DocMind 企业知识库服务在数据管理、访问控制、审计与应急等方面的基本要求，适用于所有使用知识库服务的组
  - top@2 `01-knowledge-base-policy.md` defs=['第2条'] score=0.5356：第2条 本办法适用于接入 DocMind 知识库服务的组织及其成员所使用的文档上传、解析、检索、问答与导出功能。
  - top@3 `06-archives-law.md` defs=['第37条'] score=0.1968：第三十七条 电子档案应当来源可靠、程序规范、要素合规。
电子档案与传统载体档案具有同等效力，可以以电子形式作为凭证使用。
电子档案管理办法由国家档案主管部门会同有关部门制定。
- `clause_chinese_7`（hybrid，clause_lookup）　query：《知识库服务管理办法》第七条规定了什么？
  - gold：01-knowledge-base-policy.md:第7条　gold_rank：None
  - near-miss：01-knowledge-base-policy.md:第8条　near_miss_rank：None
  - top@1 `01-knowledge-base-policy.md` defs=[] score=1.0：# 知识库服务管理办法

> 版本：v3.0　生效日期：2026-01-01　文号：DM-POL-2026-001

本办法规定了 DocMind 企业知识库服务在数据管理、访问控制、审计与应急等方面的基本要求，适用于所有使用知识库服务的组
  - top@2 `01-knowledge-base-policy.md` defs=['第2条'] score=0.5255：第2条 本办法适用于接入 DocMind 知识库服务的组织及其成员所使用的文档上传、解析、检索、问答与导出功能。
  - top@3 `06-archives-law.md` defs=['第37条'] score=0.2133：第三十七条 电子档案应当来源可靠、程序规范、要素合规。
电子档案与传统载体档案具有同等效力，可以以电子形式作为凭证使用。
电子档案管理办法由国家档案主管部门会同有关部门制定。

### 中文/阿拉伯数字不归一（2 例展示）

- `clause_arabic_3`（hybrid，clause_lookup）　query：《知识库服务管理办法》第3条规定了什么？
  - gold：01-knowledge-base-policy.md:第3条　gold_rank：None
  - near-miss：01-knowledge-base-policy.md:第4条　near_miss_rank：None
  - top@1 `01-knowledge-base-policy.md` defs=[] score=1.0：# 知识库服务管理办法

> 版本：v3.0　生效日期：2026-01-01　文号：DM-POL-2026-001

本办法规定了 DocMind 企业知识库服务在数据管理、访问控制、审计与应急等方面的基本要求，适用于所有使用知识库服务的组
  - top@2 `01-knowledge-base-policy.md` defs=['第2条'] score=0.5356：第2条 本办法适用于接入 DocMind 知识库服务的组织及其成员所使用的文档上传、解析、检索、问答与导出功能。
  - top@3 `06-archives-law.md` defs=['第37条'] score=0.1968：第三十七条 电子档案应当来源可靠、程序规范、要素合规。
电子档案与传统载体档案具有同等效力，可以以电子形式作为凭证使用。
电子档案管理办法由国家档案主管部门会同有关部门制定。
- `clause_chinese_7`（hybrid，clause_lookup）　query：《知识库服务管理办法》第七条规定了什么？
  - gold：01-knowledge-base-policy.md:第7条　gold_rank：None
  - near-miss：01-knowledge-base-policy.md:第8条　near_miss_rank：None
  - top@1 `01-knowledge-base-policy.md` defs=[] score=1.0：# 知识库服务管理办法

> 版本：v3.0　生效日期：2026-01-01　文号：DM-POL-2026-001

本办法规定了 DocMind 企业知识库服务在数据管理、访问控制、审计与应急等方面的基本要求，适用于所有使用知识库服务的组
  - top@2 `01-knowledge-base-policy.md` defs=['第2条'] score=0.5255：第2条 本办法适用于接入 DocMind 知识库服务的组织及其成员所使用的文档上传、解析、检索、问答与导出功能。
  - top@3 `06-archives-law.md` defs=['第37条'] score=0.2133：第三十七条 电子档案应当来源可靠、程序规范、要素合规。
电子档案与传统载体档案具有同等效力，可以以电子形式作为凭证使用。
电子档案管理办法由国家档案主管部门会同有关部门制定。

### chunk 边界（1 例展示）

- `clause_chinese_7`（dense，clause_lookup）　query：《知识库服务管理办法》第七条规定了什么？
  - gold：01-knowledge-base-policy.md:第7条　gold_rank：None
  - near-miss：01-knowledge-base-policy.md:第8条　near_miss_rank：3
  - top@1 `01-knowledge-base-policy.md` defs=[] score=1.4425168：# 知识库服务管理办法

> 版本：v3.0　生效日期：2026-01-01　文号：DM-POL-2026-001

本办法规定了 DocMind 企业知识库服务在数据管理、访问控制、审计与应急等方面的基本要求，适用于所有使用知识库服务的组
  - top@2 `03-data-security-law.md` defs=['第34条'] score=1.4343333：第三十四条 法律、行政法规规定提供数据处理相关服务应当取得行政许可的，服务提供者应当依法取得许可。
  - top@3 `01-knowledge-base-policy.md` defs=['第8条'] score=1.4238768：第8条 权限变更（含授权成员增补与回收）须由本组织管理员发起，经上一级负责人审批后方可生效。与第 7 条所列组织隔离规则冲突的授权申请一律不予受理。
- 本 run 仅 1 例符合该启发式；未凑数（更多例子需在更大语料或其他检索配置下采集）。

### near-miss 误召（2 例展示）

- `pipl_clause_13_basis`（hybrid，clause_lookup）　query：《个人信息保护法》第13条规定的处理个人信息的合法情形有哪些？
  - gold：04-personal-information-protection-law.md:第13条　gold_rank：None
  - near-miss：04-personal-information-protection-law.md:第14条　near_miss_rank：2
  - top@1 `04-personal-information-protection-law.md` defs=['第45条'] score=1.0：第四十五条 个人有权向个人信息处理者查阅、复制其个人信息；有本法第十八条第一款、第三十五条规定情形的除外。
个人请求查阅、复制其个人信息的，个人信息处理者应当及时提供。
个人请求将个人信息转移至其指定的个人信息处理者，符合国家网信部门规定条
  - top@2 `04-personal-information-protection-law.md` defs=['第14条'] score=1.0：第十四条 基于个人同意处理个人信息的，该同意应当由个人在充分知情的前提下自愿、明确作出。法律、行政法规规定处理个人信息应当取得个人单独同意或者书面同意的，从其规定。
个人信息的处理目的、处理方式和处理的个人信息种类发生变更的，应当重新取得个
  - top@3 `05-cybersecurity-law.md` defs=['第42条'] score=1.0：第四十二条 网络运营者应当对其收集的用户信息严格保密，并建立健全用户信息保护制度。
网络运营者处理个人信息，应当遵守本法和《中华人民共和国民法典》、《中华人民共和国个人信息保护法》等法律、行政法规的规定。
- `cii_clause_2_scope`（hybrid，clause_lookup）　query：《关键信息基础设施安全保护条例》第2条如何界定关键信息基础设施？
  - gold：07-cii-security-protection-regulation.md:第2条　gold_rank：None
  - near-miss：05-cybersecurity-law.md:第33条　near_miss_rank：4
  - top@1 `07-cii-security-protection-regulation.md` defs=['第34条'] score=1.0：第三十四条 国家制定和完善关键信息基础设施安全标准，指导、规范关键信息基础设施安全保护工作。
  - top@2 `07-cii-security-protection-regulation.md` defs=['第4条'] score=1.0：第四条 关键信息基础设施安全保护坚持综合协调、分工负责、依法保护，强化和落实关键信息基础设施运营者（以下简称运营者）主体责任，充分发挥政府及社会各方面的作用，共同保护关键信息基础设施安全。
  - top@3 `05-cybersecurity-law.md` defs=['第34条'] score=1.0：第三十四条 按照国务院规定的职责分工，负责关键信息基础设施安全保护工作的部门分别编制并组织实施本行业、本领域的关键信息基础设施安全规划，指导和监督关键信息基础设施运行安全保护工作。

### 负例误召（2 例展示）

- `neg_face_login`（dense，unanswerable）　query：请推荐适合阳台种植的香草植物。
  - gold：-　gold_rank：None
  - top@1 `07-cii-security-protection-regulation.md` defs=['第49条'] score=1.3639597：第四十九条 违反本条例规定，给他人造成损害的，依法承担民事责任。
违反本条例规定，构成违反治安管理行为的，依法给予治安管理处罚；构成犯罪的，依法追究刑事责任。
  - top@2 `03-data-security-law.md` defs=['第41条'] score=1.3455579：第四十一条 国家机关应当遵循公正、公平、便民的原则，按照规定及时、准确地公开政务数据。依法不予公开的除外。
  - top@3 `07-cii-security-protection-regulation.md` defs=['第51条'] score=1.345307：第五十一条 本条例自2021年9月1日起施行。
- `neg_quantum`（dense，unanswerable）　query：请解释量子纠错码的容错阈值定理。
  - gold：-　gold_rank：None
  - top@1 `01-knowledge-base-policy.md` defs=['第12条'] score=1.3421263：第12条 数据删除申请需经数据负责人审批。审批通过后，系统应在 30 天内完成删除，删除范围包括文档原文、解析分块与向量索引，且不可恢复。
  - top@2 `07-cii-security-protection-regulation.md` defs=['第39条'] score=1.3378053：第三十九条 运营者有下列情形之一的，由有关主管部门依据职责责令改正，给予警告；拒不改正或者导致危害网络安全等后果的，处10万元以上100万元以下罚款，对直接负责的主管人员处1万元以上10万元以下罚款：
（一）在关键信息基础设施发生较大变化，
  - top@3 `03-data-security-law.md` defs=['第40条'] score=1.3371155：第四十条 国家机关委托他人建设、维护电子政务系统，存储、加工政务数据，应当经过严格的批准程序，并应当监督受托方履行相应的数据安全保护义务。受托方应当依照法律、法规的规定和合同约定履行数据安全保护义务，不得擅自留存、使用、泄露或者向他人提供政

## 复现

```bash
# 仅需 Docker ES 8.11，无需 Ollama / 网络
cd backend
python -m evals check
make eval-public       # seed --recreate → run(hybrid) → run(dense) → report
make eval-ci           # CI 门禁（hybrid，7 项指标 + 基线比对）
```

## 限制

- fake 模式的数字只反映「确定性伪嵌入 + 检索管道」的行为面，**不可**与 live （真实 embedding）数字混比；对外引用必须标注 `mode=fake/live`、日期、语料 commit。
- 本报告不经过 rerank / 语义缓存 / LLM 改写；live rerank 报告由 `python -m evals.live_rerank` 生成（缺少真实 provider 时只提供占位报告，绝不伪造数字）。
- Wilson 区间按二项比例计算；未引入 bootstrap（重采样单位与收益未论证）。
