import { afterEach, describe, expect, it, vi } from "vitest";

import {
  archiveKnowledgeBase,
  createKnowledgeBase,
  getKnowledgeBase,
  listKnowledgeBases,
  updateKnowledgeBase,
} from "@/api/knowledge-bases";

const knowledgeBase = {
  id: "knowledge-base-1",
  name: "项目资料库",
  description: "保存项目规范和会议资料",
  embedding_provider: "mock",
  embedding_model: "mock-hash-1024",
  embedding_dimension: 1024,
  is_archived: false,
  created_at: "2026-08-11T01:00:00Z",
  updated_at: "2026-08-11T01:00:00Z",
};

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("知识库 API Client", () => {
  it("默认读取活动知识库，并传递 Bearer Token", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify([knowledgeBase]), { status: 200 }),
    );
    vi.stubGlobal("fetch", fetchMock);

    const result = await listKnowledgeBases("access-token");

    expect(result).toEqual([knowledgeBase]);
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/v1/knowledge-bases",
      expect.objectContaining({ method: "GET" }),
    );
    const [, options] = fetchMock.mock.calls[0];
    expect(new Headers(options.headers).get("Authorization")).toBe(
      "Bearer access-token",
    );
  });

  it("读取归档知识库时使用 include_archived 查询参数", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify([knowledgeBase]), { status: 200 }),
    );
    vi.stubGlobal("fetch", fetchMock);

    await listKnowledgeBases("access-token", { includeArchived: true });

    expect(fetchMock).toHaveBeenCalledWith(
      "/api/v1/knowledge-bases?include_archived=true",
      expect.any(Object),
    );
  });

  it("读取详情时编码知识库 ID，避免特殊字符改变路径", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify(knowledgeBase), { status: 200 }),
    );
    vi.stubGlobal("fetch", fetchMock);

    const result = await getKnowledgeBase("access-token", "资料 / 1");

    expect(result).toEqual(knowledgeBase);
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/v1/knowledge-bases/%E8%B5%84%E6%96%99%20%2F%201",
      expect.objectContaining({ method: "GET" }),
    );
  });

  it("创建知识库时发送 JSON 请求体，并返回配置快照", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify(knowledgeBase), { status: 201 }),
    );
    vi.stubGlobal("fetch", fetchMock);

    const result = await createKnowledgeBase("access-token", {
      name: "项目资料库",
      description: "保存项目规范和会议资料",
    });

    expect(result).toEqual(knowledgeBase);
    const [path, options] = fetchMock.mock.calls[0];
    expect(path).toBe("/api/v1/knowledge-bases");
    expect(options.method).toBe("POST");
    expect(JSON.parse(options.body)).toEqual({
      name: "项目资料库",
      description: "保存项目规范和会议资料",
    });
  });

  it("更新知识库时保留 description: null 的清空语义", async () => {
    const updatedKnowledgeBase = { ...knowledgeBase, description: null };
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify(updatedKnowledgeBase), { status: 200 }),
    );
    vi.stubGlobal("fetch", fetchMock);

    const result = await updateKnowledgeBase(
      "access-token",
      "knowledge-base-1",
      { description: null },
    );

    expect(result).toEqual(updatedKnowledgeBase);
    const [path, options] = fetchMock.mock.calls[0];
    expect(path).toBe("/api/v1/knowledge-bases/knowledge-base-1");
    expect(options.method).toBe("PATCH");
    expect(JSON.parse(options.body)).toEqual({ description: null });
  });

  it("归档知识库时使用 DELETE，并正确处理 204 空响应", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(null, { status: 204 }),
    );
    vi.stubGlobal("fetch", fetchMock);

    await archiveKnowledgeBase("access-token", "资料 / 1");

    expect(fetchMock).toHaveBeenCalledWith(
      "/api/v1/knowledge-bases/%E8%B5%84%E6%96%99%20%2F%201",
      expect.objectContaining({ method: "DELETE" }),
    );
  });
});
