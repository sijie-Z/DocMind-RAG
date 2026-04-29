<template>
  <n-card v-if="hasError" class="error-boundary" :bordered="false">
    <n-result status="error" title="页面出现了一些问题" description="请尝试刷新页面或联系管理员">
      <template #footer>
        <n-space justify="center">
          <n-button @click="handleReload">刷新页面</n-button>
          <n-button @click="handleGoHome">返回首页</n-button>
        </n-space>
      </template>
    </n-result>
  </n-card>
  <slot v-else />
</template>

<script setup lang="ts">
import { ref, onErrorCaptured } from 'vue'
import { useRouter } from 'vue-router'
import { NCard, NResult, NButton, NSpace } from 'naive-ui'

const router = useRouter()
const hasError = ref(false)

onErrorCaptured((err: Error) => {
  console.error('[ErrorBoundary]', err)
  hasError.value = true
  return false
})

const handleReload = () => {
  window.location.reload()
}

const handleGoHome = () => {
  router.push('/')
}
</script>

<style scoped>
.error-boundary {
  min-height: 100vh;
  display: flex;
  align-items: center;
  justify-content: center;
  background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
}
</style>
