<template>
  <div class="landing">
    <!-- Navbar -->
    <nav class="nav">
      <div class="nav-inner">
        <div class="nav-brand">
          <div class="brand-icon">
            <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M12 2L2 7l10 5 10-5-10-5z"/><path d="M2 17l10 5 10-5"/><path d="M2 12l10 5 10-5"/></svg>
          </div>
          <span class="brand-text">DocMind</span>
        </div>
        <div class="nav-links">
          <button @click="goToLogin" class="btn-ghost">{{ t('firsthome.nav.login') }}</button>
          <button @click="handlePrimaryCta" class="btn-primary-sm">{{ ctaLabel(t('firsthome.nav.trial')) }}</button>
        </div>
      </div>
    </nav>

    <!-- Hero -->
    <section class="hero">
      <div class="hero-bg">
        <div class="hero-glow hero-glow-1"></div>
        <div class="hero-glow hero-glow-2"></div>
        <div class="hero-grid"></div>
      </div>
      <div class="hero-content">
        <div class="hero-badge">
          <span class="badge-dot"></span>
          {{ t('firsthome.hero.badge') }}
        </div>
        <h1 class="hero-title">
          {{ t('firsthome.hero.titleLine1') }}<br>
          <span class="hero-title-accent">{{ t('firsthome.hero.titleAccent') }}</span>
        </h1>
        <p class="hero-desc">
          {{ t('firsthome.hero.desc') }}
        </p>
        <div class="hero-actions">
          <button @click="handlePrimaryCta" class="btn-primary">
            {{ ctaLabel(t('firsthome.hero.ctaPrimary')) }}
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M5 12h14"/><path d="M12 5l7 7-7 7"/></svg>
          </button>
          <button @click="goToLogin" class="btn-outline">
            {{ t('firsthome.hero.ctaLogin') }}
          </button>
        </div>
        <div class="hero-stats">
          <div class="stat-item">
            <span class="stat-value">88%</span>
            <span class="stat-label">{{ t('firsthome.stats.clauseHit') }}</span>
          </div>
          <div class="stat-divider"></div>
          <div class="stat-item">
            <span class="stat-value">28</span>
            <span class="stat-label">{{ t('firsthome.stats.cases') }}</span>
          </div>
          <div class="stat-divider"></div>
          <div class="stat-item">
            <span class="stat-value">4</span>
            <span class="stat-label">zh / en / ja / fr</span>
          </div>
        </div>
      </div>
    </section>

    <!-- How it works -->
    <section class="section">
      <div class="section-header">
        <h2 class="section-title">{{ t('firsthome.steps.title') }}</h2>
        <p class="section-desc">{{ t('firsthome.steps.desc') }}</p>
      </div>
      <div class="steps-grid">
        <div v-for="(step, i) in steps" :key="i" class="step-card">
          <div class="step-number">{{ String(i + 1).padStart(2, '0') }}</div>
          <div class="step-icon">
            <component :is="step.icon" size="24" />
          </div>
          <h3 class="step-title">{{ step.title }}</h3>
          <p class="step-desc">{{ step.desc }}</p>
        </div>
      </div>
    </section>

    <!-- Capabilities -->
    <section class="section section-alt">
      <div class="section-header">
        <h2 class="section-title">{{ t('firsthome.features.title') }}</h2>
        <p class="section-desc">{{ t('firsthome.features.desc') }}</p>
      </div>
      <div class="features-grid">
        <div v-for="f in features" :key="f.title" class="feature-card">
          <div class="feature-icon">
            <component :is="f.icon" size="22" />
          </div>
          <h3 class="feature-title">{{ f.title }}</h3>
          <p class="feature-desc">{{ f.desc }}</p>
        </div>
      </div>
    </section>

    <!-- Agent demo terminal -->
    <section class="section">
      <div class="section-header">
        <h2 class="section-title">{{ t('firsthome.terminal.title') }}</h2>
        <p class="section-desc">{{ t('firsthome.terminal.desc') }}</p>
      </div>
      <div class="terminal-wrap">
        <div class="terminal">
          <div class="terminal-bar">
            <div class="terminal-dots">
              <span></span><span></span><span></span>
            </div>
            <span class="terminal-title">Agent Terminal</span>
            <span class="terminal-demo">{{ t('firsthome.terminal.demo') }}</span>
          </div>
          <div class="terminal-body">
            <div class="terminal-line query">{{ t('firsthome.terminal.query') }}</div>
            <div class="terminal-line think">{{ t('firsthome.terminal.callSearch') }}</div>
            <div class="terminal-line result">{{ t('firsthome.terminal.result') }}</div>
            <div class="terminal-line think">{{ t('firsthome.terminal.callAnalyze') }}</div>
            <div class="terminal-line think">{{ t('firsthome.terminal.callCompare') }}</div>
            <div class="terminal-line answer">
              <span class="answer-label">{{ t('firsthome.terminal.answerLabel') }}</span>
              <span>{{ t('firsthome.terminal.answer') }}<br><br><span class="source-ref">{{ t('firsthome.terminal.sources') }}</span></span>
            </div>
          </div>
        </div>
      </div>
    </section>

    <!-- CTA -->
    <section class="cta">
      <div class="cta-glow"></div>
      <h2 class="cta-title">{{ t('firsthome.cta.title') }}</h2>
      <p class="cta-desc">{{ t('firsthome.cta.desc') }}</p>
      <button @click="handlePrimaryCta" class="btn-lg">
        {{ ctaLabel(t('firsthome.cta.button')) }}
        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M5 12h14"/><path d="M12 5l7 7-7 7"/></svg>
      </button>
    </section>

    <!-- Footer -->
    <footer class="footer">
      <div class="footer-inner">
        <div class="footer-brand">
          <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 2L2 7l10 5 10-5-10-5z"/><path d="M2 17l10 5 10-5"/><path d="M2 12l10 5 10-5"/></svg>
          <span>DocMind</span>
        </div>
        <span class="footer-copy">{{ t('firsthome.footer.copy') }}</span>
      </div>
    </footer>
  </div>
