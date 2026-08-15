import { defineStore } from "pinia";

import {
  getConversation,
  listConversationMessages,
  retryConversationMessage,
} from "@/api/conversations";
import { streamConversationMessage } from "@/api/conversation-stream";
import { ApiError } from "@/api/client";
import type {
  ConversationResponse,
  MessageResponse,
} from "@/api/contracts";
import { useAuthStore } from "@/stores/auth";

interface ConversationDetailState {
  conversationId: string | null;
  conversation: ConversationResponse | null;
  messages: MessageResponse[];
  isLoading: boolean;
  hasLoaded: boolean;
  errorMessage: string | null;
  isSending: boolean;
  isCancelling: boolean;
  sendErrorMessage: string | null;
  retryingMessageIds: string[];
  retryErrorMessages: Record<string, string>;
  loadRequestVersion: number;
}

interface ActiveStream {
  controller: AbortController;
  conversationId: string;
  assistantMessageId: string | null;
}

// AbortController 不是可序列化的业务状态，因此只保留在 Store 实例的内部 WeakMap 中。
const activeStreams = new WeakMap<object, ActiveStream>();

/** 将网络和 HTTP 层错误转换为详情页面可直接展示的中文消息。 */
function getErrorMessage(error: unknown): string {
  return error instanceof ApiError
    ? error.message
    : "加载会话详情失败，请稍后重试";
}

/** 将 SSE、网络中断等流式错误转换为发送区域的安全中文提示。 */
function getSendErrorMessage(error: unknown): string {
  return error instanceof ApiError ? error.message : "消息生成中断，请重试";
}

/** 将普通重试接口错误保留在对应消息附近，避免与发送错误混淆。 */
function getRetryErrorMessage(error: unknown): string {
  return error instanceof ApiError ? error.message : "重试失败，请稍后重试";
}

/** 识别浏览器主动取消，不把用户点击停止误显示为普通失败。 */
function isAbortError(error: unknown): boolean {
  return (
    typeof error === "object" &&
    error !== null &&
    "name" in error &&
    error.name === "AbortError"
  );
}

/** 只在列表尚未包含该消息时追加，避免重复 started 事件造成重复气泡。 */
function appendMessageIfMissing(
  messages: MessageResponse[],
  message: MessageResponse,
): MessageResponse[] {
  return messages.some((item) => item.id === message.id)
    ? messages
    : [...messages, message];
}

/** 根据消息 ID 替换终态；完成事件可保留此前实时收到的来源。 */
function replaceMessage(
  messages: MessageResponse[],
  replacement: MessageResponse,
  preserveExistingCitations = false,
): MessageResponse[] {
  return messages.map((item) => {
    if (item.id !== replacement.id) {
      return item;
    }
    const citations =
      replacement.citations ??
      (preserveExistingCitations ? item.citations : undefined);
    return citations === undefined
      ? replacement
      : { ...replacement, citations };
  });
}

