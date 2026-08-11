<script setup lang="ts">
import { computed, ref, watch } from "vue";

import type { KnowledgeBaseResponse } from "@/api/contracts";
import { useConversationKnowledgeBaseStore } from "@/stores/conversation-knowledge-bases";
import { useKnowledgeBaseStore } from "@/stores/knowledge-bases";

const props = withDefaults(
  defineProps<{
    conversationId: string;
    disabled?: boolean;
  }>(),
  { disabled: false },
);

const bindingStore = useConversationKnowledgeBaseStore();
const knowledgeBaseStore = useKnowledgeBaseStore();
const isEditing = ref(false);

/**
 * 活动知识库来自全局列表；后来被归档的既有绑定由绑定响应补回，
 * 避免它从界面消失后用户无法明确解除。
 */
const selectableKnowledgeBases = computed<KnowledgeBaseResponse[]>(() => {
  const byId = new Map<string, KnowledgeBaseResponse>();
  for (const knowledgeBase of knowledgeBaseStore.knowledgeBases) {
    if (!knowledgeBase.is_archived) {
      byId.set(knowledgeBase.id, knowledgeBase);
    }
  }
  for (const knowledgeBase of bindingStore.boundKnowledgeBases) {
    if (!byId.has(knowledgeBase.id)) {
      byId.set(knowledgeBase.id, knowledgeBase);
    }
  }
  return [...byId.values()];
});

const isInitialLoading = computed(
  () =>
    (!bindingStore.hasLoaded && bindingStore.isLoading) ||
    (!knowledgeBaseStore.hasLoaded && knowledgeBaseStore.isLoading),
);

const loadErrorMessage = computed(
  () => bindingStore.errorMessage ?? knowledgeBaseStore.errorMessage,
);

const selectionErrorMessage = computed(
  () => bindingStore.selectionErrorMessage ?? bindingStore.saveErrorMessage,
);

/** 切换会话时关闭旧编辑器，并行加载活动知识库和该会话的服务端绑定。 */
async function loadContext(conversationId: string): Promise<void> {
  isEditing.value = false;
  await Promise.all([
    knowledgeBaseStore.loadKnowledgeBases(),
    bindingStore.load(conversationId),
  ]);
}

/** 每次打开编辑器都从最近服务端快照开始，丢弃上次已取消的草稿。 */
function openEditor(): void {
  if (props.disabled || bindingStore.isSaving) {
    return;
  }
  bindingStore.discardChanges();
  isEditing.value = true;
}

function closeEditor(): void {
  if (bindingStore.isSaving) {
    return;
  }
  bindingStore.discardChanges();
  isEditing.value = false;
}

/** checkbox 的 checked 由 Store 单向决定，change 事件只提交一次切换意图。 */
function toggleKnowledgeBase(knowledgeBaseId: string): void {
  if (!props.disabled && !bindingStore.isSaving) {
    bindingStore.toggleSelection(knowledgeBaseId);
  }
}

/** 保存成功后收起编辑器；失败时保留草稿和错误，便于修正或重试。 */
async function saveSelection(): Promise<void> {
  if (await bindingStore.save()) {
    isEditing.value = false;
  }
}

/** 已归档且未选择的知识库不能重新勾选；既有选择必须允许取消。 */
function optionDisabled(knowledgeBase: KnowledgeBaseResponse): boolean {
  return (
    props.disabled ||
    bindingStore.isSaving ||
    (knowledgeBase.is_archived && !bindingStore.isSelected(knowledgeBase.id))
  );
}

watch(
  () => props.conversationId,
  (conversationId) => {
    void loadContext(conversationId);
  },
  { immediate: true },
);
</script>

