# AGENTS.md

OpenAIR：通用 AI 科研平台。用户用自然语言描述研究任务，平台经 4 阶段生命周期（problem_modeling → solution_design → execution_validation → insight_generation）编排，由 Claude Agent SDK 调用 skills 执行。文档以简体中文为主。

**工程规则唯一源是 [spec/README.md](spec/README.md)——本文件不是规则源，只做导航。**

## 技术栈

- pnpm workspace monorepo：前端 `apps/web/`（React 18 + Ant Design v6 + Vite）；后端 `services/api/`（FastAPI）；共享类型 `packages/shared/`；skill 唯一人维护源 `skills/`
- 后端执行多 runner 由 `SKILL_EXEC_MODE` 控制（live=Agent SDK / replay=fixture 回放）
- 各 IDE 的 skill 镜像目录（如 .claude/skills/）为生成物，禁手改（SPEC-GEN-01）

## 本地环境要点

- Windows + Git Bash；中文路径必须加引号
- 后端 Python venv 位于 services/api 下（.venv）；worktree 内无 venv 时用绝对路径调主仓解释器
- `import llm_core` 在 API import 时必需（packages/llm_core 需安装）
- 具体命令与新代码放置位见 [spec/workflows/development-workflow.md](spec/workflows/development-workflow.md)

## 指路表

| 要做什么 | 去哪 |
|---|---|
| 查任何工程规则（编码/门禁/安全/流程/测试/协作） | [spec/README.md](spec/README.md)（按任务→读哪篇矩阵） |
| 写代码前的质量底线与豁免棘轮 | [spec/foundations/coding-standards.md](spec/foundations/coding-standards.md) |
| 发 PR / 证据合同必填字段 | [spec/workflows/pr-workflow.md](spec/workflows/pr-workflow.md) |
| 写 commit message | [spec/collaboration/git_commit_rules.md](spec/collaboration/git_commit_rules.md) |
| 提交前跑门禁脚本 | [scripts/gates/](scripts/gates/) |
| 写任务规格（复杂任务先写 spec 再动码） | [docs/task_specs/TEMPLATE.md](docs/task_specs/TEMPLATE.md) |
| 产品设计合同（产品如何构成） | [docs/01_核心设计/](docs/01_核心设计/)（入口 docs/README.md） |
| 开发 skill（IDE 触发与索引） | [docs/dev_skills/](docs/dev_skills/) |

## Agent 协议

工程技能的 harness 无关配置在 [docs/agents/](docs/agents/)：领域文档、issue tracker 与 AI 贡献标记、分诊标签映射。这些文件只放工具配置与映射，工程规则正文仍以 `spec/` 为准，冲突时以 spec 为准。

## 维护纪律

本文件保持 <60 行：加规则去 spec/ 开 PR，不要在这里写正文；路径变更由指针完整性检查（SPEC-PTR-01）保活。

## 写作要求

所有供人阅读的文本（注释、GLOSSARY.md、ADR、issue 评论、PR 描述、agent brief、分诊记录、文档、Agent 回复）应当：

- 准确、清楚、简洁。先理解材料，再提炼结论。
- 按逻辑组织，区分相近概念。不用空泛、夸大的修饰语。
- 面向实际读者，从已知事实推到陌生结论。用分析说服，不装腔作势，不堆砌辞藻。
- 语言统一中文，不中英混用。命令、代码、路径、专有名词保留原文。
- 概念不直译。没有通行译名的概念用功能描述，不生造译名。同一概念全文只用一个词：用“标准”不用“规范”，用“任务要求”不用“规格”，测试结果用“通过、失败”不用“红、绿”。
- 标点符号：中文语境用全角中文标点（句号“。”、逗号“，”、顿号“、”）。纯英文句子用英文标点。行内代码、文件名、命令后的标点根据所在句子语境选择。引号：中文用弯引号“”，英文用直引号""。列举用顿号（“、”）分隔，最后一项前不加“和”或“与”。
- 不用破折号（“——”、“—”）、分号、箭头（“→”）、markdown 加粗和表情符号。补充说明用括号，并列与分支写成独立句子，顺序与对应关系用文字表达。
- 单位用国标：带单位的数值用 GB 3100～3102 的法定计量单位。写单位符号时，数值与符号之间留一个空格（`200 ms`、`5 min`、`64 MB`），不写 `200MS`、`64MB` 这类变体。中文行文里用汉字单位名称（30 秒、5 分钟）同样合规。

## 编码准则

- 先理解再改动：完整阅读目标文件、相似实现和相关测试。不确定 API 或惯例时查源码或文档，不猜。
- 明确目标与决策：需求或验收条件不明确时先澄清。架构选择、假设和关键取舍要说明。
- 保持简单：只实现当前需求。复用已有模式。不为单一用例过早抽象、配置化或引入依赖。
- 精准修改：只改与任务直接相关的代码，贴合既有风格。删掉本次修改产生的废弃代码，不重新格式化无关内容。
- 注释不留历史：删掉只对读过旧版本的人有意义的注释（“原来是 X”“改成 Y 是为了修 Z”）。注释解释当前代码为什么这样写，变更过程由 git 记录。
- 完整迁移：变更接口或行为时更新所有调用方、测试和文档。不保留无需求的兼容层。
- 按根因修复：先复现并读完整错误信息。一次处理一个原因，不靠吞异常或特判掩盖问题。
- 验证行为：按影响范围运行相关检查。测试可观察行为、边界和错误路径，不测试实现细节。无法测试时说明原因并做可行的烟雾验证。
- 审慎依赖：优先现有依赖和标准库。新增依赖前确认必要性、维护状态和成本，并说明理由。
- 清楚沟通：说明做了什么、为什么、验证结果和已知风险。对不确定性给出具体事实，提交信息描述实际改动。
