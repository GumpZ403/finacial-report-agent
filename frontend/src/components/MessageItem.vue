<script setup>
import { computed } from 'vue'
import { marked } from 'marked'

const props = defineProps({
  message: { type: Object, required: true },
  streaming: { type: Boolean, default: false },
})

const isUser = computed(() => props.message.role === 'user')
const html = computed(() => {
  try {
    return marked.parse(props.message.content || '', { gfm: true, breaks: true })
  } catch {
    return props.message.content || ''
  }
})

// 流式阶段提示文字
const stageText = computed(() => {
  const s = props.message.stage
  if (!s) return ''
  const map = {
    simple: '正在查询…',
    react: '正在分析并调用工具…',
    plan: '正在规划并生成报告…',
    chat: '正在回复…',
    processing: '正在处理…',
  }
  return map[s] || `正在处理（${s}）…`
})
</script>

<template>
  <div class="msg" :class="isUser ? 'msg-user' : 'msg-ai'">
    <!-- AI 消息：agent 徽标 -->
    <div v-if="!isUser && (message.agent || stageText)" class="msg-ai-tag">
      {{ message.agent || '财报解读助手' }}
    </div>

    <div class="bubble" :class="isUser ? 'bubble-user' : 'bubble-ai'">
      <!-- AI 思考中：占位 + 阶段提示 -->
      <template v-if="!isUser && !message.content && stageText">
        <div class="thinking">
          <span class="dots"><i></i><i></i><i></i></span>
          <span class="stage">{{ stageText }}</span>
        </div>
      </template>
      <!-- 正常内容 -->
      <div v-else-if="isUser" class="plain">{{ message.content }}</div>
      <div v-else class="md-body" v-html="html"></div>
    </div>
  </div>
</template>

<style scoped>
.msg {
  display: flex;
  flex-direction: column;
  margin-bottom: 22px;
}
.msg-user {
  align-items: flex-end;
}
.msg-ai {
  align-items: flex-start;
}

.msg-ai-tag {
  font-size: 0.8em;
  color: var(--text-secondary);
  margin: 0 0 6px 2px;
}

.bubble {
  max-width: 100%;
  line-height: 1.6;
}
.bubble-user {
  background: var(--bg-user-msg);
  color: var(--text);
  padding: 10px 16px;
  border-radius: 18px;
  border-bottom-right-radius: 6px;
  max-width: 76%;
  font-size: 0.96em;
}
.bubble-ai {
  width: 100%;
  font-size: 0.96em;
}
.plain {
  white-space: pre-wrap;
  word-break: break-word;
}

.thinking {
  display: flex;
  align-items: center;
  gap: 10px;
  color: var(--text-secondary);
  font-size: 0.9em;
  padding: 4px 0;
}
.dots i {
  display: inline-block;
  width: 5px;
  height: 5px;
  border-radius: 50%;
  background: var(--accent);
  margin-right: 4px;
  animation: blink 1.2s infinite ease-in-out;
}
.dots i:nth-child(2) { animation-delay: 0.2s; }
.dots i:nth-child(3) { animation-delay: 0.4s; }
@keyframes blink {
  0%, 80%, 100% { opacity: 0.25; }
  40% { opacity: 1; }
}
</style>
