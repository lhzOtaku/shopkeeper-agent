SET NAMES utf8mb4;

CREATE DATABASE IF NOT EXISTS dw_v2 DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci;
USE dw_v2;

SET FOREIGN_KEY_CHECKS = 0;

DROP TABLE IF EXISTS fact_refund;
DROP TABLE IF EXISTS fact_order_item;
DROP TABLE IF EXISTS fact_order;
DROP TABLE IF EXISTS dim_channel;
DROP TABLE IF EXISTS dim_member;
DROP TABLE IF EXISTS dim_product;
DROP TABLE IF EXISTS dim_region;
DROP TABLE IF EXISTS dim_date;

SET FOREIGN_KEY_CHECKS = 1;

CREATE TABLE dim_date
(
    date_id       INT PRIMARY KEY COMMENT '日期ID，格式yyyyMMdd',
    date_value    DATE        NOT NULL COMMENT '真实日期',
    year          INT         NOT NULL COMMENT '年份',
    quarter       VARCHAR(2)  NOT NULL COMMENT '季度，如Q1、Q2',
    month         INT         NOT NULL COMMENT '月份',
    day           INT         NOT NULL COMMENT '日期中的日',
    week_of_year  INT         NOT NULL COMMENT '一年中的第几周',
    weekday       INT         NOT NULL COMMENT '星期几，1-7',
    weekday_name  VARCHAR(10) NOT NULL COMMENT '星期名称',
    is_weekend    TINYINT     NOT NULL DEFAULT 0 COMMENT '是否周末，1是0否',
    festival_name VARCHAR(50) NULL COMMENT '节日或活动名称'
) ENGINE = InnoDB
  DEFAULT CHARSET = utf8mb4
  COLLATE = utf8mb4_general_ci
  COMMENT = '日期维度表，用于支持年、季度、月份、周末和节日活动分析';

CREATE TABLE dim_region
(
    region_id   VARCHAR(32) PRIMARY KEY COMMENT '地区ID',
    region_name VARCHAR(50) NOT NULL COMMENT '大区名称，如华东、华南',
    province    VARCHAR(50) NOT NULL COMMENT '省份',
    city        VARCHAR(50) NOT NULL COMMENT '城市',
    city_level  VARCHAR(20) NOT NULL COMMENT '城市等级，如一线、新一线、二线',
    country     VARCHAR(20) NOT NULL DEFAULT '中国' COMMENT '国家'
) ENGINE = InnoDB
  DEFAULT CHARSET = utf8mb4
  COLLATE = utf8mb4_general_ci
  COMMENT = '地区维度表，用于描述订单发生的大区、省份、城市和城市等级';

CREATE TABLE dim_product
(
    product_id   VARCHAR(32) PRIMARY KEY COMMENT '商品ID',
    product_name VARCHAR(200)   NOT NULL COMMENT '商品名称',
    category_l1  VARCHAR(50)    NOT NULL COMMENT '一级品类',
    category_l2  VARCHAR(50)    NOT NULL COMMENT '二级品类',
    brand        VARCHAR(50)    NOT NULL COMMENT '品牌',
    price_band   VARCHAR(20)    NOT NULL COMMENT '价格带，如低价、中价、高价',
    list_price   DECIMAL(12, 2) NOT NULL COMMENT '商品标价'
) ENGINE = InnoDB
  DEFAULT CHARSET = utf8mb4
  COLLATE = utf8mb4_general_ci
  COMMENT = '商品维度表，用于描述商品、品类、品牌和价格带';

CREATE TABLE dim_member
(
    member_id        VARCHAR(32) PRIMARY KEY COMMENT '会员ID',
    member_level     VARCHAR(20) NOT NULL COMMENT '会员等级，如普通、银卡、金卡、铂金',
    gender           VARCHAR(10) NOT NULL COMMENT '性别',
    age_band         VARCHAR(20) NOT NULL COMMENT '年龄段',
    register_date_id INT         NOT NULL COMMENT '注册日期ID',
    member_stage     VARCHAR(20) NOT NULL COMMENT '会员阶段，如新客、老客、高价值、沉默',
    CONSTRAINT fk_dim_member_register_date
        FOREIGN KEY (register_date_id) REFERENCES dim_date (date_id)
) ENGINE = InnoDB
  DEFAULT CHARSET = utf8mb4
  COLLATE = utf8mb4_general_ci
  COMMENT = '会员维度表，用于描述会员等级、年龄段和用户阶段';

CREATE TABLE dim_channel
(
    channel_id   VARCHAR(32) PRIMARY KEY COMMENT '渠道ID',
    channel_name VARCHAR(50) NOT NULL COMMENT '渠道名称，如App、小程序、直播',
    channel_type VARCHAR(50) NOT NULL COMMENT '渠道类型，如自有渠道、付费渠道、内容渠道',
    is_paid      TINYINT     NOT NULL DEFAULT 0 COMMENT '是否付费渠道，1是0否'
) ENGINE = InnoDB
  DEFAULT CHARSET = utf8mb4
  COLLATE = utf8mb4_general_ci
  COMMENT = '渠道维度表，用于描述订单来源渠道和渠道类型';

