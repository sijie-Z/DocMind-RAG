<template>
  <!-- 全局文档预览 Modal -->
  <n-modal
    v-model:show="showPreviewModal"
    preset="card"
    :title="previewDoc?.filename || '文档预览'"
    class="max-w-4xl w-[90vw]"
    style="height: 80vh"
    :segmented="{ content: true, footer: true }"
  >
    <div class="h-full flex flex-col overflow-hidden">
      <!-- 文档元数据/简介区域 -->
      <div class="bg-gray-50 dark:bg-gray-900/50 p-4 rounded-xl mb-4 border border-gray-100 dark:border-gray-800 flex flex-col gap-3">
        <div class="flex items-center justify-between">
          <div class="flex items-center gap-2">
            <n-tag size="small" :type="previewDoc?.source === '聊天上传' ? 'info' : 'success'" round>
              {{ previewDoc?.source || '知识库上传' }}
            </n-tag>
            <n-tag v-if="previewDoc?.file_size" size="small" quaternary round>
              {{ (previewDoc.file_size / 1024).toFixed(2) }} KB
            </n-tag>
          </div>
          <div class="text-xs text-gray-400">上传于: {{ previewDoc?.created_at ? new Date(previewDoc.created_at).toLocaleString() : '-' }}</div>
        </div>
        
        <div v-if="previewDoc?.description" class="text-xs text-gray-600 dark:text-gray-400 italic">
          <span class="font-bold not-italic text-gray-800 dark:text-gray-200">简介：</span>
          {{ previewDoc.description }}
        </div>

        <div v-if="previewDoc?.summary" class="text-xs text-gray-600 dark:text-gray-400">
          <span class="font-bold text-gray-800 dark:text-gray-200">内容摘要：</span>
          {{ previewDoc.summary }}
        </div>
        
        <div v-if="previewDoc?.keywords && previewDoc.keywords.length > 0" class="flex flex-wrap gap-1">
          <n-tag v-for="tag in previewDoc.keywords" :key="tag" size="tiny" quaternary round># {{ tag }}</n-tag>
        </div>
        <div v-else-if="previewDoc?.suggested_tags?.length" class="flex flex-wrap gap-1">
          <n-tag v-for="tag in previewDoc.suggested_tags" :key="tag" size="tiny" quaternary round># {{ tag }}</n-tag>
        </div>
      </div>

      <!-- 内容预览区域 -->
      <div class="flex-1 overflow-y-auto pr-2 scrollbar-thin">
        <n-skeleton v-if="previewLoading" :repeat="10" />
        <div v-else-if="previewContent" class="markdown-body text-sm leading-relaxed p-2 whitespace-pre-wrap">
          {{ previewContent }}
        </div>
        <n-empty v-else description="暂无内容" />
      </div>
    </div>
    
    <template #footer>
      <div class="flex justify-end gap-3">
        <n-button @click="showPreviewModal = false">关闭</n-button>
        <n-button type="primary" secondary @click="handleDownload(previewDoc?.id)">下载文档</n-button>
      </div>
    </template>
  </n-modal>

  <div class="flex h-full w-full bg-gray-50 dark:bg-gray-950 overflow-hidden transition-colors duration-300">
    <!-- 侧边栏 -->
    <aside 
      class="flex-shrink-0 bg-white/80 dark:bg-gray-900/80 backdrop-blur-xl border-r border-gray-200/50 dark:border-gray-800/50 transition-all duration-300 flex flex-col z-20"
      :class="[
        sidebarOpen ? 'w-72 translate-x-0' : 'w-0 -translate-x-full overflow-hidden opacity-0 md:opacity-100 md:w-0 md:translate-x-0'
      ]"
    >
      <!-- 侧边栏头部 -->
      <div class="p-4 border-b border-gray-100/50 dark:border-gray-800/50 flex items-center justify-between bg-white/40 dark:bg-gray-900/40 backdrop-blur-md sticky top-0 z-10">
        <n-button 
          block 
          round
          class="flex-1 mr-2 bg-blue-600 hover:bg-blue-700 text-white border-none shadow-lg shadow-blue-500/20 transition-all duration-300"
          @click="newConversation"
        >
          <template #icon><n-icon><AddOutline /></n-icon></template>
          {{ t('chat.newChat') }}
        </n-button>
        <n-tooltip trigger="hover">
          <template #trigger>
            <n-button quaternary circle size="small" @click="fetchConversations" :loading="isListLoading" class="hover:bg-gray-100 dark:hover:bg-gray-800 transition-colors">
               <template #icon><n-icon><RefreshOutline /></n-icon></template>
            </n-button>
          </template>
          {{ t('common.refresh') }}
        </n-tooltip>
      </div>

      <!-- 会话列表 -->
      <div class="flex-1 overflow-y-auto p-3 space-y-2 scrollbar-thin">
        <!-- 加载状态 (骨架屏) -->
        <div v-if="isListLoading" class="space-y-3 px-2">
          <div v-for="i in 5" :key="i" class="flex items-center gap-3 p-3 rounded-xl bg-gray-50/50 dark:bg-gray-800/30">
            <n-skeleton circle size="small" />
            <div class="flex-1 space-y-2">
              <n-skeleton text style="width: 80%" />
              <n-skeleton text style="width: 40%" size="small" />
            </div>
          </div>
        </div>

        <div v-else-if="conversations.length === 0" class="text-center text-gray-400 dark:text-gray-500 text-sm py-8 flex flex-col items-center">
          <n-icon size="48" class="mb-2 opacity-20"><ChatboxOutline /></n-icon>
          {{ t('chat.noHistory') }}
        </div>
        
        <div 
          v-for="conv in conversations" 
          :key="conv.id"
          class="group relative flex items-center p-3 rounded-xl cursor-pointer transition-all duration-300 border border-transparent"
          :class="[
            currentConversationId === conv.id 
              ? 'bg-blue-50 dark:bg-blue-900/20 border-blue-100/50 dark:border-blue-800/30 text-blue-900 dark:text-blue-100 shadow-sm' 
              : 'hover:bg-gray-50/80 dark:hover:bg-gray-800/40 text-gray-700 dark:text-gray-300 hover:scale-[1.02]'
          ]"
          @click="handleSelectConversation(conv)"
        >
          <!-- 选中时的左侧呼吸灯 -->
          <div 
            v-if="currentConversationId === conv.id"
            class="absolute left-0 top-1/2 -translate-y-1/2 w-1 h-6 bg-blue-500 rounded-r-full animate-pulse shadow-[0_0_10px_rgba(59,130,246,0.5)]"
          ></div>

          <div class="flex-shrink-0 mr-3 text-lg opacity-80 transition-transform duration-300 group-hover:scale-110">
            <n-icon v-if="currentConversationId === conv.id" class="text-blue-600 dark:text-blue-400"><ChatboxEllipsesOutline /></n-icon>
            <n-icon v-else class="text-gray-400 group-hover:text-blue-500"><ChatboxOutline /></n-icon>
          </div>
          
          <div class="flex-1 min-w-0">
            <h3 class="text-sm font-medium truncate leading-tight mb-1.5 transition-colors duration-200">
              {{ conv.title || t('chat.defaultTitle') }}
            </h3>
            <p class="text-xs opacity-60 truncate flex justify-between items-center font-light">
              <span>{{ formatDate(conv.updatedAt || conv.created_at) }}</span>
            </p>
          </div>

          <!-- 删除按钮 (Hover显示) -->
          <div 
            class="absolute right-2 opacity-0 group-hover:opacity-100 transition-all duration-200 bg-gradient-to-l from-white via-white to-transparent dark:from-gray-800 dark:via-gray-800 pl-4 py-1"
            v-if="currentConversationId !== conv.id"
          >
             <n-popconfirm @positive-click.stop="handleDeleteConversation(conv.id)">
               <template #trigger>
                 <n-button size="tiny" quaternary circle class="text-gray-400 hover:text-red-500 hover:bg-red-50 dark:hover:bg-red-900/20 transition-colors">
                   <template #icon><n-icon><TrashOutline /></n-icon></template>
                 </n-button>
               </template>
               {{ t('chat.confirmDelete') }}
             </n-popconfirm>
          </div>
        </div>
      </div>
      
      <!-- 侧边栏底部 -->
      <div class="p-4 border-t border-gray-100 dark:border-gray-800 text-xs text-center text-gray-400 dark:text-gray-600">
        AI Assistant v1.0
      </div>
    </aside>

    <!-- 主内容区域 -->
    <main class="flex-1 flex flex-col h-full relative min-w-0">
      <!-- ⚡ 核心修复：将 Input 移动到容器最外层，防止被 footer 样式遮挡，并添加 ID -->
      <input 
        type="file" 
        id="global-chat-file-input" 
        style="display: none;" 
        @change="handleFileUpload" 
        accept=".pdf,.doc,.docx,.txt,.md"
      />
      <!-- 顶部导航栏 -->
      <header class="absolute top-0 left-0 right-0 z-10 px-4 py-3 flex items-center justify-between bg-white/80 dark:bg-gray-900/80 backdrop-blur-md border-b border-gray-100 dark:border-gray-800 transition-colors duration-300">
        <div class="flex items-center gap-3">
          <n-button quaternary circle size="small" @click="toggleSidebar" class="mr-1">
            <template #icon>
              <n-icon size="20" class="text-gray-600 dark:text-gray-300">
                <MenuOutline v-if="!sidebarOpen" />
                <CloseOutline v-else />
              </n-icon>
            </template>
          </n-button>
          
          <div class="flex items-center space-x-2">
            <div class="bg-blue-100 dark:bg-blue-900/30 p-1.5 rounded-lg text-blue-600 dark:text-blue-400">
              <n-icon size="18"><SparklesOutline /></n-icon>
            </div>
            <div class="hidden sm:block">
            <h1 class="text-sm font-bold text-gray-800 dark:text-white leading-tight">
              {{ chatStore.currentConversation?.title || t('chat.aiAssistant') }}
            </h1>
            <div v-if="isBoundMode" class="mt-1">
              <n-tag size="tiny" round :bordered="false" type="primary">
                <template #icon>
                  <n-icon :component="AttachOutline" />
                </template>
                仅当前文档模式
                <n-button text size="tiny" @click="handleUnbind" class="ml-2 hover:text-white">解除</n-button>
              </n-tag>
            </div>
          </div>
          </div>
        </div>

        <div class="flex items-center space-x-2">
          <n-tooltip trigger="hover">
            <template #trigger>
              <n-button quaternary circle size="small" @click="clearChat" class="text-gray-500 dark:text-gray-400" :disabled="!currentConversationId">
                <template #icon><n-icon><TrashOutline /></n-icon></template>
              </n-button>
            </template>
            {{ t('chat.clearChat') }}
          </n-tooltip>
        </div>
      </header>

      <!-- 消息区域 -->
      <div 
        ref="chatContainer" 
        class="flex-1 overflow-y-auto px-4 pt-20 pb-40 scroll-smooth relative"
        @scroll="handleScroll"
      >
        <div class="max-w-3xl mx-auto space-y-6">
          <!-- 欢迎页 (无消息时显示) -->
          <div v-if="messages.length === 0" class="flex flex-col items-center justify-center mt-16 md:mt-24 text-center animate-fade-in">
            <div class="relative mb-10">
              <div class="w-28 h-28 bg-gradient-to-br from-blue-500 via-blue-600 to-blue-700 rounded-3xl shadow-2xl shadow-blue-500/30 flex items-center justify-center transform hover:scale-105 transition-transform duration-300">
                <n-icon size="52" class="text-white drop-shadow">
                  <SparklesOutline />
                </n-icon>
              </div>
              <div class="absolute -inset-2 bg-blue-400/20 rounded-[1.5rem] blur-xl -z-10"></div>
            </div>
            <h2 class="text-3xl md:text-4xl font-extrabold tracking-tight text-slate-900 dark:text-white mb-3">
              {{ t('chat.welcome') }}
            </h2>
            <p class="text-base md:text-lg text-slate-500 dark:text-slate-400 max-w-md mb-10 font-light leading-relaxed">
              {{ t('chat.welcomeDesc') }}
            </p>
            
            <div class="grid grid-cols-1 sm:grid-cols-2 gap-3 w-full max-w-2xl">
              <button 
                v-for="(suggestion, index) in suggestions" 
                :key="index"
                class="p-4 md:p-5 bg-white/80 dark:bg-gray-800/80 backdrop-blur border border-slate-200/60 dark:border-gray-700/60 rounded-2xl text-left hover:border-blue-400/60 hover:shadow-lg hover:shadow-blue-500/10 transition-all duration-300 group relative overflow-hidden"
                @click="useSuggestion(suggestion)"
              >
                <div class="absolute top-0 right-0 p-3 opacity-0 group-hover:opacity-100 transition-all duration-300 transform translate-x-2 group-hover:translate-x-0">
                  <n-icon class="text-blue-500" size="18"><ArrowForwardOutline /></n-icon>
                </div>
                <h3 class="font-semibold text-sm md:text-base text-slate-800 dark:text-slate-200 group-hover:text-blue-600 dark:group-hover:text-blue-400 mb-1.5 tracking-wide">
                  {{ suggestion.title }}
                </h3>
                <p class="text-xs md:text-sm text-slate-500 dark:text-slate-400 line-clamp-2 leading-relaxed font-light">
                  {{ suggestion.desc }}
                </p>
              </button>
            </div>
          </div>

          <!-- 消息列表 -->
          <DynamicScroller
            :items="messages"
            :min-item-size="120"
            key-field="id"
            class="flex-1"
            v-slot="{ item: message, index, active }"
          >
            <!-- ⚠️ 核心修复：必须在这里传出 item, active, index 属性 -->
            <DynamicScrollerItem
              :item="message"
              :active="active"
              :index="index"
              :data-index="index"
            >
              <div
                class="flex gap-4 animate-slide-in-bottom group mb-4"
                :class="{ 'flex-row-reverse': message.messageType === 'user' }"
              >
                <!-- 头像 -->
                <div 
                  class="flex-shrink-0 w-10 h-10 rounded-2xl flex items-center justify-center overflow-hidden shadow-lg mt-1 transform transition-all duration-300 group-hover:scale-110 group-hover:shadow-blue-500/20"
                  :class="message.messageType === 'user' ? 'bg-gradient-to-br from-gray-800 to-black' : 'bg-white dark:bg-gray-800 border border-gray-100 dark:border-gray-700'"
                >
                  <n-avatar
                    v-if="message.messageType === 'user'"
                    :src="userStore.userInfo?.avatar"
                    :fallback-src="'/default-avatar.png'"
                    size="small"
                    class="bg-transparent"
                  />
                  <n-icon v-else size="20" class="text-blue-600 dark:text-blue-400"><SparklesOutline /></n-icon>
                </div>

                <!-- 消息气泡 -->
                <div class="max-w-[85%] relative group/bubble">
                  <div
                    class="px-6 py-4 rounded-[1.5rem] shadow-sm text-[15px] leading-relaxed tracking-wide transition-all duration-300"
                    :class="{
                      'bg-blue-600 text-white border border-blue-500 rounded-tr-sm hover:shadow-xl hover:shadow-blue-600/20': message.messageType === 'user',
                      'bg-white dark:bg-gray-800/80 backdrop-blur-md text-gray-800 dark:text-gray-100 border border-gray-100 dark:border-gray-700/50 rounded-tl-sm hover:shadow-xl hover:shadow-black/5': message.messageType === 'assistant'
                    }"
                  >
                    <div v-if="message.messageType === 'user'" class="whitespace-pre-wrap font-medium">{{ message.content }}</div>
                    <div v-else class="max-w-none">
                      <Markdown :content="message.content" />
                    </div>
                    
                    <!-- 用户上传的文件列表展示 -->
                    <div v-if="message.messageType === 'user' && message.files && message.files.length > 0" class="mt-3 flex flex-col gap-2">
                      <div v-for="file in message.files" :key="file.id" 
                           class="flex items-center gap-3 p-2.5 rounded-xl bg-white/10 border border-white/20 hover:bg-white/20 transition-colors cursor-pointer group/file"
                           @click="handlePreviewFile(file.id)">
                        <div class="w-8 h-8 rounded-lg bg-blue-500 flex items-center justify-center shrink-0 group-hover/file:bg-blue-400 transition-colors">
                          <n-icon size="18"><DocumentTextOutline /></n-icon>
                        </div>
                        <div class="flex flex-col flex-1 min-w-0">
                          <span class="text-sm font-medium truncate" :title="file.name">{{ file.name }}</span>
                          <span class="text-[10px] opacity-70 group-hover/file:opacity-100 transition-opacity">点击在当前窗口预览文档</span>
                        </div>
                      </div>
                    </div>

                    <!-- 缓存命中提示 -->
                    <div v-if="message.isCached" class="flex items-center gap-1.5 mb-2 mt-2 px-1">
                      <span class="text-xs font-medium text-green-600 dark:text-green-400 bg-green-50 dark:bg-green-900/30 px-2.5 py-0.5 rounded-full border border-green-200 dark:border-green-800 flex items-center gap-1">
                        ⚡ 语义缓存秒回 (0ms)
                      </span>
                    </div>

                    <!-- 检索状态提示 -->
                    <div v-if="isRetrieving && index === messages.length - 1" class="flex items-center gap-2 mb-2 px-1 animate-pulse">
                      <n-icon size="14" class="text-primary-500"><SearchOutline /></n-icon>
                      <span class="text-xs text-primary-500">正在知识库中检索相关内容...</span>
                    </div>

                    <!-- 引用来源 -->
                    <div v-if="message.sources && message.sources.length > 0" class="mt-5 pt-4 border-t border-gray-100/50 dark:border-gray-700/50">
                      <div class="flex items-center gap-2 text-[10px] opacity-60 mb-3 text-gray-500 dark:text-gray-400 font-black uppercase tracking-[0.2em]">
                        <n-icon size="14"><DocumentTextOutline /></n-icon>
                        <span>{{ t('chat.sources') }}</span>
                      </div>
                      <div class="flex flex-wrap gap-2">
                        <n-tooltip 
                          v-for="(source, idx) in message.sources" 
                          :key="idx"
                          trigger="hover" 
                          placement="top"
                          :style="{ maxWidth: '400px' }"
                        >
                          <template #trigger>
                            <div
                              class="flex flex-col gap-1.5 px-3 py-2 bg-gray-50/50 dark:bg-gray-900/50 border border-gray-100 dark:border-gray-700/50 rounded-xl text-xs hover:border-primary-400 dark:hover:border-primary-500 transition-all cursor-help text-gray-600 dark:text-gray-300 group/source"
                            >
                              <div class="flex items-center justify-between gap-2">
                                <span class="truncate max-w-[150px] font-medium">{{ source.filename || '未知文档' }}</span>
                                <span class="px-2 py-0.5 bg-primary-100 dark:bg-primary-900/30 text-primary-600 dark:text-primary-400 rounded-lg font-black text-[10px]">{{ (source.relevanceScore * 100).toFixed(0) }}%</span>
                              </div>
                              <div class="flex flex-wrap items-center gap-1 mt-0.5">
                                <span v-if="source.hasKeyword" class="px-1.5 py-0.5 bg-blue-100 dark:bg-blue-900/30 text-blue-600 dark:text-blue-400 rounded-md text-[9px] font-bold">关键词</span>
                                <span v-if="source.hasVector" class="px-1.5 py-0.5 bg-indigo-100 dark:bg-indigo-900/30 text-blue-600 dark:text-blue-400 rounded-md text-[9px] font-bold">向量</span>
                              </div>
                            </div>
                          </template>
                          <div class="text-xs leading-relaxed max-h-60 overflow-y-auto pr-2 scrollbar-thin">
                            <div class="font-bold mb-2 pb-2 border-b border-gray-200 dark:border-gray-700 text-primary-400">原文片段:</div>
                            {{ source.content || source.snippet || '无原文内容' }}
                          </div>
                        </n-tooltip>
                      </div>
                    </div>
                  </div>
                  
                  <!-- 消息操作栏 -->
                  <div 
                    class="absolute top-0 flex gap-1 opacity-0 group-hover/bubble:opacity-100 transition-all duration-300 transform translate-y-[-70%] scale-90 group-hover/bubble:scale-100"
                    :class="message.messageType === 'user' ? 'right-4 flex-row-reverse' : 'left-4'"
                  >
                    <div class="bg-white/90 dark:bg-gray-800/90 backdrop-blur-md shadow-2xl border border-gray-100 dark:border-gray-700 rounded-full flex p-1.5 space-x-1 items-center">
                      <template v-if="message.messageType === 'assistant'">
                        <n-tooltip trigger="hover" placement="top">
                          <template #trigger>
                            <n-button 
                              size="tiny" 
                              quaternary 
                              circle 
                              :class="message.feedback === 1 ? 'text-blue-600 bg-blue-50 dark:bg-blue-900/30' : 'text-gray-400 hover:text-blue-600'" 
                              @click="handleFeedback(message, 1)"
                            >
                              <template #icon><n-icon><ThumbsUpOutline /></n-icon></template>
                            </n-button>
                          </template>
                          有用
                        </n-tooltip>
                        <n-tooltip trigger="hover" placement="top">
                          <template #trigger>
                            <n-button 
                              size="tiny" 
                              quaternary 
                              circle 
                              :class="message.feedback === -1 ? 'text-red-600 bg-red-50 dark:bg-red-900/30' : 'text-gray-400 hover:text-red-600'" 
                              @click="handleFeedback(message, -1)"
                            >
                              <template #icon><n-icon><ThumbsDownOutline /></n-icon></template>
                            </n-button>
                          </template>
                          没用
                        </n-tooltip>
                      </template>
                      <n-button size="tiny" quaternary circle class="text-gray-400" @click="copyText(message.content)">
                        <template #icon><n-icon><CopyOutline /></n-icon></template>
                      </n-button>
                    </div>
                  </div>
                </div>
              </div>
            </DynamicScrollerItem>
          </DynamicScroller>

          <!-- Loading -->
          <div v-if="isLoading" class="flex gap-4 animate-pulse">
            <div class="w-10 h-10 bg-white dark:bg-gray-800 rounded-2xl border border-gray-200 dark:border-gray-700 flex items-center justify-center shadow-sm">
              <n-icon size="20" class="text-primary-600 dark:text-primary-400 animate-spin-slow"><SparklesOutline /></n-icon>
            </div>
            <div class="flex items-center h-10">
              <div class="flex space-x-1.5">
                <div class="w-2 h-2 bg-primary-400 rounded-full animate-bounce" style="animation-delay: 0ms"></div>
                <div class="w-2 h-2 bg-primary-400 rounded-full animate-bounce" style="animation-delay: 150ms"></div>
                <div class="w-2 h-2 bg-primary-400 rounded-full animate-bounce" style="animation-delay: 300ms"></div>
              </div>
            </div>
          </div>
        </div>

        <!-- 回到最新消息悬浮按钮 -->
        <transition name="fade">
          <div 
            v-if="showBackToBottom" 
            class="absolute bottom-32 left-1/2 -translate-x-1/2 z-20"
          >
            <n-button 
              round 
              size="small" 
              class="shadow-xl bg-white/80 dark:bg-gray-800/80 backdrop-blur-md border border-gray-200 dark:border-gray-700 text-primary-600 dark:text-primary-400 font-bold"
              @click="scrollToBottom('smooth', true)"
            >
              <template #icon><n-icon><ArrowDownOutline /></n-icon></template>
              {{ t('chat.backToBottom') || '回到最新消息' }}
            </n-button>
          </div>
        </transition>
      </div>

      <!-- 底部输入框 -->
      <footer class="absolute bottom-0 left-0 right-0 p-4 md:p-6 bg-gradient-to-t from-gray-50 via-gray-50 to-transparent dark:from-gray-950 dark:via-gray-950 transition-colors duration-300">
        <div class="max-w-3xl mx-auto relative">
          <!-- 停止生成按钮 (仅在加载时显示) -->
          <div v-if="isLoading" class="absolute -top-12 left-1/2 -translate-x-1/2">
             <n-button 
              round 
              size="small" 
              class="shadow-lg border-red-200 dark:border-red-900/30 text-red-500 bg-white/90 dark:bg-gray-800/90 backdrop-blur-sm"
              @click="stopGeneration"
            >
              <template #icon><n-icon><StopCircleOutline /></n-icon></template>
              {{ t('chat.stopGenerating') || '停止生成' }}
            </n-button>
          </div>

          <div 
            class="bg-white dark:bg-gray-800 rounded-2xl shadow-[0_8px_30px_rgb(0,0,0,0.08)] border border-gray-200 dark:border-gray-700 p-2 transition-all duration-300 focus-within:shadow-[0_8px_30px_rgb(0,0,0,0.12)] focus-within:border-primary-300 dark:focus-within:border-primary-700"
          >
            <div v-if="attachedFiles.length > 0" class="flex flex-wrap gap-2 px-3 pt-2 items-center">
              <div v-for="(file, index) in attachedFiles" :key="index"
                class="flex items-center gap-1.5 px-2.5 py-1 rounded-full border text-xs transition-all duration-300"
                :class="file.status === 'done'
                  ? 'bg-green-50 border-green-200 text-green-600 dark:bg-green-900/30 dark:border-green-800/50 dark:text-green-400'
                  : file.status === 'error'
                   ? 'bg-red-50 border-red-200 text-red-600 dark:bg-red-900/30 dark:border-red-800/50 dark:text-red-400 cursor-pointer hover:bg-red-100 dark:hover:bg-red-900/50'
                   : file.status === 'indexing'
                    ? 'bg-blue-50 border-blue-200 text-blue-600 dark:bg-blue-900/30 dark:border-blue-800/50 dark:text-blue-400'
                   : file.status === 'parsing'
                    ? 'bg-amber-50 border-amber-200 text-amber-600 dark:bg-amber-900/30 dark:border-amber-800/50 dark:text-amber-400'
                   : 'bg-gray-50 border-gray-200 text-gray-500 dark:bg-gray-800 dark:border-gray-700 animate-pulse'"
                @click="file.status === 'error' && toggleFileError(file, index)"
              >
                <n-icon size="14">
                  <DocumentTextOutline v-if="file.name.endsWith('.pdf')" />
                  <DocumentOutline v-else />
                </n-icon>
                <span class="truncate max-w-[120px]" :title="file.name">{{ file.name }}</span>

                <span v-if="file.status === 'uploading'" class="ml-1 text-[10px] text-gray-400 flex items-center gap-1">
                  <n-icon size="10"><CloudUploadOutline /></n-icon>
                  上传中...
                </span>
                <span v-else-if="file.status === 'parsing'" class="ml-1 text-[10px] text-amber-500 flex items-center gap-1">
                  <n-spin :size="10" stroke="currentColor" />
                  {{ file.statusDetail || '解析中...' }}
                </span>
                <span v-else-if="file.status === 'indexing'" class="ml-1 text-[10px] text-blue-500 flex items-center gap-1">
                  <n-spin :size="10" stroke="currentColor" />
                  {{ file.statusDetail || '索引中...' }}
                </span>
                <span v-else-if="file.status === 'done'" class="ml-1 text-[10px] text-green-500 flex items-center gap-1">
                  <n-icon size="10"><CheckmarkCircleOutline /></n-icon>
                  {{ file.statusDetail || '已完成' }}
                </span>
                <span v-else-if="file.status === 'error'" class="ml-1 text-[10px] flex items-center gap-1">
                  <n-icon size="12" class="flex-shrink-0"><CloseCircleOutline /></n-icon>
                  <span class="truncate max-w-[80px]" :title="file.errorMsg">{{ file.errorMsg || '解析失败' }}</span>
                  <n-popover v-if="expandedErrorFile?.name === file.name" trigger="click" placement="top" @update:show="(val: boolean) => { if (!val) expandedErrorFile = null }">
                    <template #trigger>
                      <button @click.stop="toggleFileError(file, index)" class="p-0.5 rounded-full hover:bg-red-100 dark:hover:bg-red-900/30 transition-colors">
                        <n-icon size="12"><ExpandOutline v-if="!file._errorExpanded" /></n-icon>
                      </button>
                    </template>
                    <div class="max-w-sm p-3">
                      <div class="text-xs font-bold text-red-600 dark:text-red-400 mb-2 flex items-center gap-1">
                        <n-icon size="14"><CloseCircleOutline /></n-icon>
                        解析失败详情
                      </div>
                      <div class="text-xs text-gray-600 dark:text-gray-400 mb-3 break-all leading-relaxed">
                        {{ file.errorMsg || '未知错误' }}
                      </div>
                      <div class="flex gap-2">
                        <n-button size="tiny" type="error" round @click.stop="retryFileUpload(file, index)">
                          <template #icon><n-icon size="12"><RefreshOutline /></n-icon></template>
                          重新上传
                        </n-button>
                        <n-button size="tiny" quaternary round @click.stop="removeAttachment(index); expandedErrorFile = null">
                          移除
                        </n-button>
                      </div>
                    </div>
                  </n-popover>
                  <button v-else @click.stop="toggleFileError(file, index)" class="p-0.5 rounded-full hover:bg-red-100 dark:hover:bg-red-900/30 transition-colors">
                    <n-icon size="12"><ExpandOutline /></n-icon>
                  </button>
                </span>

                <button @click.stop="removeAttachment(index)" class="ml-1 hover:text-red-500 transition-colors p-0.5 rounded-full hover:bg-red-50 dark:hover:bg-red-900/20">
                  <n-icon size="14"><CloseOutline /></n-icon>
                </button>
              </div>

              <!-- ⚡ 核心功能：仅基于附件回答硬开关 -->
              <div v-if="attachedFileIds.length > 0" class="ml-auto pr-2 flex items-center gap-2">
                <n-tooltip trigger="hover">
                  <template #trigger>
                    <div class="flex items-center gap-2 bg-amber-50 dark:bg-amber-900/20 px-3 py-1.5 rounded-lg border border-amber-200 dark:border-amber-800/50 hover:border-amber-400 dark:hover:border-amber-600 transition-colors">
                      <n-icon size="14" class="text-amber-600 dark:text-amber-400"><ShieldOutline /></n-icon>
                      <span class="text-[11px] font-bold text-amber-700 dark:text-amber-400 uppercase tracking-wider">仅附件模式</span>
                      <n-switch v-model:value="strictMode" size="small" :round="false" />
                    </div>
                  </template>
                  <div class="text-xs leading-relaxed">
                    <div class="font-bold mb-1">强约束模式</div>
                    <div>开启后，AI 将 <span class="text-red-500 font-bold">完全基于</span> 你上传的文档内容回答，<span class="text-red-500 font-bold">不会</span> 使用全局知识库或其他外部知识。</div>
                  </div>
                </n-tooltip>
              </div>
            </div>

            <n-input
              v-model:value="inputMessage"
              type="textarea"
              :placeholder="t('chat.inputPlaceholder')"
              :autosize="{ minRows: 1, maxRows: 6 }"
              :bordered="false"
              class="text-base px-2 py-1 !bg-transparent text-gray-900 dark:text-gray-100 placeholder-gray-400 dark:placeholder-gray-500"
              @keydown.enter.prevent="handleKeydown"
              :disabled="isLoading"
            />
            
            <div class="flex justify-between items-center mt-2 px-2 pb-1">
              <div class="flex gap-2 items-center">
                <n-tooltip trigger="hover">
                  <template #trigger>
                    <n-button size="tiny" quaternary circle class="text-gray-400 hover:text-primary-600 dark:hover:text-primary-400" @click="triggerFileUpload">
                      <template #icon><n-icon size="18"><AttachOutline /></n-icon></template>
                    </n-button>
                  </template>
                  {{ t('chat.uploadFile') }}
                </n-tooltip>
                
                <!-- 连接状态：更醒目的胶囊 (SSE模式显示SSE状态) -->
                <span 
                  class="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-medium border"
                  :class="effectiveConnectionStatus === 'connected' 
                    ? 'bg-emerald-50 dark:bg-emerald-900/20 text-emerald-600 dark:text-emerald-400 border-emerald-200 dark:border-emerald-800/50' 
                    : (effectiveConnectionStatus === 'connecting'
                    ? 'bg-amber-50 dark:bg-amber-900/20 text-amber-600 dark:text-amber-400 border-amber-200 dark:border-amber-800/50'
                    : 'bg-red-50 dark:bg-red-900/20 text-red-600 dark:text-red-400 border-red-200 dark:border-red-800/50')"
                >
                  <span 
                    class="w-2 h-2 rounded-full shrink-0"
                    :class="effectiveConnectionStatus === 'connected' ? 'bg-emerald-500 animate-pulse' : effectiveConnectionStatus === 'connecting' ? 'bg-amber-500 animate-pulse' : 'bg-red-500'"
                  ></span>
                  {{ useSSE ? 'SSE' : '' }}{{ getConnectionStatusText(effectiveConnectionStatus) }}
                </span>
                
                <n-popover trigger="hover" placement="top">
                  <template #trigger>
                     <n-button size="tiny" quaternary circle :class="useStream ? 'text-primary-600 dark:text-primary-400' : 'text-gray-400'">
                      <template #icon><n-icon size="18"><FlashOutline /></n-icon></template>
                    </n-button>
                  </template>
                  <div class="flex items-center gap-2">
                    <n-switch v-model:value="useStream" size="small" />
                    <span class="text-xs">{{ t('chat.streamResponse') }}</span>
                  </div>
                </n-popover>

                <n-popover trigger="hover" placement="top">
                  <template #trigger>
                     <n-button size="tiny" quaternary circle :class="privacyMode ? 'text-green-600 dark:text-green-400' : 'text-gray-400'">
                      <template #icon><n-icon size="18"><ShieldCheckmarkOutline /></n-icon></template>
                    </n-button>
                  </template>
                  <div class="flex items-center gap-2">
                    <n-switch v-model:value="privacyMode" size="small" />
                    <span class="text-xs">隐私模式 (自动脱敏敏感信息)</span>
                  </div>
                </n-popover>

                <n-popover trigger="hover" placement="top">
                  <template #trigger>
                     <n-button size="tiny" quaternary circle :class="useSSE ? 'text-amber-600 dark:text-amber-400' : 'text-gray-400'">
                      <template #icon><n-icon size="18"><RadioButtonOnOutline /></n-icon></template>
                    </n-button>
                  </template>
                  <div class="flex items-center gap-2">
                    <n-switch v-model:value="useSSE" size="small" />
                    <span class="text-xs">SSE模式 (企业防火墙兼容)</span>
                  </div>
                </n-popover>
              </div>

              <div class="flex items-center gap-3">
                <n-button
                  type="primary"
                  round
                  size="small"
                  :loading="isLoading"
                  :disabled="!inputMessage.trim() || isLoading"
                  @click="handleSend"
                  class="!px-4"
                >
                  <template #icon>
                    <n-icon><SendOutline /></n-icon>
                  </template>
                  {{ t('chat.send') }}
                </n-button>
              </div>
            </div>
          </div>
          
          <div class="text-center mt-3 text-xs text-gray-400 dark:text-gray-500 hidden sm:block">
            {{ t('chat.disclaimer') }}
          </div>
        </div>
      </footer>
    </main>
  </div>
