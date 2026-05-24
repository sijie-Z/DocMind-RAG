<template>
  <div class="h-full overflow-y-auto p-6 lg:p-8 bg-gray-50 dark:bg-gray-950">
    <div class="max-w-4xl mx-auto space-y-6">
      <!-- Header -->
      <div class="flex items-center gap-4 mb-2">
        <div class="w-12 h-12 rounded-2xl bg-emerald-500/10 flex items-center justify-center">
          <n-icon size="28" class="text-emerald-500"><HelpCircleOutline /></n-icon>
        </div>
        <div>
          <h1 class="text-2xl font-bold text-gray-900 dark:text-white">帮助中心</h1>
          <p class="text-sm text-gray-500 dark:text-gray-400">快速上手 DocMind 所需的一切</p>
        </div>
      </div>

      <!-- Quick Start -->
      <div class="bg-white dark:bg-gray-800 rounded-2xl border border-gray-100 dark:border-gray-700 shadow-sm overflow-hidden">
        <div class="px-6 py-4 border-b border-gray-100 dark:border-gray-700 bg-gradient-to-r from-emerald-50 to-transparent dark:from-emerald-900/10">
          <h2 class="text-base font-semibold text-gray-900 dark:text-white flex items-center gap-2">
            <n-icon class="text-emerald-500"><RocketOutline /></n-icon>
            快速入门指南
          </h2>
        </div>
        <div class="p-6">
          <div class="grid grid-cols-1 md:grid-cols-2 gap-4">
            <div v-for="(step, i) in quickStart" :key="i" class="flex gap-4 p-4 rounded-xl bg-gray-50 dark:bg-gray-700/30 hover:bg-emerald-50 dark:hover:bg-emerald-900/10 transition-colors">
              <div class="flex-shrink-0 w-10 h-10 rounded-xl bg-emerald-500/10 flex items-center justify-center">
                <span class="text-lg font-bold text-emerald-600 dark:text-emerald-400">{{ i + 1 }}</span>
              </div>
              <div>
                <h3 class="text-sm font-semibold text-gray-900 dark:text-white mb-1">{{ step.title }}</h3>
                <p class="text-xs text-gray-500 dark:text-gray-400 leading-relaxed">{{ step.desc }}</p>
              </div>
            </div>
          </div>
        </div>
      </div>

      <!-- Features -->
      <div class="bg-white dark:bg-gray-800 rounded-2xl border border-gray-100 dark:border-gray-700 shadow-sm overflow-hidden">
        <div class="px-6 py-4 border-b border-gray-100 dark:border-gray-700">
          <h2 class="text-base font-semibold text-gray-900 dark:text-white flex items-center gap-2">
            <n-icon class="text-blue-500"><GridOutline /></n-icon>
            核心功能
          </h2>
        </div>
        <div class="p-6 grid grid-cols-1 md:grid-cols-2 gap-4">
          <div v-for="feature in features" :key="feature.title" class="flex gap-3 p-3 rounded-xl border border-gray-100 dark:border-gray-700">
            <div class="w-9 h-9 rounded-lg flex items-center justify-center flex-shrink-0" :class="feature.bg">
              <n-icon size="18" class="text-gray-600 dark:text-gray-300">
                <component :is="feature.icon === 'SearchOutline' ? SearchOutline : feature.icon === 'HardwareChipOutline' ? HardwareChipOutline : feature.icon === 'LinkOutline' ? LinkOutline : DocumentsOutline" />
              </n-icon>
            </div>
            <div>
              <h3 class="text-sm font-semibold text-gray-900 dark:text-white">{{ feature.title }}</h3>
              <p class="text-xs text-gray-500 dark:text-gray-400 mt-0.5">{{ feature.desc }}</p>
            </div>
          </div>
        </div>
      </div>

      <!-- FAQ -->
      <div class="bg-white dark:bg-gray-800 rounded-2xl border border-gray-100 dark:border-gray-700 shadow-sm overflow-hidden">
        <div class="px-6 py-4 border-b border-gray-100 dark:border-gray-700">
          <h2 class="text-base font-semibold text-gray-900 dark:text-white flex items-center gap-2">
            <n-icon class="text-blue-500"><ChatbubblesOutline /></n-icon>
            FAQ
          </h2>
        </div>
        <div class="p-6">
          <n-collapse>
            <n-collapse-item v-for="faq in faqs" :key="faq.name" :title="faq.q" :name="faq.name">
              <p class="text-sm text-gray-600 dark:text-gray-300 leading-relaxed">{{ faq.a }}</p>
            </n-collapse-item>
          </n-collapse>
        </div>
      </div>

      <!-- Keyboard Shortcuts -->
      <div class="bg-white dark:bg-gray-800 rounded-2xl border border-gray-100 dark:border-gray-700 shadow-sm overflow-hidden">
        <div class="px-6 py-4 border-b border-gray-100 dark:border-gray-700">
          <h2 class="text-base font-semibold text-gray-900 dark:text-white flex items-center gap-2">
            <n-icon class="text-orange-500"><KeyOutline /></n-icon>
            快捷键
          </h2>
        </div>
        <div class="p-6 grid grid-cols-1 md:grid-cols-2 gap-3">
          <div v-for="shortcut in shortcuts" :key="shortcut.key" class="flex items-center justify-between p-3 rounded-lg bg-gray-50 dark:bg-gray-700/30">
            <span class="text-sm text-gray-700 dark:text-gray-300">{{ shortcut.desc }}</span>
            <kbd class="px-2 py-1 text-xs font-mono bg-gray-200 dark:bg-gray-600 rounded text-gray-700 dark:text-gray-200">{{ shortcut.key }}</kbd>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { NCollapse, NCollapseItem, NIcon } from 'naive-ui'
