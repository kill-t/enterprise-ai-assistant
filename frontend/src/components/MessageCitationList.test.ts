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

  it("文档已经删除时保留快照提示，未知页码不会被猜测", () => {
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
    expect(wrapper.text()).toContain("页码未知");
    expect(wrapper.find("a").exists()).toBe(false);
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
