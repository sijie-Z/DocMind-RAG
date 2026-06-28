<template>
  <div class="h-full flex">
    <!-- 左侧节点面板 -->
    <div class="w-72 bg-white dark:bg-gray-800 border-r border-gray-200 dark:border-gray-700 flex flex-col">
      <div class="p-4 border-b border-gray-200 dark:border-gray-700">
        <h3 class="font-semibold text-gray-800 dark:text-white flex items-center gap-2">
          <n-icon><AppsOutline /></n-icon>
          节点库
        </h3>
      </div>
      <div class="flex-1 overflow-y-auto p-3">
        <!-- 动态加载节点定义 -->
        <template v-if="hasDynamicDefs">
          <n-collapse :default-expanded-names="['llm', 'tool', 'io', 'logic', 'data']">
            <n-collapse-item v-for="cat in Object.keys(nodeDefsByCategory)" :key="cat" :name="cat">
              <template #header>
                <n-icon class="mr-1"><component :is="CATEGORY_ICONS[cat] || AppsOutline" /></n-icon>
                {{ CATEGORY_LABELS[cat] || cat }}
              </template>
              <div class="space-y-2">
                <div
                  v-for="node in nodeDefsByCategory[cat]"
                  :key="node.node_type"
                  class="p-3 bg-gray-50 dark:bg-gray-700/50 rounded-lg cursor-grab hover:bg-blue-50 dark:hover:bg-blue-900/20 border border-gray-200 dark:border-gray-600 hover:border-blue-300 dark:hover:border-blue-500 transition-all"
                  draggable="true"
                  @dragstart="onDragStart($event, node)"
                >
                  <div class="flex items-center gap-2">
                    <n-icon size="20"><component :is="getNodeIconComponent(node.node_type)" /></n-icon>
                    <div>
                      <div class="font-medium text-gray-700 dark:text-gray-200">{{ node.name }}</div>
                      <div class="text-xs text-gray-500">{{ node.description }}</div>
                    </div>
                  </div>
                </div>
              </div>
            </n-collapse-item>
          </n-collapse>
        </template>

        <!-- 回退：API 加载失败时使用硬编码节点列表 -->
        <template v-else>
          <n-collapse :default-expanded-names="['llm', 'tool', 'io', 'logic', 'data']">
          <n-collapse-item name="llm">
            <template #header><n-icon class="mr-1"><HardwareChipOutline /></n-icon> 大模型节点</template>
            <div class="space-y-2">
              <div
                v-for="node in llmNodes"
                :key="node.node_type"
                class="p-3 bg-gray-50 dark:bg-gray-700/50 rounded-lg cursor-grab hover:bg-blue-50 dark:hover:bg-blue-900/20 border border-gray-200 dark:border-gray-600 hover:border-blue-300 dark:hover:border-blue-500 transition-all"
                draggable="true"
                @dragstart="onDragStart($event, node)"
              >
                <div class="flex items-center gap-2">
                  <n-icon size="20"><component :is="getNodeIconComponent(node.node_type)" /></n-icon>
                  <div>
                    <div class="font-medium text-gray-700 dark:text-gray-200">{{ node.name }}</div>
                    <div class="text-xs text-gray-500">{{ node.description }}</div>
                  </div>
                </div>
              </div>
            </div>
          </n-collapse-item>
          <n-collapse-item name="tool">
            <template #header><n-icon class="mr-1"><SettingsOutline /></n-icon> 工具节点</template>
            <div class="space-y-2">
              <div
                v-for="node in toolNodes"
                :key="node.node_type"
                class="p-3 bg-gray-50 dark:bg-gray-700/50 rounded-lg cursor-grab hover:bg-amber-50 dark:hover:bg-amber-900/20 border border-gray-200 dark:border-gray-600 hover:border-amber-300 dark:hover:border-amber-500 transition-all"
                draggable="true"
                @dragstart="onDragStart($event, node)"
              >
                <div class="flex items-center gap-2">
                  <n-icon size="20"><component :is="getNodeIconComponent(node.node_type)" /></n-icon>
                  <div>
                    <div class="font-medium text-gray-700 dark:text-gray-200">{{ node.name }}</div>
                    <div class="text-xs text-gray-500">{{ node.description }}</div>
                  </div>
                </div>
              </div>
            </div>
          </n-collapse-item>
          <n-collapse-item name="io">
            <template #header><n-icon class="mr-1"><EnterOutline /></n-icon> 输入输出</template>
            <div class="space-y-2">
              <div
                v-for="node in ioNodes"
                :key="node.node_type"
                class="p-3 bg-gray-50 dark:bg-gray-700/50 rounded-lg cursor-grab hover:bg-emerald-50 dark:hover:bg-emerald-900/20 border border-gray-200 dark:border-gray-600 hover:border-emerald-300 dark:hover:border-emerald-500 transition-all"
                draggable="true"
                @dragstart="onDragStart($event, node)"
              >
                <div class="flex items-center gap-2">
                  <n-icon size="20"><component :is="getNodeIconComponent(node.node_type)" /></n-icon>
                  <div>
                    <div class="font-medium text-gray-700 dark:text-gray-200">{{ node.name }}</div>
                    <div class="text-xs text-gray-500">{{ node.description }}</div>
                  </div>
                </div>
              </div>
            </div>
          </n-collapse-item>
          <n-collapse-item name="logic">
            <template #header><n-icon class="mr-1"><GitBranchOutline /></n-icon> 逻辑控制</template>
            <div class="space-y-2">
              <div
                v-for="node in logicNodes"
                :key="node.node_type"
                class="p-3 bg-gray-50 dark:bg-gray-700/50 rounded-lg cursor-grab hover:bg-cyan-50 dark:hover:bg-cyan-900/20 border border-gray-200 dark:border-gray-600 hover:border-cyan-300 dark:hover:border-cyan-500 transition-all"
                draggable="true"
                @dragstart="onDragStart($event, node)"
              >
                <div class="flex items-center gap-2">
                  <n-icon size="20"><component :is="getNodeIconComponent(node.node_type)" /></n-icon>
                  <div>
                    <div class="font-medium text-gray-700 dark:text-gray-200">{{ node.name }}</div>
                    <div class="text-xs text-gray-500">{{ node.description }}</div>
                  </div>
                </div>
              </div>
            </div>
          </n-collapse-item>
          <n-collapse-item name="data">
            <template #header><n-icon class="mr-1"><ServerOutline /></n-icon> 数据处理</template>
            <div class="space-y-2">
              <div
                v-for="node in dataNodes"
                :key="node.node_type"
                class="p-3 bg-gray-50 dark:bg-gray-700/50 rounded-lg cursor-grab hover:bg-teal-50 dark:hover:bg-teal-900/20 border border-gray-200 dark:border-gray-600 hover:border-teal-300 dark:hover:border-teal-500 transition-all"
                draggable="true"
                @dragstart="onDragStart($event, node)"
              >
                <div class="flex items-center gap-2">
                  <n-icon size="20"><component :is="getNodeIconComponent(node.node_type)" /></n-icon>
                  <div>
                    <div class="font-medium text-gray-700 dark:text-gray-200">{{ node.name }}</div>
                    <div class="text-xs text-gray-500">{{ node.description }}</div>
                  </div>
                </div>
              </div>
            </div>
          </n-collapse-item>
        </n-collapse>
        </template>
      </div>

      <!-- 快速模板 -->
      <div class="p-3 border-t border-gray-200 dark:border-gray-700">
        <div class="text-xs text-gray-500 mb-2">快速模板</div>
        <div class="space-y-2">
          <n-button size="small" block quaternary @click="loadTemplate('rag')">
            <template #icon><n-icon><SearchOutline /></n-icon></template>
            RAG 问答流程
          </n-button>
          <n-button size="small" block quaternary @click="loadTemplate('chat')">
            <template #icon><n-icon><ChatbubbleEllipsesOutline /></n-icon></template>
            多轮对话流程
          </n-button>
          <n-button size="small" block quaternary @click="loadTemplate('agent')">
            <template #icon><n-icon><HardwareChipOutline /></n-icon></template>
            Agent 记忆流程
          </n-button>
          <n-button size="small" block quaternary @click="loadTemplate('report')">
            <template #icon><n-icon><FlashOutline /></n-icon></template>
            报告生成流程
          </n-button>
        </div>
      </div>
    </div>

    <!-- 中间画布区域 -->
    <div class="flex-1 flex flex-col">
      <!-- 工具栏 -->
      <div class="h-14 bg-white dark:bg-gray-800 border-b border-gray-200 dark:border-gray-700 flex items-center justify-between px-4">
        <div class="flex items-center gap-3">
          <n-input v-model:value="workflowStore.workflowName" placeholder="工作流名称" style="width: 200px" />
          <n-select v-model:value="engineType" :options="engineTypeOptions" style="width: 140px" size="small" />
          <n-button type="primary" @click="saveWorkflow" :loading="saving">
            <template #icon><n-icon><SaveOutline /></n-icon></template>
            保存
          </n-button>
          <n-button quaternary @click="handleCreateNew">
            <template #icon><n-icon><AddOutline /></n-icon></template>
            新建
          </n-button>
          <n-button quaternary @click="openLoadModal">
            <template #icon><n-icon><FolderOpenOutline /></n-icon></template>
            加载
          </n-button>
          <n-button type="info" @click="openDebugPanel" :disabled="workflowStore.nodes.length === 0">
            <template #icon><n-icon><PlayOutline /></n-icon></template>
            调试运行
          </n-button>
          <n-button quaternary @click="showLLMConfig = true">
            <template #icon><n-icon><SettingsOutline /></n-icon></template>
            LLM配置
          </n-button>
        </div>
        <div class="flex items-center gap-4">
          <div class="flex items-center gap-2 text-xs text-gray-500">
            <span>节点: {{ workflowStore.nodes.length }}</span>
            <span>连线: {{ workflowStore.edges.length }}</span>
          </div>
          <n-button quaternary size="small" @click="fitView">
            <template #icon><n-icon><ExpandOutline /></n-icon></template>
          </n-button>
          <n-button quaternary size="small" @click="clearCanvas">
            <template #icon><n-icon><TrashOutline /></n-icon></template>
          </n-button>
        </div>
      </div>

      <!-- Vue Flow 画布 -->
      <div class="flex-1 relative" @drop="onDrop" @dragover.prevent>
        <VueFlow
          v-model:nodes="nodes"
          v-model:edges="edges"
          :default-viewport="{ zoom: 1, x: 0, y: 0 }"
          :min-zoom="0.2"
          :max-zoom="4"
          :snap-to-grid="true"
          :snap-grid="[20, 20]"
          :connection-line-style="{ strokeWidth: 2, stroke: '#3b82f6' }"
          :default-edge-options="defaultEdgeOptions"
          @node-click="onNodeClick"
          @connect="onConnect"
          class="bg-gray-100 dark:bg-gray-900"
          ref="vueFlowRef"
        >
          <!-- 输入输出节点 -->
          <template #node-input="nodeProps">
            <InputNode :data="nodeProps.data" :selected="selectedNodeId === nodeProps.id" :executing="executingNodeId === nodeProps.id" />
          </template>
          <template #node-output="nodeProps">
            <OutputNode :data="nodeProps.data" :selected="selectedNodeId === nodeProps.id" :executing="executingNodeId === nodeProps.id" />
          </template>

          <!-- LLM 节点 -->
          <template #node-llm="nodeProps">
            <LLMNode :data="nodeProps.data" :selected="selectedNodeId === nodeProps.id" :executing="executingNodeId === nodeProps.id" :model="(nodeProps.data?.provider as string) || 'LLM'" color="purple" />
          </template>
          <template #node-llm_openai="nodeProps">
            <LLMNode :data="nodeProps.data" :selected="selectedNodeId === nodeProps.id" :executing="executingNodeId === nodeProps.id" model="OpenAI GPT" color="green" />
          </template>
          <template #node-llm_deepseek="nodeProps">
            <LLMNode :data="nodeProps.data" :selected="selectedNodeId === nodeProps.id" :executing="executingNodeId === nodeProps.id" model="DeepSeek" color="blue" />
          </template>
          <template #node-llm_qwen="nodeProps">
            <LLMNode :data="nodeProps.data" :selected="selectedNodeId === nodeProps.id" :executing="executingNodeId === nodeProps.id" model="通义千问" color="orange" />
          </template>

          <!-- 工具节点 -->
          <template #node-tool_search="nodeProps">
            <ToolNode :data="nodeProps.data" :selected="selectedNodeId === nodeProps.id" :executing="executingNodeId === nodeProps.id" name="知识库检索" icon="SearchOutline" />
          </template>
          <template #node-tool_tts="nodeProps">
            <ToolNode :data="nodeProps.data" :selected="selectedNodeId === nodeProps.id" :executing="executingNodeId === nodeProps.id" name="语音合成" icon="MusicalNotesOutline" />
          </template>

          <!-- 逻辑节点 -->
          <template #node-condition="nodeProps">
            <ConditionNode :data="nodeProps.data" :selected="selectedNodeId === nodeProps.id" :executing="executingNodeId === nodeProps.id" />
          </template>
          <template #node-router="nodeProps">
            <ConditionNode :data="nodeProps.data" :selected="selectedNodeId === nodeProps.id" :executing="executingNodeId === nodeProps.id" />
          </template>

          <!-- 数据节点 -->
          <template #node-memory="nodeProps">
            <MemoryNode :data="nodeProps.data" :selected="selectedNodeId === nodeProps.id" :executing="executingNodeId === nodeProps.id" />
          </template>
          <template #node-code="nodeProps">
            <CodeNode :data="nodeProps.data" :selected="selectedNodeId === nodeProps.id" :executing="executingNodeId === nodeProps.id" />
          </template>
          <template #node-api_call="nodeProps">
            <ApiNode :data="nodeProps.data" :selected="selectedNodeId === nodeProps.id" :executing="executingNodeId === nodeProps.id" />
          </template>
          <template #node-transform="nodeProps">
            <TransformNode :data="nodeProps.data" :selected="selectedNodeId === nodeProps.id" :executing="executingNodeId === nodeProps.id" />
          </template>

          <Background :gap="20" :size="1" pattern-color="#aaa" />
          <Controls />
          <MiniMap :node-color="nodeColor" />

          <!-- 执行状态指示 -->
          <div v-if="executingNodeId" class="absolute top-2 left-2 bg-blue-500 text-white px-3 py-1.5 rounded-lg text-sm z-10 flex items-center gap-2 shadow-lg">
            <n-spin size="small" />
            <span>正在执行节点...</span>
          </div>
        </VueFlow>
      </div>
    </div>

    <!-- 右侧属性面板 -->
    <div class="w-80 bg-white dark:bg-gray-800 border-l border-gray-200 dark:border-gray-700 flex flex-col">
      <div class="p-4 border-b border-gray-200 dark:border-gray-700">
        <h3 class="font-semibold text-gray-800 dark:text-white flex items-center gap-2">
          <n-icon><SettingsOutline /></n-icon>
          节点属性
        </h3>
      </div>
      <div class="flex-1 overflow-y-auto p-4">
        <div v-if="!selectedNode" class="text-center text-gray-400 py-8">
          <n-icon size="48" class="mb-2 opacity-30"><LocateOutline /></n-icon>
          <p>点击节点查看属性</p>
        </div>
        <div v-else class="space-y-4">
          <!-- 节点基本信息 -->
          <div class="flex items-center gap-2 pb-3 border-b border-gray-200 dark:border-gray-700">
            <n-icon size="24"><component :is="getNodeIconComponent(selectedNode.type)" /></n-icon>
            <div>
              <div class="font-medium text-gray-800 dark:text-white">{{ getNodeLabel(selectedNode.type) }}</div>
              <div class="text-xs text-gray-500">ID: {{ selectedNode.id }}</div>
            </div>
          </div>

          <!-- LLM 节点配置 -->
          <template v-if="selectedNode.type?.startsWith('llm_') || selectedNode.type === 'llm'">
            <!-- 通用 llm 节点：provider 下拉选择 -->
            <div v-if="selectedNode.type === 'llm'" class="mb-4">
              <label class="text-sm font-medium text-gray-600 dark:text-gray-300">选择供应商</label>
              <n-select
                v-model:value="nodeData.provider"
                :options="llmConfigStore.providers.filter(p => p.key !== 'llm').map(p => ({ label: p.label, value: p.key }))"
                placeholder="选择 LLM 供应商..."
                class="mt-1"
              />
            </div>

            <!-- 全局配置引用 -->
            <div class="mb-4 p-3 bg-blue-50 dark:bg-blue-900/20 rounded-lg">
              <div class="flex items-center justify-between mb-2">
                <label class="text-sm font-medium text-gray-700 dark:text-gray-300">使用全局配置</label>
                <n-switch v-model:value="nodeData.useGlobalConfig" />
              </div>
              <template v-if="nodeData.useGlobalConfig">
                <n-select
                  v-model:value="nodeData.configProvider"
                  :options="llmConfigStore.getOptionsForProvider(getLlmNodeProvider(selectedNode))"
                  placeholder="选择已保存的配置..."
                  class="mt-1"
                  clearable
                />
                <div v-if="nodeData.configProvider" class="text-xs text-gray-500 mt-1">
                  API Key/URL/Model 将从所选全局配置自动加载
                </div>
              </template>
              <template v-else>
                <div class="text-xs text-orange-500">将使用下方手动填写的 API 信息</div>
              </template>
            </div>

            <!-- Skill 选择器 -->
            <div class="mb-4">
              <label class="text-sm font-medium text-gray-600 dark:text-gray-300">关联 Skill</label>
              <n-select
                v-model:value="nodeData.skillName"
                :options="skills.map(s => ({ label: `${s.name} (成功率 ${Math.round(s.success_rate * 100)}%)`, value: s.name }))"
                placeholder="选择 Skill（可选）..."
                class="mt-1"
                clearable
                filterable
              />
              <div class="text-xs text-gray-400 mt-1">Skill 指南将注入到 LLM 的 system prompt</div>
            </div>

            <n-divider />

            <div>
              <label class="text-sm font-medium text-gray-600 dark:text-gray-300">系统提示词</label>
              <n-input
                v-model:value="nodeData.systemPrompt"
                type="textarea"
                placeholder="定义AI的角色和行为..."
                :autosize="{ minRows: 4, maxRows: 10 }"
                class="mt-1"
              />
            </div>
            <div class="grid grid-cols-2 gap-3">
              <div>
                <label class="text-sm font-medium text-gray-600 dark:text-gray-300">温度</label>
                <n-input-number v-model:value="nodeData.temperature" :min="0" :max="2" :step="0.1" class="mt-1 w-full" />
              </div>
              <div>
                <label class="text-sm font-medium text-gray-600 dark:text-gray-300">最大Token</label>
                <n-input-number v-model:value="nodeData.maxTokens" :min="100" :max="8000" :step="100" class="mt-1 w-full" />
              </div>
            </div>

            <!-- 参数引用系统 -->
            <n-divider />
            <div class="mb-3">
              <div class="flex items-center justify-between mb-2">
                <label class="text-sm font-medium text-gray-700 dark:text-gray-300">输入参数</label>
                <n-button size="tiny" quaternary @click="addLlmInputParam">
                  <template #icon><n-icon><AddOutline /></n-icon></template>
                </n-button>
              </div>
              <div v-if="!nodeData.inputParams || nodeData.inputParams.length === 0" class="text-gray-400 text-xs text-center py-2 border border-dashed rounded">
                点击 + 添加输入参数
              </div>
              <div v-for="(param, idx) in (nodeData.inputParams || [])" :key="idx" class="flex items-center gap-2 mb-2">
                <n-input v-model:value="param.name" placeholder="参数名" size="small" style="width:80px" />
                <n-select v-model:value="param.type" size="small" style="width:70px" :options="[{label:'输入',value:'input'},{label:'引用',value:'reference'}]" />
                <n-input v-if="param.type === 'input'" v-model:value="param.value" placeholder="值" size="small" style="flex:1" />
                <n-select v-else v-model:value="param.referenceNode" size="small" style="flex:1" :options="getReferenceableParams().map(p => ({label:p.label, value:p.value}))" placeholder="选择引用" />
                <n-button size="tiny" text type="error" @click="removeLlmInputParam(idx)"><template #icon><n-icon><CloseOutline /></n-icon></template></n-button>
              </div>
            </div>
            <div class="mb-2">
              <div class="text-xs text-gray-400">
                💡 Prompt 中可使用 <code v-text="'{{paramName}}'"></code> 引用输入参数
              </div>
            </div>

            <!-- LLM 输出参数 -->
            <n-divider />
            <div class="mb-3">
              <div class="flex items-center justify-between mb-2">
                <label class="text-sm font-medium text-gray-700 dark:text-gray-300">输出参数</label>
                <n-button size="tiny" quaternary @click="addLlmOutputParam">
                  <template #icon><n-icon><AddOutline /></n-icon></template>
                </n-button>
              </div>
              <div v-if="!nodeData.outputParams || nodeData.outputParams.length === 0" class="text-gray-400 text-xs text-center py-2 border border-dashed rounded">
                点击 + 添加输出参数，如 output / tokens
              </div>
              <div v-for="(param, idx) in (nodeData.outputParams || [])" :key="idx" class="flex items-center gap-2 mb-2">
                <n-input v-model:value="param.name" placeholder="变量名" size="small" style="width:100px" />
                <n-input v-model:value="'string'" disabled size="small" style="width:70px" />
                <n-input v-model:value="param.description" placeholder="描述（可选）" size="small" style="flex:1" />
                <n-button size="tiny" text type="error" @click="removeLlmOutputParam(idx)"><template #icon><n-icon><CloseOutline /></n-icon></template></n-button>
              </div>
            </div>
          </template>

          <!-- 输入节点配置 -->
          <template v-if="selectedNode.type === 'input'">
            <!-- 参数定义列表 -->
            <div class="mb-3">
              <div class="flex items-center justify-between mb-2">
                <label class="text-sm font-medium text-gray-700 dark:text-gray-300">参数定义</label>
                <n-button size="tiny" quaternary @click="addInputParam">
                  <template #icon><n-icon><AddOutline /></n-icon></template>
                </n-button>
              </div>
              <div v-if="!nodeData.inputParams || nodeData.inputParams.length === 0" class="text-gray-400 text-xs text-center py-2 border border-dashed rounded">
                默认为 {'{{'} input {'}}'}，点击 + 添加自定义参数
              </div>
              <div v-for="(param, idx) in (nodeData.inputParams || [])" :key="idx" class="flex items-center gap-2 mb-2">
                <n-input v-model:value="param.name" placeholder="参数名" size="small" style="width:100px" />
                <n-input v-model:value="param.value" placeholder="默认值" size="small" style="flex:1" />
                <n-select v-model:value="param.type" size="small" style="width:80px" :options="[{label:'String',value:'string'},{label:'Number',value:'number'}]" />
                <n-button size="tiny" text type="error" @click="removeInputParam(idx)"><template #icon><n-icon><CloseOutline /></n-icon></template></n-button>
              </div>
            </div>
            <div>
              <label class="text-sm font-medium text-gray-600 dark:text-gray-300">提示词模板</label>
              <n-input
                v-model:value="nodeData.prompt"
                type="textarea"
                placeholder="输入提示词模板..."
                :autosize="{ minRows: 4, maxRows: 8 }"
                class="mt-1"
              />
              <div class="text-xs text-gray-400 mt-1" v-text="'支持变量: {{paramName}}'"></div>
            </div>
          </template>

          <!-- 输出节点配置 -->
          <template v-if="selectedNode.type === 'output'">
            <div class="mb-3">
              <div class="flex items-center justify-between mb-2">
                <label class="text-sm font-medium text-gray-700 dark:text-gray-300">输出参数</label>
                <n-button size="tiny" quaternary @click="addOutputParam">
                  <template #icon><n-icon><AddOutline /></n-icon></template>
                </n-button>
              </div>
              <div v-if="!nodeData.outputParams || nodeData.outputParams.length === 0" class="text-gray-400 text-xs text-center py-2 border border-dashed rounded">
                点击 + 添加输出参数
              </div>
              <div v-for="(param, idx) in (nodeData.outputParams || [])" :key="idx" class="flex items-center gap-2 mb-2">
                <n-input v-model:value="param.name" placeholder="参数名" size="small" style="width:80px" />
                <n-select v-model:value="param.type" size="small" style="width:70px" :options="[{label:'输入',value:'input'},{label:'引用',value:'reference'}]" />
                <n-input v-if="param.type === 'input'" v-model:value="param.value" placeholder="值" size="small" style="flex:1" />
                <n-select v-else v-model:value="param.referenceNode" size="small" style="flex:1" :options="getReferenceableParams().map(p => ({label:p.label, value:p.value}))" placeholder="选择引用" />
                <n-button size="tiny" text type="error" @click="removeOutputParam(idx)"><template #icon><n-icon><CloseOutline /></n-icon></template></n-button>
              </div>
            </div>
            <div>
              <label class="text-sm font-medium text-gray-600 dark:text-gray-300">回答模板</label>
              <n-input
                v-model:value="nodeData.responseContent"
                type="textarea"
                placeholder="使用 {{paramName}} 引用参数..."
                :autosize="{ minRows: 4, maxRows: 8 }"
                class="mt-1"
              />
              <div class="text-xs text-gray-400 mt-1" v-text="'支持变量: {{paramName}}'"></div>
            </div>
          </template>

          <!-- TTS 节点配置 -->
          <template v-if="selectedNode.type === 'tool_tts'">
            <div class="mb-3">
              <div class="flex items-center justify-between mb-2">
                <label class="text-sm font-medium text-gray-700 dark:text-gray-300">输入参数</label>
                <n-button size="tiny" quaternary @click="addTtsInputParam">
                  <template #icon><n-icon><AddOutline /></n-icon></template>
                </n-button>
              </div>
              <div v-if="!nodeData.inputParams || nodeData.inputParams.length === 0" class="text-gray-400 text-xs text-center py-2 border border-dashed rounded">
                点击 + 添加，如 text（需要合成的文本）
              </div>
              <div v-for="(param, idx) in (nodeData.inputParams || [])" :key="idx" class="flex items-center gap-2 mb-2">
                <n-input v-model:value="param.name" placeholder="参数名" size="small" style="width:80px" />
                <n-select v-model:value="param.type" size="small" style="width:70px" :options="[{label:'输入',value:'input'},{label:'引用',value:'reference'}]" />
                <n-input v-if="param.type === 'input'" v-model:value="param.value" placeholder="值" size="small" style="flex:1" />
                <n-select v-else v-model:value="param.referenceNode" size="small" style="flex:1" :options="getReferenceableParams().map(p => ({label:p.label, value:p.value}))" placeholder="选择引用" />
                <n-button size="tiny" text type="error" @click="removeTtsInputParam(idx)"><template #icon><n-icon><CloseOutline /></n-icon></template></n-button>
              </div>
            </div>
            <div class="mb-3">
              <div class="flex items-center justify-between mb-2">
                <label class="text-sm font-medium text-gray-700 dark:text-gray-300">输出参数</label>
                <n-button size="tiny" quaternary @click="addTtsOutputParam">
                  <template #icon><n-icon><AddOutline /></n-icon></template>
                </n-button>
              </div>
              <div v-if="!nodeData.outputParams || nodeData.outputParams.length === 0" class="text-gray-400 text-xs text-center py-2 border border-dashed rounded">
                默认: audioUrl, fileName, output
              </div>
              <div v-for="(param, idx) in (nodeData.outputParams || [])" :key="idx" class="flex items-center gap-2 mb-2">
                <n-input v-model:value="param.name" placeholder="参数名" size="small" style="width:100px" />
                <n-input v-model:value="param.value" placeholder="引用字段" size="small" style="flex:1" />
                <n-button size="tiny" text type="error" @click="removeTtsOutputParam(idx)"><template #icon><n-icon><CloseOutline /></n-icon></template></n-button>
              </div>
            </div>
            <div>
              <label class="text-sm font-medium text-gray-600 dark:text-gray-300">音色</label>
              <n-select v-model:value="nodeData.voice" :options="ttsVoiceOptions" class="mt-1" placeholder="选择音色" />
            </div>
            <div class="mt-3">
              <label class="text-sm font-medium text-gray-600 dark:text-gray-300">语言</label>
              <n-select v-model:value="nodeData.languageType" :options="[{ label: 'Auto', value: 'Auto' }]" class="mt-1" />
            </div>
            <div class="mt-3">
              <label class="text-sm font-medium text-gray-600 dark:text-gray-300">API Key</label>
              <n-input v-model:value="nodeData.ttsApiKey" type="password" placeholder="阿里百炼 API Key" show-password-on="click" class="mt-1" />
            </div>
            <div class="mt-3">
              <label class="text-sm font-medium text-gray-600 dark:text-gray-300">模型</label>
              <n-input v-model:value="nodeData.ttsModel" placeholder="如 qwen3-tts-flash" class="mt-1" />
            </div>
          </template>

          <!-- 知识库检索配置 -->
          <template v-if="selectedNode.type === 'tool_search'">
            <div class="grid grid-cols-2 gap-3">
              <div>
                <label class="text-sm font-medium text-gray-600 dark:text-gray-300">返回数量</label>
                <n-input-number v-model:value="nodeData.topK" :min="1" :max="20" class="mt-1 w-full" />
              </div>
              <div>
                <label class="text-sm font-medium text-gray-600 dark:text-gray-300">相似度阈值</label>
                <n-input-number v-model:value="nodeData.scoreThreshold" :min="0" :max="1" :step="0.1" class="mt-1 w-full" />
              </div>
            </div>
          </template>

          <!-- 记忆节点配置 -->
          <template v-if="selectedNode.type === 'memory'">
            <div>
              <label class="text-sm font-medium text-gray-600 dark:text-gray-300">记忆类型</label>
              <n-select v-model:value="nodeData.memoryType" :options="memoryTypeOptions" class="mt-1" />
            </div>
            <div>
              <label class="text-sm font-medium text-gray-600 dark:text-gray-300">操作类型</label>
              <n-select v-model:value="nodeData.action" :options="memoryActionOptions" class="mt-1" />
            </div>
          </template>

          <!-- 条件分支配置 -->
          <template v-if="selectedNode.type === 'condition' || selectedNode.type === 'router'">
            <div>
              <label class="text-sm font-medium text-gray-600 dark:text-gray-300">条件表达式</label>
              <n-input
                v-model:value="nodeData.condition"
                placeholder="如: contains(text, '翻译')"
                class="mt-1"
              />
              <div class="text-xs text-gray-400 mt-1">
                支持函数: contains(), starts_with(), ends_with()
              </div>
            </div>
            <div class="p-2 bg-cyan-50 dark:bg-cyan-900/20 rounded text-xs">
              <div class="font-medium mb-1">连接多条边时：</div>
              <div>• 使用边的 label 标记分支名</div>
              <div>• 如 "翻译"、"代码"、"总结"</div>
            </div>
          </template>

          <!-- API调用节点配置 -->
          <template v-if="selectedNode.type === 'api_call'">
            <div>
              <label class="text-sm font-medium text-gray-600 dark:text-gray-300">请求方法</label>
              <n-select v-model:value="nodeData.method" :options="httpMethods" class="mt-1" />
            </div>
            <div>
              <label class="text-sm font-medium text-gray-600 dark:text-gray-300">URL</label>
              <n-input v-model:value="nodeData.url" placeholder="https://api.example.com" class="mt-1" />
            </div>
            <div>
              <label class="text-sm font-medium text-gray-600 dark:text-gray-300">超时时间(秒)</label>
              <n-input-number v-model:value="nodeData.timeout" :min="1" :max="60" class="mt-1 w-full" />
            </div>
          </template>

          <!-- 代码执行节点配置 -->
          <template v-if="selectedNode.type === 'code'">
            <div>
              <label class="text-sm font-medium text-gray-600 dark:text-gray-300">语言</label>
              <n-select v-model:value="nodeData.language" :options="codeLanguages" class="mt-1" />
            </div>
            <div>
              <label class="text-sm font-medium text-gray-600 dark:text-gray-300">代码</label>
              <n-input
                v-model:value="nodeData.code"
                type="textarea"
                placeholder="# Python 代码&#10;result = input.get('text', '')"
                :autosize="{ minRows: 6, maxRows: 15 }"
                class="mt-1 font-mono"
              />
            </div>
          </template>

          <!-- 数据转换节点配置 -->
          <template v-if="selectedNode.type === 'transform'">
            <div>
              <label class="text-sm font-medium text-gray-600 dark:text-gray-300">转换类型</label>
              <n-select v-model:value="nodeData.transformType" :options="transformTypes" class="mt-1" />
            </div>
            <div v-if="nodeData?.transformType === 'json_extract'">
              <label class="text-sm font-medium text-gray-600 dark:text-gray-300">JSON路径</label>
              <n-input v-model:value="nodeData.jsonPath" placeholder="$.data.result" class="mt-1" />
            </div>
            <div v-if="nodeData?.transformType === 'regex_extract'">
              <label class="text-sm font-medium text-gray-600 dark:text-gray-300">正则表达式</label>
              <n-input v-model:value="nodeData.pattern" placeholder="[0-9]+" class="mt-1" />
            </div>
          </template>

          <n-divider />

          <n-button type="error" block @click="deleteSelectedNode">
            <template #icon><n-icon><TrashOutline /></n-icon></template>
            删除节点
          </n-button>
        </div>
      </div>
    </div>

    <!-- 调试面板抽屉 -->
    <n-drawer v-model:show="showDebugPanel" :width="520" placement="right">
      <n-drawer-content closable>
        <template #header>
          <div class="flex items-center gap-2">
            <n-icon><RocketOutline /></n-icon>
            <span>调试面板</span>
          </div>
        </template>
        <div class="space-y-4">
          <!-- 输入测试 -->
          <n-card title="测试输入" size="small" class="shadow-sm">
            <n-input
              v-model:value="testInput"
              type="textarea"
              placeholder="请输入测试文本..."
              :autosize="{ minRows: 3, maxRows: 6 }"
              :disabled="executing"
            />
            <div class="flex gap-2 mt-3">
              <n-button
                type="primary"
                class="flex-1"
                @click="executeWorkflow"
                :loading="executing"
              >
                <template #icon><n-icon><PlayOutline /></n-icon></template>
                {{ executing ? '执行中...' : '开始执行' }}
              </n-button>
              <n-button @click="workflowStore.resetExecution()" :disabled="executing">
                重置
              </n-button>
            </div>
          </n-card>

          <!-- 执行状态 -->
          <n-card v-if="executing || executionResults.length > 0" title="执行状态" size="small" class="shadow-sm">
            <div class="space-y-3">
              <div class="flex items-center justify-between">
                <span class="text-sm">进度</span>
                <n-progress
                  type="line"
                  :percentage="executionProgress"
                  :status="executionStatus === 'failed' ? 'error' : executionStatus === 'success' ? 'success' : 'default'"
                  :show-indicator="false"
                  class="w-32"
                />
              </div>
              <div class="flex items-center justify-between text-sm text-gray-500">
                <span>已完成: {{ completedNodes }} / {{ executionResults.length }} 节点</span>
                <n-tag :type="executionStatus === 'success' ? 'success' : executionStatus === 'failed' ? 'error' : 'info'" size="small">
                  {{ statusText }}
                </n-tag>
              </div>
            </div>
          </n-card>

          <!-- 节点执行结果 -->
          <n-card v-if="executionResults.length > 0" title="节点执行结果" size="small" class="shadow-sm">
            <n-collapse>
              <n-collapse-item
                v-for="result in executionResults"
                :key="result.nodeId"
                :name="result.nodeId"
              >
                <template #header>
                  <div class="flex items-center justify-between w-full pr-4">
                    <span class="flex items-center gap-2">
                      <n-spin v-if="result.status === 'running'" size="small" />
                      <n-icon v-else-if="result.status === 'success'" class="text-green-500"><CheckmarkCircleOutline /></n-icon>
                      <n-icon v-else-if="result.status === 'failed'" class="text-red-500"><CloseCircleOutline /></n-icon>
                      <span class="text-sm">{{ getNodeLabel(result.nodeType) }}</span>
                    </span>
                    <n-tag v-if="result.duration" size="small" :type="result.status === 'success' ? 'success' : result.status === 'failed' ? 'error' : 'info'">
                      {{ result.duration }}ms
                    </n-tag>
                  </div>
                </template>
                <div class="space-y-2 text-xs">
                  <div v-if="result.output">
                    <div class="text-gray-500 mb-1">输出:</div>
                    <pre class="bg-gray-50 dark:bg-gray-800 p-2 rounded overflow-auto max-h-40 text-xs">{{ formatOutput(result.output) }}</pre>
                  </div>
                  <div v-if="result.error" class="text-red-500">
                    错误: {{ result.error }}
                  </div>
                </div>
              </n-collapse-item>
            </n-collapse>
          </n-card>

          <!-- 最终输出 -->
          <n-card v-if="finalOutput" title="最终输出" size="small" class="shadow-sm">
            <n-input
              :value="typeof finalOutput === 'string' ? finalOutput : JSON.stringify(finalOutput, null, 2)"
              type="textarea"
              readonly
              :autosize="{ minRows: 3, maxRows: 10 }"
              class="font-mono"
            />
          </n-card>

          <!-- 执行日志 -->
          <n-card title="执行日志" size="small" class="shadow-sm">
            <div class="max-h-48 overflow-auto">
              <n-timeline>
                <n-timeline-item
                  v-for="(log, index) in executionLogs"
                  :key="index"
                  :type="log.includes('❌') ? 'error' : log.includes('✅') ? 'success' : 'info'"
                >
                  <span class="text-xs font-mono">{{ log }}</span>
                </n-timeline-item>
              </n-timeline>
              <div v-if="executionLogs.length === 0" class="text-gray-400 text-center py-4 text-sm">
                点击"开始执行"运行工作流
              </div>
            </div>
          </n-card>
        </div>
      </n-drawer-content>
    </n-drawer>

    <!-- 加载工作流弹窗 -->
    <n-modal v-model:show="showLoadModal" preset="card" title="加载工作流" :style="{ width: '600px' }">
      <n-list v-if="!loadingWorkflows && workflowList.length > 0" hoverable clickable>
        <n-list-item v-for="wf in workflowList" :key="wf.id" @click="loadWorkflowById(wf.id)">
          <template #prefix>
            <n-tag size="small" :bordered="false" type="info">工作流</n-tag>
          </template>
          <div class="font-medium">{{ wf.name }}</div>
          <template #suffix>
            <span class="text-xs text-gray-500">{{ wf.created_at ? new Date(wf.created_at).toLocaleString() : '' }}</span>
          </template>
        </n-list-item>
      </n-list>
      <n-spin v-else-if="loadingWorkflows" class="flex justify-center py-8" />
      <n-empty v-else description="暂无工作流" />
      <template #footer>
        <div class="flex justify-end gap-2">
          <n-button @click="showLoadModal = false">取消</n-button>
          <n-button type="primary" @click="handleCreateNew">新建工作流</n-button>
        </div>
      </template>
    </n-modal>

    <!-- LLM 全局配置弹窗 (表格式管理，多配置 per provider) -->
    <n-modal v-model:show="showLLMConfig" preset="card" title="LLM 全局配置" :style="{ width: '800px' }" :mask-closable="false">
      <!-- 已有配置表格 -->
      <n-data-table
        v-if="llmConfigStore.configs.length > 0"
        :columns="llmConfigColumns"
        :data="llmConfigStore.configs"
        :bordered="false"
        :single-line="false"
        size="small"
        class="mb-4"
      />
      <n-empty v-else description="暂无配置，请新建" class="py-4" />

      <n-divider />

      <!-- 新建/编辑表单 -->
      <n-form :model="llmConfigForm" label-placement="left" label-width="80" size="small">
        <n-grid :cols="2" :x-gap="12">
          <n-form-item label="配置名称" required>
            <n-input v-model:value="llmConfigForm.config_name" placeholder="如：公司OpenAI账号" />
          </n-form-item>
          <n-form-item label="供应商" :required="!editingConfigId">
            <n-select
              v-model:value="llmConfigForm.provider"
              :options="llmConfigStore.providers.map(p => ({ label: p.label, value: p.key }))"
              :disabled="!!editingConfigId"
              placeholder="选择供应商"
            />
          </n-form-item>
          <n-form-item label="API Key" required>
            <n-input v-model:value="llmConfigForm.api_key" type="password" placeholder="sk-..." show-password-on="click" />
          </n-form-item>
          <n-form-item label="API URL">
            <n-input v-model:value="llmConfigForm.api_url" placeholder="如 https://api.openai.com/v1" />
          </n-form-item>
          <n-form-item label="模型">
            <n-input v-model:value="llmConfigForm.model" placeholder="如 gpt-4o-mini" />
          </n-form-item>
          <n-form-item label="温度">
            <n-input-number v-model:value="llmConfigForm.temperature" :min="0" :max="2" :step="0.1" style="width:100%" />
          </n-form-item>
        </n-grid>
        <n-form-item label="设为默认">
          <n-switch v-model:value="llmConfigForm.is_default" />
          <span class="text-xs text-gray-400 ml-2">同一供应商只能有一个默认配置</span>
        </n-form-item>
      </n-form>

      <template #footer>
        <div class="flex justify-between">
          <n-button type="error" size="small" v-if="editingConfigId" @click="handleDeleteConfig">
            删除当前配置
          </n-button>
          <n-button size="small" v-else @click="editingConfigId = null; resetLlmConfigForm()">重置</n-button>
          <div class="flex gap-2">
            <n-button @click="showLLMConfig = false">关闭</n-button>
            <n-button type="primary" @click="handleSaveConfig">
              {{ editingConfigId ? '更新配置' : '新建配置' }}
            </n-button>
          </div>
        </div>
      </template>
    </n-modal>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted, watch, onUnmounted, h } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { VueFlow, useVueFlow, MarkerType, type Connection } from '@vue-flow/core'
