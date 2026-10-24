# DocMind 评估 v1.5 标注说明与 20% 双标审计

## 1. 标注主体与流程

- **主标注**：Claude Code 执行 agent（AI），2026-10-24。
- **流程**：先写查询（贴近真实问法），再回到语料原文定位**唯一定义答案的条款**；near-miss 选相邻/最易混条款；负例必须是语料完全无法回答的主题。
- **语料**：8 份公开法规/制度文本（6 份公开法律/行政法规 + 2 份仓库内制度 fixture），共 362 chunks / 354 条 clause-level 定义。公开法律文本依《著作权法》第五条不适用著作权保护；每份语料文件头部记录来源 URL、检索日期与文本版本。
- **口径**：ClauseHit@k 以「文件名 + 条号行首定义位」判定；正文引用不计命中；Wilson 95% CI；不引入 bootstrap。

## 2. 20% 双标审计（16 / 80 条）

- **第二标注**：Claude Code Explore 子代理（AI），2026-10-24，**盲标**——只拿到 16 条查询与语料目录，未读取任何 golden JSON/主标结果。
- **结果**：16/16 与主标在「是否可答 + 首选 gold 条款」上一致；1 条（N）复核补充了额外支撑条款，但不改变主标 gold；1 条（L）复核排除了一个易混来源。
- **裁决**：无需要改动的 gold；N 的补充条款写入其 notes/审计记录，L 的排除理由保留在下方。

| # | item id | 主标 gold | 复核置信度 | 一致性 | 说明 |
|---|---|---|---|---|---|
| A | `dsl_clause_21_classification` | `03-data-security-law.md:第21条` | high | 一致：第21条（数据分类分级） | 无 |
| B | `dsl_clause_35_retrieval` | `03-data-security-law.md:第35条` | high | 一致：第35条（依法调取数据） | 无 |
| C | `pipl_clause_38_crossborder` | `04-personal-information-protection-law.md:第38条` | high | 一致：第38条（出境条件） | 无 |
| D | `csl_clause_23_mlps` | `05-cybersecurity-law.md:第23条` | high | 一致：第23条（等级保护义务，2025 修正文本条号） | 复核特别指出 2016 版为第21条；语料用 2025 修正文本，条号正确 |
| E | `cii_clause_17_assessment` | `07-cii-security-protection-regulation.md:第17条` | high | 一致：条例第17条（每年至少一次） | 无 |
| F | `archives_clause_27_open` | `06-archives-law.md:第27条` | high | 一致：档案法第27条（满二十五年） | 无 |
| G | `dsl_important_data_assessment` | `03-data-security-law.md:第30条` | high | 一致：第30条（重要数据处理者风险评估与报送） | 无 |
| H | `pipl_minor_consent` | `04-personal-information-protection-law.md:第31条` | high | 一致：第31条（父母或其他监护人同意） | 无 |
| I | `csl_real_name` | `05-cybersecurity-law.md:第26条` | high | 一致：第26条（真实身份信息，2025 修正文本条号） | 无 |
| J | `archives_transfer` | `06-archives-law.md:第14条` | high | 一致：第14条（向本单位档案机构移交） | 无 |
| K | `pipl_assessment_retention_3y` | `04-personal-information-protection-law.md:第56条` | high | 一致：第56条（至少保存三年） | 无 |
| L | `incident_cii_report_1h` | `08-cybersecurity-incident-reporting-measures.md:第4条` | high | 一致：报告办法第4条（最迟1小时） | 复核排除应急手册 15 分钟（内部告警响应，不适用于 CII 事件报告） |
| M | `cross_doc_incident_chain` | `07-cii-security-protection-regulation.md:第18条`, `08-cybersecurity-incident-reporting-measures.md:第4条` | high | 一致：条例第18条 + 报告办法第4条 | 无 |
| N | `conflict_retention_vs_archive` | `01-knowledge-base-policy.md:第6条`, `06-archives-law.md:第13条` | medium | 主标：办法第6条 + 档案法第13条；复核另列第14条 | 复核补充第14条（归档材料移交集中管理）；主标不变，另记录“适用对象不同、属生命周期逻辑差异” |
| O | `neg_kubernetes_autoscale` | — | high | 一致：unanswerable | 无 |
| P | `neg_typhoon_path` | — | high | 一致：unanswerable | 无 |

