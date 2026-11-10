import { onBeforeUnmount, onMounted, type Ref } from 'vue'

/**
 * 滚动进场：元素进入视口时加 .is-revealed（配合 styles/index.css 的 .reveal）。
 * 减少动态偏好或无 IntersectionObserver 时立即全部可见——绝不把内容藏死。
 */
export function useReveal(root: Ref<HTMLElement | null>, selector = '.reveal') {
  let observer: IntersectionObserver | null = null

  onMounted(() => {
    const el = root.value
    if (!el) return
    const targets = Array.from(el.querySelectorAll<HTMLElement>(selector))
    const reduced = window.matchMedia('(prefers-reduced-motion: reduce)').matches
    if (reduced || !('IntersectionObserver' in window)) {
      targets.forEach((target) => target.classList.add('is-revealed'))
      return
    }
    observer = new IntersectionObserver(
      (entries) => {
        for (const entry of entries) {
          if (!entry.isIntersecting) continue
          entry.target.classList.add('is-revealed')
          observer?.unobserve(entry.target)
        }
      },
      { threshold: 0.15, rootMargin: '0px 0px -8% 0px' },
    )
    targets.forEach((target) => observer?.observe(target))
  })

  onBeforeUnmount(() => {
    observer?.disconnect()
    observer = null
  })
}