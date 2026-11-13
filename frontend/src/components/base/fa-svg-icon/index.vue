<!-- 图标组件 -->
<template>
  <Icon v-if="icon" :icon="icon" v-bind="bindAttrs" class="fa-svg-icon inline" />
</template>

<script setup lang="ts">
import { Icon } from "@iconify/vue";

defineOptions({ name: "FaSvgIcon", inheritAttrs: false });

interface Props {
  /** Iconify 图标名称 */
  icon?: string;
}

withDefaults(defineProps<Props>(), {
  icon: "",
});

const attrs = useAttrs();

const bindAttrs = computed<{ class: string; style: string }>(() => ({
  class: (attrs.class as string) || "",
  style: (attrs.style as string) || "",
}));
</script>
