# Shopkeeper Agent

Shopkeeper Agent 是一个面向电商经营分析场景的智能问数项目。用户可以使用自然语言提出业务问题，系统会结合指标定义、数仓结构和字段值信息生成 SQL，完成安全校验、执行，并通过聊天界面流式返回结果。


## 核心功能

### 自然语言问数

- 支持 GMV、订单、销量、退款等电商经营指标查询。
- 支持时间、地区、渠道、会员、商品等维度的筛选与分组。
- 将自然语言问题转换为 SQL，并返回查询结果。

### 多轮对话与追问

- `main_agent` 识别闲聊、独立问数和追问问数。
- 使用会话记忆保存上一轮查询上下文。
- 将“那华东呢”“换成按月统计”等追问改写成可独立执行的问题。
- 对信息不足、取消查询和重试耗尽等情况提供明确响应。

### 元数据与多路召回

- 从 MySQL 读取表结构、字段、表关系和指标口径。
- 使用 Qdrant 召回相关字段和指标。
- 使用 Elasticsearch 召回可能的字段取值。
- 合并多路召回结果，为 SQL 生成提供受约束的上下文。

### 指标口径与粒度处理

- 根据用户问题筛选相关指标和候选表。
- 处理同一指标的不同业务口径和数据粒度。
- 在生成 SQL 前补充日期、关联字段和表关系信息。

### SQL 生成与安全执行

- 使用大模型生成 SQL。
- 通过 SQLGlot 执行语法和安全检查。
- 在执行前进行校验，并在失败时进入有限次数的修正流程。
- 通过异步 MySQL 客户端执行最终查询。

### 流式 API 与 Web 界面

- FastAPI 通过 SSE 持续返回 Agent 执行进度、结果和错误。
- React 前端提供聊天式交互、步骤展示和表格结果展示。
- 使用 `session_id` 区分不同会话的上下文。

## 工作流程

```text
用户问题
   │
   ▼
main_agent
   ├── 闲聊 ───────────────► 直接回复
   ├── 独立问数 ───────────► ask_agent
   └── 追问 ─► 上下文改写 ─► ask_agent
                              │
                              ▼
                    关键词抽取与多路召回
                              │
                              ▼
                    表、字段和指标筛选
                              │
                              ▼
                    SQL 生成、校验与修正
                              │
                              ▼
                         执行并返回结果
```

## 项目结构

```text
shopkeeper-agent/
├── app/
│   ├── agents/
│   │   ├── ask_agent/       # Text2SQL 图、状态、节点和对外适配器
│   │   ├── main_agent/      # 意图识别、多轮追问、路由和会话记忆
│   │   └── report_agent/    # 报告 Agent 预留骨架，当前未接入主要流程
│   ├── api/
│   │   ├── routers/         # /api/chat 与 /api/query 路由
│   │   └── schemas/         # API 请求结构
│   ├── clients/             # MySQL、Qdrant、ES 和 Embedding 客户端
│   ├── conf/                # YAML 配置加载与结构化配置对象
│   ├── core/                # 日志、上下文和重试等通用能力
│   ├── entities/            # 业务实体
│   ├── memory/              # 会话记忆与查询规格
│   ├── models/              # SQLAlchemy ORM 模型
│   ├── prompt/              # Prompt 加载器
│   ├── repositories/        # MySQL、Qdrant 和 ES 数据访问层
│   ├── scripts/             # 元数据知识库构建入口
│   └── services/            # 对话、问数和元数据构建服务
├── conf/                    # 应用配置、表结构和指标定义
├── docker/                  # 本地基础服务、初始化 SQL 和 ES 插件
├── docs/                    # 设计文档和界面图片
├── frontend/                # React + Vite + TypeScript 前端
├── prompts/                 # ask_agent 与 main_agent 的 Prompt 模板
├── scripts/v2/              # 数仓初始化、数据生成和评测工具
├── tests/                   # 单元、节点契约、图、语义和集成测试
├── main.py                  # FastAPI 应用入口
├── pyproject.toml           # Python 项目和工具配置
└── uv.lock                  # Python 依赖锁文件
```

