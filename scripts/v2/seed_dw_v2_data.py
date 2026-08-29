"""Generate deterministic v2 e-commerce warehouse demo data.

This script builds data for the standalone dw_v2 database. It intentionally
does not use LLMs: synthetic business data must be repeatable, relationally
consistent, and easy to validate.
"""

import argparse
import asyncio
import random
import sys
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.conf.app_config import app_config


DEFAULT_SEED = 20260702
DEFAULT_DATABASE = "dw_v2"
DEFAULT_ORDERS = 30000
DEFAULT_MEMBERS = 8000
DEFAULT_PRODUCTS = 300
BATCH_SIZE = 2000


@dataclass(frozen=True)
class ProductProfile:
    product_id: str
    product_name: str
    category_l1: str
    category_l2: str
    brand: str
    price_band: str
    list_price: Decimal
    cost_rate_min: float
    cost_rate_max: float


@dataclass(frozen=True)
class RegionProfile:
    region_id: str
    region_name: str
    province: str
    city: str
    city_level: str


@dataclass(frozen=True)
class ChannelProfile:
    channel_id: str
    channel_name: str
    channel_type: str
    is_paid: int


@dataclass(frozen=True)
class MemberProfile:
    member_id: str
    member_level: str
    gender: str
    age_band: str
    register_date_id: int
    member_stage: str


@dataclass(frozen=True)
class DateProfile:
    date_id: int
    date_value: date
    year: int
    quarter: str
    month: int
    day: int
    week_of_year: int
    weekday: int
    weekday_name: str
    is_weekend: int
    festival_name: str | None


def money(value: float | Decimal) -> Decimal:
    """Round a numeric value to money scale."""

    return Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def date_id(value: date) -> int:
    return value.year * 10000 + value.month * 100 + value.day


def weighted_choice[T](items: list[T], weights: list[float]) -> T:
    return random.choices(items, weights=weights, k=1)[0]


def build_async_url(
    database: str,
    user: str | None = None,
    password: str | None = None,
) -> str:
    db = app_config.db_dw
    user = user or db.user
    password = password or db.password
    return (
        f"mysql+asyncmy://{user}:{password}@{db.host}:{db.port}/"
        f"{database}?charset=utf8mb4"
    )


def get_project_root() -> Path:
    return PROJECT_ROOT


async def execute_sql_file(engine: AsyncEngine, sql_path: Path) -> None:
    """Execute a simple semicolon-separated SQL file."""

    sql_text = sql_path.read_text(encoding="utf-8")
    statements = [
        statement.strip()
        for statement in sql_text.split(";")
        if statement.strip()
    ]
    async with engine.begin() as conn:
        for statement in statements:
            await conn.execute(text(statement))


async def truncate_tables(engine: AsyncEngine) -> None:
    tables = [
        "fact_refund",
        "fact_order_item",
        "fact_order",
        "dim_channel",
        "dim_member",
        "dim_product",
        "dim_region",
        "dim_date",
    ]
    async with engine.begin() as conn:
        await conn.execute(text("SET FOREIGN_KEY_CHECKS = 0"))
        for table in tables:
            await conn.execute(text(f"TRUNCATE TABLE {table}"))
        await conn.execute(text("SET FOREIGN_KEY_CHECKS = 1"))


async def batch_insert(
    engine: AsyncEngine,
    table: str,
    rows: list[dict[str, Any]],
    batch_size: int = BATCH_SIZE,
) -> None:
    if not rows:
        return

    columns = list(rows[0].keys())
    column_sql = ", ".join(columns)
    value_sql = ", ".join(f":{column}" for column in columns)
    insert_sql = text(f"INSERT INTO {table} ({column_sql}) VALUES ({value_sql})")

    async with engine.begin() as conn:
        for start in range(0, len(rows), batch_size):
            await conn.execute(insert_sql, rows[start : start + batch_size])


def get_festival(value: date) -> str | None:
    month_day = (value.month, value.day)
    if value.year in {2024, 2025, 2026}:
        if (value.month == 1 and value.day >= 20) or (
            value.month == 2 and value.day <= 8
        ):
            return "春节"
        if value.month == 6 and 1 <= value.day <= 18:
            return "618"
        if value.month == 11 and 1 <= value.day <= 11:
            return "双11"
        if month_day == (12, 12):
            return "双12"
    return None


