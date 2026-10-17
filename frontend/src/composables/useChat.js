// 对话状态管理：SSE 流式调用后端 + 会话列表本地持久化（前后端分离，前端只管会话 UI）
import { ref, computed } from 'vue'

const STORAGE_KEY = 'fra_sessions'
const ACTIVE_KEY = 'fra_active_session'

/* ---------- 会话持久化（localStorage） ---------- */
function loadSessions() {
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    return raw ? JSON.parse(raw) : []
  } catch {
    return []
  }
}

function save(sessions) {
  localStorage.setItem(STORAGE_KEY, JSON.stringify(sessions))
}

/* ---------- 全局单例状态 ---------- */
const sessions = ref(loadSessions())
const activeId = ref(localStorage.getItem(ACTIVE_KEY) || sessions.value[0]?.id || null)
const streaming = ref(false)   // 是否有请求在跑（禁用发送）
const busyText = ref('')       // 正在处理中的用户问题（重复提交保护）
// 输入框草稿：放在这里是为了让空状态的示例问题能直接填入输入框
const draft = ref('')

const activeSession = computed(
  () => sessions.value.find((s) => s.id === activeId.value) || null,
)

function persistActive() {
  if (activeId.value) localStorage.setItem(ACTIVE_KEY, activeId.value)
}

function createSession() {
  const s = {
    id: Date.now().toString(36),
    title: '新对话',
    messages: [],
    createdAt: Date.now(),
  }
  sessions.value.unshift(s)
  activeId.value = s.id
  persistActive()
  save(sessions.value)
  return s
}

function selectSession(id) {
  activeId.value = id
  persistActive()
}

function deleteSession(id) {
  const idx = sessions.value.findIndex((s) => s.id === id)
  if (idx === -1) return
  sessions.value.splice(idx, 1)
  if (activeId.value === id) {
    activeId.value = sessions.value[0]?.id || null
    persistActive()
  }
  save(sessions.value)
}

function clearSessions() {
  sessions.value = []
  activeId.value = null
  localStorage.removeItem(ACTIVE_KEY)
  save(sessions.value)
}

/* ---------- SSE 流式对话 ---------- */
async function sendMessage(text) {
  const q = text.trim()
  if (!q || streaming.value) return

  // 保证存在会话；注意 createSession 返回的是原始对象，
  // 必须从 activeSession（proxy）重新取，后续 push/修改才能触发响应式更新
  let s = activeSession.value
  if (!s) {
    createSession()
    s = activeSession.value
  }
  if (s.title === '新对话') {
    s.title = q.length > 24 ? q.slice(0, 24) + '…' : q
  }

  // 追加用户消息 + 空的 AI 消息占位（data/clarify/options 一并预留）
  s.messages.push({ role: 'user', content: q })
  s.messages.push({
    role: 'assistant',
    content: '',
    agent: '',
    stage: '',
    data: [],
    clarify: false,
    options: [],
  })
  // 注意：必须通过响应式数组索引（proxy）读写，不能持有原始对象引用——
  // 直接修改原始对象不会触发 Vue 的 set 拦截，页面将不更新（刷新才可见）。
  const aiIdx = s.messages.length - 1
  const ai = () => s.messages[aiIdx]
  save(sessions.value)

  streaming.value = true
  busyText.value = q
  try {
    const resp = await fetch('/api/agent/chat/stream', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      // session_id 让后端按会话隔离记忆（此前不传，所有标签页共用一份上下文）
      body: JSON.stringify({ query: q, session_id: s.id }),
    })
    if (!resp.ok || !resp.body) {
      throw new Error(`后端返回 ${resp.status}`)
    }

    const reader = resp.body.getReader()
    const decoder = new TextDecoder('utf-8')
    let buf = ''

    while (true) {
      const { done, value } = await reader.read()
      if (done) break
      buf += decoder.decode(value, { stream: true })

      // 按 SSE 规范按行解析（data: JSON）
      const lines = buf.split('\n')
      buf = lines.pop() ?? ''
      for (const line of lines) {
        const t = line.trim()
        if (!t.startsWith('data:')) continue
        let evt
        try {
          evt = JSON.parse(t.slice(5).trim())
        } catch {
          continue
        }
        if (evt.type === 'stage') {
          // 后端按节点下发可读文案（识别意图… / 调用工具取数…）；stage 只做标识
          ai().stage = evt.message || evt.stage || 'processing'
        } else if (evt.type === 'result') {
          ai().content = evt.reply || ''
          ai().agent = evt.agent || ''
          ai().stage = ''
          // 结构化数字：display 已由后端格式化，前端直接展示
          ai().data = Array.isArray(evt.data) ? evt.data : []
          ai().clarify = Boolean(evt.clarify)
          ai().options = Array.isArray(evt.options) ? evt.options : []
        } else if (evt.type === 'error') {
          ai().stage = ''
          ai().data = []
          ai().clarify = false
          ai().options = []
          ai().content = `处理失败：${evt.message || '未知错误'}`
        }
      }
    }
    if (!ai().content) ai().content = '（未收到回复）'
    save(sessions.value)
  } catch (e) {
    ai().stage = ''
    ai().data = []
    ai().clarify = false
    ai().options = []
    ai().content = `请求失败：${e.message || '网络异常'}`
    save(sessions.value)
  } finally {
    streaming.value = false
    busyText.value = ''
  }
}

export function useChat() {
  return {
    sessions,
    activeId,
    activeSession,
    streaming,
    busyText,
    draft,
    createSession,
    selectSession,
    deleteSession,
    clearSessions,
    sendMessage,
  }
}