CREATE TABLE fact_order
(
    order_id        VARCHAR(32) PRIMARY KEY COMMENT '订单ID',
    date_id         INT            NOT NULL COMMENT '下单日期ID',
    member_id       VARCHAR(32)    NOT NULL COMMENT '下单会员ID',
    region_id       VARCHAR(32)    NOT NULL COMMENT '下单地区ID',
    channel_id      VARCHAR(32)    NOT NULL COMMENT '下单渠道ID',
    order_status    VARCHAR(20)    NOT NULL COMMENT '订单状态，如paid、completed、cancelled、refunded',
    order_amount    DECIMAL(12, 2) NOT NULL COMMENT '订单原始成交金额，可作为GMV基础',
    discount_amount DECIMAL(12, 2) NOT NULL DEFAULT 0.00 COMMENT '订单优惠金额',
    pay_amount      DECIMAL(12, 2) NOT NULL COMMENT '用户实际支付金额',
    shipping_amount DECIMAL(12, 2) NOT NULL DEFAULT 0.00 COMMENT '运费金额',
    CONSTRAINT fk_fact_order_date
        FOREIGN KEY (date_id) REFERENCES dim_date (date_id),
    CONSTRAINT fk_fact_order_member
        FOREIGN KEY (member_id) REFERENCES dim_member (member_id),
    CONSTRAINT fk_fact_order_region
        FOREIGN KEY (region_id) REFERENCES dim_region (region_id),
    CONSTRAINT fk_fact_order_channel
        FOREIGN KEY (channel_id) REFERENCES dim_channel (channel_id)
) ENGINE = InnoDB
  DEFAULT CHARSET = utf8mb4
  COLLATE = utf8mb4_general_ci
  COMMENT = '订单事实表，一行一笔订单，用于GMV、订单数、客单价和支付用户数分析';

CREATE TABLE fact_order_item
(
    item_id         VARCHAR(32) PRIMARY KEY COMMENT '订单明细ID',
    order_id        VARCHAR(32)    NOT NULL COMMENT '所属订单ID',
    product_id      VARCHAR(32)    NOT NULL COMMENT '商品ID',
    quantity        INT            NOT NULL COMMENT '购买件数',
    item_amount     DECIMAL(12, 2) NOT NULL COMMENT '明细原始金额',
    discount_amount DECIMAL(12, 2) NOT NULL DEFAULT 0.00 COMMENT '明细优惠金额',
    pay_amount      DECIMAL(12, 2) NOT NULL COMMENT '明细实际支付金额',
    cost_amount     DECIMAL(12, 2) NOT NULL COMMENT '明细成本金额',
    CONSTRAINT fk_fact_order_item_order
        FOREIGN KEY (order_id) REFERENCES fact_order (order_id),
    CONSTRAINT fk_fact_order_item_product
        FOREIGN KEY (product_id) REFERENCES dim_product (product_id)
) ENGINE = InnoDB
  DEFAULT CHARSET = utf8mb4
  COLLATE = utf8mb4_general_ci
  COMMENT = '订单明细事实表，一行一个订单商品明细，用于销量、商品GMV和品类分析';

CREATE TABLE fact_refund
(
    refund_id       VARCHAR(32) PRIMARY KEY COMMENT '退款ID',
    order_id        VARCHAR(32)    NOT NULL COMMENT '关联订单ID',
    item_id         VARCHAR(32)    NOT NULL COMMENT '关联订单明细ID',
    date_id         INT            NOT NULL COMMENT '退款日期ID',
    refund_amount   DECIMAL(12, 2) NOT NULL COMMENT '退款金额',
    refund_quantity INT            NOT NULL COMMENT '退款件数',
    refund_reason   VARCHAR(100)   NOT NULL COMMENT '退款原因',
    refund_status   VARCHAR(20)    NOT NULL COMMENT '退款状态，如success、processing、rejected',
    CONSTRAINT fk_fact_refund_order
        FOREIGN KEY (order_id) REFERENCES fact_order (order_id),
    CONSTRAINT fk_fact_refund_item
        FOREIGN KEY (item_id) REFERENCES fact_order_item (item_id),
    CONSTRAINT fk_fact_refund_date
        FOREIGN KEY (date_id) REFERENCES dim_date (date_id)
) ENGINE = InnoDB
  DEFAULT CHARSET = utf8mb4
  COLLATE = utf8mb4_general_ci
  COMMENT = '退款事实表，一行一次退款事件，用于退款金额、退款订单数和退款率分析';

CREATE INDEX idx_dim_date_year_quarter ON dim_date (year, quarter);
CREATE INDEX idx_dim_date_year_month ON dim_date (year, month);
CREATE INDEX idx_dim_date_festival ON dim_date (festival_name);

CREATE INDEX idx_dim_region_region_name ON dim_region (region_name);
CREATE INDEX idx_dim_region_city_level ON dim_region (city_level);

CREATE INDEX idx_dim_product_category ON dim_product (category_l1, category_l2);
CREATE INDEX idx_dim_product_brand ON dim_product (brand);
CREATE INDEX idx_dim_product_price_band ON dim_product (price_band);

CREATE INDEX idx_dim_member_level ON dim_member (member_level);
CREATE INDEX idx_dim_member_stage ON dim_member (member_stage);

CREATE INDEX idx_dim_channel_name ON dim_channel (channel_name);
CREATE INDEX idx_dim_channel_type ON dim_channel (channel_type);

CREATE INDEX idx_fact_order_date ON fact_order (date_id);
CREATE INDEX idx_fact_order_region ON fact_order (region_id);
CREATE INDEX idx_fact_order_member ON fact_order (member_id);
CREATE INDEX idx_fact_order_channel ON fact_order (channel_id);
CREATE INDEX idx_fact_order_status ON fact_order (order_status);

CREATE INDEX idx_fact_order_item_order ON fact_order_item (order_id);
CREATE INDEX idx_fact_order_item_product ON fact_order_item (product_id);

CREATE INDEX idx_fact_refund_order ON fact_refund (order_id);
CREATE INDEX idx_fact_refund_item ON fact_refund (item_id);
CREATE INDEX idx_fact_refund_date ON fact_refund (date_id);
CREATE INDEX idx_fact_refund_status ON fact_refund (refund_status);