def generate_dates() -> list[DateProfile]:
    start = date(2024, 1, 1)
    end = date(2026, 6, 30)
    weekday_names = {
        1: "星期一",
        2: "星期二",
        3: "星期三",
        4: "星期四",
        5: "星期五",
        6: "星期六",
        7: "星期日",
    }

    rows: list[DateProfile] = []
    current = start
    while current <= end:
        weekday = current.isoweekday()
        quarter = f"Q{((current.month - 1) // 3) + 1}"
        rows.append(
            DateProfile(
                date_id=date_id(current),
                date_value=current,
                year=current.year,
                quarter=quarter,
                month=current.month,
                day=current.day,
                week_of_year=current.isocalendar().week,
                weekday=weekday,
                weekday_name=weekday_names[weekday],
                is_weekend=1 if weekday >= 6 else 0,
                festival_name=get_festival(current),
            )
        )
        current += timedelta(days=1)
    return rows


def generate_regions() -> list[RegionProfile]:
    region_rows = [
        ("华东", "上海市", "上海", "一线"),
        ("华东", "浙江省", "杭州", "新一线"),
        ("华东", "江苏省", "南京", "新一线"),
        ("华东", "江苏省", "苏州", "新一线"),
        ("华东", "福建省", "厦门", "二线"),
        ("华南", "广东省", "广州", "一线"),
        ("华南", "广东省", "深圳", "一线"),
        ("华南", "广东省", "佛山", "二线"),
        ("华南", "广西壮族自治区", "南宁", "二线"),
        ("华北", "北京市", "北京", "一线"),
        ("华北", "天津市", "天津", "新一线"),
        ("华北", "山东省", "青岛", "新一线"),
        ("华北", "河北省", "石家庄", "二线"),
        ("华中", "湖北省", "武汉", "新一线"),
        ("华中", "湖南省", "长沙", "新一线"),
        ("华中", "河南省", "郑州", "新一线"),
        ("西南", "四川省", "成都", "新一线"),
        ("西南", "重庆市", "重庆", "新一线"),
        ("西南", "云南省", "昆明", "二线"),
        ("西南", "贵州省", "贵阳", "二线"),
        ("西北", "陕西省", "西安", "新一线"),
        ("西北", "甘肃省", "兰州", "二线"),
        ("西北", "新疆维吾尔自治区", "乌鲁木齐", "三线"),
        ("东北", "辽宁省", "沈阳", "二线"),
        ("东北", "吉林省", "长春", "二线"),
        ("东北", "黑龙江省", "哈尔滨", "二线"),
    ]
    return [
        RegionProfile(
            region_id=f"R{index:03d}",
            region_name=region_name,
            province=province,
            city=city,
            city_level=city_level,
        )
        for index, (region_name, province, city, city_level) in enumerate(
            region_rows,
            start=1,
        )
    ]


def generate_channels() -> list[ChannelProfile]:
    channel_rows = [
        ("App", "自有渠道", 0),
        ("小程序", "自有渠道", 0),
        ("直播", "内容渠道", 0),
        ("搜索广告", "付费渠道", 1),
        ("信息流广告", "付费渠道", 1),
        ("自然流量", "自然渠道", 0),
        ("社群", "私域渠道", 0),
    ]
    return [
        ChannelProfile(
            channel_id=f"CH{index:03d}",
            channel_name=channel_name,
            channel_type=channel_type,
            is_paid=is_paid,
        )
        for index, (channel_name, channel_type, is_paid) in enumerate(
            channel_rows,
            start=1,
        )
    ]


