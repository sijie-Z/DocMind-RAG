<template>
  <div ref="landingRef" class="landing">
    <!-- 顶栏：纸面底色 + 细边框，单一动作蓝只用于主操作 -->
    <nav class="nav">
      <div class="nav-inner">
        <div class="nav-brand">
          <span class="brand-icon"><DocMindMark :size="22" /></span>
          <span class="brand-text">DocMind</span>
        </div>
        <div class="nav-links">
          <button type="button" class="btn-ghost" @click="goToLogin">
            {{ t('firsthome.nav.login') }}
          </button>
          <button type="button" class="btn-primary-sm" @click="handlePrimaryCta">
            {{ ctaLabel(t('firsthome.nav.trial')) }}
          </button>
        </div>
      </div>
    </nav>

    <main id="main-content">
    <!-- Hero：价值主张 + 产品示意（纯 HTML/CSS，标注为界面示意） -->
    <header class="hero">
      <div class="hero-inner">
        <div class="hero-copy">
          <p class="hero-badge">{{ t('firsthome.hero.badge') }}</p>
          <h1 class="hero-title">
            {{ t('firsthome.hero.titleLine1') }}
            <span class="hero-title-accent">{{ t('firsthome.hero.titleAccent') }}</span>
          </h1>
          <p class="hero-desc">{{ t('firsthome.hero.desc') }}</p>
          <div class="hero-actions">
            <button type="button" class="btn-primary" @click="handlePrimaryCta">
              {{ ctaLabel(t('firsthome.hero.ctaPrimary')) }}
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M5 12h14" /><path d="M13 6l6 6-6 6" /></svg>
            </button>
            <button type="button" class="btn-outline" @click="goToLogin">
              {{ t('firsthome.hero.ctaLogin') }}
            </button>
          </div>
        </div>

        <div class="hero-mock" role="img" :aria-label="t('firsthome.mock.label')">
          <div class="mock-caption" aria-hidden="true">{{ t('firsthome.mock.label') }}</div>
          <div class="mock-panel" aria-hidden="true">
            <div class="mock-head">
              <span class="mock-case">{{ t('firsthome.mock.caseTitle') }}</span>
              <span class="mock-diff">{{ t('firsthome.mock.diffBadge') }}</span>
            </div>

            <div class="mock-review">
              <div class="mock-review-title">{{ t('firsthome.mock.reviewTitle') }}</div>
              <div class="mock-compare">
                <div class="mock-value">
                  <span class="mock-label">{{ t('firsthome.mock.policyLabel') }}</span>
                  <span class="mock-text">{{ t('firsthome.mock.policyValue') }}</span>
                </div>
                <svg class="mock-arrow" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M5 12h14" /><path d="M13 6l6 6-6 6" /></svg>
                <div class="mock-value mock-value-changed">
                  <span class="mock-label">{{ t('firsthome.mock.contractLabel') }}</span>
                  <span class="mock-text">{{ t('firsthome.mock.contractValue') }}</span>
                </div>
              </div>
            </div>

            <div class="mock-evidence">
              <div class="mock-evidence-head">
                <span class="mock-evidence-label">{{ t('firsthome.mock.evidenceLabel') }}</span>
                <span class="mock-intact">
                  <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><path d="M20 6L9 17l-5-5" /></svg>
                  {{ t('firsthome.mock.evidenceIntact') }}
                </span>
              </div>
              <blockquote class="mock-quote">{{ t('firsthome.mock.evidenceQuote') }}</blockquote>
              <div class="mock-meta">{{ t('firsthome.mock.evidenceMeta') }}</div>
            </div>

            <div class="mock-qa">
              <div class="mock-question">{{ t('firsthome.mock.qaQuestion') }}</div>
              <p class="mock-answer">{{ t('firsthome.mock.qaAnswer') }}</p>
              <div class="mock-sources">
                <span>{{ t('firsthome.mock.qaSource1') }}</span>
                <span>{{ t('firsthome.mock.qaSource2') }}</span>
              </div>
            </div>
          </div>
        </div>
      </div>
    </header>

    <!-- 产品事实：不用无法核验的百分比 -->
    <section class="facts" :aria-label="t('firsthome.facts.evidence.title')">
      <ul class="facts-inner">
        <li v-for="(fact, i) in facts" :key="fact.title" class="fact reveal" :style="{ '--reveal-delay': `${i * 70}ms` }">
          <span class="fact-icon" aria-hidden="true">
            <component :is="fact.icon" />
          </span>
          <span class="fact-text">
            <span class="fact-title">{{ fact.title }}</span>
            <span class="fact-desc">{{ fact.desc }}</span>
          </span>
        </li>
      </ul>
    </section>

    <!-- 三步 -->
    <section class="section">
      <div class="section-header reveal">
        <h2 class="section-title">{{ t('firsthome.steps.title') }}</h2>
        <p class="section-desc">{{ t('firsthome.steps.desc') }}</p>
      </div>
      <ol class="steps-grid">
        <li v-for="(step, i) in steps" :key="step.title" class="step-card card-lift reveal" :style="{ '--reveal-delay': `${i * 90}ms` }">
          <span class="step-number" aria-hidden="true">{{ String(i + 1).padStart(2, '0') }}</span>
          <span class="step-icon" aria-hidden="true"><component :is="step.icon" /></span>
          <h3 class="step-title">{{ step.title }}</h3>
          <p class="step-desc">{{ step.desc }}</p>
        </li>
      </ol>
    </section>

    <!-- 能力 -->
    <section class="section section-alt">
      <div class="section-header reveal">
        <h2 class="section-title">{{ t('firsthome.features.title') }}</h2>
        <p class="section-desc">{{ t('firsthome.features.desc') }}</p>
      </div>
      <ul class="features-grid">
        <li v-for="(feature, i) in features" :key="feature.title" class="feature-card card-lift reveal" :style="{ '--reveal-delay': `${i * 70}ms` }">
          <span class="feature-icon" aria-hidden="true"><component :is="feature.icon" /></span>
          <h3 class="feature-title">{{ feature.title }}</h3>
          <p class="feature-desc">{{ feature.desc }}</p>
        </li>
      </ul>
    </section>

    <!-- CTA：墨色实底，无渐变 -->
    <section class="cta">
      <div class="cta-inner reveal">
        <h2 class="cta-title">{{ t('firsthome.cta.title') }}</h2>
        <p class="cta-desc">{{ t('firsthome.cta.desc') }}</p>
        <button type="button" class="btn-cta" @click="handlePrimaryCta">
          {{ ctaLabel(t('firsthome.cta.button')) }}
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M5 12h14" /><path d="M13 6l6 6-6 6" /></svg>
        </button>
      </div>
    </section>
    </main>

    <footer class="footer">
      <div class="footer-inner">
        <span class="footer-brand">
          <DocMindMark :size="20" />
          <span>DocMind</span>
        </span>
        <span class="footer-copy">{{ t('firsthome.footer.copy', { year: currentYear }) }}</span>
      </div>
    </footer>
  </div>
