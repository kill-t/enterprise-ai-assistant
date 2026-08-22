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

/** 根据结构化位置展示真正有意义的页码、标题、单元格或 OCR 区域。 */
function formatSourceLocation(source: MessageSource): string {
  const locator = source.source_locator;
  if (locator.kind === "page") {
    if (locator.page_end === locator.page_start) {
      return `第 ${locator.page_start} 页`;
    }
    return `第 ${locator.page_start}–${locator.page_end} 页`;
  }
  if (locator.kind === "section") {
    const blockLabels = {
      paragraph: "段落",
      list: "列表",
      table: "表格",
    } as const;
    const block =
      locator.block_kind === null
        ? ""
        : ` · ${blockLabels[locator.block_kind]} ${(locator.block_index ?? 0) + 1}`;
    const heading = locator.heading_path.join(" / ") || "文档开头";
    return `${heading}${block}`;
  }
  if (locator.kind === "sheet_range") {
    return `${locator.sheet_name}!${locator.cell_range}`;
  }
  if (locator.kind === "ocr_region") {
    const page = locator.page_number === null ? "图片" : `第 ${locator.page_number} 页`;
    return `${page} · OCR 置信度 ${Math.round(locator.confidence * 100)}%`;
  }
  return "整篇文档";
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
          <span>{{ formatSourceLocation(source) }}</span>
        </p>
        <p
          v-if="source.heading_path && source.source_locator.kind !== 'section'"
          class="citation-path"
        >
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
