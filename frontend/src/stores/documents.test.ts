import { createPinia, setActivePinia } from "pinia";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import * as documentsApi from "@/api/documents";
import { ApiError } from "@/api/client";
import type { DocumentResponse } from "@/api/contracts";
import { useAuthStore } from "@/stores/auth";
import { useDocumentStore } from "@/stores/documents";

vi.mock("@/api/documents", () => ({
  deleteDocument: vi.fn(),
  getDocument: vi.fn(),
  listDocuments: vi.fn(),
  retryDocument: vi.fn(),
  uploadDocument: vi.fn(),
}));

const pendingDocument: DocumentResponse = {
  id: "document-1",
  knowledge_base_id: "knowledge-base-1",
  filename: "项目规范.md",
  mime_type: "text/markdown",
  size_bytes: 1024,
  sha256: "a".repeat(64),
  status: "pending",
  parser_version: null,
  chunker_version: null,
  page_count: 0,
  chunk_count: 0,
  error_code: null,
  error_message: null,
  created_at: "2026-08-11T01:00:00Z",
  updated_at: "2026-08-11T01:00:00Z",
  finished_at: null,
};

const readyDocument: DocumentResponse = {
  ...pendingDocument,
  status: "ready",
  parser_version: "pdf-v1",
  chunker_version: "token-v1",
  page_count: 3,
  chunk_count: 8,
  finished_at: "2026-08-11T01:01:00Z",
};

const failedDocument: DocumentResponse = {
  ...pendingDocument,
  status: "failed",
  error_code: "DOCUMENT_PARSE_FAILED",
  error_message: "文档没有可提取文本",
  finished_at: "2026-08-11T01:01:00Z",
};

beforeEach(() => {
  setActivePinia(createPinia());
  vi.clearAllMocks();
});

afterEach(() => {
  // 每个测试都主动清理模块内部计时器，避免轮询影响后续用例。
  useDocumentStore().reset();
  vi.useRealTimers();
});

function setAuthenticatedToken(): void {
  useAuthStore().token = "access-token";
}