import { Background } from '@vue-flow/background'
import { Controls } from '@vue-flow/controls'
import { MiniMap } from '@vue-flow/minimap'
import {
  NIcon, NInput, NButton, NInputNumber, NCard, NCollapse, NCollapseItem,
  NDivider, NDrawer, NDrawerContent, NTimeline, NTimelineItem, NTag, NSpin, NProgress,
  NModal, NForm, NGrid, NFormItem, NSelect, NSwitch, NList, NListItem, NEmpty, NPopconfirm, NDataTable, NPopover,
  type DataTableColumns
} from 'naive-ui'
import {
  SaveOutline, PlayOutline, TrashOutline, ExpandOutline, SettingsOutline,
  AppsOutline, LocateOutline, CheckmarkCircleOutline, CloseCircleOutline,
  HardwareChipOutline, ChatbubbleEllipsesOutline, SearchOutline, VolumeHighOutline,
  EnterOutline, ExitOutline, GitBranchOutline, CompassOutline, ServerOutline,
  CodeSlashOutline, GlobeOutline, SyncOutline, RocketOutline, FlashOutline,
  FolderOpenOutline, AddOutline, CloseOutline
} from '@vicons/ionicons5'

// Icon mapping for node types
const NODE_ICON_MAP: Record<string, unknown> = {
  llm_openai: HardwareChipOutline,
  llm_deepseek: HardwareChipOutline,
  llm_qwen: ChatbubbleEllipsesOutline,
  tool_search: SearchOutline,
  tool_tts: VolumeHighOutline,
  input: EnterOutline,
  output: ExitOutline,
  condition: GitBranchOutline,
  router: CompassOutline,
  memory: ServerOutline,
  code: CodeSlashOutline,
  api_call: GlobeOutline,
  transform: SyncOutline,
}

