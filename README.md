# Programming Interview Agent

一个用 LLM API key 调用大模型的面试训练 agent。它支持 Python、Java 和 AI Agent 面试方向，可以生成题目、提示、评估答案、继续追问，并用 SQLite 知识库记录训练过程、memory 和上下文。当前包含 Web 版和 CLI 版。

## Web 版运行

```powershell
python .\src\interview_agent_app\web_agent.py
```

然后打开：

```text
http://127.0.0.1:8000
```

页面里可以填写 API Key、Base URL、模型、操作者、面试方向、难度和题数。原来的交互命令已经变成按钮：

- `提交评估`：提交你的思路或代码。
- `提示`：请求一个渐进式提示。
- `参考答案`：查看参考答案和复杂度分析。
- `下一题`：进入下一道题，最后一题后会生成训练总结。
- `查看历史 Memory`：读取当前操作者和面试方向的历史记忆。
- `训练进度`：查看训练次数、评估次数、平均分、最高分、薄弱点排行和最近训练趋势。
- `错题本`：查看低分或未评分的评估记录，并从错题直接重练相似题。
- `训练历史/复盘`：查看历史 session、平均分、题型、模式，并打开详细复盘。
- `错题重练`：基于历史低分评估生成相似但不重复的训练题。
- `结束当前训练`：结束当前 Web 会话。

## 训练模式与题型

Web 版支持三种训练模式：

- `练习模式`：可以看提示和参考答案，适合学习。
- `模拟面试`：禁用提示和参考答案，更接近真实面试。
- `错题重练`：读取历史低分评估和薄弱点，生成针对性复练题。

评估结果会自动渲染成评分卡，包含总分、正确性、复杂度、边界条件、代码质量、沟通表达和标签。模拟面试结束时会生成模拟面试报告，包括是否建议通过、关键失分点和下一次训练建议。

题型会根据方向变化：

- `Python`：数组/字符串、哈希表、链表、树/图、动态规划、并发、装饰器/迭代器、性能优化等。
- `Java`：集合、JVM、多线程、Spring、数据库、系统设计、性能调优等。
- `AI Agent`：Agent 架构、Tool Calling、Memory、RAG、Evaluation、Guardrails、部署监控、成本控制等。

## SQLite 知识库

默认会在当前目录创建：

```text
interview_memory.sqlite3
```

也可以指定路径：

```powershell
python .\src\interview_agent_app\web_agent.py --db .\data\interview_memory.sqlite3
```

数据库包含：

- `sessions`：每次训练的操作者、方向、难度、模型、开始和结束时间。
- `events`：题目、候选人答案、提示、参考答案、评估、总结等上下文，并记录轮次、评分、标签、模型、耗时和 token usage。
- `memories`：训练结束后生成的可复用候选人记忆，下次同一操作者和方向会自动注入上下文。

`sessions` 还会记录训练 `topic`、`mode` 和错题重练来源；`events` 会解析评估结果里的 `evaluation_json`，用于历史复盘、平均分和错题筛选。

## 安全与可靠性

- Web 服务默认只允许本机访问。确实需要局域网访问时，可以设置 `INTERVIEW_AGENT_ALLOW_REMOTE=1`。
- JSON 请求体默认限制为 512KB，可以用 `INTERVIEW_AGENT_MAX_BODY_BYTES` 调整。
- LLM 请求内置重试，适合处理临时网络错误、429 和 5xx。
- 长会话会自动压缩旧上下文，避免历史消息无限增长。
- 历史 memory 会作为不可信候选人画像注入，不会被当成系统指令执行。

## 工程结构

核心代码已经拆出几个独立模块：

- `src/interview_agent_app/app_config.py`：环境变量、默认模型、训练方向、题型、模式等配置。
- `src/interview_agent_app/evaluation.py`：解析模型输出的 `evaluation_json`，提取分数和标签。
- `src/interview_agent_app/repository.py`：SQLite 建表、迁移、sessions/events/memories 读写。
- `src/interview_agent_app/sql/001_init.sql`：完整初始表结构和索引。
- `src/interview_agent_app/sql/002_compat_columns.sql`：兼容旧数据库的补字段迁移清单。
- `src/interview_agent_app/interview_agent.py`：LLM client 和面试 Agent 核心逻辑。
- `src/interview_agent_app/web_agent.py`：当前标准库 Web 服务和页面。

不调用大模型的自检：

```powershell
python .\src\interview_agent_app\self_check.py
```

它会检查配置、评估 JSON 解析、SQLite 建表/写入/查询。

## CLI 版运行

```powershell
python .\src\interview_agent_app\interview_agent.py --api-key "你的 API Key"
```

也可以用环境变量：

```powershell
$env:LLM_API_KEY="你的 API Key"
python .\src\interview_agent_app\interview_agent.py
```

## 常用参数

```powershell
python .\src\interview_agent_app\interview_agent.py `
  --language Python `
  --difficulty medium `
  --rounds 3 `
  --model gpt-4o-mini `
  --base-url https://api.openai.com/v1
```

参数说明：

- `--language`：面试方向，例如 `Python`、`Java`、`AI Agent`、`JavaScript`、`TypeScript`、`C++`、`Go`、`Rust`，也可以传自定义方向名。
- `--difficulty`：难度，可选 `easy`、`medium`、`hard`。
- `--rounds`：训练题数。
- `--model`：模型名，也可以用环境变量 `LLM_MODEL`。
- `--base-url`：OpenAI-compatible API 地址，也可以用环境变量 `LLM_BASE_URL`。

## 交互命令

答题时支持：

- `/done`：提交当前多行答案。
- `/hint`：请求一个提示。
- `/answer`：查看参考答案。
- `/next`：跳到下一题。
- `/clear`：清空当前多行输入。
- `/quit`：退出训练。