describe("useDocumentStore", () => {
  it("加载成功后按知识库保存文档，终态列表不会启动轮询", async () => {
    setAuthenticatedToken();
    vi.mocked(documentsApi.listDocuments).mockResolvedValue([readyDocument]);
    const store = useDocumentStore();

    await store.loadDocuments("knowledge-base-1");

    expect(documentsApi.listDocuments).toHaveBeenCalledWith(
      "access-token",
      "knowledge-base-1",
    );
    expect(store.documentsForKnowledgeBase("knowledge-base-1")).toEqual([
      readyDocument,
    ]);
    expect(store.hasLoadedKnowledgeBase("knowledge-base-1")).toBe(true);
    expect(store.isPollingKnowledgeBase("knowledge-base-1")).toBe(false);
  });

  it("加载失败时保留上一次成功列表和后端中文错误", async () => {
    setAuthenticatedToken();
    vi.mocked(documentsApi.listDocuments).mockRejectedValue(
      new ApiError("文档服务暂时不可用", {
        status: 503,
        code: "SERVICE_UNAVAILABLE",
      }),
    );
    const store = useDocumentStore();
    store.documentsByKnowledgeBase["knowledge-base-1"] = [readyDocument];

    await store.loadDocuments("knowledge-base-1");

    expect(store.documentsForKnowledgeBase("knowledge-base-1")).toEqual([
      readyDocument,
    ]);
    expect(store.errorMessages["knowledge-base-1"]).toBe(
      "文档服务暂时不可用",
    );
  });

  it("上传成功后插入 pending 文档并启动状态轮询", async () => {
    setAuthenticatedToken();
    vi.mocked(documentsApi.uploadDocument).mockResolvedValue({
      document: pendingDocument,
      job_id: "job-1",
      job_status: "pending",
    });
    const store = useDocumentStore();
    const file = new File(["# 项目规范"], "项目规范.md", {
      type: "text/markdown",
    });

    await expect(
      store.uploadDocument("knowledge-base-1", file),
    ).resolves.toEqual(pendingDocument);

    expect(documentsApi.uploadDocument).toHaveBeenCalledWith(
      "access-token",
      "knowledge-base-1",
      file,
    );
    expect(store.documentsForKnowledgeBase("knowledge-base-1")[0]).toEqual(
      pendingDocument,
    );
    expect(store.isPollingKnowledgeBase("knowledge-base-1")).toBe(true);
  });

  it("轮询看到全部文档进入终态后自动停止", async () => {
    vi.useFakeTimers();
    setAuthenticatedToken();
    vi.mocked(documentsApi.uploadDocument).mockResolvedValue({
      document: pendingDocument,
      job_id: "job-1",
      job_status: "pending",
    });
    vi.mocked(documentsApi.listDocuments).mockResolvedValue([readyDocument]);
    const store = useDocumentStore();

    await store.uploadDocument(
      "knowledge-base-1",
      new File(["内容"], "项目规范.md"),
    );
    await vi.advanceTimersByTimeAsync(2_000);

    expect(documentsApi.listDocuments).toHaveBeenCalledTimes(1);
    expect(store.documentsForKnowledgeBase("knowledge-base-1")).toEqual([
      readyDocument,
    ]);
    expect(store.isPollingKnowledgeBase("knowledge-base-1")).toBe(false);
  });

  it("一次轮询故障时保留旧状态，并在下一周期自动恢复", async () => {
    vi.useFakeTimers();
    setAuthenticatedToken();
    vi.mocked(documentsApi.listDocuments)
      .mockRejectedValueOnce(
        new ApiError("网络暂时不可用", {
          status: 0,
          code: "NETWORK_ERROR",
        }),
      )
      .mockResolvedValueOnce([readyDocument]);
    const store = useDocumentStore();
    store.documentsByKnowledgeBase["knowledge-base-1"] = [pendingDocument];
    store.startPolling("knowledge-base-1");

    await vi.advanceTimersByTimeAsync(2_000);
    expect(store.documentsForKnowledgeBase("knowledge-base-1")).toEqual([
      pendingDocument,
    ]);
    expect(store.errorMessages["knowledge-base-1"]).toBe("网络暂时不可用");
    expect(store.isPollingKnowledgeBase("knowledge-base-1")).toBe(true);

    await vi.advanceTimersByTimeAsync(2_000);
    expect(store.documentsForKnowledgeBase("knowledge-base-1")).toEqual([
      readyDocument,
    ]);
    expect(store.errorMessages["knowledge-base-1"]).toBeUndefined();
    expect(store.isPollingKnowledgeBase("knowledge-base-1")).toBe(false);
  });

  it("失败重试成功后替换为 pending 状态并重新轮询", async () => {
    setAuthenticatedToken();
    vi.mocked(documentsApi.retryDocument).mockResolvedValue({
      document: pendingDocument,
      job_id: "job-2",
      job_status: "pending",
    });
    const store = useDocumentStore();
    store.documentsByKnowledgeBase["knowledge-base-1"] = [failedDocument];

    await expect(store.retryDocument(failedDocument.id)).resolves.toEqual(
      pendingDocument,
    );

    expect(store.documentsForKnowledgeBase("knowledge-base-1")).toEqual([
      pendingDocument,
    ]);
    expect(store.isRetryingDocument(failedDocument.id)).toBe(false);
    expect(store.isPollingKnowledgeBase("knowledge-base-1")).toBe(true);
  });

  it("重试失败时保留原失败文档和可展示的单项错误", async () => {
    setAuthenticatedToken();
    vi.mocked(documentsApi.retryDocument).mockRejectedValue(
      new ApiError("该文档当前不能重试", {
        status: 409,
        code: "DOCUMENT_NOT_RETRYABLE",
      }),
    );
    const store = useDocumentStore();
    store.documentsByKnowledgeBase["knowledge-base-1"] = [failedDocument];

    await expect(store.retryDocument(failedDocument.id)).resolves.toBeNull();

    expect(store.documentsForKnowledgeBase("knowledge-base-1")).toEqual([
      failedDocument,
    ]);
    expect(store.actionErrorMessages[failedDocument.id]).toBe(
      "该文档当前不能重试",
    );
  });

  it("删除响应成功前不乐观移除文档，204 后才从列表删除", async () => {
    setAuthenticatedToken();
    let finishDelete: (() => void) | undefined;
    vi.mocked(documentsApi.deleteDocument).mockImplementation(
      () =>
        new Promise<void>((resolve) => {
          finishDelete = resolve;
        }),
    );
    const store = useDocumentStore();
    store.documentsByKnowledgeBase["knowledge-base-1"] = [readyDocument];

    const deletePromise = store.deleteDocument(readyDocument.id);
    expect(store.documentsForKnowledgeBase("knowledge-base-1")).toEqual([
      readyDocument,
    ]);
    expect(store.isDeletingDocument(readyDocument.id)).toBe(true);

    finishDelete?.();
    await expect(deletePromise).resolves.toBe(true);
    expect(store.documentsForKnowledgeBase("knowledge-base-1")).toEqual([]);
  });

  it("可恢复删除失败后保留文档 ID，后续列表刷新也不能把它丢掉", async () => {
    setAuthenticatedToken();
    vi.mocked(documentsApi.deleteDocument).mockRejectedValue(
      new ApiError("对象存储暂时不可用，可再次删除继续清理", {
        status: 503,
        code: "OBJECT_STORAGE_UNAVAILABLE",
      }),
    );
    vi.mocked(documentsApi.listDocuments).mockResolvedValue([]);
    const store = useDocumentStore();
    store.documentsByKnowledgeBase["knowledge-base-1"] = [readyDocument];

    await expect(store.deleteDocument(readyDocument.id)).resolves.toBe(false);
    await store.loadDocuments("knowledge-base-1");

    expect(store.documentsForKnowledgeBase("knowledge-base-1")).toEqual([
      readyDocument,
    ]);
    expect(store.deletionRecoveryDocumentIds).toContain(readyDocument.id);
    expect(store.actionErrorMessages[readyDocument.id]).toContain(
      "再次删除",
    );
  });

  it("恢复删除再次得到 404 时按幂等成功清理本地残留", async () => {
    setAuthenticatedToken();
    vi.mocked(documentsApi.deleteDocument).mockRejectedValue(
      new ApiError("文档不存在", {
        status: 404,
        code: "DOCUMENT_NOT_FOUND",
      }),
    );
    const store = useDocumentStore();
    store.documentsByKnowledgeBase["knowledge-base-1"] = [readyDocument];
    store.deletionRecoveryDocumentIds = [readyDocument.id];

    await expect(store.deleteDocument(readyDocument.id)).resolves.toBe(true);

    expect(store.documentsForKnowledgeBase("knowledge-base-1")).toEqual([]);
    expect(store.deletionRecoveryDocumentIds).toEqual([]);
    expect(store.actionErrorMessages[readyDocument.id]).toBeUndefined();
  });

  it("reset 停止轮询并清除切换账号前的全部文档状态", async () => {
    vi.useFakeTimers();
    setAuthenticatedToken();
    const store = useDocumentStore();
    store.documentsByKnowledgeBase["knowledge-base-1"] = [pendingDocument];
    store.startPolling("knowledge-base-1");

    store.reset();
    await vi.advanceTimersByTimeAsync(3_000);

    expect(documentsApi.listDocuments).not.toHaveBeenCalled();
    expect(store.documentsByKnowledgeBase).toEqual({});
    expect(store.pollingKnowledgeBaseIds).toEqual([]);
  });

  it("没有 Token 时不访问后端，并清除旧账号的文档状态", async () => {
    const store = useDocumentStore();
    store.documentsByKnowledgeBase["knowledge-base-1"] = [readyDocument];

    await store.loadDocuments("knowledge-base-1");

    expect(documentsApi.listDocuments).not.toHaveBeenCalled();
    expect(store.documentsByKnowledgeBase).toEqual({});
    expect(store.errorMessages.global).toBe("登录状态已失效，请重新登录");
  });
});
