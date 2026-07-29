import { useEffect, useRef, useState } from "react";
import * as api from "./../api";
import { Mode, toast } from "./../store";
import { memoryNoticeText, shouldShowMemoryNotice } from "../memoryNotice.mjs";
import {
  memoryObserverPollDelay,
  shouldContinueMemoryObserverPolling,
} from "../observerPolling.mjs";
import { TurnIngressBuffer, buildTurnEnvelopeContent } from "../turnIngressBuffer.mjs";
import { ReplyPresentationBuffer } from "../replyPresentation.mjs";

interface Props {
  sessionId: string | null;
  focusMessageId?: string | null;
  onMode: (m: Mode) => void;
  companionCluster?: string;
  onCompanionState: (state: api.CompanionState | null) => void;
  onSessionsChanged: () => void;
}

interface Streaming {
  text: string;
  phase?: "retrieval" | "generation" | "persistence" | "completed";
}

interface ActiveChatRequest {
  controller: AbortController;
  cancelToken: string;
  chatNonce: string;
  sessionId: string;
}

interface PendingGrant {
  preview: api.KnowledgeGrantPreflight;
  content: string;
  requestNonce: string;
  regenerate: boolean;
  locationChanged: boolean;
  activeSessionId: string;
  ingressMessages?: api.TurnIngressMessage[];
  imageAuthorization?: ImageAuthorization;
}

interface ImageAuthorization {
  image_transmission_consent: boolean;
  image_provider_id: string;
  image_model: string;
  image_location_revision: number;
}

type GrantAction = "allow_once" | "skip" | "always_allow" | "local_only";

type BufferedIngressEntry = api.TurnIngressMessage & {
  sessionId: string;
  attachments: api.ChatAttachmentResult[];
};

type PersonaMode = "companionship" | "focused_work";
type PersonaStyle = NonNullable<api.ChatRequestOptions["persona_style"]>;
const DEFAULT_PERSONA_STYLE: PersonaStyle = {
  address_style: "natural",
  detail_level: "balanced",
  poetic_level: "balanced",
  proactivity_level: "balanced",
};