</template>

<script setup lang="ts">
import { computed, h, ref } from 'vue'
import { useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { useUserStore } from '@/stores/user'
import { useReveal } from '@/composables/useReveal'
import DocMindMark from '@/components/brand/DocMindMark.vue'

const router = useRouter()
const userStore = useUserStore()
const { t } = useI18n()
const landingRef = ref<HTMLElement | null>(null)
useReveal(landingRef)

// ISS-1：登录态感知——已登录访客的主 CTA 变为「进入工作台」直通 /dashboard。
const isLoggedIn = computed(() => userStore.isLoggedIn)
const ctaLabel = (guestLabel: string) => (isLoggedIn.value ? t('firsthome.enterWorkspace') : guestLabel)
const handlePrimaryCta = () => router.push(isLoggedIn.value ? '/dashboard' : '/register')
const goToLogin = () => router.push('/login')
const currentYear = new Date().getFullYear()

// 线性图标（无 emoji）：统一 24 视框、1.6 描边、currentColor
const icon = (paths: string[]) =>
  ({
    render: () =>
      h(
        'svg',
        {
          width: 20,
          height: 20,
          viewBox: '0 0 24 24',
          fill: 'none',
          stroke: 'currentColor',
          'stroke-width': '1.6',
          'stroke-linecap': 'round',
          'stroke-linejoin': 'round',
          'aria-hidden': 'true',
        },
        paths.map((d) => h('path', { d })),
      ),
  })

const EvidenceIcon = icon([
  'M14 2H6a2 2 0 00-2 2v16a2 2 0 002 2h12a2 2 0 002-2V8z',
  'M14 2v6h6',
  'M9 15l2 2 4-4',
])
const LinkIcon = icon(['M10 13a5 5 0 007.1 0l2-2a5 5 0 00-7.1-7.1l-1.1 1.1', 'M14 11a5 5 0 00-7.1 0l-2 2A5 5 0 0012 20.1l1.1-1.1'])
const HashIcon = icon(['M4 9h16', 'M4 15h16', 'M10 3L8 21', 'M16 3l-2 18'])
const GlobeIcon = icon(['M12 2a10 10 0 100 20 10 10 0 000-20z', 'M2 12h20', 'M12 2a15.3 15.3 0 014 10 15.3 15.3 0 01-4 10 15.3 15.3 0 01-4-10 15.3 15.3 0 014-10z'])
const UploadIcon = icon(['M21 15v4a2 2 0 01-2 2H5a2 2 0 01-2-2v-4', 'M17 8l-5-5-5 5', 'M12 3v12'])
const AlignIcon = icon(['M8 6h13', 'M3 6h.01', 'M8 12h13', 'M3 12h.01', 'M8 18h13', 'M3 18h.01'])
const ReportIcon = icon(['M14 2H6a2 2 0 00-2 2v16a2 2 0 002 2h12a2 2 0 002-2V8z', 'M14 2v6h6', 'M8 13h8', 'M8 17h5'])
const QuoteIcon = icon(['M21 15a2 2 0 01-2 2H7l-4 4V5a2 2 0 012-2h14a2 2 0 012 2z', 'M8 9h8', 'M8 13h5'])
const CompareIcon = icon(['M8 3H5a2 2 0 00-2 2v14a2 2 0 002 2h3', 'M16 3h3a2 2 0 012 2v14a2 2 0 01-2 2h-3', 'M12 8v8', 'M9 11l3-3 3 3'])
const ShieldCheckIcon = icon(['M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z', 'M9 12l2 2 4-4'])

const facts = computed(() => [
  { icon: EvidenceIcon, title: t('firsthome.facts.evidence.title'), desc: t('firsthome.facts.evidence.desc') },
  { icon: LinkIcon, title: t('firsthome.facts.traceable.title'), desc: t('firsthome.facts.traceable.desc') },
  { icon: HashIcon, title: t('firsthome.facts.hash.title'), desc: t('firsthome.facts.hash.desc') },
  { icon: GlobeIcon, title: t('firsthome.facts.languages.title'), desc: t('firsthome.facts.languages.desc') },
])

const steps = computed(() => [
  { icon: UploadIcon, title: t('firsthome.steps.upload.title'), desc: t('firsthome.steps.upload.desc') },
  { icon: AlignIcon, title: t('firsthome.steps.align.title'), desc: t('firsthome.steps.align.desc') },
  { icon: ReportIcon, title: t('firsthome.steps.decide.title'), desc: t('firsthome.steps.decide.desc') },
])

const features = computed(() => [
  { icon: QuoteIcon, title: t('firsthome.features.qa.title'), desc: t('firsthome.features.qa.desc') },
  { icon: CompareIcon, title: t('firsthome.features.review.title'), desc: t('firsthome.features.review.desc') },
  { icon: ShieldCheckIcon, title: t('firsthome.features.audit.title'), desc: t('firsthome.features.audit.desc') },
  { icon: GlobeIcon, title: t('firsthome.features.deploy.title'), desc: t('firsthome.features.deploy.desc') },
])
</script>

<style scoped>
/* ===== 纸 + 墨：单一动作蓝 #2563eb，结构由边框与灰度建立 ===== */
.landing {
  --c-bg: #f8fafc;
  --c-surface: #ffffff;
  --c-surface-subtle: #f1f5f9;
  --c-border: rgba(15, 23, 42, 0.1);
  --c-border-strong: rgba(15, 23, 42, 0.2);
  --c-text: #0f172a;
  --c-text-secondary: #475569;
  --c-text-muted: #64748b;
  --c-accent: #2563eb;
  --c-accent-strong: #1d4ed8;
  --c-success: #047857;
  --c-success-bg: #ecfdf5;
  --c-warning: #b45309;
  --c-warning-bg: #fffbeb;
  --c-ink: #0f172a;
  --c-on-ink: #f8fafc;

  font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', 'PingFang SC', 'Hiragino Sans GB', 'Microsoft YaHei', sans-serif;
  color: var(--c-text);
  background: var(--c-bg);
  -webkit-font-smoothing: antialiased;
}

button {
  font: inherit;
}

button:focus-visible {
  outline: 2px solid var(--c-accent);
  outline-offset: 2px;
}

/* ===== 顶栏 ===== */
.nav {
  position: sticky;
  top: 0;
  z-index: 50;
  background: rgba(248, 250, 252, 0.85);
  backdrop-filter: saturate(1.4) blur(10px);
  border-bottom: 1px solid var(--c-border);
}

.dark .nav {
  background: rgba(11, 18, 32, 0.82);
}
.nav-inner {
  max-width: 1120px;
  margin: 0 auto;
  padding: 0 24px;
  min-height: 64px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
}
.nav-brand {
  display: flex;
  align-items: center;
  gap: 10px;
  min-width: 0;
}
.brand-icon {
  width: 34px;
  height: 34px;
  flex-shrink: 0;
  border-radius: 8px;
  background: var(--c-accent-strong);
  color: #fff;
  display: flex;
  align-items: center;
  justify-content: center;
}
.brand-text {
  font-size: 17px;
  font-weight: 600;
  letter-spacing: -0.01em;
  color: var(--c-text);
}
.nav-links {
  display: flex;
  align-items: center;
  gap: 8px;
}
.btn-ghost {
  min-height: 44px;
  padding: 0 16px;
  border: none;
  background: none;
  color: var(--c-text-secondary);
  font-size: 14px;
  font-weight: 500;
  border-radius: 6px;
  cursor: pointer;
  transition: background-color 0.15s;
}
.btn-ghost:hover {
  background: var(--c-surface-subtle);
}
.btn-ghost:active {
  background: var(--c-surface);
}
.btn-ghost:disabled {
  color: var(--c-text-muted);
  cursor: not-allowed;
}
.btn-primary-sm {
  min-height: 44px;
  padding: 0 18px;
  border: none;
  background: var(--c-accent);
  color: #fff;
  font-size: 14px;
  font-weight: 500;
  border-radius: 6px;
  cursor: pointer;
  transition: background-color 0.15s;
}
.btn-primary-sm:hover {
  background: var(--c-accent-strong);
}
.btn-primary-sm:active {
  background: var(--c-accent-strong);
}
.btn-primary-sm:disabled {
  opacity: 0.55;
  cursor: not-allowed;
}

/* ===== Hero ===== */
.hero {
  position: relative;
  padding: 64px 24px 72px;
  border-bottom: 1px solid var(--c-border);
  background:
    radial-gradient(ellipse 60% 48% at 82% 8%, var(--c-primary-soft) 0%, transparent 70%),
    radial-gradient(ellipse 42% 36% at 12% 92%, rgba(4, 120, 87, 0.05) 0%, transparent 70%);
}

/* 点阵纸纹：首屏的「证据纸」底，向四周淡出 */
.hero::before {
  content: '';
  position: absolute;
  inset: 0;
  background-image: radial-gradient(var(--c-border) 1px, transparent 1px);
  background-size: 22px 22px;
  mask-image: radial-gradient(ellipse 70% 60% at 50% 40%, black 0%, transparent 80%);
  pointer-events: none;
}

.hero-inner {
  position: relative;
  max-width: 1120px;
  margin: 0 auto;
  display: grid;
  grid-template-columns: minmax(0, 1.02fr) minmax(0, 0.98fr);
  gap: 56px;
  align-items: center;
}
.hero-copy {
  min-width: 0;
}
.hero-badge {
  display: inline-flex;
  align-items: center;
  margin: 0 0 20px;
  padding: 5px 12px;
  border: 1px solid var(--c-border-strong);
  border-radius: 999px;
  font-size: 12px;
  font-weight: 500;
  letter-spacing: 0.02em;
  color: var(--c-accent);
}
.hero-title {
  margin: 0 0 20px;
  font-size: clamp(2.125rem, 4.4vw, 3.25rem);
  line-height: 1.14;
  letter-spacing: -0.025em;
  font-weight: 600;
  color: var(--c-text);
  text-wrap: balance;
}
.hero-title-accent {
  display: block;
  color: var(--c-accent);
}
.hero-desc {
  margin: 0 0 32px;
  max-width: 34em;
  font-size: 16px;
  line-height: 1.7;
  color: var(--c-text-secondary);
  text-wrap: pretty;
}
.hero-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 12px;
}
.btn-primary {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  min-height: 48px;
  padding: 0 24px;
  border: none;
  background: var(--c-accent);
  color: #fff;
  font-size: 15px;
  font-weight: 500;
  border-radius: 8px;
  cursor: pointer;
  transition: background-color 0.15s;
}
.btn-primary:hover {
  background: var(--c-accent-strong);
}
.btn-primary:active {
  background: var(--c-accent-strong);
}
.btn-primary:disabled {
  opacity: 0.55;
  cursor: not-allowed;
}
.btn-outline {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-height: 48px;
  padding: 0 24px;
  border: 1px solid var(--c-border-strong);
  background: var(--c-surface);
  color: var(--c-text);
  font-size: 15px;
  font-weight: 500;
  border-radius: 8px;
  cursor: pointer;
  transition: background-color 0.15s, border-color 0.15s;
}
.btn-outline:hover {
  background: var(--c-surface-subtle);
  border-color: var(--c-text-muted);
}
.btn-outline:active {
  background: var(--c-surface);
}
.btn-outline:disabled {
  opacity: 0.55;
  cursor: not-allowed;
}

