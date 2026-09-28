<script setup>
import { ref } from 'vue'
import AppSidebar from './components/AppSidebar.vue'
import ChatView from './components/ChatView.vue'

const collapsed = ref(false)
</script>

<template>
  <div class="app-layout">
    <AppSidebar v-model:collapsed="collapsed" />
    <ChatView />
  </div>
</template>

<style scoped>
.app-layout {
  display: flex;
  height: 100vh;
  width: 100vw;
  overflow: hidden;
  background: var(--bg);
}
</style>
