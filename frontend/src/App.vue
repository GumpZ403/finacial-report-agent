<script setup>
import { onMounted, onUnmounted, ref } from 'vue'
import AppSidebar from './components/AppSidebar.vue'
import ChatView from './components/ChatView.vue'

// 窄屏抽屉：桌面端侧边栏常驻，此状态只影响 <820px 的覆盖层
const drawerOpen = ref(false)

function closeDrawer() {
  drawerOpen.value = false
}

// 离开窄屏时收起，避免回到窄屏时抽屉意外处于打开态
function onResize() {
  if (window.innerWidth >= 820) drawerOpen.value = false
}

// Esc 关闭浮层，符合常见习惯
function onKeydown(e) {
  if (e.key === 'Escape') drawerOpen.value = false
}

onMounted(() => {
  window.addEventListener('resize', onResize)
  window.addEventListener('keydown', onKeydown)
})

onUnmounted(() => {
  window.removeEventListener('resize', onResize)
  window.removeEventListener('keydown', onKeydown)
})
</script>

<template>
  <div class="app">
    <AppSidebar :open="drawerOpen" @close="closeDrawer" />

    <!-- 抽屉遮罩：仅窄屏出现，低对比、无重阴影 -->
    <button
      v-if="drawerOpen"
      class="backdrop"
      type="button"
      aria-label="关闭侧边栏"
      @click="closeDrawer"
    ></button>

    <ChatView @open-sidebar="drawerOpen = true" />
  </div>
</template>

<style scoped>
.app {
  display: flex;
  width: 100%;
  height: 100vh;
  height: 100dvh;
  overflow: hidden;
  background: var(--bg);
}

/* 遮罩默认隐藏，窄屏且有抽屉时覆盖对话区 */
.backdrop {
  display: none;
}

@media (max-width: 819px) {
  .backdrop {
    display: block;
    position: fixed;
    inset: 0;
    z-index: 20;
    background: rgba(31, 35, 40, 0.28);
  }
}
</style>
