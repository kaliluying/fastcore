import { defineConfig, loadEnv } from "vite";
import vue from "@vitejs/plugin-vue";
import autoprefixer from "autoprefixer";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "url";
import vueDevTools from "vite-plugin-vue-devtools";
import viteCompression from "vite-plugin-compression";
import Components from "unplugin-vue-components/vite";
import AutoImport from "unplugin-auto-import/vite";
import ElementPlus from "unplugin-element-plus/vite";
import { ElementPlusResolver } from "unplugin-vue-components/resolvers";
import tailwindcss from "@tailwindcss/vite";
import vitePluginStart from "./build/vitePluginStart.ts";
import pkg from "./package.json" with { type: "json" };

const { name, version, engines, dependencies, devDependencies } = pkg;

const __dirname = path.dirname(fileURLToPath(import.meta.url));

const buildTimestamp = Date.now();

const __APP_INFO__ = {
  pkg: { name, version, engines, dependencies, devDependencies },
  buildTimestamp,
};

/**
 * Return style entry points for Element Plus components used by the source tree.
 *
 * Returns:
 *     Existing `style/index` and `style/css` entries for referenced components.
 */
function elementPlusStyleIncludes(): string[] {
  const componentsDir = path.join(
    process.cwd(),
    "node_modules",
    "element-plus",
    "es",
    "components"
  );
  const sourceDir = path.join(__dirname, "src");
  try {
    const componentNames = new Set<string>();
    const sourceFiles = fs.readdirSync(sourceDir, { recursive: true });

    for (const sourceFile of sourceFiles) {
      if (!/\.(ts|vue)$/.test(sourceFile)) continue;

      const source = fs.readFileSync(path.join(sourceDir, sourceFile), "utf-8");
      for (const match of source.matchAll(/\bEl([A-Z][A-Za-z0-9]*)\b/g)) {
        const componentName = match[1]
          .replace(/([a-z0-9])([A-Z])/g, "$1-$2")
          .replace(/([A-Z])([A-Z][a-z])/g, "$1-$2")
          .toLowerCase();
        componentNames.add(componentName);
      }
    }

    return [...componentNames].sort().flatMap((componentName) => {
      const styleDir = path.join(componentsDir, componentName, "style");
      if (!fs.existsSync(styleDir)) return [];
      return [
        `element-plus/es/components/${componentName}/style/index`,
        `element-plus/es/components/${componentName}/style/css`,
      ];
    });
  } catch {
    return [];
  }
}

