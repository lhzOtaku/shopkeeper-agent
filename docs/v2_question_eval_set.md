# v2 问数评测集设计说明

这份文档解释 `eval/v2_questions.yaml` 为什么这样设计，以及后续如何用它评估 askAgent。

当前阶段的目标不是马上做复杂自动评分，而是先建立一套稳定的、可复用的人工/半自动评测基准。这样每次改提示词、改召回、改指标定义、改 SQL 修正逻辑之后，都可以用同一批问题回归测试。

## 1. 为什么要做评测集

一个问数 Agent 只靠页面演示是不够的，因为页面演示通常只能证明“某几个问题能跑通”。

评测集要证明的是：

```text
1. 系统能不能稳定识别用户意图。
2. 系统能不能召回正确字段和指标。
3. 系统能不能识别用户提到的真实业务值。
4. 系统能不能按正确指标口径生成 SQL。
5. 系统能不能在多表 Join、TopN、退款、时间过滤等复杂场景下稳定工作。
6. 系统改动后有没有退化。
```

面试时你可以这样讲：

```text
我没有只停留在 demo 层面，而是为 v2 数仓设计了一套覆盖指标口径、字段召回、值召回、多表 Join 和退款口径的评测集，用它来做问数 Agent 的回归测试。
```

## 2. 评测集文件在哪里

机器可读评测集：

```text
eval/v2_questions.yaml
```

说明文档：

```text
docs/v2_question_eval_set.md
```

第一版共有 30 条问题。

## 3. 每条评测样本的字段含义

每条样本不是只保存一个自然语言问题，而是保存一组检查点。

```yaml
- id: V2_EVAL_001
  group: basic_metric
  difficulty: easy
  question: "统计 2025 年第一季度各大区的 GMV，并按 GMV 从高到低排序"
  intent: data_query
  expected_metrics: [GMV]
  expected_tables: [fact_order, dim_region, dim_date]
  expected_columns:
    - fact_order.order_amount
    - dim_region.region_name
    - dim_date.year
    - dim_date.quarter
  requires_join: true
  requires_value_recall: false
  sql_assertions:
    must_include: ["sum", "order_amount", "dim_region", "dim_date", "2025", "Q1"]
    should_exclude: ["cancelled"]
  notes: "检查 GMV 是否使用 order_amount，并且按大区聚合。"
```

### id

唯一编号，后续自动化脚本会按 id 输出测试结果。

### group

问题类别，用来观察系统在哪一类问题上容易失败。

当前包含：

```text
basic_metric      基础指标
metric_confusion  容易混淆的指标
value_recall      字段值召回
topn              TopN 排名
member_analysis   会员分析
channel_analysis  渠道分析
refund            退款分析
time              时间分析
region_analysis   地区分析
product_analysis  商品分析
status_filter     状态过滤
multi_dimension   多维度聚合
followup_ready    后续多轮对话预留
```

### difficulty

难度分层。

```text
easy：单指标或简单 Join
medium：指标口径区分、字段值召回、多表 Join
hard：复杂口径、多维度、退款率、派生指标
```

### question

用户自然语言问题。

这部分是 askAgent 的直接输入。

### expected_metrics

期望命中的业务指标。

例如：

```text
GMV
实付金额
订单数
销量
客单价
人均消费
退款金额
订单退款率
件数退款率
```

这可以用来检查指标召回是否正确。

### expected_tables

生成 SQL 时应该用到的表。

例如查询地区 GMV，应该涉及：

```text
fact_order
dim_region
dim_date
```

如果模型漏了 `dim_region`，通常说明它无法按地区聚合。

### expected_columns

生成 SQL 时应该用到的关键字段。

这个字段比 expected_tables 更细，可以判断模型是否用对口径。

例如：

```text
GMV -> fact_order.order_amount 或商品粒度下的 fact_order_item.item_amount
实付金额 -> fact_order.pay_amount
销量 -> fact_order_item.quantity
退款金额 -> fact_refund.refund_amount
```

### requires_join

是否需要多表 Join。

大多数真实问数问题都需要 Join，因为事实表只存 id，维度名称在维表里。

