// 轻量全局状态：当前视图、当前会话、当前模型、刷新信号、toast。
// 不引第三方状态库，避免"所有状态堆在巨型 App.tsx"（需求 10 前端边界）。
import { useCallback, useEffect, useState } from "react";
import * as api from "./api";

export type View =
  | "chat"
  | "tasks"
  | "life"
  | "memories"
  | "files"
  | "tools"
  | "settings";

export type Mode = "companion" | "thinking" | "executing" | "resting";

let toastCb: ((msg: string) => void) | null = null;
export function toast(msg: string) {
  toastCb?.(msg);
}
export function useToast() {
  const [msg, setMsg] = useState<string | null>(null);
  useEffect(() => {
    toastCb = (m: string) => {
      setMsg(m);
      setTimeout(() => setMsg(null), 2400);
    };
    return () => {
      toastCb = null;
    };
  }, []);
  return msg;
}

export function useCurrentModel() {
  const [model, setModel] = useState<api.CurrentModel | null>(null);
  const refresh = useCallback(() => {
    api.getCurrentModel().then(setModel).catch(() => setModel(null));
  }, []);
  useEffect(refresh, [refresh]);
  return { model, refresh };
}
