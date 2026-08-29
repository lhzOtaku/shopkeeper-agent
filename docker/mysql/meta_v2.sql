SET NAMES utf8mb4;
CREATE DATABASE IF NOT EXISTS meta_v2 DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci;

USE meta_v2;

DROP TABLE IF EXISTS metric_variant_column;
DROP TABLE IF EXISTS metric_variant;
DROP TABLE IF EXISTS column_metric;
DROP TABLE IF EXISTS table_relation;
DROP TABLE IF EXISTS metric_info;
DROP TABLE IF EXISTS column_info;
DROP TABLE IF EXISTS table_info;

CREATE TABLE table_info
(
    id          VARCHAR(64) PRIMARY KEY COMMENT '表编号',
    name        VARCHAR(128) COMMENT '表名称',
    role        VARCHAR(32) COMMENT '表类型(fact/dim)',
    description TEXT COMMENT '表描述',
    grain       VARCHAR(64) COMMENT '表粒度'
);

CREATE TABLE column_info
(
    id          VARCHAR(64) PRIMARY KEY COMMENT '列编号',
    name        VARCHAR(128) COMMENT '列名称',
    type        VARCHAR(64) COMMENT '数据类型',
    role        VARCHAR(32) COMMENT '列类型(primary_key,foreign_key,measure,dimension)',
    examples    JSON COMMENT '数据示例',
    description TEXT COMMENT '列描述',
    alias       JSON COMMENT '列别名',
    table_id    VARCHAR(64) COMMENT '所属表编号'
);

CREATE TABLE metric_info
(
    id                 VARCHAR(64) PRIMARY KEY COMMENT '指标编码',
    name               VARCHAR(128) COMMENT '指标名称',
    description        TEXT COMMENT '指标描述',
    alias              JSON COMMENT '指标别名',
    default_variant_id VARCHAR(128) COMMENT '默认指标口径 ID'
);

CREATE TABLE metric_variant
(
    id                 VARCHAR(128) PRIMARY KEY COMMENT '口径 ID',
    metric_id          VARCHAR(64) COMMENT '所属指标 ID',
    name               VARCHAR(128) COMMENT '口径名称',
    grain              VARCHAR(64) COMMENT '计算粒度',
    base_table         VARCHAR(128) COMMENT '基础事实表',
    formula            TEXT COMMENT '指标公式',
    default_filters    JSON COMMENT '默认过滤条件',
    suitable_dimensions JSON COMMENT '适用维度表',
    required_joins     JSON COMMENT '额外 Join 约束',
    UNIQUE KEY uk_metric_variant_name (metric_id, name)
);

CREATE TABLE metric_variant_column
(
    variant_id VARCHAR(128) COMMENT '指标口径 ID',
    column_id  VARCHAR(64) COMMENT '列编号',
    PRIMARY KEY (variant_id, column_id)
);

CREATE TABLE table_relation
(
    id             VARCHAR(128) PRIMARY KEY COMMENT '关系编号',
    left_table     VARCHAR(64) COMMENT '左表',
    left_column    VARCHAR(64) COMMENT '左字段',
    right_table    VARCHAR(64) COMMENT '右表',
    right_column   VARCHAR(64) COMMENT '右字段',
    relation_type  VARCHAR(32) COMMENT '关系类型',
    description    TEXT COMMENT '关系描述'
);