const getNodeIconComponent = (type: string) => NODE_ICON_MAP[type] || AppsOutline

import { useWorkflowStore } from '@/stores/workflow'
import { useLLMConfigStore, type LLMProviderConfig } from '@/stores/llmConfigStore'
import { getWorkflow, createWorkflow, updateWorkflow, executeWorkflow as execWorkflow, getWorkflows, getNodeDefinitions, type WorkflowNode, type WorkflowEdge, type WorkflowConfig, type NodeDefinition } from '@/api/workflow'
import { agentApi } from '@/api/agent'
import type { SkillInfo } from '@/types/agent'
import { useDedupedMessage } from '@/utils/message'

// 节点组件
import InputNode from './nodes/InputNode.vue'
import OutputNode from './nodes/OutputNode.vue'
import LLMNode from './nodes/LLMNode.vue'
import ToolNode from './nodes/ToolNode.vue'
import ConditionNode from './nodes/ConditionNode.vue'
import MemoryNode from './nodes/MemoryNode.vue'
import CodeNode from './nodes/CodeNode.vue'
import ApiNode from './nodes/ApiNode.vue'
import TransformNode from './nodes/TransformNode.vue'

import '@vue-flow/core/dist/style.css'
import '@vue-flow/core/dist/theme-default.css'
import '@vue-flow/controls/dist/style.css'
import '@vue-flow/minimap/dist/style.css'

