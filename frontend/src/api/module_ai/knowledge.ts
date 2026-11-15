import { request } from "@/utils/http";

const API_PATH = "/ai/knowledge";

const KnowledgeAPI = {
  listKnowledgeBase(query: KnowledgeBaseListQuery) {
    return request<ApiResponse<PageResult<KnowledgeBase>>>({
      url: `${API_PATH}/list`,
      method: "get",
      params: query,
    });
  },

  createKnowledgeBase(body: KnowledgeBaseForm) {
    return request<ApiResponse<KnowledgeBase>>({
      url: `${API_PATH}/create`,
      method: "post",
      data: body,
    });
  },

  updateKnowledgeBase(id: number, body: KnowledgeBaseForm) {
    return request<ApiResponse<KnowledgeBase>>({
      url: `${API_PATH}/update/${id}`,
      method: "put",
      data: body,
    });
  },

  optionselect() {
    return request<ApiResponse<KnowledgeBase[]>>({
      url: `${API_PATH}/optionselect`,
      method: "get",
    });
  },

  deleteKnowledgeBase(body: number[]) {
    return request<ApiResponse>({
      url: `${API_PATH}/delete`,
      method: "delete",
      data: body,
    });
  },

  getStats() {
    return request<ApiResponse<KnowledgeStats>>({
      url: `${API_PATH}/stats`,
      method: "get",
      showErrorMessage: false,
    });
  },

  listDocument(query: KnowledgeDocumentListQuery) {
    return request<ApiResponse<PageResult<KnowledgeDocument>>>({
      url: `${API_PATH}/document/list`,
      method: "get",
      params: query,
    });
  },

  downloadDocument(id: number) {
    return request<Blob>({
      url: `${API_PATH}/document/${id}/file`,
      method: "get",
      responseType: "blob",
    });
  },

  listChunks(id: number, query: PageQuery) {
    return request<ApiResponse<PageResult<KnowledgeChunk>>>({
      url: `${API_PATH}/document/${id}/chunks`,
      method: "get",
      params: query,
    });
  },

  documentContent(id: number) {
    return request<ApiResponse<string>>({
      url: `${API_PATH}/document/${id}/content`,
      method: "get",
    });
  },

  updateDocumentContent(id: number, content: string) {
    return request<ApiResponse<KnowledgeDocument>>({
      url: `${API_PATH}/document/${id}/content`,
      method: "put",
      data: { content },
    });
  },

  replaceDocument(id: number, body: FormData) {
    return request<ApiResponse<KnowledgeDocument>>({
      url: `${API_PATH}/document/${id}/replace`,
      method: "post",
      headers: { "Content-Type": "multipart/form-data" },
      data: body,
    });
  },

  uploadDocument(body: FormData) {
    return request<ApiResponse<KnowledgeDocument>>({
      url: `${API_PATH}/document/upload`,
      method: "post",
      headers: { "Content-Type": "multipart/form-data" },
      data: body,
    });
  },

  reindexDocument(id: number) {
    return request<ApiResponse>({
      url: `${API_PATH}/document/${id}/reindex`,
      method: "post",
    });
  },

  deleteDocument(body: number[]) {
    return request<ApiResponse>({
      url: `${API_PATH}/document/delete`,
      method: "delete",
      data: body,
    });
  },

  testRetrieval(body: RetrievalTestForm) {
    return request<ApiResponse<RetrievalTestResult>>({
      url: `${API_PATH}/retrieval/test`,
      method: "post",
      data: body,
    });
  },
};

export default KnowledgeAPI;

export interface KnowledgeBaseListQuery extends PageQuery {
  name?: string;
  is_enabled?: boolean;
}

export interface KnowledgeBaseForm {
  name: string;
  description?: string | null;
  is_enabled: boolean;
}

export interface KnowledgeBase extends BaseType, KnowledgeBaseForm {
  document_count: number;
  indexed_document_count: number;
  indexing_document_count: number;
  failed_document_count: number;
}

export interface KnowledgeStats {
  knowledge_bases: number;
  documents: number;
  indexed_documents: number;
  processing_documents: number;
  failed_documents: number;
}

export interface KnowledgeDocumentListQuery extends PageQuery {
  knowledge_base_id?: number;
  file_name?: string;
  parse_status?: string;
  index_status?: string;
}

export interface KnowledgeDocument extends BaseType {
  knowledge_base_id: number;
  file_name: string;
  file_path?: string | null;
  file_type: string;
  file_size: number;
  parse_status: string;
  index_status: string;
  error_message?: string | null;
  chunk_count: number;
  replacement_pending?: boolean;
}

export interface RetrievalTestForm {
  query: string;
  knowledge_base_ids: number[];
  top_k: number;
}

export interface RetrievalHit {
  content: string;
  metadata: Record<string, unknown>;
  distance?: number;
  score?: number;
}

export interface RetrievalTestResult {
  query: string;
  retrieval_mode: "vector" | "bm25" | "hybrid";
  results: RetrievalHit[];
}

export interface KnowledgeChunk extends BaseType {
  chunk_index: number;
  content: string;
  token_count: number;
}
