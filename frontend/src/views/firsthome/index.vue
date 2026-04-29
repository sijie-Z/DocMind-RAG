<template>
  <div class="first-home min-h-screen bg-[#f7f8fc] dark:bg-[#06070b] transition-colors duration-300 px-6 py-10 md:px-10">
    <div class="mx-auto w-full max-w-6xl space-y-12">
      <section class="rounded-3xl border border-slate-200/70 dark:border-slate-800 bg-white/90 dark:bg-slate-900/70 px-7 py-10 md:px-10 md:py-12 shadow-sm">
        <div class="flex flex-col gap-10 md:flex-row md:items-center md:justify-between">
          <div class="max-w-2xl">
            <div class="inline-flex items-center gap-2 rounded-full border border-slate-200 dark:border-slate-700 px-3 py-1 text-xs text-slate-500 dark:text-slate-300 mb-5">
              <span class="w-2 h-2 rounded-full bg-emerald-500"></span>
              Agent Workflow Ready
            </div>
            <div class="inline-flex items-center justify-center w-14 h-14 rounded-2xl bg-slate-900 dark:bg-slate-100 text-white dark:text-slate-900 font-bold text-lg tracking-tight shadow-lg mb-6">
              DM
            </div>
            <h1 class="text-3xl md:text-5xl font-bold text-slate-900 dark:text-white tracking-tight mb-4 leading-tight">
              DocMind 智能工作台
            </h1>
            <p class="text-slate-600 dark:text-slate-300 text-base md:text-lg leading-relaxed">
              以 Agent 为核心，把“目标拆解 → 检索 → 推理 → 输出 → 追踪”串成完整闭环，
              除核心 RAG 外，同步优化个人中心、操作体验与可观测能力。
            </p>
            <div class="mt-8 flex flex-wrap gap-3">
              <button
                @click="goToLogin"
                class="px-7 py-3 rounded-xl bg-slate-900 dark:bg-white text-white dark:text-slate-900 font-semibold shadow-lg hover:opacity-90 transition-opacity"
              >
                立即登录体验
              </button>
              <button
                @click="goToRegister"
                class="px-7 py-3 rounded-xl border border-slate-300 dark:border-slate-600 text-slate-700 dark:text-slate-200 font-semibold hover:bg-slate-50 dark:hover:bg-slate-800/70 transition-colors"
              >
                创建账号
              </button>
            </div>
          </div>
          <div class="w-full md:w-[320px] rounded-2xl border border-slate-200 dark:border-slate-800 bg-slate-50 dark:bg-slate-900 px-5 py-5 space-y-4">
            <h3 class="text-sm font-semibold text-slate-900 dark:text-slate-100">体验升级重点</h3>
            <div v-for="item in upgradeHighlights" :key="item.title" class="rounded-xl bg-white dark:bg-slate-800 border border-slate-200/80 dark:border-slate-700 px-4 py-3">
              <div class="text-sm font-semibold text-slate-800 dark:text-slate-100">{{ item.title }}</div>
              <div class="text-xs text-slate-500 dark:text-slate-400 mt-1">{{ item.desc }}</div>
            </div>
          </div>
        </div>
      </section>

      <section class="rounded-3xl border border-slate-200/70 dark:border-slate-800 bg-white dark:bg-slate-900 px-7 py-8 md:px-10">
        <div class="flex items-center justify-between gap-4 mb-6">
          <h2 class="text-xl font-bold text-slate-900 dark:text-white">Agent Workflow</h2>
          <span class="text-xs text-slate-500 dark:text-slate-400">参考主流 Agent 产品的分层编排体验</span>
        </div>
        <div class="grid grid-cols-1 md:grid-cols-5 gap-3">
          <div
            v-for="(step, index) in workflowSteps"
            :key="step.title"
            class="relative rounded-2xl border border-slate-200 dark:border-slate-700 px-4 py-4 bg-slate-50/70 dark:bg-slate-800/70"
          >
            <div class="text-[11px] text-slate-400 mb-2">STEP {{ String(index + 1).padStart(2, '0') }}</div>
            <h3 class="text-sm font-semibold text-slate-900 dark:text-slate-100 mb-1">{{ step.title }}</h3>
            <p class="text-xs text-slate-500 dark:text-slate-400 leading-relaxed">{{ step.desc }}</p>
          </div>
        </div>
      </section>

      <section class="grid grid-cols-1 md:grid-cols-3 gap-4">
        <div v-for="card in capabilityCards" :key="card.title" class="rounded-2xl bg-white dark:bg-slate-900 border border-slate-200/80 dark:border-slate-800 px-5 py-5">
          <h3 class="font-semibold text-slate-900 dark:text-white mb-2">{{ card.title }}</h3>
          <p class="text-sm text-slate-500 dark:text-slate-400 leading-relaxed">{{ card.desc }}</p>
        </div>
      </section>
    </div>
  </div>
</template>

<script setup lang="ts">
import { useRouter } from 'vue-router'

const router = useRouter()

const goToLogin = () => router.push('/login')
const goToRegister = () => router.push('/register')

const upgradeHighlights = [
  { title: '个人中心增强', desc: '新增快捷操作、产能视图与会话安全管理。' },
  { title: '工作流可视化', desc: '将 Agent 过程拆分为可理解、可追踪的阶段。' },
  { title: '操作路径收敛', desc: '从首页到核心页面的跳转更直观，学习成本更低。' }
]

const workflowSteps = [
  { title: '任务建模', desc: '解析用户意图，形成可执行目标与约束。' },
  { title: '上下文检索', desc: '组合历史、文档与规则，获得可用证据。' },
  { title: '策略推理', desc: '决定调用顺序、行动路径与风险控制。' },
  { title: '结果编排', desc: '产出答案、引用与后续行动建议。' },
  { title: '反馈追踪', desc: '记录请求链路并持续优化下一轮表现。' }
]

const capabilityCards = [
  { title: '统一工作台', desc: '聊天、知识库、提示词、搜索与个人配置在同一体验层组织。' },
  { title: '企业安全导向', desc: '账号安全、设备会话与操作审计形成基础安全护栏。' },
  { title: '可扩展架构', desc: '支持后续接入更多 Agent 工具能力与自动化流程节点。' }
]
</script>

<style scoped>
.first-home {
  font-family: system-ui, -apple-system, 'Segoe UI', sans-serif;
}
</style>
