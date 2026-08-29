# v2 电商问数指标口径设计稿

这份文档是 v2 落地的第三步：在表结构基础上，明确 askAgent 问数时每个业务指标到底怎么算。

表结构回答的是：

```text
数据放在哪里？
```

指标口径回答的是：

```text
用户说 GMV、订单数、销量、退款率时，SQL 应该怎么写？
```

如果指标口径不清楚，大模型很容易把“销售额”“销量”“订单数”混在一起。这也是 Text2SQL 项目最常见的错误来源之一。

## 1. 指标设计原则

v2 第一版先遵守这些原则：

```text
指标名称要贴近业务人员说法。
每个指标必须有明确 SQL 口径。
金额类、订单类、商品件数类指标要严格区分。
派生指标要写清楚分子和分母。
默认只统计有效订单。
默认只统计成功退款。
```

## 2. 默认数据过滤规则

### 2.1 有效订单

第一版建议把这些订单状态视为有效订单：

```text
paid
completed
partial_refunded
refunded
```

排除：

```text
cancelled
```

原因：

```text
cancelled 表示订单取消，不应该进入 GMV、订单数、销量等经营统计。
refunded 和 partial_refunded 表示订单曾经成交过，是否扣除退款要看具体指标口径。
```

所以大多数订单相关指标默认加：

```sql
fact_order.order_status <> 'cancelled'
```

或者：

```sql
fact_order.order_status IN ('paid', 'completed', 'partial_refunded', 'refunded')
```

### 2.2 成功退款

退款相关指标默认只统计：

```text
refund_status = 'success'
```

排除：

```text
processing
rejected
```

原因：

```text
processing 还在处理中，金额不确定。
rejected 表示退款被拒绝，不应该算作实际退款。
```

## 3. 基础指标

### 3.1 GMV

业务含义：

```text
成交总额，表示有效订单的原始成交金额总和。
```

推荐口径：

```sql
SUM(fact_order.order_amount)
```

默认过滤：

```sql
fact_order.order_status <> 'cancelled'
```

常见别名：

```text
销售额
成交额
成交总额
订单金额
销售金额
GMV
```

说明：

```text
GMV 使用 order_amount，不扣优惠，不扣退款。
如果用户明确说“实付金额”，才使用 pay_amount。
```

示例 SQL 片段：

```sql
SELECT SUM(fo.order_amount) AS GMV
FROM fact_order fo
WHERE fo.order_status <> 'cancelled';
```

### 3.2 实付金额

业务含义：

```text
用户实际支付的金额总和，通常已经扣除优惠。
```

推荐口径：

```sql
SUM(fact_order.pay_amount)
```

默认过滤：

```sql
fact_order.order_status <> 'cancelled'
```

常见别名：

```text
支付金额
实收金额
实付销售额
实际支付金额
```

说明：

```text
不要和 GMV 混用。GMV 看成交规模，实付金额看用户实际付款。
```

### 3.3 订单数

业务含义：

```text
有效订单数量。
```

推荐口径：

```sql
COUNT(DISTINCT fact_order.order_id)
```

默认过滤：

```sql
fact_order.order_status <> 'cancelled'
```

常见别名：

```text
订单量
下单数
成交订单数
订单数量
```

说明：

```text
订单数不是销量。一个订单可能包含多个商品和多件商品。
```

### 3.4 销量

业务含义：

```text
有效订单中卖出的商品件数。
```

推荐口径：

```sql
SUM(fact_order_item.quantity)
```

默认关联：

```text
fact_order_item -> fact_order
```

默认过滤：

```sql
fact_order.order_status <> 'cancelled'
```

常见别名：

```text
销售件数
售出数量
商品销量
购买件数
销售数量
```

说明：

```text
销量必须从订单明细表计算，不能从订单表计算。
```

示例 SQL 片段：

```sql
SELECT SUM(foi.quantity) AS 销量
FROM fact_order_item foi
JOIN fact_order fo ON foi.order_id = fo.order_id
WHERE fo.order_status <> 'cancelled';
```

### 3.5 支付用户数

业务含义：

```text
发生有效下单行为的去重会员数。
```

推荐口径：

```sql
COUNT(DISTINCT fact_order.member_id)
```

默认过滤：

```sql
fact_order.order_status <> 'cancelled'
```

常见别名：

```text
购买用户数
成交用户数
下单用户数
付费用户数
支付会员数
```

说明：

```text
支付用户数关注用户规模，不关注订单笔数。
```

## 4. 派生指标

### 4.1 客单价 AOV

业务含义：

```text
平均每笔订单的成交金额。
```

推荐口径：

```sql
SUM(fact_order.order_amount) / COUNT(DISTINCT fact_order.order_id)
```

默认过滤：

```sql
fact_order.order_status <> 'cancelled'
```

常见别名：

```text
平均订单金额
AOV
平均客单价
每单金额
```

说明：

```text
客单价的分子是 GMV，分母是订单数。
不要用 SUM(order_amount) / SUM(quantity)，那更接近件单价。
```

