# v2 电商数仓表结构设计稿

这份文档是 v2 落地的第二步：把教学文档里的业务理解收敛成具体表结构。

当前目标不是追求企业级大而全，而是设计一套适合 askAgent 问数、可造数据、可评测、可面试讲清楚的小型电商数仓。

## 1. 设计边界

v2 数据库建议命名为：

```text
dw_v2
```

第一版只设计 8 张表：

```text
dim_date
dim_region
dim_product
dim_member
dim_channel
fact_order
fact_order_item
fact_refund
```

暂时不做：

```text
库存表
物流表
优惠券表
曝光点击表
搜索行为表
客服工单表
```

原因是当前主目标是做深问数链路，不是复刻完整电商平台。

## 2. 核心建模思想

v2 使用简单星型/雪花模型：

```text
维表负责描述业务对象。
事实表负责记录业务事件。
```

维表：

```text
dim_date      描述时间
dim_region    描述地区
dim_product   描述商品
dim_member    描述会员
dim_channel   描述渠道
```

事实表：

```text
fact_order        一行一笔订单
fact_order_item   一行一个订单商品明细
fact_refund       一行一次退款
```

最重要的是事实表粒度：

| 表名 | 粒度 | 主要回答的问题 |
| --- | --- | --- |
| fact_order | 一行一笔订单 | GMV、订单数、客单价、支付用户数 |
| fact_order_item | 一行一个订单里的商品明细 | 销量、商品销售额、品类销售额、Top 商品 |
| fact_refund | 一行一次退款 | 退款金额、退款订单数、退款率、退款原因 |

如果你能把粒度讲清楚，后续问数就不会混淆“订单数”“销量”“GMV”。

## 3. 表关系总览

```text
dim_date.date_id        -> fact_order.date_id
dim_region.region_id    -> fact_order.region_id
dim_member.member_id    -> fact_order.member_id
dim_channel.channel_id  -> fact_order.channel_id

fact_order.order_id     -> fact_order_item.order_id
dim_product.product_id  -> fact_order_item.product_id

fact_order.order_id     -> fact_refund.order_id
fact_order_item.item_id -> fact_refund.item_id
dim_date.date_id        -> fact_refund.date_id
```

注意：`fact_refund.date_id` 表示退款发生日期，不是下单日期。

如果用户问：

```text
2025 年退款金额
```

通常按退款日期过滤。

如果用户问：

```text
2025 年下单订单的退款率
```

则要按订单日期过滤，再关联退款表。

这类差异后面会进入指标口径设计。

## 4. 字段命名约定

为了让大模型更容易理解，也为了让 SQL 更稳定，字段命名尽量直白：

```text
主键：xxx_id
外键：保持和维表主键同名
金额：xxx_amount
数量：xxx_count 或 quantity
状态：xxx_status
类型：xxx_type
名称：xxx_name
```

金额统一使用：

```text
DECIMAL(12, 2)
```

ID 第一版可以使用：

```text
VARCHAR(32)
```

这样造数据更直观，比如：

```text
O202501010001
I20250101000101
R20250101000101
```

日期主键使用：

```text
INT
```

格式为：

```text
yyyyMMdd
```

例如：

```text
20250101
```

## 5. dim_date 日期维表

### 5.1 业务作用

日期维表用于支持这些表达：

```text
去年
今年
第一季度
2025 年 3 月
按月统计
周末
618
双 11
春节前
```

### 5.2 字段设计

| 字段名 | 类型 | 角色 | 说明 |
| --- | --- | --- | --- |
| date_id | INT | 主键 | 日期 ID，格式 yyyyMMdd |
| date_value | DATE | 维度 | 真实日期 |
| year | INT | 维度 | 年份 |
| quarter | VARCHAR(2) | 维度 | 季度，如 Q1、Q2 |
| month | INT | 维度 | 月份 |
| day | INT | 维度 | 日期中的日 |
| week_of_year | INT | 维度 | 一年中的第几周 |
| weekday | INT | 维度 | 星期几，1-7 |
| weekday_name | VARCHAR(10) | 维度 | 星期一、星期二等 |
| is_weekend | TINYINT | 维度 | 是否周末，1 是，0 否 |
| festival_name | VARCHAR(50) | 维度 | 节日或活动名，如 618、双11、春节 |

### 5.3 为什么需要这些字段

`year`、`quarter`、`month` 用于稳定生成时间过滤 SQL。

`is_weekend` 和 `festival_name` 用于后续分析：

```text
周末 GMV 是否更高？
618 期间销售额是否上涨？
双 11 哪些品类增长明显？
```

## 6. dim_region 地区维表

### 6.1 业务作用

地区维表用于支持：

```text
按大区统计
按省份统计
按城市统计
按城市等级统计
```