const route = useRoute()
const router = useRouter()
const message = useDedupedMessage()
const workflowStore = useWorkflowStore()
const llmConfigStore = useLLMConfigStore()

const { fitView: doFitView } = useVueFlow()

// 边的默认配置
const defaultEdgeOptions = {
  animated: true,
  style: { strokeWidth: 2, stroke: '#94a3b8' },
  markerEnd: MarkerType.ArrowClosed,
  type: 'smoothstep'
}

// ── 从 API 动态加载的节点定义 ──
const nodeDefinitions = ref<NodeDefinition[]>([])
const nodeDefsLoaded = ref(false)
const nodeDefsByCategory = computed(() => {
  const groups: Record<string, NodeDefinition[]> = {}
  for (const def of nodeDefinitions.value) {
    const cat = def.category || 'other'
    if (!groups[cat]) groups[cat] = []
    groups[cat].push(def)
  }
  return groups
})

const CATEGORY_LABELS: Record<string, string> = {
  llm: '大模型节点',
  tool: '工具节点',
  io: '输入输出',
  logic: '逻辑控制',
  data: '数据处理',
}
const CATEGORY_ICONS: Record<string, unknown> = {
  llm: HardwareChipOutline,
  tool: SettingsOutline,
  io: EnterOutline,
  logic: GitBranchOutline,
  data: ServerOutline,
}

