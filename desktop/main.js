// 遐蝶桌面壳（需求第 3、10 节）：
// 默认只显示 Live2D 桌宠窗口；点击桌宠打开主窗口；系统托盘常驻。
// 不做启动多窗口堆叠。
const { app, BrowserWindow, Tray, Menu, ipcMain, screen, shell } = require("electron");
const path = require("path");
const { spawn } = require("child_process");
const http = require("http");

const isDev = !app.isPackaged;
const BACKEND_PORT = 8756;
const DEV_URL = "http://127.0.0.1:5173";

let petWin = null;
let mainWin = null;
let tray = null;
let backendProc = null;

// ---- 前端资源定位：dev 用 vite server，prod 用打包进 resources 的静态文件 ----
function frontendUrl(page) {
  if (isDev) return `${DEV_URL}/${page}`;
  // 打包后前端在 resources/frontend/（见 electron-builder.yml extraResources）
  return "file://" + path.join(process.resourcesPath, "frontend", page);
}

// ---------------------------------------------------------------- 后端
function startBackend() {
  // 生产环境随应用启动本地 FastAPI（PyInstaller 冻结的独立 exe）；
  // dev 期假定开发者已手动 `python run.py`。
  if (isDev) return;
  // 冻结后端在 resources/backend/xiadie-backend(.exe)
  const exeName =
    process.platform === "win32" ? "xiadie-backend.exe" : "xiadie-backend";
  const backendExe = path.join(process.resourcesPath, "backend", exeName);
  // 数据写入用户可写目录（resources 是只读的），后端读 XIADIE_DATA_DIR
  const dataDir = path.join(app.getPath("userData"), "data");
  backendProc = spawn(backendExe, [], {
    cwd: path.dirname(backendExe),
    stdio: "ignore",
    env: { ...process.env, XIADIE_DATA_DIR: dataDir },
  });
  // 必须监听 error：否则 ENOENT 会作为未处理的 EventEmitter error 抛出，导致主进程崩溃。
  backendProc.on("error", (e) => {
    console.error("后端启动失败:", e);
    backendProc = null;
  });
  backendProc.on("exit", (code, signal) => {
    console.warn(`后端退出: code=${code} signal=${signal}`);
    backendProc = null;
  });
}

function waitForBackend(cb, tries = 0) {
  http
    .get(`http://127.0.0.1:${BACKEND_PORT}/api/health`, (res) => {
      res.resume();
      cb(true);
    })
    .on("error", () => {
      if (tries > 40) return cb(false);
      setTimeout(() => waitForBackend(cb, tries + 1), 500);
    });
}

// ---------------------------------------------------------------- 桌宠窗口
function createPetWindow() {
  const { width } = screen.getPrimaryDisplay().workAreaSize;
  petWin = new BrowserWindow({
    width: 280,
    height: 380,
    x: width - 320,
    y: 120,
    frame: false,
    transparent: true,
    resizable: false,
    skipTaskbar: true,
    alwaysOnTop: true,
    hasShadow: false,
    webPreferences: {
      preload: path.join(__dirname, "preload.js"),
      contextIsolation: true,
    },
  });
  petWin.setAlwaysOnTop(true, "screen-saver");
  petWin.loadURL(frontendUrl("pet.html"));
  petWin.on("closed", () => (petWin = null));
  // 任何来源的显隐变化都刷新托盘标签（hide-pet / resetPet / togglePet 均覆盖）
  petWin.on("show", refreshTrayMenu);
  petWin.on("hide", refreshTrayMenu);
}

// ---------------------------------------------------------------- 主窗口
function createMainWindow() {
  if (mainWin) {
    if (mainWin.isMinimized()) mainWin.restore();
    mainWin.show();
    mainWin.focus();
    return;
  }
  mainWin = new BrowserWindow({
    width: 1100,
    height: 720,
    minWidth: 900,
    minHeight: 560,
    frame: false,
    transparent: true,
    backgroundColor: "#00000000",
    show: false,
    webPreferences: {
      preload: path.join(__dirname, "preload.js"),
      contextIsolation: true,
    },
  });
  mainWin.loadURL(frontendUrl("index.html"));
  mainWin.once("ready-to-show", () => mainWin.show());
  mainWin.on("closed", () => (mainWin = null));
}

// ---------------------------------------------------------------- 托盘
function createTray() {
  // 用一个内联的 1x1 透明图兜底，避免缺图标导致崩溃；有真实图标时替换。
  const { nativeImage } = require("electron");
  let icon = nativeImage.createFromPath(path.join(__dirname, "assets", "tray.png"));
  if (icon.isEmpty()) {
    icon = nativeImage.createEmpty();
  }
  tray = new Tray(icon);
  tray.setToolTip("遐蝶");
  tray.setContextMenu(buildTrayMenu());
  tray.on("click", () => createMainWindow());
}

// 托盘菜单是静态快照，桌宠显隐后需重建，否则"显示/隐藏遐蝶"标签与实际状态相反。
function refreshTrayMenu() {
  if (tray) tray.setContextMenu(buildTrayMenu());
}

function buildTrayMenu() {
  return Menu.buildFromTemplate([
    { label: "打开主窗口", click: () => createMainWindow() },
    {
      label: petWin && petWin.isVisible() ? "隐藏遐蝶" : "显示遐蝶",
      click: () => togglePet(),
    },
    { label: "重置桌宠位置", click: () => resetPet() },
    { type: "separator" },
    { label: "退出", click: () => quit() },
  ]);
}

function togglePet() {
  if (!petWin) return createPetWindow();
  if (petWin.isVisible()) petWin.hide();
  else petWin.show();
  if (tray) tray.setContextMenu(buildTrayMenu());
}

function resetPet() {
  if (!petWin) return;
  const { width } = screen.getPrimaryDisplay().workAreaSize;
  petWin.setPosition(width - 320, 120);
  petWin.show();
}

function quit() {
  app.isQuitting = true;
  if (backendProc) backendProc.kill();
  app.quit();
}

// ---------------------------------------------------------------- IPC
ipcMain.on("open-main", () => createMainWindow());
ipcMain.on("hide-main", () => mainWin && mainWin.hide());
ipcMain.on("minimize-main", () => mainWin && mainWin.minimize());
ipcMain.on("hide-pet", () => petWin && petWin.hide());
ipcMain.on("reset-pet", () => resetPet());
ipcMain.on("quit", () => quit());
ipcMain.on("show-pet-menu", () => {
  buildTrayMenu().popup();
});

// 桌宠窗口拖拽移动
ipcMain.on("pet-drag", (_e, { dx, dy }) => {
  if (!petWin) return;
  const [x, y] = petWin.getPosition();
  petWin.setPosition(x + Math.round(dx), y + Math.round(dy));
});

// 主窗口 → 桌宠：状态联动（思考中/完成 等触发气泡）
ipcMain.on("pet-state", (_e, payload) => {
  if (petWin && !petWin.isDestroyed()) petWin.webContents.send("pet-state", payload);
});

// ---------------------------------------------------------------- 生命周期
app.whenReady().then(() => {
  startBackend();
  createTray();
  if (isDev) {
    createPetWindow();
  } else {
    waitForBackend(() => createPetWindow());
  }
});

app.on("window-all-closed", (e) => {
  // 桌宠/主窗口都关了也不退出，保持托盘常驻（需求：后台运行）。
  if (!app.isQuitting) {
    // 不调用 app.quit()
  }
});

app.on("activate", () => {
  if (!petWin) createPetWindow();
});

app.on("before-quit", () => {
  app.isQuitting = true;
  if (backendProc) backendProc.kill();
});
