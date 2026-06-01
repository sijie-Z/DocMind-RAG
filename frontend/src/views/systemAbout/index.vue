<template>
  <div class="h-full overflow-y-auto p-6 lg:p-8 bg-gray-50 dark:bg-gray-950">
    <div class="max-w-4xl mx-auto space-y-6">
      <!-- Header -->
      <div class="flex items-center gap-4 mb-2">
        <div class="w-12 h-12 rounded-2xl bg-emerald-500 flex items-center justify-center text-white font-bold text-xl shadow-lg shadow-emerald-500/25">D</div>
        <div>
          <h1 class="text-2xl font-bold text-gray-900 dark:text-white">DocMind</h1>
          <p class="text-sm text-gray-500 dark:text-gray-400">企业级 RAG 知识库系统</p>
        </div>
        <div class="ml-auto">
          <n-tag type="success" round size="small">v2.0.0</n-tag>
        </div>
      </div>

      <!-- System Info -->
      <div class="bg-white dark:bg-gray-800 rounded-2xl border border-gray-100 dark:border-gray-700 shadow-sm overflow-hidden">
        <div class="px-6 py-4 border-b border-gray-100 dark:border-gray-700">
          <h2 class="text-base font-semibold text-gray-900 dark:text-white">系统信息</h2>
        </div>
        <div class="p-6">
          <n-descriptions :column="1" bordered label-placement="left" class="rounded-xl overflow-hidden">
            <n-descriptions-item label="产品">DocMind — 企业级 RAG 知识库</n-descriptions-item>
            <n-descriptions-item label="架构">RAG (检索增强生成) + Agent 自主推理</n-descriptions-item>
            <n-descriptions-item label="后端">FastAPI + SQLAlchemy(异步) + MySQL 8 + Redis + Elasticsearch 8 + Kafka + MinIO</n-descriptions-item>
            <n-descriptions-item label="前端">Vue 3 + TypeScript + Vite + Naive UI + Pinia</n-descriptions-item>
            <n-descriptions-item label="AI 引擎">DeepSeek API + OpenAI 兼容 Embedding + LangChain</n-descriptions-item>
            <n-descriptions-item label="Agent 系统">PER 三阶段（规划→执行→反思）+ 工具注册 + 上下文引擎 + 技能学习</n-descriptions-item>
            <n-descriptions-item label="安全机制">JWT + RBAC + 组织级多租户隔离</n-descriptions-item>
          </n-descriptions>
        </div>
      </div>

      <!-- Architecture Highlights -->
      <div class="bg-white dark:bg-gray-800 rounded-2xl border border-gray-100 dark:border-gray-700 shadow-sm overflow-hidden">
        <div class="px-6 py-4 border-b border-gray-100 dark:border-gray-700">
          <h2 class="text-base font-semibold text-gray-900 dark:text-white">架构亮点</h2>
        </div>
        <div class="p-6 grid grid-cols-1 md:grid-cols-2 gap-4">
          <div v-for="item in highlights" :key="item.title" class="p-4 rounded-xl bg-gray-50 dark:bg-gray-700/30">
            <h3 class="text-sm font-semibold text-gray-900 dark:text-white mb-2">{{ item.title }}</h3>
            <p class="text-xs text-gray-500 dark:text-gray-400 leading-relaxed">{{ item.desc }}</p>
          </div>
        </div>
      </div>

      <!-- Data Flow -->
      <div class="bg-white dark:bg-gray-800 rounded-2xl border border-gray-100 dark:border-gray-700 shadow-sm overflow-hidden">
        <div class="px-6 py-4 border-b border-gray-100 dark:border-gray-700">
          <h2 class="text-base font-semibold text-gray-900 dark:text-white">数据流</h2>
        </div>
        <div class="p-6">
          <div class="flex flex-wrap items-center gap-2 text-sm">
            <span v-for="(step, i) in dataFlow" :key="i" class="flex items-center gap-2">
              <span class="px-3 py-1.5 rounded-lg bg-emerald-50 dark:bg-emerald-900/20 text-emerald-700 dark:text-emerald-300 font-medium text-xs">{{ step }}</span>
              <span v-if="i < dataFlow.length - 1" class="text-gray-300 dark:text-gray-600">&rarr;</span>
            </span>
          </div>
        </div>
      </div>

      <!-- Footer -->
      <div class="text-center py-4">
        <p class="text-xs text-gray-400 dark:text-gray-500">
          基于 FastAPI + Vue 3 + Elasticsearch + DeepSeek LLM 构建
        </p>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { NDescriptions, NDescriptionsItem, NTag } from 'naive-ui'

const highlights = [
  { title: '混合检索', desc: '结合 BM25 关键词搜索与向量语义搜索，使用倒数排名融合 (RRF) 最大化召回率。' },
  { title: 'Agent 架构', desc: '自研 PER 三阶段架构（Plan-Execute-Reflect）。Planner 拆解 DAG、Executor 调度工具、Reflector 检查纠错。' },
  { title: '语义缓存', desc: '基于 Embedding 的答案缓存，消除相似问题的重复 LLM 调用，降低延迟和成本。' },
  { title: '熔断机制', desc: '外部服务（LLM、ES、DB）的容错保护，自动降级防止级联故障。' },
  { title: '多租户 RBAC', desc: '组织级数据隔离 + 基于角色的访问控制，支持细粒度权限管理。' },
  { title: '实时流式', desc: 'WebSocket + SSE 双模式流式响应，实时通知推送。' },
]

const dataFlow = [
  '上传',
  'MinIO + DB',
  'Kafka',
  'Worker',
  '解析分块',
  '向量化',
  'Elasticsearch',
  '用户提问',
  '混合搜索',
  '重排序',
  'DeepSeek LLM',
  '流式响应',
]
</script>
