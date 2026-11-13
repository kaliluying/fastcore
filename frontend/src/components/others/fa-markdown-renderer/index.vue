<template>
  <div class="markdown-content" @click="handleContentClick" v-html="renderedContent"></div>
</template>

<script setup lang="ts">
import { computed } from "vue";
import MarkdownIt, { type MarkdownIt as MarkdownItInstance } from "markdown-it";
import hljs from "highlight.js/lib/core";
import javascript from "highlight.js/lib/languages/javascript";
import typescript from "highlight.js/lib/languages/typescript";
import python from "highlight.js/lib/languages/python";
import bash from "highlight.js/lib/languages/bash";
import json from "highlight.js/lib/languages/json";
import sql from "highlight.js/lib/languages/sql";
import xml from "highlight.js/lib/languages/xml";
import css from "highlight.js/lib/languages/css";
import yaml from "highlight.js/lib/languages/yaml";
import markdown from "highlight.js/lib/languages/markdown";
import diff from "highlight.js/lib/languages/diff";
import java from "highlight.js/lib/languages/java";
import go from "highlight.js/lib/languages/go";
import rust from "highlight.js/lib/languages/rust";
import c from "highlight.js/lib/languages/c";
import cpp from "highlight.js/lib/languages/cpp";
import DOMPurify from "dompurify";
import "highlight.js/styles/atom-one-light.css";

const languages: Record<string, Parameters<typeof hljs.registerLanguage>[1]> = {
  javascript,
  js: javascript,
  typescript,
  ts: typescript,
  python,
  py: python,
  bash,
  sh: bash,
  json,
  sql,
  xml,
  html: xml,
  css,
  yaml,
  yml: yaml,
  markdown,
  md: markdown,
  diff,
  java,
  go,
  rust,
  rs: rust,
  c,
  cpp,
};

Object.entries(languages).forEach(([name, lang]) => hljs.registerLanguage(name, lang));

defineOptions({ name: "FaMarkdownRenderer" });

interface Props {
  /** Markdown 源文本 */
  content: string;
  /** 超过此长度则截断并追加 "..."（按字符数，不按渲染后长度） */
  maxLength?: number;
  /** 是否对渲染后的 HTML 做 XSS 消毒，默认 true（AI 场景默认开；可信后端 HTML 渲染时可关闭） */
  sanitize?: boolean;
}

const props = withDefaults(defineProps<Props>(), {
  maxLength: undefined,
  sanitize: true,
});

const md: MarkdownItInstance = new MarkdownIt({
  html: true,
  linkify: true,
  typographer: true,
  breaks: true,
  highlight(str: string, lang: string): string {
    const cleanLang = (lang || "").trim().toLowerCase();
    if (cleanLang && hljs.getLanguage(cleanLang)) {
      try {
        return `<pre class="hljs"><code>${hljs.highlight(str, { language: cleanLang, ignoreIllegals: true }).value}</code></pre>`;
      } catch {
        return `<pre class="hljs"><code>${md.utils.escapeHtml(str)}</code></pre>`;
      }
    }
    return `<pre class="hljs"><code>${md.utils.escapeHtml(str)}</code></pre>`;
  },
});

type RenderRule = NonNullable<MarkdownItInstance["renderer"]["rules"]["fence"]>;
const defaultFence = md.renderer.rules.fence;
const COPY_BTN_HTML =
  '<button class="code-copy-btn" type="button" title="复制代码" aria-label="复制代码"><svg class="icon-copy" width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="9" y="9" width="13" height="13" rx="2" ry="2"></rect><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"></path></svg><svg class="icon-check" width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="20 6 9 17 4 12"></polyline></svg></button>';

md.renderer.rules.fence = function (...args: Parameters<RenderRule>) {
  const [tokens, idx, options, env, self] = args;
  const token = tokens[idx];
  if (!token) return "";
  const info = token.info ? md.utils.unescapeAll(token.info).trim() : "";
  const lang = info ? info.split(/\s+/g)[0] : "";
  const rendered = defaultFence
    ? defaultFence(tokens, idx, options, env, self)
    : self.renderToken(tokens, idx, options);
  const langAttr = lang ? ` data-lang="${lang.toLowerCase()}"` : "";
  return rendered.replace(/^<pre([^>]*)>/, `<pre$1${langAttr}>${COPY_BTN_HTML}`);
};

const handleContentClick = async (event: MouseEvent) => {
  const target = event.target as HTMLElement | null;
  const copyBtn = target?.closest(".code-copy-btn") as HTMLButtonElement | null;
  if (!copyBtn) return;
  event.preventDefault();
  event.stopPropagation();
  const pre = copyBtn.closest("pre");
  const code = pre?.querySelector("code");
  if (!code) return;
  const textToCopy = code.textContent || "";
  try {
    await navigator.clipboard.writeText(textToCopy);
    copyBtn.classList.add("is-copied");
    setTimeout(() => copyBtn.classList.remove("is-copied"), 1600);
  } catch (err) {
    void err;
  }
};

const defaultRender =
  md.renderer.rules.link_open ||
  function (tokens: any[], idx: number, options: any, env: any, self: any) {
    return self.renderToken(tokens, idx, options, env, self);
  };

