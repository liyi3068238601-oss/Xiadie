import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const api = readFileSync(new URL("../src/api.ts", import.meta.url), "utf8");
const page = readFileSync(new URL("../src/components/SettingsPage.tsx", import.meta.url), "utf8");

test("ordinary cognition settings expose only natural capabilities", () => {
  assert.match(page, /让遐蝶更稳妥地理解和回应/);
  assert.match(page, /cognition\.natural_capabilities/);
  assert.match(page, /<details className="settings-advanced">/);
});

test("advanced cognition settings use authenticated API and safe diagnostics", () => {
  assert.match(api, /\/api\/cognition\/settings/);
  assert.match(api, /\/api\/cognition\/diagnostics\/v2/);
  assert.match(page, /模式由已冻结注册表限制/);
  assert.match(page, /诊断不保存正文、Prompt、原始模型输出或候选 ID/);
  assert.match(page, /一键回退到原有逻辑/);
});
