<template>
  <div class="min-h-screen bg-[#fafafa] dark:bg-gray-950 flex flex-col md:flex-row overflow-hidden transition-colors duration-300">
    <!-- 左侧：品牌区（取苏式园林项目的分屏品牌区构图：中央图形 + 品牌叙事 + 能力点） -->
    <div class="login-brand-panel hidden md:flex md:w-[46%] p-12 flex-col justify-between relative overflow-hidden login-animate-in">
      <div class="relative z-10 flex items-center gap-3">
        <div class="w-10 h-10 rounded-xl bg-white/10 ring-1 ring-white/15 flex items-center justify-center text-white">
          <DocMindMark :size="26" />
        </div>
        <span class="text-white text-xl font-bold tracking-tight">DocMind</span>
      </div>

      <div class="relative z-10 flex flex-col items-center text-center">
        <div class="login-halo relative mb-10 flex h-44 w-44 items-center justify-center">
          <span class="login-halo-ring absolute inset-0 rounded-full border border-white/25"></span>
          <span class="login-halo-ring absolute inset-5 rounded-full border border-white/30" style="animation-delay: 0.9s"></span>
          <span class="login-halo-ring absolute inset-10 rounded-full border border-white/35" style="animation-delay: 1.8s"></span>
          <div class="absolute h-28 w-28 rounded-full bg-sky-500/10 blur-2xl"></div>
          <div class="relative flex h-20 w-20 items-center justify-center rounded-2xl border border-white/20 bg-white/5 backdrop-blur text-sky-300">
            <DocMindMark :size="58" />
          </div>
        </div>

        <h1 class="text-4xl font-bold text-white leading-tight login-stagger" style="--stagger: 120ms">
          {{ t('login.brandHeadline') }}
        </h1>
        <p class="mt-4 max-w-sm text-base text-slate-400 leading-relaxed login-stagger" style="--stagger: 240ms">
          {{ t('login.brandSubtitle') }}
        </p>

        <ul class="mt-10 space-y-3 text-left login-stagger" style="--stagger: 360ms">
          <li
            v-for="feature in brandFeatures"
            :key="feature"
            class="flex items-center gap-3 text-sm text-slate-300"
          >
            <span class="flex h-6 w-6 shrink-0 items-center justify-center rounded-full border border-sky-400/30 bg-sky-400/10 text-sky-300">
              <svg viewBox="0 0 16 16" fill="none" class="h-3.5 w-3.5" aria-hidden="true">
                <path d="m3.5 8.5 3 3 6-7" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" />
              </svg>
            </span>
            {{ feature }}
          </li>
        </ul>
      </div>

      <!-- 装饰光晕 -->
      <div class="absolute -bottom-24 -right-20 w-72 h-72 bg-sky-500/10 rounded-full blur-3xl"></div>
      <div class="absolute top-1/4 -left-16 w-48 h-48 bg-blue-500/10 rounded-full blur-3xl"></div>
    </div>

    <!-- 右侧：表单（卡片式表单 + 顶部渐变条，取园林项目登录卡的构图） -->
    <div class="flex-1 flex items-center justify-center p-6 md:p-12 bg-[#fafafa] dark:bg-gray-950">
      <div class="w-full max-w-md">
        <div class="login-form-card relative overflow-hidden rounded-3xl border border-slate-200/70 bg-white/95 p-7 shadow-xl backdrop-blur-xl dark:border-slate-800 dark:bg-slate-900/90 md:p-10 login-card-animate">
          <div class="absolute inset-x-0 top-0 h-1 bg-gradient-to-r from-sky-500 via-blue-500 to-sky-400"></div>
          <div class="md:hidden text-center mb-8">
          <div class="w-14 h-14 rounded-2xl bg-blue-700 text-white ring-1 ring-blue-800/20 shadow-sm flex items-center justify-center mx-auto mb-4">
            <DocMindMark :size="31" />
          </div>
          <h1 class="text-2xl font-bold text-slate-900 dark:text-white">DocMind</h1>
          <p class="mt-2 text-xs text-slate-500 dark:text-slate-400">{{ t('login.brandHeadline') }}</p>
        </div>

        <div class="mb-8 text-center md:text-left">
          <h2 class="text-2xl font-bold text-slate-900 dark:text-white mb-2">
            {{ isRegister ? t('login.createAccount') : t('login.welcomeBack') }}
          </h2>
          <p class="text-slate-500 dark:text-slate-400 text-sm">
            {{ isRegister ? t('login.registerDesc') : t('login.loginDesc') }}
          </p>
        </div>

        <div class="space-y-5">
          <n-form
            ref="formRef"
            :model="form"
            :rules="rules"
            size="large"
            @submit.prevent="handleSubmit"
            label-placement="top"
          >
            <n-form-item path="username" :label="t('login.username')">
              <n-input
                v-model:value="form.username"
                :placeholder="t('login.usernamePlaceholder')"
                clearable
                class="login-input !rounded-xl"
              >
                <template #prefix>
                  <n-icon class="text-slate-400"><UserOutline /></n-icon>
                </template>
              </n-input>
            </n-form-item>

            <n-form-item v-if="isRegister" path="email" :label="t('login.email')">
              <n-input
                v-model:value="form.email"
                :placeholder="t('login.emailPlaceholder')"
                clearable
                class="login-input !rounded-xl"
              >
                <template #prefix>
                  <n-icon class="text-slate-400"><MailOutline /></n-icon>
                </template>
              </n-input>
            </n-form-item>

            <n-form-item path="password" :label="t('login.password')">
              <n-input
                v-model:value="form.password"
                type="password"
                :placeholder="t('login.passwordPlaceholder')"
                show-password-on="click"
                clearable
                class="login-input !rounded-xl"
              >
                <template #prefix>
                  <n-icon class="text-slate-400"><LockClosedOutline /></n-icon>
                </template>
              </n-input>
            </n-form-item>

            <div class="pt-2">
              <n-button
                type="primary"
                size="large"
                :loading="loading"
                :disabled="loading"
                block
                attr-type="submit"
                class="!rounded-xl !h-12 !font-semibold login-cta press focus-visible:ring-2 focus-visible:ring-blue-500 focus-visible:ring-offset-2 focus-visible:ring-offset-white dark:focus-visible:ring-offset-slate-900"
              >
                {{ isRegister ? t('login.register') : t('login.login') }}
              </n-button>
            </div>
          </n-form>

          <div class="text-center text-sm text-slate-500 dark:text-slate-400">
            {{ isRegister ? t('login.hasAccount') : t('login.noAccount') }}
            <button
              type="button"
              class="ml-1 inline-flex min-h-11 items-center border-0 bg-transparent px-1 font-semibold text-slate-800 hover:underline dark:text-slate-200"
              @click.prevent="toggleMode"
            >
              {{ isRegister ? t('login.goToLogin') : t('login.goToRegister') }}
            </button>
          </div>

          <div v-if="!isRegister && demoMode === 'true'" class="pt-6 border-t border-slate-200 dark:border-slate-800">
            <div class="rounded-xl bg-slate-100 dark:bg-slate-800/50 p-4 text-sm text-slate-600 dark:text-slate-400">
              <p class="font-medium text-slate-700 dark:text-slate-300 mb-1">
                {{ t('login.demoAccountsTitle') }}
              </p>
              <p>{{ demoAccounts.length ? t('login.demoAccountsHint') : t('login.demoDesc') }}</p>
              <div v-if="demoAccounts.length" class="mt-3 flex flex-col gap-2">
                <button
                  v-for="account in demoAccounts"
                  :key="account.username"
                  type="button"
                  class="group flex w-full items-center justify-between gap-3 rounded-xl border border-slate-200 bg-white px-3.5 py-2.5 text-left transition-all hover:-translate-y-px hover:border-blue-300 hover:shadow-md hover:shadow-blue-500/5 disabled:cursor-not-allowed disabled:opacity-60 dark:border-slate-700 dark:bg-slate-900/60 dark:hover:border-blue-500/50 dark:hover:bg-blue-500/10"
                  :disabled="loading"
                  :data-testid="`demo-login-${account.username}`"
                  @click="quickLogin(account)"
                >
                  <span class="min-w-0">
                    <span class="flex items-center gap-2">
                      <span class="truncate text-sm font-semibold text-slate-800 dark:text-slate-100">
                        {{ account.label }}
                      </span>
                      <span class="shrink-0 rounded-full border border-amber-200 bg-amber-50 px-1.5 py-0.5 text-[10px] font-medium text-amber-700 dark:border-amber-500/30 dark:bg-amber-500/10 dark:text-amber-300">
                        {{ t('login.demoTag') }}
                      </span>
                    </span>
                    <span class="mt-0.5 block truncate text-xs text-slate-500 dark:text-slate-400">
                      {{ account.description || account.username }}
                    </span>
                  </span>
                  <span class="shrink-0 text-xs font-medium text-blue-600 dark:text-blue-400">
                    {{ t('login.quickLogin') }} →
                  </span>
                </button>
              </div>
            </div>
          </div>
        </div>

        <!-- 页脚法务链接：DOM 顺序放在表单之后（Tab 先到主任务），移动端也可见 -->
        <div class="mt-6 text-center text-xs text-slate-500 dark:text-slate-400">
          &copy; DocMind ·
          <a :href="PRIVACY_URL" class="text-slate-500 hover:text-slate-700 hover:underline underline-offset-2 transition-colors dark:text-slate-400 dark:hover:text-slate-200">{{ t('login.privacy') }}</a>
          ·
          <a :href="TERMS_URL" class="text-slate-500 hover:text-slate-700 hover:underline underline-offset-2 transition-colors dark:text-slate-400 dark:hover:text-slate-200">{{ t('login.terms') }}</a>
        </div>
      </div>
    </div>
  </div>
  </div>