### 6.2 字段设计

| 字段名 | 类型 | 角色 | 说明 |
| --- | --- | --- | --- |
| region_id | VARCHAR(32) | 主键 | 地区 ID |
| region_name | VARCHAR(50) | 维度 | 大区名称，如华东、华南 |
| province | VARCHAR(50) | 维度 | 省份 |
| city | VARCHAR(50) | 维度 | 城市 |
| city_level | VARCHAR(20) | 维度 | 城市等级，如一线、新一线、二线 |
| country | VARCHAR(20) | 维度 | 国家，默认中国 |

### 6.3 为什么这样设计

用户说“地区”时可能指不同粒度：

```text
华东
广东省
广州
一线城市
```

所以地区维表不能只有 `region_name`，还要有省份、城市、城市等级。

## 7. dim_product 商品维表

### 7.1 业务作用

商品维表用于支持：

```text
商品销量
品类 GMV
品牌销售额
价格带分析
退款率按品类分析
```

### 7.2 字段设计

| 字段名 | 类型 | 角色 | 说明 |
| --- | --- | --- | --- |
| product_id | VARCHAR(32) | 主键 | 商品 ID |
| product_name | VARCHAR(200) | 维度 | 商品名称 |
| category_l1 | VARCHAR(50) | 维度 | 一级品类，如数码、服饰 |
| category_l2 | VARCHAR(50) | 维度 | 二级品类，如手机、女装 |
| brand | VARCHAR(50) | 维度 | 品牌 |
| price_band | VARCHAR(20) | 维度 | 价格带，如低价、中价、高价 |
| list_price | DECIMAL(12,2) | 描述字段 | 商品标价 |

### 7.3 为什么商品不直接放在 fact_order

因为一笔订单可能包含多个商品。

商品粒度属于：

```text
fact_order_item
```

而不是：

```text
fact_order
```

所以商品维表应该通过 `fact_order_item.product_id` 关联。

## 8. dim_member 会员维表

### 8.1 业务作用

会员维表用于支持：

```text
会员等级分析
性别分析
年龄段分析
支付用户数
人均消费
```

### 8.2 字段设计

| 字段名 | 类型 | 角色 | 说明 |
| --- | --- | --- | --- |
| member_id | VARCHAR(32) | 主键 | 会员 ID |
| member_level | VARCHAR(20) | 维度 | 会员等级，如普通、银卡、金卡、铂金 |
| gender | VARCHAR(10) | 维度 | 性别 |
| age_band | VARCHAR(20) | 维度 | 年龄段，如 18-24、25-34 |
| register_date_id | INT | 维度/外键 | 注册日期，可关联 dim_date |
| member_stage | VARCHAR(20) | 维度 | 用户阶段，如新客、老客、高价值 |

### 8.3 为什么不用 customer

v1 里叫 `dim_customer`，v2 建议叫 `dim_member`。

原因是电商场景里更常用“会员”“用户”：

```text
会员等级
支付用户数
新客老客
人均消费
```

`member` 比 `customer` 更贴近电商运营分析。

### 8.4 关于新客老客

严格来说，新客老客应该结合订单日期动态判断。

比如：

```text
某用户 2025-01-01 注册，2025-01-10 下单是新客，2026-01-10 下单就是老客。
```

第一版为了问数演示，可以先用 `member_stage` 做模拟标签。

后续如果要更严谨，可以用：

```text
register_date_id
first_order_date_id
```

动态计算新老客。

## 9. dim_channel 渠道维表

### 9.1 业务作用

渠道维表用于支持：

```text
各渠道 GMV
各渠道订单数
各渠道客单价
各渠道退款率
付费渠道和自然渠道对比
```

### 9.2 字段设计

| 字段名 | 类型 | 角色 | 说明 |
| --- | --- | --- | --- |
| channel_id | VARCHAR(32) | 主键 | 渠道 ID |
| channel_name | VARCHAR(50) | 维度 | 渠道名称，如 App、小程序、直播 |
| channel_type | VARCHAR(50) | 维度 | 渠道类型，如自有渠道、付费渠道、内容渠道 |
| is_paid | TINYINT | 维度 | 是否付费渠道，1 是，0 否 |

### 9.3 为什么需要渠道

渠道是电商分析里很常见的维度。

比如：

```text
直播渠道销量高但退款率也高
自然流量 GMV 稳定且退款率低
搜索广告订单多但客单价低
```

这些规律能让后续 data_analysis 更有内容。

## 10. fact_order 订单事实表

### 10.1 业务粒度

`fact_order` 的粒度是：

```text
一行一笔订单
```

它不描述订单里具体买了哪些商品，只描述订单整体。

### 10.2 适合回答的问题

