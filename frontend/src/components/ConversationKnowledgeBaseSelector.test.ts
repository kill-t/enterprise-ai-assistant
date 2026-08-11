import { flushPromises, mount } from "@vue/test-utils";
import { createPinia, setActivePinia } from "pinia";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { createMemoryHistory, createRouter } from "vue-router";

import * as bindingApi from "@/api/conversation-knowledge-bases";
import * as knowledgeBasesApi from "@/api/knowledge-bases";
import { ApiError } from "@/api/client";
import type { KnowledgeBaseResponse } from "@/api/contracts";
import ConversationKnowledgeBaseSelector from "@/components/ConversationKnowledgeBaseSelector.vue";
import { useAuthStore } from "@/stores/auth";

vi.mock("@/api/conversation-knowledge-bases", () => ({
  getConversationKnowledgeBases: vi.fn(),
  replaceConversationKnowledgeBases: vi.fn(),
}));

vi.mock("@/api/knowledge-bases", () => ({
  archiveKnowledgeBase: vi.fn(),
  createKnowledgeBase: vi.fn(),
  getKnowledgeBase: vi.fn(),
  listKnowledgeBases: vi.fn(),
  updateKnowledgeBase: vi.fn(),
}));

const knowledgeBase = (index: number, archived = false): KnowledgeBaseResponse => ({
  id: `knowledge-base-${index}`,
  name: `资料库 ${index}`,
  description: index === 1 ? "项目规范和会议资料" : null,
  embedding_provider: "mock",
  embedding_model: "mock-hash-1024",
  embedding_dimension: 1024,
  is_archived: archived,
  created_at: "2026-08-11T01:00:00Z",
  updated_at: "2026-08-11T01:00:00Z",
});

function deferred<T>() {
  let resolvePromise!: (value: T) => void;
  const promise = new Promise<T>((resolve) => {
    resolvePromise = resolve;
  });
  return { promise, resolve: resolvePromise };
}

async function mountSelector(disabled = false) {
  const pinia = createPinia();
  setActivePinia(pinia);
  useAuthStore().token = "access-token";
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: "/", name: "home", component: { template: "<p>首页</p>" } },
      {
        path: "/knowledge-bases",
        name: "knowledge-bases",
        component: { template: "<p>知识库</p>" },
      },
    ],
  });
  await router.push("/");
  await router.isReady();
  const wrapper = mount(ConversationKnowledgeBaseSelector, {
    props: { conversationId: "conversation-1", disabled },
    global: { plugins: [pinia, router] },
  });
  return wrapper;
}

beforeEach(() => {
  sessionStorage.clear();
  vi.clearAllMocks();
  vi.mocked(knowledgeBasesApi.listKnowledgeBases).mockResolvedValue([
    knowledgeBase(1),
    knowledgeBase(2),
  ]);
  vi.mocked(bindingApi.getConversationKnowledgeBases).mockResolvedValue({
    conversation_id: "conversation-1",
    knowledge_bases: [knowledgeBase(1)],
  });
});