</template>



<script setup lang="ts">
import { ref, nextTick, onMounted, onUnmounted, watch, computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { NAvatar } from 'naive-ui'
import { useDedupedMessage } from '@/utils/message'
import { useI18n } from 'vue-i18n'
import { useChatStore } from '@/stores/chat'
import { useUserStore } from '@/stores/user'
import { useAppStore } from '@/stores/app'
import { 
  getConversationMessages, createConversation, deleteConversation, 
  updateMessageFeedback, clearConversationMessages 
} from '@/api/chat'
import { getConversations } from '@/api/conversation'
import { uploadKnowledgeBase, getKnowledgeBase, getDocumentDetail, getDocumentContent } from '@/api/knowledge'
import { wsService } from '@/utils/websocket'
import { sseService } from '@/utils/sseService'
import { getToken } from '@/utils/auth'
import Markdown from '@/components/common/Markdown.vue'
import { format } from 'date-fns'
import {
  SparklesOutline, SendOutline, AddOutline, TrashOutline, DocumentTextOutline, DocumentOutline,
  AttachOutline, FlashOutline, CopyOutline, MenuOutline, CloseOutline,
  ShieldCheckmarkOutline, RadioButtonOnOutline,
  ChatboxOutline, ChatboxEllipsesOutline, RefreshOutline, ArrowForwardOutline,
  ArrowDownOutline, StopCircleOutline, CloseCircleOutline, SearchOutline,
  ExpandOutline, ShieldOutline, ThumbsUpOutline, ThumbsDownOutline,
  ThumbsUp, ThumbsDown, CloudUploadOutline, CheckmarkCircleOutline
} from '@vicons/ionicons5'
import type { ChatMessage, AttachedFile } from '@/types/chat'

const message = useDedupedMessage()
const { t } = useI18n()
const route = useRoute()
const router = useRouter()
const chatStore = useChatStore()
const userStore = useUserStore()
const appStore = useAppStore()

const inputMessage = ref('')
const isLoading = ref(false)
const isRetrieving = ref(false) // 检索状态
const useStream = ref(true)
const useSSE = ref(false)
const privacyMode = ref(true)
const chatContainer = ref<HTMLElement>()
const connectionStatus = ref<'connected' | 'disconnected' | 'connecting' | 'error'>('disconnected')
const sseStatus = ref<'connected' | 'disconnected' | 'connecting' | 'error'>('disconnected')
const showBackToBottom = ref(false)
const isUserScrolling = ref(false)
const autoFollowResponse = ref(false)
const strictMode = ref(false)
const fileInputRef = ref<HTMLInputElement | null>(null)
const expandedErrorFile = ref<AttachedFile | null>(null)
let statusTimer: any = null

const effectiveConnectionStatus = computed(() => {
  if (useSSE.value) return sseStatus.value
  return connectionStatus.value
})

// ⚡ 新增：附件状态管理
const attachedFiles = ref<AttachedFile[]>([])
const attachedFileIds = computed(() => attachedFiles.value.filter(f => f.status === 'done' && f.id).map(f => f.id!))

// ⚡ 预览状态管理
const showPreviewModal = ref(false)
const previewLoading = ref(false)
const previewDoc = ref<any>(null)
const previewContent = ref('')

const handlePreviewFile = async (fileId?: string) => {
  if (!fileId) return
  
  showPreviewModal.value = true
  previewLoading.value = true
  previewDoc.value = null
  previewContent.value = ''
  
  try {
    const [statusRes, contentRes] = await Promise.all([
      getDocumentDetail(fileId),
      getDocumentContent(fileId)
    ])
    
    if (statusRes.data?.data) {
      const data = statusRes.data.data
      previewDoc.value = {
        id: data.id,
        filename: data.filename,
        file_size: data.file_size,
        created_at: data.created_at,
        description: data.description,
        keywords: data.keywords,
        source: data.upload_source || (data.description && data.description.includes('来自聊天') ? '聊天上传' : '知识库上传')
      }
    }
    
    if (contentRes.data?.data) {
      previewContent.value = contentRes.data.data.content
      if (previewDoc.value) {
        previewDoc.value.summary = contentRes.data.data.summary || ''
        previewDoc.value.suggested_tags = contentRes.data.data.suggested_tags || []
      }
    }
  } catch (e) {
    message.error(t('chat.previewFailed'))
  } finally {
    previewLoading.value = false
  }
}

const handleDownload = (fileId?: string) => {
  if (!fileId) return
  // 直接通过接口下载
  window.open(`${import.meta.env.VITE_API_URL || ''}/api/v1/documents/${fileId}/download`, '_blank')
}

const messages = ref<ChatMessage[]>([])
const conversations = ref<any[]>([])
const isListLoading = ref(false)
const sidebarOpen = computed({
  get: () => !appStore.sidebarCollapsed,
  set: (val) => appStore.setSidebarCollapsed(!val)
})

const checkScreenSize = () => {
  if (window.innerWidth < 768) appStore.setSidebarCollapsed(true)
  else appStore.setSidebarCollapsed(false)
}
const toggleSidebar = () => appStore.toggleSidebar()

const suggestions = computed(() =>[
  { title: t('chat.suggestions.quantum'), desc: t('chat.suggestions.quantumDesc') },
  { title: t('chat.suggestions.code'), desc: t('chat.suggestions.codeDesc') },
  { title: t('chat.suggestions.report'), desc: t('chat.suggestions.reportDesc') },
  { title: t('chat.suggestions.travel'), desc: t('chat.suggestions.travelDesc') }
])
const currentConversationId = computed(() => chatStore.currentConversation?.id)
const useSuggestion = (s: any) => { inputMessage.value = s.title + '，' + s.desc }

const handleKeydown = (e: KeyboardEvent) => {
  if (e.key === 'Enter' && !e.shiftKey) handleSend()
}
const formatDate = (dateStr: string) => {
  if (!dateStr) return ''
  try { return format(new Date(dateStr), 'MM-dd HH:mm') } catch (e) { return dateStr }
}

const handleSSESend = async () => {
  let userMessage = inputMessage.value.trim()
  if (!userMessage && attachedFileIds.value.length > 0) {
    userMessage = `请分析上传的 ${attachedFiles.value.length} 个文件`
  }
  
  if (!userMessage) return
  
  inputMessage.value = ''
  const currentFiles = [...attachedFiles.value]
  attachedFiles.value = []

  const userMsgId = Date.now()
  messages.value.push({
    id: userMsgId,
    content: userMessage,
    messageType: 'user',
    conversationId: chatStore.currentConversation?.id || 0,
    files: currentFiles.map(f => ({ ...f }))
  })
  scrollToBottom()
  isLoading.value = true
  isRetrieving.value = true

  const aiMsgId = 'sse-' + Date.now()
  let fullContent = ''
  messages.value.push({
    id: aiMsgId,
    content: '',
    messageType: 'assistant',
    conversationId: chatStore.currentConversation?.id || 0
  })

  sseService.off('chunk')
  sseService.off('message')
  sseService.off('error')
  sseService.off('retry')

  const onChunk = (data: any) => {
    sseStatus.value = 'connected'
    if (data.conversationId && (!chatStore.currentConversation || chatStore.currentConversation.id !== data.conversationId)) {
      chatStore.setCurrentConversation({
        id: data.conversationId,
        title: userMessage.slice(0, 20),
        userId: userStore.userInfo?.id || 0,
        createdAt: new Date().toISOString(),
        updatedAt: new Date().toISOString()
      })
      fetchConversations()
    }

    isRetrieving.value = false
    const lastMsg = messages.value[messages.value.length - 1]
    if (lastMsg && lastMsg.messageType === 'assistant' && lastMsg.id === aiMsgId) {
      fullContent += data.content || ''
      lastMsg.content = fullContent
      if (data.sources) lastMsg.sources = data.sources
    }
    scrollToBottom()
  }

  const onMessage = (data: any) => {
    isLoading.value = false
    isRetrieving.value = false
    sseStatus.value = 'connected'
    const lastMsg = messages.value[messages.value.length - 1]
    if (lastMsg && lastMsg.id === aiMsgId) {
      lastMsg.content = data.content || fullContent
      if (data.sources) lastMsg.sources = data.sources
      if (data.is_cached) lastMsg.isCached = true
    }
    fetchConversations()
    scrollToBottom()
  }

  const onError = (data: any) => {
    isLoading.value = false
    isRetrieving.value = false
    sseStatus.value = 'error'
    message.error(data.content || t('chat.sseError'))
  }

  const onRetry = (data: any) => {
    const left = data.maxRetries ? (data.maxRetries - data.attempt + 1) : 0
    message.warning(t('chat.sseRetry', { left }))
  }

  sseService.on('chunk', onChunk)
  sseService.on('message', onMessage)
  sseService.on('error', onError)
  sseService.on('retry', onRetry)

  // 获取系统提示词（如果有的话）
  const systemPrompt = localStorage.getItem('activeSystemPrompt')

  try {
    const sent = await sseService.post('stream', {
      content: userMessage,
      conversationId: chatStore.currentConversation?.id,
      fileIds: currentFiles.filter(f => f.status === 'done' && f.id).map(f => f.id!),
      payload: {
        strictMode: strictMode.value,
        privacyMode: privacyMode.value,
        systemPrompt: systemPrompt || undefined
      }
    })

    if (!sent) {
      message.error(t('chat.sendFailed'))
      isLoading.value = false
      attachedFiles.value = currentFiles
      return
    }
  } catch (error) {
    message.error(t('chat.networkError'))
    isLoading.value = false
    attachedFiles.value = currentFiles
  }
}

const handleFeedback = async (msg: ChatMessage, feedback: number) => {
  if (!msg.id) return
  
  // 如果点击的是已经选中的反馈，则取消反馈 (设为 0)
  const newFeedback = msg.feedback === feedback ? 0 : feedback
  
  try {
    const res = await updateMessageFeedback(msg.id, newFeedback)
    if (res.data?.success) {
      msg.feedback = newFeedback
      if (newFeedback !== 0) {
        message.success(newFeedback === 1 ? t('chat.feedbackLiked') : t('chat.feedbackDisliked'))
      }
    } else {
      message.error(t('chat.feedbackFailed', { message: res.data?.message || '未知错误' }))
    }
  } catch (err) {
    message.error(t('chat.feedbackRetryLater'))
  }
}

const copyText = async (text: string) => {
  try { await navigator.clipboard.writeText(text); message.success(t('common.copySuccess')) }
  catch (err) { message.error(t('common.copyFailed')) }
}
const stopGeneration = () => {
  wsService.send(JSON.stringify({ type: 'stop' }))
  isLoading.value = false
  isRetrieving.value = false
}

const handleScroll = () => {
  if (!chatContainer.value) return
  const { scrollTop, scrollHeight, clientHeight } = chatContainer.value
  const distanceToBottom = scrollHeight - scrollTop - clientHeight
  
  // ⚡ 只要用户不在底部（距离底部超过 50px），就认为用户在主动查看历史，不应被强制滚动
  isUserScrolling.value = distanceToBottom > 50
  showBackToBottom.value = distanceToBottom > 300
}

const scrollToBottom = (behavior: ScrollBehavior = 'smooth', force = false) => {
  if (!force) {
    if (!autoFollowResponse.value) return
    if (isUserScrolling.value) return
  }

  nextTick(() => {
    if (chatContainer.value) {
      chatContainer.value.scrollTo({ top: chatContainer.value.scrollHeight, behavior })
    }
  })
}
// ⚡ 仅在消息数量变化（新消息产生）时触发滚动，流式输出内容变化不强制触发 watch
watch(() => messages.value.length, () => { 
  if (!isUserScrolling.value && autoFollowResponse.value) scrollToBottom() 
})

const fetchConversations = async () => {
  isListLoading.value = true
  try {
    const res = await getConversations({ page: 1, page_size: 50 })
    if (res.data.data && Array.isArray(res.data.data.data)) conversations.value = res.data.data.data
  } catch (error) { console.error(error) } finally { isListLoading.value = false }
}

const handleSelectConversation = async (conv: any) => {
  if (currentConversationId.value === conv.id) return
  router.push({ query: { conversation_id: conv.id } })
  await loadConversation(conv.id)
  if (window.innerWidth < 768) sidebarOpen.value = false
}

const handleDeleteConversation = async (id: string) => {
  try {
    await deleteConversation(id)
    message.success(t('common.deleteSuccess'))
    if (currentConversationId.value === id) newConversation()
    await fetchConversations()
  } catch (error) { message.error(t('common.deleteFailed')) }
}

// ⚡ 增强版触发器
const triggerFileUpload = () => {
  const fileInput = document.getElementById('global-chat-file-input') as HTMLInputElement;
  if (fileInput) fileInput.click();
}

const handleFileUpload = async (event: Event) => {
  const target = event.target as HTMLInputElement
  const file = target.files?.[0]
  if (!file) return

  if (file.size > 10 * 1024 * 1024) {
    message.error(t('chat.fileSizeLimit'))
    if (fileInputRef.value) fileInputRef.value.value = ''
    return
  }

  // 添加到界面显示
  const tempFile: AttachedFile = { name: file.name, status: 'uploading', _originalFile: file, progress: 0, statusDetail: '上传中...' }
  attachedFiles.value.push(tempFile)

  try {
    const formData = new FormData()
    formData.append('file', file)
    formData.append('title', file.name)
    formData.append('description', '来自聊天上传')

    const res = await uploadKnowledgeBase(formData)
    const responseData = res.data as any
    const docId = responseData?.data?.id || responseData?.id

    if (docId) {
       tempFile.id = String(docId)
       tempFile.status = 'parsing'
       tempFile.progress = 20
       tempFile.statusDetail = '已上传，等待解析'

       // 开始轮询解析状态
       pollDocumentStatus(String(docId), tempFile)
    } else {
       throw new Error('未获取到文件ID')
    }
  } catch (error: any) {
    tempFile.status = 'error'
    tempFile.errorMsg = '上传失败'
    message.error(t('chat.uploadFailedWithReason', { message: error.message || '未知错误' }))
  } finally {
    // 清空 input
    target.value = ''
  }
}

const pollDocumentStatus = async (docId: string, tempFile: AttachedFile) => {
  const maxRetries = 120; // 增加到 120 次（约 2 分钟）
  let retries = 0;

  while (retries < maxRetries) {
    try {
      const res = await getKnowledgeBase(docId)
      const data = res.data?.data || (res.data as any)
      const status = data.status
      const rawStatus = data.raw_status || status

      // 更新详细状态文本
      const statusDetailMap: Record<string, string> = {
        pending: '等待处理',
        uploaded: '已上传',
        parsing: '解析中',
        parsed: '解析完成',
        indexing: '索引中',
        indexed: '已完成',
        completed: '已完成',
        failed: '解析失败'
      }
      tempFile.statusDetail = statusDetailMap[rawStatus] || statusDetailMap[status] || '处理中'

      // 根据 raw_status 细分状态
      if (rawStatus === 'pending' || rawStatus === 'uploaded') {
        tempFile.status = 'uploading'
        tempFile.progress = 10
      } else if (rawStatus === 'parsing') {
        tempFile.status = 'parsing'
        tempFile.progress = 40
      } else if (rawStatus === 'parsed') {
        tempFile.status = 'indexing'
        tempFile.progress = 70
      } else if (['indexed', 'completed', 'success', 'done', 'parsed'].includes(rawStatus) ||
                 ['indexed', 'completed', 'success', 'done', 'parsed'].includes(status)) {
        tempFile.status = 'done'
        tempFile.progress = 100
        if (!tempFile.id && docId) {
          tempFile.id = docId
        }
        if (tempFile.statusDetail !== '已完成' && tempFile.statusDetail !== '✅ 已就绪') {
          tempFile.statusDetail = '✅ 已就绪'
          message.success(t('chat.docParsed', { name: tempFile.name }))
        }
        return
      } else if (status === 'failed' || rawStatus === 'failed') {
        tempFile.status = 'error'
        tempFile.errorMsg = data.parse_error || '解析失败'
        tempFile.statusDetail = '❌ 失败'
        return
      }
    } catch (e) {
      console.error('Polling document status failed', e)
    }

    await new Promise(resolve => setTimeout(resolve, 1000))
    retries++
  }

  if (tempFile.status === 'parsing' || tempFile.status === 'indexing' || tempFile.status === 'uploading') {
    tempFile.status = 'error'
    tempFile.errorMsg = '处理超时，请稍后在知识库中查看状态'
    tempFile.statusDetail = '⏱ 超时'
  }
}

const removeAttachment = (index: number) => {
  attachedFiles.value.splice(index, 1)
}

const toggleFileError = (file: AttachedFile, index: number) => {
  if (expandedErrorFile.value?.name === file.name) {
    expandedErrorFile.value = null
  } else {
    expandedErrorFile.value = { ...file, _errorExpanded: true } as AttachedFile
  }
}

const retryFileUpload = async (file: AttachedFile, index: number) => {
  const originalFile = file._originalFile as File
  if (!originalFile) {
    message.error(t('chat.noOriginalFile'))
    return
  }

  file.status = 'uploading'
  file.errorMsg = undefined
  expandedErrorFile.value = null

  try {
    const formData = new FormData()
    formData.append('file', originalFile)
    formData.append('title', originalFile.name)
    formData.append('description', '来自聊天上传（重试）')

    const res = await uploadKnowledgeBase(formData)
    const responseData = res.data as any
    const docId = responseData?.data?.id || responseData?.id

    if (docId) {
      file.id = String(docId)
      file.status = 'parsing'
      pollDocumentStatus(String(docId), file)
    } else {
      throw new Error('未获取到文件ID')
    }
  } catch (error: any) {
    file.status = 'error'
    file.errorMsg = '重试失败: ' + (error.message || '未知错误')
    message.error(t('chat.retryUploadFailed'))
  }
}

// ⚡ 发送逻辑 (传递 fileIds 并在发送后清空)
const handleSend = async () => {
  // 如果正在上传中，提示用户
  if (attachedFiles.value.some(f => f.status === 'uploading')) {
    message.warning(t('chat.waitForUpload'))
    return
  }

  // 过滤出正在解析/索引的文件（不算已完成的）
  const pendingFiles = attachedFiles.value.filter(f => ['parsing', 'indexing'].includes(f.status))
  if (pendingFiles.length > 0) {
    message.warning(t('chat.waitForParsing'))
    return
  }

  if (attachedFiles.value.some(f => f.status === 'error')) {
    message.warning(t('chat.removeFailedAttachments'))
    return
  }

  // ⚡ 修复：有文件但状态不对时，提供更友好的提示并允许发送纯文本
  const hasUnreadyFiles = attachedFiles.value.some(f => f.status !== 'done' && f.status !== 'error')
  if (attachedFiles.value.length > 0 && hasUnreadyFiles && attachedFileIds.value.length === 0) {
    message.warning(t('chat.partialAttachmentsNotReady'))
  }

  // 允许发送：只要有输入内容 或 有已完成的附件ID，或有未完成的附件（发送纯文本）
  if (!inputMessage.value.trim() && attachedFileIds.value.length === 0 && attachedFiles.value.length === 0) return
  if (isLoading.value) return

  // 如果完全没有消息内容，且有未完成的附件，给用户一个默认问题
  let userMessage = inputMessage.value.trim()
  if (!userMessage && attachedFiles.value.length > 0 && attachedFileIds.value.length > 0) {
    userMessage = `请分析上传的 ${attachedFiles.value.length} 个文件`
  } else if (!userMessage && attachedFiles.value.length > 0 && attachedFileIds.value.length === 0) {
    // 有未完成的附件但没有文本，给提示并返回
    message.warning(t('chat.attachmentsNotReady'))
    return
  }
  if (!userMessage) return
  
  if (useSSE.value) {
    await handleSSESend()
    return
  }
  
  if (!wsService.isConnected()) {
    message.warning(t('chat.connecting'))
    await connectWebSocket()
    await new Promise(resolve => setTimeout(resolve, 500))
    if (!wsService.isConnected()) { message.error(t('chat.connectionFailed')); return }
  }

  // 构建消息内容
  // userMessage 已经在此之前声明过了，直接使用
  if (!userMessage) {
    userMessage = inputMessage.value.trim()
    if (!userMessage && attachedFileIds.value.length > 0) {
      userMessage = `请分析上传的 ${attachedFiles.value.length} 个文件`
    }
  }
  
  inputMessage.value = ''
  
  // 暂存当前发送的文件列表，用于清空 UI
  const currentFiles = [...attachedFiles.value]
  // 避免直接赋值给计算属性，使用辅助变量或修改原数组
  attachedFiles.value = [] // 立即清空 UI，提升体验

  messages.value.push({
    id: Date.now(),
    content: userMessage,
    messageType: 'user',
    conversationId: chatStore.currentConversation?.id || 0,
    files: currentFiles.map(f => ({ ...f }))
  })
  scrollToBottom()
  isLoading.value = true
  isRetrieving.value = true // 开始检索
  
  try {
    // 传递 fileIds, strictMode 和 privacyMode 参数给 WS 服务
    const sent = wsService.send(
      userMessage,
      chatStore.currentConversation?.id,
      currentFiles.filter(f => f.status === 'done' && f.id).map(f => f.id!),
      strictMode.value,
      privacyMode.value
    )
    if (!sent) {
      message.error(t('chat.sendFailed'))
      isLoading.value = false
      // 恢复文件列表（可选，如果发送失败让用户重试）
      attachedFiles.value = currentFiles
      return
    }
  } catch (error) {
    message.error(t('chat.networkError'))
    isLoading.value = false
    attachedFiles.value = currentFiles
  }
}

const clearChat = async () => {
  if (!currentConversationId.value) return
  
  try {
    const res = await clearConversationMessages(String(currentConversationId.value))
    if (res.data?.success) {
      messages.value = []
      message.success(t('chat.chatCleared'))
    }
  } catch (err) {
    message.error(t('chat.clearFailed'))
  }
}
const newConversation = () => {
  messages.value = []
  attachedFiles.value =[] // 切换会话时清空附件
  chatStore.setCurrentConversation(null)
  router.push({ query: {} })
  if (window.innerWidth < 768) sidebarOpen.value = false
}

const connectWebSocket = async () => {
  if (useSSE.value) return
  
  let userId = userStore.userInfo?.id
  if (!userId || userId <= 0) {
     try {
       await userStore.getUserInfo()
       userId = userStore.userInfo?.id
       await new Promise(resolve => setTimeout(resolve, 100))
     } catch (e) { return }
  }
  if (!userId || userId <= 0) return

  connectionStatus.value = 'connecting'
  
  // 先清理并重新挂载监听器
  wsService.off('connect'); wsService.off('disconnect'); wsService.off('error'); wsService.off('message'); wsService.off('chunk')

  wsService.on('connect', () => { 
    console.log("✅ WebSocket 已连通，更新状态")
    connectionStatus.value = 'connected' 
  })
  wsService.on('disconnect', () => { connectionStatus.value = 'disconnected' })
  wsService.on('error', (data) => {
    connectionStatus.value = 'error'
    if (data.content) message.error(data.content)
    isLoading.value = false
  })
  
  wsService.on('chunk', (data) => {
      isRetrieving.value = false // 收到第一块数据，取消检索动画
      if (data.conversationId && (!chatStore.currentConversation || chatStore.currentConversation.id !== data.conversationId)) {
           chatStore.setCurrentConversation({
             id: data.conversationId,
             title: data.title || (messages.value.find(m => m.messageType === 'user')?.content.slice(0, 20) || 'New Chat'),
             userId: userId!,
             createdAt: new Date().toISOString(),
             updatedAt: new Date().toISOString()
           })
           fetchConversations()
      }
      const lastMsg = messages.value[messages.value.length - 1]
      if (lastMsg && lastMsg.messageType === 'assistant' && lastMsg.id === data.messageId) {
          lastMsg.content += (data.content || '')
      } else {
        messages.value.push({ id: data.messageId, content: data.content || '', messageType: 'assistant', conversationId: data.conversationId || 0, sources: data.sources })
      }
      scrollToBottom()
  })

  wsService.on('message', (data) => {
    if (data.content) {
      isRetrieving.value = false // 收到消息表示检索结束
      const lastMsg = messages.value[messages.value.length - 1]
      const isCached = (data as any).is_cached;
      
      if (!lastMsg || lastMsg.id !== data.messageId) {
          messages.value.push({ 
            id: data.messageId, 
            content: data.content, 
            messageType: 'assistant', 
            conversationId: data.conversationId || chatStore.currentConversation?.id || 0, 
            sources: data.sources,
            isCached: isCached
          } as any)
      } else {
          lastMsg.content = data.content
          if (data.sources) { lastMsg.sources = data.sources }
          if (isCached) { (lastMsg as any).isCached = true }
      }
      isLoading.value = false
      scrollToBottom()
      fetchConversations()
    }
  })

  wsService.connect(userId, chatStore.currentConversation?.id)
}

const disconnectWebSocket = () => {
  if (wsService && typeof wsService.disconnect === 'function') wsService.disconnect()
  connectionStatus.value = 'disconnected'
}

const getConnectionStatusText = (status?: string) => {
  const s = status || effectiveConnectionStatus.value
  switch (s) {
    case 'connected': return t('chat.status.connected')
    case 'connecting': return t('chat.status.connecting')
    case 'disconnected': return t('chat.status.disconnected')
    case 'error': return t('chat.status.error')
    default: return t('chat.status.unknown')
  }
}

const isBoundMode = computed(() => {
  return chatStore.currentConversation?.settings?.bound_document_ids && chatStore.currentConversation.settings.bound_document_ids.length > 0
})

const handleUnbind = async () => {
  try {
    await chatStore.unbindDocuments()
    message.success(t('chat.unbindSuccess'))
  } catch (error) {
    message.error(t('chat.unbindFailed'))
  }
}

const loadConversation = async (id: string) => {
  isLoading.value = true
  try {
    const res = await getConversationMessages(id)
    if (res.data) {
      const data = (res.data as any).data || res.data
      const msgs = data.messages ||[]
      chatStore.setCurrentConversation({
        id: data.id, title: data.title, userId: userStore.userInfo?.id || 0,
        createdAt: data.created_at, updatedAt: data.updated_at || data.created_at,
        settings: data.settings
      })
      messages.value = msgs.map((m: any) => ({
        id: m.id, content: m.content, messageType: m.message_type || m.messageType,
        conversationId: data.id, createdAt: m.created_at, sources: m.sources, files: m.files
      }))
    }
  } catch (e) {
    message.error(t('chat.historyFailed'))
  } finally {
    isLoading.value = false
    scrollToBottom('auto', true)
  }
}



onMounted(async () => {
  const token = userStore.token || getToken()
  if (!token) { router.push({ name: 'Login' }); return }
  checkScreenSize(); window.addEventListener('resize', checkScreenSize)
  await fetchConversations()
  const conversationId = route.query.conversation_id as string
  if (conversationId) await loadConversation(conversationId)

  // 处理提示词参数 - 从提示词管理页面跳转过来
  const promptContent = route.query.prompt as string
  const promptName = route.query.promptName as string
  if (promptContent) {
    // 清除 URL 中的提示词参数，避免刷新后重复
    router.replace({ query: { ...route.query, prompt: undefined, promptName: undefined } })

    // 自动填充提示词作为系统设定，并提示用户开始对话
    inputMessage.value = ''
    message.info(`已应用提示词模板"${promptName || '未命名'}"，请开始对话`)

    // 将提示词设置为隐私模式下的系统提示（存储到本地，发送时使用）
    localStorage.setItem('activeSystemPrompt', promptContent)
    localStorage.setItem('activeSystemPromptName', promptName || '')
  }

  // ⚡ 增加状态轮询，每秒同步一次真实连接状态
  statusTimer = setInterval(() => {
    if (wsService.isConnected()) {
      connectionStatus.value = 'connected'
    } else if (wsService.getConnectionStatus() === 'connecting') {
      connectionStatus.value = 'connecting'
    }
  }, 1000)

  watch(() => userStore.userInfo?.id, () => { connectWebSocket() }, { immediate: true })
})

onUnmounted(() => {
  window.removeEventListener('resize', checkScreenSize)
  disconnectWebSocket()
  if (statusTimer) clearInterval(statusTimer) // ⚡ 清理定时器
})
</script>

<style scoped>
/* Scrollbar Styles */
.scrollbar-thin::-webkit-scrollbar {
  width: 4px;
}
.scrollbar-thin::-webkit-scrollbar-track {
  background: transparent;
}
.scrollbar-thin::-webkit-scrollbar-thumb {
  background-color: rgba(156, 163, 175, 0.3);
  border-radius: 20px;
}
.dark .scrollbar-thin::-webkit-scrollbar-thumb {
  background-color: rgba(156, 163, 175, 0.1);
}
.scrollbar-thin:hover::-webkit-scrollbar-thumb {
  background-color: rgba(156, 163, 175, 0.5);
}
</style>

<style>
/* Markdown Styles */
.markdown-body {
  color: inherit;
  font-size: 0.95rem;
  line-height: 1.6;
}
.markdown-body p {
  margin-bottom: 0.8em;
}
.markdown-body p:last-child {
  margin-bottom: 0;
}
.markdown-body pre {
  background-color: #f6f8fa;
  border-radius: 6px;
  padding: 12px;
  margin: 10px 0;
  overflow-x: auto;
}
.dark .markdown-body pre {
  background-color: #1f2937; /* gray-800 */
  color: #e5e7eb; /* gray-200 */
}
.markdown-body code {
  font-family: 'SFMono-Regular', Consolas, 'Liberation Mono', Menlo, monospace;
  background-color: rgba(175, 184, 193, 0.2);
  padding: 0.2em 0.4em;
  border-radius: 4px;
  font-size: 85%;
}
.dark .markdown-body code {
  background-color: rgba(110, 118, 129, 0.4);
  color: #e5e7eb;
}
.markdown-body pre code {
  background-color: transparent;
  padding: 0;
  color: inherit;
}
.markdown-body ul, .markdown-body ol {
  padding-left: 1.5em;
  margin-bottom: 0.8em;
}
.markdown-body li {
  margin-bottom: 0.2em;
}

/* Animations */
@keyframes fade-in {
  from { opacity: 0; transform: translateY(10px); }
  to { opacity: 1; transform: translateY(0); }
}
.animate-fade-in {
  animation: fade-in 0.5s ease-out forwards;
}

@keyframes slide-in-bottom {
  from { opacity: 0; transform: translateY(20px); }
  to { opacity: 1; transform: translateY(0); }
}
.animate-slide-in-bottom {
  animation: slide-in-bottom 0.3s ease-out forwards;
}
</style>
