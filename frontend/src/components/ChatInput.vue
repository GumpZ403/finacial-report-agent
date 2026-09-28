<script setup>
import { ref } from 'vue'
import { useChat } from '../composables/useChat'

const { sendMessage, streaming, busyText } = useChat()

const text = ref('')
const ta = ref(null)

function autoGrow() {
  const el = ta.value
  if (!el) return
  el.style.height = 'auto'
  el.style.height = Math.min(el.scrollHeight, 200) + 'px'
}

async function submit() {
  const q = text.value.trim()
  if (!q || streaming.value) return
  text.value = ''
  if (ta.value) ta.value.style.height = 'auto'
  await sendMessage(q)
  ta.value?.focus()
}

function onKeydown(e) {
  if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) {
    e.preventDefault()
    submit()
  }
}
</script>

<template>
  <div class="input-wrap">
    <div class="input-box">
      <textarea
        ref="ta"
        v-model="text"
        rows="1"
        placeholder="向财报解读助手提问…（Enter 发送，Shift+Enter 换行）"
        @input="autoGrow"
        @keydown="onKeydown"
      ></textarea>
      <button
        class="send-btn"
        :disabled="!text.trim() || streaming"
        :title="streaming ? '处理中…' : '发送'"
        @click="submit"
      >
        <svg width="16" height="16" viewBox="0 0 16 16" fill="none" aria-hidden="true">
          <path d="M2 8L14 2L9.5 14L7.6 9.4L2 8Z" stroke="currentColor" stroke-width="1.4" stroke-linejoin="round" />
          <path d="M7.6 9.4L14 2" stroke="currentColor" stroke-width="1.4" stroke-linejoin="round" />
        </svg>
      </button>
    </div>
    <div class="input-hint">
      <span v-if="streaming">正在处理"{{ busyText.slice(0, 18) }}"…</span>
      <span v-else>财报解读助手 · 数据来自已入库的 A 股财报</span>
    </div>
  </div>
</template>

<style scoped>
.input-wrap {
  width: 100%;
  max-width: 760px;
  margin: 0 auto;
  padding: 8px 20px 16px;
}

.input-box {
  display: flex;
  align-items: flex-end;
  gap: 8px;
  background: var(--bg-input);
  border: 1px solid var(--border-strong);
  border-radius: var(--radius-lg);
  padding: 10px 10px 10px 18px;
  box-shadow: var(--shadow-soft);
  transition: border-color 0.15s, box-shadow 0.15s;
}
.input-box:focus-within {
  border-color: var(--accent);
  box-shadow: 0 0 0 3px var(--accent-soft);
}

textarea {
  flex: 1;
  border: none;
  outline: none;
  resize: none;
  background: transparent;
  font: inherit;
  color: var(--text);
  max-height: 200px;
  line-height: 1.5;
  padding: 4px 0;
}
textarea::placeholder {
  color: var(--text-faint);
}

.send-btn {
  width: 36px;
  height: 36px;
  flex-shrink: 0;
  border-radius: 50%;
  background: var(--accent);
  color: #fff;
  display: flex;
  align-items: center;
  justify-content: center;
  transition: background 0.15s, transform 0.1s;
}
.send-btn:hover:not(:disabled) {
  background: var(--accent-hover);
}
.send-btn:active:not(:disabled) {
  transform: scale(0.94);
}
.send-btn:disabled {
  background: #d8d5cc;
  color: #fff;
}

.input-hint {
  text-align: center;
  font-size: 0.78em;
  color: var(--text-faint);
  margin-top: 10px;
}
</style>