### requires_value_recall

是否需要 ES 字段值召回。

例如用户说：

```text
上海
华东
直播
数码
服饰
618
```

这些词本身不是字段名，而是字段值。系统需要通过 ES 找到它们属于哪一列。

### value_hints

字段值召回的期望结果。

例如：

```yaml
value_hints:
  - value: 上海
    expected_column: dim_region.city
```

这表示用户问题里的“上海”应该被识别为 `dim_region.city` 的取值。

### sql_assertions

SQL 字符串级别的粗粒度检查。

第一版不做精确 SQL 等价判断，因为同一个问题可以有多种正确 SQL 写法。

所以先检查：

```text
必须包含哪些关键词
不应该包含哪些字段
是否应该排除取消订单
TopN 是否带 limit
```

例如：

```yaml
must_include: ["sum", "order_amount", "2025", "Q1"]
must_not_include: ["sum(pay_amount"]
should_exclude: ["cancelled"]
```

## 4. 为什么第一版是 30 题

30 题的目标是覆盖主要能力面，而不是追求数量。

覆盖面如下：

```text
基础指标：GMV、订单数、销量、实付金额
指标混淆：GMV vs 实付金额，订单数 vs 销量，客单价 vs 人均消费
字段值召回：上海、华东、直播、数码、服饰、618
多表 Join：订单、明细、商品、地区、渠道、会员、日期、退款
退款分析：退款金额、退款件数、订单退款率、件数退款率
时间分析：季度、月份、618、工作日/周末
多维分析：大区 x 渠道，城市等级 x 渠道类型
状态过滤：默认排除 cancelled，以及用户明确查询 cancelled 的例外
```

这 30 题足够用于第一版项目验收和面试展示。

## 5. 如何人工评测

第一阶段可以手工跑。

步骤：

```text
1. 打开前端页面。
2. 按 eval/v2_questions.yaml 中的 question 逐条提问。
3. 查看前端返回结果和后端日志中的 SQL。
4. 对照 expected_tables、expected_columns、expected_metrics、sql_assertions。
5. 记录通过/失败原因。
```

建议记录结果：

```text
case_id
是否跑通
生成 SQL
错误类型
修复建议
```

## 6. 如何自动化评测

后续可以写一个脚本：

```text
scripts/v2/run_eval_v2.py
```

脚本逻辑：

```text
1. 读取 eval/v2_questions.yaml。
2. 对每个 question 调用 askAgent。
3. 捕获生成的 SQL。
4. 检查 SQL 是否包含 must_include。
5. 检查 SQL 是否不包含 must_not_include。
6. 执行 SQL，确认不报错。
7. 输出每条 case 的 PASS/FAIL。
8. 汇总通过率和失败原因。
```

第一版自动评测可以先做 SQL 字符串检查和执行成功检查。

第二版再做：

```text
召回结果检查
结果数值校验
SQL AST 结构校验
指标口径校验
多轮问题评测
```

## 7. 失败类型怎么分类

建议把失败分成几类，这样更容易定位问题。

```text
intent_error
意图识别错误，比如问数问题被当成普通聊天。

retrieval_column_error
字段召回错误，比如没有召回 order_amount。

retrieval_metric_error
指标召回错误，比如问实付金额却召回 GMV。

retrieval_value_error
字段值召回错误，比如“上海”没有识别到 dim_region.city。

table_filter_error
表过滤错误，比如缺少 dim_product 或误选无关表。

metric_filter_error
指标过滤错误，比如保留了错误指标。

sql_generation_error
SQL 生成错误，比如字段名不存在、Join 条件错误。

sql_validation_error
SQL 校验或修正链路错误。

execution_error
SQL 可以生成但执行失败。

business_semantic_error
SQL 能执行，但指标口径错了。
```

面试时这个分类很有价值，因为它说明你知道 Agent 失败不是一个笼统的“模型不行”，而是可以拆到具体链路。

## 8. 当前评测集最重要的几类题

### 8.1 GMV 和实付金额

```text
GMV -> order_amount / item_amount
实付金额 -> pay_amount
```