/* ===== 产品示意（纯 HTML/CSS） ===== */
.hero-mock {
  min-width: 0;
}
.mock-caption {
  margin: 0 0 8px;
  font-size: 12px;
  color: var(--c-text-muted);
  text-align: right;
}
.mock-panel {
  border: 1px solid var(--c-border);
  border-radius: 12px;
  background: var(--c-surface);
  box-shadow: var(--shadow-2);
  overflow: hidden;
  transition:
    transform var(--dur-3) var(--ease-out),
    box-shadow var(--dur-3) var(--ease-out);
}

.hero-mock:hover .mock-panel {
  transform: translateY(-4px) rotate(-0.4deg);
  box-shadow: var(--shadow-3);
}
.mock-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 12px 16px;
  border-bottom: 1px solid var(--c-border);
  background: var(--c-surface-subtle);
}
.mock-case {
  font-size: 13px;
  font-weight: 600;
  color: var(--c-text);
}
.mock-diff {
  flex-shrink: 0;
  padding: 2px 8px;
  border: 1px solid rgba(180, 83, 9, 0.35);
  border-radius: 999px;
  background: var(--c-warning-bg);
  color: var(--c-warning);
  font-size: 11px;
  font-weight: 500;
  white-space: nowrap;
}
.mock-review {
  padding: 16px;
  border-bottom: 1px solid var(--c-border);
}
.mock-review-title {
  margin-bottom: 12px;
  font-size: 13px;
  font-weight: 600;
  color: var(--c-text);
}
.mock-compare {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto minmax(0, 1fr);
  align-items: center;
  gap: 12px;
}
.mock-value {
  min-width: 0;
  padding: 10px 12px;
  border: 1px solid var(--c-border);
  border-radius: 8px;
  background: var(--c-bg);
}
.mock-value-changed {
  border-color: rgba(180, 83, 9, 0.4);
  background: var(--c-warning-bg);
}
.mock-label {
  display: block;
  margin-bottom: 4px;
  font-size: 11px;
  color: var(--c-text-muted);
}
.mock-text {
  display: block;
  font-size: 13px;
  line-height: 1.45;
  color: var(--c-text);
  overflow-wrap: anywhere;
}
.mock-arrow {
  flex-shrink: 0;
  color: var(--c-text-muted);
}
.mock-evidence {
  padding: 16px;
  border-bottom: 1px solid var(--c-border);
}
.mock-evidence-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  margin-bottom: 8px;
}
.mock-evidence-label {
  font-size: 12px;
  font-weight: 600;
  color: var(--c-text-secondary);
}
.mock-intact {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  flex-shrink: 0;
  padding: 2px 8px;
  border: 1px solid rgba(4, 120, 87, 0.3);
  border-radius: 999px;
  background: var(--c-success-bg);
  color: var(--c-success);
  font-size: 11px;
  font-weight: 500;
  white-space: nowrap;
}
.mock-quote {
  margin: 0 0 8px;
  padding: 0 0 0 10px;
  border-left: 2px solid var(--c-accent);
  font-size: 13px;
  line-height: 1.6;
  color: var(--c-text-secondary);
}
.mock-meta {
  font-size: 11px;
  color: var(--c-text-muted);
}
.mock-qa {
  padding: 16px;
}
.mock-question {
  margin-bottom: 8px;
  font-size: 13px;
  font-weight: 600;
  color: var(--c-text);
}
.mock-answer {
  margin: 0 0 8px;
  font-size: 13px;
  line-height: 1.6;
  color: var(--c-text-secondary);
}
.mock-sources {
  display: flex;
  flex-direction: column;
  gap: 2px;
  font-size: 11px;
  color: var(--c-accent);
}