</template>

<script setup lang="ts">
import { computed, h } from 'vue'
import { useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { useUserStore } from '@/stores/user'

const router = useRouter()
const userStore = useUserStore()
const { t } = useI18n()

// ISS-1：登录态感知 —— 已登录访客的主 CTA 变为「进入工作台」直通 /dashboard，
// 未登录维持注册引导（已登录时 /register 会被守卫弹回工作台，CTA 不应再引向注册）
const isLoggedIn = computed(() => userStore.isLoggedIn)
const ctaLabel = (guestLabel: string) => (isLoggedIn.value ? t('firsthome.enterWorkspace') : guestLabel)
const handlePrimaryCta = () => router.push(isLoggedIn.value ? '/dashboard' : '/register')
const goToLogin = () => router.push('/login')

// Inline SVG icons
const UploadIcon = { render: () => h('svg', { width: 24, height: 24, viewBox: '0 0 24 24', fill: 'none', stroke: 'currentColor', 'stroke-width': '1.5', 'stroke-linecap': 'round', 'stroke-linejoin': 'round' }, [h('path', { d: 'M21 15v4a2 2 0 01-2 2H5a2 2 0 01-2-2v-4' }), h('path', { d: 'M17 8l-5-5-5 5' }), h('path', { d: 'M12 3v12' })]) }
const SearchIcon = { render: () => h('svg', { width: 24, height: 24, viewBox: '0 0 24 24', fill: 'none', stroke: 'currentColor', 'stroke-width': '1.5', 'stroke-linecap': 'round', 'stroke-linejoin': 'round' }, [h('circle', { cx: 11, cy: 11, r: 8 }), h('path', { d: 'M21 21l-4.3-4.3' })]) }
const BrainIcon = { render: () => h('svg', { width: 24, height: 24, viewBox: '0 0 24 24', fill: 'none', stroke: 'currentColor', 'stroke-width': '1.5', 'stroke-linecap': 'round', 'stroke-linejoin': 'round' }, [h('path', { d: 'M9.5 2A2.5 2.5 0 0112 4.5V15a2 2 0 01-2 2H6a2 2 0 01-2-2V7a2 2 0 012-2h3.5z' }), h('path', { d: 'M14.5 2A2.5 2.5 0 0012 4.5V15a2 2 0 002 2h4a2 2 0 002-2V7a2 2 0 00-2-2h-3.5z' })]) }
const ShieldIcon = { render: () => h('svg', { width: 24, height: 24, viewBox: '0 0 24 24', fill: 'none', stroke: 'currentColor', 'stroke-width': '1.5', 'stroke-linecap': 'round', 'stroke-linejoin': 'round' }, [h('path', { d: 'M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z' })]) }
const ZapIcon = { render: () => h('svg', { width: 24, height: 24, viewBox: '0 0 24 24', fill: 'none', stroke: 'currentColor', 'stroke-width': '1.5', 'stroke-linecap': 'round', 'stroke-linejoin': 'round' }, [h('polygon', { points: '13 2 3 14 12 14 11 22 21 10 12 10 13 2' })]) }
const LayersIcon = { render: () => h('svg', { width: 24, height: 24, viewBox: '0 0 24 24', fill: 'none', stroke: 'currentColor', 'stroke-width': '1.5', 'stroke-linecap': 'round', 'stroke-linejoin': 'round' }, [h('polygon', { points: '12 2 22 8.5 12 15 2 8.5 12 2' }), h('polyline', { points: '2 15.5 12 22 22 15.5' })]) }
const CodeIcon = { render: () => h('svg', { width: 24, height: 24, viewBox: '0 0 24 24', fill: 'none', stroke: 'currentColor', 'stroke-width': '1.5', 'stroke-linecap': 'round', 'stroke-linejoin': 'round' }, [h('polyline', { points: '16 18 22 12 16 6' }), h('polyline', { points: '8 6 2 12 8 18' })]) }

const steps = computed(() => [
  { icon: UploadIcon, title: t('firsthome.steps.upload.title'), desc: t('firsthome.steps.upload.desc') },
  { icon: SearchIcon, title: t('firsthome.steps.index.title'), desc: t('firsthome.steps.index.desc') },
  { icon: BrainIcon, title: t('firsthome.steps.ask.title'), desc: t('firsthome.steps.ask.desc') },
])

const features = computed(() => [
  { icon: LayersIcon, title: t('firsthome.features.hybrid.title'), desc: t('firsthome.features.hybrid.desc') },
  { icon: BrainIcon, title: t('firsthome.features.agent.title'), desc: t('firsthome.features.agent.desc') },
  { icon: ZapIcon, title: t('firsthome.features.stream.title'), desc: t('firsthome.features.stream.desc') },
  { icon: CodeIcon, title: t('firsthome.features.api.title'), desc: t('firsthome.features.api.desc') },
  { icon: ShieldIcon, title: t('firsthome.features.security.title'), desc: t('firsthome.features.security.desc') },
  { icon: UploadIcon, title: t('firsthome.features.tenant.title'), desc: t('firsthome.features.tenant.desc') },
])
</script>

<style scoped>
/* ===== Color palette: slate + blue accent ===== */
.landing {
  --c-bg: #f8fafc;
  --c-surface: #ffffff;
  --c-border: rgba(15, 23, 42, 0.07);
  --c-border-hover: rgba(15, 23, 42, 0.15);
  --c-text: #0f172a;
  --c-text-secondary: #475569;
  --c-text-muted: #94a3b8;
  --c-accent: #3b82f6;
  --c-accent-light: rgba(59, 130, 246, 0.08);
  --c-accent-border: rgba(59, 130, 246, 0.2);
  --c-accent-bg: rgba(59, 130, 246, 0.04);
  --c-icon-bg: #f1f5f9;
  --c-icon-fg: #475569;

  font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', 'PingFang SC', 'Hiragino Sans GB', 'Microsoft YaHei', sans-serif;
  color: var(--c-text);
  background: var(--c-bg);
  -webkit-font-smoothing: antialiased;
}

/* ===== Nav ===== */
.nav {
  position: fixed;
  top: 0; left: 0; right: 0;
  z-index: 50;
  background: rgba(248, 250, 252, 0.82);
  backdrop-filter: blur(16px);
  -webkit-backdrop-filter: blur(16px);
  border-bottom: 1px solid var(--c-border);
}
.nav-inner {
  max-width: 1200px;
  margin: 0 auto;
  padding: 0 24px;
  height: 60px;
  display: flex;
  align-items: center;
  justify-content: space-between;
}
.nav-brand {
  display: flex;
  align-items: center;
  gap: 10px;
}
.brand-icon {
  width: 34px; height: 34px;
  background: var(--c-text);
  border-radius: 10px;
  display: flex;
  align-items: center;
  justify-content: center;
  color: #fff;
}
.brand-text {
  font-size: 18px;
  font-weight: 700;
  letter-spacing: -0.5px;
  color: var(--c-text);
}
.nav-links {
  display: flex;
  align-items: center;
  gap: 8px;
}
.btn-ghost {
  padding: 8px 18px;
  border: none;
  background: none;
  color: var(--c-text-secondary);
  font-size: 14px;
  font-weight: 600;
  cursor: pointer;
  border-radius: 10px;
  transition: background 0.15s;
}
.btn-ghost:hover { background: rgba(15, 23, 42, 0.05); }
.btn-primary-sm {
  padding: 8px 18px;
  border: none;
  background: var(--c-text);
  color: #fff;
  font-size: 14px;
  font-weight: 600;
  cursor: pointer;
  border-radius: 10px;
  transition: opacity 0.15s, transform 0.15s;
}
.btn-primary-sm:hover { opacity: 0.9; transform: translateY(-1px); }

/* ===== Hero ===== */
.hero {
  position: relative;
  padding: 120px 24px 80px;
  overflow: hidden;
}
.hero-bg {
  position: absolute;
  inset: 0;
  pointer-events: none;
}
.hero-glow {
  position: absolute;
  border-radius: 50%;
  filter: blur(120px);
  opacity: 0.25;
}
.hero-glow-1 {
  top: -20%; left: -10%;
  width: 600px; height: 600px;
  background: rgba(59, 130, 246, 0.12);
}
.hero-glow-2 {
  bottom: -20%; right: -10%;
  width: 500px; height: 500px;
  background: rgba(100, 116, 139, 0.08);
}
.hero-grid {
  position: absolute;
  inset: 0;
  background-image:
    linear-gradient(rgba(15, 23, 42, 0.03) 1px, transparent 1px),
    linear-gradient(90deg, rgba(15, 23, 42, 0.03) 1px, transparent 1px);
  background-size: 60px 60px;
}
.hero-content {
  position: relative;
  z-index: 1;
  max-width: 800px;
  margin: 0 auto;
  text-align: center;
}
.hero-badge {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  padding: 6px 16px;
  border-radius: 100px;
  border: 1px solid var(--c-accent-border);
  background: var(--c-accent-bg);
  font-size: 13px;
  font-weight: 500;
  color: var(--c-accent);
  margin-bottom: 28px;
}
.badge-dot {
  width: 6px; height: 6px;
  border-radius: 50%;
  background: var(--c-accent);
  animation: pulse-dot 2s infinite;
}
@keyframes pulse-dot {
  0%, 100% { opacity: 1; }
  50% { opacity: 0.4; }
}
.hero-title {
  font-size: 56px;
  font-weight: 800;
  line-height: 1.15;
  letter-spacing: -2px;
  color: var(--c-text);
  margin: 0 0 20px;
}
.hero-title-accent {
  background: linear-gradient(135deg, #1e40af, #3b82f6, #1e40af);
  background-size: 200% 200%;
  -webkit-background-clip: text;
  -webkit-text-fill-color: transparent;
  background-clip: text;
  animation: gradient-shift 4s ease infinite;
}
@keyframes gradient-shift {
  0%, 100% { background-position: 0% 50%; }
  50% { background-position: 100% 50%; }
}
.hero-desc {
  font-size: 17px;
  line-height: 1.7;
  color: var(--c-text-secondary);
  margin: 0 0 36px;
  max-width: 560px;
  margin-left: auto;
  margin-right: auto;
}
.hero-actions {
  display: flex;
  gap: 12px;
  justify-content: center;
  margin-bottom: 56px;
}
.btn-primary {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  padding: 14px 28px;
  border: none;
  background: var(--c-text);
  color: #fff;
  font-size: 15px;
  font-weight: 600;
  cursor: pointer;
  border-radius: 14px;
  box-shadow: 0 4px 24px rgba(15, 23, 42, 0.25);
  transition: transform 0.15s, box-shadow 0.15s;
}
.btn-primary:hover { transform: translateY(-2px); box-shadow: 0 8px 32px rgba(15, 23, 42, 0.35); }
.btn-outline {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  padding: 14px 28px;
  border: 1.5px solid var(--c-border-hover);
  background: var(--c-surface);
  color: var(--c-text-secondary);
  font-size: 15px;
  font-weight: 600;
  cursor: pointer;
  border-radius: 14px;
  transition: border-color 0.15s, background 0.15s;
}
.btn-outline:hover { border-color: rgba(15, 23, 42, 0.25); background: #f1f5f9; }
.hero-stats {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 32px;
}
.stat-item {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 4px;
}
.stat-value {
  font-size: 24px;
  font-weight: 800;
  letter-spacing: -1px;
  color: var(--c-text);
}
.stat-label {
  font-size: 13px;
  color: var(--c-text-muted);
}
.stat-divider {
  width: 1px;
  height: 36px;
  background: var(--c-border);
}

/* ===== Sections ===== */
.section {
  padding: 80px 24px;
  max-width: 1200px;
  margin: 0 auto;
}
.section-alt {
  background: rgba(15, 23, 42, 0.015);
  max-width: none;
  padding: 80px 24px;
}
.section-header {
  text-align: center;
  margin-bottom: 56px;
}
.section-title {
  font-size: 36px;
  font-weight: 800;
  letter-spacing: -1px;
  color: var(--c-text);
  margin: 0 0 12px;
}
.section-desc {
  font-size: 16px;
  color: var(--c-text-muted);
  margin: 0;
}

/* ===== Steps ===== */
.steps-grid {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 20px;
  max-width: 960px;
  margin: 0 auto;
}
.step-card {
  position: relative;
  padding: 36px 28px;
  border-radius: 20px;
  border: 1px solid var(--c-border);
  background: var(--c-surface);
  transition: border-color 0.2s, box-shadow 0.2s;
}
.step-card:hover {
  border-color: var(--c-border-hover);
  box-shadow: 0 8px 32px rgba(15, 23, 42, 0.06);
}
.step-number {
  position: absolute;
  top: 20px; right: 24px;
  font-size: 48px;
  font-weight: 800;
  color: rgba(15, 23, 42, 0.04);
  letter-spacing: -2px;
  line-height: 1;
}
.step-icon {
  width: 48px; height: 48px;
  border-radius: 14px;
  display: flex;
  align-items: center;
  justify-content: center;
  margin-bottom: 20px;
  background: var(--c-icon-bg);
  color: var(--c-icon-fg);
}
.step-title {
  font-size: 17px;
  font-weight: 700;
  color: var(--c-text);
  margin: 0 0 8px;
}
.step-desc {
  font-size: 14px;
  line-height: 1.6;
  color: var(--c-text-muted);
  margin: 0;
}

/* ===== Features ===== */
.features-grid {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 16px;
  max-width: 1100px;
  margin: 0 auto;
}
.feature-card {
  padding: 28px;
  border-radius: 18px;
  border: 1px solid var(--c-border);
  background: var(--c-surface);
  transition: border-color 0.2s, transform 0.2s;
}
.feature-card:hover {
  border-color: var(--c-border-hover);
  transform: translateY(-2px);
}
.feature-icon {
  width: 44px; height: 44px;
  border-radius: 12px;
  display: flex;
  align-items: center;
  justify-content: center;
  margin-bottom: 16px;
  background: var(--c-icon-bg);
  color: var(--c-icon-fg);
}
.feature-title {
  font-size: 15px;
  font-weight: 700;
  color: var(--c-text);
  margin: 0 0 6px;
}
.feature-desc {
  font-size: 13px;
  line-height: 1.6;
  color: var(--c-text-muted);
  margin: 0;
}

/* ===== Terminal ===== */
.terminal-wrap {
  max-width: 760px;
  margin: 0 auto;
}
.terminal {
  border-radius: 18px;
  border: 1px solid rgba(15, 23, 42, 0.08);
  background: #0f172a;
  overflow: hidden;
  box-shadow: 0 16px 48px rgba(15, 23, 42, 0.12);
}
.terminal-bar {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 14px 18px;
  border-bottom: 1px solid rgba(255, 255, 255, 0.06);
}
.terminal-dots {
  display: flex;
  gap: 6px;
}
.terminal-dots span {
  width: 10px; height: 10px;
  border-radius: 50%;
}
.terminal-dots span:nth-child(1) { background: #ef4444; }
.terminal-dots span:nth-child(2) { background: #f59e0b; }
.terminal-dots span:nth-child(3) { background: #10b981; }
.terminal-title {
  font-size: 11px;
  color: #64748b;
  font-family: 'SF Mono', 'Fira Code', monospace;
}
.terminal-demo {
  margin-left: auto;
  font-size: 11px;
  color: #f59e0b;
  border: 1px solid rgba(245, 158, 11, 0.4);
  border-radius: 999px;
  padding: 2px 10px;
  letter-spacing: 0.5px;
}
.terminal-body {
  padding: 20px 22px;
  display: flex;
  flex-direction: column;
  gap: 10px;
}
.terminal-line {
  font-size: 13px;
  font-family: 'SF Mono', 'Fira Code', monospace;
  line-height: 1.7;
}
.terminal-line.query  { color: #e2e8f0; }
.terminal-line.think  { color: #60a5fa; }
.terminal-line.result { color: #34d399; font-size: 12px; }
.terminal-line.answer {
  margin-top: 8px;
  padding-top: 12px;
  border-top: 1px solid rgba(255, 255, 255, 0.06);
  color: #cbd5e1;
  display: flex;
  gap: 10px;
}
.answer-label { color: #34d399; flex-shrink: 0; font-weight: 600; }
.source-ref { color: #64748b; font-size: 12px; }

/* ===== CTA ===== */
.cta {
  position: relative;
  margin: 0 24px 80px;
  max-width: 1100px;
  margin-left: auto;
  margin-right: auto;
  padding: 72px 48px;
  border-radius: 28px;
  background: linear-gradient(135deg, #0f172a, #1e293b);
  text-align: center;
  overflow: hidden;
}
.cta-glow {
  position: absolute;
  top: 0; left: 50%;
  transform: translateX(-50%);
  width: 400px; height: 200px;
  background: rgba(59, 130, 246, 0.12);
  border-radius: 50%;
  filter: blur(80px);
}
.cta-title {
  position: relative;
  font-size: 36px;
  font-weight: 800;
  letter-spacing: -1px;
  color: #fff;
  margin: 0 0 12px;
}
.cta-desc {
  position: relative;
  font-size: 16px;
  color: rgba(255, 255, 255, 0.6);
  margin: 0 0 32px;
}
.btn-lg {
  position: relative;
  display: inline-flex;
  align-items: center;
  gap: 8px;
  padding: 16px 40px;
  font-size: 16px;
  border-radius: 16px;
  background: #fff;
  color: #0f172a;
  font-weight: 700;
  border: none;
  box-shadow: 0 4px 24px rgba(0, 0, 0, 0.15);
  cursor: pointer;
  transition: transform 0.15s, box-shadow 0.15s;
}
.btn-lg:hover { transform: translateY(-2px); box-shadow: 0 8px 32px rgba(0, 0, 0, 0.2); }

/* ===== Footer ===== */
.footer {
  padding: 40px 24px;
  border-top: 1px solid var(--c-border);
}
.footer-inner {
  max-width: 1100px;
  margin: 0 auto;
  display: flex;
  align-items: center;
  justify-content: space-between;
}
.footer-brand {
  display: flex;
  align-items: center;
  gap: 10px;
  font-size: 15px;
  font-weight: 700;
  color: var(--c-text);
}
.footer-brand svg { color: var(--c-text-secondary); }
.footer-copy {
  font-size: 13px;
  color: var(--c-text-muted);
}

@media (max-width: 768px) {
  .hero-title { font-size: 36px; }
  .steps-grid, .features-grid { grid-template-columns: 1fr; }
  .hero-stats { gap: 20px; }
  .footer-inner { flex-direction: column; gap: 12px; text-align: center; }
}

/* ===== H5 暗色适配（跟随全站 html.dark；扁平选择器，勿用嵌套块） =====
   亮色令牌在 .landing 上定义，暗色只重定义令牌变量；
   顶栏渐变/主按钮/品牌图标等硬编码亮底另行覆盖。 */
html.dark .landing {
  --c-bg: #0b1220;
  --c-surface: #111c2e;
  --c-border: rgba(148, 163, 184, 0.14);
  --c-border-hover: rgba(148, 163, 184, 0.3);
  --c-text: #e2e8f0;
  --c-text-secondary: #cbd5e1;
  --c-text-muted: #94a3b8;
  --c-accent: #60a5fa;
  --c-accent-light: rgba(96, 165, 250, 0.12);
  --c-accent-border: rgba(96, 165, 250, 0.32);
  --c-accent-bg: rgba(96, 165, 250, 0.08);
  --c-icon-bg: #1e293b;
  --c-icon-fg: #cbd5e1;
}

html.dark .nav {
  background: rgba(11, 18, 32, 0.82);
}

html.dark .btn-ghost:hover {
  background: rgba(148, 163, 184, 0.1);
}

html.dark .brand-icon {
  background: #2563eb;
}

html.dark .btn-primary-sm {
  background: #2563eb;
}

html.dark .btn-primary {
  background: #2563eb;
  box-shadow: 0 4px 24px rgba(37, 99, 235, 0.35);
}

html.dark .btn-primary:hover {
  box-shadow: 0 8px 32px rgba(37, 99, 235, 0.45);
}

html.dark .btn-outline:hover {
  background: #1e293b;
  border-color: rgba(148, 163, 184, 0.4);
}

/* 渐变标题：亮色渐变 #1e40af 起笔在深底上过暗，暗色换亮蓝系 */
html.dark .hero-title-accent {
  background-image: linear-gradient(135deg, #60a5fa, #93c5fd, #60a5fa);
  background-size: 200% 200%;
}

html.dark .hero-grid {
  background-image:
    linear-gradient(rgba(148, 163, 184, 0.07) 1px, transparent 1px),
    linear-gradient(90deg, rgba(148, 163, 184, 0.07) 1px, transparent 1px);
}

html.dark .section-alt {
  background: rgba(148, 163, 184, 0.04);
}

html.dark .step-number {
  color: rgba(148, 163, 184, 0.1);
}

html.dark .step-card:hover {
  box-shadow: 0 8px 32px rgba(0, 0, 0, 0.35);
}
</style>
