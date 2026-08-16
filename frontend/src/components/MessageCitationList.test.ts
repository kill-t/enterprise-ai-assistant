import { mount } from "@vue/test-utils";
import { describe, expect, it } from "vitest";

import type { MessageSource } from "@/api/contracts";
import MessageCitationList from "@/components/MessageCitationList.vue";

const firstSource: MessageSource = {
  knowledge_base_id: "knowledge-base-1",
  knowledge_base_name: "项目资料库",
  document_id: "document-1",
  chunk_id: "chunk-1",
  filename: "项目计划.pdf",
  chunk_index: 4,
  content: "项目第一阶段需要先完成需求确认和风险评审。",
  heading_path: "实施计划 / 第一阶段",
  page_start: 3,
  page_end: 5,
  source_type: "pdf",
  source_locator: {
    kind: "page",
    page_start: 3,
    page_end: 5,
    heading_path: ["实施计划", "第一阶段"],
  },
  source_order: 1,
  score: 0.91,
};

const secondSource: MessageSource = {
  ...firstSource,
  document_id: "document-2",
  chunk_id: "chunk-2",
  filename: "会议纪要.md",
  content: "会议确认下周一开始开发。",
  heading_path: "会议决定",
  page_start: null,
  page_end: null,
  source_type: "markdown",
  source_locator: {
    kind: "section",
    heading_path: ["会议决定"],
    block_kind: null,
    block_index: null,
  },
  source_order: 2,
  score: 0.83,
};

describe("MessageCitationList", () => {
  it("按来源编号排序并展示文件、页码、知识库和标题路径", () => {
    const wrapper = mount(MessageCitationList, {
      props: { sources: [secondSource, firstSource] },
    });

    const items = wrapper.findAll('[data-test^="citation-item-"]');
    expect(items).toHaveLength(2);
    expect(items[0].text()).toContain("[S1]");
    expect(items[0].text()).toContain("项目计划.pdf");
    expect(items[0].text()).toContain("第 3–5 页");
    expect(items[0].text()).toContain("项目资料库");
    expect(items[0].text()).toContain("实施计划 / 第一阶段");
    expect(items[1].text()).toContain("[S2]");
  });

  it("文档已经删除时仍用快照展示结构化章节位置", () => {
    const wrapper = mount(MessageCitationList, {
      props: {
        sources: [
          {
            ...secondSource,
            knowledge_base_id: null,
            document_id: null,
            chunk_id: null,
          },
        ],
      },
    });

    expect(wrapper.text()).toContain("历史快照");
    expect(wrapper.text()).toContain("会议决定");
    expect(wrapper.find("a").exists()).toBe(false);
  });

  it("展示 Excel 单元格范围和 OCR 页码置信度", () => {
    const wrapper = mount(MessageCitationList, {
      props: {
        sources: [
          {
            ...secondSource,
            filename: "助学金标准.xlsx",
            source_type: "xlsx",
            source_locator: {
              kind: "sheet_range",
              sheet_name: "资助标准",
              cell_range: "A2:F12",
              header_range: "A1:F1",
            },
          },
          {
            ...firstSource,
            filename: "扫描通知.pdf",
            source_order: 2,
            source_locator: {
              kind: "ocr_region",
              page_number: 4,
              bounding_box: {
                x_min: 0.1,
                y_min: 0.2,
                x_max: 0.8,
                y_max: 0.7,
              },
              confidence: 0.914,
              engine: "paddleocr",
            },
          },
        ],
      },
    });

    expect(wrapper.text()).toContain("资助标准!A2:F12");
    expect(wrapper.text()).toContain("第 4 页 · OCR 置信度 91%");
  });

  it("长片段默认截短，并允许用户展开和收起原文", async () => {
    const longContent = `${"这是需要折叠的原文内容。".repeat(18)}片段结尾`;
    const wrapper = mount(MessageCitationList, {
      props: { sources: [{ ...firstSource, content: longContent }] },
    });

    const snippet = wrapper.get('[data-test="citation-snippet-1"]');
    const toggle = wrapper.get('[data-test="citation-toggle-1"]');
    expect(snippet.text()).not.toContain("片段结尾");
    expect(toggle.text()).toBe("展开原文");
    expect(toggle.attributes("aria-expanded")).toBe("false");

    await toggle.trigger("click");

    expect(snippet.text()).toBe(longContent);
    expect(toggle.text()).toBe("收起原文");
    expect(toggle.attributes("aria-expanded")).toBe("true");

    await toggle.trigger("click");

    expect(snippet.text()).not.toContain("片段结尾");
  });

  it("没有引用时不渲染空标题和空卡片", () => {
    const wrapper = mount(MessageCitationList, { props: { sources: [] } });

    expect(wrapper.find('[data-test="citation-list"]').exists()).toBe(false);
  });
});
