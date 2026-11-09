# CI 与发布流水线

> 状态：已确认
> 更新时间：2026-11-09
> 用途：说明公开仓 PR 必过检查、完整测试报告与 tag 发布流程。

## 1. 工作流

| 工作流 | 触发 | 作用 |
| --- | --- | --- |
| `ci` | PR、main push、`v*` tag | 前端生产构建、全历史 gitleaks 扫描 |
| `required-checks` | PR、main push | web、python-gates、unit-test、platform、windows、gate-canary、docker-build |
| `release` | `v*` tag | 多架构镜像、CLI 包、校验和、Release Notes、干净 Ubuntu 冒烟 |

## 2. PR 必过检查

分支保护要求以下 context 全部通过：

- `web`：typecheck、生产构建、增量 eslint、Vitest 全量运行与 known-failures 棘轮
- `python-gates`：增量 ruff、增量 mypy、placement guard、指针完整性、测试资产守卫、commit message、PR 证据、issue 关联、风险路径
- `unit-test`：门禁自测、测试资产守卫自测、testkit 与后端配额单测
- `platform`：变更文件的 CRLF、大小写冲突、可执行位、bash 语法，以及 compose 配置
- `windows`：CRLF 与盘符路径敏感用例
- `gate-canary`：门禁带病样本必红、干净样本必绿
- `docker-build`：api 与 web 的 compose 镜像构建；worker 镜像由 release 工作流构建
- `web-build`：现有前端构建快检
- `secrets-scan`：gitleaks 全历史扫描

完整后端目录选面由 `unit-test-full-informational` 运行。当前基线仍有历史失败与不可收集文件，该 job 只报告、不阻断。known-failures 基线刷新后再升级为必过。

## 3. 本地复现

```bash
pnpm --filter @openair/web typecheck
pnpm --filter @openair/web build
python3 scripts/gates/diff_gate.py --eslint --base origin/main
python3 scripts/gates/check_platform_consistency.py --base origin/main
python3 scripts/gates/check_pointer_integrity.py --base origin/main
python3 scripts/gates/check_known_failures_ratchet.py --base origin/main
services/api/.venv/bin/python -m pytest scripts/gates/canary -q
```

完整选面：

```bash
PYTHONPATH=services/api services/api/.venv/bin/python scripts/gates/select_unit_tests.py --json > selection.json
PYTHONPATH=services/api services/api/.venv/bin/python scripts/gates/run_unit_suite.py run --selection selection.json
python3 scripts/gates/run_unit_suite.py ratchet
```

## 4. 发布

推送 `v*` tag 后，流水线执行：

1. 构建 `openair-cli` 的 wheel 与 sdist，生成 `SHA256SUMS`。
2. 构建 `linux/amd64` 与 `linux/arm64` 的 api、web、worker 镜像，推送 `ghcr.io/<owner>/openair-*`。
3. 创建 GitHub Release，附 CLI 包、校验和、镜像 digest、`docker-compose.yml` 与 `.env.example`。
4. 在干净 Ubuntu runner 安装 CLI wheel，执行 `openair up --mode replay` 并检查 API 与工作台。

发布前按 [阶段发布流程](../../04_开发协作/releases/release_process.md) 整理里程碑与验证记录。

## 5. 当前边界

- 上游仓库分支保护需要管理员权限；fork 侧已配置同样的 required contexts。
- 完整后端单测选面尚未转绿，`unit-test-full-informational` 只提供证据。
- release 冒烟依赖 MinIO 镜像替换先合入，因为默认 compose 需要可拉取的对象存储镜像。
- macOS 暂不作为必过平台，只在后续按需增加。
