import { createPinia, setActivePinia } from "pinia";
import { beforeEach, describe, expect, it, vi } from "vitest";

import * as knowledgeBasesApi from "@/api/knowledge-bases";
import { ApiError } from "@/api/client";
import type { KnowledgeBaseResponse } from "@/api/contracts";
import { useAuthStore } from "@/stores/auth";
import { useKnowledgeBaseStore } from "@/stores/knowledge-bases";

vi.mock("@/api/knowledge-bases", () => ({
  archiveKnowledgeBase: vi.fn(),
  createKnowledgeBase: vi.fn(),
  getKnowledgeBase: vi.fn(),
  listKnowledgeBases: vi.fn(),
  updateKnowledgeBase: vi.fn(),
}));

const firstKnowledgeBase: KnowledgeBaseResponse = {
  id: "knowledge-base-1",
  name: "项目资料库",
  description: "项目规范和会议资料",
  embedding_provider: "mock",
  embedding_model: "mock-hash-1024",
  embedding_dimension: 1024,
  is_archived: false,
  created_at: "2026-08-11T01:00:00Z",
  updated_at: "2026-08-11T01:00:00Z",
};

const secondKnowledgeBase: KnowledgeBaseResponse = {
  ...firstKnowledgeBase,
  id: "knowledge-base-2",
  name: "产品手册库",
};

beforeEach(() => {
  setActivePinia(createPinia());
  vi.clearAllMocks();
});

/** 为受保护接口准备最小登录状态，不依赖真实认证网络请求。 */
function setAuthenticatedToken(): void {
  useAuthStore().token = "access-token";
}

