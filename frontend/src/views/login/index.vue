<template>
  <div class="min-h-screen bg-[#fafafa] dark:bg-gray-950 flex flex-col md:flex-row overflow-hidden transition-colors duration-300">
    <!-- 左侧：品牌区（取苏式园林项目的分屏品牌区构图：中央图形 + 品牌叙事 + 能力点） -->
    <div class="login-brand-panel hidden md:flex md:w-[46%] p-12 flex-col justify-between relative overflow-hidden login-animate-in">
      <div class="relative z-10 flex items-center gap-3">
        <div class="w-10 h-10 rounded-xl bg-white/10 ring-1 ring-white/15 flex items-center justify-center text-white font-bold text-sm">
          DM
        </div>
        <span class="text-white text-xl font-bold tracking-tight">DocMind</span>
      </div>

      <div class="relative z-10 flex flex-col items-center text-center">
        <div class="login-halo relative mb-10 flex h-44 w-44 items-center justify-center">
          <span class="login-halo-ring absolute inset-0 rounded-full border border-white/15"></span>
          <span class="login-halo-ring absolute inset-5 rounded-full border border-white/20" style="animation-delay: 0.9s"></span>
          <span class="login-halo-ring absolute inset-10 rounded-full border border-white/25" style="animation-delay: 1.8s"></span>
          <div class="absolute h-28 w-28 rounded-full bg-sky-500/10 blur-2xl"></div>
          <div class="relative flex h-20 w-20 items-center justify-center rounded-2xl border border-white/20 bg-white/5 backdrop-blur">
            <svg viewBox="0 0 48 48" fill="none" class="h-10 w-10 text-sky-300" aria-hidden="true">
              <path d="M14 6h14l8 8v24a4 4 0 0 1-4 4H14a4 4 0 0 1-4-4V10a4 4 0 0 1 4-4Z" stroke="currentColor" stroke-width="1.8" stroke-linejoin="round" />
              <path d="M28 6v8h8" stroke="currentColor" stroke-width="1.8" stroke-linejoin="round" />
              <path d="M16 24h10M16 31h7" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" />
              <circle cx="34" cy="34" r="7" fill="currentColor" fill-opacity="0.12" stroke="currentColor" stroke-width="1.8" />
              <path d="m31 34 2.2 2.2L37 32.5" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" />
            </svg>
          </div>
        </div>

        <h1 class="text-4xl font-bold text-white leading-tight">
          {{ t('login.brandHeadline') }}
        </h1>
        <p class="mt-4 max-w-sm text-base text-slate-400 leading-relaxed">
          {{ t('login.brandSubtitle') }}
        </p>

        <ul class="mt-10 space-y-3 text-left">
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
          <div class="w-14 h-14 rounded-2xl bg-slate-800 dark:bg-slate-700 text-white font-bold text-lg flex items-center justify-center mx-auto mb-4">
            DM
          </div>
          <h1 class="text-2xl font-bold text-slate-900 dark:text-white">DocMind</h1>
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
                class="!rounded-xl !h-12 !font-semibold bg-slate-800 hover:bg-slate-700 focus-visible:ring-2 focus-visible:ring-blue-500 focus-visible:ring-offset-2 focus-visible:ring-offset-white dark:bg-slate-100 dark:text-slate-900 dark:hover:bg-white dark:focus-visible:ring-offset-slate-900 btn-bounce"
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
      ? [{ min: 3, message: t('validation.usernameLength'), trigger: 'input' }]
      : [])
  ],
  password: [
    { required: true, message: t('login.passwordPlaceholder'), trigger: 'input' },
    ...(isRegister.value
      ? [{ min: 8, message: t('validation.passwordLength'), trigger: 'input' }]
      : [])
  ],
  email: isRegister.value ? [
    { required: true, message: t('login.emailPlaceholder'), trigger: 'input' },
    { type: 'email', message: t('validation.emailInvalid'), trigger: 'input' }
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
/* 品牌区：深墨蓝径向渐变（保留 DocMind 蓝系，不照搬园林绿）。 */
.login-brand-panel {
  background: radial-gradient(circle at 30% 42%, #10304f 0%, #0a1b30 48%, #030712 100%);
}

.login-halo-ring {
  animation: halo-pulse 7s ease-in-out infinite;
}

@keyframes halo-pulse {
  0%,
  100% {
    opacity: 0.25;
    transform: scale(1);
  }
  50% {
    opacity: 0.55;
    transform: scale(1.04);
  }
}

.login-form-card {
  box-shadow: 0 25px 50px -12px rgba(15, 23, 42, 0.18);
}

.login-input {
  --n-height: 44px;
}

@media (prefers-reduced-motion: reduce) {
  .login-halo-ring {
    animation: none;
  }
}
</style>
