import { mount } from "@vue/test-utils";
import { describe, expect, it } from "vitest";

import MessageMarkdownContent from "@/components/MessageMarkdownContent.vue";

describe("MessageMarkdownContent", () => {
  it("把助手 Markdown 渲染为标题、列表、代码和表格", () => {
    const wrapper = mount(MessageMarkdownContent, {
      props: {
        content: [
          "## 申请条件",
          "",
          "- **国籍**：中华人民共和国国籍",
          "- 状态：`在校生`",
          "",
          "> 以下标准来自学校文件。",
          "",
          "| 项目 | 标准 |",
          "| --- | --- |",
          "| 一等助学金 | 3800 元 |",
        ].join("\n"),
      },
    });

    expect(wrapper.get("h2").text()).toBe("申请条件");
    expect(wrapper.get("ul").text()).toContain("中华人民共和国国籍");
    expect(wrapper.get("strong").text()).toBe("国籍");
    expect(wrapper.get("code").text()).toBe("在校生");
    expect(wrapper.get("blockquote").text()).toContain("学校文件");
    expect(wrapper.get("table").text()).toContain("一等助学金");
    expect(wrapper.findAll("th")).toHaveLength(2);
    expect(wrapper.findAll("td")).toHaveLength(2);
  });

  it("清理脚本、事件属性、远程图片和危险链接", () => {
    const wrapper = mount(MessageMarkdownContent, {
      props: {
        content: [
          '<script>alert("xss")</script>',
          '<img src="https://tracker.example/pixel" onerror="alert(1)">',
          '[危险链接](javascript:alert(1))',
          '<a href="https://example.com" onclick="alert(1)">安全链接</a>',
        ].join("\n"),
      },
    });

    expect(wrapper.find("script").exists()).toBe(false);
    expect(wrapper.find("img").exists()).toBe(false);
    expect(wrapper.html()).not.toContain("onerror");
    expect(wrapper.html()).not.toContain("onclick");
    const renderedLinks = wrapper.findAll("a");
    expect(renderedLinks).toHaveLength(1);
    expect(renderedLinks[0]?.attributes("href")).toBe("https://example.com");
    expect(renderedLinks[0]?.text()).toBe("安全链接");
  });

  it("流式正文变化时重新生成安全 HTML", async () => {
    const wrapper = mount(MessageMarkdownContent, {
      props: { content: "## 会议" },
    });

    expect(wrapper.get("h2").text()).toBe("会议");

    await wrapper.setProps({ content: "## 会议纪要\n\n- 已完成需求确认" });

    expect(wrapper.get("h2").text()).toBe("会议纪要");
    expect(wrapper.get("li").text()).toBe("已完成需求确认");
  });

  it("正文为空时显示明确占位文本", () => {
    const wrapper = mount(MessageMarkdownContent, {
      props: { content: "" },
    });

    expect(wrapper.text()).toBe("模型未产生正文");
  });
});
