<template>
  <div v-if="visible" data-testid="org-switcher" class="min-w-0">
    <n-dropdown
      trigger="click"
      :options="options"
      :disabled="switching || organizations.length === 0"
      @select="handleSelect"
    >
      <n-button
        quaternary
        size="small"
        class="max-w-[13rem]"
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
  </div>
</template>

<script setup lang="ts">
/**
 * 顶栏组织切换器（B1）。
 *
 * 数据源：GET /users/me/organizations（成员关系 + 主组织兜底）。
 * 切换：POST /users/me/organizations/{id}/switch —— 服务端校验成员关系并
 * 落库当前组织；成功后 user store 的 organizationVersion 递增，布局层页面
 * key 随之变化，当前页面重挂载并按新组织重新拉取文档/会话等数据。
 * 失败时只提示、不改变本地组织状态。
 */
import { computed, onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { NButton, NDropdown, NIcon } from 'naive-ui'
import { BusinessOutline, ChevronDownOutline } from '@vicons/ionicons5'
import { useUserStore } from '@/stores/user'
import { useDedupedMessage } from '@/utils/message'

interface OrgOption {
  id: number
  name: string
}

const { t } = useI18n()
const userStore = useUserStore()
const message = useDedupedMessage()

const organizations = ref<OrgOption[]>([])
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

const loadOrganizations = async () => {
  if (!userStore.token) return
  organizations.value = await userStore.fetchMyOrganizations()
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