<template>
  <section class="knowledge-context-panel" aria-labelledby="knowledge-context-title">
    <header class="knowledge-context-header">
      <div>
        <p class="section-label">RAG Context</p>
        <h2 id="knowledge-context-title">会话知识库</h2>
      </div>
      <button
        class="text-button"
        data-test="configure-knowledge-bases"
        type="button"
        :aria-expanded="isEditing"
        aria-controls="knowledge-base-selector-editor"
        :disabled="disabled || bindingStore.isLoading || bindingStore.isSaving"
        @click="openEditor"
      >
        {{ isEditing ? "正在配置" : "配置知识库" }}
      </button>
    </header>

    <p v-if="disabled" class="knowledge-context-busy" role="status">
      AI 生成结束后可修改知识库绑定。
    </p>

    <p v-if="isInitialLoading" class="knowledge-context-status" role="status">
      正在加载会话知识库…
    </p>

    <div v-else-if="loadErrorMessage && !bindingStore.hasLoaded" class="knowledge-context-error" role="alert">
      <p>{{ loadErrorMessage }}</p>
      <button class="text-button" type="button" @click="loadContext(conversationId)">
        重新加载
      </button>
    </div>

    <div v-else data-test="knowledge-context-summary" class="knowledge-context-summary">
      <template v-if="bindingStore.boundKnowledgeBases.length > 0">
        <p>当前会话保存了以下绑定；已归档的资料空间不会参与检索：</p>
        <ul class="bound-knowledge-base-list">
          <li v-for="knowledgeBase in bindingStore.boundKnowledgeBases" :key="knowledgeBase.id">
            <span>{{ knowledgeBase.name }}</span>
            <small v-if="knowledgeBase.is_archived">已归档，不参与检索</small>
          </li>
        </ul>
      </template>
      <p v-else>
        未绑定知识库，后续消息将使用普通聊天，不会执行企业资料检索。
      </p>
      <RouterLink
        v-if="knowledgeBaseStore.knowledgeBases.length === 0"
        class="text-button knowledge-context-link"
        data-test="open-knowledge-bases"
        :to="{ name: 'knowledge-bases' }"
      >
        前往创建知识库
      </RouterLink>
    </div>

    <div
      v-if="isEditing"
      id="knowledge-base-selector-editor"
      class="knowledge-base-selector-editor"
      data-test="knowledge-base-selector-editor"
    >
      <div class="knowledge-base-selector-heading">
        <div>
          <h3>选择检索范围</h3>
          <p>最多选择 5 个。保存后，新消息才会使用新的绑定。</p>
        </div>
        <span>{{ bindingStore.selectedKnowledgeBaseIds.length }} / 5</span>
      </div>

      <p v-if="selectableKnowledgeBases.length === 0" class="knowledge-selector-empty">
        当前没有可选择的知识库，请先创建并上传资料。
      </p>
      <ul v-else class="knowledge-base-option-list">
        <li v-for="knowledgeBase in selectableKnowledgeBases" :key="knowledgeBase.id">
          <label
            class="knowledge-base-option"
            :class="{ 'knowledge-base-option--archived': knowledgeBase.is_archived }"
          >
            <input
              type="checkbox"
              :data-test="`knowledge-base-option-${knowledgeBase.id}`"
              :checked="bindingStore.isSelected(knowledgeBase.id)"
              :disabled="optionDisabled(knowledgeBase)"
              @change="toggleKnowledgeBase(knowledgeBase.id)"
            >
            <span>
              <strong>{{ knowledgeBase.name }}</strong>
              <small>{{ knowledgeBase.description ?? "暂无描述" }}</small>
              <em v-if="knowledgeBase.is_archived">已归档，请取消选择</em>
            </span>
          </label>
        </li>
      </ul>

      <p
        v-if="selectionErrorMessage"
        class="knowledge-context-error-message"
        data-test="knowledge-base-selection-error"
        role="alert"
      >
        {{ selectionErrorMessage }}
      </p>
      <div class="knowledge-base-selector-actions">
        <button class="secondary-button" type="button" :disabled="bindingStore.isSaving" @click="closeEditor">
          取消
        </button>
        <button
          class="primary-button"
          data-test="save-knowledge-base-selection"
          type="button"
          :disabled="disabled || bindingStore.isSaving || !bindingStore.hasUnsavedChanges"
          @click="saveSelection"
        >
          {{ bindingStore.isSaving ? "正在保存…" : "保存选择" }}
        </button>
      </div>
    </div>
  </section>
</template>