</template>

<script setup lang="ts">
import { ref, reactive, computed, watch, nextTick, onMounted } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import { useDedupedMessage } from '@/utils/message'
import { useI18n } from 'vue-i18n'
import { useUserStore } from '@/stores/user'
import { resolvePostAuthTarget } from '@/utils/postAuthRedirect'
import { parseDemoAccounts, type DemoAccount } from '@/utils/demoAccounts'
import DocMindMark from '@/components/brand/DocMindMark.vue'
import {
  PersonOutline as UserOutline,
  LockClosedOutline,
  MailOutline
} from '@vicons/ionicons5'

const router = useRouter()
const route = useRoute()
const message = useDedupedMessage()
const userStore = useUserStore()
const { t } = useI18n()
const demoMode = import.meta.env.VITE_DEMO_MODE
const demoAccounts = parseDemoAccounts(import.meta.env.VITE_DEMO_ACCOUNTS)
const brandFeatures = computed(() => [
  t('login.brandFeatureTrace'),
  t('login.brandFeatureEvidence'),
  t('login.brandFeatureDeterministic')
])

// T13：法务页面真链接 —— 占位路由；正式法务页上线后仅需在此替换为实际站内路径或外链常量
const PRIVACY_URL = '/privacy'
const TERMS_URL = '/terms'

