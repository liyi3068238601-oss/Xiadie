// 遐蝶桌面壳（需求第 3、10 节）：
// 默认只显示 Live2D 桌宠窗口；点击桌宠打开主窗口；系统托盘常驻。
// 不做启动多窗口堆叠。
const { app, BrowserWindow, Tray, Menu, ipcMain, screen, shell, Notification } = require("electron");
const path = require("path");
const { fileURLToPath } = require("url");
const { spawn } = require("child_process");
const http = require("http");
const { randomBytes } = require("crypto");

// Electron 33 的 app.isPackaged 在从 node_modules/electron 运行时仍可能返回
// true（已知行为变化），用 resourcesPath 是否落在 node_modules 内作为补充判断。
const isDev =
  !app.isPackaged ||
  process.resourcesPath.includes("node_modules") ||
  process.env.XIADIE_DEV_MODE === "1";
const BACKEND_PORT = 8756;
const DEV_URL = "http://127.0.0.1:5173";
const inheritedToken = process.env.XIADIE_API_TOKEN || "";
// 开发启动器需要先启动后端，因此会提供同一枚临时令牌；正式包始终由 Electron 生成。
const API_TOKEN = isDev && inheritedToken.length >= 32
  ? inheritedToken
  : randomBytes(32).toString("base64url");

let petWin = null;
let mainWin = null;
let tray = null;
let backendProc = null;
let deliveryTimer = null;
let deliveryBusy = false;
const deliveryConsumerId = `electron-${process.pid}-${randomBytes(8).toString("hex")}`;
const rendererDeliveries = new Map();

function backendJson(method, apiPath, body) {
  return new Promise((resolve, reject) => {
    const encoded = body === undefined ? null : Buffer.from(JSON.stringify(body));
    const req = http.request({
      hostname: "127.0.0.1", port: BACKEND_PORT, path: apiPath, method,
      headers: {
        "X-Xiadie-Token": API_TOKEN,
        ...(encoded ? { "Content-Type": "application/json", "Content-Length": encoded.length } : {}),
      },
      timeout: 5000,
    }, (res) => {
      const chunks = [];
      res.on("data", (chunk) => chunks.push(chunk));
      res.on("end", () => {
        const raw = Buffer.concat(chunks).toString("utf8");
        if (res.statusCode < 200 || res.statusCode >= 300) {
          return reject(new Error(`backend_${res.statusCode || "error"}`));
        }
        try { resolve(raw ? JSON.parse(raw) : null); }
        catch { reject(new Error("backend_invalid_json")); }
      });
    });
    req.on("timeout", () => req.destroy(new Error("backend_timeout")));
    req.on("error", reject);
    if (encoded) req.write(encoded);
    req.end();
  });
}

async function acknowledgeDelivery(item, success, errorCode) {
  try {
    await backendJson("POST", `/api/proactive-deliveries/${item.id}/ack`, {
      consumer_id: deliveryConsumerId,
      lease_token: item.lease_token,
      success,
      error_code: success ? null : errorCode,
    });
  } finally {
    rendererDeliveries.delete(item.id);
  }
}

async function pollProactiveDelivery() {
  if (deliveryBusy || app.isQuitting) return;
  deliveryBusy = true;
  try {
    const claimedResponse = await backendJson("POST", "/api/proactive-deliveries/claim", {
      consumer_id: deliveryConsumerId,
    });
    const claimed = claimedResponse?.delivery;
    if (!claimed) return;
    const begun = await backendJson("POST", `/api/proactive-deliveries/${claimed.id}/begin`, {
      consumer_id: deliveryConsumerId,
      lease_token: claimed.lease_token,
    });
    if (begun.status === "delivered" && begun.channel === "chat") {
      mainWin?.webContents.send("proactive-chat-message", {
        session_id: begun.session_id, delivery_id: begun.id, message_id: begun.message_id,
      });
      return;
    }
    if (begun.status !== "delivering") return;
    if (begun.channel === "live2d" || begun.channel === "bubble") {
      if (!petWin || petWin.isDestroyed()) {
        await acknowledgeDelivery(begun, false, "pet_window_unavailable");
        return;
      }
      rendererDeliveries.set(begun.id, begun);
      petWin.webContents.send("proactive-delivery", {
        id: begun.id, channel: begun.channel, payload: begun.payload,
      });
      return;
    }
    if (begun.channel === "desktop_notification") {
      if (!Notification.isSupported()) {
        await acknowledgeDelivery(begun, false, "notification_unsupported");
        return;
      }
      const notice = new Notification({ title: begun.payload.title, body: begun.payload.body });
      let settled = false;
      const settle = (success, code) => {
        if (settled) return;
        settled = true;
        void acknowledgeDelivery(begun, success, code);
      };
      notice.once("show", () => settle(true));
      notice.once("failed", () => settle(false, "notification_failed"));
      notice.show();
    }
  } catch (error) {
    console.warn("proactive delivery bridge unavailable:", error.message);
  } finally {
    deliveryBusy = false;
  }
}

