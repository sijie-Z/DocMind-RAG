<template>
  <!-- 批次十八：公开知识库 iframe 嵌入代码。
       仅在发布已开启匿名聊天时由管理面板展示；token 只存在组件内存，
       不写日志/不持久化。传入 link 时生成固定片段（轮换结果），
       否则让管理员粘贴公开链接或 token（列表接口永不回显 token）。 -->
  <div class="public-kb-embed" data-testid="public-kb-embed">
    <p class="public-kb-label">{{ t('publicKb.embed.title') }}</p>
    <p class="public-kb-hint">{{ t('publicKb.embed.hint') }}</p>
    <p class="public-kb-warning" data-testid="public-kb-embed-warning">
      {{ t('publicKb.embed.warning') }}
    </p>

    <template v-if="!fixedLink">
      <label class="public-kb-label" :for="linkInputId">
        {{ t('publicKb.embed.linkLabel') }}
      </label>
      <input
        :id="linkInputId"
        v-model="linkInput"
        class="public-kb-input"
        data-testid="public-kb-embed-link"
        :placeholder="t('publicKb.embed.linkPlaceholder')"
        autocomplete="off"
        spellcheck="false"
      />
    </template>
    <p
      v-if="inputValue.trim() && !snippet"
      class="public-kb-error"
      role="alert"
      data-testid="public-kb-embed-invalid"
    >
      {{ t('publicKb.embed.invalidLink') }}
    </p>

    <template v-if="snippet">
      <label class="public-kb-label" :for="snippetId">
        {{ t('publicKb.embed.snippetLabel') }}
      </label>
      <textarea
        :id="snippetId"
        class="public-kb-input public-kb-embed-snippet"
        data-testid="public-kb-embed-snippet"
        :value="snippet"
        readonly
        rows="4"
        spellcheck="false"
      />
      <p
        v-if="crossOriginOrigin"
        class="public-kb-warning"
        data-testid="public-kb-embed-cross-origin"
      >
        {{ t('publicKb.embed.crossOrigin', { origin: crossOriginOrigin }) }}
      </p>
      <div class="public-kb-dialog-actions">
        <button
          class="public-kb-btn primary"
          type="button"
          data-testid="public-kb-embed-copy"
          @click="copySnippet"
        >
          {{ copyState === 'copied' ? t('publicKb.copied') : t('publicKb.copy') }}
        </button>
      </div>
      <p
        v-if="copyState === 'failed'"
        class="public-kb-error"
        role="alert"
        data-testid="public-kb-embed-copy-error"
      >
        {{ t('publicKb.embed.copyFailed') }}
      </p>
    </template>
  </div>
</template>

<script setup lang="ts">
import { computed, ref, useId, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import {
  buildPublicKbEmbedSnippet,
  resolvePublicKbPageOrigin,
} from '@/utils/publicKbEmbed'

const props = defineProps<{
  /** 已知公开链接（创建/轮换响应）；为空时由管理员粘贴。 */
  link?: string
  /** 发布标题，用作 iframe 的 title（可访问性）。 */
  title?: string
}>()

const { t } = useI18n()
const uid = useId()
const linkInputId = `public-kb-embed-link-${uid}`
const snippetId = `public-kb-embed-snippet-${uid}`
const linkInput = ref('')
const copyState = ref<'idle' | 'copied' | 'failed'>('idle')
const currentOrigin = typeof window === 'undefined' ? '' : window.location.origin

const fixedLink = computed(() => (props.link || '').trim())
const inputValue = computed(() => fixedLink.value || linkInput.value)
const frameTitle = computed(
  () => (props.title || '').trim() || t('publicKb.embed.frameTitle'),
)
const snippet = computed(() =>
  buildPublicKbEmbedSnippet(inputValue.value, frameTitle.value, currentOrigin),
)
const crossOriginOrigin = computed(() => {
  const origin = resolvePublicKbPageOrigin(inputValue.value, currentOrigin)
  return origin && origin !== currentOrigin ? origin : ''
})

// 输入变化后片段已变，不得继续显示「已复制」。
watch(snippet, () => {
  copyState.value = 'idle'
})

async function copySnippet() {
  if (!snippet.value) return
  try {
    await navigator.clipboard.writeText(snippet.value)
    copyState.value = 'copied'
  } catch {
    copyState.value = 'failed'
  }
}
</script>

<style scoped>
/* 子组件拿不到父组件的 scoped 样式（父 scope id 只落在子组件根节点），
   这里重复管理面板共享表单/按钮类，避免嵌入区在暗色模式下变成原生控件。 */
.public-kb-embed {
  margin-top: 16px;
  border-top: 1px dashed #cbd5e1;
  padding-top: 8px;
}
.dark .public-kb-embed {
  border-color: #475569;
}
.public-kb-label {
  display: block;
  margin: 12px 0 6px;
  font-size: 13px;
  font-weight: 600;
  color: #334155;
}
.dark .public-kb-label {
  color: #cbd5e1;
}
.public-kb-hint {
  font-size: 12px;
  color: #64748b;
}
.dark .public-kb-hint {
  color: #94a3b8;
}
.public-kb-warning {
  margin: 14px 0;
  border: 1px solid #fde68a;
  background: #fffbeb;
  color: #92400e;
  border-radius: 10px;
  padding: 10px 12px;
  font-size: 13px;
}
.dark .public-kb-warning {
  border-color: #92400e;
  background: #451a03;
  color: #fcd34d;
}
.public-kb-error {
  color: #b91c1c;
  font-size: 13px;
}
.dark .public-kb-error {
  color: #fca5a5;
}
.public-kb-input {
  width: 100%;
  border: 1px solid #cbd5e1;
  border-radius: 8px;
  padding: 7px 9px;
  font-size: 13px;
  background: #fff;
  color: #0f172a;
}
.dark .public-kb-input {
  border-color: #475569;
  background: #020617;
  color: #e2e8f0;
}
.public-kb-dialog-actions {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
  margin-top: 16px;
}
.public-kb-btn {
  border: 1px solid #cbd5e1;
  background: #fff;
  color: #334155;
  border-radius: 8px;
  padding: 5px 10px;
  font-size: 12px;
  cursor: pointer;
}
.dark .public-kb-btn {
  border-color: #475569;
  background: #0f172a;
  color: #e2e8f0;
}
.public-kb-btn.primary {
  border-color: #2563eb;
  background: #2563eb;
  color: #fff;
}
.public-kb-embed-snippet {
  min-height: 86px;
  resize: vertical;
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  font-size: 12px;
  line-height: 1.5;
  word-break: break-all;
}
</style>