const formRef = ref()
const loading = ref(false)
const isRegister = ref(route.name === 'Register')

const form = reactive({
  username: '',
  password: '',
  email: ''
})

// 注册后后端未直接发放 token 的兜底：带着 username + registered=1 回登录页时，
// 预填用户名并给出「注册成功」提示。/register → /login 复用同一组件实例，
// 故首挂载（onMounted）与后续路由切换（watch）两个时机都要处理。
// F 波修复：提示弹毕即从 URL 剥离 registered（router.replace，不新增历史条目），
// 否则用户每次刷新都会重复弹「注册成功」（跨会话实机两跑一致）。
const applyLoginQueryHints = () => {
  if (route.name !== 'Login') return
  const { username, registered } = route.query
  if (typeof username === 'string' && username) {
    form.username = username
  }
  if (registered === '1') {
    message.success(t('login.registerSuccess'))
    const query = { ...route.query }
    delete query.registered
    router.replace({ path: '/login', query })
  }
}
onMounted(applyLoginQueryHints)

watch(() => route.name, (newName) => {
  isRegister.value = newName === 'Register'
  if (newName === 'Login') applyLoginQueryHints()
})
const rules = computed(() => ({
  username: [
    { required: true, message: t('login.usernamePlaceholder'), trigger: 'input' },
    // 长度规则只属于注册：登录是"验证已有凭据"，不能用注册策略把老用户挡在门外。
    ...(isRegister.value
      ? [{ min: 3, message: t('validation.usernameLength'), trigger: 'blur' }]
      : [])
  ],
  password: [
    { required: true, message: t('login.passwordPlaceholder'), trigger: 'input' },
    ...(isRegister.value
      ? [{ min: 8, message: t('validation.passwordLength'), trigger: 'blur' }]
      : [])
  ],
  email: isRegister.value ? [
    { required: true, message: t('login.emailPlaceholder'), trigger: 'input' },
    { type: 'email', message: t('validation.emailInvalid'), trigger: 'blur' }
  ] : []
}))

const toggleMode = () => {
  router.push(isRegister.value ? '/login' : '/register')
  setTimeout(() => formRef.value?.restoreValidation(), 0)
}