示例：

```sql
SELECT
  SUM(fo.order_amount) / NULLIF(COUNT(DISTINCT fo.order_id), 0) AS 客单价
FROM fact_order fo
WHERE fo.order_status <> 'cancelled';
```

### 4.2 人均消费

业务含义：

```text
平均每个支付用户贡献的成交金额。
```

推荐口径：

```sql
SUM(fact_order.order_amount) / COUNT(DISTINCT fact_order.member_id)
```

默认过滤：

```sql
fact_order.order_status <> 'cancelled'
```

常见别名：

```text
客均消费
ARPPU
人均 GMV
人均成交额
```

说明：

```text
人均消费的分母是用户数，不是订单数。
```

### 4.3 件单价

业务含义：

```text
平均每件商品的成交金额。
```

推荐口径：

```sql
SUM(fact_order_item.item_amount) / SUM(fact_order_item.quantity)
```

默认过滤：

```sql
fact_order.order_status <> 'cancelled'
```

常见别名：

```text
平均件单价
单件均价
商品均价
```

说明：

```text
这个指标用于商品分析，不要和客单价混淆。
```

## 5. 退款指标

### 5.1 退款金额

业务含义：

```text
成功退款的金额总和。
```

推荐口径：

```sql
SUM(fact_refund.refund_amount)
```

默认过滤：

```sql
fact_refund.refund_status = 'success'
```

常见别名：

```text
售后金额
退款总额
退货金额
```

### 5.2 退款订单数

业务含义：

```text
发生成功退款的去重订单数量。
```

推荐口径：

```sql
COUNT(DISTINCT fact_refund.order_id)
```

默认过滤：

```sql
fact_refund.refund_status = 'success'
```

常见别名：

```text
售后订单数
退款单数
退货订单数
```

### 5.3 退款件数

业务含义：

```text
成功退款的商品件数。
```

推荐口径：

```sql
SUM(fact_refund.refund_quantity)
```

默认过滤：

```sql
fact_refund.refund_status = 'success'
```

常见别名：

```text
退货件数
售后件数
退款数量
```

### 5.4 订单退款率

业务含义：

```text
发生退款的订单数占有效订单数的比例。
```

推荐口径：

```sql
COUNT(DISTINCT fact_refund.order_id) / COUNT(DISTINCT fact_order.order_id)
```

默认过滤：

```sql
fact_order.order_status <> 'cancelled'
fact_refund.refund_status = 'success'
```

常见别名：

```text
退款率
售后率
订单售后率
退货率
```

说明：

```text
如果用户只说“退款率”，默认使用订单退款率。
如果用户问“商品退款率”或“品类退款率”，优先使用件数退款率。
```

### 5.5 件数退款率

业务含义：

```text
退款件数占销售件数的比例。
```

推荐口径：

```sql
SUM(fact_refund.refund_quantity) / SUM(fact_order_item.quantity)
```

默认过滤：

```sql
fact_order.order_status <> 'cancelled'
fact_refund.refund_status = 'success'
```

常见别名：

```text
商品退款率
品类退款率
退货件数占比
```

说明：

```text
按商品、品牌、品类分析时，更推荐件数退款率，因为分子分母都在商品明细粒度。
```

## 6. 维度与指标的常见组合

### 6.1 地区维度

适合组合：

```text
GMV
订单数
客单价
支付用户数
退款率
```

常见问题：

```text
统计各地区 GMV
查询华东地区订单数
按城市等级统计客单价
```

表路径：

```text
fact_order -> dim_region
```

### 6.2 商品和品类维度

适合组合：

```text
销量
商品销售额
件单价
退款件数
件数退款率
```

常见问题：

```text
查询销量最高的前 5 个商品
统计各品类 GMV
查询退款率最高的品类
```

表路径：

```text
fact_order -> fact_order_item -> dim_product
fact_refund -> fact_order_item -> dim_product
```

### 6.3 会员维度

适合组合：

```text
GMV
订单数
客单价
人均消费
支付用户数
```

常见问题：

```text
按会员等级统计客单价
统计新客和老客 GMV
不同年龄段人均消费是多少
```

表路径：

```text
fact_order -> dim_member
```

### 6.4 渠道维度

适合组合：

```text
GMV
订单数
客单价
支付用户数
退款率
```

常见问题：

```text
各渠道 GMV 排名
直播渠道退款率是多少
付费渠道和自然渠道客单价对比
```

表路径：

```text
fact_order -> dim_channel
fact_refund -> fact_order -> dim_channel
```

### 6.5 时间维度

适合组合：

```text
所有指标
```

常见问题：

```text
按月统计 GMV
统计去年第一季度订单数
对比 618 期间各品类销量
```

表路径：

```text
fact_order -> dim_date
fact_refund -> dim_date
```

注意：

```text
订单类指标按下单日期过滤。
退款类指标按退款日期过滤。
```

## 7. 容易混淆的业务词

### 7.1 销售额、GMV、实付金额

