#!/usr/bin/env python3
"""Seed benchmark knowledge documents — 10 comprehensive docs covering all 30 benchmark questions.

Usage:
    python seed_docs/seed_benchmark.py
"""

import asyncio
import json
import logging
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger(__name__)


# ── Document definitions ──────────────────────────────────────────────────
# Each document has meaningful Chinese content designed to answer the
# keyword-based evaluation criteria in benchmark/questions/v2.json

DOCUMENTS = [
    # ──────────────────────────────────────────────────────────────────
    # Doc 1: Company A annual report (星辰科技) — financial data
    # ──────────────────────────────────────────────────────────────────
    {
        "filename": "星辰科技_2024年度报告.md",
        "title": "星辰科技 2024 年度财务报告",
        "description": "星辰科技（股票代码：600888）2024年度财务报告，包含营收、利润、毛利率等财务指标及同比变化",
        "keywords": ["星辰科技", "年度报告", "财务指标", "营收", "利润", "毛利率", "资产负债率"],
        "chunks": [
            ("财务摘要", "星辰科技（股票代码：600888）发布2024年度报告。全年实现营业收入 58.3 亿元，同比增长 18.7%；归属于母公司股东的净利润 11.2 亿元，同比增长 24.5%；扣非净利润 10.6 亿元，同比增长 22.1%。基本每股收益 1.52 元，同比增长 21.6%。"),
            ("盈利分析", "毛利率方面，2024年综合毛利率为 42.5%，较 2023 年的 39.8% 提升 2.7 个百分点。净利率为 19.2%，较 2023 年的 17.9% 提升 1.3 个百分点。收入增长主要得益于人工智能业务板块的快速扩张，该板块收入同比增长 35.2%，占总收入比重从 32% 提升至 38%。"),
            ("资产负债", "资产负债方面，截至 2024年12月31日，公司总资产 126.5 亿元，总负债 52.8 亿元，资产负债率为 41.7%，较 2023 年的 44.2% 下降 2.5 个百分点。经营活动现金流净额为 15.6 亿元，同比增长 31.2%。净资产收益率（ROE）为 15.8%，较 2023 年的 14.2% 提升 1.6 个百分点。"),
            ("业绩回顾与展望", "2023年对比数据：营业收入 49.1 亿元，净利润 9.0 亿元，毛利率 39.8%，净利率 17.9%，资产负债率 44.2%，ROE 14.2%。展望 2025 年，公司预计营收增长 20-25%，将重点布局 AI 大模型和智慧城市业务。"),
        ],
    },
    # ──────────────────────────────────────────────────────────────────
    # Doc 2: Company B annual report (远航制造) — for cross-doc comparison
    # ──────────────────────────────────────────────────────────────────
    {
        "filename": "远航制造_2024年度报告.md",
        "title": "远航制造 2024 年度财务报告",
        "description": "远航制造（股票代码：600999）2024年度财务报告，传统制造企业，可对比科技公司",
        "keywords": ["远航制造", "年度报告", "制造业", "财务指标", "毛利率", "资产负债率"],
        "chunks": [
            ("财务摘要", "远航制造（股票代码：600999）发布2024年度报告。全年实现营业收入 42.1 亿元，同比增长 5.3%；归属于母公司股东的净利润 4.8 亿元，同比增长 3.2%；扣非净利润 4.5 亿元，同比增长 2.8%。基本每股收益 0.65 元。"),
            ("盈利分析", "毛利率方面，2024年综合毛利率为 28.6%，较 2023 年的 27.1% 提升 1.5 个百分点。净利率为 11.4%，较 2023 年的 11.2% 仅提升 0.2 个百分点。原材料成本上涨对利润端形成压力，但通过自动化改造有效控制了制造成本。"),
            ("资产负债", "截至 2024年12月31日，公司总资产 98.7 亿元，总负债 52.3 亿元，资产负债率为 53.0%，较 2023 年的 55.1% 下降 2.1 个百分点。经营活动现金流净额为 7.2 亿元。净资产收益率（ROE）为 9.6%，较 2023 年的 9.3% 微增 0.3 个百分点。"),
            ("对比分析", "2023年对比数据：营业收入 40.0 亿元，净利润 4.6 亿元，毛利率 27.1%，净利率 11.2%，资产负债率 55.1%，ROE 9.3%。与同行业相比，公司毛利率处于中等偏上水平，但净利率偏低，主要受期间费用率较高的影响。"),
        ],
    },
    # ──────────────────────────────────────────────────────────────────
    # Doc 3: Supplier A contract (供应商甲)
    # ──────────────────────────────────────────────────────────────────
    {
        "filename": "供应商甲_采购合同.md",
        "title": "与供应商甲签订的年度采购合同",
        "description": "一份完整的采购合同，包含违约责任、赔偿上限、终止条件和争议解决方式",
        "keywords": ["采购合同", "违约责任", "赔偿", "终止条件", "争议解决"],
        "chunks": [
            ("合同基本信息", "采购合同。甲方（采购方）：星辰科技有限公司。乙方（供应商）：华为技术有限公司。合同编号：XC-HW-2024-001。签约日期：2024年1月15日。合同期限：自2024年2月1日至2025年1月31日。采购标的：AI服务器及配套软件。合同总金额：人民币 8500 万元整。"),
            ("质量与交付条款", "质量与交付条款：乙方应确保产品符合 ISO 9001 质量标准。交货期为收到订单后 30 个工作日内。每延迟一天交付，乙方应按迟延交付货物金额的 0.1% 向甲方支付违约金。延迟超过 30 天的，甲方有权单方解除合同并要求乙方赔偿由此造成的实际损失。"),
            ("付款与赔偿", "付款条款：合同签订后预付 20%，验收合格后支付 70%，质保期满后支付剩余 10%。质保期为验收合格之日起 24 个月。赔偿限制：任一方累计赔偿总额不超过合同总金额的 30%。涉及人身伤害或故意违约的不受此限。知识产权侵权赔偿上限为合同总金额的 100%。"),
            ("终止与争议解决", "终止条件：任何一方严重违约且在收到书面通知后 30 日内未纠正的，守约方有权终止合同。不可抗力持续超过 60 日的，任一方可终止。争议解决方式：双方应首先友好协商解决。协商不成的，提交北京仲裁委员会按照其仲裁规则进行仲裁。仲裁地为北京，使用中文。仲裁裁决为终局性的，对双方均有约束力。"),
        ],
    },
    # ──────────────────────────────────────────────────────────────────
    # Doc 4: Supplier B contract (供应商乙) — for cross-doc comparison
    # ──────────────────────────────────────────────────────────────────
    {
        "filename": "供应商乙_技术服务合同.md",
        "title": "与供应商乙签订的技术服务合同",
        "description": "一份技术服务合同，条款与采购合同有差异，用于对比分析",
        "keywords": ["技术服务合同", "违约责任", "赔偿", "终止条件", "争议解决"],
        "chunks": [
            ("合同基本信息", "技术服务合同。甲方（委托方）：星辰科技有限公司。乙方（服务方）：阿里云计算有限公司。合同编号：XC-ALY-2024-002。签约日期：2024年3月1日。合同期限：自2024年3月15日至2025年3月14日。服务内容：AI 模型训练平台搭建及技术支持。合同总金额：人民币 3200 万元整。"),
            ("服务标准与违约责任", "服务水平协议：乙方保证平台可用性不低于 99.9%。每低于 0.1% 减免 2% 的服务费。违约责任：乙方违反保密义务的，每次向甲方支付 50 万元违约金。甲方逾期付款的，每逾期一天按应付金额的 0.05% 支付违约金。赔偿上限：任一方累计赔偿总额不超过合同总金额的 50%，较采购合同更为宽松。"),
            ("付款与期限", "付款条款：分四期等额支付，每季度初支付 800 万元。合同期限届满前 60 日，双方协商是否续签。终止条件：任一方可提前 90 日书面通知终止合同。违约方在收到通知后 15 日内未纠正的，守约方可立即终止。不可抗力持续超过 30 日的，任一方可终止。"),
            ("知识产权与争议", "知识产权：服务过程中产生的知识产权归甲方所有。乙方在提供服务前已拥有的知识产权归乙方所有。争议解决方式：双方应友好协商。协商不成的，提交杭州仲裁委员会仲裁。与供应商甲的北京仲裁不同。仲裁裁决为终局性。适用法律为中华人民共和国法律。"),
        ],
    },
    # ──────────────────────────────────────────────────────────────────
    # Doc 5: Industry analysis report
    # ──────────────────────────────────────────────────────────────────
    {
        "filename": "人工智能行业研究报告_2024Q4.md",
        "title": "2024年第四季度人工智能行业研究报告",
        "description": "AI行业市场研究报告，包含市场规模、竞争格局、技术趋势和PEST分析",
        "keywords": ["AI", "行业研究", "市场规模", "PEST分析", "技术趋势"],
        "chunks": [
            ("市场概览", "2024年第四季度中国人工智能行业市场规模达到 3820 亿元，同比增长 28.5%。其中生成式 AI 市场规模为 890 亿元，同比增长 65.2%，成为增长最快的细分领域。预计 2025 年整体市场规模将突破 5000 亿元。AI 大模型赛道竞争激烈，截至 2024年底国内已发布 230+ 个大模型。"),
            ("竞争格局", "竞争格局方面，百度、阿里巴巴、腾讯、华为等科技巨头占据市场份额的 65% 左右。初创企业虽然数量众多，但主要集中在垂直领域，如医疗 AI、金融 AI、工业 AI 等。2024年 AI 领域融资总额为 1850 亿元，其中大模型公司融资占比达 42%。头部效应明显。"),
            ("PEST分析", "政治环境：国务院发布《新一代人工智能发展规划》，提出 2025 年 AI 核心产业规模超过 5000 亿元。北京、上海、深圳等地出台 AI 产业扶持政策。经济环境：数字化转型需求旺盛，企业 IT 支出预计增长 12%。社会环境：公众对 AI 接受度提升，AIGC 用户规模突破 2.5 亿。技术环境：多模态大模型、AI Agent、端侧推理等技术快速发展。"),
            ("技术趋势", "技术趋势方面，2024年 AI Agent 成为最热门方向。大模型从单一文本向多模态（文本+图像+视频+音频）演进。端侧 AI 芯片算力提升，推动 AI 应用向手机、PC、IoT 设备渗透。AI 安全与治理也受到更多关注，多部委联合发布《AI 安全治理框架》。全球对比看，中国在应用层领先，但基础算力和高端芯片仍受制于人。"),
        ],
    },
    # ──────────────────────────────────────────────────────────────────
    # Doc 6: Market research report — for multi-year comparison
    # ──────────────────────────────────────────────────────────────────
    {
        "filename": "半导体行业市场回顾_2023年度.md",
        "title": "2023年半导体行业市场回顾与2024年展望",
        "description": "半导体行业2023年回顾与2024年前瞻，含市场规模、需求变化、政策影响",
        "keywords": ["半导体", "市场回顾", "行业展望", "芯片", "供需变化"],
        "chunks": [
            ("2023年市场回顾", "2023年全球半导体市场规模为 5200 亿美元，同比下降 8.2%，主要受智能手机、PC 需求疲软影响。中国半导体市场规模为 1.3 万亿元人民币，同比下降 4.5%。存储芯片价格在下半年触底反弹，AI 芯片需求逆势增长 45%。行业库存调整持续全年，到 2023Q4 库存水位恢复正常。"),
            ("2023年竞争格局", "2023年全球半导体企业排名：英特尔（营收 510 亿美元）、三星（490 亿美元）、台积电（460 亿美元）。中国方面，中芯国际营收 68 亿美元，同比增长 3.5%，但先进制程产能受限。AI 芯片设计公司如寒武纪、地平线等营收增长较快。全年行业资本支出同比下降 12%。"),
            ("2024年市场展望", "2024年展望：全球半导体市场预计恢复增长，增速约 12%，市场规模接近 5800 亿美元。驱动因素包括 AI 服务器需求爆发、汽车电子化率提升、IoT 设备渗透率增长。中国市场的政策利好将持续，国家大基金三期落地。但同时面临地缘政治不确定性。与 2023 年相比，2024 年的行业关键词为复苏与结构性增长。"),
            ("2025年前瞻", "2025年前瞻：预计全球半导体市场规模将突破 6000 亿美元。AI 芯片占比将从 2023 年的 12% 提升至 20%。中国在成熟制程领域的自给率有望提升至 30%。行业整体判断：周期性下行已结束，新一轮上行周期开启。企业需关注地缘政治风险和技术路线选择。"),
        ],
    },
    # ──────────────────────────────────────────────────────────────────
    # Doc 7: New regulation document
    # ──────────────────────────────────────────────────────────────────
    {
        "filename": "数据安全管理条例_2024修订.md",
        "title": "数据安全管理条例（2024年修订版）",
        "description": "2024年新修订的数据安全法规，包含核心变化和对企业的影响",
        "keywords": ["数据安全", "法规", "合规", "个人信息保护", "跨境数据"],
        "chunks": [
            ("修订背景与总则", "2024年修订版数据安全管理条例于2024年6月1日正式施行。本次修订是在 2021 年《数据安全法》基础上的细化与补充。修订的核心背景包括：生成式 AI 快速发展带来的新型数据风险、跨境数据流动需求增加、以及数据要素市场化改革的需要。条例共 8 章 65 条。"),
            ("核心变化一：数据分类分级", "核心变化一：数据分类分级制度更加严格。企业需建立数据分类分级管理制度，将数据分为一般数据、重要数据和核心数据三级。重要数据目录由行业主管部门制定，涉及 15 个重点行业。未按规定进行数据分类分级的，最高可处 100 万元罚款。较原条例的处罚上限提高了 5 倍。"),
            ("核心变化二：AI 数据合规", "核心变化二：新增 AI 训练数据合规要求。使用个人信息进行 AI 模型训练前，必须取得用户单独同意。AI 生成内容需添加不可篡改的数字水印。算法备案范围扩大至所有具有舆论属性和社会动员能力的算法。违反规定的最高可处 5000 万元或上一年度营业额 5% 的罚款。"),
            ("对企业的主要影响", "对企业的主要影响：一、合规成本上升，中型企业预计新增 50-100 万元/年的合规支出。二、数据跨境传输需通过安全评估，申报门槛从 100 万人降至 10 万人个人信息。三、企业需在 2025年6月1日前完成存量数据分类分级工作。四、鼓励企业通过数据安全认证，认证结果可作为合规证明。过渡期为一年。"),
        ],
    },
    # ──────────────────────────────────────────────────────────────────
    # Doc 8: Corporate announcement — for cross-doc comparison with annual report
    # ──────────────────────────────────────────────────────────────────
    {
        "filename": "星辰科技_收购云帆科技公告.md",
        "title": "星辰科技关于收购云帆科技100%股权的公告",
        "description": "星辰科技重大资产重组公告，涉及收购AI芯片设计公司云帆科技",
        "keywords": ["星辰科技", "收购", "云帆科技", "资产重组", "商誉"],
        "chunks": [
            ("交易概述", "证券代码：600888 证券简称：星辰科技 公告编号：2024-045。星辰科技有限公司关于收购云帆科技 100% 股权的公告。本公司董事会及全体董事保证本公告内容不存在虚假记载、误导性陈述或者重大遗漏。交易金额：人民币 15.8 亿元。支付方式：发行股份及支付现金。"),
            ("交易详情", "交易对方：云帆科技现有股东（合计 12 名）。标的资产：云帆科技 100% 股权。云帆科技是一家专注于 AI 芯片设计的初创公司，2023年营收 2.1 亿元，净利润 0.32 亿元。2024年预计营收 3.5 亿元。评估采用收益法，评估增值率为 320%。本次交易预计形成商誉约 10.2 亿元。"),
            ("交易影响", "对上市公司的影响：收购完成后，星辰科技将获得 AI 芯片自研能力，形成'算法+算力'一体化布局。预计 2025 年将增厚公司每股收益约 0.15 元。需关注商誉减值风险，若云帆科技未来业绩不达预期，可能产生商誉减值。该事项尚需股东大会审议通过并经证监会核准。风险提示：整合不及预期、商誉减值、技术迭代等风险。"),
        ],
    },
    # ──────────────────────────────────────────────────────────────────
    # Doc 9: Policy analysis report — for PEST analysis
    # ──────────────────────────────────────────────────────────────────
    {
        "filename": "中国AI产业政策分析报告.md",
        "title": "2024年中国人工智能产业政策环境分析",
        "description": "中国AI产业政策分析，涵盖国家战略、地方政策、产业扶持、监管框架",
        "keywords": ["AI产业政策", "监管", "产业扶持", "智算中心", "数据要素"],
        "chunks": [
            ("国家战略层面", "国家层面：2024年《政府工作报告》首次提出'人工智能+'行动，将 AI 定位为新质生产力的核心驱动力。科技部牵头实施新一代 AI 重大项目，中央财政年度投入超 200 亿元。发改委等八部门联合印发《关于促进人工智能创新发展的指导意见》，目标到 2026 年 AI 核心产业规模突破 8000 亿元。"),
            ("地方政策与产业扶持", "地方层面：全国 28 个省级行政区已出台 AI 产业专项政策。北京打造'全球 AI 创新策源地'，对国家级 AI 研发平台最高支持 1 亿元。上海推进'模塑申城'工程，目标 2025 年 AI 产业规模突破 4000 亿元。深圳建设'AI 先锋城市'，设立 100 亿元 AI 产业基金。地方政府对 AI 企业的税收优惠力度较大。"),
            ("监管与合规框架", "监管方面：AI 治理框架逐步完善。《生成式人工智能服务管理暂行办法》2023年8月施行，明确生成式 AI 需备案。2024年新增 AI 深度合成内容标识要求。数据跨境流动管理趋严。知识产权方面，AI 生成内容的版权归属尚无明确法律规定。整体监管思路为'包容审慎，分级分类'。"),
            ("产业趋势与挑战", "产业趋势研判：智算中心建设加速，2024年全国智算中心数量超过 120 个。数据要素市场逐步形成，2024年数据交易规模预计超 100 亿元。AI 人才缺口达 100 万以上。算力卡脖子问题短期内难以解决。技术路线方面，模型开源与闭源之争持续，国产框架生态快速成长。整体来看，政策环境对 AI 产业极度友好。"),
        ],
    },
    # ──────────────────────────────────────────────────────────────────
    # Doc 10: SWOT & DuPont framework reference document
    # ──────────────────────────────────────────────────────────────────
    {
        "filename": "企业战略分析框架指南.md",
        "title": "企业战略分析框架指南：SWOT、PEST、杜邦分析",
        "description": "对企业战略分析中常用框架的方法论说明，包括SWOT、PEST、杜邦分析体系的介绍",
        "keywords": ["SWOT", "PEST", "杜邦分析", "战略框架", "财务分析"],
        "chunks": [
            ("SWOT分析框架", "SWOT 分析是一种常用的企业战略分析工具。S（Strengths）指企业的内部优势，如技术壁垒、品牌影响力、成本优势等。W（Weaknesses）指内部劣势，如资金不足、技术落后。O（Opportunities）指外部机会，如市场增长、政策利好。T（Threats）指外部威胁，如新进入者、替代品威胁。SWOT 分析的核心是发挥优势、弥补劣势、抓住机会、规避威胁。"),
            ("PEST分析框架", "PEST 分析用于评估宏观环境。P（Political）政治环境：政府政策、法律法规、贸易政策、税收政策等。E（Economic）经济环境：GDP 增长、利率、通胀、就业率、可支配收入等。S（Social）社会环境：人口结构、文化趋势、消费习惯、教育水平等。T（Technological）技术环境：技术革新、研发投入、自动化、知识产权保护等。"),
            ("杜邦分析体系", "杜邦分析（DuPont Analysis）是一种将净资产收益率（ROE）分解为多个财务比率的分析方法。核心公式：ROE = 净利润率 × 总资产周转率 × 权益乘数。净利润率 = 净利润 / 营业收入，反映盈利能力。总资产周转率 = 营业收入 / 平均总资产，反映运营效率。权益乘数 = 总资产 / 净资产 = 1 / (1 - 资产负债率)，反映财务杠杆水平。通过杜邦分解可以找到 ROE 变动的驱动因素。"),
            ("财务比率分析框架", "财务比率分析通常从四个维度进行：一、盈利能力指标：毛利率、净利率、总资产报酬率（ROA）、净资产收益率（ROE）。二、偿债能力指标：流动比率、速动比率、资产负债率、利息保障倍数。三、营运能力指标：应收账款周转率、存货周转率、总资产周转率。四、现金流指标：经营活动现金流净额、自由现金流、现金转换周期。综合运用这些指标可以全面评估企业财务状况。"),
        ],
    },
]


