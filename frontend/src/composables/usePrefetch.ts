import { onMounted } from 'vue'

export function usePrefetch() {
  const prefetchComponent = (componentPath: string) => {
    const link = document.createElement('link')
    link.rel = 'prefetch'
    link.as = 'script'
    link.href = `/src/${componentPath}`
    document.head.appendChild(link)
  }

  const prefetchRoute = (routePath: string) => {
    const link = document.createElement('link')
    link.rel = 'prefetch'
    link.as = 'fetch'
    link.href = routePath
    document.head.appendChild(link)
  }

  const preloadResource = (href: string, as: 'image' | 'font' | 'script' | 'style' = 'script') => {
    const link = document.createElement('link')
    link.rel = 'preload'
    link.as = as
    link.href = href
    if (as === 'font') {
      link.crossOrigin = 'anonymous'
    }
    document.head.appendChild(link)
  }

  onMounted(() => {
    const criticalRoutes = ['/chat', '/knowledge']
    criticalRoutes.forEach(route => prefetchRoute(route))
  })

  return {
    prefetchComponent,
    prefetchRoute,
    preloadResource
  }
}

export function useResourceHint() {
  const addDnsPrefetch = (domain: string) => {
    const link = document.createElement('link')
    link.rel = 'dns-prefetch'
    link.href = `https://${domain}`
    document.head.appendChild(link)
  }

  const addPreconnect = (domain: string) => {
    const link = document.createElement('link')
    link.rel = 'preconnect'
    link.href = `https://${domain}`
    link.crossOrigin = 'anonymous'
    document.head.appendChild(link)
  }

  onMounted(() => {
    addDnsPrefetch('fonts.googleapis.com')
    addDnsPrefetch('fonts.gstatic.com')
    addPreconnect('fonts.googleapis.com')
    addPreconnect('fonts.gstatic.com')
  })

  return {
    addDnsPrefetch,
    addPreconnect
  }
}