```text
GMV 是多少？
订单数是多少？
客单价是多少？
支付用户数是多少？
各地区 GMV 是多少？
各渠道订单数是多少？
不同会员等级客单价是多少？
```

### 10.3 字段设计

| 字段名 | 类型 | 角色 | 说明 |
| --- | --- | --- | --- |
| order_id | VARCHAR(32) | 主键 | 订单 ID |
| date_id | INT | 外键 | 下单日期，关联 dim_date |
| member_id | VARCHAR(32) | 外键 | 下单会员，关联 dim_member |
| region_id | VARCHAR(32) | 外键 | 下单地区，关联 dim_region |
| channel_id | VARCHAR(32) | 外键 | 下单渠道，关联 dim_channel |
| order_status | VARCHAR(20) | 维度 | 订单状态，如 paid、completed、cancelled、refunded |
| order_amount | DECIMAL(12,2) | 度量 | 订单原始成交金额，可作为 GMV 基础 |
| discount_amount | DECIMAL(12,2) | 度量 | 优惠金额 |
| pay_amount | DECIMAL(12,2) | 度量 | 用户实际支付金额 |
| shipping_amount | DECIMAL(12,2) | 度量 | 运费金额 |

### 10.4 字段解释

`order_amount` 更适合对应：

```text
GMV
成交额
销售额
```

`pay_amount` 更适合对应：

```text
实付金额
支付金额
用户实际支付
```

`discount_amount` 可以支持：

```text
优惠金额
折扣金额
```

第一版指标可以先不做优惠分析，但字段可以保留。

## 11. fact_order_item 订单明细事实表

### 11.1 业务粒度

`fact_order_item` 的粒度是：

```text
一行代表一笔订单中的一个商品明细
```

例如：

```text
订单 O001 买了商品 A 和商品 B
```

则：

```text
fact_order 有 1 行
fact_order_item 有 2 行
```

### 11.2 适合回答的问题

```text
商品销量最高的是哪个？
各品类销售额是多少？
各品牌销量是多少？
高价商品 GMV 是多少？
各商品售出件数是多少？
```

### 11.3 字段设计

| 字段名 | 类型 | 角色 | 说明 |
| --- | --- | --- | --- |
| item_id | VARCHAR(32) | 主键 | 订单明细 ID |
| order_id | VARCHAR(32) | 外键 | 所属订单，关联 fact_order |
| product_id | VARCHAR(32) | 外键 | 商品，关联 dim_product |
| quantity | INT | 度量 | 购买件数 |
| item_amount | DECIMAL(12,2) | 度量 | 明细原始金额 |
| discount_amount | DECIMAL(12,2) | 度量 | 明细优惠金额 |
| pay_amount | DECIMAL(12,2) | 度量 | 明细实际支付金额 |
| cost_amount | DECIMAL(12,2) | 度量 | 明细成本金额 |

### 11.4 为什么销量要从这里算

销量不是订单数。

```text
订单数 = COUNT(DISTINCT fact_order.order_id)
销量 = SUM(fact_order_item.quantity)
```

如果用户问：

```text
去年第一季度各品类销量
```

应该使用：

```text
fact_order_item.quantity
```

再关联：

```text
dim_product.category_l1
fact_order.date_id
dim_date
```

## 12. fact_refund 退款事实表

### 12.1 业务粒度

`fact_refund` 的粒度是：

```text
一行一次退款事件
```

第一版建议按商品明细退款，也就是每条退款记录关联到 `item_id`。

这样更容易回答：

```text
哪个商品退款率高？
哪个品类退款金额高？
```

### 12.2 适合回答的问题

```text
退款金额是多少？
退款订单数是多少？
退款率是多少？
退款率最高的品类是什么？
哪个渠道退款率最高？
常见退款原因有哪些？
```

### 12.3 字段设计

| 字段名 | 类型 | 角色 | 说明 |
| --- | --- | --- | --- |
| refund_id | VARCHAR(32) | 主键 | 退款 ID |
| order_id | VARCHAR(32) | 外键 | 关联订单 |
| item_id | VARCHAR(32) | 外键 | 关联订单明细 |
| date_id | INT | 外键 | 退款日期，关联 dim_date |
| refund_amount | DECIMAL(12,2) | 度量 | 退款金额 |
| refund_quantity | INT | 度量 | 退款件数 |
| refund_reason | VARCHAR(100) | 维度 | 退款原因 |
| refund_status | VARCHAR(20) | 维度 | 退款状态，如 success、processing、rejected |

### 12.4 退款日期和订单日期的区别

`fact_refund.date_id` 是退款发生日期。

`fact_order.date_id` 是下单日期。

这两个字段会影响问题解释。

例如：

```text
统计 2025 年 3 月退款金额
```

