<template>
  <div ref="chatWorkspaceRef" class="chat-workspace" :style="{ height: workspaceHeight }">
    <ElSplitter class="main-chat" :lazy="true">
      <ElSplitterPanel
        v-if="!isMobileViewport"
        v-model:size="sidebarPanelSize"
        :min="64"
        :max="400"
        aria-label="会话列表"
        class="chat-split-panel sidebar-panel"
        :class="{ collapsed: isSidebarCollapsed }"
      >
        <FaSidebar
          ref="sidebarRef"
          :current-session-id="currentSessionId"
          :is-collapsed="isSidebarCollapsed"
          @select-session="handleSelectSession"
          @new-session="handleNewSession"
          @delete-session="handleDeleteSession"
        />
      </ElSplitterPanel>
      <ElSplitterPanel :min="isMobileViewport ? 0 : 360" class="chat-split-panel center-panel">
        <ElContainer aria-label="对话内容" class="chat-container">
          <ElHeader class="chat-header">
            <FaChatNavbar
              :message-count="messages.length"
              :is-sidebar-collapsed="isSidebarCollapsed"
              :knowledge-bases="knowledgeBases"
              v-model:knowledge-base-ids="selectedKnowledgeBaseIds"
              @clear-chat="handleClearChat"
              @toggle-sidebar="toggleSidebar"
              @show-evidence="isEvidenceDrawerOpen = true"
              @show-usage="usageOpen = true"
            >
              <ElButton text @click="diagnosticAgentOpen = true">只读诊断 Agent</ElButton>
            </FaChatNavbar>
          </ElHeader>
          <ElMain class="chat-main" :aria-busy="switchingSession">
            <p v-if="switchingSession" class="builtin-tool-notice" role="status">正在加载对话…</p>
            <FaAsyncState
              v-else-if="restoreRequired"
              state="error"
              title="对话恢复失败"
              description="未能加载已保存的对话，请重试，或在会话列表中选择其他对话。"
            >
              <template #action>
                <ElButton @click="restoreSession">重试加载对话</ElButton>
              </template>
            </FaAsyncState>
            <FaChatMessages
              v-show="!switchingSession && !restoreRequired"
              ref="chatMessagesRef"
              :messages="messages"
              :error="error"
              @prompt-click="handleSendMessage"
              @error-close="error = ''"
              @retry="retryMessage"
              @show-citations="showCitations"
            />
          </ElMain>
          <ElFooter class="chat-footer">
            <div v-if="sending || processStage === 'error'" class="request-progress">
              <FaAiProcessStatus :stage="processStage" :elapsed-seconds="elapsedSeconds" />
            </div>
            <p
              v-if="
                logAvailability === 'enabled' &&
                (currentLogContextId || (sending && toolQueryIndex))
              "
              class="builtin-tool-notice"
            >
              <FaSvgIcon icon="ri:terminal-box-line" />
              <span v-if="currentLogContextId && !sending">
                日志追问中 · 沿用条件重新查询（10 分钟内）
              </span>
              <span v-else-if="sending && toolQueryIndex">
                正在检索运行日志（第 {{ toolQueryIndex }} 次）
              </span>
              <ElButton
                v-if="currentLogContextId"
                link
                :disabled="sending"
                @click="currentLogContextId = null"
              >
                结束日志追问
              </ElButton>
            </p>
            <p v-else-if="logAvailability === 'error'" class="builtin-tool-notice">
              日志服务连接异常
              <ElButton link @click="loadRuntimeLogStatus">重试</ElButton>
            </p>
            <FaChatInput
              ref="chatInputRef"
              :disabled="switchingSession || restoreRequired"
              :sending="sending"
              @send="handleSendMessage"
              @stop="stopGeneration"
            />
          </ElFooter>
        </ElContainer>
      </ElSplitterPanel>
      <ElSplitterPanel
        v-if="!isCompactViewport && isEvidenceDrawerOpen"
        v-model:size="evidencePanelSize"
        :min="200"
        :max="420"
        aria-label="回答依据"
        class="chat-split-panel evidence-panel"
      >
        <div class="evidence-heading">
          <strong>回答依据</strong>
          <ElButton text aria-label="关闭回答依据" @click="isEvidenceDrawerOpen = false"
            >关闭</ElButton
          >
        </div>
        <FaRuntimeLogReport :results="activeLogResults" />
        <p v-if="!activeCitations.length && !activeLogResults.length" class="evidence-empty">
          当前回答暂无依据。
        </p>
        <FaCitationList :citations="activeCitations" @select="selectedCitation = $event" />
      </ElSplitterPanel>
    </ElSplitter>
    <FaDiagnosticAgent v-model="diagnosticAgentOpen" />
    <ElDrawer v-model="usageOpen" append-to-body title="今日 AI 用量" size="min(92vw, 720px)">
      <UsagePanel v-if="usageOpen" />
    </ElDrawer>
    <!-- 移动端会话抽屉（≤768px 时使用） -->
    <ElDrawer
      v-model="isMobileDrawerOpen"
      append-to-body
      title="会话列表"
      direction="ltr"
      size="260px"
    >
      <FaSidebar
        ref="mobileSidebarRef"
        :current-session-id="currentSessionId"
        :is-collapsed="false"
        @select-session="handleSelectSession"
        @new-session="handleNewSession"
        @delete-session="handleDeleteSession"
      />
    </ElDrawer>
    <ElDrawer
      append-to-body
      :model-value="isCompactViewport && isEvidenceDrawerOpen"
      title="回答依据"
      size="min(90vw, 380px)"
      @update:model-value="isEvidenceDrawerOpen = $event"
    >
      <FaRuntimeLogReport :results="activeLogResults" />
      <p v-if="!activeCitations.length && !activeLogResults.length" class="evidence-empty">
        当前回答暂无依据。
      </p>
      <FaCitationList :citations="activeCitations" @select="selectedCitation = $event" />
    </ElDrawer>
    <ElDialog
      append-to-body
      :model-value="!!selectedCitation"
      title="引用片段"
      width="min(90vw, 640px)"
      @close="selectedCitation = null"
    >
      <h3>{{ selectedCitation?.title }}</h3>
      <p class="citation-preview">{{ selectedCitation?.snippet || "暂无片段" }}</p>
    </ElDialog>
  </div>
