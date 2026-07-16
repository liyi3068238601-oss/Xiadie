// 桌宠页：在透明窗口里渲染 Live2D 遐蝶。
// 关键约束（需求 L2D-001..007 + 11.2）：
// - 模型/Core 缺失时不崩溃，退化为 CSS 蝴蝶占位并给出清晰提示。
// - 单击打开主窗口；按住拖拽移动窗口；右键出菜单。
// - 支持状态气泡（欢迎/思考中/完成/提醒）。
import { createRoot } from "react-dom/client";
import { useEffect, useRef, useState } from "react";
import "./pet.css";
import { getClusterPresentation } from "./affectPresentation.mjs";

const MODEL_URL = "./models/xiadie/Xiadie.model3.json";

type PetState = "idle" | "welcome" | "thinking" | "executing" | "resting" | "done" | "remind";

const desktop = (window as any).xiadie;

function Pet() {
  const canvasWrap = useRef<HTMLDivElement>(null);
  const [bubble, setBubble] = useState<string | null>("你好呀，我是遐蝶~");
  const [modelReady, setModelReady] = useState(false);
  const [modelFailed, setModelFailed] = useState(false);
  const appRef = useRef<any>(null);
  const modelRef = useRef<any>(null);
  const latestState = useRef<{ state: PetState; cluster: string }>({ state: "idle", cluster: "neutral" });
  const lastInteract = useRef(0);
  const bumpIdle = () => { lastInteract.current = Date.now(); };

  // 初次欢迎气泡自动消失
  useEffect(() => {
    const t = setTimeout(() => setBubble(null), 3500);
    return () => clearTimeout(t);
  }, []);

  // 主窗口状态联动：工作模式控制动作，后端 cluster 独立控制面部表情。
  useEffect(() => {
    desktop?.onPetState?.((p: { state: PetState; bubble?: string; cluster?: string }) => {
      if (p.bubble) {
        setBubble(p.bubble);
        setTimeout(() => setBubble(null), 3000);
      }
      latestState.current = {
        state: p.state,
        cluster: p.cluster || latestState.current.cluster || "neutral",
      };
      reactToState(modelRef.current, latestState.current.state, latestState.current.cluster);
      bumpIdle();
    });
  }, []);

  // idle 只做轻微动作，不再随机换脸，避免覆盖后端情绪簇这份唯一真相。
  useEffect(() => {
    if (!modelReady) return;
    const id = setInterval(() => {
      const m = modelRef.current;
      if (!m || m.__thinkTimer) return;
      if (Date.now() - lastInteract.current < 5000) return;
      perk(m);
    }, 9000);
    return () => clearInterval(id);
  }, [modelReady]);

  // 加载 Live2D 模型（失败即回退占位）
  useEffect(() => {
    let disposed = false;
    async function load() {
      const core = (window as any).Live2DCubismCore;
      if (!core) {
        setModelFailed(true);
        return;
      }
      try {
        const PIXI = await import("pixi.js");
        (window as any).PIXI = PIXI;
        const { Live2DModel } = await import("pixi-live2d-display/cubism4");

        // 探测模型文件是否存在，避免 404 噪音
        const probe = await fetch(MODEL_URL, { method: "GET" });
        if (!probe.ok) throw new Error("model missing");

        const app = new PIXI.Application({
          width: 280,
          height: 380,
          backgroundAlpha: 0,
          antialias: true,
        });
        if (disposed) return;
        canvasWrap.current!.appendChild(app.view as HTMLCanvasElement);
        appRef.current = app;

        const model = await Live2DModel.from(MODEL_URL);
        if (disposed) return;
        modelRef.current = model;
        (window as any).__petModel = model; // 本地调试句柄（可在控制台调表情/focus）
        app.stage.addChild(model);

        // 适配窗口大小
        const scale = Math.min(280 / model.width, 380 / model.height) * 0.95;
        model.scale.set(scale);
        model.anchor.set(0.5, 0.5);
        model.position.set(PET_CX, PET_CY);
        // 记录静止基准，供 perk 回弹动画使用
        (model as any).__baseScale = scale;
        (model as any).__baseY = PET_CY;
        reactToState(model, latestState.current.state, latestState.current.cluster);

        // 点击模型 → 打开主窗口（并做点击动作/表情）
        model.on("pointertap", () => {
          reactToState(model, "welcome", latestState.current.cluster);
          bumpIdle();
          desktop?.openMain?.();
        });

        setModelReady(true);
      } catch (e) {
        console.warn("Live2D 模型加载失败，使用占位：", e);
        setModelFailed(true);
      }
    }
    load();
    return () => {
      disposed = true;
      try {
        appRef.current?.destroy(true);
      } catch {
        /* ignore */
      }
    };
  }, []);

  // 拖拽移动窗口 vs 单击打开主窗口
  const drag = useRef<{ x: number; y: number; moved: boolean } | null>(null);
  function onDown(e: React.PointerEvent) {
    if (e.button !== 0) return;
    drag.current = { x: e.screenX, y: e.screenY, moved: false };
    (e.target as HTMLElement).setPointerCapture(e.pointerId);
  }
  function onMove(e: React.PointerEvent) {
    if (!drag.current) return;
    const dx = e.screenX - drag.current.x;
    const dy = e.screenY - drag.current.y;
    if (Math.abs(dx) + Math.abs(dy) > 3) {
      drag.current.moved = true;
      desktop?.dragPet?.(dx, dy);
      drag.current.x = e.screenX;
      drag.current.y = e.screenY;
    }
  }
  function onUp(e: React.PointerEvent) {
    const d = drag.current;
    drag.current = null;
    (e.target as HTMLElement).releasePointerCapture?.(e.pointerId);
    // 未拖动视为单击：打开主窗口（占位模式下也生效）
    if (d && !d.moved) {
      if (!modelReady) {
        desktop?.openMain?.();
      }
      // 有模型时点击由 model.pointertap 处理，避免重复
    }
  }
  function onContext(e: React.MouseEvent) {
    e.preventDefault();
    desktop?.showPetMenu?.();
  }

  return (
    <div
      className="pet"
      onPointerDown={onDown}
      onPointerMove={onMove}
      onPointerUp={onUp}
      onContextMenu={onContext}
    >
      {bubble && <div className="bubble">{bubble}</div>}

      <div ref={canvasWrap} className="canvas-wrap" style={{ display: modelReady ? "block" : "none" }} />

      {!modelReady && (
        <div className="placeholder">
          <div className="butterfly">🦋</div>
          <div className="pname">遐蝶</div>
          {modelFailed && (
            <div className="phint">
              未找到 Live2D 模型
              <br />
              点击我打开主窗口
            </div>
          )}
        </div>
      )}
    </div>
  );
}

