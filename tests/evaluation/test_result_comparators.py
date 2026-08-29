from scripts.v2.accuracy_eval_lib import compare_results


def test_scalar_accepts_decimal_string_and_alias():
    result = compare_results(
        [{"gmv": "12.30"}],
        [{"销售额": 12.3}],
        {"mode": "scalar", "columns": ["gmv"], "tolerance": "0.01"},
    )
    assert result["passed"] is True


def test_unordered_uses_dimension_key_not_row_order():
    result = compare_results(
        [
            {"region_name": "华东", "order_count": 2},
            {"region_name": "华南", "order_count": 1},
        ],
        [
            {"订单数": 1, "大区": "华南"},
            {"订单数": 2, "大区": "华东"},
        ],
        {
            "mode": "unordered",
            "columns": ["region_name", "order_count"],
            "key_columns": ["region_name"],
        },
    )
    assert result["passed"] is True


def test_ordered_rejects_wrong_topn_order():
    result = compare_results(
        [{"brand": "A", "gmv": 2}, {"brand": "B", "gmv": 1}],
        [{"brand": "B", "gmv": 1}, {"brand": "A", "gmv": 2}],
        {"mode": "ordered", "columns": ["brand", "gmv"]},
    )
    assert result["passed"] is False


def test_empty_requires_zero_rows():
    assert compare_results([], [], {"mode": "empty", "columns": []})["passed"]


def test_null_is_not_equal_to_zero():
    result = compare_results(
        [{"gmv": None}],
        [{"gmv": 0}],
        {"mode": "scalar", "columns": ["gmv"]},
    )
    assert result["passed"] is False


def test_successful_refund_amount_is_a_metric_alias():
    result = compare_results(
        [{"category_l2": "女装", "refund_amount": "10.00"}],
        [{"二级品类": "女装", "成功退款金额": 10}],
        {
            "mode": "unordered",
            "columns": ["category_l2", "refund_amount"],
            "key_columns": ["category_l2"],
        },
    )
    assert result["passed"] is True