/* ===== 产品事实 ===== */
.facts {
  border-bottom: 1px solid var(--c-border);
  background: var(--c-surface);
}
.facts-inner {
  max-width: 1120px;
  margin: 0 auto;
  padding: 24px;
  list-style: none;
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 24px;
}
.fact {
  display: flex;
  align-items: flex-start;
  gap: 10px;
  min-width: 0;
}
.fact-icon {
  flex-shrink: 0;
  display: inline-flex;
  color: var(--c-accent);
}
.fact-text {
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
}
.fact-title {
  font-size: 14px;
  font-weight: 600;
  color: var(--c-text);
}
.fact-desc {
  font-size: 13px;
  line-height: 1.5;
  color: var(--c-text-muted);
}

/* ===== 区块 ===== */
.section {
  padding: 72px 24px;
  max-width: 1120px;
  margin: 0 auto;
}
.section-alt {
  max-width: none;
  background: var(--c-surface);
  border-top: 1px solid var(--c-border);
  border-bottom: 1px solid var(--c-border);
}
.section-alt .section-header,
.section-alt .features-grid {
  max-width: 1120px;
  margin-left: auto;
  margin-right: auto;
}
.section-header {
  margin-bottom: 40px;
}
.section-title {
  margin: 0 0 8px;
  font-size: clamp(1.5rem, 2.4vw, 1.75rem);
  line-height: 1.25;
  letter-spacing: -0.02em;
  font-weight: 600;
  color: var(--c-text);
  text-wrap: balance;
}
.section-desc {
  margin: 0;
  font-size: 15px;
  line-height: 1.6;
  color: var(--c-text-muted);
  text-wrap: pretty;
}