export function ChatView({ sessionId, focusMessageId, onMode, companionCluster, onCompanionState, onSessionsChanged }: Props) {
  const [messages, setMessages] = useState<api.Message[]>([]);
  const [input, setInput] = useState("");
  const [streaming, setStreaming] = useState<Streaming | null>(null);
  const [errorCard, setErrorCard] = useState<{ msg: string; hint: string } | null>(null);
  const [memoryNotice, setMemoryNotice] = useState<string | null>(null);
  const [pendingGrant, setPendingGrant] = useState<PendingGrant | null>(null);
  const [grantBusy, setGrantBusy] = useState(false);
  // 附件三态：上传中 / 就绪 / 失败。失败 chip 不参与发送，可单独移除
  type PendingAttachment =
    | { localId: string; filename: string; status: "uploading" }
    | { localId: string; filename: string; status: "ready"; result: api.ChatAttachmentResult }
    | { localId: string; filename: string; status: "error"; error: string };
  const [pendingAttachments, setPendingAttachments] = useState<PendingAttachment[]>([]);
  const [attachmentBusy, setAttachmentBusy] = useState(false);
  const [cieEnabled, setCieEnabled] = useState(false);
  const [ingressCount, setIngressCount] = useState(0);
  const [personaMode, setPersonaMode] = useState<PersonaMode>("companionship");
  const [personaStyle, setPersonaStyle] = useState<PersonaStyle>(DEFAULT_PERSONA_STYLE);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const scrollRef = useRef<HTMLDivElement>(null);
  const memoryWatchId = useRef(0);
  const noticeTimer = useRef<number | null>(null);
  const activeSessionRef = useRef<string | null>(sessionId);
  const activeRequestRef = useRef<ActiveChatRequest | null>(null);
  const replyPresentationRef = useRef<ReplyPresentationBuffer | null>(null);
  const ingressWindowId = useRef(`window_${newRequestNonce().replace(/-/g, "_")}`);
  const flushIngressHandler = useRef<(
    scope: string, entries: BufferedIngressEntry[], reason: string,
  ) => Promise<void>>(async () => undefined);
  const ingressBuffer = useRef<TurnIngressBuffer<BufferedIngressEntry> | null>(null);
  if (ingressBuffer.current === null) {
    ingressBuffer.current = new TurnIngressBuffer<BufferedIngressEntry>({
      onFlush: (scope: string, entries: BufferedIngressEntry[], reason: string) =>
        flushIngressHandler.current(scope, entries, reason),
      onPendingChange: (scope: string, count: number) => {
        if (scope.startsWith(`${activeSessionRef.current}:`)) setIngressCount(count);
      },
    });
  }
  const busy = streaming !== null || grantBusy || pendingGrant !== null || attachmentBusy;
  const composerBusy = grantBusy || pendingGrant !== null || attachmentBusy
    || (streaming !== null && !cieEnabled);

  useEffect(() => {
    api.getCieSettings()
      .then((settings) => setCieEnabled(settings.enabled))
      .catch(() => setCieEnabled(false));
  }, []);

  useEffect(() => {
    if (!sessionId) return;
    const key = `xiadie-persona-v1:${sessionId}`;
    try {
      const saved = JSON.parse(window.sessionStorage.getItem(key) || "null");
      setPersonaMode(saved?.mode === "focused_work" ? "focused_work" : "companionship");
      setPersonaStyle({ ...DEFAULT_PERSONA_STYLE, ...(saved?.style || {}) });
    } catch {
      setPersonaMode("companionship");
      setPersonaStyle(DEFAULT_PERSONA_STYLE);
    }
  }, [sessionId]);

  useEffect(() => {
    if (!sessionId) return;
    try {
      window.sessionStorage.setItem(
        `xiadie-persona-v1:${sessionId}`,
        JSON.stringify({ mode: personaMode, style: personaStyle }),
      );
    } catch {
      /* sessionStorage 不可用时只影响偏好持久化，不阻断聊天 */
    }
  }, [sessionId, personaMode, personaStyle]);

  useEffect(() => {
    const previousSession = activeSessionRef.current;
    activeSessionRef.current = sessionId;
    if (previousSession && previousSession !== sessionId) {
      replyPresentationRef.current?.cancel();
      replyPresentationRef.current = null;
      const previousScope = `${previousSession}:${ingressWindowId.current}`;
      void ingressBuffer.current!.flush(previousScope, "explicit_send");
      setIngressCount(0);
      setStreaming(null);
      void stopActiveGeneration();
    }
    if (!sessionId) {
      setMessages([]);
      return;
    }
    api.listMessages(sessionId).then(setMessages);
    setErrorCard(null);
    setMemoryNotice(null);
    setPendingGrant(null);
    setGrantBusy(false);
    memoryWatchId.current += 1;
    if (noticeTimer.current !== null) window.clearTimeout(noticeTimer.current);
  }, [sessionId]);

  useEffect(() => api.desktop?.onProactiveChatMessage?.((item) => {
    if (!sessionId || item.session_id !== sessionId) return;
    api.listMessages(sessionId).then(setMessages).catch(() => undefined);
    onSessionsChanged();
  }), [sessionId, onSessionsChanged]);

  useEffect(() => () => {
    memoryWatchId.current += 1;
    if (noticeTimer.current !== null) window.clearTimeout(noticeTimer.current);
    ingressBuffer.current?.dispose();
    replyPresentationRef.current?.cancel();
    replyPresentationRef.current = null;
    const active = activeRequestRef.current;
    if (active) {
      void api.cancelChat(active.cancelToken).then((result) => {
        if (result.accepted) active.controller.abort();
      }).catch(() => undefined);
    }
  }, []);

  useEffect(() => {
    if (!focusMessageId || !messages.some((message) => message.id === focusMessageId)) return;
    requestAnimationFrame(() => {
      document.getElementById(`message-${focusMessageId}`)?.scrollIntoView({
        behavior: "smooth",
        block: "center",
      });
    });
  }, [focusMessageId, messages]);

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: "smooth" });
  }, [messages, streaming]);

  async function handleFileSelect(files: FileList | null) {
    if (!files || files.length === 0) return;
    setAttachmentBusy(true);
    let probedCapability: api.VisionCapability | null = null;
    const existingImages = pendingAttachments.filter(
      (item) => item.status === "ready" && item.result.attachment_kind === "image",
    ).length;
    let selectedImages = 0;
    for (const file of Array.from(files)) {
      const lower = file.name.toLowerCase();
      const isImage = /\.(png|jpe?g)$/.test(lower);
      if (!/\.(txt|md|pdf|docx|png|jpe?g)$/.test(lower)) {
        toast("仅支持 txt/md/pdf/docx/png/jpg/jpeg 文件");
        continue;
      }
      if (isImage && existingImages + selectedImages >= 4) {
        toast("每轮最多选择 4 张图片");
        continue;
      }
      const sizeLimit = isImage ? 5 * 1024 * 1024 : 10 * 1024 * 1024;
      if (file.size > sizeLimit) {
        toast(`${file.name} 超过 ${isImage ? 5 : 10} MiB 限制`);
        continue;
      }
      if (isImage) {
        try {
          if (!probedCapability) {
            probedCapability = await api.getVisionCapability();
            if (probedCapability.status === "unknown") {
              toast("正在验证当前模型的真实图片能力…");
              probedCapability = await api.probeVisionCapability();
            }
          }
          if (probedCapability.status !== "supported") {
            toast("当前模型未通过图片能力验证；没有发送图片，也不会假装看到了图片");
            continue;
          }
          selectedImages += 1;
        } catch (error) {
          toast(error instanceof api.ApiError ? error.message : "图片能力验证失败，请稍后重试");
          continue;
        }
      }
      // 先 push uploading 状态，让用户看到正在处理
      const localId = `${file.name}-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
      setPendingAttachments((prev) => [...prev, { localId, filename: file.name, status: "uploading" }]);
      try {
        const result = await api.uploadChatAttachment(file);
        setPendingAttachments((prev) => prev.map((a) =>
          a.localId === localId
            ? { localId, filename: file.name, status: "ready", result }
            : a,
        ));
      } catch (error: any) {
        // 按 status code 分类 toast，优先使用后端返回的中文 message
        // 后端 upload_chat_attachment 已统一返回 {code, message} 结构化格式
        const e = error as { status?: number; message?: string };
        let friendly: string;
        if (e.status === 401) {
          friendly = "令牌失效，请重启遐蝶";
        } else if (e.status === 413) {
          friendly = e.message || "文件超过 10 MiB 限制";
        } else if (e.status === 415) {
          friendly = e.message || "文件类型不支持";
        } else if (e.status && e.status >= 500) {
          friendly = `后端异常：${e.message || "服务异常"}`;
        } else if (/Failed to fetch|NetworkError|ERR_CONNECTION/i.test(e.message || "")) {
          friendly = "无法连接到后端，请确认遐蝶已正常启动";
        } else {
          friendly = e.message || "上传失败";
        }
        setPendingAttachments((prev) => prev.map((a) =>
          a.localId === localId
            ? { localId, filename: file.name, status: "error", error: friendly }
            : a,
        ));
        toast(`${file.name}：${friendly}`);
      }
    }
    setAttachmentBusy(false);
  }

  async function removeAttachment(localId: string) {
    const target = pendingAttachments.find((a) => a.localId === localId);
    // 乐观更新：先从 UI 移除
    setPendingAttachments((prev) => prev.filter((a) => a.localId !== localId));
    // ready 状态的附件已上传到后端，需要调用 DELETE 清理，避免孤儿数据
    // uploading/error 状态没有后端记录，直接移除即可
    if (target?.status === "ready") {
      try {
        await api.deleteChatAttachment(target.result.id);
      } catch {
        // 删除失败不阻塞 UI，启动时 GC 会兜底清理（cleanup_orphan_attachments）
      }
    }
  }

  async function send(regenerate = false, explicitBoundary = false) {
    if (!sessionId || (busy && !(cieEnabled && streaming && !regenerate))) return;
    let content = regenerate ? lastUserContent() : input.trim();
    // 只有 ready 状态的附件参与发送；uploading 由 attachmentBusy 阻塞，error 不发送
    const readyAttachments = pendingAttachments.filter(
      (a): a is Extract<PendingAttachment, { status: "ready" }> => a.status === "ready",
    );
    if (!content && readyAttachments.length === 0) return;
    if (!regenerate && cieEnabled && streaming) {
      const stopped = await stopActiveGeneration();
      if (!stopped) return;
    }
    if (!regenerate && cieEnabled) {
      const boundary = content === "/stop"
        ? "stop"
        : explicitBoundary ? "explicit_send" : "idle_timeout";
      queueIngress(content, readyAttachments, boundary);
      return;
    }
    const requestNonce = newRequestNonce();
    memoryWatchId.current += 1;
    setErrorCard(null);
    setMemoryNotice(null);
    setGrantBusy(true);
    const attachmentIds = !regenerate && readyAttachments.length > 0
      ? readyAttachments.map((a) => a.result.id)
      : undefined;
    try {
      const imageAuthorization = regenerate
        ? undefined
        : await authorizeImages(readyAttachments.map((item) => item.result));
      const preview = await api.preflightKnowledgeTransmission(
        sessionId, requestNonce, content, attachmentIds,
      );
      // 纯附件无文字消息：preflight 直接返回 not_needed，无需授权，直接发
      if (preview.status === "pending" && preview.id) {
        setPendingGrant({
          preview,
          content,
          requestNonce,
          regenerate,
          locationChanged: rememberProviderLocation(preview.provider),
          activeSessionId: sessionId,
          imageAuthorization,
        });
        return;
      }
      setGrantBusy(false);
      await runChat({ content, requestNonce, regenerate, activeSessionId: sessionId, imageAuthorization });
    } catch (error) {
      showRequestError(error, "无法检查资料发送范围，请稍后重试。");
    } finally {
      setGrantBusy(false);
    }
  }

  async function handleGrant(action: GrantAction) {
    if (!pendingGrant || grantBusy || !pendingGrant.preview.id) return;
    const pending = pendingGrant;
    const grantId = pending.preview.id as string;
    const activeSessionId = pending.activeSessionId;
    setGrantBusy(true);
    setErrorCard(null);
    try {
      let token: string | undefined;
      let skipRestricted = false;
      if (action === "skip") {
        await api.denyKnowledgeTransmissionGrant(grantId);
        skipRestricted = true;
      } else {
        const resolved = await api.resolveKnowledgeTransmissionGrant({
          grant_id: grantId,
          action,
          session_id: activeSessionId,
          request_nonce: pending.requestNonce,
          content: pending.content,
        });
        token = resolved.token || undefined;
        skipRestricted = action === "local_only";
      }
      setPendingGrant(null);
      setGrantBusy(false);
      await runChat({
        content: pending.content,
        requestNonce: pending.requestNonce,
        regenerate: pending.regenerate,
        activeSessionId,
        ingressMessages: pending.ingressMessages,
        imageAuthorization: pending.imageAuthorization,
        token,
        skipRestricted,
      });
    } catch (error) {
      showRequestError(error, "授权状态可能已经变化，请关闭提示后重新发送。");
    } finally {
      setGrantBusy(false);
    }
  }

  async function cancelGrant() {
    if (!pendingGrant?.preview.id || grantBusy) return;
    const grantId = pendingGrant.preview.id;
    setGrantBusy(true);
    try {
      await api.denyKnowledgeTransmissionGrant(grantId);
      setPendingGrant(null);
    } catch (error) {
      showRequestError(error, "无法关闭这次授权，请稍后重试。");
    } finally {
      setGrantBusy(false);
    }
  }

  async function runChat(options: {
    content: string;
    requestNonce: string;
    regenerate: boolean;
    activeSessionId?: string;
    ingressMessages?: api.TurnIngressMessage[];
    token?: string;
    skipRestricted?: boolean;
    imageAuthorization?: ImageAuthorization;
  }) {
    const activeSessionId = options.activeSessionId ?? sessionId;
    if (!activeSessionId) return;
    const { content, requestNonce, regenerate, ingressMessages, token, skipRestricted = false } = options;
    const activeInView = () => activeSessionRef.current === activeSessionId;
    replyPresentationRef.current?.cancel();
    const presentation = cieEnabled ? new ReplyPresentationBuffer({
      onDisplay: (text) => {
        if (!activeInView()) return;
        setStreaming((current) => ({
          text: (current?.text ?? "") + text,
          phase: current?.phase,
        }));
      },
      onReplace: (text) => {
        if (!activeInView()) return;
        setStreaming((current) => ({ text, phase: current?.phase }));
      },
    }) : null;
    replyPresentationRef.current = presentation;
    const requestControl = cieEnabled ? {
      controller: new AbortController(),
      cancelToken: newRequestNonce(),
      chatNonce: newRequestNonce(),
      sessionId: activeSessionId,
    } : null;
    if (requestControl) activeRequestRef.current = requestControl;
    const clearActiveRequest = () => {
      if (requestControl && activeRequestRef.current?.cancelToken === requestControl.cancelToken) {
        activeRequestRef.current = null;
      }
    };
    // 本地立即显示用户消息（含附件卡片），不等后端刷新。只展示 ready 的附件
    const readyForLocal = !regenerate
      ? pendingAttachments.filter(
          (a): a is Extract<PendingAttachment, { status: "ready" }> => a.status === "ready",
        )
      : [];
    const localAttachments = readyForLocal.length > 0
      ? readyForLocal.map((a) => ({ ...a.result }))
      : undefined;
    if (!regenerate && !ingressMessages) {
      setMessages((m) => [...m, localMsg("user", content, localAttachments)]);
      setInput("");
    }
    if (activeInView()) {
      setStreaming({ text: "" });
      onMode("thinking");
      api.desktop?.setPetState?.("thinking", "让我想想…", companionCluster);
    }

    await api.streamChat(
      activeSessionId,
      content,
      {
        onDelta: (t) => {
          if (!activeInView()) return;
          if (presentation) presentation.push(t);
          else setStreaming((s) => (s ? { ...s, text: s.text + t } : { text: t }));
        },
        onFinal: (final) => {
          if (!activeInView()) return;
          if (presentation) presentation.finish(final.content);
          setStreaming({ text: final.content, phase: "completed" });
          if (replyPresentationRef.current === presentation) replyPresentationRef.current = null;
        },
        onPhase: (phase) => {
          if (!activeInView()) return;
          setStreaming((current) => ({ text: current?.text ?? "", phase }));
        },
        onCancelled: () => {
          presentation?.cancel();
          if (replyPresentationRef.current === presentation) replyPresentationRef.current = null;
          clearActiveRequest();
          if (!activeInView()) return;
          setStreaming(null);
          onMode("companion");
          api.desktop?.setPetState?.("idle", undefined, companionCluster);
        },
        onAbort: () => {
          presentation?.cancel();
          if (replyPresentationRef.current === presentation) replyPresentationRef.current = null;
          clearActiveRequest();
          if (!activeInView()) return;
          setStreaming(null);
          onMode("companion");
          api.desktop?.setPetState?.("idle", undefined, companionCluster);
        },
        onError: (msg, hint) => {
          presentation?.cancel();
          if (replyPresentationRef.current === presentation) replyPresentationRef.current = null;
          clearActiveRequest();
          if (!activeInView()) return;
          setStreaming(null);
          const finalHint = options.regenerate
            ? (hint ? hint + " · 旧回复已保留" : "旧回复已保留")
            : hint;
          setErrorCard({ msg, hint: finalHint });
          onMode("companion");
          api.desktop?.setPetState?.("idle", undefined, companionCluster);
          // 重新生成失败时重新加载消息列表，恢复旧回复的显示
          if (options.regenerate && sessionId) {
            api.listMessages(sessionId).then(setMessages);
          }
        },
        onDone: (d) => {
          if (replyPresentationRef.current === presentation) replyPresentationRef.current = null;
          clearActiveRequest();
          if (activeInView()) {
            setStreaming(null);
            onMode("companion");
            onCompanionState(d.companion_state);
            api.listMessages(activeSessionId).then(setMessages);
          }
          onSessionsChanged();
          if (d.memory_observation?.id && d.memory_observation.status === "queued") {
            void watchMemoryResult(d.memory_observation.id);
          }
        },
      },
      {
        persona_mode: personaMode,
        persona_style: personaStyle,
        regenerate,
        request_nonce: requestNonce,
        knowledge_grant_token: token,
        knowledge_skip_restricted: skipRestricted,
        attachment_ids: readyForLocal.length > 0
          ? readyForLocal.map((a) => a.result.id)
          : undefined,
        ingress_messages: ingressMessages,
        chat_nonce: requestControl?.chatNonce,
        cancel_token: requestControl?.cancelToken,
        ...options.imageAuthorization,
        signal: requestControl?.controller.signal,
      },
    );
    // 发送成功后清空附件
    if (!options.regenerate) {
      setPendingAttachments([]);
    }
  }

  async function stopActiveGeneration(): Promise<boolean> {
    const active = activeRequestRef.current;
    if (!active) return true;
    try {
      const result = await api.cancelChat(active.cancelToken);
      if (!result.found) {
        setErrorCard({ msg: "暂时无法停止", hint: "请求仍在建立，请稍后再试。" });
        return false;
      }
      if (!result.accepted) {
        setErrorCard({ msg: "回复正在保存", hint: "保存完成后即可继续补充，不会删除已有回复。" });
        return false;
      }
      replyPresentationRef.current?.cancel();
      replyPresentationRef.current = null;
      active.controller.abort();
      if (activeRequestRef.current?.cancelToken === active.cancelToken) {
        activeRequestRef.current = null;
      }
      setStreaming(null);
      onMode("companion");
      api.desktop?.setPetState?.("idle", undefined, companionCluster);
      return true;
    } catch (error) {
      showRequestError(error, "无法确认停止状态，原回复不会被误删。");
      return false;
    }
  }

  function queueIngress(
    content: string,
    attachments: Extract<PendingAttachment, { status: "ready" }>[],
    boundary: api.TurnIngressMessage["boundary"],
  ) {
    if (!sessionId) return;
    const imageItems = attachments.filter((item) => item.result.attachment_kind === "image");
    const imageLocation = imageItems[0]?.result.vision_capability?.provider_location;
    const apiEntry: api.TurnIngressMessage = {
      client_message_id: `message_${newRequestNonce().replace(/-/g, "_")}`,
      window_id: ingressWindowId.current,
      content,
      attachment_ids: attachments.map((item) => item.result.id),
      authorization_scope: imageItems.length === 0
        ? "local_text_only"
        : imageLocation === "local" ? "local_image" : "remote_image_once",
      queued_at_ms: Date.now(),
      boundary,
    };
    const entry: BufferedIngressEntry = {
      ...apiEntry,
      sessionId,
      attachments: attachments.map((item) => ({ ...item.result })),
    };
    setMessages((current) => [
      ...current,
      localMsg("user", content, entry.attachments.length > 0 ? entry.attachments : undefined),
    ]);
    setInput("");
    setPendingAttachments([]);
    const scope = `${sessionId}:${ingressWindowId.current}`;
    setIngressCount(ingressBuffer.current!.enqueue(scope, entry));
  }

  async function flushBufferedEntries(
    _scope: string, entries: BufferedIngressEntry[], _reason: string,
  ) {
    if (entries.length === 0) return;
    setIngressCount(0);
    const activeSessionId = entries[0].sessionId;
    const ingressMessages = entries.map(({ sessionId: _sessionId, attachments: _attachments, ...item }) => item);
    const content = buildTurnEnvelopeContent(ingressMessages);
    const attachmentIds = ingressMessages.flatMap((item) => item.attachment_ids);
    const requestNonce = newRequestNonce();
    memoryWatchId.current += 1;
    setErrorCard(null);
    setMemoryNotice(null);
    setGrantBusy(true);
    try {
      const imageAuthorization = await authorizeImages(
        entries.flatMap((item) => item.attachments),
      );
      const preview = await api.preflightKnowledgeTransmission(
        activeSessionId, requestNonce, content, attachmentIds.length > 0 ? attachmentIds : undefined,
      );
      if (preview.status === "pending" && preview.id) {
        setPendingGrant({
          preview, content, requestNonce, regenerate: false,
          locationChanged: rememberProviderLocation(preview.provider),
          activeSessionId, ingressMessages,
          imageAuthorization,
        });
        return;
      }
      setGrantBusy(false);
      await runChat({
        content, requestNonce, regenerate: false, activeSessionId, ingressMessages,
        imageAuthorization,
      });
    } catch (error) {
      if (error instanceof api.ApiError && ["cie_disabled", "image_consent_declined"].includes(error.code || "")) {
        if (error.code === "cie_disabled") setCieEnabled(false);
        const persisted = await api.listMessages(activeSessionId).catch(() => []);
        if (activeSessionRef.current === activeSessionId) {
          setMessages(persisted);
          setInput(content);
          setPendingAttachments(entries.flatMap((item) => item.attachments.map((result) => ({
            localId: `restored-${result.id}`,
            filename: result.filename,
            status: "ready" as const,
            result,
          }))));
        }
        showRequestError(
          error,
          error.code === "cie_disabled"
            ? "CIE 已关闭，消息已恢复到输入框，可按旧单消息路径重新发送。"
            : "图片仍保留在输入框；只有再次发送并确认后才会传给模型。",
        );
        return;
      }
      showRequestError(error, "连续消息发送失败，请稍后重试。");
      throw error;
    } finally {
      setGrantBusy(false);
    }
  }
  flushIngressHandler.current = flushBufferedEntries;

  async function authorizeImages(
    attachments: api.ChatAttachmentResult[],
  ): Promise<ImageAuthorization | undefined> {
    const images = attachments.filter((item) => item.attachment_kind === "image");
    if (images.length === 0) return undefined;
    const uploaded = images[0].vision_capability;
    if (!uploaded || images.some((item) =>
      !item.vision_capability
      || item.vision_capability.provider_id !== uploaded.provider_id
      || item.vision_capability.model !== uploaded.model
      || item.vision_capability.provider_location_revision !== uploaded.provider_location_revision
    )) {
      throw new api.ApiError(409, "图片的发送目标不一致，请重新选择图片", "image_snapshot_mismatch");
    }
    const current = await api.getVisionCapability();
    if (
      current.status !== "supported"
      || current.provider_id !== uploaded.provider_id
      || current.model !== uploaded.model
      || current.provider_location_revision !== uploaded.provider_location_revision
    ) {
      throw new api.ApiError(
        409, "模型或 Provider 位置已变化，请重新选择图片", "image_authorization_snapshot_changed",
      );
    }
    const remote = current.provider_location !== "local";
    if (remote && !window.confirm(
      `仅本轮将 ${images.length} 张图片发送给 ${current.provider_id} / ${current.model}。\n`
      + "图片不会进入长期记忆或知识库，发送后本地临时原始字节会销毁。是否继续？",
    )) {
      throw new api.ApiError(409, "已取消本轮图片发送", "image_consent_declined");
    }
    return {
      image_transmission_consent: remote,
      image_provider_id: current.provider_id,
      image_model: current.model,
      image_location_revision: current.provider_location_revision,
    };
  }

  function showRequestError(error: unknown, fallbackHint: string) {
    const message = error instanceof api.ApiError ? error.message : "请求失败";
    setErrorCard({ msg: message, hint: fallbackHint });
  }

  async function watchMemoryResult(runId: string) {
    const watchId = ++memoryWatchId.current;
    const startedAt = Date.now();
    let consecutiveErrors = 0;
    while (
      memoryWatchId.current === watchId
      && shouldContinueMemoryObserverPolling(Date.now() - startedAt, consecutiveErrors)
    ) {
      try {
        const result = await api.getMemoryObserverResult(runId);
        consecutiveErrors = 0;
        if (result.status === "applied") {
          if (result.remembered_count > 0) showRememberedNotice(result.remembered_count);
          return;
        }
        if (result.status === "exhausted" || result.status === "skipped") return;
      } catch {
        consecutiveErrors += 1;
      }
      if (!shouldContinueMemoryObserverPolling(Date.now() - startedAt, consecutiveErrors)) return;
      const delay = memoryObserverPollDelay(Date.now() - startedAt);
      await new Promise((resolve) => window.setTimeout(resolve, delay));
    }
  }

  function showRememberedNotice(count: number) {
    const storageKey = "xiadie:last-memory-notice-at";
    let lastShownAt = Number.NaN;
    try {
      lastShownAt = Number(window.sessionStorage.getItem(storageKey));
    } catch {
      /* sessionStorage 不可用时仍允许本次轻提示 */
    }
    const now = Date.now();
    if (!shouldShowMemoryNotice(lastShownAt, now)) return;
    try {
      window.sessionStorage.setItem(storageKey, String(now));
    } catch {
      /* ignore */
    }
    setMemoryNotice(memoryNoticeText(count));
    if (noticeTimer.current !== null) window.clearTimeout(noticeTimer.current);
    noticeTimer.current = window.setTimeout(() => setMemoryNotice(null), 5200);
  }

  function lastUserContent(): string {
    for (let i = messages.length - 1; i >= 0; i--) {
      if (messages[i].role === "user") return messages[i].content;
    }
    return "";
  }

  async function makeTask() {
    const text = lastUserContent();
    if (!sessionId || !text) return;
    await api.createTask(text.slice(0, 40), sessionId);
    toast("已从本次对话创建任务");
  }

  return (
    <>
      <div className="messages" ref={scrollRef}>
        {messages.length === 0 && !streaming && (
          <div className="empty">
            <div className="empty-greeting">
              我是遐蝶，随时在这里。<br />
              聊点什么，或让我帮你记一个任务、存一条记忆都可以。
            </div>
            <div className="chat-starters" role="list">
              <button
                className="chat-starter-card"
                role="listitem"
                onClick={() => { setInput("今天想聊点什么？"); textareaRef.current?.focus(); }}
              >
                <span className="chat-starter-emoji" aria-hidden="true">🌸</span>
                <span className="chat-starter-text">今天想聊点什么？</span>
                <small className="chat-starter-hint">随便聊聊</small>
              </button>
              <button
                className="chat-starter-card"
                role="listitem"
                onClick={() => { setInput("帮我记一件事"); textareaRef.current?.focus(); }}
              >
                <span className="chat-starter-emoji" aria-hidden="true">📝</span>
                <span className="chat-starter-text">帮我记一件事</span>
                <small className="chat-starter-hint">功能引导</small>
              </button>
              <button
                className="chat-starter-card"
                role="listitem"
                onClick={() => { setInput("你在做什么呢？"); textareaRef.current?.focus(); }}
              >
                <span className="chat-starter-emoji" aria-hidden="true">💭</span>
                <span className="chat-starter-text">你在做什么呢？</span>
                <small className="chat-starter-hint">自由陪伴</small>
              </button>
            </div>
          </div>
        )}
        {messages.map((m) => (
          <MessageRow
            key={m.id}
            m={m}
            highlighted={m.id === focusMessageId}
            onFavorite={() => favorite(m, setMessages)}
          />
        ))}

        {streaming && (
          <div className="msg assistant">
            <div className="avatar">蝶</div>
            <div>
              <div className="bubble">
                {streaming.text || (
                  <span className="typing-dots">
                    <span>·</span>
                    <span>·</span>
                    <span>·</span>
                  </span>
                )}
              </div>
              <div className="streaming-status" role="status" aria-live="polite">
                {replyPhaseLabel(streaming.phase, Boolean(streaming.text))}
              </div>
            </div>
          </div>
        )}

        {memoryNotice && (
          <div className="memory-notice" role="status" aria-live="polite">
            <span aria-hidden="true">✦</span>
            <div>
              <div>{memoryNotice}</div>
              <small>可以在「记忆与关系」中查看、纠正或删除</small>
            </div>
          </div>
        )}

        {pendingGrant && (
          <KnowledgeGrantCard
            pending={pendingGrant}
            busy={grantBusy}
            onAction={(action) => void handleGrant(action)}
            onCancel={() => void cancelGrant()}
          />
        )}

        {errorCard && (
          <div className="card error">
            <div className="card-title">⚠ {errorCard.msg}</div>
            <div className="card-hint">{errorCard.hint}</div>
            <div style={{ marginTop: 8 }}>
              <button className="btn ghost" onClick={() => send(true)}>
                重试
              </button>
            </div>
          </div>
        )}
      </div>

      <div className="composer">
        <div className="persona-controls" aria-label="遐蝶对话方式">
          <label>
            <span>对话方式</span>
            <select
              value={personaMode}
              disabled={composerBusy}
              onChange={(event) => setPersonaMode(event.target.value as PersonaMode)}
            >
              <option value="companionship">陪伴</option>
              <option value="focused_work">技术工作</option>
            </select>
          </label>
          <label>
            <span>篇幅</span>
            <select
              value={personaStyle.detail_level}
              disabled={composerBusy}
              onChange={(event) => setPersonaStyle((current) => ({
                ...current, detail_level: event.target.value as PersonaStyle["detail_level"],
              }))}
            >
              <option value="concise">简短</option>
              <option value="balanced">适中</option>
              <option value="detailed">详细</option>
            </select>
          </label>
          <label>
            <span>诗意</span>
            <select
              value={personaStyle.poetic_level}
              disabled={composerBusy}
              onChange={(event) => setPersonaStyle((current) => ({
                ...current, poetic_level: event.target.value as PersonaStyle["poetic_level"],
              }))}
            >
              <option value="low">克制</option>
              <option value="balanced">适中</option>
              <option value="high">较多</option>
            </select>
          </label>
          <label>
            <span>主动性</span>
            <select
              value={personaStyle.proactivity_level}
              disabled={composerBusy}
              onChange={(event) => setPersonaStyle((current) => ({
                ...current, proactivity_level: event.target.value as PersonaStyle["proactivity_level"],
              }))}
            >
              <option value="reserved">克制</option>
              <option value="balanced">适中</option>
              <option value="engaged">积极</option>
            </select>
          </label>
          <label>
            <span>称呼</span>
            <select
              value={personaStyle.address_style}
              disabled={composerBusy}
              onChange={(event) => setPersonaStyle((current) => ({
                ...current, address_style: event.target.value as PersonaStyle["address_style"],
              }))}
            >
              <option value="natural">自然</option>
              <option value="ge_xia_low">低频阁下</option>
              <option value="name_if_known">已知姓名</option>
              <option value="none">不主动称呼</option>
            </select>
          </label>
        </div>
        {pendingAttachments.length > 0 && (
          <div className="attachment-chips">
            {pendingAttachments.map((a) => {
              if (a.status === "uploading") {
                return (
                  <span className="attachment-chip attachment-chip-loading" key={a.localId}>
                    <span className="attachment-chip-spinner" aria-hidden="true" />
                    {a.filename}
                  </span>
                );
              }
              if (a.status === "error") {
                return (
                  <span
                    className="attachment-chip attachment-chip-error"
                    key={a.localId}
                    title={a.error}
                  >
                    <span aria-hidden="true">✕</span>
                    {a.filename}
                    <button
                      className="attachment-chip-remove"
                      onClick={() => void removeAttachment(a.localId)}
                      title="移除"
                    >
                      ×
                    </button>
                  </span>
                );
              }
              return (
                <span className="attachment-chip" key={a.localId}>
                  {a.result.attachment_kind === "image" ? "🖼️" : "📎"} {a.filename}
                  <button
                    className="attachment-chip-remove"
                    onClick={() => void removeAttachment(a.localId)}
                    title="移除"
                  >
                    ×
                  </button>
                </span>
              );
            })}
          </div>
        )}
        <div className="composer-inner">
          <input
            ref={fileInputRef}
            type="file"
            multiple
            accept=".txt,.md,.pdf,.docx,.png,.jpg,.jpeg,image/png,image/jpeg"
            style={{ display: "none" }}
            onChange={(e) => {
              handleFileSelect(e.target.files);
              e.target.value = "";
            }}
          />
          <button
            className="attach-btn"
            disabled={!sessionId || composerBusy}
            onClick={() => fileInputRef.current?.click()}
            title="上传文件让遐蝶阅读"
          >
            📎
          </button>
          <textarea
            ref={textareaRef}
            rows={1}
            placeholder={sessionId ? "和遐蝶说点什么…" : "正在准备对话…"}
            value={input}
            disabled={!sessionId || composerBusy}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                send(false, e.ctrlKey || e.metaKey);
              }
            }}
          />
          <button
            className="send-btn"
            disabled={composerBusy || (!input.trim() && !pendingAttachments.some((a) => a.status === "ready"))}
            onClick={() => void send()}
          >
            {streaming && cieEnabled ? "补充" : ingressCount > 0 ? `➤ ${ingressCount}` : "➤"}
          </button>
          {streaming && cieEnabled && (
            <button className="send-btn" onClick={() => void stopActiveGeneration()}>停止</button>
          )}
        </div>
        <div className="msg-meta" style={{ marginTop: 8, paddingLeft: 4 }}>
          <button onClick={makeTask} disabled={!lastUserContent()}>
            ＋ 存为任务
          </button>
          <button onClick={() => send(true)} disabled={busy || ingressCount > 0 || !lastUserContent()}>
            ↻ 重新生成
          </button>
        </div>
      </div>
    </>
  );
}

function KnowledgeGrantCard({
  pending,
  busy,
  onAction,
  onCancel,
}: {
  pending: PendingGrant;
  busy: boolean;
  onAction: (action: GrantAction) => void;
  onCancel: () => void;
}) {
  const { preview } = pending;
  const remote = preview.provider.location !== "local";
  const dialogRef = useRef<HTMLElement>(null);
  const primaryRef = useRef<HTMLButtonElement>(null);
  const cancelRef = useRef(onCancel);
  cancelRef.current = onCancel;
  useEffect(() => {
    primaryRef.current?.focus();
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.preventDefault();
        cancelRef.current();
        return;
      }
      if (event.key !== "Tab" || !dialogRef.current) return;
      const controls = Array.from(dialogRef.current.querySelectorAll<HTMLElement>(
        "button:not(:disabled), [href], [tabindex]:not([tabindex='-1'])",
      ));
      if (!controls.length) return;
      const first = controls[0];
      const last = controls[controls.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    };
    document.addEventListener("keydown", handleKeyDown);
    return () => document.removeEventListener("keydown", handleKeyDown);
  }, []);
  return (
    <section ref={dialogRef} className="knowledge-grant-card" role="dialog" aria-modal="true"
      aria-labelledby="knowledge-grant-title" aria-describedby="knowledge-grant-description">
      <div className="knowledge-grant-head">
        <div className="knowledge-grant-icon" aria-hidden="true">◇</div>
        <div>
          <span className="knowledge-grant-eyebrow">相关资料</span>
          <h2 id="knowledge-grant-title">我找到一些相关资料（{preview.chunk_count} 条），可以发给我看看吗？</h2>
        </div>
        <button className="knowledge-grant-close" onClick={onCancel} disabled={busy} aria-label="取消">×</button>
      </div>

      <div className={`knowledge-grant-route ${remote ? "is-remote" : "is-local"}`}>
        <span>{remote ? "在线模型" : "本地模型"}</span>
        <strong>{preview.provider.id || "未知 Provider"} · {preview.provider.model}</strong>
        <small>
          位置：{locationText(preview.provider.location)} · 配置版本 {preview.provider.location_revision}
        </small>
      </div>
      {pending.locationChanged && (
        <div className="knowledge-grant-warning">模型位置或配置已变化，请重新确认本次发送范围。</div>
      )}
      {remote && (
        <p className="knowledge-grant-explain" id="knowledge-grant-description">
          这些资料会随本轮消息发给当前模型服务商，授权仅绑定这条消息、这个模型与当前资料版本。
        </p>
      )}

      <div className="knowledge-grant-documents">
        {preview.documents.map((document) => (
          <div className="knowledge-grant-document" key={document.id}>
            <span className="knowledge-grant-file" aria-hidden="true">▧</span>
            <div>
              <strong>{document.name}</strong>
              <small>{document.chunk_count} 个片段 · 约 {document.token_estimate} tokens</small>
            </div>
            <div className="knowledge-grant-badges">
              {document.sensitivity === "sensitive" && <span className="is-sensitive">敏感</span>}
              <span>{policyText(document.policy)}</span>
            </div>
          </div>
        ))}
      </div>

      <div className="knowledge-grant-summary">
        <span>{preview.document_count} 份文档 · {preview.chunk_count} 个片段</span>
        <span>预计 {preview.token_range.min}–{preview.token_range.max} tokens</span>
        <span>不保存正文或明文授权码</span>
      </div>

      <div className="knowledge-grant-actions">
        <button
          ref={primaryRef}
          className="knowledge-grant-primary"
          disabled={busy || (!preview.can_always_allow && !preview.can_allow_once)}
          title={
            preview.can_always_allow
              ? "以后可直接使用这些资料"
              : preview.can_allow_once
                ? "仅本轮允许使用"
                : "包含仅限本地资料，不能放行"
          }
          onClick={() => onAction(preview.can_always_allow ? "always_allow" : "allow_once")}
        >{preview.can_always_allow ? "可以用" : "这次可以用"}</button>
        <button disabled={busy} onClick={() => onAction("skip")}>这次不要用</button>
      </div>
      <small className="knowledge-grant-footnote">
        “这次不要用”会继续发送消息，但本轮不使用资料。可以在设置中改为每次问我。
      </small>
      <span className="sr-only" role="status" aria-live="polite">
        {busy ? "正在处理资料授权" : "等待选择资料发送方式"}
      </span>
    </section>
  );
}

function newRequestNonce(): string {
  if (globalThis.crypto?.randomUUID) return globalThis.crypto.randomUUID();
  return `request-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 14)}`;
}

function rememberProviderLocation(provider: api.KnowledgeGrantPreflight["provider"]): boolean {
  const key = `xiadie:provider-location:${provider.id || "unknown"}:${provider.model}`;
  const value = `${provider.location}:${provider.location_revision}`;
  try {
    const previous = window.localStorage.getItem(key);
    window.localStorage.setItem(key, value);
    return previous !== null && previous !== value;
  } catch {
    return false;
  }
}

function locationText(location: api.KnowledgeGrantPreflight["provider"]["location"]): string {
  if (location === "local") return "本机";
  if (location === "remote") return "在线 / 远程";
  return "未知（按在线处理）";
}

function policyText(policy: api.KnowledgeGrantDocument["policy"]): string {
  if (policy === "remote_allowed") return "可以分享";
  if (policy === "local_only") return "只在本机";
  return "用之前问我";
}

function MessageRow({
  m,
  highlighted,
  onFavorite,
}: {
  m: api.Message;
  highlighted?: boolean;
  onFavorite: () => void;
}) {
  const [source, setSource] = useState<api.KnowledgeCitation | api.EvidenceLink | null>(null);
  const [sourceError, setSourceError] = useState<string | null>(null);
  const [attachmentContent, setAttachmentContent] = useState<{
    filename: string;
    content: string;
  } | null>(null);
  const [attachmentError, setAttachmentError] = useState<string | null>(null);

  async function openSource(citation: api.KnowledgeCitation) {
    try {
      setSourceError(null);
      setSource(await api.getKnowledgeCitation(citation.id));
    } catch (error) {
      setSource(null);
      setSourceError(error instanceof api.ApiError ? error.message : "无法读取原始资料");
    }
  }

  async function openEvidence(link: api.EvidenceLink) {
    try {
      setSourceError(null);
      setSource(await api.getEvidenceLink(link.id));
    } catch (error) {
      setSource(null);
      setSourceError(error instanceof api.ApiError ? error.message : "无法读取证据来源");
    }
  }

  async function openAttachment(attachment: api.ChatAttachmentResult) {
    // 本地未发送的附件（id 以 local- 开头）没有后端记录，跳过
    if (m.id.startsWith("local-")) {
      setAttachmentError(null);
      setAttachmentContent({
        filename: attachment.filename,
        content: attachment.content_preview || "（本地预览暂不可用）",
      });
      return;
    }
    try {
      setAttachmentError(null);
      const result = await api.getMessageAttachmentContent(m.id, attachment.id);
      setAttachmentContent({
        filename: result.filename,
        content: result.content,
      });
    } catch (error) {
      setAttachmentContent(null);
      setAttachmentError(error instanceof api.ApiError ? error.message : "无法读取附件全文");
    }
  }

  const hasAttachments = !!m.attachments?.length;
  const hasContent = !!m.content;

  return (
    <div
      id={`message-${m.id}`}
      className={`msg ${m.role}${highlighted ? " source-highlight" : ""}`}
    >
      <div className="avatar">{m.role === "user" ? "你" : "蝶"}</div>
      <div>
        <div className="bubble">
          {hasAttachments && (
            <div className="message-attachments">
              {m.attachments!.map((attachment) => (
                <div className="message-attachment-card" key={attachment.id}>
                  <span className="message-attachment-icon" aria-hidden="true">
                    {attachment.attachment_kind === "image" ? "🖼️" : "📄"}
                  </span>
                  <div className="message-attachment-info">
                    <strong>{attachment.filename}</strong>
                    <small>
                      {attachment.attachment_kind === "image"
                        ? `${attachment.pixel_width}×${attachment.pixel_height} · 原始字节已销毁`
                        : `${attachment.char_count} 字符`}
                    </small>
                  </div>
                  {attachment.attachment_kind !== "image" && (
                    <button
                      className="message-attachment-view"
                      onClick={() => void openAttachment(attachment)}
                    >查看全文</button>
                  )}
                </div>
              ))}
            </div>
          )}
          {hasContent && <div className="bubble-text">{m.content}</div>}
        </div>
        {(attachmentContent || attachmentError) && (
          <div className="knowledge-source" role="region" aria-label="附件全文">
            <button className="knowledge-source-close" onClick={() => {
              setAttachmentContent(null); setAttachmentError(null);
            }}>×</button>
            {attachmentContent ? (
              <>
                <strong>{attachmentContent.filename}</strong>
                <div>{attachmentContent.content}</div>
              </>
            ) : <span>{attachmentError}</span>}
          </div>
        )}
        {!!m.knowledge_citations?.length && (
          <div className="knowledge-citations" aria-label="本回复引用的资料">
            {m.knowledge_citations.map((citation) => (
              <button key={citation.id} onClick={() => void openSource(citation)}>
                {citation.citation_key} · {citation.original_name} · {citation.content_fingerprint}
              </button>
            ))}
          </div>
        )}
        {!!m.evidence_links?.length && (
          <div className="knowledge-citations evidence-strip" aria-label="本回复的跨来源证据">
            {m.evidence_links.map((link) => (
              <button key={link.id} onClick={() => void openEvidence(link)}>
                {link.citation_key} · {link.source_label} · {link.available ? link.content_fingerprint : "来源不可用"}
              </button>
            ))}
          </div>
        )}
        {(source || sourceError) && (
          <div className="knowledge-source" role="region" aria-label="资料原文">
            <button className="knowledge-source-close" onClick={() => {
              setSource(null); setSourceError(null);
            }}>×</button>
            {source && "original_name" in source ? (
              <>
                <strong>{source.original_name}</strong>
                <small>{sourceLocation(source)}</small>
                <div>{source.content}</div>
              </>
            ) : source ? (
              <>
                <strong>{source.source_label}</strong>
                <small>{source.locator_snapshot} · {source.content_fingerprint}</small>
                {source.available
                  ? <div>{source.content}</div>
                  : <div>{source.unavailable_reason || "来源当前不可访问"}</div>}
              </>
            ) : <span>{sourceError}</span>}
          </div>
        )}
        <div className="msg-meta">
          {m.model && <span>{m.model}</span>}
          <button onClick={() => navigator.clipboard?.writeText(m.content)}>复制</button>
          <button onClick={onFavorite}>{m.favorite ? "★ 已收藏" : "☆ 收藏"}</button>
        </div>
      </div>
    </div>
  );
}

function sourceLocation(source: api.KnowledgeCitation): string {
  const heading = source.heading_path.length ? ` · ${source.heading_path.join(" › ")}` : "";
  const page = source.page_start ? ` · 第 ${source.page_start}${source.page_end !== source.page_start ? `–${source.page_end}` : ""} 页` : "";
  return `段落 ${source.paragraph_start}–${source.paragraph_end} · 行 ${source.line_start}–${source.line_end}${page}${heading} · ${source.content_fingerprint}`;
}

function replyPhaseLabel(
  phase: Streaming["phase"],
  hasVisibleText: boolean,
): string {
  if (phase === "persistence") return "正在整理这次回复…";
  if (phase === "completed") return "回复完成";
  if (phase === "generation") return hasVisibleText ? "正在继续回应…" : "正在组织语言…";
  return "正在准备回复…";
}

function localMsg(
  role: "user" | "assistant",
  content: string,
  attachments?: api.ChatAttachmentResult[],
): api.Message {
  return {
    id: "local-" + Math.random().toString(36).slice(2),
    session_id: "",
    role,
    content,
    favorite: false,
    created_at: Date.now() / 1000,
    attachments,
  };
}

async function favorite(
  m: api.Message,
  setMessages: React.Dispatch<React.SetStateAction<api.Message[]>>
) {
  if (m.id.startsWith("local-")) return;
  const r = await api.toggleFavorite(m.id);
  setMessages((list) =>
    list.map((x) => (x.id === m.id ? { ...x, favorite: r.favorite } : x))
  );
}