// 遐蝶模型：11 个表情。索引按 cat.cdi3.json 的显示名对应：
// 0 中性 · 1 生气 · 2 哭 · 3 笑 · 4 脸红 · 5 调皮 · 6 星星眼 · 7 委屈 · 8 睡觉 · 9 无语 · 10 问号
// （模型自带的 4 个 CAT_motion 只切耳朵/蝴蝶装饰且 Loop，不用于表情动效；头身动效改用 perk + focus）

// 模型在画布中的静止中心
const PET_CX = 140;
const PET_CY = 200;

function setExpression(model: any, idx: number) {
  try {
    model?.expression?.(idx);
  } catch {
    // 模型或指定表情缺失时回退中性；不影响文字聊天和其余动作。
    try {
      model?.expression?.(0);
    } catch {
      /* ignore */
    }
  }
}

// 切表情时的"精神一下"：轻微下沉回弹 + 交替左右微倾（用 sprite 变换，稳）
function perk(model: any) {
  if (!model) return;
  model.__perkGeneration = (model.__perkGeneration ?? 0) + 1;
  const generation = model.__perkGeneration;
  const bs = model.__baseScale ?? model.scale.x;
  const by = model.__baseY ?? model.position.y;
  model.__perkDir = model.__perkDir === 1 ? -1 : 1;
  const dir = model.__perkDir;
  const DUR = 480;
  const start = performance.now();
  const step = (now: number) => {
    if (model.__perkGeneration !== generation) return;
    const t = Math.min(1, (now - start) / DUR);
    const e = Math.sin(t * Math.PI); // 0→1→0 的柔和弹跳
    model.scale.set(bs * (1 + 0.045 * e));
    model.position.y = by + 8 * e;
    model.rotation = 0.035 * e * dir;
    if (t < 1) requestAnimationFrame(step);
    else {
      model.scale.set(bs);
      model.position.y = by;
      model.rotation = 0;
    }
  };
  requestAnimationFrame(step);
}

// 思考中：头部缓慢左右歪（focus 驱动真实头身角度参数），像在托腮思考
function startThinking(model: any) {
  if (!model) return;
  stopThinking(model);
  let side = 1;
  const look = () => {
    try {
      model.focus(PET_CX + 55 * side, PET_CY - 95);
    } catch {
      /* ignore */
    }
    side *= -1;
  };
  look();
  model.__thinkTimer = setInterval(look, 1400);
}

function stopThinking(model: any) {
  if (model?.__thinkTimer) {
    clearInterval(model.__thinkTimer);
    model.__thinkTimer = null;
  }
  try {
    model?.focus?.(PET_CX, PET_CY); // 头回正
  } catch {
    /* ignore */
  }
}

// 模式只决定动作；cluster 只决定表情，二者不得互相覆盖。
function reactToState(model: any, state: PetState, cluster: string) {
  if (!model) return;
  setExpression(model, getClusterPresentation(cluster).expression);
  if (state === "thinking") {
    startThinking(model);
    perk(model);
    return;
  }
  stopThinking(model);
  if (state === "resting" || state === "idle") return;
  if (state === "executing" || state === "remind" || state === "welcome" || state === "done") {
    perk(model);
  }
}

createRoot(document.getElementById("pet-root")!).render(<Pet />);