/* ===== 三步 ===== */
.steps-grid {
  list-style: none;
  margin: 0;
  padding: 0;
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 16px;
}
.step-card {
  position: relative;
  padding: 24px;
  border: 1px solid var(--c-border);
  border-radius: 8px;
  background: var(--c-surface);
}
.step-number {
  position: absolute;
  top: 20px;
  right: 20px;
  font-size: 13px;
  font-weight: 500;
  color: var(--c-text-muted);
  font-variant-numeric: tabular-nums;
}
.step-icon {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 40px;
  height: 40px;
  margin-bottom: 16px;
  border-radius: 8px;
  background: var(--c-primary-soft);
  color: var(--c-accent);
  transition:
    background var(--dur-2) var(--ease-out),
    color var(--dur-2) var(--ease-out),
    transform var(--dur-2) var(--ease-spring);
}

.step-card:hover .step-icon {
  background: var(--c-accent);
  color: #ffffff;
  transform: scale(1.06);
}
.step-title {
  margin: 0 0 8px;
  font-size: 16px;
  font-weight: 500;
  color: var(--c-text);
}
.step-desc {
  margin: 0;
  font-size: 14px;
  line-height: 1.65;
  color: var(--c-text-secondary);
}

/* ===== 能力 ===== */
.features-grid {
  list-style: none;
  margin: 0;
  padding: 0;
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 16px;
}
.feature-card {
  padding: 24px;
  border: 1px solid var(--c-border);
  border-radius: 8px;
  background: var(--c-bg);
}
.feature-icon {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 40px;
  height: 40px;
  margin-bottom: 16px;
  border-radius: 8px;
  background: var(--c-surface);
  border: 1px solid var(--c-border);
  color: var(--c-accent);
}
.feature-title {
  margin: 0 0 8px;
  font-size: 15px;
  font-weight: 500;
  color: var(--c-text);
}
.feature-desc {
  margin: 0;
  font-size: 13px;
  line-height: 1.65;
  color: var(--c-text-secondary);
}

