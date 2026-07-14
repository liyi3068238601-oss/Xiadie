// 暴露受控的桌面能力给渲染进程（contextIsolation 下的安全桥）。
const { contextBridge, ipcRenderer } = require("electron");
const apiToken = ipcRenderer.sendSync("get-api-token");

contextBridge.exposeInMainWorld("xiadie", {
  // 主窗口 / 桌宠控制
  openMain: () => ipcRenderer.send("open-main"),
  hideMain: () => ipcRenderer.send("hide-main"),
  minimizeMain: () => ipcRenderer.send("minimize-main"),
  hidePet: () => ipcRenderer.send("hide-pet"),
  resetPet: () => ipcRenderer.send("reset-pet"),
  quit: () => ipcRenderer.send("quit"),
  showPetMenu: () => ipcRenderer.send("show-pet-menu"),

  // 桌宠窗口拖拽
  dragPet: (dx, dy) => ipcRenderer.send("pet-drag", { dx, dy }),

  // 主窗口 → 桌宠 状态联动（气泡 / 表情 / 动作）
  setPetState: (state, bubble, emotion) =>
    ipcRenderer.send("pet-state", { state, bubble, emotion }),
  onPetState: (cb) =>
    ipcRenderer.on("pet-state", (_e, payload) => cb(payload)),

  // 仅保存在当前渲染进程内存中；不进入 URL、日志或浏览器存储。
  getApiToken: () => apiToken,
});

// 后端地址注入，供前端 api.ts 读取
contextBridge.exposeInMainWorld("__XIADIE_API__", "http://127.0.0.1:8756");
