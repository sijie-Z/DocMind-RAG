<template>
  <div class="min-h-screen bg-[#fafafa] dark:bg-gray-950 flex flex-col md:flex-row overflow-hidden transition-colors duration-300">
    <!-- 左侧：品牌区 -->
    <div class="hidden md:flex md:w-[44%] bg-slate-900 dark:bg-slate-950 p-12 flex-col justify-between relative overflow-hidden login-animate-in">
      <div class="relative z-10 flex items-center gap-3">
        <div class="w-10 h-10 rounded-xl bg-slate-700 flex items-center justify-center text-white font-bold text-sm">
          DM
        </div>
        <span class="text-white text-xl font-bold tracking-tight">DocMind</span>
      </div>
      <div class="relative z-10 space-y-6 max-w-sm">
        <h1 class="text-4xl font-bold text-white leading-tight">
          {{ t('login.brandHeadline') }}
        </h1>
        <p class="text-slate-400 text-base leading-relaxed">
          {{ t('login.brandSubtitle') }}
        </p>
      </div>
      <div class="relative z-10 text-slate-500 text-sm">
        &copy; DocMind ·
        <a :href="PRIVACY_URL" class="hover:text-slate-300 hover:underline underline-offset-2 transition-colors">{{ t('login.privacy') }}</a>
        ·
        <a :href="TERMS_URL" class="hover:text-slate-300 hover:underline underline-offset-2 transition-colors">{{ t('login.terms') }}</a>
      </div>
      <!-- 装饰光晕 -->
      <div class="absolute -bottom-20 -right-20 w-64 h-64 bg-blue-500/10 rounded-full blur-3xl"></div>
      <div class="absolute top-1/3 -left-10 w-40 h-40 bg-indigo-500/10 rounded-full blur-3xl"></div>
    </div>

    <!-- 右侧：表单 -->
    <div class="flex-1 flex items-center justify-center p-6 md:p-12 bg-[#fafafa] dark:bg-gray-950">
      <div class="max-w-md w-full login-card-animate">
        <div class="md:hidden text-center mb-8">
          <div class="w-14 h-14 rounded-2xl bg-slate-800 dark:bg-slate-700 text-white font-bold text-lg flex items-center justify-center mx-auto mb-4">
            DM
          </div>
          <h2 class="text-2xl font-bold text-slate-900 dark:text-white">DocMind</h2>
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
                class="!rounded-xl"
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
                class="!rounded-xl"
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
                class="!rounded-xl"
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
                class="!rounded-xl !h-12 !font-semibold bg-slate-800 hover:bg-slate-700 dark:bg-slate-100 dark:text-slate-900 dark:hover:bg-white btn-bounce"
              >
                {{ isRegister ? t('login.register') : t('login.login') }}
              </n-button>
            </div>
          </n-form>

          <div class="text-center text-sm text-slate-500 dark:text-slate-400">
            {{ isRegister ? t('login.hasAccount') : t('login.noAccount') }}
            <button
              class="text-slate-800 dark:text-slate-200 font-semibold ml-1 hover:underline"
              @click.prevent="toggleMode"
            >
              {{ isRegister ? t('login.goToLogin') : t('login.goToRegister') }}
            </button>
          </div>

          <div v-if="!isRegister && demoMode === 'true'" class="pt-6 border-t border-slate-200 dark:border-slate-800">
            <div class="rounded-xl bg-slate-100 dark:bg-slate-800/50 p-4 text-sm text-slate-600 dark:text-slate-400">
              <p class="font-medium text-slate-700 dark:text-slate-300 mb-1">演示账号</p>
              <p>{{ t('login.demoDesc') }}</p>
            </div>
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
    { required: true, message: t('login.usernamePlaceholder'), trigger: 'blur' },
    { min: 3, message: t('validation.usernameLength'), trigger: 'blur' }
  ],
  password: [
    { required: true, message: t('login.passwordPlaceholder'), trigger: 'blur' },
    { min: 8, message: t('validation.passwordLength'), trigger: 'blur' }
  ],
  email: isRegister.value ? [
    { required: true, message: t('login.emailPlaceholder'), trigger: 'blur' },
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
        message.error(result.message || t('login.loginFailed'))
      }
    }
  } catch {
    // 操作失败
  } finally {
    loading.value = false
  }
}
</script>
