<script setup>
import { computed, nextTick, onMounted, ref, watch } from 'vue'
import { useChat } from '../composables/useChat'
import MessageItem from './MessageItem.vue'
import ChatInput from './ChatInput.vue'

const emit = defineEmits(['open-sidebar'])

const { activeSession, activeId, streaming, draft } = useChat()

const scrollEl = ref(null)
const nearBottom = ref(true)

const isEmpty = computed(
  () => !activeSession.value || activeSession.value.messages.length === 0,
)

// 空状态示例：点击只填入输入框，不直接发送
const suggestions = [
  '贵州茅台 2025 年一季度毛利率是多少？',
  '宁德时代近三年营收趋势如何？',
  '写一份贵州茅台的财报解读报告',
]

function fill(text) {
  draft.value = text
}

function scrollToBottom() {
  if (scrollEl.value) scrollEl.value.scrollTop = scrollEl.value.scrollHeight
}

function onScroll() {
  const el = scrollEl.value
  if (!el) return
  // 距底部 120px 内视为“正在跟随”，避免上翻时被拽回
  nearBottom.value = el.scrollHeight - el.scrollTop - el.clientHeight < 120
}

// 用最后一条消息的长相做信号：新消息、阶段文案变化都能触发跟随
const streamSignature = computed(() => {
  const msgs = activeSession.value?.messages
  if (!msgs?.length) return ''
  const last = msgs[msgs.length - 1]
  return `${msgs.length}:${(last.content || '').length}:${last.stage || ''}`
})

watch(streamSignature, async () => {
  await nextTick()
  if (nearBottom.value) scrollToBottom()
})

// 切换会话后直接落到最新位置
watch(activeId, async () => {
  nearBottom.value = true
  await nextTick()
  scrollToBottom()
})

onMounted(async () => {
  await nextTick()
  scrollToBottom()
})
</script>

<template>
  <main class="chat">
    <!-- 窄屏顶栏：抽屉入口 + 标题，桌面端隐藏 -->
    <header class="mobile-bar">
      <button
        class="menu-btn"
        type="button"
        aria-label="打开侧边栏"
        @click="emit('open-sidebar')"
      >
        <svg width="18" height="18" viewBox="0 0 18 18" fill="none" aria-hidden="true">
          <path
            d="M2.5 5h13M2.5 9h13M2.5 13h13"
            stroke="currentColor"
            stroke-width="1.5"
            stroke-linecap="round"
          />
        </svg>
      </button>
      <span class="mobile-title">财报解读助手</span>
    </header>

    <!-- 空状态：居中标题 + 一句说明 + 3 个示例问题 -->
    <section v-if="isEmpty" class="empty">
      <h1 class="empty-title">解读财报，从一个问题开始</h1>
      <p class="empty-sub">
        基于已入库的 A 股财报数据，查询财务指标、分析趋势，生成可读的财报解读。
      </p>
      <div class="suggestions">
        <button
          v-for="s in suggestions"
          :key="s"
          class="suggestion"
          type="button"
          @click="fill(s)"
        >
          <span>{{ s }}</span>
          <svg width="14" height="14" viewBox="0 0 16 16" fill="none" aria-hidden="true">
            <path
              d="M4 12L12 4M6 4h6v6"
              stroke="currentColor"
              stroke-width="1.4"
              stroke-linecap="round"
              stroke-linejoin="round"
            />
          </svg>
        </button>
      </div>
    </section>

    <!-- 对话流：贴底跟随，内容列居中限宽 -->
    <section
      v-else
      ref="scrollEl"
      class="messages"
      aria-live="polite"
      @scroll="onScroll"
    >
      <div class="messages-inner">
        <MessageItem
          v-for="(m, i) in activeSession.messages"
          :key="i"
          :message="m"
          :streaming="streaming"
        />
      </div>
    </section>

    <ChatInput />
  </main>
</template>

<style scoped>
.chat {
  display: flex;
  flex: 1;
  flex-direction: column;
  min-width: 0;
  height: 100%;
  background: var(--bg);
}

.mobile-bar {
  display: none;
}

/* 空状态：大量留白，焦点落在标题与示例上 */
.empty {
  display: flex;
  flex: 1;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  padding: 48px 24px 24px;
  text-align: center;
}

.empty-title {
  margin: 0;
  color: var(--ink);
  font-size: 28px;
  font-weight: 600;
  line-height: 1.35;
  letter-spacing: -0.02em;
}

.empty-sub {
  max-width: 480px;
  margin: 10px 0 0;
  color: var(--muted);
  font-size: 15px;
  line-height: 1.7;
}

/* 示例问题：三列卡片，点击仅填入输入框 */
.suggestions {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 12px;
  width: 100%;
  max-width: 760px;
  margin-top: 36px;
}

.suggestion {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 10px;
  padding: 16px;
  border: 1px solid var(--line);
  border-radius: var(--radius-md);
  background: var(--bg);
  color: var(--ink);
  font-size: 14px;
  line-height: 1.6;
  text-align: left;
  transition: border-color 0.15s, background 0.15s;
}

.suggestion:hover {
  border-color: var(--accent-line);
  background: var(--accent-soft);
}

.suggestion svg {
  flex-shrink: 0;
  margin-top: 3px;
  color: var(--muted-weak);
}

.suggestion:hover svg {
  color: var(--accent);
}

/* 对话流滚动区：只有这里滚动，输入框保持底部固定 */
.messages {
  flex: 1;
  overflow-y: auto;
  scroll-behavior: smooth;
}

.messages-inner {
  max-width: 720px;
  margin: 0 auto;
  padding: 28px 24px 16px;
}

/* 窄屏：顶栏出现，示例改单列 */
@media (max-width: 819px) {
  .mobile-bar {
    display: flex;
    flex-shrink: 0;
    align-items: center;
    gap: 10px;
    height: 52px;
    padding: 0 12px;
    border-bottom: 1px solid var(--line);
  }

  .menu-btn {
    display: flex;
    align-items: center;
    justify-content: center;
    width: 34px;
    height: 34px;
    border-radius: var(--radius-sm);
    color: var(--muted);
  }

  .menu-btn:hover {
    background: var(--bg-hover);
    color: var(--ink);
  }

  .mobile-title {
    color: var(--ink);
    font-size: 14px;
    font-weight: 500;
  }

  .empty {
    justify-content: flex-start;
    padding: 64px 20px 24px;
  }

  .empty-title {
    font-size: 24px;
  }

  .suggestions {
    grid-template-columns: 1fr;
    margin-top: 28px;
  }

  .messages-inner {
    padding: 20px 18px 8px;
  }
}
</style>
