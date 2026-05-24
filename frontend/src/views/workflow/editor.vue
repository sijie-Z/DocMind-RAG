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
        <n-collapse :default-expanded-names="['llm', 'tool', 'io', 'logic', 'data']">
          <n-collapse-item name="llm">
            <template #header><n-icon class="mr-1"><HardwareChipOutline /></n-icon> 大模型节点</template>
            <div class="space-y-2">
              <div
                v-for="node in llmNodes"
                :key="node.type"
                class="p-3 bg-gray-50 dark:bg-gray-700/50 rounded-lg cursor-grab hover:bg-blue-50 dark:hover:bg-blue-900/20 border border-gray-200 dark:border-gray-600 hover:border-blue-300 dark:hover:border-blue-500 transition-all"
                draggable="true"
                @dragstart="onDragStart($event, node)"
              >
                <div class="flex items-center gap-2">
                  <n-icon size="20"><component :is="getNodeIconComponent(node.type)" /></n-icon>
                  <div>
                    <div class="font-medium text-gray-700 dark:text-gray-200">{{ node.label }}</div>
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
                :key="node.type"
                class="p-3 bg-gray-50 dark:bg-gray-700/50 rounded-lg cursor-grab hover:bg-amber-50 dark:hover:bg-amber-900/20 border border-gray-200 dark:border-gray-600 hover:border-amber-300 dark:hover:border-amber-500 transition-all"
                draggable="true"
                @dragstart="onDragStart($event, node)"
              >
                <div class="flex items-center gap-2">
                  <n-icon size="20"><component :is="getNodeIconComponent(node.type)" /></n-icon>
                  <div>
                    <div class="font-medium text-gray-700 dark:text-gray-200">{{ node.label }}</div>
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
                :key="node.type"
                class="p-3 bg-gray-50 dark:bg-gray-700/50 rounded-lg cursor-grab hover:bg-emerald-50 dark:hover:bg-emerald-900/20 border border-gray-200 dark:border-gray-600 hover:border-emerald-300 dark:hover:border-emerald-500 transition-all"
                draggable="true"
                @dragstart="onDragStart($event, node)"
              >
                <div class="flex items-center gap-2">
                  <n-icon size="20"><component :is="getNodeIconComponent(node.type)" /></n-icon>
                  <div>
                    <div class="font-medium text-gray-700 dark:text-gray-200">{{ node.label }}</div>
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
                :key="node.type"
                class="p-3 bg-gray-50 dark:bg-gray-700/50 rounded-lg cursor-grab hover:bg-cyan-50 dark:hover:bg-cyan-900/20 border border-gray-200 dark:border-gray-600 hover:border-cyan-300 dark:hover:border-cyan-500 transition-all"
                draggable="true"
                @dragstart="onDragStart($event, node)"
              >
                <div class="flex items-center gap-2">
                  <n-icon size="20"><component :is="getNodeIconComponent(node.type)" /></n-icon>
                  <div>
                    <div class="font-medium text-gray-700 dark:text-gray-200">{{ node.label }}</div>
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
                :key="node.type"
                class="p-3 bg-gray-50 dark:bg-gray-700/50 rounded-lg cursor-grab hover:bg-teal-50 dark:hover:bg-teal-900/20 border border-gray-200 dark:border-gray-600 hover:border-teal-300 dark:hover:border-teal-500 transition-all"
                draggable="true"
                @dragstart="onDragStart($event, node)"
              >
                <div class="flex items-center gap-2">
                  <n-icon size="20"><component :is="getNodeIconComponent(node.type)" /></n-icon>
                  <div>
                    <div class="font-medium text-gray-700 dark:text-gray-200">{{ node.label }}</div>
                    <div class="text-xs text-gray-500">{{ node.description }}</div>
                  </div>
                </div>
              </div>
            </div>
          </n-collapse-item>
        </n-collapse>
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
          <n-button type="primary" @click="saveWorkflow" :loading="saving">
            <template #icon><n-icon><SaveOutline /></n-icon></template>
            保存
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
          <template v-if="selectedNode.type?.startsWith('llm_')">
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
          </template>

          <!-- 输入节点配置 -->
          <template v-if="selectedNode.type === 'input'">
            <div>
              <label class="text-sm font-medium text-gray-600 dark:text-gray-300">提示词模板</label>
              <n-input
                v-model:value="nodeData.prompt"
                type="textarea"
                placeholder="输入提示词模板..."
                :autosize="{ minRows: 4, maxRows: 8 }"
                class="mt-1"
              />
              <div class="text-xs text-gray-400 mt-1" v-text="'支持变量: {{input}}'"></div>
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
                  :type="log.includes('✗') ? 'error' : log.includes('✓') ? 'success' : 'info'"
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

    <!-- LLM 配置弹窗 -->
    <n-modal v-model:show="showLLMConfig" preset="card" title="LLM 模型配置" :style="{ width: '650px' }">
      <n-tabs type="line">
        <n-tab-pane name="openai" tab="OpenAI">
          <div class="space-y-4 p-2">
            <n-form-item label="API Key">
              <n-input v-model:value="llmConfig.openai.apiKey" type="password" placeholder="sk-..." show-password-on="click" />
            </n-form-item>
            <n-form-item label="Base URL">
              <n-input v-model:value="llmConfig.openai.baseUrl" placeholder="https://api.openai.com/v1" />
            </n-form-item>
            <n-form-item label="默认模型">
              <n-select v-model:value="llmConfig.openai.model" :options="openaiModels" />
            </n-form-item>
          </div>
        </n-tab-pane>
        <n-tab-pane name="deepseek" tab="DeepSeek">
          <div class="space-y-4 p-2">
            <n-form-item label="API Key">
              <n-input v-model:value="llmConfig.deepseek.apiKey" type="password" placeholder="sk-..." show-password-on="click" />
            </n-form-item>
            <n-form-item label="默认模型">
              <n-select v-model:value="llmConfig.deepseek.model" :options="deepseekModels" />
            </n-form-item>
          </div>
        </n-tab-pane>
        <n-tab-pane name="qwen" tab="通义千问">
          <div class="space-y-4 p-2">
            <n-form-item label="API Key (DashScope)">
              <n-input v-model:value="llmConfig.qwen.apiKey" type="password" placeholder="sk-..." show-password-on="click" />
            </n-form-item>
            <n-form-item label="默认模型">
              <n-select v-model:value="llmConfig.qwen.model" :options="qwenModels" />
            </n-form-item>
          </div>
        </n-tab-pane>
      </n-tabs>
      <template #footer>
        <div class="flex justify-end gap-2">
          <n-button @click="showLLMConfig = false">取消</n-button>
          <n-button type="primary" @click="saveLLMConfig">保存配置</n-button>
        </div>
      </template>
    </n-modal>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'