</template>

<script setup lang="ts">
defineOptions({
  name: "Chat",
  inheritAttrs: false,
});

import { ref, computed, watch, onMounted, onUnmounted, onActivated, onDeactivated } from "vue";
import {
  useElementBounding,
  useIntervalFn,
  useMediaQuery,
  useSessionStorage,
  useWindowSize,
} from "@vueuse/core";
import { ElMessage, ElMessageBox } from "element-plus";
import { Auth } from "@/utils/auth";
import AiChatAPI, { ChatSession } from "@/api/module_ai/chat";
import KnowledgeAPI, { type KnowledgeBase } from "@/api/module_ai/knowledge";
import QualityAPI from "@/api/module_ai/quality";
import UsagePanel from "@/views/module_ai/model-config/UsagePanel.vue";
import type { ChatMessage, ChatCitation, ChatProcessStage, UploadedFile } from "./types";
import FaRuntimeLogReport from "./components/FaRuntimeLogReport.vue";
import FaDiagnosticAgent from "./components/FaDiagnosticAgent.vue";
import FaSidebar from "./components/FaSidebar.vue";
import FaChatNavbar from "./components/FaChatNavbar.vue";
import FaChatMessages from "./components/FaChatMessages.vue";
import FaChatInput from "./components/FaChatInput.vue";
import FaAiProcessStatus from "@/views/module_ai/components/FaAiProcessStatus.vue";
import FaCitationList from "@/views/module_ai/components/FaCitationList.vue";
import FaAsyncState from "@/components/feedback/fa-async-state/index.vue";