describe("useKnowledgeBaseStore", () => {
  it("加载知识库时保存列表、筛选条件和完成状态", async () => {
    setAuthenticatedToken();
    vi.mocked(knowledgeBasesApi.listKnowledgeBases).mockResolvedValue([
      firstKnowledgeBase,
    ]);
    const store = useKnowledgeBaseStore();

    await store.loadKnowledgeBases({ includeArchived: true });

    expect(knowledgeBasesApi.listKnowledgeBases).toHaveBeenCalledWith(
      "access-token",
      { includeArchived: true },
    );
    expect(store.knowledgeBases).toEqual([firstKnowledgeBase]);
    expect(store.includeArchived).toBe(true);
    expect(store.hasLoaded).toBe(true);
    expect(store.isLoading).toBe(false);
  });

  it("加载失败时保留上一次成功列表，并展示后端中文错误", async () => {
    setAuthenticatedToken();
    vi.mocked(knowledgeBasesApi.listKnowledgeBases).mockRejectedValue(
      new ApiError("知识库服务暂时不可用", {
        status: 503,
        code: "SERVICE_UNAVAILABLE",
      }),
    );
    const store = useKnowledgeBaseStore();
    store.knowledgeBases = [firstKnowledgeBase];
    store.hasLoaded = true;

    await store.loadKnowledgeBases();

    expect(store.knowledgeBases).toEqual([firstKnowledgeBase]);
    expect(store.hasLoaded).toBe(true);
    expect(store.errorMessage).toBe("知识库服务暂时不可用");
    expect(store.isLoading).toBe(false);
  });

  it("没有 Token 时不访问后端，并清空旧用户状态", async () => {
    const store = useKnowledgeBaseStore();
    store.knowledgeBases = [firstKnowledgeBase];

    await store.loadKnowledgeBases();

    expect(knowledgeBasesApi.listKnowledgeBases).not.toHaveBeenCalled();
    expect(store.knowledgeBases).toEqual([]);
    expect(store.errorMessage).toBe("登录状态已失效，请重新登录");
  });

  it("创建成功后把新知识库插入列表开头", async () => {
    setAuthenticatedToken();
    vi.mocked(knowledgeBasesApi.createKnowledgeBase).mockResolvedValue(
      secondKnowledgeBase,
    );
    const store = useKnowledgeBaseStore();
    store.knowledgeBases = [firstKnowledgeBase];

    const created = await store.createKnowledgeBase({
      name: "产品手册库",
      description: null,
    });

    expect(knowledgeBasesApi.createKnowledgeBase).toHaveBeenCalledWith(
      "access-token",
      { name: "产品手册库", description: null },
    );
    expect(created).toEqual(secondKnowledgeBase);
    expect(store.knowledgeBases).toEqual([
      secondKnowledgeBase,
      firstKnowledgeBase,
    ]);
    expect(store.isCreating).toBe(false);
  });

  it("更新期间标记目标处理中，成功后只替换对应知识库", async () => {
    setAuthenticatedToken();
    const renamedKnowledgeBase = {
      ...firstKnowledgeBase,
      name: "项目规范库",
    };
    let finishRequest:
      | ((knowledgeBase: KnowledgeBaseResponse) => void)
      | undefined;
    vi.mocked(knowledgeBasesApi.updateKnowledgeBase).mockImplementation(
      () =>
        new Promise<KnowledgeBaseResponse>((resolve) => {
          finishRequest = resolve;
        }),
    );
    const store = useKnowledgeBaseStore();
    store.knowledgeBases = [firstKnowledgeBase, secondKnowledgeBase];

    const updatePromise = store.updateKnowledgeBase(firstKnowledgeBase.id, {
      name: "项目规范库",
    });

    expect(store.isUpdatingKnowledgeBase(firstKnowledgeBase.id)).toBe(true);
    expect(store.isManagingKnowledgeBase(firstKnowledgeBase.id)).toBe(true);

    finishRequest?.(renamedKnowledgeBase);
    await expect(updatePromise).resolves.toEqual(renamedKnowledgeBase);

    expect(knowledgeBasesApi.updateKnowledgeBase).toHaveBeenCalledWith(
      "access-token",
      firstKnowledgeBase.id,
      { name: "项目规范库" },
    );
    expect(store.knowledgeBases).toEqual([
      renamedKnowledgeBase,
      secondKnowledgeBase,
    ]);
    expect(store.isUpdatingKnowledgeBase(firstKnowledgeBase.id)).toBe(false);
  });

  it("归档成功后只移除默认活动列表中的目标", async () => {
    setAuthenticatedToken();
    vi.mocked(knowledgeBasesApi.archiveKnowledgeBase).mockResolvedValue();
    const store = useKnowledgeBaseStore();
    store.knowledgeBases = [firstKnowledgeBase, secondKnowledgeBase];

    await expect(
      store.archiveKnowledgeBase(firstKnowledgeBase.id),
    ).resolves.toBe(true);

    expect(knowledgeBasesApi.archiveKnowledgeBase).toHaveBeenCalledWith(
      "access-token",
      firstKnowledgeBase.id,
    );
    expect(store.knowledgeBases).toEqual([secondKnowledgeBase]);
    expect(store.isArchivingKnowledgeBase(firstKnowledgeBase.id)).toBe(false);
  });

  it("包含归档记录时保留目标，并只更新后端已经确认的归档状态", async () => {
    setAuthenticatedToken();
    vi.mocked(knowledgeBasesApi.archiveKnowledgeBase).mockResolvedValue();
    const store = useKnowledgeBaseStore();
    store.includeArchived = true;
    store.knowledgeBases = [firstKnowledgeBase];

    await store.archiveKnowledgeBase(firstKnowledgeBase.id);

    expect(store.knowledgeBases).toEqual([
      { ...firstKnowledgeBase, is_archived: true },
    ]);
    expect(store.knowledgeBases[0].updated_at).toBe(
      firstKnowledgeBase.updated_at,
    );
  });

  it("同一知识库更新未完成时拒绝并行归档，避免响应顺序互相覆盖", async () => {
    setAuthenticatedToken();
    let finishUpdate:
      | ((knowledgeBase: KnowledgeBaseResponse) => void)
      | undefined;
    vi.mocked(knowledgeBasesApi.updateKnowledgeBase).mockImplementation(
      () =>
        new Promise<KnowledgeBaseResponse>((resolve) => {
          finishUpdate = resolve;
        }),
    );
    const store = useKnowledgeBaseStore();
    store.knowledgeBases = [firstKnowledgeBase];

    const updatePromise = store.updateKnowledgeBase(firstKnowledgeBase.id, {
      name: "项目规范库",
    });
    const archiveResult = await store.archiveKnowledgeBase(
      firstKnowledgeBase.id,
    );

    expect(archiveResult).toBe(false);
    expect(knowledgeBasesApi.archiveKnowledgeBase).not.toHaveBeenCalled();

    finishUpdate?.({ ...firstKnowledgeBase, name: "项目规范库" });
    await updatePromise;
  });

  it("单项操作失败时保留列表并记录该知识库的中文错误", async () => {
    setAuthenticatedToken();
    vi.mocked(knowledgeBasesApi.updateKnowledgeBase).mockRejectedValue(
      new ApiError("知识库名称已经存在", {
        status: 409,
        code: "KNOWLEDGE_BASE_NAME_EXISTS",
      }),
    );
    const store = useKnowledgeBaseStore();
    store.knowledgeBases = [firstKnowledgeBase];

    await expect(
      store.updateKnowledgeBase(firstKnowledgeBase.id, { name: "产品手册库" }),
    ).resolves.toBeNull();

    expect(store.knowledgeBases).toEqual([firstKnowledgeBase]);
    expect(store.actionErrorMessages[firstKnowledgeBase.id]).toBe(
      "知识库名称已经存在",
    );
    expect(store.isUpdatingKnowledgeBase(firstKnowledgeBase.id)).toBe(false);
  });

  it("reset 会清空切换账号前的知识库状态", () => {
    const store = useKnowledgeBaseStore();
    store.knowledgeBases = [firstKnowledgeBase];
    store.hasLoaded = true;
    store.includeArchived = true;
    store.errorMessage = "旧错误";

    store.reset();

    expect(store.knowledgeBases).toEqual([]);
    expect(store.hasLoaded).toBe(false);
    expect(store.includeArchived).toBe(false);
    expect(store.errorMessage).toBeNull();
  });
});