CATEGORY_CONFIG = {
    "数码": {
        "category_l2": ["手机", "耳机", "平板", "智能手表"],
        "brands": ["华为", "小米", "苹果", "荣耀", "三星"],
        "price": (800, 8000),
        "bands": ["中价", "高价"],
        "cost_rate": (0.75, 0.9),
        "base_weight": 0.12,
    },
    "服饰": {
        "category_l2": ["女装", "男装", "鞋靴", "箱包"],
        "brands": ["优衣库", "李宁", "安踏", "森马", "蕉内"],
        "price": (59, 899),
        "bands": ["低价", "中价"],
        "cost_rate": (0.4, 0.6),
        "base_weight": 0.18,
    },
    "食品": {
        "category_l2": ["零食", "饮料", "粮油", "礼盒"],
        "brands": ["三只松鼠", "良品铺子", "蒙牛", "伊利", "金龙鱼"],
        "price": (10, 299),
        "bands": ["低价", "中价"],
        "cost_rate": (0.6, 0.75),
        "base_weight": 0.2,
    },
    "美妆": {
        "category_l2": ["护肤", "彩妆", "香水", "面膜"],
        "brands": ["珀莱雅", "欧莱雅", "完美日记", "薇诺娜", "雅诗兰黛"],
        "price": (49, 1299),
        "bands": ["低价", "中价", "高价"],
        "cost_rate": (0.35, 0.55),
        "base_weight": 0.14,
    },
    "家居": {
        "category_l2": ["收纳", "厨具", "清洁", "床品"],
        "brands": ["美的", "苏泊尔", "宜家", "蓝月亮", "罗莱"],
        "price": (29, 1599),
        "bands": ["低价", "中价"],
        "cost_rate": (0.5, 0.7),
        "base_weight": 0.11,
    },
    "运动": {
        "category_l2": ["运动鞋", "运动服", "健身器材", "户外装备"],
        "brands": ["耐克", "阿迪达斯", "安踏", "李宁", "迪卡侬"],
        "price": (79, 1999),
        "bands": ["低价", "中价", "高价"],
        "cost_rate": (0.45, 0.65),
        "base_weight": 0.1,
    },
    "母婴": {
        "category_l2": ["奶粉", "纸尿裤", "玩具", "洗护"],
        "brands": ["帮宝适", "贝亲", "飞鹤", "好孩子", "乐高"],
        "price": (39, 999),
        "bands": ["低价", "中价"],
        "cost_rate": (0.5, 0.68),
        "base_weight": 0.08,
    },
    "图书": {
        "category_l2": ["教辅", "文学", "经管", "童书"],
        "brands": ["中信出版", "人民文学", "机械工业", "外研社", "童趣"],
        "price": (15, 199),
        "bands": ["低价"],
        "cost_rate": (0.35, 0.55),
        "base_weight": 0.07,
    },
}


def price_band(price: float) -> str:
    if price < 100:
        return "低价"
    if price < 800:
        return "中价"
    return "高价"


def generate_products(product_count: int) -> list[ProductProfile]:
    categories = list(CATEGORY_CONFIG.keys())
    weights = [CATEGORY_CONFIG[category]["base_weight"] for category in categories]
    products: list[ProductProfile] = []

    for index in range(1, product_count + 1):
        if index <= len(categories):
            category_l1 = categories[index - 1]
        else:
            category_l1 = weighted_choice(categories, weights)
        config = CATEGORY_CONFIG[category_l1]
        category_l2 = random.choice(config["category_l2"])
        brand = random.choice(config["brands"])
        low, high = config["price"]
        price = money(random.uniform(low, high))
        band = price_band(float(price))
        products.append(
            ProductProfile(
                product_id=f"P{index:05d}",
                product_name=f"{brand}{category_l2}商品{index:04d}",
                category_l1=category_l1,
                category_l2=category_l2,
                brand=brand,
                price_band=band,
                list_price=price,
                cost_rate_min=config["cost_rate"][0],
                cost_rate_max=config["cost_rate"][1],
            )
        )

    return products


def generate_members(member_count: int, dates: list[DateProfile]) -> list[MemberProfile]:
    levels = ["普通", "银卡", "金卡", "铂金"]
    level_weights = [0.6, 0.23, 0.12, 0.05]
    genders = ["男", "女"]
    age_bands = ["18-24", "25-34", "35-44", "45+"]
    age_weights = [0.22, 0.42, 0.24, 0.12]
    stages = ["新客", "老客", "高价值", "沉默"]

    register_candidates = [d for d in dates if d.date_value <= date(2026, 5, 31)]
    members: list[MemberProfile] = []
    for index in range(1, member_count + 1):
        level = weighted_choice(levels, level_weights)
        if level == "铂金":
            stage = weighted_choice(stages, [0.05, 0.35, 0.5, 0.1])
        elif level == "金卡":
            stage = weighted_choice(stages, [0.08, 0.45, 0.3, 0.17])
        elif level == "银卡":
            stage = weighted_choice(stages, [0.15, 0.48, 0.12, 0.25])
        else:
            stage = weighted_choice(stages, [0.28, 0.38, 0.04, 0.3])

        members.append(
            MemberProfile(
                member_id=f"M{index:06d}",
                member_level=level,
                gender=random.choice(genders),
                age_band=weighted_choice(age_bands, age_weights),
                register_date_id=random.choice(register_candidates).date_id,
                member_stage=stage,
            )
        )
    return members