/* ===== CTA ===== */
.cta {
  position: relative;
  background: linear-gradient(115deg, var(--c-ink) 0%, #12244a 58%, #1d4ed8 132%);
  overflow: hidden;
}

.cta::before {
  content: '';
  position: absolute;
  width: 560px;
  height: 560px;
  right: -160px;
  top: -260px;
  background: radial-gradient(circle, rgba(96, 165, 250, 0.22) 0%, transparent 65%);
  pointer-events: none;
}

.cta-inner {
  position: relative;
  max-width: 1120px;
  margin: 0 auto;
  padding: 56px 24px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 32px;
}
.cta-title {
  margin: 0 0 8px;
  font-size: clamp(1.5rem, 2.6vw, 1.875rem);
  line-height: 1.25;
  letter-spacing: -0.02em;
  font-weight: 600;
  color: var(--c-on-ink);
  text-wrap: balance;
}
.cta-desc {
  margin: 0;
  font-size: 14px;
  line-height: 1.6;
  color: #cbd5e1;
}
.btn-cta {
  flex-shrink: 0;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  min-height: 48px;
  padding: 0 24px;
  border: none;
  border-radius: 8px;
  background: var(--c-accent);
  color: #fff;
  font-size: 15px;
  font-weight: 500;
  cursor: pointer;
  transition: background-color 0.15s;
}
.btn-cta:hover {
  background: #1d4ed8;
}
.btn-cta:active {
  background: var(--c-accent-strong);
}
.btn-cta:disabled {
  opacity: 0.55;
  cursor: not-allowed;
}

/* ===== 页脚 ===== */
.footer {
  border-top: 1px solid var(--c-border);
  background: var(--c-bg);
}
.footer-inner {
  max-width: 1120px;
  margin: 0 auto;
  padding: 28px 24px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
}
.footer-brand {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  font-size: 14px;
  font-weight: 600;
  color: var(--c-text);
}
.footer-copy {
  font-size: 12px;
  color: var(--c-text-muted);
}

@media (max-width: 1023px) {
  .hero-inner {
    grid-template-columns: minmax(0, 1fr);
    gap: 40px;
  }
  .hero-mock {
    max-width: 560px;
    width: 100%;
  }
  .facts-inner {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
  .features-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}

@media (max-width: 767px) {
  .hero {
    padding: 40px 20px 56px;
  }
  .nav-inner {
    padding: 0 20px;
  }
  .hero-title {
    font-size: clamp(1.875rem, 9vw, 2.375rem);
  }
  .hero-actions {
    flex-direction: column;
    align-items: stretch;
  }
  .btn-primary,
  .btn-outline {
    width: 100%;
  }
  .facts-inner {
    padding: 20px;
    gap: 16px;
  }
  .steps-grid {
    grid-template-columns: minmax(0, 1fr);
  }
  .features-grid {
    grid-template-columns: minmax(0, 1fr);
  }
  .section {
    padding: 56px 20px;
  }
  .cta-inner {
    padding: 40px 20px;
    flex-direction: column;
    align-items: flex-start;
  }
  .btn-cta {
    width: 100%;
  }
  .footer-inner {
    flex-direction: column;
    align-items: flex-start;
    padding: 24px 20px;
  }
  .mock-compare {
    grid-template-columns: minmax(0, 1fr);
    gap: 8px;
  }
  .mock-arrow {
    justify-self: center;
    transform: rotate(90deg);
  }
}

@media (prefers-reduced-motion: reduce) {
  .btn-ghost,
  .btn-primary-sm,
  .btn-primary,
  .btn-outline,
  .btn-cta {
    transition: none;
  }

  .mock-panel,
  .step-icon {
    transition: none;
  }

  .hero-mock:hover .mock-panel,
  .step-card:hover .step-icon {
    transform: none;
  }
}

/* ===== 暗色：只重定义令牌，扁平选择器 ===== */
html.dark .landing {
  --c-bg: #0b1220;
  --c-surface: #111c2e;
  --c-surface-subtle: #172033;
  --c-border: rgba(148, 163, 184, 0.18);
  --c-border-strong: rgba(148, 163, 184, 0.34);
  --c-text: #e2e8f0;
  --c-text-secondary: #cbd5e1;
  --c-text-muted: #94a3b8;
  --c-accent: #93c5fd;
  --c-accent-strong: #60a5fa;
  --c-success: #6ee7b7;
  --c-success-bg: rgba(4, 120, 87, 0.16);
  --c-warning: #fbbf24;
  --c-warning-bg: rgba(180, 83, 9, 0.16);
  --c-ink: #0f172a;
  --c-on-ink: #f8fafc;
}

html.dark .nav {
  background: var(--c-bg);
}

html.dark .brand-icon {
  background: #2563eb;
}

html.dark .btn-primary-sm {
  background: #2563eb;
  color: #fff;
}

html.dark .btn-primary-sm:hover {
  background: #1d4ed8;
}

html.dark .btn-primary {
  background: #2563eb;
  color: #fff;
}

html.dark .btn-primary:hover {
  background: #1d4ed8;
}

html.dark .btn-outline:hover {
  background: #1e293b;
  border-color: rgba(148, 163, 184, 0.5);
}

html.dark .hero-title-accent {
  color: #93c5fd;
}

html.dark .mock-diff {
  border-color: rgba(251, 191, 36, 0.45);
}

html.dark .mock-value-changed {
  border-color: rgba(251, 191, 36, 0.45);
}

html.dark .mock-intact {
  border-color: rgba(110, 231, 183, 0.35);
}

html.dark .mock-sources {
  color: #93c5fd;
}

/* 暗色下 --c-ink 与页面底色几乎同色（约 1.05:1），CTA 带改用纸面 + 边框分层 */
html.dark .cta {
  background: var(--c-surface);
  border-top: 1px solid var(--c-border);
}

html.dark .cta-desc {
  color: #cbd5e1;
}

html.dark .btn-cta {
  background: #2563eb;
  color: #fff;
}

/* 悬停保持 #2563eb（对 #111c2e >3:1），用内描边给出状态，不再压暗到 2.6:1 */
html.dark .btn-cta:hover {
  background: #2563eb;
  box-shadow: inset 0 0 0 1px #bfdbfe;
}
</style>