/** 管理当前打开会话的元数据和已持久化消息，避免不同会话响应相互覆盖。 */
export const useConversationDetailStore = defineStore("conversation-detail", {
  state: (): ConversationDetailState => ({
    conversationId: null,
    conversation: null,
    messages: [],
    isLoading: false,
    hasLoaded: false,
    errorMessage: null,
    isSending: false,
    isCancelling: false,
    sendErrorMessage: null,
    retryingMessageIds: [],
    retryErrorMessages: {},
    // 仅用于判定异步响应是否仍属于当前请求，不应直接被页面使用。
    loadRequestVersion: 0,
  }),
  getters: {
    /** 判断指定消息是否正在原位重试，供对应按钮单独禁用。 */
    isRetryingMessage: (state) => (messageId: string): boolean =>
      state.retryingMessageIds.includes(messageId),
  },
  actions: {
    /** 清空详情数据并使尚未返回的请求失效，防止退出后写回旧用户信息。 */
    reset(): void {
      const activeStream = activeStreams.get(this);
      activeStream?.controller.abort();
      activeStreams.delete(this);
      this.loadRequestVersion += 1;
      this.conversationId = null;
      this.conversation = null;
      this.messages = [];
      this.isLoading = false;
      this.hasLoaded = false;
      this.errorMessage = null;
      this.isSending = false;
      this.isCancelling = false;
      this.sendErrorMessage = null;
      this.retryingMessageIds = [];
      this.retryErrorMessages = {};
    },

    /** 缺少 Token 时不访问受保护接口，同时清理可能残留的详情。 */
    resetForMissingToken(): void {
      this.reset();
      this.errorMessage = "登录状态已失效，请重新登录";
    },

    /** 并行读取会话与历史消息；仅最新一次请求可以修改 Store 状态。 */
    async load(conversationId: string): Promise<void> {
      const token = useAuthStore().token;
      if (!token) {
        this.resetForMissingToken();
        return;
      }

      const isSameConversation = this.conversationId === conversationId;
      const requestVersion = this.loadRequestVersion + 1;
      this.loadRequestVersion = requestVersion;
      if (!isSameConversation) {
        this.cancelActiveStream();
        // 切换会话时先移除旧消息，避免用户把 A 的内容误认为 B 的内容。
        this.conversationId = conversationId;
        this.conversation = null;
        this.messages = [];
        this.hasLoaded = false;
      }
      this.isLoading = true;
      this.errorMessage = null;

      try {
        const [conversation, messages] = await Promise.all([
          getConversation(token, conversationId),
          listConversationMessages(token, conversationId),
        ]);
        if (this.loadRequestVersion !== requestVersion) {
          return;
        }
        this.conversation = conversation;
        this.messages = messages;
        this.hasLoaded = true;
      } catch (error) {
        if (this.loadRequestVersion !== requestVersion) {
          return;
        }
        this.errorMessage = getErrorMessage(error);
      } finally {
        if (this.loadRequestVersion === requestVersion) {
          this.isLoading = false;
        }
      }
    },

    /** 发起当前会话的内部 SSE 流，并把事件转换为页面直接渲染的消息数组。 */
    async sendMessage(content: string): Promise<void> {
      const normalizedContent = content.trim();
      const token = useAuthStore().token;
      const conversationId = this.conversationId;
      if (!normalizedContent) {
        this.sendErrorMessage = "请输入消息内容";
        return;
      }
      if (!token || !conversationId) {
        this.sendErrorMessage = "登录状态已失效或会话尚未加载完成";
        return;
      }
      if (this.isSending) {
        return;
      }

      const activeStream: ActiveStream = {
        controller: new AbortController(),
        conversationId,
        assistantMessageId: null,
      };
      activeStreams.set(this, activeStream);
      this.isSending = true;
      this.isCancelling = false;
      this.sendErrorMessage = null;

      try {
        for await (const event of streamConversationMessage({
          conversationId,
          content: normalizedContent,
          token,
          signal: activeStream.controller.signal,
        })) {
          // 路由已切走时不把旧会话的增量写入新会话；切换逻辑会主动取消流。
          if (this.conversationId !== activeStream.conversationId) {
            continue;
          }

          switch (event.type) {
            case "started":
              this.messages = appendMessageIfMissing(
                this.messages,
                event.user_message,
              );
              this.messages = appendMessageIfMissing(
                this.messages,
                event.assistant_message,
              );
              activeStream.assistantMessageId = event.assistant_message.id;
              break;
            case "delta":
              this.messages = this.messages.map((message) =>
                message.id === event.message_id
                  ? { ...message, content: `${message.content}${event.delta}` }
                  : message,
              );
              break;
            case "sources": {
              const sourceTarget = this.messages.find(
                (message) => message.id === event.message_id,
              );
              if (
                activeStream.assistantMessageId !== event.message_id ||
                sourceTarget?.role !== "assistant"
              ) {
                throw new Error("sources 不属于当前正在生成的助手消息");
              }
              this.messages = this.messages.map((message) =>
                message.id === event.message_id
                  ? { ...message, citations: event.sources }
                  : message,
              );
              break;
            }
            case "completed":
              this.messages = replaceMessage(
                this.messages,
                event.message,
                true,
              );
              break;
            case "error":
              this.messages = replaceMessage(this.messages, event.message);
              this.sendErrorMessage =
                event.message.error_message ?? "模型生成失败，请重试";
              break;
          }
        }
      } catch (error) {
        if (this.conversationId !== activeStream.conversationId) {
          return;
        }
        if (isAbortError(error)) {
          if (activeStream.assistantMessageId) {
            this.messages = this.messages.map((message) =>
              message.id === activeStream.assistantMessageId
                ? {
                    ...message,
                    status: "cancelled",
                    error_code: null,
                    error_message: null,
                  }
                : message,
            );
          }
          return;
        }

        this.sendErrorMessage = getSendErrorMessage(error);
        if (activeStream.assistantMessageId) {
          this.messages = this.messages.map((message) =>
            message.id === activeStream.assistantMessageId
              ? {
                  ...message,
                  status: "failed",
                  error_code: "STREAM_INTERRUPTED",
                  error_message: this.sendErrorMessage,
                }
              : message,
          );
        }
      } finally {
        if (activeStreams.get(this) === activeStream) {
          activeStreams.delete(this);
          this.isSending = false;
          this.isCancelling = false;
        }
      }
    },

    /** 中断当前 HTTP 流；最终取消状态会在 sendMessage 的 AbortError 分支统一收口。 */
    cancelActiveStream(): void {
      const activeStream = activeStreams.get(this);
      if (!activeStream) {
        return;
      }
      this.isCancelling = true;
      activeStream.controller.abort();
    },

    /** 原位重试失败或取消的助手消息；请求失败时优先恢复数据库保存的最新终态。 */
    async retryMessage(messageId: string): Promise<void> {
      const token = useAuthStore().token;
      const conversationId = this.conversationId;
      const originalMessage = this.messages.find(
        (message) => message.id === messageId,
      );
      if (
        !token ||
        !conversationId ||
        this.isSending ||
        this.isRetryingMessage(messageId) ||
        !originalMessage ||
        originalMessage.role !== "assistant" ||
        !["failed", "cancelled"].includes(originalMessage.status)
      ) {
        return;
      }

      const pendingMessage: MessageResponse = {
        ...originalMessage,
        content: "",
        status: "pending",
        error_code: null,
        error_message: null,
        finished_at: null,
      };
      this.messages = replaceMessage(this.messages, pendingMessage);
      this.retryingMessageIds = [...this.retryingMessageIds, messageId];
      const { [messageId]: _ignoredError, ...otherRetryErrors } =
        this.retryErrorMessages;
      this.retryErrorMessages = otherRetryErrors;

      try {
        const retriedMessage = await retryConversationMessage(
          token,
          conversationId,
          messageId,
        );
        if (this.conversationId === conversationId) {
          this.messages = replaceMessage(this.messages, retriedMessage);
        }
      } catch (error) {
        const retryErrorMessage = getRetryErrorMessage(error);
        let refreshedMessage: MessageResponse | undefined;
        try {
          // 502 也可能表示模型已返回半截正文；重新读取才能展示后端刚保存的真实失败状态。
          const latestMessages = await listConversationMessages(
            token,
            conversationId,
          );
          refreshedMessage = latestMessages.find(
            (message) => message.id === messageId,
          );
          if (this.conversationId === conversationId && refreshedMessage) {
            this.messages = latestMessages;
          }
        } catch {
          // 网络完全不可用时无法恢复数据库状态，下面退回点击前快照并展示请求错误。
        }

        if (this.conversationId === conversationId) {
          if (!refreshedMessage) {
            this.messages = replaceMessage(this.messages, originalMessage);
          }
          // 数据库消息已经携带同一错误时不重复展示第二条操作错误。
          if (refreshedMessage?.error_message !== retryErrorMessage) {
            this.retryErrorMessages = {
              ...this.retryErrorMessages,
              [messageId]: retryErrorMessage,
            };
          }
        }
      } finally {
        this.retryingMessageIds = this.retryingMessageIds.filter(
          (retryingId) => retryingId !== messageId,
        );
      }
    },
  },
});
