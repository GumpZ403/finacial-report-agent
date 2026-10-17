<script setup>
defineProps({
  // 后端 result.data：结构化数字数组，display 已格式化
  data: { type: Array, default: () => [] },
})
</script>

<template>
  <section v-if="data.length" class="metrics" aria-label="关键指标">
    <p class="metrics-head">关键数据</p>

    <!-- 两列表格（label｜display），行间 1px 细线 -->
    <div class="metrics-table">
      <div v-for="(m, i) in data" :key="i" class="metric-row">
        <div class="metric-label">
          <span class="label-text" :title="m.label">{{ m.label }}</span>
          <span v-if="m.period" class="period">{{ m.period }}</span>
        </div>
        <div class="metric-value">
          <span class="value">{{ m.display ?? m.value }}</span>
          <span
            v-if="m.estimated"
            class="badge"
            title="系统按公式派生，非公司直接披露"
          >
            派生
          </span>
        </div>
      </div>
    </div>
  </section>
</template>

<style scoped>
.metrics {
  max-width: 620px;
  margin-top: 16px;
}

.metrics-head {
  margin: 0 0 8px;
  color: var(--muted);
  font-size: 12px;
  letter-spacing: 0.02em;
}

/* 卡片只留 1px 外框，内部靠行线分隔 */
.metrics-table {
  overflow: hidden;
  border: 1px solid var(--line);
  border-radius: var(--radius-md);
  background: var(--bg);
}

.metric-row {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 16px;
  padding: 11px 14px;
}

.metric-row + .metric-row {
  border-top: 1px solid var(--line-soft);
}

.metric-label {
  display: flex;
  min-width: 0;
  align-items: baseline;
  gap: 8px;
}

.label-text {
  overflow: hidden;
  color: var(--ink);
  font-size: 14px;
  white-space: nowrap;
  text-overflow: ellipsis;
}

.period {
  flex-shrink: 0;
  color: var(--muted-weak);
  font-size: 12px;
  font-variant-numeric: tabular-nums;
}

.metric-value {
  display: flex;
  flex-shrink: 0;
  align-items: center;
  gap: 8px;
}

/* 数字等宽对齐，扫读时不跳动 */
.value {
  color: var(--ink);
  font-size: 15px;
  font-weight: 500;
  font-variant-numeric: tabular-nums;
  font-feature-settings: 'tnum';
}

/* 派生值：浅蓝底小徽标，克制地提示数据来源 */
.badge {
  display: inline-flex;
  align-items: center;
  height: 18px;
  padding: 0 7px;
  border-radius: 999px;
  background: var(--accent-soft);
  color: var(--accent);
  font-size: 11px;
  font-weight: 500;
  line-height: 1;
  letter-spacing: 0.02em;
}
</style>