// ── 硬编码回退（API 加载失败时使用）──
const FALLBACK_NODES: NodeDefinition[] = [
  { id: 0, node_type: 'llm', name: '通用LLM', category: 'llm', description: '支持多供应商的通用大模型节点' },
  { id: 0, node_type: 'llm_openai', name: 'OpenAI GPT', category: 'llm', description: 'GPT-4o 等模型' },
  { id: 0, node_type: 'llm_deepseek', name: 'DeepSeek', category: 'llm', description: '国产推理模型' },
  { id: 0, node_type: 'llm_qwen', name: '通义千问', category: 'llm', description: '阿里云大模型' },
  { id: 0, node_type: 'tool_search', name: '知识库检索', category: 'tool', description: 'RAG检索增强' },
  { id: 0, node_type: 'tool_tts', name: '语音合成', category: 'tool', description: '文本转语音' },
  { id: 0, node_type: 'input', name: '输入节点', category: 'io', description: '工作流入口' },
  { id: 0, node_type: 'output', name: '输出节点', category: 'io', description: '工作流出口' },
  { id: 0, node_type: 'condition', name: '条件分支', category: 'logic', description: '根据条件路由' },
  { id: 0, node_type: 'router', name: '智能路由', category: 'logic', description: 'LLM智能路由' },
  { id: 0, node_type: 'memory', name: '记忆节点', category: 'data', description: '存储/检索记忆' },
  { id: 0, node_type: 'code', name: '代码执行', category: 'data', description: '执行Python代码' },
  { id: 0, node_type: 'api_call', name: 'API调用', category: 'data', description: 'HTTP请求' },
  { id: 0, node_type: 'transform', name: '数据转换', category: 'data', description: 'JSON/文本处理' },
]

// 在 template 中使用的动态节点列表（回退到硬编码）
const llmNodes = computed(() => nodeDefsByCategory.value['llm'] || [])
const toolNodes = computed(() => nodeDefsByCategory.value['tool'] || [])
const ioNodes = computed(() => nodeDefsByCategory.value['io'] || [])
const logicNodes = computed(() => nodeDefsByCategory.value['logic'] || [])
const dataNodes = computed(() => nodeDefsByCategory.value['data'] || [])
const hasDynamicDefs = computed(() => nodeDefsLoaded.value && nodeDefinitions.value.length > 0)

// 配置选项
const openaiModels = [
  { label: 'GPT-4o', value: 'gpt-4o' },
  { label: 'GPT-4o Mini', value: 'gpt-4o-mini' },
  { label: 'GPT-4 Turbo', value: 'gpt-4-turbo' }
]