// 状态
const messages = ref<ChatMessage[]>([]);
const usageOpen = ref(false);
const diagnosticAgentOpen = ref(false);
const sending = ref(false);
const error = ref("");
const currentSessionId = ref<string | null>(null);
const loginSession = Auth.getSessionKey();
const isCurrentLogin = () => loginSession === Auth.getSessionKey();
// 只保留当前标签页的会话定位；正文始终经后端鉴权后读取。
const savedSession = useSessionStorage(
  "ai-chat-active-session",
  { login: "", sessionId: "" },
  { writeDefaults: false }
);
const restoreRequired = ref(false);
const setCurrentSession = (sessionId: string | null) => {
  currentSessionId.value = sessionId;
  savedSession.value = { login: loginSession, sessionId: sessionId || "" };
};
const currentLogContextId = ref<string | null>(null);
const switchingSession = ref(false);
const isSidebarCollapsed = ref(false);
const sidebarPanelSize = ref<number | string>(220);
const evidencePanelSize = ref<number | string>(260);
const knowledgeBases = ref<KnowledgeBase[]>([]);
const selectedKnowledgeBaseIds = ref<number[]>([]);
const logAvailability = ref<"hidden" | "enabled" | "error">("hidden");
const toolQueryIndex = ref(0);
let runtimeLogStatusRequest = 0;

// 响应式引用
const chatMessagesRef = ref<{ scrollToBottom: () => void }>();
const sidebarRef = ref<{ loadSessions: () => void }>();
const mobileSidebarRef = ref<{ loadSessions: () => void }>();
const chatInputRef = ref<{ clearDraft: (sentMessage: string) => void }>();

// 回答依据面板
const evidenceMessageId = ref<string | null>(null);
const activeCitations = computed(
  () => messages.value.find((message) => message.id === evidenceMessageId.value)?.citations || []
);
const selectedCitation = ref<ChatCitation | null>(null);
const activeLogResults = computed(
  () => messages.value.find((message) => message.id === evidenceMessageId.value)?.logResults || []
);
const isEvidenceDrawerOpen = ref(false);
const processStage = ref<ChatProcessStage>("idle");
const elapsedSeconds = ref(0);
let requestStartedAt = 0;
const { pause: pauseElapsed, resume: resumeElapsed } = useIntervalFn(
  () => {
    elapsedSeconds.value = Math.floor((Date.now() - requestStartedAt) / 1000);
  },
  1000,
  { immediate: false }
);
watch(
  sending,
  (active) => {
    if (active) {
      requestStartedAt = Date.now();
      elapsedSeconds.value = 0;
      resumeElapsed();
    } else {
      pauseElapsed();
    }
  },
  { flush: "sync" }
);
const isCompactViewport = useMediaQuery("(max-width: 1024px)");
const chatWorkspaceRef = ref<HTMLElement>();
const { top: chatTop } = useElementBounding(chatWorkspaceRef);
const { height: viewportHeight } = useWindowSize();
const workspaceHeight = computed(
  () => `${Math.max(280, viewportHeight.value - Math.max(0, chatTop.value) - 12)}px`
);
let activeRequest: { id: string; messageId: string } | null = null;
let sendGeneration = 0;
let selectionGeneration = 0;
let pendingSelectionId: string | null = null;

// 移动端抽屉
const isMobileDrawerOpen = ref(false);
const isMobileViewport = useMediaQuery("(max-width: 768px)");

let streamController: AbortController | null = null;