const handleSubmit = async () => {
  try {
    await formRef.value?.validate()
    loading.value = true
    if (isRegister.value) {
      const res = await userStore.register({
        username: form.username,
        password: form.password,
        email: form.email
      })
      if (res.success) {
        if (res.tokenIssued) {
          // 后端已直接发放 token：视为注册即登录，按认证后落点规则跳转
          // （合法站内 redirect 优先，默认 /dashboard —— 此前硬跳 '/' 被丢回营销页）
          message.success(t('login.registerSuccess'))
          await nextTick()
          router.replace(resolvePostAuthTarget(route.query.redirect))
        } else {
          // 后端未直接发放 token：回登录页，预填用户名并提示
          await nextTick()
          router.replace({ path: '/login', query: { username: form.username, registered: '1' } })
        }
      } else {
        message.error(res.message || t('login.registerFailed'))
      }
    } else {
      const result = await userStore.login({
        username: form.username,
        password: form.password
      })
      if (result.success) {
        message.success(t('login.loginSuccess'))
        await nextTick()
        // 认证后落点统一走共享函数：合法站内 redirect（如守卫传入的 ?redirect=/chat）
        // 优先，其余（含 //evil.com、/\evil.com、/%09 等恶意变体）一律回落 /dashboard
        router.replace(resolvePostAuthTarget(route.query.redirect))
      } else {
        // 失败后清空密码：错误密码不应该继续留在输入框里
        form.password = ''
        message.error(result.message || t('login.loginFailed'))
      }
    }
  } catch {
    // 操作失败
  } finally {
    loading.value = false
  }
}

// 演示账号一键登录：填入配置的凭据并复用正式登录流程（含校验、错误提示与
// 登录后落点规则），不绕过任何认证逻辑。
const quickLogin = async (account: DemoAccount) => {
  if (loading.value || isRegister.value) return
  form.username = account.username
  form.password = account.password
  await handleSubmit()
}
</script>

<style scoped>
/* 品牌区：深墨蓝径向渐变 + 证据蓝顶光（保留 DocMind 蓝系，不照搬园林绿）。 */
.login-brand-panel {
  background:
    radial-gradient(circle at 78% 12%, rgba(37, 99, 235, 0.28) 0%, transparent 42%),
    radial-gradient(circle at 30% 42%, #10304f 0%, #0a1b30 48%, #030712 100%);
}

/* 细网格：给深墨蓝一点「证据纸」的结构感，而非纯色块 */
.login-brand-panel::before {
  content: '';
  position: absolute;
  inset: 0;
  background-image:
    linear-gradient(rgba(148, 163, 184, 0.07) 1px, transparent 1px),
    linear-gradient(90deg, rgba(148, 163, 184, 0.07) 1px, transparent 1px);
  background-size: 44px 44px;
  mask-image: radial-gradient(circle at 50% 40%, black 0%, transparent 78%);
  pointer-events: none;
}

/* 漂移光晕：慢速、低透明度，只作氛围不抢表单 */
.login-brand-panel::after {
  content: '';
  position: absolute;
  width: 420px;
  height: 420px;
  left: -120px;
  top: 52%;
  background: radial-gradient(circle, rgba(56, 189, 248, 0.16) 0%, transparent 65%);
  filter: blur(10px);
  animation: login-drift 16s ease-in-out infinite alternate;
  pointer-events: none;
}

@keyframes login-drift {
  from {
    transform: translate3d(0, 0, 0) scale(1);
  }
  to {
    transform: translate3d(140px, -80px, 0) scale(1.15);
  }
}

.login-halo-ring {
  animation: halo-pulse 7s ease-in-out infinite;
}

@keyframes halo-pulse {
  0%,
  100% {
    opacity: 0.35;
    transform: scale(1);
  }
  50% {
    opacity: 0.7;
    transform: scale(1.04);
  }
}

.login-form-card {
  box-shadow: 0 25px 50px -12px rgba(15, 23, 42, 0.18);
}

.login-input {
  /* Naive 在根节点内联写入 --n-height:40px（size=large），需 !important 覆盖。 */
  --n-height: 44px !important;
}

/* 主 CTA 回到动作蓝（此前是 slate-800 灰，与全站 #2563eb 动作色脱节） */
.login-cta {
  background: linear-gradient(135deg, #2563eb 0%, #1d4ed8 62%, #1e40af 100%);
  color: #ffffff;
  box-shadow: 0 10px 24px -10px rgba(37, 99, 235, 0.6);
  transition:
    box-shadow var(--dur-2) var(--ease-out),
    filter var(--dur-2) var(--ease-out),
    transform var(--dur-1) var(--ease-spring);
}

.login-cta:hover {
  filter: brightness(1.08);
  box-shadow: 0 14px 30px -10px rgba(37, 99, 235, 0.7);
}

.dark .login-cta {
  background: linear-gradient(135deg, #3b82f6 0%, #2563eb 62%, #1d4ed8 100%);
}

.login-input:focus-within {
  box-shadow: 0 0 0 3px var(--c-primary-soft);
  border-radius: 12px;
}

.login-stagger {
  animation: login-fade-in 0.7s var(--ease-out) both;
  animation-delay: var(--stagger, 0ms);
}

@media (prefers-reduced-motion: reduce) {
  .login-halo-ring {
    animation: none;
  }

  .login-brand-panel::after {
    animation: none;
  }

  .login-stagger {
    animation: none;
  }
}
</style>