export default ({ mode }: { mode: string }) => {
  const root = process.cwd();
  const env = loadEnv(mode, root);
  const isProduction = mode === "production";

  return defineConfig({
    define: {
      __APP_VERSION__: JSON.stringify(env.VITE_VERSION),
      __APP_NAME__: JSON.stringify(env.VITE_APP_TITLE),
      __APP_INFO__: JSON.stringify(__APP_INFO__),
    },
    base: env.VITE_BASE_URL,
    server: {
      host: true,
      port: Number(env.VITE_PORT),
      open: true,
      proxy: {
        [env.VITE_APP_BASE_API]: {
          target: env.VITE_API_BASE_URL, // 代理目标地址：https://后端地址
          secure: false, // 请求是否https
          changeOrigin: true, // 是否跨域
          // rewrite: (path: string) => path.replace(new RegExp("^" + env.VITE_APP_BASE_API), ""),
        },
      },
    },
    resolve: {
      alias: {
        "@": fileURLToPath(new URL("./src", import.meta.url)),
        "@views": resolvePath("src/views"),
        "@views/*": resolvePath("src/views/*"),
        "@imgs": resolvePath("src/assets/images"),
        "@icons": resolvePath("src/assets/images/svg"),
        "@utils": resolvePath("src/utils"),
        "@stores": resolvePath("src/store"),
        "@plugins": resolvePath("src/plugins"),
        "@styles": resolvePath("src/styles"),
        "@api": resolvePath("src/api"),
        "@fa_imgs": resolvePath("src/assets/fa_imgs"),
        "@fa_imgs/*": resolvePath("src/assets/fa_imgs/*"),
      },
    },
    build: {
      target: "es2024",
      outDir: "dist",
      chunkSizeWarningLimit: 4000,
      minify: isProduction ? "terser" : false,
      terserOptions: isProduction
        ? {
            compress: {
              keep_infinity: true,
              drop_console: true,
              drop_debugger: true,
              pure_funcs: ["console.log", "console.info"],
            },
            format: {
              comments: true,
            },
          }
        : {},
      rollupOptions: {
        output: {
          manualChunks(id) {
            if (!id.includes("node_modules")) return;
            if (id.includes("echarts") || id.includes("zrender")) return "echarts";
            if (id.includes("element-plus")) return "element-plus";
            if (id.includes("exceljs")) return "exceljs";
            if (id.includes("highlight.js") || id.includes("highlightjs")) return "highlight";
            if (id.includes("markdown-it")) return "markdown";
            if (id.includes("@iconify-json")) return "iconify-icons";

            if (id.includes("dayjs")) return "dayjs";
            if (
              id.includes("vue/") ||
              id.includes("vue-router") ||
              id.includes("pinia") ||
              id.includes("vue-i18n") ||
              id.includes("@vueuse")
            )
              return "vue-vendor";

            const module = id
              .toString()
              .replace(/^.*[/\\]node_modules[/\\]\.pnpm[/\\][^/\\]+[/\\]node_modules[/\\]/, "")
              .split("node_modules/")
              .pop()
              ?.split("/")[0];
            if (
              !module ||
              [
                "birpc",
                "hookable",
                "tslib",
                "copy-anything",
                "lodash-unified",
                "perfect-debounce",
              ].includes(module)
            )
              return;
            return module;
          },
          entryFileNames: "js/[name].[hash].js",
          chunkFileNames: "js/[name].[hash].js",
          assetFileNames: (assetInfo: any) => {
            const info = assetInfo.name.split(".");
            let extType = info[info.length - 1];
            if (/\.(mp4|webm|ogg|mp3|wav|flac|aac)(\?.*)?$/i.test(assetInfo.name)) {
              extType = "media";
            } else if (/\.(png|jpe?g|gif|svg)(\?.*)?$/.test(assetInfo.name)) {
              extType = "img";
            } else if (/\.(woff2?|eot|ttf|otf)(\?.*)?$/i.test(assetInfo.name)) {
              extType = "fonts";
            }
            return `${extType}/[name].[hash].[ext]`;
          },
        },
      },
      dynamicImportVarsOptions: {
        warnOnError: true,
        exclude: [],
        include: ["src/views/**/*.vue"],
      },
    },
    plugins: [
      vue(),
      {
        name: "fastcore-version-manifest",
        generateBundle() {
          this.emitFile({
            type: "asset",
            fileName: "version.json",
            source: JSON.stringify(
              {
                version: env.VITE_VERSION || version,
                buildTimestamp,
              },
              null,
              2
            ),
          });
        },
      },
      vitePluginStart(),
      tailwindcss(),
      AutoImport({
        imports: ["vue", "vue-router", "pinia", "@vueuse/core", "vue-i18n"],
        dts: "src/types/import/auto-imports.d.ts",
        resolvers: [ElementPlusResolver()],
        eslintrc: {
          enabled: true,
          filepath: "./.auto-import.json",
          globalsPropValue: true,
        },
        vueTemplate: true,
      }),
      Components({
        dirs: ["src/components"],
        dts: "src/types/import/components.d.ts",
        resolvers: [ElementPlusResolver()],
      }),
      ElementPlus({
        useSource: true,
      }),
      viteCompression({
        verbose: false, // 是否在控制台输出压缩结果
        disable: false, // 是否禁用
        algorithm: "gzip", // 压缩算法
        ext: ".gz", // 压缩后的文件名后缀
        threshold: 10240, // 只有大小大于该值的资源会被处理 10240B = 10KB
        deleteOriginFile: false, // 压缩后是否删除原文件
      }),
      /** 仅开发启用：避免生产包体积膨胀与运行期 DevTools 开销 */
      ...(isProduction ? [] : [vueDevTools()]),
    ],
    optimizeDeps: {
      include: [
        "vue",
        "vue-router",
        "vue-i18n",
        "vue-json-pretty",
        "vue-draggable-plus",
        "element-plus",
        "@element-plus/icons-vue",
        "element-plus/es",
        "element-plus/es/locale/lang/en",
        "element-plus/es/locale/lang/zh-cn",
        "pinia",
        "axios",
        "@vueuse/core",
        "exceljs",
        "echarts/core",
        "echarts/renderers",
        "echarts/charts",
        "echarts/components",
        "nprogress",
        "qs",
        "@iconify/vue",
        "highlight.js/lib/core",
        "dompurify",
        "markdown-it",
        "mitt",
        "pinia-plugin-persistedstate",
        ...elementPlusStyleIncludes(),
      ],
    },
    css: {
      preprocessorOptions: {
        scss: {
          additionalData: `
            // Element Plus 主题色（@use with 直接改 common/var 模块默认值）。
            @use "@styles/element-plus/theme.scss" as *;
            // 业务工具 mixin 注入（供 src 内 SCSS 文件直接使用，无需手动引入）。
            @use "@styles/core/mixin.scss" as *;
          `,
        },
      },
      postcss: {
        plugins: [
          autoprefixer(),
          {
            postcssPlugin: "internal:charset-removal",
            AtRule: {
              charset: (atRule: any) => {
                if (atRule.name === "charset") {
                  atRule.remove();
                }
              },
            },
          },
        ],
      },
    },
  });
};

function resolvePath(paths: string) {
  return path.resolve(__dirname, paths);
}