// ============ 消息处理 ============
const handleStreamMessage = (data: string) => {
  let event;
  try {
    event = JSON.parse(data);
  } catch {
    return;
  }
  if (!activeRequest || event.request_id !== activeRequest.id || !isCurrentLogin()) return;
  const message = messages.value.find((item) => item.id === activeRequest?.messageId);
  if (!message) return;
  if (
    event.type === "stage" &&
    ["routing", "querying_logs", "analyzing", "retrieving", "reranking", "generating"].includes(
      event.stage
    )
  )
    processStage.value = event.stage;
  if (event.type === "citations")
    message.citations = Array.isArray(event.citations) ? event.citations : [];
  if (event.type === "log_result") {
    message.logResults = [
      ...(message.logResults || []).filter((result) => result.query_index !== event.query_index),
      event,
    ];
  }
  if (event.type === "tool_call" && event.name === "query_runtime_logs")
    toolQueryIndex.value = event.query_index;
  if (event.type === "chunk" && typeof event.content === "string") message.content += event.content;
  if (event.type === "warning") message.warning = event.message;
  if (event.type === "error") {
    message.errorCode = event.error_code;
    if (Number.isInteger(event.retry_after) && event.retry_after > 0)
      message.retryUntil = Date.now() + Math.min(event.retry_after, 60) * 1000;
    if (
      event.error_code === "log_context_expired" &&
      message.request?.logContextId === currentLogContextId.value
    )
      currentLogContextId.value = null;
    failCurrentRequest(event.message || "生成失败，请重试");
  }
  if (event.type === "done" || event.type === "cancelled") {
    if (event.type === "done") {
      currentLogContextId.value = event.log_context_id || null;
      if (event.session_id) setCurrentSession(event.session_id);
      message.runId = event.run_id || undefined;
      message.sessionId = event.session_id || undefined;
      let original = messages.value.find((item) => item.id === message.retryOf);
      while (original) {
        original.retriedMessageId = message.id;
        original = messages.value.find((item) => item.id === original?.retryOf);
      }
    }
    message.loading = false;
    message.stopped = event.type === "cancelled";
    sending.value = false;
    activeRequest = null;
    processStage.value = event.type === "done" ? "complete" : "idle";
    if (event.type === "done") {
      sidebarRef.value?.loadSessions();
      mobileSidebarRef.value?.loadSessions();
    }
  }
  chatMessagesRef.value?.scrollToBottom();
};

const failCurrentRequest = (reason: string) => {
  sendGeneration += 1;
  const message = messages.value.find((item) => item.id === activeRequest?.messageId);
  if (message) {
    message.loading = false;
    message.error = reason;
  }
  activeRequest = null;
  sending.value = false;
  processStage.value = "error";
  error.value = reason;
};

const stopGeneration = () => {
  sendGeneration += 1;
  streamController?.abort();
  streamController = null;
  if (activeRequest) {
    const message = messages.value.find((item) => item.id === activeRequest?.messageId);
    if (message) {
      message.loading = false;
      message.stopped = true;
    }
  }
  activeRequest = null;
  sending.value = false;
  processStage.value = "idle";
};

const showCitations = (messageId: string) => {
  evidenceMessageId.value = messageId;
  isEvidenceDrawerOpen.value = true;
};

const retryMessage = (messageId: string) => {
  const message = messages.value.find((item) => item.id === messageId);
  if (
    message?.retriedMessageId ||
    message?.errorCode === "log_context_expired" ||
    (message?.retryUntil || 0) > Date.now()
  )
    return;
  const request = message?.request;
  if (request)
    void handleSendMessage(
      request.message,
      request.files,
      request.knowledgeBaseIds,
      false,
      request.logContextId || null,
      messageId
    );
};

const addMessage = (type: "user" | "assistant", content: string, files?: UploadedFile[]) => {
  messages.value.push({
    id: generateId(),
    type,
    content,
    timestamp: Date.now(),
    thinkingCollapsed: type === "assistant",
    files,
  });
};

const generateId = () => {
  return Date.now().toString(36) + Math.random().toString(36).slice(2);
};