const deepseekModels = [
  { label: 'DeepSeek V4 Flash', value: 'deepseek-v4-flash' },
  { label: 'DeepSeek V4 Pro', value: 'deepseek-v4-pro' }
]

const qwenModels = [
  { label: '通义千问 Plus', value: 'qwen-plus' },
  { label: '通义千问 Max', value: 'qwen-max' }
]

const memoryTypeOptions = [
  { label: '短期记忆', value: 'short_term' },
  { label: '长期记忆', value: 'long_term' }
]

const memoryActionOptions = [
  { label: '存储', value: 'store' },
  { label: '检索', value: 'retrieve' }
]

const httpMethods = [
  { label: 'GET', value: 'GET' },
  { label: 'POST', value: 'POST' },
  { label: 'PUT', value: 'PUT' },
  { label: 'DELETE', value: 'DELETE' }
]

const codeLanguages = [
  { label: 'Python', value: 'python' },
  { label: 'JavaScript', value: 'javascript' }
]

const transformTypes = [
  { label: 'JSON提取', value: 'json_extract' },
  { label: '文本截取', value: 'text_slice' },
  { label: '正则提取', value: 'regex_extract' }
]

// ── Skills 列表 ──
const skills = ref<SkillInfo[]>([])
const skillsLoaded = ref(false)

async function loadSkills() {
  if (skillsLoaded.value) return
  try {
    const res = await agentApi.listSkills()
    skills.value = (res as any).data?.data || (res as any).data || []
    skillsLoaded.value = true
  } catch {
    // 静默失败，skills 是可选功能
  }
}

// ── 参数定义接口（Input/LLM/Output 节点共用）──
interface ParamDef {
  name: string
  type: 'input' | 'reference'
  value: string
  referenceNode?: string
}

// 状态
const saving = ref(false)
const executing = ref(false)
const executingNodeId = ref<string | null>(null)
const showDebugPanel = ref(false)
const showLLMConfig = ref(false)
const showLoadModal = ref(false)
const workflowList = ref<{ id: number; name: string; created_at: string }[]>([])
const loadingWorkflows = ref(false)
const testInput = ref('')
const vueFlowRef = ref()
const engineType = ref<'dag' | 'langgraph'>('dag')
const engineTypeOptions = [
  { label: 'DAG 引擎', value: 'dag' as const },
  { label: 'LangGraph 引擎', value: 'langgraph' as const },
]

// LLM 全局配置（表格式管理）
const editingConfigId = ref<string | null>(null)

const llmConfigForm = ref({
  provider: 'deepseek' as string,
  config_name: '',
  api_key: '',
  api_url: '',
  model: '',
  temperature: 0.7,
  is_default: false,
})

function resetLlmConfigForm() {
  editingConfigId.value = null
  llmConfigForm.value = {
    provider: 'deepseek',
    config_name: '',
    api_key: '',
    api_url: '',
    model: '',
    temperature: 0.7,
    is_default: false,
  }
}

// 表格列定义
const llmConfigColumns: DataTableColumns<LLMProviderConfig> = [
  { title: '供应商', key: 'provider', width: 90, render: (row) => llmConfigStore.getProviderLabel(row.provider) },
  { title: '配置名', key: 'config_name', ellipsis: { tooltip: true }, width: 130 },
  { title: 'API URL', key: 'api_url', ellipsis: { tooltip: true }, width: 160 },
  { title: '模型', key: 'model', width: 120 },
  { title: '温度', key: 'temperature', width: 60 },
  {
    title: '默认', key: 'is_default', width: 60,
    render: (row) => row.is_default ? h('span', { style: 'color: #10b981' }, '✓') : ''
  },
  {
    title: '操作', key: 'actions', width: 140,
    render: (row) => {
      const buttons: any[] = [
        h(NButton, { size: 'tiny', quaternary: true, onClick: () => editConfig(row) }, { icon: () => h(NIcon, null, h(SearchOutline as any)) }),
        h(NButton, { size: 'tiny', quaternary: true, type: 'error', onClick: () => handleDeleteConfigById(row.id) }, { icon: () => h(NIcon, null, h(TrashOutline as any)) }),
      ]
      if (!row.is_default) {
        buttons.splice(1, 0,
          h(NButton, { size: 'tiny', quaternary: true, onClick: () => handleSetDefault(row.id) }, { default: () => '设默认' })
        )
      }
      return h('div', { style: 'display:flex;gap:4px' }, buttons)
    },
  },
]

function editConfig(config: LLMProviderConfig) {
  editingConfigId.value = config.id
  llmConfigForm.value = {
    provider: config.provider,
    config_name: config.config_name,
    api_key: config.api_key,
    api_url: config.api_url,
    model: config.model,
    temperature: config.temperature,
    is_default: config.is_default,
  }
}

async function handleSaveConfig() {
  if (!llmConfigForm.value.config_name.trim()) {
    message.error('请输入配置名称')
    return
  }
  if (!llmConfigForm.value.api_key.trim()) {
    message.error('请输入 API Key')
    return
  }
  try {
    if (editingConfigId.value) {
      await llmConfigStore.update(editingConfigId.value, llmConfigForm.value as any)
      message.success('配置已更新')
    } else {
      await llmConfigStore.create(llmConfigForm.value as any)
      message.success('配置已创建')
    }
    resetLlmConfigForm()
  } catch {
    message.error('保存失败')
  }
}

async function handleDeleteConfigById(configId: string) {
  try {
    await llmConfigStore.remove(configId)
    message.success('配置已删除')
    if (editingConfigId.value === configId) resetLlmConfigForm()
  } catch {
    message.error('删除失败')
  }
}

async function handleDeleteConfig() {
  if (editingConfigId.value) await handleDeleteConfigById(editingConfigId.value)
}

async function handleSetDefault(configId: string) {
  try {
    await llmConfigStore.setDefault(configId)
    message.success('已设为默认')
  } catch {
    message.error('设置失败')
  }
}

// ── 自动保存定时器 ──
let autoSaveTimer: ReturnType<typeof setTimeout> | null = null

// 本地状态
const nodes = computed({
  get: () => workflowStore.nodes as any,
  set: (val) => workflowStore.setNodes(val)
})

const edges = computed({
  get: () => workflowStore.edges as any,
  set: (val) => workflowStore.setEdges(val)
})

const selectedNodeId = computed(() => workflowStore.selectedNode?.id)
const selectedNode = computed(() => workflowStore.selectedNode)
const nodeData = computed(() => (selectedNode.value?.data ?? {}) as Record<string, any>)
const executionResults = computed(() => workflowStore.executionResults)
const executionLogs = computed(() => workflowStore.executionLogs)
const finalOutput = computed(() => workflowStore.finalOutput)

const executionStatus = computed(() => {
  if (executionResults.value.length === 0) return 'idle'
  if (executionResults.value.some(r => r.status === 'failed')) return 'failed'
  if (executionResults.value.every(r => r.status === 'success')) return 'success'
  return 'running'
})

const executionProgress = computed(() => {
  if (executionResults.value.length === 0) return 0
  const completed = executionResults.value.filter(r => r.status === 'success').length
  return Math.round((completed / executionResults.value.length) * 100)
})

const completedNodes = computed(() => executionResults.value.filter(r => r.status === 'success').length)

const statusText = computed(() => {
  const map: Record<string, string> = {
    idle: '等待执行',
    running: '执行中',
    success: '执行成功',
    failed: '执行失败'
  }
  return map[executionStatus.value] || '未知'
})

const nodeColor = (node: { type?: string }) => {
  const colorMap: Record<string, string> = {
    input: '#3b82f6',
    output: '#10b981',
    llm_openai: '#22c55e',
    llm_deepseek: '#3b82f6',
    llm_qwen: '#f97316',
    tool_search: '#f59e0b',
    condition: '#06b6d4',
    memory: '#3b82f6',
    code: '#f43f5e',
    api_call: '#0ea5e9',
    transform: '#14b8a6'
  }
  return colorMap[node.type ?? ''] || '#6b7280'
}

