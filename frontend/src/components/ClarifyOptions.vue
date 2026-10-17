<script setup>
import { ref } from 'vue'

const props = defineProps({
  options: { type: Array, default: () => [] },
  disabled: { type: Boolean, default: false },
})
const emit = defineEmits(['select'])

// 选定后整体禁用，避免重复发送同一条澄清回复
const picked = ref('')

function choose(opt) {
  if (props.disabled || picked.value) return
  picked.value = opt
  emit('select', opt)
}
</script>

<template>
  <div v-if="options.length" class="clarify">
    <p class="clarify-hint">选择一个选项，或直接输入</p>
    <div class="clarify-options">
      <button
        v-for="(opt, i) in options"
        :key="i"
        class="option"
        :class="{ picked: picked === opt }"
        type="button"
        :disabled="disabled || !!picked"
        @click="choose(opt)"
      >
        {{ opt }}
      </button>
    </div>
  </div>
</template>

<style scoped>
.clarify {
  margin-top: 14px;
}

.clarify-hint {
  margin: 0 0 8px;
  color: var(--muted);
  font-size: 12px;
}

.clarify-options {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

/* 选项按钮：细边框 + hover 浅蓝，和主按钮区分层级 */
.option {
  padding: 8px 14px;
  border: 1px solid var(--line);
  border-radius: var(--radius-sm);
  background: var(--bg);
  color: var(--ink);
  font-size: 14px;
  line-height: 1.4;
  text-align: left;
  transition: border-color 0.15s, background 0.15s, color 0.15s;
}

.option:hover:not(:disabled) {
  border-color: var(--accent);
  background: var(--accent-soft);
  color: var(--accent);
}

.option:disabled {
  cursor: not-allowed;
}

/* 已选项保留蓝色高亮，其余变弱 */
.option.picked {
  border-color: var(--accent);
  background: var(--accent-soft);
  color: var(--accent);
}

.option:disabled:not(.picked) {
  opacity: 0.55;
}

@media (max-width: 819px) {
  .option {
    width: 100%;
  }
}
</style>
