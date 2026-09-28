<script setup>
import { computed } from 'vue'
import { useChat } from '../composables/useChat'

const { sessions, activeId, createSession, selectSession, deleteSession } = useChat()
const collapsed = defineModel('collapsed', { type: Boolean, default: false })

const sessionList = computed(() => sessions.value)
</script>

<template>
  <aside class="sidebar" :class="{ collapsed }">
    <!-- 顶部：新建对话 -->
    <div class="sidebar-head">
      <button class="new-chat-btn" @click="createSession" title="新建对话">
        <svg width="16" height="16" viewBox="0 0 16 16" fill="none" aria-hidden="true">
          <path d="M8 3v10M3 8h10" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" />
        </svg>
        <span v-if="!collapsed">新建对话</span>
      </button>
    </div>

    <!-- 会话列表 -->
    <nav class="session-list" v-if="!collapsed">
      <div
        v-for="s in sessionList"
        :key="s.id"
        class="session-item"
        :class="{ active: s.id === activeId }"
        @click="selectSession(s.id)"
      >
        <span class="session-title" :title="s.title">{{ s.title }}</span>
        <button
          class="session-del"
          title="删除会话"
          @click.stop="deleteSession(s.id)"
        >
          <svg width="13" height="13" viewBox="0 0 16 16" fill="none" aria-hidden="true">
            <path d="M4 4l8 8M12 4l-8 8" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" />
          </svg>
        </button>
      </div>
      <div v-if="sessionList.length === 0" class="session-empty">还没有对话</div>
    </nav>

    <!-- 底部：折叠/展开 -->
    <div class="sidebar-foot">
      <button class="collapse-btn" @click="collapsed = !collapsed" :title="collapsed ? '展开侧边栏' : '收起侧边栏'">
        <svg v-if="!collapsed" width="16" height="16" viewBox="0 0 16 16" fill="none" aria-hidden="true">
          <path d="M10 4L6 8l4 4" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" />
        </svg>
        <svg v-else width="16" height="16" viewBox="0 0 16 16" fill="none" aria-hidden="true">
          <path d="M6 4l4 4-4 4" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" />
        </svg>
        <span v-if="!collapsed">收起</span>
      </button>
    </div>
  </aside>
</template>

<style scoped>
.sidebar {
  width: 272px;
  height: 100%;
  background: var(--bg-sidebar);
  display: flex;
  flex-direction: column;
  border-right: 1px solid var(--border);
  flex-shrink: 0;
  transition: width 0.18s ease;
}
.sidebar.collapsed {
  width: 56px;
}
.sidebar.collapsed .sidebar-head {
  justify-content: center;
  padding: 10px 10px;
}
.sidebar.collapsed .new-chat-btn {
  width: 36px;
  height: 36px;
  justify-content: center;
  padding: 0;
  border-radius: 50%;
}
.sidebar.collapsed .new-chat-btn span {
  display: none;
}

.sidebar-head {
  padding: 12px;
}
.new-chat-btn {
  width: 100%;
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 9px 12px;
  border-radius: var(--radius-md);
  color: var(--text);
  background: var(--bg-input);
  border: 1px solid var(--border-strong);
  font-weight: 500;
  transition: background 0.15s, border-color 0.15s;
}
.new-chat-btn:hover {
  background: var(--bg);
  border-color: var(--accent);
  color: var(--accent);
}

.session-list {
  flex: 1;
  overflow-y: auto;
  padding: 4px 8px;
}
.session-item {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 8px 10px;
  border-radius: var(--radius-sm);
  cursor: pointer;
  color: var(--text-secondary);
  font-size: 0.93em;
  transition: background 0.12s;
}
.session-item:hover {
  background: var(--bg-hover);
}
.session-item.active {
  background: var(--bg-active);
  color: var(--text);
}
.session-title {
  flex: 1;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.session-del {
  width: 22px;
  height: 22px;
  border-radius: 6px;
  display: none;
  align-items: center;
  justify-content: center;
  color: var(--text-faint);
}
.session-item:hover .session-del {
  display: flex;
}
.session-del:hover {
  color: var(--accent);
  background: var(--bg-hover);
}
.session-empty {
  padding: 16px 10px;
  color: var(--text-faint);
  font-size: 0.9em;
  text-align: center;
}

.sidebar-foot {
  padding: 10px 12px;
  border-top: 1px solid var(--border);
}
.collapse-btn {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 7px 10px;
  border-radius: var(--radius-sm);
  color: var(--text-secondary);
  width: 100%;
  transition: background 0.12s;
}
.collapse-btn:hover {
  background: var(--bg-hover);
}
.sidebar.collapsed .collapse-btn {
  justify-content: center;
  padding: 7px 0;
}
.sidebar.collapsed .collapse-btn span {
  display: none;
}
</style>
