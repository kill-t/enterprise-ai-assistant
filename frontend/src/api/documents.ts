import { requestJson } from "@/api/client";
import type {
  DocumentResponse,
  DocumentRetryResponse,
  DocumentUploadResponse,
} from "@/api/contracts";

/** 对知识库 ID 进行路径编码，防止特殊字符被浏览器解释成额外路径段。 */
function knowledgeBaseDocumentsPath(knowledgeBaseId: string): string {
  return `/api/v1/knowledge-bases/${encodeURIComponent(knowledgeBaseId)}/documents`;
}

/** 统一生成单个文档路径，重试和删除接口都从这个基础路径派生。 */
function documentPath(documentId: string): string {
  return `/api/v1/documents/${encodeURIComponent(documentId)}`;
}

/** 读取当前用户在指定知识库中可见的全部文档。 */
export async function listDocuments(
  token: string,
  knowledgeBaseId: string,
): Promise<DocumentResponse[]> {
  return requestJson<DocumentResponse[]>(
    knowledgeBaseDocumentsPath(knowledgeBaseId),
    {
      method: "GET",
      token,
    },
  );
}

/** 获取单个文档的最新处理状态和统计信息。 */
export async function getDocument(
  token: string,
  documentId: string,
): Promise<DocumentResponse> {
  return requestJson<DocumentResponse>(documentPath(documentId), {
    method: "GET",
    token,
  });
}

/**
 * 使用 multipart/form-data 上传原文件。不要手工设置 Content-Type，浏览器需要自动
 * 生成包含随机 boundary 的请求头，否则 FastAPI 无法正确解析文件边界。
 */
export async function uploadDocument(
  token: string,
  knowledgeBaseId: string,
  file: File,
): Promise<DocumentUploadResponse> {
  const formData = new FormData();
  formData.append("file", file);
  return requestJson<DocumentUploadResponse>(
    knowledgeBaseDocumentsPath(knowledgeBaseId),
    {
      method: "POST",
      token,
      body: formData,
    },
  );
}

/** 为 FAILED 文档创建一次新的持久化入库任务。 */
export async function retryDocument(
  token: string,
  documentId: string,
): Promise<DocumentRetryResponse> {
  return requestJson<DocumentRetryResponse>(
    `${documentPath(documentId)}/retry`,
    {
      method: "POST",
      token,
    },
  );
}

/**
 * 请求后端执行可恢复删除；只有收到 204 后，调用方才能确认本次删除已完成。
 */
export async function deleteDocument(
  token: string,
  documentId: string,
): Promise<void> {
  await requestJson<void>(documentPath(documentId), {
    method: "DELETE",
    token,
  });
}
