import { flushPromises, mount } from "@vue/test-utils";
import { createPinia, setActivePinia } from "pinia";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { createMemoryHistory, createRouter } from "vue-router";

import * as knowledgeBasesApi from "@/api/knowledge-bases";
import * as documentsApi from "@/api/documents";
import { ApiError } from "@/api/client";
import type {
  DocumentResponse,
  KnowledgeBaseResponse,
} from "@/api/contracts";
import { useAuthStore } from "@/stores/auth";
import { useDocumentStore } from "@/stores/documents";
import KnowledgeBasesView from "@/views/KnowledgeBasesView.vue";

vi.mock("@/api/knowledge-bases", () => ({
  archiveKnowledgeBase: vi.fn(),
  createKnowledgeBase: vi.fn(),
  getKnowledgeBase: vi.fn(),
  listKnowledgeBases: vi.fn(),
  updateKnowledgeBase: vi.fn(),
}));

vi.mock("@/api/documents", () => ({
  deleteDocument: vi.fn(),
  getDocument: vi.fn(),
  listDocuments: vi.fn(),
  retryDocument: vi.fn(),
  uploadDocument: vi.fn(),
}));

const knowledgeBase: KnowledgeBaseResponse = {
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

const archivedKnowledgeBase: KnowledgeBaseResponse = {
  ...knowledgeBase,
  id: "knowledge-base-2",
  name: "旧项目资料库",
  is_archived: true,
};

const readyDocument: DocumentResponse = {
  id: "document-1",
  knowledge_base_id: knowledgeBase.id,
  filename: "项目规范.md",
  mime_type: "text/markdown",
  size_bytes: 1024,
  sha256: "a".repeat(64),
  status: "ready",
  parser_version: "markdown-v1",
  chunker_version: "token-v1",
  page_count: 3,
  chunk_count: 8,
  error_code: null,
  error_message: null,
  created_at: "2026-08-11T01:00:00Z",
  updated_at: "2026-08-11T01:01:00Z",
  finished_at: "2026-08-11T01:01:00Z",
};

const failedDocument: DocumentResponse = {
  ...readyDocument,
  id: "document-2",
  filename: "扫描文件.pdf",
  status: "failed",
  parser_version: null,
  chunker_version: null,
  page_count: 0,
  chunk_count: 0,
  error_code: "DOCUMENT_NO_EXTRACTABLE_TEXT",
  error_message: "PDF 没有可提取文本",
};

/** 挂载知识库页，并提供真实 Pinia Store 和最小路由环境。 */
async function mountKnowledgeBasesView() {
  const pinia = createPinia();
  setActivePinia(pinia);
  const authStore = useAuthStore();
  authStore.token = "access-token";
  authStore.user = {
    id: "user-1",
    email: "demo@example.com",
    is_active: true,
    created_at: "2026-08-11T00:00:00Z",
    updated_at: "2026-08-11T00:00:00Z",
  };
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: "/", name: "home", component: { template: "<p>会话列表</p>" } },
      {
        path: "/knowledge-bases",
        name: "knowledge-bases",
        component: KnowledgeBasesView,
      },
      { path: "/login", name: "login", component: { template: "<p>登录页</p>" } },
    ],
  });
  await router.push("/knowledge-bases");
  await router.isReady();
  const wrapper = mount(KnowledgeBasesView, {
    global: { plugins: [pinia, router] },
  });
  return { wrapper, router, pinia };
}

/** 创建由测试手动结束的异步请求，用于观察提交中的真实按钮状态。 */
function deferred<T>() {
  let resolvePromise!: (value: T) => void;
  const promise = new Promise<T>((resolve) => {
    resolvePromise = resolve;
  });
  return { promise, resolve: resolvePromise };
}

beforeEach(() => {
  sessionStorage.clear();
  vi.clearAllMocks();
  vi.mocked(documentsApi.listDocuments).mockResolvedValue([]);
});

afterEach(() => {
  // 组件用例可能启动文档轮询，结束时统一释放计时器。
  useDocumentStore().reset();
});

