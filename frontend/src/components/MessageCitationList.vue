<script setup lang="ts">
import { computed, ref } from "vue";

import type { MessageSource } from "@/api/contracts";

const props = defineProps<{
  sources: MessageSource[];
}>();

const CONTENT_PREVIEW_LENGTH = 140;
const expandedSourceOrders = ref<Set<number>>(new Set());

/** 使用副本排序，避免展示组件意外修改 Store 中的原始引用顺序。 */
const sortedSources = computed(() =>
  [...props.sources].sort((left, right) => left.source_order - right.source_order),
);

/** 页码缺失时明确显示未知；起止页相同时只显示一个页码。 */
function formatPageRange(source: MessageSource): string {
  if (source.page_start === null) {
    return "页码未知";
  }
  const pageEnd = source.page_end ?? source.page_start;
  if (pageEnd === source.page_start) {
    return `第 ${source.page_start} 页`;
  }
  return `第 ${source.page_start}–${pageEnd} 页`;
}

/** 判断指定来源的原文片段当前是否已经展开。 */
function isExpanded(sourceOrder: number): boolean {
  return expandedSourceOrders.value.has(sourceOrder);
}

/** 返回当前应该展示的片段；短文本无需截断，也不会显示无意义的展开按钮。 */
function visibleContent(source: MessageSource): string {
  if (
    isExpanded(source.source_order) ||
    source.content.length <= CONTENT_PREVIEW_LENGTH
  ) {
    return source.content;
  }
  return `${source.content.slice(0, CONTENT_PREVIEW_LENGTH)}…`;
}

/** 通过创建新 Set 触发 Vue 响应式更新，并保留其他卡片各自的展开状态。 */
function toggleSource(sourceOrder: number): void {
  const nextExpandedSourceOrders = new Set(expandedSourceOrders.value);
  if (nextExpandedSourceOrders.has(sourceOrder)) {
    nextExpandedSourceOrders.delete(sourceOrder);
  } else {
    nextExpandedSourceOrders.add(sourceOrder);
  }
  expandedSourceOrders.value = nextExpandedSourceOrders;
}
</script>

<template>
  <section
    v-if="sortedSources.length > 0"
    class="citation-list"
    data-test="citation-list"
    aria-label="参考来源"
  >
    <h3>参考来源（{{ sortedSources.length }}）</h3>
    <ol class="citation-items">
      <li
        v-for="source in sortedSources"
        :key="`${source.source_order}-${source.chunk_id ?? source.filename}`"
        class="citation-item"
        :data-test="`citation-item-${source.source_order}`"
      >
        <div class="citation-heading">
          <span class="citation-order">[S{{ source.source_order }}]</span>
          <strong>{{ source.filename }}</strong>
          <span v-if="source.document_id === null" class="citation-snapshot">
            历史快照
          </span>
        </div>
        <p class="citation-meta">
          <span>{{ source.knowledge_base_name }}</span>
          <span aria-hidden="true">·</span>
          <span>{{ formatPageRange(source) }}</span>
        </p>
        <p v-if="source.heading_path" class="citation-path">
          {{ source.heading_path }}
        </p>
        <blockquote
          class="citation-snippet"
          :data-test="`citation-snippet-${source.source_order}`"
        >
          {{ visibleContent(source) }}
        </blockquote>
        <button
          v-if="source.content.length > CONTENT_PREVIEW_LENGTH"
          class="citation-toggle"
          :data-test="`citation-toggle-${source.source_order}`"
          type="button"
          :aria-expanded="isExpanded(source.source_order)"
          @click="toggleSource(source.source_order)"
        >
          {{ isExpanded(source.source_order) ? "收起原文" : "展开原文" }}
        </button>
      </li>
    </ol>
  </section>
</template>
