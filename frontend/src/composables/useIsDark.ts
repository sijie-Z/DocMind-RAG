import { onMounted, onUnmounted, ref } from 'vue'

/**
 * 从 <html class="dark"> 读取当前主题，并用 MutationObserver 跟随切换。
 * 图表页只需要“现在是不是深色”，不应为此依赖 Pinia（纯展示组件可在无 store
 * 的测试环境直接挂载）。
 */
export function useIsDark() {
  const isDark = ref(
    typeof document !== 'undefined' && document.documentElement.classList.contains('dark'),
  )
  let observer: MutationObserver | null = null

  onMounted(() => {
    if (typeof MutationObserver === 'undefined') return
    observer = new MutationObserver(() => {
      isDark.value = document.documentElement.classList.contains('dark')
    })
    observer.observe(document.documentElement, { attributes: true, attributeFilter: ['class'] })
  })

  onUnmounted(() => {
    observer?.disconnect()
    observer = null
  })

  return isDark
}