import { useRoute } from 'vue-router'
import { VueFlow, useVueFlow, MarkerType, type Connection } from '@vue-flow/core'
import { Background } from '@vue-flow/background'
import { Controls } from '@vue-flow/controls'
import { MiniMap } from '@vue-flow/minimap'
import {
  NIcon, NInput, NButton, NInputNumber, NCard, NCollapse, NCollapseItem,
  NDivider, NDrawer, NDrawerContent, NTimeline, NTimelineItem, NTag, NSpin, NProgress,
  NModal, NTabs, NTabPane, NFormItem, NSelect
} from 'naive-ui'
import {
  SaveOutline, PlayOutline, TrashOutline, ExpandOutline, SettingsOutline,
  AppsOutline, LocateOutline, CheckmarkCircleOutline, CloseCircleOutline,
  HardwareChipOutline, ChatbubbleEllipsesOutline, SearchOutline, VolumeHighOutline,
  EnterOutline, ExitOutline, GitBranchOutline, CompassOutline, ServerOutline,
  CodeSlashOutline, GlobeOutline, SyncOutline, RocketOutline, FlashOutline
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
import { getWorkflow, createWorkflow, updateWorkflow, executeWorkflow as execWorkflow, type WorkflowNode, type WorkflowEdge, type WorkflowConfig } from '@/api/workflow'
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
const message = useDedupedMessage()
const workflowStore = useWorkflowStore()

const { fitView: doFitView } = useVueFlow()

// 边的默认配置
const defaultEdgeOptions = {
  animated: true,
  style: { strokeWidth: 2, stroke: '#94a3b8' },
  markerEnd: MarkerType.ArrowClosed,
  type: 'smoothstep'
}

// 节点定义
const llmNodes = [
  { type: 'llm_openai', label: 'OpenAI GPT', description: 'GPT-4o 等模型' },
  { type: 'llm_deepseek', label: 'DeepSeek', description: '国产推理模型' },
  { type: 'llm_qwen', label: '通义千问', description: '阿里云大模型' }
]

const toolNodes = [
  { type: 'tool_search', label: '知识库检索', description: 'RAG检索增强' },
  { type: 'tool_tts', label: '语音合成', description: '文本转语音' }
]

const ioNodes = [
  { type: 'input', label: '输入节点', description: '工作流入口' },
  { type: 'output', label: '输出节点', description: '工作流出口' }
]

const logicNodes = [
  { type: 'condition', label: '条件分支', description: '根据条件路由' },
  { type: 'router', label: '智能路由', description: 'LLM智能路由' }
]

const dataNodes = [
  { type: 'memory', label: '记忆节点', description: '存储/检索记忆' },
  { type: 'code', label: '代码执行', description: '执行Python代码' },
  { type: 'api_call', label: 'API调用', description: 'HTTP请求' },
  { type: 'transform', label: '数据转换', description: 'JSON/文本处理' }
]

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

// 状态
const saving = ref(false)
const executing = ref(false)
const executingNodeId = ref<string | null>(null)
const showDebugPanel = ref(false)
const showLLMConfig = ref(false)
const testInput = ref('')
const vueFlowRef = ref()

// LLM 配置
const llmConfig = ref({
  openai: { apiKey: '', baseUrl: '', model: 'gpt-4o-mini', temperature: 0.7 },
  deepseek: { apiKey: '', baseUrl: 'https://api.deepseek.com/v1', model: 'deepseek-v4-flash', temperature: 0.7 },
  qwen: { apiKey: '', model: 'qwen-plus', temperature: 0.7 }
})

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
const nodeData = computed(() => (selectedNode.value?.data ?? {}) as Record<string, any>) // eslint-disable-line @typescript-eslint/no-explicit-any -- Naive UI v-model bindings require any for index-signature types
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
  const labelMap: Record<string, string> = {
    input: '输入', output: '输出',
    llm_openai: 'OpenAI GPT', llm_deepseek: 'DeepSeek', llm_qwen: '通义千问',
    tool_search: '知识库检索', tool_tts: '语音合成',
    condition: '条件分支', router: '智能路由',
    memory: '记忆', code: '代码执行', api_call: 'API调用', transform: '数据转换'
  }
  return labelMap[type] || type
}

