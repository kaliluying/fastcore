<template>
  <div class="chat-messages-container">
    <ElScrollbar ref="messagesContainer" class="chat-messages" @scroll="handleScroll">
      <WelcomeScreen v-if="messages.length === 0" @prompt-click="handlePromptClick" />
      <div v-else ref="messagesListRef" class="messages-list">
        <FaMessageItem
          v-for="message in messages"
          :key="message.id"
          :message="message"
          @toggle-thinking="handleToggleThinking(message)"
          @retry="emit('retry', message.id)"
          @show-retry="scrollToMessage"
          @show-citations="emit('show-citations', message.id)"
        />
      </div>
      <div v-if="error" class="error-banner">
        <ElAlert :title="error" type="error" :closable="true" show-icon @close="handleErrorClose" />
      </div>
    </ElScrollbar>

    <Transition name="fade-slide">
      <button
        v-if="showScrollBottom"
        type="button"
        class="scroll-bottom-pill"
        aria-label="回到底部"
        @click="handleScrollToBottomClick"
      >
        <svg
          class="scroll-icon"
          viewBox="0 0 24 24"
          width="14"
          height="14"
          stroke="currentColor"
          stroke-width="2.2"
          fill="none"
          stroke-linecap="round"
          stroke-linejoin="round"
        >
          <polyline points="6 9 12 15 18 9" />
        </svg>
        <span class="scroll-text">回到底部</span>
      </button>
    </Transition>
  </div>
</template>

<script setup lang="ts">
import { ref, nextTick, watch, onMounted, onUnmounted } from "vue";
import { ElScrollbar } from "element-plus";
import WelcomeScreen from "./FaWelcomeScreen.vue";
import FaMessageItem from "./FaMessageItem.vue";
import type { ChatMessage } from "../types";

interface Props {
  messages: ChatMessage[];
  error: string;
}

interface Emits {
  (e: "prompt-click", prompt: string): void;
  (e: "error-close"): void;
  (e: "retry", messageId: string): void;
  (e: "show-citations", messageId: string): void;
}

const props = defineProps<Props>();
const emit = defineEmits<Emits>();

const messagesContainer = ref<InstanceType<typeof ElScrollbar>>();
const messagesListRef = ref<HTMLElement>();
let listResizeObserver: ResizeObserver | null = null;
const isUserScrolledUp = ref(false);
const showScrollBottom = ref(false);

const handleScroll = () => {
  const wrap = messagesContainer.value?.wrapRef;
  if (!wrap) return;
  const distanceToBottom = wrap.scrollHeight - wrap.scrollTop - wrap.clientHeight;
  if (distanceToBottom > 80) {
    isUserScrolledUp.value = true;
    showScrollBottom.value = true;
  } else if (distanceToBottom <= 40) {
    isUserScrolledUp.value = false;
    showScrollBottom.value = false;
  }
};

const scrollToBottom = (force = true) => {
  if (force) {
    isUserScrolledUp.value = false;
    showScrollBottom.value = false;
  }
  nextTick(() => {
    const wrap = messagesContainer.value?.wrapRef;
    if (wrap) wrap.scrollTop = wrap.scrollHeight;
  });
};

const handleScrollToBottomClick = () => {
  isUserScrolledUp.value = false;
  showScrollBottom.value = false;
  const wrap = messagesContainer.value?.wrapRef;
  if (wrap) {
    wrap.scrollTo({ top: wrap.scrollHeight, behavior: "smooth" });
  }
};

const scrollToMessage = (messageId: string) => {
  const message = messagesContainer.value?.wrapRef?.querySelector<HTMLElement>(
    `[data-message-id="${messageId}"]`
  );
  message?.scrollIntoView({ block: "center", behavior: "smooth" });
  message?.focus({ preventScroll: true });
};

