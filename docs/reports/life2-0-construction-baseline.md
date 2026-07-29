# LIFE2.0 ConstructionBaseline

- 日期：2026-07-30
- 分支：`agent/life-v2-specialty`
- commit：`303ce2c02a7c19584a8a28199a2ddf58e61b3a8f`
- predecessor：`main@3a663391cf12f5a843f4c1d5e311628ce8637c6e`
- Schema：81
- Python：3.12.13（项目 `backend/.venv`）
- Node.js：24.16.0
- npm：11.13.0
- Electron：33.4.11

## 依赖指纹

| 文件 | SHA-256 |
|---|---|
| `backend/requirements.txt` | `ebb9ff8af31964fa41ae6e6ed391e7f8e9cb977f8ce06504c3d875cbee6f7435` |
| `frontend/package-lock.json` | `000b31c1344d7c8bc247106bcc58fa161b5983de79e2eaa5a19c5b1a2010d489` |
| `desktop/package-lock.json` | `a693db5929e10edfd361a0a0b18dddca335ee6dd3cf045c6b3efe4a741a8a059` |

## 实际测试

| 范围 | 命令 | 结果 |
|---|---|---|
| 后端全量 | `backend/.venv/Scripts/python.exe -m pytest -p no:cacheprovider tests` | 2597 passed，1 warning，498.75s |
| 前端单测 | `npm.cmd test` | 71 passed，0 failed |
| 前端生产构建 | `npm.cmd run build` | 通过，189 modules transformed；保留既有非阻断 `pet.html` script bundle 提示 |

第一次误用系统 Python 3.12.10 得到 2582 passed / 15 failed，其中失败集中于系统环境缺少 `tzdata`；项目锁定的 `.venv` 为 Python 3.12.13、`tzdata 2026.3`，同一全量集合全部通过。权威基线只采用项目虚拟环境结果，不把误调用记为产品缺陷或掩盖为通过。

## 冻结边界

- 基线提交只含计划、Persona/WorldBook 内容与来源文档、Review 响应。
- 无运行时代码变更、无迁移、无 Provider 调用。
- Persona v2、WorldBook r1、ShortMemo 与 InnerStateProjection 均未启用。
- 下一阶段只建立 LIFE2.1 合成评测与当前 Persona 的 DeepSeek 基线。