async def main():
    import logging
    logging.basicConfig(level=logging.INFO)

    from sqlalchemy import delete, select

    from app.core.config import settings
    from app.core.database import AsyncSessionLocal, init_db
    from app.core.elasticsearch import (
        _create_index_if_not_exists,
        es_client,
        init_elasticsearch,
    )
    from app.models.document import Document, DocumentChunk, DocumentStatus, DocumentType
    from app.models.organization import Organization
    from app.models.user import User
    from app.services.clause_refs import extract_clause_fields
    from app.services.embedding_service import embedding_service

    await init_db()
    await init_elasticsearch()

    # Ensure ES index exists with correct mapping
    index_name = settings.ELASTICSEARCH_INDEX_NAME
    try:
        exists = await es_client.indices.exists(index=index_name)
        if exists:
            logger.info(f"Dropping existing ES index '{index_name}'...")
            await es_client.indices.delete(index=index_name)
        await _create_index_if_not_exists(await es_client)
        logger.info(f"ES index '{index_name}' ready")
    except Exception as e:
        logger.warning(f"ES index setup: {e}")

    async with AsyncSessionLocal() as db:
        org = (await db.execute(select(Organization).where(Organization.name == "Default"))).scalar_one()
        user = (await db.execute(select(User).where(User.username == "guest"))).scalar_one()
        org_id = org.id
        user_id = user.id
        logger.info(f"Using org_id={org_id}, user_id={user_id}")

        # ── Delete existing documents (handle FK constraints) ──
        existing = (await db.execute(select(Document))).scalars().all()
        logger.info(f"Existing documents: {len(existing)}")
        from app.models.knowledge_job import KnowledgeProcessingJob
        for d in existing:
            await db.execute(delete(KnowledgeProcessingJob).where(KnowledgeProcessingJob.document_id == d.id))
            await db.execute(delete(DocumentChunk).where(DocumentChunk.document_id == d.id))
            logger.info(f"  Cleaned FK refs for {d.filename}")
        for d in existing:
            await db.delete(d)
            logger.info(f"  Deleted doc: {d.filename}")
        await db.commit()
        logger.info("All old documents cleaned")

        # ── Create documents ──
        all_chunks = []  # (chunk_text, doc_id, chunk_index, section_title, chunk_id, filename)
        current_time = datetime.now(UTC)

        for doc_info in DOCUMENTS:
            doc_id = str(uuid.uuid4())
            full_text = "\n\n".join(c[1] for c in doc_info["chunks"])
            content_length = len(full_text)
            chunk_count = len(doc_info["chunks"])

            doc = Document(
                id=doc_id,
                filename=doc_info["filename"],
                file_path=f"seed_docs/{doc_info['filename']}",
                file_size=len(full_text.encode("utf-8")),
                file_type=DocumentType.TXT,
                mime_type="text/markdown",
                title=doc_info["title"],
                description=doc_info["description"],
                keywords=json.dumps(doc_info.get("keywords", []), ensure_ascii=False),
                status=DocumentStatus.INDEXED,
                content_length=content_length,
                chunk_count=chunk_count,
                organization_id=org_id,
                uploaded_by=user_id,
                created_at=current_time,
                updated_at=current_time,
                parsed_at=current_time,
                indexed_at=current_time,
            )
            db.add(doc)

            for idx, (section_title, text) in enumerate(doc_info["chunks"]):
                chunk_id = str(uuid.uuid4())
                chunk = DocumentChunk(
                    id=chunk_id,
                    document_id=doc_id,
                    chunk_index=idx,
                    chunk_text=text,
                    chunk_length=len(text),
                    section_title=section_title,
                    meta_data={"source": "benchmark_seed", "section": section_title},
                )
                db.add(chunk)
                all_chunks.append((text, doc_id, idx, section_title, chunk_id, doc_info["filename"]))

            logger.info(f"  + {doc_info['filename']} ({chunk_count} chunks, {content_length} chars)")

        await db.commit()
        logger.info(f"\nAll {len(DOCUMENTS)} documents saved to DB ({len(all_chunks)} total chunks)\n")

    # ── Generate embeddings and index to ES ──
    logger.info("=" * 50)
    logger.info("Generating embeddings...")
    logger.info("=" * 50)

    texts = [c[0] for c in all_chunks]
    embeddings = await embedding_service.get_embeddings(texts)
    logger.info(f"Generated {len(embeddings)} embeddings (dim={len(embeddings[0]) if embeddings else 0})")

    logger.info("\nIndexing to Elasticsearch...")
    indexed = 0
    for (text, doc_id, idx, section_title, chunk_id, filename), emb in zip(all_chunks, embeddings):
        doc_body = {
            "chunk_id": chunk_id,
            "document_id": doc_id,
            "filename": filename,
            "content": text,
            "chunk_text": text,
            "chunk_index": idx,
            "section_title": section_title,
            # #188：结构化条款字段与 worker 索引链同口径（定义位抽取，见 clause_refs）
            **extract_clause_fields(text),
            "organization_id": str(org_id),
            "content_length": len(text),
            "embedding": emb,
        }
        try:
            await es_client.index(
                index=index_name,
                id=chunk_id,
                body=doc_body,
                refresh="wait_for",
            )
            indexed += 1
        except Exception as e:
            logger.warning(f"  Failed to index chunk {chunk_id[:8]}: {e}")

    logger.info(f"Indexed {indexed}/{len(all_chunks)} chunks to Elasticsearch")

    # ── Summary ──
    print(f"""
{'=' * 55}
  Benchmark Knowledge Base — 导入完成
{'=' * 55}
  文档数:  {len(DOCUMENTS)}
  总Chunk: {len(all_chunks)}
  向量维度: {len(embeddings[0]) if embeddings else 0}

  文档清单:
""")
    for i, d in enumerate(DOCUMENTS, 1):
        chunks = len(d["chunks"])
        chars = sum(len(c[1]) for c in d["chunks"])
        print(f"  {i:2d}. {d['filename']:<35s} {chunks}chunk {chars}chars")
    print(f"""
  覆盖题目:
    single_doc: L1-DOC-01~04
    cross_doc:  L1-CROSS-01~05
    framework:  L1-FRAME-01~05
    multi_step: L1-MULTI-01~04
    tool_recovery: L2-RECOV-01~03 (部分依赖文档)

  下一步:
    python -m benchmark.run --mode baseline --questions benchmark/questions/v2.json --output benchmark/results/baseline_v2.json
    python -m benchmark.run --mode agent    --questions benchmark/questions/v2.json --output benchmark/results/agent_v2.json
{'=' * 55}
""")


if __name__ == "__main__":
    asyncio.run(main())
