import { requestJson } from "@/api/client";
import type {
  KnowledgeBaseCreateInput,
  KnowledgeBaseResponse,
  KnowledgeBaseUpdateInput,
} from "@/api/contracts";

export interface ListKnowledgeBasesOptions {
  includeArchived?: boolean;
}

/** 统一编码知识库 ID，避免特殊字符改变请求路径或被误解为额外路径段。 */
function knowledgeBasePath(knowledgeBaseId: string): string {
  return `/api/v1/knowledge-bases/${encodeURIComponent(knowledgeBaseId)}`;
}

/** 默认只读取当前用户的活动知识库，可显式包含软归档记录。 */
export async function listKnowledgeBases(
  token: string,
  options: ListKnowledgeBasesOptions = {},
): Promise<KnowledgeBaseResponse[]> {
  const query = options.includeArchived ? "?include_archived=true" : "";
  return requestJson<KnowledgeBaseResponse[]>(
    `/api/v1/knowledge-bases${query}`,
    {
      method: "GET",
      token,
    },
  );
}

/** 读取单个知识库元数据；所有权过滤由后端统一执行。 */
export async function getKnowledgeBase(
  token: string,
  knowledgeBaseId: string,
): Promise<KnowledgeBaseResponse> {
  return requestJson<KnowledgeBaseResponse>(knowledgeBasePath(knowledgeBaseId), {
    method: "GET",
    token,
  });
}

/** 创建当前用户私有知识库，Embedding Provider 和维度不允许由页面伪造。 */
export async function createKnowledgeBase(
  token: string,
  input: KnowledgeBaseCreateInput,
): Promise<KnowledgeBaseResponse> {
  return requestJson<KnowledgeBaseResponse>("/api/v1/knowledge-bases", {
    method: "POST",
    token,
    body: JSON.stringify(input),
  });
}

/** 修改名称、描述或归档状态；具体字段组合仍由 Pydantic 进行最终校验。 */
export async function updateKnowledgeBase(
  token: string,
  knowledgeBaseId: string,
  input: KnowledgeBaseUpdateInput,
): Promise<KnowledgeBaseResponse> {
  return requestJson<KnowledgeBaseResponse>(knowledgeBasePath(knowledgeBaseId), {
    method: "PATCH",
    token,
    body: JSON.stringify(input),
  });
}

/**
 * 后端 DELETE 实际执行软归档，不会物理删除原文件和历史引用；204 响应没有 JSON 正文。
 */
export async function archiveKnowledgeBase(
  token: string,
  knowledgeBaseId: string,
): Promise<void> {
  await requestJson<void>(knowledgeBasePath(knowledgeBaseId), {
    method: "DELETE",
    token,
  });
}