通常按退款日期：

```text
fact_refund.date_id -> dim_date
```

但是：

```text
统计 2025 年 3 月订单的退款率
```

通常按订单日期：

```text
fact_order.date_id -> dim_date
```

再关联退款表。

## 13. 常见问题对应表路径

### 13.1 GMV

问题：

```text
统计去年第一季度 GMV
```

表路径：

```text
fact_order -> dim_date
```

核心字段：

```text
fact_order.order_amount
dim_date.year
dim_date.quarter
```

### 13.2 各地区 GMV

问题：

```text
统计去年第一季度各地区 GMV
```

表路径：

```text
fact_order -> dim_date
fact_order -> dim_region
```

核心字段：

```text
dim_region.region_name
fact_order.order_amount
```

### 13.3 各品类销量

问题：

```text
统计 2025 年各品类销量
```

表路径：

```text
fact_order -> dim_date
fact_order -> fact_order_item
fact_order_item -> dim_product
```

核心字段：

```text
dim_product.category_l1
fact_order_item.quantity
```

### 13.4 各渠道客单价

问题：

```text
统计各渠道客单价
```

表路径：

```text
fact_order -> dim_channel
```

核心字段：

```text
SUM(fact_order.order_amount) / COUNT(DISTINCT fact_order.order_id)
```

### 13.5 各会员等级人均消费

问题：

```text
统计不同会员等级人均消费
```

表路径：

```text
fact_order -> dim_member
```

核心字段：

```text
SUM(fact_order.order_amount) / COUNT(DISTINCT fact_order.member_id)
```

### 13.6 各品类退款率

问题：

```text
查询退款率最高的品类
```

表路径：

```text
fact_order_item -> dim_product
fact_refund -> fact_order_item
```

核心字段：

```text
退款件数 = SUM(fact_refund.refund_quantity)
销售件数 = SUM(fact_order_item.quantity)
退款率 = 退款件数 / 销售件数
```

这里要注意：退款率的分子和分母要在同一粒度上比较。

如果按商品品类分析，推荐用：

```text
退款件数 / 销售件数
```

如果按订单分析，可以用：

```text
退款订单数 / 订单数
```

这部分会在指标口径文档里进一步明确。

## 14. 建议索引

为了让查询和 EXPLAIN 更稳定，v2 建表时建议加这些索引：

```text
fact_order(date_id)
fact_order(region_id)
fact_order(member_id)
fact_order(channel_id)
fact_order(order_status)

fact_order_item(order_id)
fact_order_item(product_id)

fact_refund(order_id)
fact_refund(item_id)
fact_refund(date_id)
fact_refund(refund_status)
```

维表主键天然有索引。

第一版数据量不会很大，索引不是性能瓶颈，但它能让设计更像真实工程。

## 15. 设计取舍说明

### 15.1 为什么不把商品字段放进 fact_order

因为订单和商品不是同一粒度。

一笔订单可能有多个商品。

如果把商品字段放进 `fact_order`，就会导致：

```text
订单金额重复
商品销量不准确
品类 GMV 容易算错
退款无法对应具体商品
```

### 15.2 为什么 fact_refund 同时有 order_id 和 item_id

`item_id` 可以定位具体退款商品。

`order_id` 是冗余字段，但非常方便：

```text
按渠道分析退款
按会员等级分析退款
按地区分析退款
```

这些都需要从退款回到订单。

有 `order_id` 可以少走一步，也更容易让大模型生成 SQL。

### 15.3 为什么保留 pay_amount

GMV 和实付金额不是一回事。

```text
GMV：订单原始成交金额，通常不扣优惠
实付金额：用户最终支付金额，扣除优惠
```

面试时你可以讲：

```text
我在表结构中保留 order_amount 和 pay_amount，是为了区分成交口径和支付口径，避免把 GMV 和实付收入混在一起。
```

### 15.4 为什么第一版不用太多状态

真实电商订单状态很多：

```text
待支付、已支付、已发货、已完成、已取消、已退款、部分退款
```

v2 第一版只需要：

```text
paid
completed
cancelled
refunded
partial_refunded
```

状态太多会增加造数和指标口径复杂度，不利于第一版稳定。

## 16. 下一步

这份表结构定下来后，下一步不是马上造数据，而是先定义指标口径。

需要明确：

```text
GMV 用哪个字段
订单数是否排除 cancelled
销量用哪个字段
客单价怎么算
退款率按订单算还是按件数算
支付用户数怎么算
哪些状态纳入统计
```

指标口径定清楚，后面才能写：

```text
conf/meta_config_v2.yaml
docs/v2_question_eval_set.md
scripts/v2/seed_dw_v2_data.py
```

否则表有了，问数仍然会混乱。
