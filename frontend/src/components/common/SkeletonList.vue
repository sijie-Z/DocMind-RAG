<template>
  <div class="skeleton-container">
    <div v-for="i in count" :key="i" class="skeleton-item" :style="{ animationDelay: `${i * 0.1}s` }">
      <div v-if="avatar" class="skeleton-avatar"></div>
      <div class="skeleton-content">
        <div class="skeleton-title"></div>
        <div class="skeleton-lines">
          <div v-for="j in lines" :key="j" class="skeleton-line" :style="{ width: `${60 + Math.random() * 40}%` }"></div>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
defineProps({
  count: {
    type: Number,
    default: 3
  },
  avatar: {
    type: Boolean,
    default: true
  },
  lines: {
    type: Number,
    default: 3
  }
})
</script>

<style scoped>
.skeleton-container {
  padding: 16px;
}

.skeleton-item {
  display: flex;
  gap: 12px;
  padding: 16px;
  animation: fade-in 0.3s ease-out forwards;
  opacity: 0;
}

.skeleton-avatar {
  width: 40px;
  height: 40px;
  border-radius: 50%;
  background: linear-gradient(90deg, #f0f0f0 25%, #e0e0e0 50%, #f0f0f0 75%);
  background-size: 200% 100%;
  animation: shimmer 1.5s infinite;
  flex-shrink: 0;
}

.skeleton-content {
  flex: 1;
}

.skeleton-title {
  height: 16px;
  width: 40%;
  border-radius: 4px;
  background: linear-gradient(90deg, #f0f0f0 25%, #e0e0e0 50%, #f0f0f0 75%);
  background-size: 200% 100%;
  animation: shimmer 1.5s infinite;
  margin-bottom: 12px;
}

.skeleton-lines {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.skeleton-line {
  height: 12px;
  border-radius: 4px;
  background: linear-gradient(90deg, #f0f0f0 25%, #e0e0e0 50%, #f0f0f0 75%);
  background-size: 200% 100%;
  animation: shimmer 1.5s infinite;
}

@keyframes shimmer {
  0% { background-position: 200% 0; }
  100% { background-position: -200% 0; }
}

@keyframes fade-in {
  from { opacity: 0; transform: translateY(10px); }
  to { opacity: 1; transform: translateY(0); }
}
</style>
