<script setup>
import { useChat } from '../composables/useChat'

defineProps({
  open: { type: Boolean, default: false },
})
const emit = defineEmits(['close'])

const { sessions, activeId, createSession, selectSession, deleteSession } = useChat()

function createNew() {
  createSession()
  emit('close') // 窄屏选择后自动收起抽屉
}

function pick(id) {
  selectSession(id)
  emit('close')
}

// 时间：当天显示时刻，昨天 / 今年 / 跨年逐级简化
function formatTime(ts) {
  if (!ts) return ''
  const d = new Date(ts)
  const now = new Date()
  if (d.toDateString() === now.toDateString()) {
    return d.toLocaleTimeString('zh-CN', {
      hour: '2-digit',
      minute: '2-digit',
      hour12: false,
    })
  }
  const yesterday = new Date(now)
  yesterday.setDate(now.getDate() - 1)
  if (d.toDateString() === yesterday.toDateString()) return '昨天'
  if (d.getFullYear() === now.getFullYear()) {
    return `${d.getMonth() + 1}月${d.getDate()}日`
  }
  return `${d.getFullYear()}年${d.getMonth() + 1}月${d.getDate()}日`
}
</script>

<template>
  <aside class="sidebar" :class="{ open }">
    <div class="sidebar-head">
      <button class="new-chat" type="button" @click="createNew">
        <svg width="16" height="16" viewBox="0 0 16 16" fill="none" aria-hidden="true">
          <path
            d="M8 3.5v9M3.5 8h9"
            stroke="currentColor"
            stroke-width="1.6"
            stroke-linecap="round"
          />
        </svg>
        <span>新建对话</span>
      </button>
      <button
        class="drawer-close"
        type="button"
        aria-label="收起侧边栏"
        @click="emit('close')"
      >
        <svg width="16" height="16" viewBox="0 0 16 16" fill="none" aria-hidden="true">
          <path
            d="M4 4l8 8M12 4l-8 8"
            stroke="currentColor"
            stroke-width="1.5"
            stroke-linecap="round"
          />
        </svg>
      </button>
    </div>

    <!-- 会话列表：标题 + 时间，当前项左侧 2px 蓝线 -->
    <nav class="session-list" aria-label="历史对话">
      <div
        v-for="s in sessions"
        :key="s.id"
        class="session"
        :class="{ active: s.id === activeId }"
      >
        <button class="session-main" type="button" @click="pick(s.id)">
          <span class="session-title">{{ s.title || '新对话' }}</span>
          <span class="session-time">{{ formatTime(s.createdAt) }}</span>
        </button>
        <button
          class="session-del"
          type="button"
          title="删除对话"
          aria-label="删除对话"
          @click.stop="deleteSession(s.id)"
        >
          <svg width="12" height="12" viewBox="0 0 16 16" fill="none" aria-hidden="true">
            <path
              d="M4 4l8 8M12 4l-8 8"
              stroke="currentColor"
              stroke-width="1.6"
              stroke-linecap="round"
            />
          </svg>
        </button>
      </div>

      <p v-if="sessions.length === 0" class="session-empty">还没有对话</p>
    </nav>
  </aside>
</template>

<style scoped>
/* 侧边栏：260px 窄栏，弱底色 + 1px 右分隔线 */
.sidebar {
  display: flex;
  flex-direction: column;
  flex: 0 0 260px;
  width: 260px;
  height: 100%;
  background: var(--bg-soft);
  border-right: 1px solid var(--line);
}

.sidebar-head {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 16px 14px 10px;
}

/* 新建对话：白底细边框，hover 变浅灰，不抢主色 */
.new-chat {
  display: flex;
  flex: 1;
  align-items: center;
  gap: 8px;
  padding: 9px 12px;
  border: 1px solid var(--line);
  border-radius: var(--radius-sm);
  background: var(--bg);
  color: var(--ink);
  font-size: 14px;
  font-weight: 500;
  text-align: left;
  transition: background 0.15s, border-color 0.15s;
}

.new-chat:hover {
  background: var(--bg-hover);
  border-color: #d7dae0;
}

.new-chat svg {
  color: var(--accent);
  flex-shrink: 0;
}

/* 抽屉关闭按钮：只在窄屏出现 */
.drawer-close {
  display: none;
  align-items: center;
  justify-content: center;
  width: 34px;
  height: 34px;
  border-radius: var(--radius-sm);
  color: var(--muted);
}

.drawer-close:hover {
  background: var(--bg-hover);
  color: var(--ink);
}

.session-list {
  flex: 1;
  overflow-y: auto;
  padding: 4px 8px 16px;
}

.session {
  position: relative;
  display: flex;
  align-items: center;
  border-radius: var(--radius-sm);
  transition: background 0.15s;
}

.session:hover {
  background: var(--bg-hover);
}

.session.active {
  background: var(--accent-soft);
}

/* 当前会话：贴侧边栏左缘的 2px 蓝竖线 */
.session.active::before {
  content: '';
  position: absolute;
  left: -8px;
  top: 50%;
  width: 2px;
  height: 22px;
  border-radius: 1px;
  background: var(--accent);
  transform: translateY(-50%);
}

.session-main {
  display: flex;
  flex: 1;
  min-width: 0;
  flex-direction: column;
  gap: 2px;
  padding: 9px 10px;
  border-radius: var(--radius-sm);
  text-align: left;
}

.session-title {
  overflow: hidden;
  color: var(--ink);
  font-size: 14px;
  line-height: 1.4;
  white-space: nowrap;
  text-overflow: ellipsis;
}

.session-time {
  color: var(--muted-weak);
  font-size: 12px;
  line-height: 1.3;
  font-variant-numeric: tabular-nums;
}

/* 删除：hover / 选中时才显示，减少常驻噪音 */
.session-del {
  display: flex;
  flex-shrink: 0;
  align-items: center;
  justify-content: center;
  width: 24px;
  height: 24px;
  margin-right: 6px;
  border-radius: 6px;
  color: var(--muted-weak);
  opacity: 0;
  transition: opacity 0.12s, color 0.12s, background 0.12s;
}

.session:hover .session-del,
.session.active .session-del {
  opacity: 1;
}

.session-del:hover {
  color: var(--ink);
  background: rgba(31, 35, 40, 0.06);
}

.session-empty {
  padding: 20px 10px;
  color: var(--muted-weak);
  font-size: 13px;
  text-align: center;
}

/* 窄屏：侧边栏改为覆盖式抽屉，默认移出视口 */
@media (max-width: 819px) {
  .sidebar {
    position: fixed;
    inset: 0 auto 0 0;
    z-index: 30;
    transform: translateX(-100%);
    transition: transform 0.22s ease;
  }

  .sidebar.open {
    transform: none;
    box-shadow: 12px 0 32px rgba(31, 35, 40, 0.08); /* 仅抽屉需要轻投影区分层级 */
  }

  .drawer-close {
    display: flex;
  }
}
</style>