import {
  HelpCircleOutline, RocketOutline, GridOutline, ChatbubblesOutline, KeyOutline,
  SearchOutline, HardwareChipOutline, LinkOutline, DocumentsOutline
} from '@vicons/ionicons5'

const quickStart = [
  { title: '上传文档', desc: '前往知识库上传 PDF、Word、Excel 或文本文件，系统会自动解析并建立索引。' },
  { title: '提问', desc: '打开智能对话，用自然语言提问。系统会搜索您的知识库并生成带引用的答案。' },
  { title: '使用 Agent 模式', desc: '复杂问题请使用 Agent 模式。它会逐步推理、调用多个工具，组织全面的答案。' },
  { title: '检查与改进', desc: '查看来源引用，给答案反馈，上传更多文档以提升知识覆盖范围。' },
]

const features = [
  { icon: 'SearchOutline', bg: 'bg-emerald-50 dark:bg-emerald-900/30', title: '混合搜索', desc: '关键词 + 向量搜索 + RRF 融合，最大化召回精度。' },
  { icon: 'HardwareChipOutline', bg: 'bg-blue-50 dark:bg-blue-900/30', title: 'Agent 推理', desc: '多步工具调用循环，规划、搜索、验证答案。' },
  { icon: 'LinkOutline', bg: 'bg-blue-50 dark:bg-blue-900/30', title: '来源引用', desc: '每个答案都包含 [n] 引用标记，链接回源文档。' },
  { icon: 'DocumentsOutline', bg: 'bg-orange-50 dark:bg-orange-900/30', title: '多格式支持', desc: '支持 PDF、Word、Excel、PowerPoint、纯文本等格式。' },
]

const faqs = [
  { name: 'q1', q: '为什么答案和我的文档不匹配？', a: '请确保问题足够具体且文档包含相关内容。检查知识库页面确认文档状态为"已索引"。尝试用文档中的关键词重新组织问题。' },
  { name: 'q2', q: '为什么文档一直处于"解析中"状态？', a: '大文件解析可能需要更长时间，请等待几分钟后刷新。如果状态显示"失败"，请在知识库中点击"重试向量化"按钮。' },
  { name: 'q3', q: 'Agent 模式和普通对话有什么区别？', a: 'Agent 模式使用自主推理循环+工具调用。它可以多次搜索、分析文档、逐步组织答案。普通对话只做单次检索+生成。' },
  { name: 'q4', q: '演示账号如何使用？', a: '演示账号 (guest / 123456) 可使用对话、知识库、Agent 等全部功能。用户管理、审计日志等管理功能需要管理员账号。' },
]

const shortcuts = [
  { key: 'Ctrl+K', desc: '聚焦全局搜索' },
  { key: 'Enter', desc: '发送消息' },
  { key: 'Shift+Enter', desc: '换行' },
]
</script>
