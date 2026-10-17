<script setup>
import { nextTick, ref, watch } from 'vue'
import { useChat } from '../composables/useChat'

const { sendMessage, streaming, draft } = useChat()

const ta = ref(null)

// 自动增高：单行起步，最多 200px 后内部滚动
function autoGrow() {
  const el = ta.value
  if (!el) return
  el.style.height = 'auto'
  el.style.height = Math.min(el.scrollHeight, 200) + 'px'
}

// 空状态示例填入后，重新算高并聚焦输入框
watch(draft, async () => {
  await nextTick()
  autoGrow()
  if (document.activeElement !== ta.value) ta.value?.focus()
})

async function submit() {
  const q = draft.value.trim()
  if (!q || streaming.value) return
  draft.value = ''
  await nextTick()
  autoGrow()
  await sendMessage(q)
  ta.value?.focus()
}

// Enter 发送 / Shift+Enter 换行；输入法组合中不拦截
function onKeydown(e) {
  if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) {
    e.preventDefault()
    submit()
  }
}
</script>

<template>
  <div class="composer">
    <div class="composer-box">
      <textarea
        ref="ta"
        v-model="draft"
        rows="1"
        placeholder="向财报解读助手提问…"
        aria-label="输入问题"
        @input="autoGrow"
        @keydown="onKeydown"
      ></textarea>
      <button
        class="send"
        type="button"
        :disabled="!draft.trim() || streaming"
        :title="streaming ? '正在处理…' : '发送'"
        aria-label="发送"
        @click="submit"
      >
        <svg width="16" height="16" viewBox="0 0 16 16" fill="none" aria-hidden="true">
          <path
            d="M8 13V3.5M4 7l4-3.5L12 7"
            stroke="currentColor"
            stroke-width="1.8"
            stroke-linecap="round"
            stroke-linejoin="round"
          />
        </svg>
      </button>
    </div>
    <p class="composer-hint">
      <span v-if="streaming">正在处理…</span>
      <span v-else>Enter 发送 · Shift + Enter 换行</span>
    </p>
  </div>
</template>

<style scoped>
/* 输入区：贴在对话列底部，随消息区滚动保持可见 */
.composer {
  flex-shrink: 0;
  width: 100%;
  max-width: 720px;
  margin: 0 auto;
  padding: 10px 24px calc(16px + env(safe-area-inset-bottom));
}

/* 圆角输入框：默认细线，聚焦时只换蓝边 + 一圈极浅蓝 */
.composer-box {
  display: flex;
  align-items: flex-end;
  gap: 8px;
  padding: 10px 10px 10px 16px;
  border: 1px solid var(--line);
  border-radius: var(--radius-lg);
  background: var(--bg);
  transition: border-color 0.15s, box-shadow 0.15s;
}

.composer-box:focus-within {
  border-color: var(--accent);
  box-shadow: 0 0 0 3px var(--accent-soft);
}

textarea {
  flex: 1;
  min-height: 24px;
  max-height: 200px;
  padding: 3px 0;
  border: 0;
  outline: none;
  resize: none;
  background: transparent;
  color: var(--ink);
  font-size: 15.5px;
  line-height: 1.6;
}

textarea:focus-visible {
  outline: none; /* 焦点态由外层边框表达 */
}

textarea::placeholder {
  color: var(--muted-weak);
}

/* 发送：蓝色圆形图标按钮，禁用时退为浅灰 */
.send {
  display: flex;
  flex-shrink: 0;
  align-items: center;
  justify-content: center;
  width: 34px;
  height: 34px;
  border-radius: 50%;
  background: var(--accent);
  color: #fff;
  transition: background 0.15s, transform 0.1s;
}

.send:hover:not(:disabled) {
  background: var(--accent-hover);
}

.send:active:not(:disabled) {
  transform: scale(0.94);
}

.send:disabled {
  background: var(--line);
  color: var(--muted-weak);
}

.composer-hint {
  margin: 8px 0 0;
  color: var(--muted-weak);
  font-size: 12px;
  text-align: center;
}

@media (max-width: 819px) {
  .composer {
    padding: 8px 14px calc(12px + env(safe-area-inset-bottom));
  }
}
</style>
