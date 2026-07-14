# -*- mode: python ; coding: utf-8 -*-
# PyInstaller 打包规格：把 FastAPI 后端冻结成独立可执行（onedir）。
# 用法（在 backend/ 下、已装 pyinstaller 的环境）：pyinstaller xiadie-backend.spec
# 跨平台通用；必须在目标平台各自运行（PyInstaller 不能交叉编译）。
from PyInstaller.utils.hooks import collect_all, collect_submodules

datas, binaries, hiddenimports = [], [], []

# 尽量把这些包整包收进来，避免动态导入缺失。缺某个包（如 Windows 无 uvloop）时跳过。
for pkg in (
    "uvicorn", "fastapi", "starlette", "pydantic", "pydantic_core",
    "anyio", "sniffio", "h11", "click", "annotated_types",
    "httptools", "websockets", "watchfiles", "typing_extensions",
):
    try:
        d, b, h = collect_all(pkg)
        datas += d
        binaries += b
        hiddenimports += h
    except Exception:
        pass

# 应用自身的所有子模块
hiddenimports += collect_submodules("app")

# uvicorn 的动态导入协议/事件循环（PyInstaller 静态分析抓不到）
hiddenimports += [
    "uvicorn.logging",
    "uvicorn.loops", "uvicorn.loops.auto", "uvicorn.loops.asyncio",
    "uvicorn.protocols", "uvicorn.protocols.http", "uvicorn.protocols.http.auto",
    "uvicorn.protocols.http.h11_impl", "uvicorn.protocols.http.httptools_impl",
    "uvicorn.protocols.websockets", "uvicorn.protocols.websockets.auto",
    "uvicorn.protocols.websockets.websockets_impl",
    "uvicorn.protocols.websockets.wsproto_impl",
    "uvicorn.lifespan", "uvicorn.lifespan.on", "uvicorn.lifespan.off",
]

a = Analysis(
    ["run_frozen.py"],
    pathex=["."],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter", "matplotlib", "numpy", "PIL", "pytest"],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="xiadie-backend",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,  # 后端后台运行，不弹控制台窗口；排障时可临时改 True
    disable_windowed_traceback=False,
    icon=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="xiadie-backend",
)