// ============ 发送消息 ============
const handleSendMessage = async (
  message: string,
  files?: UploadedFile[],
  baseIds = selectedKnowledgeBaseIds.value,
  clearInput = true,
  logContextId = currentLogContextId.value,
  retryOf?: string
) => {
  if (
    (!message.trim() && !files?.length) ||
    !isCurrentLogin() ||
    switchingSession.value ||
    restoreRequired.value ||
    sending.value ||
    files?.some((file) => file.status !== "ready")
  )
    return;
  if (logContextId && (files?.length || baseIds.length)) {
    ElMessage.warning("日志追问不能混用附件或知识库，请先结束日志追问。草稿和已选资料会保留。");
    return;
  }
  sending.value = true;
  toolQueryIndex.value = 0;
  error.value = "";
  const generation = ++sendGeneration;
  const knowledgeBaseIds = [...baseIds];
  const question = message.trim() || "请根据附件内容回答。";
  try {
    if (generation !== sendGeneration) return;
    const requestId = generateId();
    const assistantId = generateId();
    addMessage("user", question, files);
    messages.value.push({
      id: assistantId,
      type: "assistant",
      content: "",
      timestamp: Date.now(),
      loading: true,
      thinkingCollapsed: true,
      citations: [],
      request: { message: question, files, knowledgeBaseIds, logContextId },
      retryOf,
    });
    activeRequest = { id: requestId, messageId: assistantId };
    evidenceMessageId.value = assistantId;
    processStage.value = "preparing";
    const controller = new AbortController();
    streamController = controller;
    const body = {
      request_id: requestId,
      message: question,
      session_id: currentSessionId.value,
      log_context_id: logContextId,
      knowledge_base_ids: knowledgeBaseIds,
      files: files?.map((file) => ({
        name: file.name,
        type: file.type,
        size: file.size,
        content: file.content,
      })),
    };
    await AiChatAPI.stream(body, controller.signal, handleStreamMessage, () => {
      if (clearInput && generation === sendGeneration) chatInputRef.value?.clearDraft(message);
    });
    if (activeRequest?.id === requestId) failCurrentRequest("回答连接中断，回答未确认保存，请重试");
    chatMessagesRef.value?.scrollToBottom();
  } catch (cause) {
    if (generation === sendGeneration && activeRequest) {
      failCurrentRequest(cause instanceof Error ? cause.message : "发送失败，请重试");
    }
  } finally {
    if (generation === sendGeneration) streamController = null;
  }
};

const isSuccessResponse = (responseData?: ApiResponse<unknown>) =>
  responseData?.success === true || responseData?.code === 0 || responseData?.code === 200;

// ============ 会话操作 ============
const handleSelectSession = async (
  session: Pick<ChatSession, "id" | "session_id" | "title">,
  restoring = false
) => {
  if (!isCurrentLogin()) return;
  const sessionId = session.id || session.session_id;
  if (!sessionId) {
    ElMessage.error("会话 ID 缺失，无法切换");
    return;
  }
  stopGeneration();
  const generation = ++selectionGeneration;
  pendingSelectionId = sessionId;
  switchingSession.value = true;
  if (restoring) restoreRequired.value = true;
  try {
    const response = await AiChatAPI.getSessionDetail(sessionId);
    if (generation !== selectionGeneration || !isCurrentLogin()) return;
    const responseData = response.data;
    if (!isSuccessResponse(responseData) || !responseData.data) {
      if (!restoring)
        ElMessage.error(
          isSuccessResponse(responseData)
            ? "会话不存在或暂时无法加载，请重试"
            : responseData?.msg || "获取会话详情失败"
        );
      return;
    }

    const sessionData = responseData.data;
    const runs = sessionData.runs || [];
    const feedback = await QualityAPI.feedbackList({ session_id: sessionId, page_size: 100 }).catch(
      () => null
    );
    if (generation !== selectionGeneration || !isCurrentLogin()) return;
    setCurrentSession(sessionId);
    restoreRequired.value = false;
    currentLogContextId.value = null;
    error.value = "";
    messages.value = [];
    const feedbackByRun = new Map(
      feedback?.data?.data?.items?.map((item) => [item.run_id, item]) ?? []
    );

    runs.forEach((run: any) => {
      const runMessages = run.messages || [];
      runMessages.forEach((msg: any) => {
        if (msg.role === "user" || msg.role === "assistant") {
          addMessage(msg.role, msg.content);
          const restored = messages.value[messages.value.length - 1];
          if (restored) {
            restored.citations = msg.citations || [];
            restored.runId = run.run_id;
            restored.sessionId = sessionId;
            restored.feedbackRating = feedbackByRun.get(run.run_id)?.rating;
            restored.feedbackComment = feedbackByRun.get(run.run_id)?.comment;
            if (msg.role === "assistant") evidenceMessageId.value = restored.id;
          }
        }
      });
    });

    isMobileDrawerOpen.value = false;
    chatMessagesRef.value?.scrollToBottom();
    if (!restoring) ElMessage.success(`已切换到会话：${session.title || "未命名会话"}`);
  } catch {
    if (generation === selectionGeneration && isCurrentLogin()) {
      if (!restoring) ElMessage.error("获取会话详情失败");
    }
  } finally {
    if (generation === selectionGeneration) {
      pendingSelectionId = null;
      switchingSession.value = false;
    }
  }
};