function startDeliveryBridge() {
  if (deliveryTimer) return;
  void pollProactiveDelivery();
  deliveryTimer = setInterval(() => void pollProactiveDelivery(), 2000);
}

function stopDeliveryBridge() {
  if (deliveryTimer) clearInterval(deliveryTimer);
  deliveryTimer = null;
}

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
    env: {
      ...process.env,
      XIADIE_API_TOKEN: API_TOKEN,
      XIADIE_DATA_DIR: dataDir,
      XIADIE_PARENT_PID: String(process.pid),
      XIADIE_BGE_M3_DIR: path.join(process.resourcesPath, "models", "bge-m3"),
    },
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
let normalBounds = null;

function createMainWindow() {
  if (mainWin) {
    if (mainWin.isMinimized()) mainWin.restore();
    mainWin.show();
    mainWin.focus();
    return;
  }
  mainWin = new BrowserWindow({
    width: 1360,
    height: 880,
    minWidth: 1000,
    minHeight: 640,
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
  mainWin.on("resize", () => {
    if (!mainWin || !normalBounds) return;
    const { width: sw, height: sh } = screen.getPrimaryDisplay().workAreaSize;
    const { width: ww, height: wh } = mainWin.getBounds();
    // 窗口接近屏幕 → 最大化了，不覆盖 normalBounds
    if (ww >= sw - 8 && wh >= sh - 8) return;
    normalBounds = mainWin.getBounds();
  });
  mainWin.on("maximize", () => {
    mainWin?.webContents.send("window-maximized", true);
  });
  mainWin.on("unmaximize", () => {
    mainWin?.webContents.send("window-maximized", false);
  });
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
ipcMain.on("maximize-main", () => {
  if (!mainWin) return;
  const { width: sw, height: sh } = screen.getPrimaryDisplay().workAreaSize;
  const { width: ww, height: wh } = mainWin.getBounds();
  // 窗口宽高接近屏幕宽高 → 已处于最大化，还原
  if (ww >= sw - 8 && wh >= sh - 8 && normalBounds) {
    mainWin.setBounds(normalBounds);
    normalBounds = null;
    mainWin.webContents.send("window-maximized", false);
  } else {
    normalBounds = mainWin.getBounds();
    mainWin.setBounds({ x: 0, y: 0, width: sw, height: sh });
    mainWin.webContents.send("window-maximized", true);
  }
});
ipcMain.on("hide-pet", () => petWin && petWin.hide());
ipcMain.on("reset-pet", () => resetPet());
ipcMain.on("quit", () => quit());
ipcMain.on("show-pet-menu", () => {
  buildTrayMenu().popup();
});

// preload 同步读取一次后保存在渲染进程内存中；不通过 URL 或持久化存储传递。
ipcMain.on("get-api-token", (event) => {
  const senderUrl = event.senderFrame?.url || "";
  let trusted = false;
  try {
    if (isDev) {
      trusted = new URL(senderUrl).origin === DEV_URL;
    } else if (senderUrl.startsWith("file://")) {
      const frontendRoot = path.resolve(process.resourcesPath, "frontend") + path.sep;
      trusted = path.resolve(fileURLToPath(senderUrl)).startsWith(frontendRoot);
    }
  } catch {
    trusted = false;
  }
  event.returnValue = trusted ? API_TOKEN : "";
});

// 桌宠窗口拖拽移动
ipcMain.on("pet-drag", (_e, { dx, dy }) => {
  if (!petWin) return;
  const [x, y] = petWin.getPosition();
  petWin.setPosition(x + Math.round(dx), y + Math.round(dy));
});

// 主窗口 → 桌宠：工作模式驱动动作，后端情绪簇驱动表情。
ipcMain.on("pet-state", (_e, payload) => {
  if (petWin && !petWin.isDestroyed()) petWin.webContents.send("pet-state", payload);
});

ipcMain.on("proactive-delivery-ack", (event, payload) => {
  if (!petWin || event.sender.id !== petWin.webContents.id) return;
  const item = rendererDeliveries.get(payload?.id);
  if (!item) return;
  void acknowledgeDelivery(item, payload.success === true,
    payload.success === true ? null : "pet_render_failed");
});

// ---------------------------------------------------------------- 生命周期
app.whenReady().then(() => {
  startBackend();
  createTray();
  if (isDev) {
    createPetWindow();
    startDeliveryBridge();
  } else {
    waitForBackend((ready) => {
      createPetWindow();
      if (ready) startDeliveryBridge();
    });
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
  stopDeliveryBridge();
  if (backendProc) backendProc.kill();
});