def date_weight(day: DateProfile) -> float:
    weight = 1.0
    if day.is_weekend:
        weight *= 1.15
    if day.festival_name == "618":
        weight *= 2.6
    elif day.festival_name == "双11":
        weight *= 4.2
    elif day.festival_name == "双12":
        weight *= 1.8
    elif day.festival_name == "春节":
        weight *= 1.35
    return weight


REGION_ORDER_WEIGHTS = {
    "华东": 1.35,
    "华南": 1.25,
    "华北": 1.10,
    "华中": 0.95,
    "西南": 0.90,
    "西北": 0.75,
    "东北": 0.70,
}

CITY_LEVEL_PRICE_WEIGHTS = {
    "一线": 1.25,
    "新一线": 1.15,
    "二线": 1.0,
    "三线": 0.85,
}

MEMBER_PRICE_WEIGHTS = {
    "普通": 0.85,
    "银卡": 1.0,
    "金卡": 1.2,
    "铂金": 1.45,
}

MEMBER_ORDER_WEIGHTS = {
    "普通": 1.0,
    "银卡": 1.2,
    "金卡": 1.45,
    "铂金": 1.8,
}

CHANNEL_ORDER_WEIGHTS = {
    "App": 1.2,
    "小程序": 1.0,
    "直播": 1.3,
    "搜索广告": 1.1,
    "信息流广告": 0.95,
    "自然流量": 1.0,
    "社群": 0.8,
}

CHANNEL_PRICE_WEIGHTS = {
    "App": 1.1,
    "小程序": 1.0,
    "直播": 0.9,
    "搜索广告": 0.85,
    "信息流广告": 0.9,
    "自然流量": 1.05,
    "社群": 1.25,
}


def choose_product_category(day: DateProfile, channel: ChannelProfile) -> str:
    categories = list(CATEGORY_CONFIG.keys())
    weights = [CATEGORY_CONFIG[category]["base_weight"] for category in categories]

    adjusted = []
    for category, weight in zip(categories, weights, strict=True):
        value = weight
        if channel.channel_name == "直播" and category in {"服饰", "美妆"}:
            value *= 1.55
        if channel.channel_name == "社群" and category in {"美妆", "母婴", "数码"}:
            value *= 1.25
        if channel.channel_name == "搜索广告" and category in {"食品", "家居"}:
            value *= 1.15
        if day.festival_name == "春节" and category in {"食品", "家居", "母婴"}:
            value *= 1.75
        if day.festival_name in {"618", "双11"}:
            value *= 1.25
            if category in {"数码", "服饰", "运动", "美妆"}:
                value *= 1.25
        adjusted.append(value)

    return weighted_choice(categories, adjusted)


def choose_product(
    products_by_category: dict[str, list[ProductProfile]],
    category_l1: str,
    member: MemberProfile,
    channel: ChannelProfile,
    region: RegionProfile,
) -> ProductProfile:
    candidates = products_by_category[category_l1]
    weights = []
    for product in candidates:
        weight = 1.0
        if product.price_band == "高价":
            if member.member_level == "铂金":
                weight *= 2.0
            elif member.member_level == "金卡":
                weight *= 1.5
            elif member.member_level == "普通":
                weight *= 0.55
            if channel.channel_name == "搜索广告":
                weight *= 0.75
            if channel.channel_name == "社群":
                weight *= 1.45
            if region.city_level == "一线":
                weight *= 1.35
        elif product.price_band == "低价":
            if member.member_level == "普通":
                weight *= 1.35
            if channel.channel_name in {"直播", "搜索广告"}:
                weight *= 1.25
        weights.append(weight)
    return weighted_choice(candidates, weights)


def item_quantity(category_l1: str) -> int:
    if category_l1 == "数码":
        return weighted_choice([1, 2], [0.92, 0.08])
    if category_l1 in {"食品", "母婴", "图书"}:
        return weighted_choice([1, 2, 3, 4, 5, 6], [0.25, 0.25, 0.2, 0.15, 0.1, 0.05])
    return weighted_choice([1, 2, 3], [0.68, 0.24, 0.08])


def order_item_count(member: MemberProfile) -> int:
    weights = [0.6, 0.25, 0.1, 0.04, 0.01]
    if member.member_level == "铂金":
        weights = [0.45, 0.3, 0.16, 0.07, 0.02]
    elif member.member_level == "金卡":
        weights = [0.52, 0.28, 0.13, 0.05, 0.02]
    return weighted_choice([1, 2, 3, 4, 5], weights)


