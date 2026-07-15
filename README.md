# 遐蝶 · Windows 桌面 AI 伴侣 Agent

以 Live2D 桌宠为入口、以单主窗口为核心，具备聊天、记忆、任务、知识与工具能力的桌面 AI 伴侣。

本仓库是按需求文档 `v1.0` 从零搭建的 **可运行 MVP 骨架**，覆盖近期允许范围（Live2D 入口 + 单主窗口 + 聊天/记忆/任务/多模型 + 权限框架），刻意不做被列为"近期禁止"的完整多窗口、模型市场、QQ、语音通话、MCP、桌面自动化。

## 项目治理文档

- [Codex 项目上下文](docs/CODEX_PROJECT_CONTEXT.md)：新对话快速恢复项目边界与当前状态。
- [长期开发路线图](docs/XIADIE_LONG_TERM_ROADMAP.md)：从当前 MVP 到最终 Agent 形态的分阶段安排。
- [项目基线状态](docs/BASELINE_STATUS.md)：当前环境、验证结果、已有能力与已知风险。
- [小步开发与 PR 检查清单](docs/PR_CHECKLIST.md)：每次改动的范围、风险、验证与交付标准。
- [架构决策记录](docs/adr/README.md)：重大技术选择的记录规则与模板。

## 架构

```
desktop (Electron 壳)
  ├─ 透明置顶桌宠窗口（默认显示）+ 系统托盘 + 右键菜单
  └─ 主窗口（点击桌宠打开）
        └─ frontend (React + Vite + TS) ←→ backend (Python + FastAPI + SQLite)
```

| 层 | 技术 | 职责 |
|---|---|---|
| `desktop/` | Electron 33 | 启动、托盘、桌宠透明窗口、主窗口、IPC 状态联动 |
| `frontend/` | React 18 + Vite 5 + TS | 主窗口三栏 UI、聊天流式、设置/任务/记忆/知识/工具页、Live2D 桌宠渲染 |
| `backend/` | FastAPI + SQLite | 多模型统一接入、会话、记忆(L0/L1/L2)、任务、供应商配置、工具日志 |

## 已实现（对应需求编号）

- **Live2D 桌宠** L2D-001..007：内置固定模型、透明置顶窗口、点击打开主窗口、右键菜单、状态气泡、动作、模型缺失优雅占位。
- **聊天** CHAT-001..007：多轮上下文、SSE 流式、会话增删改、复制/收藏/重新生成、错误恢复卡、伴侣人设、任务/记忆卡片化。
- **多模型** MODEL-001..006：DeepSeek/OpenAI/GLM/Qwen/Kimi/OpenRouter/SiliconFlow/Ollama/自定义（全部 OpenAI-Compatible）、模型切换、能力标签、连接测试、密钥不回显、mock 降级。
- **记忆** 6.3：L0/L1/L2 分层，可见/可编辑/可删/可禁用；保守自动抽取标注来源；注入时"已参考记忆"提示。
- **任务** TASK-001..006：自然语言/按钮创建、状态流转、今日任务、聊天来源。
- **权限框架** 8.1：S0–S4 分级说明与只读审计视图；高风险默认需确认，无一键全开。
- **设置** 6.9：模型 API / 外观 / Live2D / 记忆 / 权限 / 数据 六分组；无模型替换入口。

## 本地开发

需要 Node ≥ 18、Python ≥ 3.10、[uv](https://github.com/astral-sh/uv)。

```bash
# 1) 后端
cd backend
uv venv --python 3.12 .venv
uv pip install --python .venv/bin/python -r requirements.txt
.venv/bin/python run.py          # http://127.0.0.1:8756

# 2) 前端（另开终端）
cd frontend
npm install
npm run dev                      # http://127.0.0.1:5173

# 3) 桌面壳（再开终端；dev 期假定前后端已启动）
cd desktop
npm install
npm start
```

启动后桌面出现 Live2D 遐蝶；点击桌宠或托盘打开主窗口。

浏览器里单独预览：主窗口 `http://127.0.0.1:5173/`，桌宠页 `http://127.0.0.1:5173/pet.html`。

依赖安装完成后，也可以直接双击仓库根目录的 `启动遐蝶.bat`。它会无终端窗口地启动后端、前端和 Electron；退出遐蝶后会清理本次拉起的后台进程，后端也会在启动器异常退出时通过父进程看门狗自行关闭。

## 测试与构建

```bash
cd backend && python -m pytest tests -q          # 后端 API 与领域回归测试（153 项）
cd frontend && npm run build                    # 前端类型检查 + 生产构建
```

## 模型供应商配置

首启使用内置 `mock` 演示模型，界面全部可用但回复为占位文案。到 **设置 → 模型 API** 填入任意兼容 OpenAI 接口的 Base URL + API Key，点"连接测试"通过后"设为当前"即可获得真实回复。密钥仅保存在本地 SQLite；正式版需迁移到系统安全存储（见"待办"）。

## 数据与安全

- 本地优先：会话/记忆/任务/设置存 `backend/data/xiadie.db`。
- 本地 API 保护：数据接口需要每次启动生成的临时令牌，CORS 仅允许明确的本机来源；令牌不写入 URL、日志或浏览器存储。
- 密钥不明文回传前端、不打印到普通日志。
- 高风险工具（S3/S4）默认需确认或禁用，留待权限系统完善后开放。

## 尚未实现 / 待办（按需求分期）

- **对外发布须替换 Live2D 模型**：当前内置的是用户提供的"遐蝶"桌宠模型，仅个人自用授权（禁止再分发/商用/上传/二改），与需求第 9 节冲突；正式版须换成原创或已授权可再分发的模型（见 [NOTICE.md](NOTICE.md)）。
- 密钥迁移到 **Electron safeStorage / 系统安全存储**（MODEL-005 正式版要求）。
- 文件与知识库的**真实索引与检索**（当前为占位页与原则展示）。
- 工具系统从说明升级为可执行 **ToolRegistry** + 确认卡 + 审计写入。
- 语音 TTS/ASR、外部平台 QQ/OneBot、桌面自动化 —— 均为需求明确的后置能力。
- 开机自启、透明窗口点击穿透在 Windows 上的实测。（Windows 打包已就绪，见 [BUILD-WINDOWS.md](BUILD-WINDOWS.md)）

## 说明

开发在 macOS 上进行，目标平台为 Windows；Electron 跨平台，但托盘行为、透明窗口、safeStorage、打包需在 Windows 上最终验证。