const formatOutput = (output: unknown) => {
  if (typeof output === 'string') return output
  if (output && typeof output === 'object' && 'content' in output) return (output as Record<string, unknown>).content
  return JSON.stringify(output, null, 2)
}

// 拖拽处理
const onDragStart = (event: DragEvent, node: Record<string, unknown>) => {
  if (event.dataTransfer) {
    event.dataTransfer.setData('application/vueflow', JSON.stringify(node))
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
    language: 'python', code: '', transformType: 'json_extract'
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
        flow_data: flowData
      })
      message.success('更新成功')
    } else {
      const res = await createWorkflow({
        name: workflowStore.workflowName,
        flow_data: flowData
      })
      workflowStore.currentWorkflowId = res.data?.data?.id
      message.success('保存成功')
    }
  } catch (error: unknown) {
    const err = error as { response?: { data?: { detail?: string } } }
    message.error(err.response?.data?.detail || '保存失败')
  } finally {
    saving.value = false
  }
}

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
  workflowStore.addExecutionLog('▸ 开始执行工作流...')

  try {
    const res = await execWorkflow(workflowStore.currentWorkflowId, { text: testInput.value })
    const result = res.data?.data

    if (result?.node_results) {
      for (const nodeResult of result.node_results) {
        executingNodeId.value = nodeResult.node_id
        workflowStore.updateNodeExecution({
          nodeId: nodeResult.node_id,
          nodeType: nodeResult.node_type,
          status: nodeResult.status || 'success',
          output: nodeResult.output,
          duration: nodeResult.duration
        })
        workflowStore.addExecutionLog(`✓ [${getNodeLabel(nodeResult.node_type)}] 完成 (${nodeResult.duration || 0}ms)`)
      }
    }

    if (result?.output) {
      workflowStore.setExecutionComplete(result.output)
      workflowStore.addExecutionLog('✓ 工作流执行成功!')
    } else {
      workflowStore.setExecutionComplete(result)
      workflowStore.addExecutionLog('✓ 工作流执行完成')
    }

  } catch (error: unknown) {
    const errMsg = error instanceof Error ? error.message : '未知错误'
    workflowStore.addExecutionLog(`✗ 执行失败: ${errMsg}`)
    message.error('执行失败')
  } finally {
    executing.value = false
    executingNodeId.value = null
  }
}

const saveLLMConfig = () => {
  workflowStore.llmConfig = { ...llmConfig.value }
  showLLMConfig.value = false
  message.success('LLM 配置已保存')
}

const loadWorkflow = async (id: number) => {
  try {
    const res = await getWorkflow(id)
    const workflow = res.data?.data
    if (workflow) {
      workflowStore.loadWorkflow(workflow)
    }
  } catch (error) {
    message.error('加载工作流失败')
  }
}

onMounted(() => {
  const id = route.query.id as string
  if (id) {
    loadWorkflow(parseInt(id))
  }
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