md.renderer.rules.link_open = function (
  tokens: any[],
  idx: number,
  options: any,
  env: any,
  self: any
) {
  tokens[idx].attrPush(["target", "_blank"]);
  tokens[idx].attrPush(["rel", "noopener noreferrer"]);
  return defaultRender(tokens, idx, options, env, self);
};

const renderedContent = computed(() => {
  if (!props.content) return "";
  const source =
    props.maxLength && props.content.length > props.maxLength
      ? props.content.substring(0, props.maxLength) + "..."
      : props.content;
  const html = md.render(source);
  return props.sanitize
    ? DOMPurify.sanitize(html, {
        ADD_ATTR: ["data-lang", "target", "rel"],
        ADD_TAGS: ["button", "svg", "path", "rect", "polyline"],
      })
    : html;
});
</script>

<style lang="scss" scoped>
.markdown-content {
  font-size: 14px;
  line-height: 1.6;
  color: var(--el-text-color-primary);
  overflow-wrap: break-word;

  :deep(pre) {
    position: relative;
    padding: 34px 16px 14px;
    margin: 12px 0;
    overflow-x: auto;
    background: var(--fa-glass-bg-inset, var(--el-fill-color-light));
    border: 1px solid var(--fa-glass-border, var(--el-border-color-lighter));
    border-radius: var(--fa-radius-control, 8px);

    &::before {
      position: absolute;
      top: 12px;
      left: 14px;
      width: 10px;
      height: 10px;
      content: "";
      background: #ff5f56;
      border-radius: 50%;
      box-shadow:
        16px 0 0 #ffbd2e,
        32px 0 0 #27c93f;
    }

    &[data-lang]::after {
      position: absolute;
      top: 10px;
      right: 42px;
      font-family:
        "JetBrains Mono", ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
      font-size: 11px;
      font-weight: 500;
      color: var(--fa-color-text-muted, var(--el-text-color-secondary));
      text-transform: uppercase;
      letter-spacing: 0.5px;
      pointer-events: none;
      user-select: none;
      content: attr(data-lang);
      opacity: 0.75;
    }

    .code-copy-btn {
      position: absolute;
      top: 7px;
      right: 10px;
      display: inline-flex;
      align-items: center;
      justify-content: center;
      width: 24px;
      height: 24px;
      padding: 0;
      color: var(--fa-color-text-muted, var(--el-text-color-secondary));
      cursor: pointer;
      background: var(--fa-glass-bg-inset, var(--el-fill-color-light));
      border: 1px solid var(--fa-glass-border, var(--el-border-color-lighter));
      border-radius: 6px;
      box-shadow: var(--fa-shadow-bento);
      opacity: 0.75;
      transition: all var(--fa-motion-control, 0.18s ease);

      .icon-check {
        display: none;
        color: var(--el-color-success);
      }

      .icon-copy {
        display: block;
      }

      &.is-copied {
        color: var(--el-color-success);
        border-color: color-mix(in srgb, var(--el-color-success) 35%, var(--fa-glass-border));

        .icon-copy {
          display: none;
        }

        .icon-check {
          display: block;
        }
      }

      &:hover {
        color: var(--theme-color, var(--el-color-primary));
        background: var(--fa-glass-bg-elevated, var(--el-fill-color));
        border-color: color-mix(
          in srgb,
          var(--theme-color, var(--el-color-primary)) 35%,
          var(--fa-glass-border)
        );
        opacity: 1;
        transform: translateY(-1px);
      }

      &:active {
        transform: translateY(0);
      }
    }

    code {
      padding: 0;
      font-family:
        "JetBrains Mono", ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
      font-size: 13px;
      line-height: 1.6;
      background: transparent;
    }
  }

  :deep(:not(pre) > code) {
    padding: 2px 6px;
    font-family: "JetBrains Mono", ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
    font-size: 12.5px;
    color: var(--theme-color, var(--el-color-primary));
    background: color-mix(
      in srgb,
      var(--theme-color, var(--el-color-primary)) 8%,
      var(--fa-glass-bg-inset, var(--el-fill-color-light))
    );
    border: 1px solid
      color-mix(in srgb, var(--theme-color, var(--el-color-primary)) 16%, transparent);
    border-radius: 4px;
  }

  :deep(p) {
    margin: 8px 0;
  }

  :deep(ul),
  :deep(ol) {
    padding-left: 24px;
    margin: 8px 0;
  }

  :deep(li) {
    margin: 4px 0;
  }

  :deep(a) {
    color: var(--el-color-primary);
    text-decoration: none;

    &:hover {
      text-decoration: underline;
    }
  }

  :deep(blockquote) {
    padding: 8px 16px;
    margin: 12px 0;
    background: var(--el-fill-color-light);
    border-left: 4px solid var(--el-color-primary);
  }

  :deep(table) {
    width: 100%;
    margin: 12px 0;
    border-collapse: collapse;

    th,
    td {
      padding: 8px 12px;
      border: 1px solid var(--el-border-color-light);
    }

    th {
      font-weight: 600;
      background: var(--el-fill-color-light);
    }
  }

  @media (prefers-reduced-motion: reduce) {
    :deep(pre .code-copy-btn) {
      transform: none !important;
      transition: none;
    }
  }
}
</style>
