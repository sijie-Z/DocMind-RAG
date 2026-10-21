<template>
  <div v-if="visible" data-testid="org-switcher" class="min-w-0">
    <!-- T7：只有存在可切换目标（至少一个非当前组织）时才渲染下拉；
         单组织且唯一选项即当前组织时点开无任何动作，改为下方静态形态。 -->
    <n-dropdown
      v-if="canSwitch"
      trigger="click"
      :options="options"
      :disabled="switching"
      @select="handleSelect"
    >
      <n-button
        quaternary
        size="small"
        class="max-w-[13rem]"
        data-testid="org-switcher-trigger"
        :title="t('org.current')"
        :aria-label="t('org.switchOrganization')"
      >
        <template #icon>
          <n-icon :size="16"><BusinessOutline /></n-icon>
        </template>
        <span class="truncate">{{ currentOrgName }}</span>
        <n-icon :size="14" class="ml-1 opacity-60"><ChevronDownOutline /></n-icon>
      </n-button>
    </n-dropdown>

    <!-- T7 单组织静态形态：无下拉箭头、无 button 语义、无点击态 -->
    <div
      v-else
      data-testid="org-switcher-static"
      class="flex min-w-0 items-center gap-1.5 text-xs text-gray-500 dark:text-gray-400"
      :title="currentOrgName"
    >
      <n-icon :size="16"><BusinessOutline /></n-icon>
      <span class="truncate">{{ currentOrgName }}</span>
    </div>
  </div>
</template>

<script setup lang="ts">
/**
 * 顶栏组织切换器（B1，T7 修订；桌面端形态）。
 *
 * 数据源：GET /users/me/organizations（成员关系 + 主组织兜底），
 * 列表由 user store 的 `myOrganizations` 共享——移动端组织入口在
 * 顶栏用户菜单（layouts/index.vue）消费同一份状态。
 * 切换：POST /users/me/organizations/{id}/switch —— 服务端校验成员关系并
 * 落库当前组织；成功后 user store 的 organizationVersion 递增，布局层页面
 * key 随之变化，当前页面重挂载并按新组织重新拉取文档/会话等数据。
 * 失败时只提示、不改变本地组织状态。
 *
 * T7：
 *  - 单组织（唯一选项即当前组织，没有任何可切换目标）渲染为静态组织名，
 *    不渲染 button / 下拉——不再出现「可点但无动作」的假按钮；
 *  - 超管无主组织但可切换（currentOrgId 为 null 且列表非空）时所有选项均可选，
 *    保留下拉与 currentOrgName 的「切换组织」提示文案；
 *  - 移动端（<640px）入口由顶栏用户菜单承接（本组件容器保持 B1 的
 *    `hidden sm:block`）：紧凑顶栏形态会被右侧固定宽度搜索区遮挡并挤压标题。
 */
import { computed, onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { NButton, NDropdown, NIcon } from 'naive-ui'
import { BusinessOutline, ChevronDownOutline } from '@vicons/ionicons5'
import { useUserStore } from '@/stores/user'
import { useDedupedMessage } from '@/utils/message'

const { t } = useI18n()
const userStore = useUserStore()
const message = useDedupedMessage()

// T7：共享列表（见 stores/user.ts fetchMyOrganizations），布局层用户菜单同源。
const organizations = computed(() => userStore.myOrganizations)
const switching = ref(false)

const currentOrgId = computed(() => userStore.currentOrgId)
const visible = computed(
  () => organizations.value.length > 0 || currentOrgId.value != null,
)
const currentOrgName = computed(() => {
  const found = organizations.value.find((org) => org.id === currentOrgId.value)
  if (found) return found.name
  if (currentOrgId.value != null) return `#${currentOrgId.value}`
  // 超管等无主组织但可切换的场景：提示可切换而不是误报“暂无组织”。
  return organizations.value.length > 0 ? t('org.switchOrganization') : t('org.noOrganizations')
})
const options = computed(() =>
  organizations.value.map((org) => ({
    label: org.name,
    key: org.id,
    disabled: org.id === currentOrgId.value,
  })),
)
// T7：存在“可切换目标”（至少一个非当前组织的选项）才渲染下拉。
// 单组织且即当前组织时唯一选项 disabled、点开无动作——静态展示；
// 超管无主组织（currentOrgId 为 null）时所有选项可选，保留可操作下拉。
const canSwitch = computed(() => options.value.some((option) => !option.disabled))

const loadOrganizations = async () => {
  if (!userStore.token) return
  await userStore.fetchMyOrganizations()
}

const handleSelect = async (key: number) => {
  if (switching.value || key === currentOrgId.value) return
  switching.value = true
  try {
    await userStore.switchOrganization(key)
    message.success(t('org.switchSuccess'))
  } catch {
    message.error(t('org.switchFailed'))
  } finally {
    switching.value = false
  }
}

onMounted(loadOrganizations)
</script>