```text
销售额 / GMV / 成交额：默认 SUM(order_amount)
实付金额 / 支付金额：默认 SUM(pay_amount)
```

### 7.2 订单数、销量

```text
订单数：COUNT(DISTINCT order_id)
销量：SUM(quantity)
```

### 7.3 客单价、人均消费、件单价

```text
客单价：GMV / 订单数
人均消费：GMV / 支付用户数
件单价：商品明细金额 / 商品件数
```

### 7.4 退款率

```text
默认退款率：退款订单数 / 订单数
商品退款率：退款件数 / 销售件数
品类退款率：退款件数 / 销售件数
```

如果用户说法不明确，后续可以让 mainAgent 或 askAgent 追问：

```text
你想看订单维度退款率，还是商品件数维度退款率？
```

第一版可以先默认使用订单退款率。

## 8. 指标清单总览

| 指标名 | 类型 | 推荐字段/公式 | 默认事实表 | 常见别名 |
| --- | --- | --- | --- | --- |
| GMV | 基础金额 | `SUM(order_amount)` | fact_order | 销售额、成交额、订单金额 |
| 实付金额 | 基础金额 | `SUM(pay_amount)` | fact_order | 支付金额、实收金额 |
| 订单数 | 基础计数 | `COUNT(DISTINCT order_id)` | fact_order | 订单量、下单数 |
| 销量 | 基础数量 | `SUM(quantity)` | fact_order_item | 销售件数、售出数量 |
| 支付用户数 | 基础计数 | `COUNT(DISTINCT member_id)` | fact_order | 购买用户数、付费用户数 |
| 客单价 | 派生指标 | `GMV / 订单数` | fact_order | AOV、平均订单金额 |
| 人均消费 | 派生指标 | `GMV / 支付用户数` | fact_order | ARPPU、客均消费 |
| 件单价 | 派生指标 | `明细金额 / 销量` | fact_order_item | 单件均价 |
| 退款金额 | 退款金额 | `SUM(refund_amount)` | fact_refund | 售后金额、退款总额 |
| 退款订单数 | 退款计数 | `COUNT(DISTINCT refund.order_id)` | fact_refund | 售后订单数 |
| 退款件数 | 退款数量 | `SUM(refund_quantity)` | fact_refund | 退货件数 |
| 订单退款率 | 派生指标 | `退款订单数 / 订单数` | fact_order + fact_refund | 退款率、售后率 |
| 件数退款率 | 派生指标 | `退款件数 / 销量` | fact_order_item + fact_refund | 商品退款率、品类退款率 |

## 9. 对 meta_config_v2 的影响

后续写 `conf/meta_config_v2.yaml` 时，要把指标定义写清楚。

例如 GMV：

```yaml
- name: GMV
  description: 有效订单的原始成交金额总和，默认排除已取消订单，使用 fact_order.order_amount 求和。
  alias: [销售额, 成交额, 成交总额, 订单金额, 销售金额]
  default_variant: order_gmv
  variants:
    - name: order_gmv
      grain: order
      base_table: fact_order
      formula: "SUM(fact_order.order_amount)"
      relevant_columns: [fact_order.order_amount, fact_order.order_status]
    - name: item_gmv
      grain: order_item
      base_table: fact_order_item
      formula: "SUM(fact_order_item.item_amount)"
      relevant_columns: [fact_order_item.item_amount, fact_order_item.order_id]
```

例如销量：

```yaml
- name: 销量
  description: 有效订单中售出的商品件数，使用 fact_order_item.quantity 求和，需要关联 fact_order 并排除已取消订单。
  alias: [销售件数, 售出数量, 商品销量, 购买件数]
  default_variant: item_quantity
  variants:
    - name: item_quantity
      grain: order_item
      base_table: fact_order_item
      formula: "SUM(fact_order_item.quantity)"
      relevant_columns:
        - fact_order_item.quantity
        - fact_order_item.order_id
        - fact_order.order_id
        - fact_order.order_status
```

指标描述越清楚，模型越不容易混用字段。

## 10. 对评测集的影响

评测集应该覆盖这些混淆点：

```text
销售额 vs 实付金额
订单数 vs 销量
客单价 vs 人均消费
订单退款率 vs 品类退款率
下单日期 vs 退款日期
```

示例评测问题：

```text
统计 2025 年第一季度 GMV
统计 2025 年第一季度实付金额
统计 2025 年第一季度订单数和销量
按会员等级统计客单价和人均消费
查询退款率最高的前 5 个品类
统计 2025 年 3 月发生的退款金额
```

这些问题可以检查 askAgent 是否真的理解指标口径。

## 11. 下一步

指标口径定下来后，下一步是设计造数规则。

造数规则要服务于指标和分析，例如：

```text
华东、华南 GMV 更高
直播渠道销量高但退款率也高
铂金会员客单价更高
服饰类退款率高于食品类
618 和双11有销售峰值
```

有了业务规律，后续 data_analysis 才能基于查询结果生成有意义的分析。
