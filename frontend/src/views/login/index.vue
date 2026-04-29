<template>
  <div class="min-h-screen bg-[#fafafa] dark:bg-[#0a0a0a] flex flex-col md:flex-row overflow-hidden transition-colors duration-300">
    <!-- 左侧：品牌区（无刺眼色） -->
    <div class="hidden md:flex md:w-[44%] bg-slate-900 dark:bg-slate-950 p-12 flex-col justify-between relative overflow-hidden">
      <div class="relative z-10 flex items-center gap-3">
        <div class="w-10 h-10 rounded-xl bg-slate-700 flex items-center justify-center text-white font-bold text-sm">
          DM
        </div>
        <span class="text-white text-xl font-bold tracking-tight">DocMind</span>
      </div>
      <div class="relative z-10 space-y-6 max-w-sm">
        <h1 class="text-4xl font-bold text-white leading-tight">
          {{ isRegister ? '加入 DocMind' : '欢迎回来' }}
        </h1>
        <p class="text-slate-400 text-base leading-relaxed">
          企业级 RAG 知识库，上传文档即可开始智能问答。
        </p>
      </div>
      <div class="relative z-10 text-slate-500 text-sm">
        &copy; DocMind · 隐私 · 服务条款
      </div>
    </div>

    <!-- 右侧：表单 -->
    <div class="flex-1 flex items-center justify-center p-6 md:p-12 bg-[#fafafa] dark:bg-[#0a0a0a]">
      <div class="max-w-md w-full">
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
                class="!rounded-xl !h-12 !font-semibold bg-slate-800 hover:bg-slate-700 dark:bg-slate-100 dark:text-slate-900 dark:hover:bg-white"
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

          <div v-if="!isRegister" class="pt-6 border-t border-slate-200 dark:border-slate-800">
            <div class="rounded-xl bg-slate-100 dark:bg-slate-800/50 p-4 text-sm text-slate-600 dark:text-slate-400">
              <p class="font-medium text-slate-700 dark:text-slate-300 mb-1">演示账号</p>
              <p>{{ t('login.demoDesc', { user: 'guest', pass: '123456' }) }}</p>
            </div>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, reactive, computed, watch, nextTick } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import { useDedupedMessage } from '@/utils/message'
import { useI18n } from 'vue-i18n'
import { useUserStore } from '@/stores/user'
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

const formRef = ref()
const loading = ref(false)
const isRegister = ref(route.name === 'Register')

watch(() => route.name, (newName) => {
  isRegister.value = newName === 'Register'
})

const form = reactive({
  username: '',
  password: '',
  email: ''
})

const rules = computed(() => ({
  username: [
    { required: true, message: t('login.usernamePlaceholder'), trigger: 'blur' },
    { min: 3, message: t('validation.usernameLength'), trigger: 'blur' }
  ],
  password: [
    { required: true, message: t('login.passwordPlaceholder'), trigger: 'blur' },
    { min: 6, message: t('validation.passwordLength'), trigger: 'blur' }
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
        message.success(t('login.registerSuccess'))
        await nextTick()
        router.push('/')
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
        router.push('/dashboard')
      } else {
        message.error(result.message || t('login.loginFailed'))
      }
    }
  } catch (error: any) {
    console.error('操作失败:', error)
  } finally {
    loading.value = false
  }
}
</script>
