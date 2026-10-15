// 说明：当前产品代码无消费者，仅由 __tests__/useDebounce.test.ts 覆盖；
// 按「测试资产」保留，请勿误删（如需复活，从生产代码 import 后即可）。
import { ref, watch, onUnmounted } from 'vue'
import type { Ref } from 'vue'

export function useDebounce<T>(value: Ref<T>, delay: number = 300): Ref<T> {
  const debouncedValue = ref(value.value) as Ref<T>
  let timeout: ReturnType<typeof setTimeout> | null = null

  watch(value, (newValue) => {
    if (timeout) clearTimeout(timeout)
    timeout = setTimeout(() => {
      debouncedValue.value = newValue
    }, delay)
  })

  onUnmounted(() => {
    if (timeout) clearTimeout(timeout)
  })

  return debouncedValue
}

export function useThrottle<T>(value: Ref<T>, delay: number = 300): Ref<T> {
  const throttledValue = ref(value.value) as Ref<T>
  let lastExecute = 0

  watch(value, (newValue) => {
    const now = Date.now()
    if (now - lastExecute >= delay) {
      throttledValue.value = newValue
      lastExecute = now
    }
  })

  return throttledValue
}

export function useDebounceFn<T extends (..._args: any[]) => any>(
  fn: T,
  delay: number = 300
): (..._args: Parameters<T>) => void {
  let timeout: ReturnType<typeof setTimeout> | null = null

  return (...args: Parameters<T>) => {
    if (timeout) clearTimeout(timeout)
    timeout = setTimeout(() => {
      fn(...args)
    }, delay)
  }
}

export function useThrottleFn<T extends (..._args: any[]) => any>(
  fn: T,
  delay: number = 300
): (..._args: Parameters<T>) => void {
  let lastExecute = 0

  return (...args: Parameters<T>) => {
    const now = Date.now()
    if (now - lastExecute >= delay) {
      fn(...args)
      lastExecute = now
    }
  }
}