const restoreSession = () => {
  const login = savedSession.value?.login;
  const sessionId = savedSession.value?.sessionId;
  if (
    loginSession &&
    isCurrentLogin() &&
    login === loginSession &&
    typeof sessionId === "string" &&
    sessionId
  ) {
    void handleSelectSession({ id: sessionId, session_id: sessionId, title: null }, true);
  }
};

const handleNewSession = () => {
  stopGeneration();
  selectionGeneration += 1;
  pendingSelectionId = null;
  switchingSession.value = false;
  restoreRequired.value = false;
  isMobileDrawerOpen.value = false;
  error.value = "";
  selectedCitation.value = null;
  setCurrentSession(null);
  currentLogContextId.value = null;
  messages.value = [];
  isEvidenceDrawerOpen.value = false;
  ElMessage.success("已开启新对话");
};

const handleDeleteSession = (sessionId: string) => {
  if (
    currentSessionId.value !== sessionId &&
    pendingSelectionId !== sessionId &&
    !(restoreRequired.value && savedSession.value?.sessionId === sessionId)
  )
    return;

  handleNewSession();
};

const handleClearChat = async () => {
  try {
    await ElMessageBox.confirm("清空当前显示？已保存的会话历史不会删除。", "确认清空", {
      confirmButtonText: "确定",
      cancelButtonText: "取消",
      type: "warning",
    });
    stopGeneration();
    selectionGeneration += 1;
    pendingSelectionId = null;
    switchingSession.value = false;
    messages.value = [];
    currentLogContextId.value = null;
    ElMessage.success("对话已清空");
  } catch {
    ElMessage.info("已取消清空对话");
  }
};

const toggleSidebar = () => {
  if (isMobileViewport.value) {
    isMobileDrawerOpen.value = true;
    return;
  }

  isSidebarCollapsed.value = !isSidebarCollapsed.value;
  sidebarPanelSize.value = isSidebarCollapsed.value ? 64 : 220;
};

// ============ 生命周期 ============
const loadKnowledgeBases = async () => {
  try {
    const res = await KnowledgeAPI.optionselect();
    knowledgeBases.value = (res.data?.data || []).filter((item) => item.id != null);
  } catch {
    knowledgeBases.value = [];
  }
};

const loadRuntimeLogStatus = async () => {
  const requestId = ++runtimeLogStatusRequest;
  try {
    const response = await AiChatAPI.getRuntimeLogStatus();
    if (requestId !== runtimeLogStatusRequest) return;
    const status = response.data.data;
    logAvailability.value = status?.can_query ? "enabled" : "hidden";
  } catch {
    if (requestId !== runtimeLogStatusRequest) return;
    logAvailability.value = "error";
  }
};

onMounted(() => {
  loadKnowledgeBases();
  loadRuntimeLogStatus();
  restoreSession();
});
onUnmounted(() => {
  selectionGeneration += 1;
  pendingSelectionId = null;
  switchingSession.value = false;
  stopGeneration();
});
// 返回缓存视图时，重新加载离开前尚未完成的会话恢复。
onActivated(() => {
  loadRuntimeLogStatus();
  if (!currentSessionId.value && !switchingSession.value) restoreSession();
});
onDeactivated(() => {
  selectionGeneration += 1;
  pendingSelectionId = null;
  switchingSession.value = false;
  currentLogContextId.value = null;
  stopGeneration();
});
</script>

