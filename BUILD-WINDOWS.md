# 遐蝶 · Windows 打包指南

把整个项目（Electron 壳 + React 前端 + Python FastAPI 后端）打成一个 **Windows 安装器（`遐蝶-Setup-<版本>.exe`）**，装完即用，**目标机器无需预装 Python 或 Node**。

## 🚀 最简单：一键打包安装（自用推荐）

把整个项目文件夹拷到你的 Windows 电脑，**双击根目录的 `一键打包安装.bat`** 就行。它会自动：

1. 检查并（用 winget）自动安装缺失的 **Node.js / Python**；
2. 构建前端 → 冻结后端 → 打成安装器；
3. 自动弹出安装向导，点几下"下一步"即完成安装。

首次运行需几分钟（下载依赖）。装完后桌面/开始菜单出现"遐蝶"。
> 若脚本提示没有 winget 或自动装依赖失败，按提示手动装好 Node（LTS）和 Python 3.12（勾 Add to PATH）后重新双击即可。

下面是手动分步说明（排障或想了解细节时看）。

---


## 为什么必须在 Windows 上打包

后端是 Python，用 **PyInstaller 冻结成独立 `.exe`**；PyInstaller **不能跨平台编译**——要得到 Windows 的后端 `.exe`，必须在 Windows 上运行冻结。因此最终安装器只能在一台 **Windows x64** 机器（或虚拟机）上产出。本仓库已把全部配置和脚本准备好，Windows 上一条命令即可。

## 前置条件（在打包用的 Windows 机器上装一次）

| 工具 | 版本 | 备注 |
|---|---|---|
| Node.js | ≥ 18 | 安装时保持默认（含 npm） |
| Python | 3.10 – 3.12 | 安装时**务必勾选 "Add Python to PATH"** |
| Git | 任意 | 用于拿到代码（或直接拷贝整个目录） |

> Windows 需要能联网：electron-builder 会下载 Windows 版 Electron，pip 会下载后端依赖。

## 一键打包

在**仓库根目录**打开 PowerShell：

```powershell
.\build-windows.ps1
```

它会依次：**构建前端 → 冻结后端 → electron-builder 打成 NSIS 安装器**。
如果仓库外层存在 `bge-m3\onnx\model_quantized.onnx`，脚本会自动把完整模型暂存并打入安装资源；安装包
会因此增加约 543 MiB。模型文件保持 Git 忽略，不会被误提交。缺少模型时仍可打包，知识库自动使用 FTS。
完成后安装器在：

```
dist-installer\遐蝶-Setup-0.1.0.exe
```

双击即可安装；装完桌面/开始菜单出现"遐蝶"，启动后桌宠浮在桌面右上角，点击弹出主窗口。

## 分步执行（排障时）

```powershell
# 1) 前端
cd frontend ; npm install ; npm run build ; cd ..
# 2) 后端冻结（产出 backend\dist\xiadie-backend\xiadie-backend.exe）
cd backend  ; .\build-backend.ps1 ; cd ..
# 3) 安装器
cd desktop  ; npm install ; npm run dist ; cd ..
```

## 打包后的运行结构

```
安装目录\
├─ 遐蝶.exe                     # Electron 主程序
└─ resources\
   ├─ app.asar                 # 桌面壳（main.js / preload.js）
   ├─ frontend\                # 前端静态产物（index.html / pet.html / models / libs）
   ├─ backend\
      ├─ xiadie-backend.exe    # 冻结的 FastAPI 后端
      └─ _internal\            # 冻结依赖
   └─ models\bge-m3\           # 可选的本地 1024 维 dense 模型
```

- 启动时 Electron 拉起 `resources\backend\xiadie-backend.exe`（本地 `127.0.0.1:8756`）。
- 用户数据（SQLite 会话/记忆/任务）写到 **`%APPDATA%\遐蝶\data\`**（可写目录，不在安装目录里）。
- 前端从 `resources\frontend\` 以 `file://` 加载。

## 常见问题

- **杀毒 / SmartScreen 提示未签名**：未做代码签名的安装器首次运行会被 Windows SmartScreen 拦一下（"更多信息 → 仍要运行"）。正式对外发布建议购买代码签名证书，在 `electron-builder.yml` 里配置签名。
- **后端起不来**：确认 `backend\dist\xiadie-backend\xiadie-backend.exe` 存在；排障时可把 `backend\xiadie-backend.spec` 里的 `console=False` 改成 `True` 重新冻结，能看到后端控制台日志。
- **端口占用**：后端固定 `127.0.0.1:8756`；若被占用，改 `backend\run_frozen.py` 与 `desktop\main.js` 里的端口后重打。
- **electron-builder 提示不能创建 symbolic link**：打开 Windows“开发者模式”，或用具备“创建符号链接”权限的
  发布构建机再运行。该错误来自签名工具缓存解压，不代表前端、后端或 BGE-M3 资源损坏。

## ⚠️ 分发前必看：Live2D 模型授权

当前内置的"遐蝶"Live2D 模型是**个人自用授权**，作者明确**禁止再分发**（见 [NOTICE.md](NOTICE.md)）。
- 自己打包**自用**没问题。
- **对外分发这个安装器会违反模型授权**——正式发布前必须把 `frontend/public/models/xiadie/` 换成一个**原创或已获再分发授权**的模型。

## 关于图标

`desktop/build/icon.ico` 是占位的紫蝶品牌图标，可按需替换（保持 `.ico`，含 16–256 多尺寸）。
