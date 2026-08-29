"""Real-data-verified business questions used by semantic and integration tests."""

SIMPLE_CASES = [
    {
        "question": "统计2025年GMV",
        "metrics": ["GMV"],
        "fact_tables": ["fact_order"],
        "grain": "order",
        "dimensions": [],
        "filters": ["dim_date.year=2025", "fact_order.order_status<>cancelled"],
        "non_empty": True,
        "key_sql": ["SUM", "fact_order.order_amount"],
        "why": "订单粒度默认GMV口径",
    },
    {
        "question": "统计2025年订单数",
        "metrics": ["订单数"],
        "fact_tables": ["fact_order"],
        "grain": "order",
        "dimensions": [],
        "filters": ["dim_date.year=2025", "fact_order.order_status<>cancelled"],
        "non_empty": True,
        "key_sql": ["COUNT", "order_id"],
        "why": "验证订单去重计数",
    },
    {
        "question": "统计2025年销量",
        "metrics": ["销量"],
        "fact_tables": ["fact_order_item"],
        "grain": "order_item",
        "dimensions": [],
        "filters": ["dim_date.year=2025", "fact_order.order_status<>cancelled"],
        "non_empty": True,
        "key_sql": ["SUM", "quantity"],
        "why": "验证明细粒度销量",
    },
    {
        "question": "统计2025年客单价",
        "metrics": ["客单价"],
        "fact_tables": ["fact_order"],
        "grain": "order",
        "dimensions": [],
        "filters": ["dim_date.year=2025", "fact_order.order_status<>cancelled"],
        "non_empty": True,
        "key_sql": ["SUM", "COUNT"],
        "why": "验证金额除以订单数而非AVG明细",
    },
    {
        "question": "统计2025年退款金额",
        "metrics": ["退款金额"],
        "fact_tables": ["fact_refund"],
        "grain": "refund",
        "dimensions": [],
        "filters": ["dim_date.year=2025", "fact_refund.refund_status=success"],
        "non_empty": True,
        "key_sql": ["SUM", "refund_amount"],
        "why": "验证退款日期和成功状态口径",
    },
]

MULTI_CONDITION_CASES = [
    {
        "question": "统计2025年第一季度华东大区App渠道的GMV",
        "metrics": ["GMV"],
        "fact_tables": ["fact_order"],
        "grain": "order",
        "dimensions": [],
        "filters": ["year=2025", "quarter=Q1", "region_name=华东", "channel_name=App"],
        "non_empty": True,
        "key_sql": ["dim_date", "dim_region", "dim_channel"],
        "why": "真实时间、地区、渠道组合",
    },
    {
        "question": "统计2025年女装品类的GMV和销量",
        "metrics": ["GMV", "销量"],
        "fact_tables": ["fact_order_item"],
        "grain": "order_item",
        "dimensions": ["dim_product.category_l2"],
        "filters": ["year=2025", "category_l2=女装"],
        "non_empty": True,
        "key_sql": ["item_amount", "quantity", "category_l2"],
        "why": "真实女装品类与多指标",
    },
    {
        "question": "统计2025年直播渠道各大区订单数",
        "metrics": ["订单数"],
        "fact_tables": ["fact_order"],
        "grain": "region",
        "dimensions": ["dim_region.region_name"],
        "filters": ["year=2025", "channel_name=直播"],
        "non_empty": True,
        "key_sql": ["COUNT", "GROUP BY", "region_name"],
        "why": "真实内容渠道与地区维度",
    },
    {
        "question": "统计2025年第二季度上海市的实付金额",
        "metrics": ["实付金额"],
        "fact_tables": ["fact_order"],
        "grain": "order",
        "dimensions": [],
        "filters": ["year=2025", "quarter=Q2", "province=上海市"],
        "non_empty": True,
        "key_sql": ["pay_amount", "province"],
        "why": "真实省份值与实付口径",
    },
    {
        "question": "按一级品类统计2025年退款金额和退款件数",
        "metrics": ["退款金额", "退款件数"],
        "fact_tables": ["fact_refund", "fact_order_item"],
        "grain": "product",
        "dimensions": ["dim_product.category_l1"],
        "filters": ["year=2025", "refund_status=success"],
        "non_empty": True,
        "key_sql": ["refund_amount", "refund_quantity", "category_l1"],
        "why": "退款事实到商品维度的安全Join",
    },
]

MULTITURN_CASES = [
    ["统计2025年GMV", "改成直播渠道"],
    ["统计2025年GMV", "加上订单数"],
    ["统计2025年华东大区GMV", "去掉地区条件"],
    ["统计2025年GMV", "统计2025年女装销量，这是独立问题"],
    ["统计2025年GMV", "那这个指标呢", "我指订单数，替换GMV"],
]

VARIANT_CASES = [
    ("统计2025年各大区GMV", "GMV", "fact_order", "order"),
    ("统计2025年各品类GMV", "GMV", "fact_order_item", "order_item"),
    ("统计2025年女装退款率", "件数退款率", "fact_refund", "order_item"),
]

JOIN_RISK_CASES = [
    {
        "question": "统计订单GMV并关联商品明细",
        "risk": "订单金额在一对多明细Join后重复求和",
        "boundary": "先按order_id聚合或改用明细GMV口径",
    },
    {
        "question": "统计按品类订单数",
        "risk": "一个订单含多商品导致COUNT(*)放大",
        "boundary": "使用COUNT(DISTINCT fact_order.order_id)",
    },
    {
        "question": "同时统计退款金额和销量",
        "risk": "退款与订单明细双事实直接连接导致分子或分母放大",
        "boundary": "分别预聚合到共同粒度后再连接",
    },
]

AMBIGUITY_CASES = [
    ("女装", {"dim_product.category_l2", "dim_product.product_name"}),
    ("手机", {"dim_product.category_l2", "dim_product.product_name"}),
    ("耳机", {"dim_product.category_l2", "dim_product.product_name"}),
]

FAILURE_CASES = [
    ("不存在的指标", "no_metric_candidate"),
    ("不存在的表", "no_table_candidate"),
    ("未来无数据区间", "empty_rows_success"),
    ("聚合全为NULL", "null_aggregate"),
    ("非法pending状态", "invalid_pending"),
]