def discount_rate(day: DateProfile, channel: ChannelProfile) -> float:
    if day.festival_name == "双11":
        low, high = 0.15, 0.35
    elif day.festival_name == "618":
        low, high = 0.10, 0.25
    elif day.festival_name == "双12":
        low, high = 0.08, 0.22
    else:
        low, high = 0.0, 0.10

    if channel.channel_name == "直播":
        high += 0.04
    elif channel.channel_name == "社群":
        high -= 0.02
    return max(0, min(0.4, random.uniform(low, high)))


def initial_order_status(day: DateProfile, channel: ChannelProfile) -> str:
    statuses = ["completed", "paid", "cancelled"]
    weights = [0.78, 0.16, 0.06]
    if day.festival_name in {"618", "双11"}:
        weights = [0.74, 0.16, 0.10]
    if channel.channel_name == "直播":
        weights = [weights[0] - 0.03, weights[1], weights[2] + 0.03]
    return weighted_choice(statuses, weights)


def refund_probability(
    product: ProductProfile,
    channel: ChannelProfile,
    day: DateProfile,
    order_status: str,
) -> float:
    base = {
        "服饰": 0.15,
        "美妆": 0.09,
        "数码": 0.07,
        "运动": 0.08,
        "家居": 0.06,
        "母婴": 0.045,
        "食品": 0.02,
        "图书": 0.015,
    }[product.category_l1]

    channel_adjust = {
        "直播": 0.06,
        "信息流广告": 0.03,
        "搜索广告": 0.015,
        "自然流量": -0.015,
        "社群": -0.015,
    }.get(channel.channel_name, 0)

    festival_adjust = 0
    if day.festival_name == "双11":
        festival_adjust = 0.035
    elif day.festival_name == "618":
        festival_adjust = 0.02

    if order_status == "cancelled":
        return 0
    return max(0.005, min(0.35, base + channel_adjust + festival_adjust))


def refund_status() -> str:
    return weighted_choice(["success", "processing", "rejected"], [0.86, 0.07, 0.07])


def refund_reason(product: ProductProfile, channel: ChannelProfile) -> str:
    reasons = ["七天无理由", "物流慢", "描述不符", "买错了", "价格波动"]
    weights = [0.28, 0.14, 0.16, 0.18, 0.08]
    if product.category_l1 == "服饰":
        reasons += ["尺码不合适", "质量问题"]
        weights += [0.32, 0.16]
    elif product.category_l1 in {"数码", "家居"}:
        reasons += ["质量问题"]
        weights += [0.24]
    elif channel.channel_name == "直播":
        reasons += ["描述不符"]
        weights += [0.18]
    return weighted_choice(reasons, weights)


def add_days_within_range(day: DateProfile, dates_by_id: dict[int, DateProfile]) -> int:
    new_date = day.date_value + timedelta(days=random.randint(1, 30))
    upper = date(2026, 6, 30)
    if new_date > upper:
        new_date = upper
    return date_id(new_date)