describe("KnowledgeBasesView", () => {
  it("挂载后读取并展示当前用户的知识库列表", async () => {
    vi.mocked(knowledgeBasesApi.listKnowledgeBases).mockResolvedValue([
      knowledgeBase,
    ]);

    const { wrapper } = await mountKnowledgeBasesView();
    await flushPromises();

    expect(knowledgeBasesApi.listKnowledgeBases).toHaveBeenCalledWith(
      "access-token",
      {},
    );
    const list = wrapper.get('[data-test="knowledge-base-list"]');
    expect(list.text()).toContain("项目资料库");
    expect(list.text()).toContain("保存项目规范和会议资料");
    expect(list.text()).toContain("mock-hash-1024");
    expect(list.text()).toContain("1024 维");
  });

  it("知识库为空时显示业务空状态", async () => {
    vi.mocked(knowledgeBasesApi.listKnowledgeBases).mockResolvedValue([]);

    const { wrapper } = await mountKnowledgeBasesView();
    await flushPromises();

    expect(wrapper.get('[data-test="knowledge-base-empty-state"]').text()).toContain(
      "还没有知识库",
    );
  });

  it("名称为空或描述过长时显示中文校验错误且不发送请求", async () => {
    vi.mocked(knowledgeBasesApi.listKnowledgeBases).mockResolvedValue([]);
    const { wrapper } = await mountKnowledgeBasesView();
    await flushPromises();

    await wrapper.get('[data-test="knowledge-base-create-form"]').trigger("submit");
    expect(wrapper.get('[data-test="knowledge-base-form-error"]').text()).toBe(
      "知识库名称不能为空",
    );

    await wrapper.get('[data-test="knowledge-base-name"]').setValue("项目资料库");
    await wrapper
      .get('[data-test="knowledge-base-description"]')
      .setValue("说".repeat(4001));
    await wrapper.get('[data-test="knowledge-base-create-form"]').trigger("submit");

    expect(wrapper.get('[data-test="knowledge-base-form-error"]').text()).toBe(
      "知识库描述不能超过 4000 个字符",
    );
    expect(knowledgeBasesApi.createKnowledgeBase).not.toHaveBeenCalled();
  });

  it("创建期间禁用表单，成功后清空输入并立即展示新知识库", async () => {
    vi.mocked(knowledgeBasesApi.listKnowledgeBases).mockResolvedValue([]);
    const createRequest = deferred<KnowledgeBaseResponse>();
    vi.mocked(knowledgeBasesApi.createKnowledgeBase).mockReturnValue(
      createRequest.promise,
    );
    const { wrapper } = await mountKnowledgeBasesView();
    await flushPromises();

    await wrapper
      .get('[data-test="knowledge-base-name"]')
      .setValue("  项目资料库  ");
    await wrapper
      .get('[data-test="knowledge-base-description"]')
      .setValue("  保存项目规范和会议资料  ");
    await wrapper.get('[data-test="knowledge-base-create-form"]').trigger("submit");
    await wrapper.vm.$nextTick();

    const submitButton = wrapper.get('[data-test="create-knowledge-base"]');
    expect(submitButton.text()).toBe("正在创建…");
    expect(submitButton.attributes("disabled")).toBeDefined();

    createRequest.resolve(knowledgeBase);
    await flushPromises();

    expect(knowledgeBasesApi.createKnowledgeBase).toHaveBeenCalledWith(
      "access-token",
      {
        name: "项目资料库",
        description: "保存项目规范和会议资料",
      },
    );
    expect(wrapper.get('[data-test="knowledge-base-list"]').text()).toContain(
      "项目资料库",
    );
    expect(
      (wrapper.get('[data-test="knowledge-base-name"]').element as HTMLInputElement)
        .value,
    ).toBe("");
    expect(wrapper.get('[data-test="knowledge-base-create-success"]').text()).toContain(
      "知识库创建成功",
    );
  });

  it("创建失败时保留已有知识库并展示后端中文错误", async () => {
    vi.mocked(knowledgeBasesApi.listKnowledgeBases).mockResolvedValue([
      knowledgeBase,
    ]);
    vi.mocked(knowledgeBasesApi.createKnowledgeBase).mockRejectedValue(
      new ApiError("知识库名称已经存在", {
        status: 409,
        code: "KNOWLEDGE_BASE_NAME_EXISTS",
      }),
    );
    const { wrapper } = await mountKnowledgeBasesView();
    await flushPromises();

    await wrapper
      .get('[data-test="knowledge-base-name"]')
      .setValue("项目资料库");
    await wrapper.get('[data-test="knowledge-base-create-form"]').trigger("submit");
    await flushPromises();

    expect(wrapper.text()).toContain("知识库名称已经存在");
    expect(wrapper.get('[data-test="knowledge-base-list"]').text()).toContain(
      "项目资料库",
    );
  });

  it("可以切换到包含归档知识库的列表", async () => {
    vi.mocked(knowledgeBasesApi.listKnowledgeBases)
      .mockResolvedValueOnce([knowledgeBase])
      .mockResolvedValueOnce([knowledgeBase, archivedKnowledgeBase]);
    const { wrapper } = await mountKnowledgeBasesView();
    await flushPromises();

    await wrapper.get('[data-test="toggle-archived-knowledge-bases"]').trigger("click");
    await flushPromises();

    expect(knowledgeBasesApi.listKnowledgeBases).toHaveBeenLastCalledWith(
      "access-token",
      { includeArchived: true },
    );
    expect(wrapper.get('[data-test="archived-knowledge-base-badge"]').text()).toBe(
      "已归档",
    );
  });

  it("首次加载失败时可以重新加载", async () => {
    vi.mocked(knowledgeBasesApi.listKnowledgeBases)
      .mockRejectedValueOnce(new Error("网络不可用"))
      .mockResolvedValueOnce([knowledgeBase]);
    const { wrapper } = await mountKnowledgeBasesView();
    await flushPromises();

    expect(wrapper.get('[role="alert"]').text()).toContain("加载知识库失败");
    await wrapper.get('[data-test="retry-load-knowledge-bases"]').trigger("click");
    await flushPromises();

    expect(knowledgeBasesApi.listKnowledgeBases).toHaveBeenCalledTimes(2);
    expect(wrapper.get('[data-test="knowledge-base-list"]').text()).toContain(
      "项目资料库",
    );
  });

  it("编辑对话框复制当前值，成功后使用后端响应替换卡片", async () => {
    const updatedKnowledgeBase: KnowledgeBaseResponse = {
      ...knowledgeBase,
      name: "项目交付资料库",
      description: null,
      updated_at: "2026-08-11T02:00:00Z",
    };
    vi.mocked(knowledgeBasesApi.listKnowledgeBases).mockResolvedValue([
      knowledgeBase,
    ]);
    vi.mocked(knowledgeBasesApi.updateKnowledgeBase).mockResolvedValue(
      updatedKnowledgeBase,
    );
    const { wrapper } = await mountKnowledgeBasesView();
    await flushPromises();

    await wrapper
      .get('[data-test="edit-knowledge-base-knowledge-base-1"]')
      .trigger("click");

    expect(
      (wrapper.get('[data-test="edit-knowledge-base-name"]').element as HTMLInputElement)
        .value,
    ).toBe("项目资料库");
    expect(
      (wrapper.get('[data-test="edit-knowledge-base-description"]')
        .element as HTMLTextAreaElement).value,
    ).toBe("保存项目规范和会议资料");

    await wrapper
      .get('[data-test="edit-knowledge-base-name"]')
      .setValue("  项目交付资料库  ");
    await wrapper
      .get('[data-test="edit-knowledge-base-description"]')
      .setValue("   ");
    await wrapper.get('[data-test="edit-knowledge-base-form"]').trigger("submit");
    await flushPromises();

    expect(knowledgeBasesApi.updateKnowledgeBase).toHaveBeenCalledWith(
      "access-token",
      knowledgeBase.id,
      { name: "项目交付资料库", description: null },
    );
    expect(wrapper.get('[data-test="knowledge-base-list"]').text()).toContain(
      "项目交付资料库",
    );
    expect(wrapper.text()).toContain("暂无描述");
    expect(wrapper.find('[data-test="edit-knowledge-base-dialog"]').exists()).toBe(
      false,
    );
  });

  it("编辑名称为空时保留对话框并阻止 PATCH 请求", async () => {
    vi.mocked(knowledgeBasesApi.listKnowledgeBases).mockResolvedValue([
      knowledgeBase,
    ]);
    const { wrapper } = await mountKnowledgeBasesView();
    await flushPromises();
    await wrapper
      .get('[data-test="edit-knowledge-base-knowledge-base-1"]')
      .trigger("click");

    await wrapper.get('[data-test="edit-knowledge-base-name"]').setValue("   ");
    await wrapper.get('[data-test="edit-knowledge-base-form"]').trigger("submit");

    expect(wrapper.get('[data-test="edit-knowledge-base-error"]').text()).toBe(
      "知识库名称不能为空",
    );
    expect(knowledgeBasesApi.updateKnowledgeBase).not.toHaveBeenCalled();
    expect(wrapper.find('[data-test="edit-knowledge-base-dialog"]').exists()).toBe(
      true,
    );
  });

  it("编辑被后端拒绝时保留原卡片和对话框中的单项错误", async () => {
    vi.mocked(knowledgeBasesApi.listKnowledgeBases).mockResolvedValue([
      knowledgeBase,
    ]);
    vi.mocked(knowledgeBasesApi.updateKnowledgeBase).mockRejectedValue(
      new ApiError("知识库名称已经存在", {
        status: 409,
        code: "KNOWLEDGE_BASE_NAME_EXISTS",
      }),
    );
    const { wrapper } = await mountKnowledgeBasesView();
    await flushPromises();
    await wrapper
      .get('[data-test="edit-knowledge-base-knowledge-base-1"]')
      .trigger("click");
    await wrapper
      .get('[data-test="edit-knowledge-base-name"]')
      .setValue("产品手册库");

    await wrapper.get('[data-test="edit-knowledge-base-form"]').trigger("submit");
    await flushPromises();

    expect(wrapper.get('[data-test="edit-knowledge-base-error"]').text()).toBe(
      "知识库名称已经存在",
    );
    expect(wrapper.get('[data-test="knowledge-base-list"]').text()).toContain(
      "项目资料库",
    );
    expect(wrapper.get('[data-test="knowledge-base-list"]').text()).not.toContain(
      "产品手册库",
    );
    expect(wrapper.find('[data-test="edit-knowledge-base-dialog"]').exists()).toBe(
      true,
    );
  });

  it("归档前显示可恢复说明，确认成功后从活动列表移除", async () => {
    vi.mocked(knowledgeBasesApi.listKnowledgeBases).mockResolvedValue([
      knowledgeBase,
    ]);
    const archiveRequest = deferred<void>();
    vi.mocked(knowledgeBasesApi.archiveKnowledgeBase).mockReturnValue(
      archiveRequest.promise,
    );
    const { wrapper } = await mountKnowledgeBasesView();
    await flushPromises();

    await wrapper
      .get('[data-test="archive-knowledge-base-knowledge-base-1"]')
      .trigger("click");
    expect(wrapper.get('[data-test="archive-knowledge-base-dialog"]').text()).toContain(
      "不会物理删除文档",
    );
    expect(knowledgeBasesApi.archiveKnowledgeBase).not.toHaveBeenCalled();

    await wrapper.get('[data-test="confirm-archive-knowledge-base"]').trigger("click");
    await wrapper.vm.$nextTick();
    const confirmButton = wrapper.get(
      '[data-test="confirm-archive-knowledge-base"]',
    );
    expect(confirmButton.text()).toBe("正在归档…");
    expect(confirmButton.attributes("disabled")).toBeDefined();

    archiveRequest.resolve(undefined);
    await flushPromises();

    expect(knowledgeBasesApi.archiveKnowledgeBase).toHaveBeenCalledWith(
      "access-token",
      knowledgeBase.id,
    );
    expect(wrapper.get('[data-test="knowledge-base-empty-state"]').text()).toContain(
      "还没有知识库",
    );
    expect(wrapper.find('[data-test="archive-knowledge-base-dialog"]').exists()).toBe(
      false,
    );
  });

  it("归档失败时保留确认框、原卡片和后端中文错误", async () => {
    vi.mocked(knowledgeBasesApi.listKnowledgeBases).mockResolvedValue([
      knowledgeBase,
    ]);
    vi.mocked(knowledgeBasesApi.archiveKnowledgeBase).mockRejectedValue(
      new ApiError("知识库仍有正在运行的入库任务", {
        status: 409,
        code: "KNOWLEDGE_BASE_HAS_RUNNING_JOBS",
      }),
    );
    const { wrapper } = await mountKnowledgeBasesView();
    await flushPromises();
    await wrapper
      .get('[data-test="archive-knowledge-base-knowledge-base-1"]')
      .trigger("click");
    await wrapper.get('[data-test="confirm-archive-knowledge-base"]').trigger("click");
    await flushPromises();

    expect(wrapper.get('[data-test="archive-knowledge-base-dialog"]').text()).toContain(
      "知识库仍有正在运行的入库任务",
    );
    expect(wrapper.get('[data-test="knowledge-base-list"]').text()).toContain(
      "项目资料库",
    );
  });

  it("已归档知识库可以通过 PATCH 恢复，并重新显示使用中状态", async () => {
    const restoredKnowledgeBase = {
      ...archivedKnowledgeBase,
      is_archived: false,
    };
    vi.mocked(knowledgeBasesApi.listKnowledgeBases).mockResolvedValue([
      archivedKnowledgeBase,
    ]);
    vi.mocked(knowledgeBasesApi.updateKnowledgeBase).mockResolvedValue(
      restoredKnowledgeBase,
    );
    const { wrapper } = await mountKnowledgeBasesView();
    await flushPromises();

    await wrapper
      .get('[data-test="restore-knowledge-base-knowledge-base-2"]')
      .trigger("click");
    await flushPromises();

    expect(knowledgeBasesApi.updateKnowledgeBase).toHaveBeenCalledWith(
      "access-token",
      archivedKnowledgeBase.id,
      { is_archived: false },
    );
    expect(wrapper.get('[data-test="knowledge-base-list"]').text()).toContain(
      "使用中",
    );
    expect(
      wrapper.find('[data-test="archived-knowledge-base-badge"]').exists(),
    ).toBe(false);
  });

  it("展开知识库后加载并展示文档状态、大小和处理统计", async () => {
    vi.mocked(knowledgeBasesApi.listKnowledgeBases).mockResolvedValue([
      knowledgeBase,
    ]);
    vi.mocked(documentsApi.listDocuments).mockResolvedValue([readyDocument]);
    const { wrapper } = await mountKnowledgeBasesView();
    await flushPromises();

    await wrapper
      .get('[data-test="manage-documents-knowledge-base-1"]')
      .trigger("click");
    await flushPromises();

    expect(documentsApi.listDocuments).toHaveBeenCalledWith(
      "access-token",
      knowledgeBase.id,
    );
    const panel = wrapper.get(
      '[data-test="document-panel-knowledge-base-1"]',
    );
    expect(panel.text()).toContain("项目规范.md");
    expect(panel.text()).toContain("可以检索");
    expect(panel.text()).toContain("1 KB");
    expect(panel.text()).toContain("3 页");
    expect(panel.text()).toContain("8 个 Chunk");
  });

  it("不支持或超过 20 MB 的文件在前端被拒绝且不发送上传请求", async () => {
    vi.mocked(knowledgeBasesApi.listKnowledgeBases).mockResolvedValue([
      knowledgeBase,
    ]);
    const { wrapper } = await mountKnowledgeBasesView();
    await flushPromises();
    await wrapper
      .get('[data-test="manage-documents-knowledge-base-1"]')
      .trigger("click");
    await flushPromises();
    const input = wrapper.get(
      '[data-test="document-file-input-knowledge-base-1"]',
    );

    const unsupportedFile = new File(["内容"], "程序.exe", {
      type: "application/octet-stream",
    });
    Object.defineProperty(input.element, "files", {
      configurable: true,
      value: [unsupportedFile],
    });
    await input.trigger("change");
    expect(wrapper.get('[data-test="document-upload-error"]').text()).toContain(
      "只支持 PDF、TXT、Markdown、Word DOCX 和 Excel XLSX",
    );

    const oversizedFile = new File(["内容"], "大文件.pdf", {
      type: "application/pdf",
    });
    Object.defineProperty(oversizedFile, "size", {
      configurable: true,
      value: 20 * 1024 * 1024 + 1,
    });
    Object.defineProperty(input.element, "files", {
      configurable: true,
      value: [oversizedFile],
    });
    await input.trigger("change");
    expect(wrapper.get('[data-test="document-upload-error"]').text()).toContain(
      "不能超过 20 MB",
    );
    expect(documentsApi.uploadDocument).not.toHaveBeenCalled();
  });

  it("Excel XLSX 文件通过前端校验并允许开始上传", async () => {
    vi.mocked(knowledgeBasesApi.listKnowledgeBases).mockResolvedValue([
      knowledgeBase,
    ]);
    const { wrapper } = await mountKnowledgeBasesView();
    await flushPromises();
    await wrapper
      .get('[data-test="manage-documents-knowledge-base-1"]')
      .trigger("click");
    await flushPromises();
    const input = wrapper.get(
      '[data-test="document-file-input-knowledge-base-1"]',
    );
    const file = new File(["OOXML"], "学生名单.xlsx", {
      type: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    });
    Object.defineProperty(input.element, "files", {
      configurable: true,
      value: [file],
    });

    await input.trigger("change");

    expect(wrapper.text()).toContain("已选择：学生名单.xlsx");
    expect(wrapper.find('[data-test="document-upload-error"]').exists()).toBe(
      false,
    );
    expect(
      wrapper
        .get('[data-test="upload-document-knowledge-base-1"]')
        .attributes("disabled"),
    ).toBeUndefined();
  });

  it("上传期间禁用按钮，成功后立即展示等待处理状态", async () => {
    vi.mocked(knowledgeBasesApi.listKnowledgeBases).mockResolvedValue([
      knowledgeBase,
    ]);
    const pendingDocument: DocumentResponse = {
      ...readyDocument,
      status: "pending",
      parser_version: null,
      chunker_version: null,
      page_count: 0,
      chunk_count: 0,
      finished_at: null,
    };
    const uploadRequest = deferred<{
      document: DocumentResponse;
      job_id: string;
      job_status: "pending";
    }>();
    vi.mocked(documentsApi.uploadDocument).mockReturnValue(
      uploadRequest.promise,
    );
    const { wrapper } = await mountKnowledgeBasesView();
    await flushPromises();
    await wrapper
      .get('[data-test="manage-documents-knowledge-base-1"]')
      .trigger("click");
    await flushPromises();
    const file = new File(["OOXML"], "项目方案.docx", {
      type: "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    });
    const input = wrapper.get(
      '[data-test="document-file-input-knowledge-base-1"]',
    );
    Object.defineProperty(input.element, "files", {
      configurable: true,
      value: [file],
    });
    await input.trigger("change");

    await wrapper
      .get('[data-test="upload-document-knowledge-base-1"]')
      .trigger("click");
    await wrapper.vm.$nextTick();
    const uploadButton = wrapper.get(
      '[data-test="upload-document-knowledge-base-1"]',
    );
    expect(uploadButton.text()).toBe("正在上传…");
    expect(uploadButton.attributes("disabled")).toBeDefined();

    uploadRequest.resolve({
      document: pendingDocument,
      job_id: "job-1",
      job_status: "pending",
    });
    await flushPromises();

    expect(documentsApi.uploadDocument).toHaveBeenCalledWith(
      "access-token",
      knowledgeBase.id,
      file,
    );
    expect(wrapper.text()).toContain("等待处理");
  });

  it("失败文档展示错误并可重试，成功后变为等待处理", async () => {
    vi.mocked(knowledgeBasesApi.listKnowledgeBases).mockResolvedValue([
      knowledgeBase,
    ]);
    vi.mocked(documentsApi.listDocuments).mockResolvedValue([failedDocument]);
    vi.mocked(documentsApi.retryDocument).mockResolvedValue({
      document: {
        ...failedDocument,
        status: "pending",
        error_code: null,
        error_message: null,
        finished_at: null,
      },
      job_id: "job-2",
      job_status: "pending",
    });
    const { wrapper } = await mountKnowledgeBasesView();
    await flushPromises();
    await wrapper
      .get('[data-test="manage-documents-knowledge-base-1"]')
      .trigger("click");
    await flushPromises();

    expect(wrapper.text()).toContain("PDF 没有可提取文本");
    await wrapper
      .get('[data-test="retry-document-document-2"]')
      .trigger("click");
    await flushPromises();

    expect(documentsApi.retryDocument).toHaveBeenCalledWith(
      "access-token",
      failedDocument.id,
    );
    expect(wrapper.text()).toContain("等待处理");
  });

  it("删除必须二次确认，失败时保留文档和确认框中的错误", async () => {
    vi.mocked(knowledgeBasesApi.listKnowledgeBases).mockResolvedValue([
      knowledgeBase,
    ]);
    vi.mocked(documentsApi.listDocuments).mockResolvedValue([readyDocument]);
    vi.mocked(documentsApi.deleteDocument).mockRejectedValue(
      new ApiError("对象存储暂时不可用，可再次删除继续清理", {
        status: 503,
        code: "OBJECT_STORAGE_UNAVAILABLE",
      }),
    );
    const { wrapper } = await mountKnowledgeBasesView();
    await flushPromises();
    await wrapper
      .get('[data-test="manage-documents-knowledge-base-1"]')
      .trigger("click");
    await flushPromises();

    await wrapper
      .get('[data-test="delete-document-document-1"]')
      .trigger("click");
    expect(documentsApi.deleteDocument).not.toHaveBeenCalled();
    expect(wrapper.get('[data-test="delete-document-dialog"]').text()).toContain(
      "项目规范.md",
    );

    await wrapper.get('[data-test="confirm-delete-document"]').trigger("click");
    await flushPromises();

    expect(documentsApi.deleteDocument).toHaveBeenCalledWith(
      "access-token",
      readyDocument.id,
    );
    expect(wrapper.get('[data-test="delete-document-dialog"]').text()).toContain(
      "再次删除继续清理",
    );
    expect(wrapper.text()).toContain("项目规范.md");
  });

  it("切换文档面板和卸载页面时停止不再观察的轮询", async () => {
    const secondKnowledgeBase: KnowledgeBaseResponse = {
      ...knowledgeBase,
      id: "knowledge-base-2",
      name: "产品资料库",
    };
    vi.mocked(knowledgeBasesApi.listKnowledgeBases).mockResolvedValue([
      knowledgeBase,
      secondKnowledgeBase,
    ]);
    vi.mocked(documentsApi.listDocuments).mockResolvedValue([]);
    const { wrapper } = await mountKnowledgeBasesView();
    await flushPromises();
    const documentStore = useDocumentStore();
    const stopPolling = vi.spyOn(documentStore, "stopPolling");

    await wrapper
      .get('[data-test="manage-documents-knowledge-base-1"]')
      .trigger("click");
    await flushPromises();
    await wrapper
      .get('[data-test="manage-documents-knowledge-base-2"]')
      .trigger("click");
    await flushPromises();

    expect(stopPolling).toHaveBeenCalledWith("knowledge-base-1");
    wrapper.unmount();
    expect(stopPolling).toHaveBeenCalledWith("knowledge-base-2");
  });
});