## 技术栈

| 部分 | 技术 |
| --- | --- |
| Agent 编排 | LangGraph、LangChain |
| 后端 | FastAPI、Pydantic、SSE |
| 大模型 | DeepSeek 兼容接口 |
| 数据库 | MySQL、SQLAlchemy、asyncmy |
| 向量检索 | Qdrant、BGE 中文 Embedding、TEI |
| 全文检索 | Elasticsearch、IK 分词 |
| SQL 处理 | SQLGlot |
| 前端 | React、TypeScript、Vite、Tailwind CSS |
| 工程工具 | uv、pnpm、Docker Compose、pytest、Ruff |

## 本地启动

### 1. 环境要求

- Python 3.14+
- [uv](https://docs.astral.sh/uv/)
- Node.js 22+ 与 pnpm
- Docker 与 Docker Compose

### 2. 安装依赖

安装后端依赖：

```bash
uv sync
```

安装前端依赖：

```bash
cd frontend
pnpm install
cd ..
```

### 3. 配置环境变量

复制环境变量模板：

```powershell
Copy-Item .env.example .env
```

填写本地 `.env`：

```dotenv
DEEPSEEK_API_KEY=your_api_key
MYSQL_ROOT_PASSWORD=your_local_root_password
MYSQL_USER=your_local_app_user
MYSQL_PASSWORD=your_local_app_password
```

`.env` 已被 Git 忽略，不要使用 `git add -f` 强制提交真实凭据。

### 4. 准备 Embedding 模型

下载 `BAAI/bge-large-zh-v1.5`，并将完整模型文件放到：

```text
docker/embedding/bge-large-zh-v1.5/
```

该目录不会提交到 Git。

### 5. 启动基础服务

```bash
docker compose --env-file .env -f docker/docker-compose.yaml up -d
```

默认端口：

| 服务 | 端口 |
| --- | ---: |
| MySQL | 3307 |
| Elasticsearch | 9200 |
| Kibana | 5601 |
| Qdrant HTTP / gRPC | 6333 / 6334 |
| Embedding | 8081 |

### 6. 初始化教学数仓

首次运行时创建 `dw_v2` 并生成模拟电商数据。将命令中的密码替换为 `.env` 中的 `MYSQL_ROOT_PASSWORD`：

```bash
uv run python scripts/v2/seed_dw_v2_data.py --init-schema --admin-user root --admin-password YOUR_MYSQL_ROOT_PASSWORD
```

### 7. 构建元数据知识库

将表、字段、指标和字段值同步到 MySQL、Qdrant 与 Elasticsearch：

```bash
uv run python -m app.scripts.build_meta_knowledge -c conf/meta_config_v2.yaml --reset-retrieval-indexes
```

`--reset-retrieval-indexes` 会重建检索索引，适合首次初始化；已有数据环境中请谨慎使用。

### 8. 启动后端

```bash
uv run fastapi dev main.py
```

后端默认地址为 `http://127.0.0.1:8000`，OpenAPI 页面位于 `http://127.0.0.1:8000/docs`。

### 9. 启动前端

另开一个终端：

```bash
cd frontend
pnpm dev
```

打开 `http://127.0.0.1:5173`。Vite 默认将 `/api` 请求代理到 `http://127.0.0.1:8000`。

## API

### `POST /api/chat`

支持多轮对话的主要接口：

```json
{
  "session_id": "demo-session",
  "message": "统计 2025 年各月份的 GMV"
}
```

响应类型为 `text/event-stream`。

### `POST /api/query`

直接调用问数 Agent 的无会话接口：

```json
{
  "query": "统计华东地区的退款率"
}
```

## 测试与检查

运行不依赖真实外部服务的测试：

```bash
uv run pytest -m "not real_data"
```

运行代码检查：

```bash
uv run ruff check app tests scripts main.py
```

检查前端类型和构建：

```bash
cd frontend
pnpm run lint
pnpm run build
```