如果问“实付金额”却用了 `order_amount`，就是指标口径错误。

### 8.2 订单数和销量

```text
订单数 -> count(distinct order_id)
销量 -> sum(quantity)
```

如果销量用 `count(*)`，就是口径错误。

### 8.3 客单价和人均消费

```text
客单价 -> GMV / 订单数
人均消费 -> GMV / 支付用户数
```

这两个很容易混，适合面试讲亮点。

### 8.4 退款日期和下单日期

退款金额、退款件数这类问题默认按退款日期过滤：

```text
fact_refund.date_id
```

订单 GMV、销量、订单数默认按下单日期过滤：

```text
fact_order.date_id
```

### 8.5 默认排除 cancelled

经营指标默认排除已取消订单。

但是如果用户明确问：

```text
统计已取消订单数量
```

这时不能排除 cancelled，而是应该过滤：

```sql
where order_status = 'cancelled'
```

## 9. 这一步对项目包装的意义

做完评测集后，简历可以写得更有工程感：

```text
设计 v2 电商数仓和 30 条问数评测集，覆盖指标口径、字段值召回、多表 Join、退款分析和多维聚合场景；基于评测结果定位召回、SQL 生成和业务语义错误，支撑 Agent 的持续迭代。
```

这比只写“实现了 Text2SQL”更有说服力。

## 10. 当前自动评测脚本

当前已经提供第一版脚本：

```text
scripts/v2/run_eval_v2.py
```

常用命令如下。

只检查评测集能否读取：

```powershell
uv run python scripts\v2\run_eval_v2.py --dry-run
```

列出前 3 条样本：

```powershell
uv run python scripts\v2\run_eval_v2.py --list --limit 3
```

只跑某一条：

```powershell
uv run python scripts\v2\run_eval_v2.py --case-id V2_EVAL_001 --write-report
```

只跑某一类：

```powershell
uv run python scripts\v2\run_eval_v2.py --group refund --write-report
```

只跑前 5 条：

```powershell
uv run python scripts\v2\run_eval_v2.py --limit 5 --write-report
```

遇到第一条失败就停止：

```powershell
uv run python scripts\v2\run_eval_v2.py --fail-fast --write-report
```

把 warning 也当成失败：

```powershell
uv run python scripts\v2\run_eval_v2.py --strict --write-report
```

脚本会输出：

```text
总数
通过数
失败数
警告数
通过率
按类别统计
详细 JSON 报告路径
```

报告默认写到：

```text
eval/reports/
```

当前第一版脚本主要检查：

```text
1. askAgent 是否生成 SQL
2. SQL 是否能通过校验并执行
3. SQL 是否包含 must_include 片段
4. SQL 是否没有出现 must_not_include 片段
5. SQL 是否是只读查询
6. TopN 问题是否带 LIMIT
7. 是否存在业务口径 warning，例如默认经营指标没有显式排除 cancelled
```

### 10.1 PASS 不等于完全正确

第一版里有些业务语义问题会先记为 warning。

例如经营指标默认应该排除已取消订单：

```sql
fact_order.order_status <> 'cancelled'
```

如果 SQL 能执行，但没有显式排除 `cancelled`，脚本会输出 warning。

这类问题说明：

```text
SQL 语法正确
查询可以执行
但业务口径还不够严谨
```

后续可以通过两种方式强化：

```text
1. 使用 --strict，把 warning 也计为失败。
2. 优化指标提示词，让 GMV、订单数、销量等经营指标默认带上 order_status 过滤。
```

## 11. 下一步

下一步建议写评测运行脚本：

```text
scripts/v2/run_eval_v2.py
```

这一步已经完成。后续建议进入第二版评测增强：

```text
1. 增加召回结果检查，判断 Qdrant/ES 是否召回了预期字段、指标和值。
2. 增加 SQL 结构检查，减少纯字符串断言的误判。
3. 增加结果数值校验，固定少量标准问题的 expected_rows。
4. 把评测结果反向用于优化提示词和元数据描述。
```

目前最值得优先修的是：经营指标默认排除已取消订单。
