<script setup>
import { computed } from 'vue'
import { marked } from 'marked'
import { useChat } from '../composables/useChat'
import MetricCards from './MetricCards.vue'
import ClarifyOptions from './ClarifyOptions.vue'

const props = defineProps({
  message: { type: Object, required: true },
  streaming: { type: Boolean, default: false },
})

const { sendMessage } = useChat()

const isUser = computed(() => props.message.role === 'user')

const html = computed(() => {
  try {
    return marked.parse(props.message.content || '', { gfm: true, breaks: true })
  } catch {
    return props.message.content || ''
  }
})

// 后端已下发可读阶段文案（如“调用工具取数…”）；这里只对旧版服务端
// 只发 task_type 的情况做兜底映射，避免出现英文键名。
const STAGE_TEXT = {
  decide: '思考中…',
  simple: '正在查询…',
  react: '正在分析并调用工具…',
  plan: '正在规划并生成报告…',
  chat: '正在回复…',
  processing: '正在处理…',
}

const stageText = computed(() => {
  const s = props.message.stage
  return s ? STAGE_TEXT[s] || s : ''
})

// 只有流式等待、且还没有正文时，才显示单行阶段提示
const showStage = computed(
  () => !isUser.value && !props.message.content && props.streaming,
)

const metrics = computed(() => props.message.data || [])
const options = computed(() => props.message.options || [])
const showOptions = computed(
  () => !isUser.value && props.message.clarify && options.value.length > 0,
)

// 后端错误会以“请求失败/处理失败”开头，用克制的红色区分
const isError = computed(
  () => !isUser.value && /^(请求失败|处理失败)/.test(props.message.content || ''),
)
</script>

<template>
  <!-- 不用气泡：靠缩进 + 小标签区分角色 -->
  <article class="msg" :class="isUser ? 'msg-user' : 'msg-ai'">
    <div class="msg-label">
      <template v-if="isUser">你</template>
      <template v-else>
        <span class="ai-dot" aria-hidden="true"></span>
        {{ message.agent || '财报解读助手' }}
      </template>
    </div>

    <template v-if="isUser">
      <div class="msg-body plain">{{ message.content }}</div>
    </template>

    <template v-else>
      <!-- 阶段提示：单行小字 + 细微闪烁点，不转圈 -->
      <div v-if="showStage" class="stage" aria-live="polite">
        <span class="stage-dot" aria-hidden="true"></span>
        <span>{{ stageText || '正在思考…' }}</span>
      </div>

      <div
        v-else
        class="msg-body md-body"
        :class="{ 'is-error': isError }"
        v-html="html"
      ></div>

      <!-- 结构化数字：display 已格式化，直接展示 -->
      <MetricCards v-if="!showStage" :data="metrics" />

      <!-- 澄清选项：点击即作为下一条用户消息发送 -->
      <ClarifyOptions
        v-if="!showStage"
        :options="options"
        :disabled="streaming"
        @select="sendMessage"
      />
    </template>
  </article>
</template>

<style scoped>
.msg {
  padding: 18px 0;
}

/* 左右留白：用户消息从左侧缩进，助手消息占满内容列 */
.msg-user {
  padding-left: 14%;
}

.msg-label {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-bottom: 8px;
  color: var(--muted);
  font-size: 12px;
  letter-spacing: 0.01em;
}

.ai-dot {
  width: 6px;
  height: 6px;
  flex-shrink: 0;
  border-radius: 50%;
  background: var(--accent);
}

.msg-body {
  color: var(--ink);
  font-size: 15.5px;
  line-height: 1.75;
}

.plain {
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}

.is-error {
  color: #b42318;
}

/* 阶段提示：固定单行，只有圆点在呼吸 */
.stage {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 3px 0;
  color: var(--muted);
  font-size: 13px;
}

.stage-dot {
  width: 6px;
  height: 6px;
  flex-shrink: 0;
  border-radius: 50%;
  background: var(--accent);
  animation: stage-pulse 1.4s ease-in-out infinite;
}

@keyframes stage-pulse {
  0%,
  100% {
    opacity: 0.25;
    transform: scale(0.85);
  }
  50% {
    opacity: 1;
    transform: scale(1);
  }
}

/* 尊重减少动效偏好 */
@media (prefers-reduced-motion: reduce) {
  .stage-dot {
    animation: none;
    opacity: 0.8;
  }
}

@media (max-width: 819px) {
  .msg {
    padding: 14px 0;
  }

  .msg-user {
    padding-left: 8%;
  }
}
</style>