<style lang="scss" scoped>
.chat-workspace {
  box-sizing: border-box;
  flex: 0 0 auto;
  width: 100%;
  min-width: 0;
  min-height: 0;
  overflow: hidden;
}

.citation-preview {
  max-height: 65vh;
  overflow-y: auto;
  line-height: 1.7;
  overflow-wrap: anywhere;
  white-space: pre-wrap;
}

.evidence-empty {
  font-size: 12px;
  line-height: 1.7;
  color: var(--fa-color-text-muted);
  overflow-wrap: anywhere;
}

.evidence-heading {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding-bottom: 10px;
  margin-bottom: 12px;
  border-bottom: 1px solid var(--fa-glass-border);

  strong {
    font-size: 14px;
    font-weight: 600;
    color: var(--el-text-color-primary);
    letter-spacing: -0.01em;
  }
}

.request-progress {
  padding: 10px 20px 0;
}

.builtin-tool-notice {
  display: flex;
  gap: 8px;
  align-items: center;
  max-width: fit-content;
  padding: 6px 12px;
  margin: 10px 20px 0;
  font-size: 12px;
  line-height: 1.5;
  color: var(--fa-color-text-secondary, #606266);
  background: var(--fa-glass-bg-inset);
  border: 1px solid var(--fa-glass-border);
  border-radius: 10px;
  -webkit-backdrop-filter: var(--fa-glass-blur);
  backdrop-filter: var(--fa-glass-blur);

  span {
    flex: 1;
    min-width: 0;
  }

  :deep(.el-button) {
    flex-shrink: 0;
    padding: 0 4px;
    font-size: 12px;
    font-weight: 500;
  }
}

.main-chat {
  height: 100%;
  overflow: hidden;
  background: var(--fa-glass-bg);
  border: 1px solid var(--fa-glass-border);
  border-radius: var(--fa-radius-bento);
  box-shadow: var(--fa-shadow-bento);
  -webkit-backdrop-filter: var(--fa-glass-blur);
  backdrop-filter: var(--fa-glass-blur);

  :deep(.chat-split-panel) {
    min-width: 0;
    min-height: 0;
    overflow: hidden;
    background: transparent;
  }

  :deep(.sidebar-panel) {
    transition: flex-basis 0.25s ease;

    .sidebar {
      min-width: 0;
    }

    &.collapsed {
      .sidebar {
        overflow: hidden;
      }
    }
  }

  :deep(.center-panel) {
    display: flex;
    flex-direction: column;
    min-width: 0;
    background: transparent;
  }

  .chat-container {
    display: flex;
    flex-direction: column;
    width: 100%;
    min-width: 0;
    height: 100%;
    min-height: 0;
    overflow: hidden;
  }

  .chat-header {
    flex-shrink: 0;
    height: auto;
    padding: 0;
    background: transparent;
    border-bottom: 0;
  }

  .chat-main {
    flex: 1;
    min-height: 0;
    padding: 0;
    overflow: hidden;
  }

  .chat-footer {
    flex-shrink: 0;
    height: auto;
    min-height: 80px;
    padding: 0;
    background: transparent;
    border-top: 1px solid var(--fa-glass-border);
  }

  :deep(.evidence-panel) {
    padding: 14px;
    overflow-y: auto;
    background: var(--fa-glass-bg-inset);
    border-left: 1px solid var(--fa-glass-border);
  }

  :deep(.el-splitter-bar__dragger) {
    border-radius: 3px;
    transition: background-color 0.2s ease;
  }

  :deep(.el-splitter-bar__dragger:hover:not(.is-disabled)),
  :deep(.el-splitter-bar__dragger-active) {
    background: color-mix(in srgb, var(--theme-color) 12%, transparent);
  }

  @media (prefers-reduced-motion: reduce) {
    :deep(.sidebar-panel),
    :deep(.el-splitter-bar__dragger) {
      transition: none;
    }
  }
}
</style>
