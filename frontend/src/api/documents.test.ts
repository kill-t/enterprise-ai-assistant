import { afterEach, describe, expect, it, vi } from "vitest";

import {
  deleteDocument,
  getDocument,
  listDocuments,
  retryDocument,
  uploadDocument,
} from "@/api/documents";
import type {
  DocumentResponse,
  DocumentRetryResponse,
  DocumentUploadResponse,
} from "@/api/contracts";

const document: DocumentResponse = {
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

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("文档 API Client", () => {
  it("读取知识库文档时编码知识库 ID 并传递 Token", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify([document]), { status: 200 }),
    );
    vi.stubGlobal("fetch", fetchMock);

    await expect(
      listDocuments("access-token", "资料 / 1"),
    ).resolves.toEqual([document]);

    expect(fetchMock).toHaveBeenCalledWith(
      "/api/v1/knowledge-bases/%E8%B5%84%E6%96%99%20%2F%201/documents",
      expect.objectContaining({ method: "GET" }),
    );
    const [, options] = fetchMock.mock.calls[0];
    expect(new Headers(options.headers).get("Authorization")).toBe(
      "Bearer access-token",
    );
  });

  it("上传使用 FormData 且不手工设置 Content-Type 边界", async () => {
    const response: DocumentUploadResponse = {
      document,
      job_id: "job-1",
      job_status: "pending",
    };
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify(response), { status: 202 }),
    );
    vi.stubGlobal("fetch", fetchMock);
    const file = new File(["# 项目规范"], "项目规范.md", {
      type: "text/markdown",
    });

    await expect(
      uploadDocument("access-token", "knowledge-base-1", file),
    ).resolves.toEqual(response);

    const [path, options] = fetchMock.mock.calls[0];
    expect(path).toBe(
      "/api/v1/knowledge-bases/knowledge-base-1/documents",
    );
    expect(options.method).toBe("POST");
    expect(options.body).toBeInstanceOf(FormData);
    expect((options.body as FormData).get("file")).toBe(file);
    expect(new Headers(options.headers).has("Content-Type")).toBe(false);
  });

  it("读取文档详情时编码文档 ID", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify(document), { status: 200 }),
    );
    vi.stubGlobal("fetch", fetchMock);

    await getDocument("access-token", "文档 / 1");

    expect(fetchMock).toHaveBeenCalledWith(
      "/api/v1/documents/%E6%96%87%E6%A1%A3%20%2F%201",
      expect.objectContaining({ method: "GET" }),
    );
  });

  it("失败重试使用 POST 并解析新的任务信息", async () => {
    const response: DocumentRetryResponse = {
      document,
      job_id: "job-2",
      job_status: "pending",
    };
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify(response), { status: 202 }),
    );
    vi.stubGlobal("fetch", fetchMock);

    await expect(
      retryDocument("access-token", "document-1"),
    ).resolves.toEqual(response);

    expect(fetchMock).toHaveBeenCalledWith(
      "/api/v1/documents/document-1/retry",
      expect.objectContaining({ method: "POST" }),
    );
  });

  it("删除使用 DELETE 并正确处理 204 空响应", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(null, { status: 204 }),
    );
    vi.stubGlobal("fetch", fetchMock);

    await deleteDocument("access-token", "document-1");

    expect(fetchMock).toHaveBeenCalledWith(
      "/api/v1/documents/document-1",
      expect.objectContaining({ method: "DELETE" }),
    );
  });
});
