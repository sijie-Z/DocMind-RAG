<template>
  <div class="h-full overflow-y-auto p-6 lg:p-8 bg-gray-50 dark:bg-gray-950">
    <div class="max-w-5xl mx-auto space-y-6">
      <n-card class="rounded-2xl border-0 shadow-sm">
        <h1 class="text-2xl font-bold mb-2">系统关于</h1>
        <p class="text-gray-500">DocMind 企业知识助手</p>
      </n-card>
      <n-card class="rounded-2xl border-0 shadow-sm">
        <n-descriptions :column="1" bordered label-placement="left">
          <n-descriptions-item label="产品定位">面向团队知识管理与检索问答的一体化系统</n-descriptions-item>
          <n-descriptions-item label="核心能力">文档解析、知识库索引、RAG 检索、对话问答、权限管理</n-descriptions-item>
          <n-descriptions-item label="技术架构">前端 + 后端 API + 数据库 + 检索引擎</n-descriptions-item>
          <n-descriptions-item label="开发目的">提升文档可用性，减少信息查找成本</n-descriptions-item>
          <n-descriptions-item label="开发周期">持续迭代中</n-descriptions-item>
        </n-descriptions>
      </n-card>
      <n-card class="rounded-2xl border-0 shadow-sm">
        <h2 class="text-lg font-semibold mb-3">团队与致谢</h2>
        <div class="space-y-2 text-gray-700 dark:text-gray-200">
          <p>产品设计：DocMind 产品团队</p>
          <p>系统研发：前后端与平台工程团队</p>
          <p>特别致谢：系统使用者的反馈与建议</p>
        </div>
      </n-card>
    </div>
  </div>
</template>

<script setup lang="ts">
</script>
