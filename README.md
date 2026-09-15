# AuraAgent

一个从零构建的轻量级个人 AI Agent：纯 `asyncio` + 官方 `anthropic` SDK 实现的白盒 ReAct 循环，不依赖 LangChain 等黑盒框架。

## v1 范围

- **核心异步 ReAct 循环**（`core/react_engine.py`）：Reason → Act → Observe，每一轮的 Thought / Tool Call / Observation 都打印到终端并写入 `logs/session-*.jsonl`。
- **可插拔的 LLM 抽象层**（`providers/`）：`LLMProvider` 接口 + `AnthropicProvider` 实现；`OpenAIProvider` 留有接口占位，后续可直接实现。
- **本地 Markdown 笔记工具**（`tools/notes/`）：`search_notes` / `read_note` / `create_note` / `update_note`，所有文件操作严格限制在 `sandbox/notes/` 内，防止路径穿越。
- **架构占位（下一轮实现）**：本地 JSON 日历 + Human-in-the-loop 确认（`tools/calendar/`, `confirmation/`）、MCP 客户端（`mcp_integration/`）、Skill 热加载（`skills/`, `skills_store/`）。这些模块的接口/目录已经搭好，方法体标注 `NotImplementedError` 或 `TODO`。

## 运行方式

```bash
# 1. 安装依赖（建议先建虚拟环境）
python -m venv .venv
.venv\Scripts\activate        # Windows PowerShell: .venv\Scripts\Activate.ps1
pip install -r requirements.txt

# 2. 配置 API Key
copy .env.example .env
# 编辑 .env，填入 ANTHROPIC_API_KEY

# 3. 运行
python main.py
```

启动后进入交互式 REPL,输入自然语言指令即可(如"帮我创建一篇笔记记录今天的会议要点"),输入 `exit` 退出。终端会实时打印每一轮的 Thought / Tool Call / Observation。

## 运行测试

```bash
pytest
```

测试使用 `FakeLLMProvider`(见 `tests/fakes.py`)驱动 ReAct 引擎,不需要真实 API Key。

## 目录结构

```
AuraAgent/
├── main.py                 # 组合根 + REPL 入口
├── config/                 # 配置加载 (pydantic-settings)
├── core/                   # ReAct 引擎、日志、异常、消息类型（不依赖任何具体实现）
├── providers/               # LLMProvider 抽象层 + Anthropic 实现
├── tools/                  # ToolRegistry + notes/calendar 工具
├── confirmation/            # Human-in-the-loop 确认通道抽象
├── mcp_integration/         # MCP 客户端接入点（占位）
├── skills/ skills_store/    # Skill 热加载机制（占位）+ 示例 skill
├── sandbox/                # 所有工具副作用限定于此
├── logs/                   # 白盒执行日志 (JSONL)
└── tests/                  # pytest 单测
```

## 下一轮迭代计划

1. 本地 JSON 日历 CRUD + `CalendarTool` 的 HITL 确认逻辑
2. MCP `stdio` 服务器真实连接 + 工具动态注册
3. Skill 加载器：解析 `SKILL.md` 并注册为可调用工具
4. （更远期）`OpenAIProvider` 真实实现、`GoogleCalendarProvider` OAuth 接入、FastAPI 封装
