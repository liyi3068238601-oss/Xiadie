import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const main = readFileSync(new URL("../main.js", import.meta.url), "utf8");

test("tray owns background lifetime while windows may close", () => {
  assert.match(main, /tray = new Tray\(icon\)/);
  assert.match(main, /app\.on\("window-all-closed"/);
  assert.match(main, /if \(!app\.isQuitting\)/);
  assert.match(main, /tray\.on\("click", \(\) => createMainWindow\(\)\)/);
});

test("suspend stops delivery and resume installs backend guard before polling", () => {
  assert.match(main, /powerMonitor\.on\("suspend", \(\) => stopDeliveryBridge\(\)\)/);
  assert.match(main, /powerMonitor\.on\("resume"/);
  assert.match(main, /\/api\/proactive\/runtime\/system-resume/);
  assert.match(main, /\.finally\(\(\) => startDeliveryBridge\(\)\)/);
});

test("quit stops polling and terminates the owned backend", () => {
  assert.match(main, /app\.on\("before-quit"/);
  assert.match(main, /stopDeliveryBridge\(\)/);
  assert.match(main, /if \(backendProc\) backendProc\.kill\(\)/);
  assert.match(main, /XIADIE_PARENT_PID: String\(process\.pid\)/);
});