// 模板加载
const loadTemplate = (type: string) => {
  workflowStore.clearWorkflow()

  const templates: Record<string, { name: string; nodes: Record<string, unknown>[]; edges: Record<string, unknown>[] }> = {
    rag: {
      name: 'RAG 问答流程',
      nodes: [
        { id: 'input_1', type: 'input', position: { x: 100, y: 200 }, data: { prompt: '', label: '输入' } },
        { id: 'search_1', type: 'tool_search', position: { x: 300, y: 100 }, data: { topK: 5, scoreThreshold: 0.5, label: '知识库检索' } },
        { id: 'llm_1', type: 'llm_deepseek', position: { x: 500, y: 200 }, data: { systemPrompt: '基于参考资料回答用户问题，如果资料中没有相关信息请如实说明', temperature: 0.7, maxTokens: 2048, label: 'DeepSeek' } },
        { id: 'output_1', type: 'output', position: { x: 700, y: 200 }, data: { label: '输出' } }
      ],
      edges: [
        { id: 'e1', source: 'input_1', target: 'search_1', animated: true, markerEnd: MarkerType.ArrowClosed },
        { id: 'e2', source: 'search_1', target: 'llm_1', animated: true, markerEnd: MarkerType.ArrowClosed },
        { id: 'e3', source: 'llm_1', target: 'output_1', animated: true, markerEnd: MarkerType.ArrowClosed }
      ]
    },
    chat: {
      name: '多轮对话流程',
      nodes: [
        { id: 'input_1', type: 'input', position: { x: 100, y: 200 }, data: { prompt: '', label: '输入' } },
        { id: 'llm_1', type: 'llm_openai', position: { x: 350, y: 200 }, data: { systemPrompt: '你是一个有帮助的AI助手，请友好、专业地回答用户的问题。', temperature: 0.7, maxTokens: 2048, label: 'OpenAI GPT' } },
        { id: 'output_1', type: 'output', position: { x: 600, y: 200 }, data: { label: '输出' } }
      ],
      edges: [
        { id: 'e1', source: 'input_1', target: 'llm_1', animated: true, markerEnd: MarkerType.ArrowClosed },
        { id: 'e2', source: 'llm_1', target: 'output_1', animated: true, markerEnd: MarkerType.ArrowClosed }
      ]
    },
    agent: {
      name: 'Agent 记忆流程',
      nodes: [
        { id: 'input_1', type: 'input', position: { x: 100, y: 200 }, data: { prompt: '', label: '输入' } },
        { id: 'memory_1', type: 'memory', position: { x: 300, y: 100 }, data: { memoryType: 'short_term', action: 'retrieve', label: '检索记忆' } },
        { id: 'llm_1', type: 'llm_deepseek', position: { x: 500, y: 200 }, data: { systemPrompt: '你是一个有记忆的AI助手，请结合历史记忆回答问题。', temperature: 0.7, maxTokens: 2048, label: 'DeepSeek' } },
        { id: 'memory_2', type: 'memory', position: { x: 700, y: 100 }, data: { memoryType: 'short_term', action: 'store', label: '存储记忆' } },
        { id: 'output_1', type: 'output', position: { x: 900, y: 200 }, data: { label: '输出' } }
      ],
      edges: [
        { id: 'e1', source: 'input_1', target: 'memory_1', animated: true, markerEnd: MarkerType.ArrowClosed },
        { id: 'e2', source: 'memory_1', target: 'llm_1', animated: true, markerEnd: MarkerType.ArrowClosed },
        { id: 'e3', source: 'llm_1', target: 'memory_2', animated: true, markerEnd: MarkerType.ArrowClosed },
        { id: 'e4', source: 'memory_2', target: 'output_1', animated: true, markerEnd: MarkerType.ArrowClosed }
      ]
    },
    report: {
      name: '报告生成流程',
      nodes: [
        { id: 'input_1', type: 'input', position: { x: 50, y: 200 }, data: { prompt: '请输入报告主题', label: '输入' } },
        { id: 'search_1', type: 'tool_search', position: { x: 200, y: 100 }, data: { topK: 10, scoreThreshold: 0.4, label: '资料检索' } },
        { id: 'llm_1', type: 'llm_deepseek', position: { x: 400, y: 100 }, data: { systemPrompt: '基于检索到的资料，生成一份结构清晰的报告大纲。', temperature: 0.5, maxTokens: 1000, label: '生成大纲' } },
        { id: 'llm_2', type: 'llm_deepseek', position: { x: 600, y: 200 }, data: { systemPrompt: '根据大纲和资料，撰写完整的报告内容。', temperature: 0.7, maxTokens: 4000, label: '撰写报告' } },
        { id: 'output_1', type: 'output', position: { x: 800, y: 200 }, data: { label: '输出' } }
      ],
      edges: [
        { id: 'e1', source: 'input_1', target: 'search_1', animated: true, markerEnd: MarkerType.ArrowClosed },
        { id: 'e2', source: 'search_1', target: 'llm_1', animated: true, markerEnd: MarkerType.ArrowClosed },
        { id: 'e3', source: 'llm_1', target: 'llm_2', animated: true, markerEnd: MarkerType.ArrowClosed },
        { id: 'e4', source: 'llm_2', target: 'output_1', animated: true, markerEnd: MarkerType.ArrowClosed }
      ]
    }
  }

  const template = templates[type]
  if (template) {
    workflowStore.workflowName = template.name
    workflowStore.setNodes(template.nodes as unknown as WorkflowNode[])
    workflowStore.setEdges(template.edges as unknown as WorkflowEdge[])
    message.success('模板加载成功')
  }
}

// 工具函数
const getNodeLabel = (type: string) => {
  // 优先从动态加载的节点定义中查找
  const def = nodeDefinitions.value.find(d => d.node_type === type)
  if (def) return def.name
  const defs = FALLBACK_NODES.find(d => d.node_type === type)
  if (defs) return defs.name
  return type
}

const formatOutput = (output: unknown) => {
  if (typeof output === 'string') return output
  if (output && typeof output === 'object' && 'content' in output) return (output as Record<string, unknown>).content
  return JSON.stringify(output, null, 2)
}

// ── 参数引用：获取可引用的上游节点参数列表 ──
function getReferenceableParams(): { label: string; value: string }[] {
  const params: { label: string; value: string }[] = []
  if (!selectedNode.value) return params
  for (const n of nodes.value) {
    if (n.id === selectedNode.value.id) continue
    const nodeType = n.data?.type as string || ''
    const nodeLabel = (n.data?.label as string) || n.id
    const outputFields = getNodeOutputFields(nodeType)
    for (const field of outputFields) {
      params.push({
        label: `${nodeLabel}.${field}`,
        value: `${n.id}.${field}`,
      })
    }
  }
  return params
}

function getLlmNodeProvider(node: any): string {
  // 通用 llm 节点用 node.data.provider；特定 provider 节点从类型推导
  if (node?.type === 'llm') return node?.data?.provider || 'deepseek'
  return (node?.type || '').replace('llm_', '')
}

function getNodeOutputFields(nodeType: string): string[] {
  switch (nodeType) {
    case 'input': return ['user_input']
    case 'llm': case 'llm_openai': case 'llm_deepseek': case 'llm_qwen':
      return ['output', 'tokens']
    case 'tool_search': return ['results', 'count']
    case 'tool_tts': return ['audioUrl', 'fileName', 'output']
    case 'memory': return ['result', 'output']
    case 'code': return ['output']
    case 'api_call': return ['output', 'status']
    case 'transform': return ['output']
    default: return ['output']
  }
}

// ── 模板变量校验 ──
function validateTemplateParams(template: string, definedParams: string[]): string[] {
  const matches = template.matchAll(/\{\{(\w+)\}\}/g)
  const missing: string[] = []
  const defined = new Set(definedParams)
  for (const m of matches) {
    if (!defined.has(m[1])) {
      missing.push(m[1])
    }
  }
  return missing
}

// 拖拽处理 — 支持动态节点定义
const onDragStart = (event: DragEvent, node: NodeDefinition | Record<string, unknown>) => {
  if (event.dataTransfer) {
    // 适配 NodeDefinition 的字段名 (node_type->type, name->label)
    const dragData = {
      type: (node as any).node_type || (node as any).type,
      label: (node as any).name || (node as any).label,
      description: (node as any).description || '',
    }
    event.dataTransfer.setData('application/vueflow', JSON.stringify(dragData))
    event.dataTransfer.effectAllowed = 'move'
  }
}

const onDrop = (event: DragEvent) => {
  const data = event.dataTransfer?.getData('application/vueflow')
  if (!data) return

  const nodeType = JSON.parse(data)
  const rect = (event.target as HTMLElement).getBoundingClientRect()
  const position = {
    x: event.clientX - rect.left,
    y: event.clientY - rect.top
  }

  const defaultData: Record<string, unknown> = {
    systemPrompt: '', temperature: 0.7, maxTokens: 2048,
    prompt: '', topK: 5, scoreThreshold: 0.5, condition: '',
    memoryType: 'short_term', action: 'store',
    method: 'GET', url: '', timeout: 30,
    language: 'python', code: '', transformType: 'json_extract',
    inputParams: [], outputParams: [],  // 参数引用系统
    skillName: '', skillId: '',           // Skill 选择器
    useGlobalConfig: false,               // LLM 全局配置引用
    configProvider: '',                   // 全局配置的 provider
  }

  const newNode: WorkflowNode = {
    id: `${nodeType.type}_${Date.now()}`,
    type: nodeType.type,
    position,
    data: { label: nodeType.label, type: nodeType.type, ...defaultData }
  }

  workflowStore.addNode(newNode)
}

const onNodeClick = (event: { node: WorkflowNode }) => {
  workflowStore.selectNode(event.node)
}

const onConnect = (params: Connection) => {
  const newEdge: WorkflowEdge = {
    id: `edge_${Date.now()}`,
    source: params.source,
    target: params.target,
    sourceHandle: params.sourceHandle ?? undefined,
    targetHandle: params.targetHandle ?? undefined,
    animated: true,
    markerEnd: MarkerType.ArrowClosed as unknown as string
  }
  workflowStore.addEdge(newEdge)
}

// ── 参数管理辅助函数 ──
function addLlmInputParam() {
  if (!selectedNode.value) return
  const data = selectedNode.value.data as any
  const params = [...(data.inputParams || [])]
  params.push({ name: '', type: 'input', value: '' })
  workflowStore.updateNodeData(selectedNode.value.id, { ...data, inputParams: params })
}
function removeLlmInputParam(idx: number) {
  if (!selectedNode.value) return
  const data = selectedNode.value.data as any
  const params = [...(data.inputParams || [])]
  params.splice(idx, 1)
  workflowStore.updateNodeData(selectedNode.value.id, { ...data, inputParams: params })
}
function addInputParam() {
  if (!selectedNode.value) return
  const data = selectedNode.value.data as any
  const params = [...(data.inputParams || [])]
  params.push({ name: '', type: 'string', value: '' })
  workflowStore.updateNodeData(selectedNode.value.id, { ...data, inputParams: params })
}
function removeInputParam(idx: number) {
  if (!selectedNode.value) return
  const data = selectedNode.value.data as any
  const params = [...(data.inputParams || [])]
  params.splice(idx, 1)
  workflowStore.updateNodeData(selectedNode.value.id, { ...data, inputParams: params })
}
function addOutputParam() {
  if (!selectedNode.value) return
  const data = selectedNode.value.data as any
  const params = [...(data.outputParams || [])]
  params.push({ name: '', type: 'input', value: '' })
  workflowStore.updateNodeData(selectedNode.value.id, { ...data, outputParams: params })
}
function removeOutputParam(idx: number) {
  if (!selectedNode.value) return
  const data = selectedNode.value.data as any
  const params = [...(data.outputParams || [])]
  params.splice(idx, 1)
  workflowStore.updateNodeData(selectedNode.value.id, { ...data, outputParams: params })
}