watch(
  () => {
    const last = props.messages[props.messages.length - 1];
    return [props.messages.length, last?.type, last?.content?.length] as const;
  },
  ([newLen, newType], oldVal) => {
    const oldLen = oldVal?.[0];
    if (newLen !== oldLen && newType === "user") {
      scrollToBottom(true);
      return;
    }
    if (!isUserScrolledUp.value) {
      scrollToBottom(false);
    }
  }
);

const handlePromptClick = (prompt: string) => {
  emit("prompt-click", prompt);
};

const handleToggleThinking = (message: ChatMessage) => {
  message.thinkingCollapsed = !message.thinkingCollapsed;
};

const handleErrorClose = () => {
  emit("error-close");
};

onMounted(() => {
  if (typeof ResizeObserver !== "undefined" && messagesListRef.value) {
    listResizeObserver = new ResizeObserver(() => {
      if (!isUserScrolledUp.value) {
        scrollToBottom(false);
      }
    });
    listResizeObserver.observe(messagesListRef.value);
  }
});

onUnmounted(() => {
  listResizeObserver?.disconnect();
  listResizeObserver = null;
});

defineExpose({
  scrollToBottom,
});
</script>

<style lang="scss" scoped>
.chat-messages-container {
  position: relative;
  display: flex;
  flex: 1;
  flex-direction: column;
  width: 100%;
  height: 100%;
  min-height: 0;
}

.scroll-bottom-pill {
  position: absolute;
  bottom: 16px;
  left: 50%;
  z-index: 10;
  display: inline-flex;
  gap: 6px;
  align-items: center;
  padding: 6px 14px;
  font-size: 12.5px;
  font-weight: 500;
  color: var(--fa-color-text, var(--el-text-color-primary));
  cursor: pointer;
  user-select: none;
  background: var(--fa-glass-bg-elevate, var(--el-bg-color-overlay));
  border: 1px solid var(--fa-glass-border, var(--el-border-color-lighter));
  border-radius: 9999px;
  box-shadow:
    0 4px 16px rgb(0 0 0 / 10%),
    0 1px 3px rgb(0 0 0 / 5%);
  -webkit-backdrop-filter: blur(16px);
  backdrop-filter: blur(16px);
  transform: translateX(-50%);
  transition:
    transform 0.2s cubic-bezier(0.16, 1, 0.3, 1),
    box-shadow 0.2s ease,
    background-color 0.2s ease;

  &:hover {
    box-shadow:
      0 6px 20px rgb(0 0 0 / 14%),
      0 2px 6px rgb(0 0 0 / 6%);
    transform: translateX(-50%) translateY(-2px);
  }

  &:active {
    transform: translateX(-50%) scale(0.96);
  }

  .scroll-icon {
    flex-shrink: 0;
    transition: transform 0.2s ease;
  }

  &:hover .scroll-icon {
    transform: translateY(1px);
  }
}

.fade-slide-enter-active,
.fade-slide-leave-active {
  transition:
    opacity 0.2s ease,
    transform 0.2s cubic-bezier(0.16, 1, 0.3, 1);
}

.fade-slide-enter-from,
.fade-slide-leave-to {
  opacity: 0;
  transform: translateX(-50%) translateY(10px);
}

.chat-messages {
  width: 100%;
  height: 100%;
  min-height: 0;
  background: transparent;

  .messages-list {
    max-width: 800px;
    padding: 24px;
    margin: 0 auto;
  }

  .error-banner {
    max-width: 800px;
    padding: 12px 24px;
    margin: 0 auto;
  }
}

@media (width <= 640px) {
  .chat-messages .messages-list,
  .chat-messages .error-banner {
    padding: 12px 14px;
  }

  .scroll-bottom-pill {
    bottom: 12px;
    min-height: 44px;
    padding: 8px 16px;
  }
}

@media (prefers-reduced-motion: reduce) {
  .scroll-bottom-pill,
  .fade-slide-enter-active,
  .fade-slide-leave-active {
    transition: none !important;
  }
}
</style>
