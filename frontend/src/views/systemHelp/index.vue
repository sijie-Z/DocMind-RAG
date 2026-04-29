<template>
  <div class="h-full overflow-y-auto p-6 lg:p-8 bg-gray-50 dark:bg-gray-950">
    <div class="max-w-5xl mx-auto space-y-6">
      <n-card class="rounded-2xl border-0 shadow-sm">
        <h1 class="text-2xl font-bold mb-2">系统帮助</h1>
        <p class="text-gray-500">快速了解系统核心功能和推荐使用流程。</p>
      </n-card>
      <n-card class="rounded-2xl border-0 shadow-sm">
        <h2 class="text-lg font-semibold mb-3">推荐使用流程</h2>
        <n-steps vertical :current="4">
          <n-step title="1. 上传文档" description="在知识库页面上传 PDF/Word/TXT 等文档，填写标题、标签和描述。" />
          <n-step title="2. 查看解析状态" description="等待文档从处理中变为已完成，失败文档可重试重建索引。" />
          <n-step title="3. 在聊天中提问" description="在智能对话页面提问，可附带文档并查看引用来源和片段。" />
          <n-step title="4. 维护资料" description="通过知识库列表查看详情、预览内容、整理标签并持续优化文档质量。" />
        </n-steps>
      </n-card>
      <n-card class="rounded-2xl border-0 shadow-sm">
        <h2 class="text-lg font-semibold mb-3">常见问题</h2>
        <n-collapse>
          <n-collapse-item title="为什么回答与文档不一致？" name="q1">
            请先确认提问是否明确，并检查文档中是否包含该问题的有效内容。可在知识库详情中查看文档预览与标签，减少无关内容干扰。
          </n-collapse-item>
          <n-collapse-item title="为什么文档状态一直处理中？" name="q2">
            可能是文档较大或解析服务繁忙。可稍后刷新，若状态失败可在知识库中点击重建索引。
          </n-collapse-item>
          <n-collapse-item title="游客账号为什么功能受限？" name="q3">
            游客账号用于体验基础能力，不包含组织架构等管理权限。请联系管理员分配对应权限。
          </n-collapse-item>
        </n-collapse>
      </n-card>
    </div>
  </div>
</template>

<script setup lang="ts">
</script>