def generate_fact_data(
    order_count: int,
    dates: list[DateProfile],
    regions: list[RegionProfile],
    products: list[ProductProfile],
    members: list[MemberProfile],
    channels: list[ChannelProfile],
) -> tuple[
    list[dict[str, Any]],
    list[dict[str, Any]],
    list[dict[str, Any]],
]:
    products_by_category: dict[str, list[ProductProfile]] = defaultdict(list)
    products_by_id = {product.product_id: product for product in products}
    for product in products:
        products_by_category[product.category_l1].append(product)

    dates_by_id = {day.date_id: day for day in dates}
    date_weights = [date_weight(day) for day in dates]
    region_weights = [REGION_ORDER_WEIGHTS[region.region_name] for region in regions]
    member_weights = [MEMBER_ORDER_WEIGHTS[member.member_level] for member in members]
    channel_weights = [
        CHANNEL_ORDER_WEIGHTS[channel.channel_name] for channel in channels
    ]

    orders_by_id: dict[str, dict[str, Any]] = {}
    order_rows: list[dict[str, Any]] = []
    item_rows: list[dict[str, Any]] = []
    refund_rows: list[dict[str, Any]] = []

    successful_refund_amount_by_order: dict[str, Decimal] = defaultdict(
        lambda: Decimal("0.00")
    )

    for order_index in range(1, order_count + 1):
        order_id = f"O{order_index:08d}"
        day = weighted_choice(dates, date_weights)
        region = weighted_choice(regions, region_weights)
        member = weighted_choice(members, member_weights)
        channel = weighted_choice(channels, channel_weights)
        status = initial_order_status(day, channel)

        item_count = order_item_count(member)
        order_item_rows: list[dict[str, Any]] = []
        order_amount = Decimal("0.00")
        discount_amount = Decimal("0.00")
        item_pay_amount = Decimal("0.00")

        for item_number in range(1, item_count + 1):
            category = choose_product_category(day, channel)
            product = choose_product(
                products_by_category,
                category,
                member,
                channel,
                region,
            )
            quantity = item_quantity(product.category_l1)
            price_factor = (
                CITY_LEVEL_PRICE_WEIGHTS[region.city_level]
                * MEMBER_PRICE_WEIGHTS[member.member_level]
                * CHANNEL_PRICE_WEIGHTS[channel.channel_name]
                * random.uniform(0.9, 1.08)
            )
            raw_item_amount = money(product.list_price * quantity * Decimal(str(price_factor)))
            raw_discount = money(raw_item_amount * Decimal(str(discount_rate(day, channel))))
            raw_pay = max(Decimal("0.00"), raw_item_amount - raw_discount)
            cost_rate = random.uniform(product.cost_rate_min, product.cost_rate_max)
            cost_amount = money(raw_item_amount * Decimal(str(cost_rate)))

            item_id = f"I{order_index:08d}{item_number:02d}"
            row = {
                "item_id": item_id,
                "order_id": order_id,
                "product_id": product.product_id,
                "quantity": quantity,
                "item_amount": raw_item_amount,
                "discount_amount": raw_discount,
                "pay_amount": raw_pay,
                "cost_amount": cost_amount,
            }
            order_item_rows.append(row)
            order_amount += raw_item_amount
            discount_amount += raw_discount
            item_pay_amount += raw_pay

        shipping_amount = Decimal("0.00")
        if item_pay_amount < Decimal("99.00") and status != "cancelled":
            shipping_amount = money(random.choice([5, 8, 10, 12]))
        pay_amount = item_pay_amount + shipping_amount
        if status == "cancelled":
            pay_amount = Decimal("0.00")

        order_row = {
            "order_id": order_id,
            "date_id": day.date_id,
            "member_id": member.member_id,
            "region_id": region.region_id,
            "channel_id": channel.channel_id,
            "order_status": status,
            "order_amount": order_amount,
            "discount_amount": discount_amount,
            "pay_amount": pay_amount,
            "shipping_amount": shipping_amount,
        }
        orders_by_id[order_id] = order_row
        order_rows.append(order_row)
        item_rows.extend(order_item_rows)

        if status == "cancelled":
            continue

        for item_row in order_item_rows:
            product = products_by_id[item_row["product_id"]]
            probability = refund_probability(product, channel, day, status)
            if random.random() > probability:
                continue

            refund_qty = random.randint(1, item_row["quantity"])
            refund_ratio = Decimal(refund_qty) / Decimal(item_row["quantity"])
            refund_amount = money(item_row["pay_amount"] * refund_ratio)
            if random.random() < 0.18:
                refund_amount = money(refund_amount * Decimal(str(random.uniform(0.35, 0.85))))

            status_value = refund_status()
            refund_id = f"RF{len(refund_rows) + 1:08d}"
            refund_row = {
                "refund_id": refund_id,
                "order_id": order_id,
                "item_id": item_row["item_id"],
                "date_id": add_days_within_range(day, dates_by_id),
                "refund_amount": refund_amount,
                "refund_quantity": refund_qty,
                "refund_reason": refund_reason(product, channel),
                "refund_status": status_value,
            }
            refund_rows.append(refund_row)

            if status_value == "success":
                successful_refund_amount_by_order[order_id] += refund_amount

    for order_id, amount in successful_refund_amount_by_order.items():
        order = orders_by_id[order_id]
        if amount >= order["pay_amount"] * Decimal("0.9"):
            order["order_status"] = "refunded"
        else:
            order["order_status"] = "partial_refunded"

    return order_rows, item_rows, refund_rows


