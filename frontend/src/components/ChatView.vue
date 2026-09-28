<script setup>
import { nextTick, ref, watch } from 'vue'
import { useChat } from '../composables/useChat'
import MessageItem from './MessageItem.vue'
import ChatInput from './ChatInput.vue'

const { activeSession, sendMessage, streaming } = useChat()

const scrollEl = ref(null)

// 示例问题（空状态引导）
const suggestions = [
  '贵州茅台 2025 年一季度毛利率是多少？',
  '宁德时代近三年营收趋势如何？',
  '写一份贵州茅台的财报解读报告',
  '当前数据库里有哪些公司？',
]

// 新消息出现时滚动到底部
watch(
  () => activeSession.value?.messages.length,
  async () => {
    await nextTick()
    if (scrollEl.value) scrollEl.value.scrollTop = scrollEl.value.scrollHeight
  },
)
</script>

<template>
  <main class="chat">
    <!-- 空状态：Claude 风格引导 -->
    <div v-if="!activeSession || activeSession.messages.length === 0" class="empty">
      <div class="empty-title">我能帮你解读财报</div>
      <div class="empty-sub">基于已入库的 A 股财报数据，查询指标、分析趋势、生成解读报告</div>
      <div class="suggestions">
        <button
          v-for="s in suggestions"
          :key="s"
          class="suggestion-card"
          @click="sendMessage(s)"
          :disabled="streaming"
        >
          {{ s }}
        </button>
      </div>
    </div>

    <!-- 对话流 -->
    <template v-else>
      <div ref="scrollEl" class="messages">
        <div class="messages-inner">
          <MessageItem
            v-for="(m, i) in activeSession.messages"
            :key="i"
            :message="m"
            :streaming="streaming"
          />
        </div>
      </div>
    </template>

    <ChatInput />
  </main>
</template>

<style scoped>
.chat {
  flex: 1;
  display: flex;
  flex-direction: column;
  min-width: 0;
  height: 100%;
}

/* 空状态 */
.empty {
  flex: 1;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 12px;
  padding: 0 24px;
}
.empty-title {
  font-size: 1.7em;
  font-weight: 600;
  letter-spacing: -0.02em;
  color: var(--text);
}
.empty-sub {
  color: var(--text-secondary);
  font-size: 0.95em;
}
.suggestions {
  display: grid;
  grid-template-columns: repeat(2, minmax(220px, 1fr));
  gap: 10px;
  max-width: 720px;
  width: 100%;
  margin-top: 18px;
}
.suggestion-card {
  text-align: left;
  padding: 14px 16px;
  border-radius: var(--radius-md);
  background: var(--bg-input);
  border: 1px solid var(--border);
  color: var(--text-secondary);
  font-size: 0.9em;
  transition: border-color 0.15s, box-shadow 0.15s, transform 0.1s;
}
.suggestion-card:hover:not(:disabled) {
  border-color: var(--border-strong);
  box-shadow: var(--shadow-soft);
  color: var(--text);
  transform: translateY(-1px);
}

/* 对话流 */
.messages {
  flex: 1;
  overflow-y: auto;
}
.messages-inner {
  max-width: 760px;
  margin: 0 auto;
  padding: 28px 20px 12px;
}

@media (max-width: 640px) {
  .suggestions {
    grid-template-columns: 1fr;
  }
}
</style>