// LLM 输出参数管理
function addLlmOutputParam() {
  if (!selectedNode.value) return
  const data = selectedNode.value.data as any
  const params = [...(data.outputParams || [])]
  params.push({ name: '', type: 'string', description: '' })
  workflowStore.updateNodeData(selectedNode.value.id, { ...data, outputParams: params })
}
function removeLlmOutputParam(idx: number) {
  if (!selectedNode.value) return
  const data = selectedNode.value.data as any
  const params = [...(data.outputParams || [])]
  params.splice(idx, 1)
  workflowStore.updateNodeData(selectedNode.value.id, { ...data, outputParams: params })
}

// TTS 参数辅助
function addTtsInputParam() {
  if (!selectedNode.value) return
  const data = selectedNode.value.data as any
  const params = [...(data.inputParams || [])]
  params.push({ name: '', type: 'input', value: '' })
  workflowStore.updateNodeData(selectedNode.value.id, { ...data, inputParams: params })
}
function removeTtsInputParam(idx: number) {
  if (!selectedNode.value) return
  const data = selectedNode.value.data as any
  const params = [...(data.inputParams || [])]
  params.splice(idx, 1)
  workflowStore.updateNodeData(selectedNode.value.id, { ...data, inputParams: params })
}
function addTtsOutputParam() {
  if (!selectedNode.value) return
  const data = selectedNode.value.data as any
  const params = [...(data.outputParams || [])]
  params.push({ name: '', value: '' })
  workflowStore.updateNodeData(selectedNode.value.id, { ...data, outputParams: params })
}
function removeTtsOutputParam(idx: number) {
  if (!selectedNode.value) return
  const data = selectedNode.value.data as any
  const params = [...(data.outputParams || [])]
  params.splice(idx, 1)
  workflowStore.updateNodeData(selectedNode.value.id, { ...data, outputParams: params })
}

// TTS 音色选项
const ttsVoiceOptions = [
  { label: 'Cherry (芊悦)', value: 'Cherry' },
  { label: 'Serena (苏瑶)', value: 'Serena' },
  { label: 'Ethan (晨煦)', value: 'Ethan' },
  { label: 'Chelsie (千雪)', value: 'Chelsie' },
  { label: 'Momo (茉兔)', value: 'Momo' },
  { label: 'Vivian (十三)', value: 'Vivian' },
  { label: 'Moon (月白)', value: 'Moon' },
  { label: 'Maia (四月)', value: 'Maia' },
  { label: 'Kai (凯)', value: 'Kai' },
  { label: 'Nofish (不吃鱼)', value: 'Nofish' },
  { label: 'Bella (萌宝)', value: 'Bella' },
  { label: 'Jennifer (詹妮弗)', value: 'Jennifer' },
  { label: 'Ryan (甜茶)', value: 'Ryan' },
  { label: 'Katerina (卡捷琳娜)', value: 'Katerina' },
  { label: 'Aiden (艾登)', value: 'Aiden' },
]

const deleteSelectedNode = () => {
  if (selectedNode.value) {
    workflowStore.removeNode(selectedNode.value.id)
    workflowStore.selectNode(null)
  }
}

const clearCanvas = () => {
  workflowStore.clearWorkflow()
}

const fitView = () => {
  doFitView()
}

// 保存工作流
const saveWorkflow = async () => {
  if (!workflowStore.workflowName.trim()) {
    message.error('请输入工作流名称')
    return
  }

  saving.value = true
  try {
    const flowData = workflowStore.getFlowData() as unknown as WorkflowConfig

    if (workflowStore.currentWorkflowId) {
      await updateWorkflow(workflowStore.currentWorkflowId, {
        name: workflowStore.workflowName,
        flow_data: flowData,
        engine_type: engineType.value,
      } as any)
    } else {
      const res = await createWorkflow({
        name: workflowStore.workflowName,
        flow_data: flowData,
        engine_type: engineType.value,
      } as any)
      workflowStore.currentWorkflowId = res.data?.data?.id
      // Update URL without navigation
      if (workflowStore.currentWorkflowId) {
        router.replace({ query: { id: String(workflowStore.currentWorkflowId) } })
      }
    }
    return true
  } catch (error: unknown) {
    const err = error as { response?: { data?: { detail?: string } } }
    message.error(err.response?.data?.detail || '保存失败')
    return false
  } finally {
    saving.value = false
  }
}

// 自动保存 — 500ms 防抖
const autoSave = () => {
  if (autoSaveTimer) clearTimeout(autoSaveTimer)
  autoSaveTimer = setTimeout(async () => {
    if (!workflowStore.currentWorkflowId || !workflowStore.workflowName.trim()) return
    try {
      const flowData = workflowStore.getFlowData() as unknown as WorkflowConfig
      await updateWorkflow(workflowStore.currentWorkflowId, {
        name: workflowStore.workflowName,
        flow_data: flowData,
        engine_type: engineType.value,
      } as any)
      console.log('[auto-save] 工作流已自动保存')
    } catch {
      // Auto-save failures are silent
    }
  }, 500)
}

// 监听画布变化 → 触发自动保存
watch(
  () => [workflowStore.nodes, workflowStore.edges, workflowStore.workflowName],
  () => {
    if (workflowStore.currentWorkflowId) {
      autoSave()
    }
  },
  { deep: true }
)

// 监听选中节点配置变化 → 自动保存到节点 data
watch(
  () => selectedNode.value?.data,
  () => {
    if (selectedNode.value) {
      workflowStore.updateNodeData(selectedNode.value.id, { ...selectedNode.value.data })
      // 同时触发自动保存到后端
      if (workflowStore.currentWorkflowId) {
        autoSave()
      }
    }
  },
  { deep: true }
)

// 调试执行
const openDebugPanel = () => {
  showDebugPanel.value = true
  workflowStore.resetExecution()
}

const executeWorkflow = async () => {
  if (!testInput.value.trim()) {
    message.error('请输入测试数据')
    return
  }

  if (!workflowStore.currentWorkflowId) {
    await saveWorkflow()
  }

  if (!workflowStore.currentWorkflowId) {
    message.error('请先保存工作流')
    return
  }

  executing.value = true
  workflowStore.startExecution()
  workflowStore.addExecutionLog('🚀 开始执行工作流...')

  try {
    const res = await execWorkflow(workflowStore.currentWorkflowId, { text: testInput.value })
    const result = res.data?.data

    if (result?.node_results) {
      for (const nodeResult of result.node_results) {
        executingNodeId.value = nodeResult.node_id
        const status = nodeResult.status || 'success'
        const icon = status === 'success' ? '✅' : status === 'failed' ? '❌' : '📊'
        workflowStore.updateNodeExecution({
          nodeId: nodeResult.node_id,
          nodeType: nodeResult.node_type,
          status,
          output: nodeResult.output,
          duration: nodeResult.duration
        })
        workflowStore.addExecutionLog(`${icon} [${getNodeLabel(nodeResult.node_type)}] ${status === 'success' ? '完成' : '失败'} (${nodeResult.duration || 0}ms)`)
      }
    }

    if (result?.output) {
      workflowStore.setExecutionComplete(result.output)
      workflowStore.addExecutionLog('✅ 工作流执行成功!')
    } else {
      workflowStore.setExecutionComplete(result)
      workflowStore.addExecutionLog('✅ 工作流执行完成')
    }

  } catch (error: unknown) {
    const errMsg = error instanceof Error ? error.message : '未知错误'
    workflowStore.addExecutionLog(`❌ 执行失败: ${errMsg}`)
    message.error('执行失败')
  } finally {
    executing.value = false
    executingNodeId.value = null
  }
}

// ── 工作流加载列表 ──
async function openLoadModal() {
  showLoadModal.value = true
  loadingWorkflows.value = true
  try {
    const res = await getWorkflows()
    workflowList.value = (res.data?.data?.items || res.data?.data || [])
  } catch {
    message.error('获取工作流列表失败')
  } finally {
    loadingWorkflows.value = false
  }
}

async function loadWorkflowById(id: number) {
  showLoadModal.value = false
  try {
    const res = await getWorkflow(id)
    const workflow = res.data?.data
    if (workflow) {
      workflowStore.loadWorkflow(workflow)
      if (workflow.engine_type) engineType.value = workflow.engine_type as 'dag' | 'langgraph'
      router.replace({ query: { id: String(id) } })
      message.success('工作流加载成功')
    }
  } catch {
    message.error('加载工作流失败')
  }
}

function handleCreateNew() {
  workflowStore.clearWorkflow()
  router.replace({ query: {} })
  message.info('已创建新工作流')
}

// ── 节点定义加载 ──
async function loadNodeDefinitions() {
  try {
    const res = await getNodeDefinitions()
    const defs = res.data?.data
    if (defs && Array.isArray(defs) && defs.length > 0) {
      nodeDefinitions.value = defs
      nodeDefsLoaded.value = true
    }
  } catch {
    // 使用硬编码回退
    nodeDefinitions.value = FALLBACK_NODES
    nodeDefsLoaded.value = true
  }
}

onMounted(async () => {
  // 并行加载所有初始化数据
  await Promise.all([
    loadNodeDefinitions(),
    llmConfigStore.fetchAll(),
    loadSkills(),
  ])

  const id = route.query.id as string
  if (id) {
    await loadWorkflowById(parseInt(id))
  }
})

onUnmounted(() => {
  if (autoSaveTimer) clearTimeout(autoSaveTimer)
})
</script>

<style scoped>
:deep(.vue-flow__node) {
  border-radius: 12px;
}
:deep(.vue-flow__edge-path) {
  stroke-width: 2;
}
:deep(.vue-flow__edge.animated path) {
  stroke-dasharray: 5;
  animation: dashdraw 0.5s linear infinite;
}
@keyframes dashdraw {
  from { stroke-dashoffset: 10; }
}
:deep(.vue-flow__minimap) {
  background-color: #f3f4f6;
  border-radius: 8px;
}
</style>