def to_date_rows(dates: list[DateProfile]) -> list[dict[str, Any]]:
    return [
        {
            "date_id": day.date_id,
            "date_value": day.date_value,
            "year": day.year,
            "quarter": day.quarter,
            "month": day.month,
            "day": day.day,
            "week_of_year": day.week_of_year,
            "weekday": day.weekday,
            "weekday_name": day.weekday_name,
            "is_weekend": day.is_weekend,
            "festival_name": day.festival_name,
        }
        for day in dates
    ]


def to_region_rows(regions: list[RegionProfile]) -> list[dict[str, Any]]:
    return [
        {
            "region_id": region.region_id,
            "region_name": region.region_name,
            "province": region.province,
            "city": region.city,
            "city_level": region.city_level,
            "country": "中国",
        }
        for region in regions
    ]


def to_product_rows(products: list[ProductProfile]) -> list[dict[str, Any]]:
    return [
        {
            "product_id": product.product_id,
            "product_name": product.product_name,
            "category_l1": product.category_l1,
            "category_l2": product.category_l2,
            "brand": product.brand,
            "price_band": product.price_band,
            "list_price": product.list_price,
        }
        for product in products
    ]


def to_member_rows(members: list[MemberProfile]) -> list[dict[str, Any]]:
    return [
        {
            "member_id": member.member_id,
            "member_level": member.member_level,
            "gender": member.gender,
            "age_band": member.age_band,
            "register_date_id": member.register_date_id,
            "member_stage": member.member_stage,
        }
        for member in members
    ]


def to_channel_rows(channels: list[ChannelProfile]) -> list[dict[str, Any]]:
    return [
        {
            "channel_id": channel.channel_id,
            "channel_name": channel.channel_name,
            "channel_type": channel.channel_type,
            "is_paid": channel.is_paid,
        }
        for channel in channels
    ]


async def validate_seed_data(engine: AsyncEngine) -> None:
    checks = [
        (
            "订单日期外键缺失",
            """
            SELECT COUNT(*) AS value
            FROM fact_order fo
            LEFT JOIN dim_date dd ON fo.date_id = dd.date_id
            WHERE dd.date_id IS NULL
            """,
        ),
        (
            "订单明细订单外键缺失",
            """
            SELECT COUNT(*) AS value
            FROM fact_order_item foi
            LEFT JOIN fact_order fo ON foi.order_id = fo.order_id
            WHERE fo.order_id IS NULL
            """,
        ),
        (
            "退款明细外键缺失",
            """
            SELECT COUNT(*) AS value
            FROM fact_refund fr
            LEFT JOIN fact_order_item foi ON fr.item_id = foi.item_id
            WHERE foi.item_id IS NULL
            """,
        ),
        (
            "订单金额和明细汇总不一致",
            """
            SELECT COUNT(*) AS value
            FROM fact_order fo
            JOIN (
                SELECT order_id, SUM(item_amount) AS item_total
                FROM fact_order_item
                GROUP BY order_id
            ) t ON fo.order_id = t.order_id
            WHERE ABS(fo.order_amount - t.item_total) > 0.01
            """,
        ),
    ]

    summaries = [
        (
            "核心指标",
            """
            SELECT
                COUNT(DISTINCT order_id) AS 订单数,
                ROUND(SUM(order_amount), 2) AS GMV,
                COUNT(DISTINCT member_id) AS 支付用户数
            FROM fact_order
            WHERE order_status <> 'cancelled'
            """,
        ),
        (
            "各地区GMV",
            """
            SELECT dr.region_name AS 地区, ROUND(SUM(fo.order_amount), 2) AS GMV
            FROM fact_order fo
            JOIN dim_region dr ON fo.region_id = dr.region_id
            WHERE fo.order_status <> 'cancelled'
            GROUP BY dr.region_name
            ORDER BY GMV DESC
            """,
        ),
        (
            "各渠道GMV",
            """
            SELECT dc.channel_name AS 渠道, ROUND(SUM(fo.order_amount), 2) AS GMV
            FROM fact_order fo
            JOIN dim_channel dc ON fo.channel_id = dc.channel_id
            WHERE fo.order_status <> 'cancelled'
            GROUP BY dc.channel_name
            ORDER BY GMV DESC
            """,
        ),
        (
            "各品类退款率",
            """
            SELECT
                dp.category_l1 AS 品类,
                ROUND(
                    SUM(CASE WHEN fr.refund_status = 'success' THEN fr.refund_quantity ELSE 0 END)
                    / NULLIF(SUM(foi.quantity), 0),
                    4
                ) AS 件数退款率
            FROM fact_order_item foi
            JOIN fact_order fo ON foi.order_id = fo.order_id
            JOIN dim_product dp ON foi.product_id = dp.product_id
            LEFT JOIN fact_refund fr ON foi.item_id = fr.item_id
            WHERE fo.order_status <> 'cancelled'
            GROUP BY dp.category_l1
            ORDER BY 件数退款率 DESC
            """,
        ),
    ]

    async with engine.connect() as conn:
        print("\n数据质量校验：")
        for name, sql in checks:
            value = (await conn.execute(text(sql))).scalar_one()
            print(f"- {name}: {value}")

        print("\n核心业务摘要：")
        for name, sql in summaries:
            rows = (await conn.execute(text(sql))).mappings().fetchall()
            print(f"\n{name}:")
            for row in rows[:10]:
                print(dict(row))