### 分歧/补充的裁决记录

- **N（`conflict_retention_vs_archive`）**：主标 gold 为 `01-knowledge-base-policy.md:第6条` + `06-archives-law.md:第13条`；复核认为 `06-archives-law.md:第14条`（归档材料向本单位档案机构移交）也直接支撑「两套生命周期要求」的差异。裁决：保留主标 gold（召回任一来源即算命中），把第14条作为该题的 nearest-miss 语义补充记录在案；不扩大 gold 集合以免降低题目判别力。
- **L（`incident_cii_report_1h`）**：复核特别指出 `02-incident-response-handbook.md:§2.3` 的 15 分钟是内部告警响应时限，不是 CII 事件报告时限；裁决：维持 gold=报告办法第4条，near-miss 仍为条例第18条。
- **D/I（网络安全法条号）**：语料采用 2025 修正文本（2026-01-01 施行），等级保护/实名制条号分别为第23/26条；复核确认与语料一致，2016 版旧条号（第21/24条）不适用。

## 3. 全量题目—条款可追溯表（80 条）

| # | item id | 类型 | 期望 | gold（文件:条款） | near-miss | notes |
|---|---|---|---|---|---|---|
| 1 | `clause_arabic_3` | clause_lookup | hit | `01-knowledge-base-policy.md:第3条` | `01-knowledge-base-policy.md:第4条` | 阿拉伯数字条号直查 |
| 2 | `clause_chinese_7` | clause_lookup | hit | `01-knowledge-base-policy.md:第7条` | `01-knowledge-base-policy.md:第8条` | 中文数字条号——与 clause_arabic_7 同 gold 构成 #2.10 数字归一化对照基线 |
| 3 | `clause_arabic_7` | clause_lookup | hit | `01-knowledge-base-policy.md:第7条` | `01-knowledge-base-policy.md:第8条` | 阿拉伯数字条号——与 clause_chinese_7 同 gold 构成 #2.10 数字归一化对照基线 |
| 4 | `clause_12_deletion` | clause_lookup | hit | `01-knowledge-base-policy.md:第12条` | `01-knowledge-base-policy.md:第11条` |  |
| 5 | `clause_chinese_12` | clause_lookup | hit | `01-knowledge-base-policy.md:第12条` | `01-knowledge-base-policy.md:第11条` | 中文数字与 clause_12_deletion 构成第二组数字归一化对照 |
| 6 | `clause_9_audit_space` | clause_lookup | hit | `01-knowledge-base-policy.md:第9条` | `01-knowledge-base-policy.md:第10条` | 带空格条号「第 9 条」的容忍度探针 |
| 7 | `sec_2_3_response` | clause_lookup | hit | `02-incident-response-handbook.md:§2.3` | `02-incident-response-handbook.md:§2.1` | § 引用形式 |
| 8 | `sec_2_3_p2` | numerical | hit | `02-incident-response-handbook.md:§2.3` | `02-incident-response-handbook.md:§2.1` |  |
| 9 | `sec_3_2_rollback` | semantic | hit | `02-incident-response-handbook.md:§3.2` | `02-incident-response-handbook.md:§3.1` |  |
| 10 | `sec_3_3_postmortem` | semantic | hit | `02-incident-response-handbook.md:§3.3` | `02-incident-response-handbook.md:§4.2` |  |
| 11 | `sec_4_1_drill` | numerical | hit | `02-incident-response-handbook.md:§4.1` | `01-knowledge-base-policy.md:第13条` | 跨文档 near-miss |
| 12 | `sec_4_2_archive` | cross_doc | hit | `02-incident-response-handbook.md:§4.2` | `01-knowledge-base-policy.md:第13条` | 跨文档 near-miss |
| 13 | `sec_2_1_levels` | semantic | hit | `02-incident-response-handbook.md:§2.1` | `02-incident-response-handbook.md:§2.3` |  |
| 14 | `cross_doc_report_deadline` | conflict | hit | `01-knowledge-base-policy.md:第14条`, `02-incident-response-handbook.md:§3.3` | `02-incident-response-handbook.md:§3.2` | 两来源事实冲突（办法第14条 3 个工作日 vs 手册 §3.3 5 个工作日）：召回任一来源即算命中；冲突本身暴露给 Phase 2 处理 |
| 15 | `concept_levels` | semantic | hit | `01-knowledge-base-policy.md:第3条` | `01-knowledge-base-policy.md:第4条` |  |
| 16 | `concept_upload_formats` | semantic | hit | `01-knowledge-base-policy.md:第4条` | `01-knowledge-base-policy.md:第2条` |  |
| 17 | `process_export` | semantic | hit | `01-knowledge-base-policy.md:第11条` | `01-knowledge-base-policy.md:第12条` |  |
| 18 | `process_permission_change` | semantic | hit | `01-knowledge-base-policy.md:第8条` | `01-knowledge-base-policy.md:第7条` |  |
| 19 | `process_isolation` | semantic | hit | `01-knowledge-base-policy.md:第7条` | `01-knowledge-base-policy.md:第2条` |  |
| 20 | `clause_15_violation` | semantic | hit | `01-knowledge-base-policy.md:第15条` | `01-knowledge-base-policy.md:第3条` |  |
| 21 | `numeric_retention_original` | numerical | hit | `01-knowledge-base-policy.md:第6条` | `01-knowledge-base-policy.md:第13条` |  |
| 22 | `numeric_audit_retention` | numerical | hit | `01-knowledge-base-policy.md:第9条` | `01-knowledge-base-policy.md:第10条` |  |
| 23 | `numeric_backup_retention` | numerical | hit | `01-knowledge-base-policy.md:第13条` | `01-knowledge-base-policy.md:第6条` |  |
| 24 | `numeric_rto` | numerical | hit | `01-knowledge-base-policy.md:第14条` | `01-knowledge-base-policy.md:第13条` |  |
| 25 | `definition_rto_rpo` | semantic | hit | `01-knowledge-base-policy.md:第14条` | `01-knowledge-base-policy.md:第13条` |  |
| 26 | `neg_face_login` | unanswerable | empty | — | — | 负例：语料外园艺主题（原「人脸识别登录」因语料第62条提及人脸识别，不再是干净负例，已替换） |
| 27 | `neg_quantum` | unanswerable | empty | — | — | 负例：主题完全不在语料内 |
| 28 | `neg_clause99` | unanswerable | empty | — | — | 负难例：条号在语料中不存在（最长 81 条）——「问不存在的条号」不得拿别的条充数；主题也完全不在语料内 |
| 29 | `dsl_clause_21_classification` | clause_lookup | hit | `03-data-security-law.md:第21条` | `03-data-security-law.md:第24条` | 阿拉伯数字直查（新语料） |
| 30 | `dsl_clause_35_retrieval` | clause_lookup | hit | `03-data-security-law.md:第35条` | `03-data-security-law.md:第36条` | 中文数字直查 |
| 31 | `dsl_clause_36_foreign` | clause_lookup | hit | `03-data-security-law.md:第36条` | `03-data-security-law.md:第35条` |  |
| 32 | `pipl_clause_13_basis` | clause_lookup | hit | `04-personal-information-protection-law.md:第13条` | `04-personal-information-protection-law.md:第14条` |  |
| 33 | `pipl_clause_38_crossborder` | clause_lookup | hit | `04-personal-information-protection-law.md:第38条` | `04-personal-information-protection-law.md:第39条` |  |
| 34 | `pipl_clause_57_breach` | clause_lookup | hit | `04-personal-information-protection-law.md:第57条` | `04-personal-information-protection-law.md:第56条` |  |
| 35 | `csl_clause_23_mlps` | clause_lookup | hit | `05-cybersecurity-law.md:第23条` | `05-cybersecurity-law.md:第36条` |  |
| 36 | `csl_clause_39_storage` | clause_lookup | hit | `05-cybersecurity-law.md:第39条` | `05-cybersecurity-law.md:第37条` |  |
| 37 | `csl_clause_40_assessment` | clause_lookup | hit | `05-cybersecurity-law.md:第40条` | `07-cii-security-protection-regulation.md:第17条` | 跨文档 near-miss |
| 38 | `cii_clause_2_scope` | clause_lookup | hit | `07-cii-security-protection-regulation.md:第2条` | `05-cybersecurity-law.md:第33条` | 跨文档 near-miss |
| 39 | `cii_clause_17_assessment` | clause_lookup | hit | `07-cii-security-protection-regulation.md:第17条` | `05-cybersecurity-law.md:第40条` | 跨文档 near-miss |
| 40 | `cii_clause_18_report` | clause_lookup | hit | `07-cii-security-protection-regulation.md:第18条` | `08-cybersecurity-incident-reporting-measures.md:第4条` | 跨文档 near-miss |
| 41 | `archives_clause_13_scope` | clause_lookup | hit | `06-archives-law.md:第13条` | `06-archives-law.md:第14条` |  |
| 42 | `archives_clause_27_open` | clause_lookup | hit | `06-archives-law.md:第27条` | `06-archives-law.md:第30条` |  |
| 43 | `archives_clause_35_digital` | clause_lookup | hit | `06-archives-law.md:第35条` | `06-archives-law.md:第36条` |  |
| 44 | `incident_clause_4_reporting` | clause_lookup | hit | `08-cybersecurity-incident-reporting-measures.md:第4条` | `08-cybersecurity-incident-reporting-measures.md:第5条` |  |
| 45 | `incident_clause_8_summary` | clause_lookup | hit | `08-cybersecurity-incident-reporting-measures.md:第8条` | `08-cybersecurity-incident-reporting-measures.md:第7条` |  |
| 46 | `incident_clause_9_hotline` | clause_lookup | hit | `08-cybersecurity-incident-reporting-measures.md:第9条` | `08-cybersecurity-incident-reporting-measures.md:第10条` |  |
| 47 | `policy_clause_16_effective` | clause_lookup | hit | `01-knowledge-base-policy.md:第16条` | `01-knowledge-base-policy.md:第1条` | 覆盖既有 fixture 的未覆盖条款 |
| 48 | `handbook_sec_2_2_oncall` | clause_lookup | hit | `02-incident-response-handbook.md:§2.2` | `02-incident-response-handbook.md:§2.3` |  |
| 49 | `handbook_sec_1_2_scope` | clause_lookup | hit | `02-incident-response-handbook.md:§1.2` | `02-incident-response-handbook.md:§1.1` |  |
| 50 | `dsl_important_data_assessment` | semantic | hit | `03-data-security-law.md:第30条` | `03-data-security-law.md:第21条` |  |
| 51 | `dsl_data_breach_response` | semantic | hit | `03-data-security-law.md:第29条` | `03-data-security-law.md:第23条` |  |
| 52 | `pipl_minor_consent` | semantic | hit | `04-personal-information-protection-law.md:第31条` | `04-personal-information-protection-law.md:第28条` |  |
| 53 | `pipl_automated_decision` | semantic | hit | `04-personal-information-protection-law.md:第24条` | `04-personal-information-protection-law.md:第55条` |  |
| 54 | `csl_real_name` | semantic | hit | `05-cybersecurity-law.md:第26条` | `05-cybersecurity-law.md:第24条` |  |
| 55 | `csl_incident_plan` | semantic | hit | `05-cybersecurity-law.md:第27条` | `07-cii-security-protection-regulation.md:第18条` | 跨文档 near-miss |
| 56 | `cii_dedicated_org` | semantic | hit | `07-cii-security-protection-regulation.md:第14条` | `07-cii-security-protection-regulation.md:第13条` |  |
| 57 | `archives_transfer` | semantic | hit | `06-archives-law.md:第14条` | `06-archives-law.md:第15条` |  |
| 58 | `pipl_assessment_retention_3y` | numerical | hit | `04-personal-information-protection-law.md:第56条` | `04-personal-information-protection-law.md:第55条` |  |
| 59 | `incident_cii_report_1h` | numerical | hit | `08-cybersecurity-incident-reporting-measures.md:第4条` | `07-cii-security-protection-regulation.md:第18条` |  |
| 60 | `cross_doc_cii_personal_info_export` | cross_doc | hit | `05-cybersecurity-law.md:第39条`, `04-personal-information-protection-law.md:第40条` | `04-personal-information-protection-law.md:第38条` | 双来源：两份法律均对 CII 个人信息出境作出规定；召回任一来源即算命中 |
| 61 | `cross_doc_incident_chain` | cross_doc | hit | `07-cii-security-protection-regulation.md:第18条`, `08-cybersecurity-incident-reporting-measures.md:第4条` | `05-cybersecurity-law.md:第27条` | 跨文档：条例规定报告对象，办法规定具体时限 |
| 62 | `cross_doc_incident_law_measure` | cross_doc | hit | `05-cybersecurity-law.md:第27条`, `08-cybersecurity-incident-reporting-measures.md:第4条` | `07-cii-security-protection-regulation.md:第18条` | 跨文档：一般义务 vs 具体程序 |
| 63 | `conflict_retention_vs_archive` | conflict | hit | `01-knowledge-base-policy.md:第6条`, `06-archives-law.md:第13条` | `06-archives-law.md:第15条` | 两来源冲突：知识库 5 年保留并清除 vs 档案法要求归档；召回任一来源即算命中，冲突本身留给上层处理 |
| 64 | `neg_kubernetes_autoscale` | unanswerable | empty | — | — | 负例：语料外技术主题 |
| 65 | `neg_expense_invoice` | unanswerable | empty | — | — | 负例：语料外财务主题 |
| 66 | `neg_python_binary_tree` | unanswerable | empty | — | — | 负例：语料外编程主题 |
| 67 | `neg_annual_lottery` | unanswerable | empty | — | — | 负例：语料外行政主题 |
| 68 | `neg_protein_supplement` | unanswerable | empty | — | — | 负例：语料外健康主题 |
| 69 | `neg_world_cup_cities` | unanswerable | empty | — | — | 负例：语料外体育主题 |
| 70 | `neg_tiramisu_recipe` | unanswerable | empty | — | — | 负例：语料外烹饪主题 |
| 71 | `neg_stock_option_price` | unanswerable | empty | — | — | 负例：语料外金融主题 |
| 72 | `neg_transformer_attention` | unanswerable | empty | — | — | 负例：语料外机器学习主题 |
| 73 | `neg_euv_lithography` | unanswerable | empty | — | — | 负例：语料外半导体主题 |
| 74 | `neg_french_poem_translation` | unanswerable | empty | — | — | 负例：语料外翻译任务 |
| 75 | `neg_passport_visa` | unanswerable | empty | — | — | 负例：语料外出入境主题 |
| 76 | `neg_piano_exam` | unanswerable | empty | — | — | 负例：语料外艺术主题 |
| 77 | `neg_typhoon_path` | unanswerable | empty | — | — | 负例：语料外气象主题 |
| 78 | `neg_tang_exam_system` | unanswerable | empty | — | — | 负例：语料外历史主题 |
| 79 | `neg_cat_nails` | unanswerable | empty | — | — | 负例：语料外宠物主题 |
| 80 | `neg_decoherence_time` | unanswerable | empty | — | — | 负例：语料外量子计算主题 |

## 4. 局限与披露

- 主标与复核均为 AI，**没有人类法律专家逐条复核**；本数据集用于检索评估，不构成法律意见。
- 公开法律文本已注明来源与版本；若官方文本更新，以官方发布为准，并在新的 PR 中显式更新语料与基线。
- 单标注 + 20% 双标是节省人力的折中；剩余 80% 条目未做第二遍盲标，其错误只能通过后续人工抽检或 live 评估暴露。
