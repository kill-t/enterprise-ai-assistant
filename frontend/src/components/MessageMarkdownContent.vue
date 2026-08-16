<script setup lang="ts">
import DOMPurify from "dompurify";
import { marked } from "marked";
import { computed } from "vue";

const props = defineProps<{
  content: string;
}>();

// 只开放回答展示确实需要的 HTML 标签，避免模型输出图片、表单或可执行节点。
const ALLOWED_MARKDOWN_TAGS = [
  "p",
  "br",
  "h1",
  "h2",
  "h3",
  "h4",
  "h5",
  "h6",
  "strong",
  "em",
  "del",
  "blockquote",
  "ul",
  "ol",
  "li",
  "code",
  "pre",
  "a",
  "table",
  "thead",
  "tbody",
  "tr",
  "th",
  "td",
  "hr",
];

/**
 * 将模型保存的原始 Markdown 转成页面可以展示的安全 HTML。
 * Marked 只负责语法转换，DOMPurify 才是阻止脚本、事件属性和危险链接的安全边界。
 */
const sanitizedHtml = computed(() => {
  const markdown = props.content.trim() || "模型未产生正文";
  const renderedHtml = marked.parse(markdown, {
    async: false,
    breaks: true,
    gfm: true,
  });

  return DOMPurify.sanitize(renderedHtml, {
    ALLOWED_TAGS: ALLOWED_MARKDOWN_TAGS,
    ALLOWED_ATTR: ["href", "title"],
    ALLOW_DATA_ATTR: false,
  });
});
</script>

<template>
  <!-- sanitizedHtml 已经过白名单过滤；任何新增标签或属性都必须先补安全测试。 -->
  <div
    class="message-content message-markdown"
    data-test="assistant-markdown-content"
    v-html="sanitizedHtml"
  />
</template>

<style scoped>
.message-markdown {
  overflow-wrap: anywhere;
  white-space: normal;
}

.message-markdown :deep(> :first-child) {
  margin-top: 0;
}

.message-markdown :deep(> :last-child) {
  margin-bottom: 0;
}

.message-markdown :deep(p),
.message-markdown :deep(ul),
.message-markdown :deep(ol),
.message-markdown :deep(blockquote),
.message-markdown :deep(pre),
.message-markdown :deep(table) {
  margin: 0.8em 0;
}

.message-markdown :deep(h1),
.message-markdown :deep(h2),
.message-markdown :deep(h3),
.message-markdown :deep(h4),
.message-markdown :deep(h5),
.message-markdown :deep(h6) {
  margin: 1.1em 0 0.55em;
  color: #203e36;
  line-height: 1.35;
}

.message-markdown :deep(h1) {
  font-size: 1.45rem;
}

.message-markdown :deep(h2) {
  padding-bottom: 0.35em;
  border-bottom: 1px solid #dce8e2;
  font-size: 1.25rem;
}

.message-markdown :deep(h3) {
  font-size: 1.08rem;
}

.message-markdown :deep(h4),
.message-markdown :deep(h5),
.message-markdown :deep(h6) {
  font-size: 1rem;
}

.message-markdown :deep(ul),
.message-markdown :deep(ol) {
  padding-left: 1.5em;
}

.message-markdown :deep(li + li) {
  margin-top: 0.3em;
}

.message-markdown :deep(blockquote) {
  padding: 0.65em 0.9em;
  border-left: 4px solid #8dbdac;
  color: #4f6a61;
  background: #f0f7f4;
}

.message-markdown :deep(code) {
  padding: 0.14em 0.35em;
  border-radius: 5px;
  color: #8a4937;
  background: #f4ece8;
  font-family: "Cascadia Code", "SFMono-Regular", Consolas, monospace;
  font-size: 0.9em;
}

.message-markdown :deep(pre) {
  overflow-x: auto;
  padding: 0.9em 1em;
  border: 1px solid #d7e4de;
  border-radius: 10px;
  background: #20332e;
}

.message-markdown :deep(pre code) {
  padding: 0;
  color: #eaf4f0;
  background: transparent;
}

.message-markdown :deep(a) {
  color: #147153;
  text-decoration-thickness: 1px;
  text-underline-offset: 3px;
}

.message-markdown :deep(table) {
  display: block;
  width: max-content;
  min-width: 100%;
  max-width: 100%;
  overflow-x: auto;
  border: 1px solid #cfdfd8;
  border-spacing: 0;
  border-collapse: collapse;
  border-radius: 8px;
  white-space: nowrap;
}

.message-markdown :deep(th),
.message-markdown :deep(td) {
  padding: 0.58em 0.75em;
  border-right: 1px solid #dce8e2;
  border-bottom: 1px solid #dce8e2;
  text-align: left;
  vertical-align: top;
}

.message-markdown :deep(th) {
  color: #294b41;
  background: #eaf4ef;
  font-weight: 750;
}

.message-markdown :deep(tbody tr:nth-child(even)) {
  background: #f5f9f7;
}

.message-markdown :deep(tr:last-child td) {
  border-bottom: 0;
}

.message-markdown :deep(th:last-child),
.message-markdown :deep(td:last-child) {
  border-right: 0;
}

.message-markdown :deep(hr) {
  height: 1px;
  margin: 1.1em 0;
  border: 0;
  background: #dce8e2;
}
</style>