async def seed(args: argparse.Namespace) -> None:
    random.seed(args.seed)

    project_root = get_project_root()
    schema_path = project_root / "scripts" / "v2" / "init_dw_v2_schema.sql"

    if args.init_schema:
        print(f"开始初始化 schema：{schema_path}")
        init_engine = create_async_engine(
            build_async_url("mysql", args.admin_user, args.admin_password),
            pool_pre_ping=True,
        )
        await execute_sql_file(init_engine, schema_path)
        await init_engine.dispose()
        print("schema 初始化完成")

    engine = create_async_engine(
        build_async_url(args.database),
        pool_pre_ping=True,
        pool_size=10,
    )

    if not args.append:
        print("开始清空 dw_v2 旧数据")
        await truncate_tables(engine)
        print("旧数据清空完成")

    print("开始生成维表数据")
    dates = generate_dates()
    regions = generate_regions()
    channels = generate_channels()
    products = generate_products(args.products)
    members = generate_members(args.members, dates)

    print("开始生成订单、订单明细和退款数据")
    orders, items, refunds = generate_fact_data(
        order_count=args.orders,
        dates=dates,
        regions=regions,
        products=products,
        members=members,
        channels=channels,
    )

    print(
        "数据生成完成："
        f"日期 {len(dates)}，地区 {len(regions)}，商品 {len(products)}，"
        f"会员 {len(members)}，渠道 {len(channels)}，订单 {len(orders)}，"
        f"明细 {len(items)}，退款 {len(refunds)}"
    )

    print("开始写入维表")
    await batch_insert(engine, "dim_date", to_date_rows(dates))
    await batch_insert(engine, "dim_region", to_region_rows(regions))
    await batch_insert(engine, "dim_product", to_product_rows(products))
    await batch_insert(engine, "dim_member", to_member_rows(members))
    await batch_insert(engine, "dim_channel", to_channel_rows(channels))

    print("开始写入事实表")
    await batch_insert(engine, "fact_order", orders)
    await batch_insert(engine, "fact_order_item", items)
    await batch_insert(engine, "fact_refund", refunds)

    print("数据写入完成，开始校验")
    await validate_seed_data(engine)
    await engine.dispose()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="生成 dw_v2 电商数仓模拟数据")
    parser.add_argument("--database", default=DEFAULT_DATABASE, help="目标数据库名")
    parser.add_argument("--orders", type=int, default=DEFAULT_ORDERS, help="订单数量")
    parser.add_argument("--members", type=int, default=DEFAULT_MEMBERS, help="会员数量")
    parser.add_argument("--products", type=int, default=DEFAULT_PRODUCTS, help="商品数量")
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED, help="固定随机种子")
    parser.add_argument(
        "--init-schema",
        action="store_true",
        help="先执行 scripts/v2/init_dw_v2_schema.sql 重建 dw_v2 表结构，需要建库权限",
    )
    parser.add_argument(
        "--admin-user",
        default=None,
        help="执行 --init-schema 时使用的管理员用户，例如 root。不传则使用 app_config.yaml 的 db_dw.user。",
    )
    parser.add_argument(
        "--admin-password",
        default=None,
        help="执行 --init-schema 时使用的管理员密码。不传则使用 app_config.yaml 的 db_dw.password。",
    )
    parser.add_argument(
        "--append",
        action="store_true",
        help="追加数据，不清空已有数据。默认会清空 dw_v2 中的 v2 表。",
    )
    return parser.parse_args()


if __name__ == "__main__":
    asyncio.run(seed(parse_args()))
