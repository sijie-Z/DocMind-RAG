<template>
  <div>
    <div v-if="loading" class="p-6 space-y-5">
      <n-skeleton height="120px" round />
      <div class="grid grid-cols-2 lg:grid-cols-4 gap-4">
        <n-skeleton v-for="i in 4" :key="i" height="120px" round />
      </div>
      <div class="grid grid-cols-1 lg:grid-cols-2 gap-5">
        <n-skeleton height="300px" round />
        <n-skeleton height="300px" round />
      </div>
    </div>
    <template v-else>
      <AdminDashboard v-if="userStore.isAdmin" />
      <UserDashboard v-else />
    </template>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { useUserStore } from '@/stores/user'
import AdminDashboard from './admin.vue'
import UserDashboard from './user.vue'

const userStore = useUserStore()
const loading = ref(true)

onMounted(() => {
  setTimeout(() => { loading.value = false }, 300)
})
</script>