describe("ConversationKnowledgeBaseSelector", () => {
  it("加载后展示真实持久化绑定，编辑器默认收起", async () => {
    const wrapper = await mountSelector();
    await flushPromises();

    expect(bindingApi.getConversationKnowledgeBases).toHaveBeenCalledWith(
      "access-token",
      "conversation-1",
    );
    expect(knowledgeBasesApi.listKnowledgeBases).toHaveBeenCalledWith(
      "access-token",
      {},
    );
    expect(wrapper.get('[data-test="knowledge-context-summary"]').text()).toContain(
      "资料库 1",
    );
    expect(wrapper.find('[data-test="knowledge-base-selector-editor"]').exists()).toBe(
      false,
    );
  });

  it("没有绑定时明确说明后续消息使用普通聊天", async () => {
    vi.mocked(knowledgeBasesApi.listKnowledgeBases).mockResolvedValue([]);
    vi.mocked(bindingApi.getConversationKnowledgeBases).mockResolvedValue({
      conversation_id: "conversation-1",
      knowledge_bases: [],
    });
    const wrapper = await mountSelector();
    await flushPromises();

    expect(wrapper.get('[data-test="knowledge-context-summary"]').text()).toContain(
      "未绑定知识库，后续消息将使用普通聊天",
    );
    expect(wrapper.get('[data-test="open-knowledge-bases"]').attributes("href")).toBe(
      "/knowledge-bases",
    );
  });

  it("编辑草稿并保存时禁用操作，成功后摘要采用后端响应", async () => {
    const saveRequest = deferred<{
      conversation_id: string;
      knowledge_bases: KnowledgeBaseResponse[];
    }>();
    vi.mocked(bindingApi.replaceConversationKnowledgeBases).mockReturnValue(
      saveRequest.promise,
    );
    const wrapper = await mountSelector();
    await flushPromises();
    await wrapper.get('[data-test="configure-knowledge-bases"]').trigger("click");

    await wrapper
      .get('[data-test="knowledge-base-option-knowledge-base-1"]')
      .setValue(false);
    await wrapper
      .get('[data-test="knowledge-base-option-knowledge-base-2"]')
      .setValue(true);
    await wrapper.get('[data-test="save-knowledge-base-selection"]').trigger("click");
    await wrapper.vm.$nextTick();

    const saveButton = wrapper.get(
      '[data-test="save-knowledge-base-selection"]',
    );
    expect(saveButton.text()).toBe("正在保存…");
    expect(saveButton.attributes("disabled")).toBeDefined();

    saveRequest.resolve({
      conversation_id: "conversation-1",
      knowledge_bases: [knowledgeBase(2)],
    });
    await flushPromises();

    expect(bindingApi.replaceConversationKnowledgeBases).toHaveBeenCalledWith(
      "access-token",
      "conversation-1",
      { knowledge_base_ids: ["knowledge-base-2"] },
    );
    expect(wrapper.get('[data-test="knowledge-context-summary"]').text()).toContain(
      "资料库 2",
    );
    expect(wrapper.get('[data-test="knowledge-context-summary"]').text()).not.toContain(
      "资料库 1",
    );
  });

  it("保存失败时保留编辑器、草稿和后端中文错误", async () => {
    vi.mocked(bindingApi.replaceConversationKnowledgeBases).mockRejectedValue(
      new ApiError("绑定服务暂时不可用", {
        status: 503,
        code: "SERVICE_UNAVAILABLE",
      }),
    );
    const wrapper = await mountSelector();
    await flushPromises();
    await wrapper.get('[data-test="configure-knowledge-bases"]').trigger("click");
    await wrapper
      .get('[data-test="knowledge-base-option-knowledge-base-2"]')
      .setValue(true);

    await wrapper.get('[data-test="save-knowledge-base-selection"]').trigger("click");
    await flushPromises();

    expect(wrapper.get('[data-test="knowledge-base-selection-error"]').text()).toBe(
      "绑定服务暂时不可用",
    );
    expect(
      (wrapper.get('[data-test="knowledge-base-option-knowledge-base-2"]')
        .element as HTMLInputElement).checked,
    ).toBe(true);
    expect(wrapper.find('[data-test="knowledge-base-selector-editor"]').exists()).toBe(
      true,
    );
  });

  it("已归档旧绑定仍然可见，并允许用户取消后保存", async () => {
    vi.mocked(bindingApi.getConversationKnowledgeBases).mockResolvedValue({
      conversation_id: "conversation-1",
      knowledge_bases: [knowledgeBase(3, true)],
    });
    vi.mocked(bindingApi.replaceConversationKnowledgeBases).mockResolvedValue({
      conversation_id: "conversation-1",
      knowledge_bases: [],
    });
    const wrapper = await mountSelector();
    await flushPromises();

    expect(wrapper.text()).toContain("资料库 3");
    expect(wrapper.text()).toContain("已归档，不参与检索");
    await wrapper.get('[data-test="configure-knowledge-bases"]').trigger("click");
    const archivedOption = wrapper.get(
      '[data-test="knowledge-base-option-knowledge-base-3"]',
    );
    expect((archivedOption.element as HTMLInputElement).checked).toBe(true);
    expect(archivedOption.attributes("disabled")).toBeUndefined();

    await archivedOption.setValue(false);
    await wrapper.get('[data-test="save-knowledge-base-selection"]').trigger("click");
    await flushPromises();

    expect(bindingApi.replaceConversationKnowledgeBases).toHaveBeenCalledWith(
      "access-token",
      "conversation-1",
      { knowledge_base_ids: [] },
    );
    expect(wrapper.text()).toContain("未绑定知识库");
  });

  it("选择第六个知识库时展示上限错误", async () => {
    vi.mocked(knowledgeBasesApi.listKnowledgeBases).mockResolvedValue(
      Array.from({ length: 6 }, (_value, index) => knowledgeBase(index + 1)),
    );
    vi.mocked(bindingApi.getConversationKnowledgeBases).mockResolvedValue({
      conversation_id: "conversation-1",
      knowledge_bases: [],
    });
    const wrapper = await mountSelector();
    await flushPromises();
    await wrapper.get('[data-test="configure-knowledge-bases"]').trigger("click");

    for (let index = 1; index <= 6; index += 1) {
      await wrapper
        .get(`[data-test="knowledge-base-option-knowledge-base-${index}"]`)
        .setValue(true);
    }

    expect(wrapper.get('[data-test="knowledge-base-selection-error"]').text()).toBe(
      "每个会话最多选择 5 个知识库",
    );
  });

  it("AI 生成期间禁用绑定配置入口", async () => {
    const wrapper = await mountSelector(true);
    await flushPromises();

    const configureButton = wrapper.get(
      '[data-test="configure-knowledge-bases"]',
    );
    expect(configureButton.attributes("disabled")).toBeDefined();
    expect(wrapper.text()).toContain("生成结束后可修改");
  });
});
