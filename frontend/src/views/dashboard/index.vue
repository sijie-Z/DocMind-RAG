<template>
  <div>
    <AdminDashboard v-if="userStore.isAdmin" />
    <UserDashboard v-else />
  </div>
</template>

<script setup lang="ts">
import { useUserStore } from '@/stores/user'
import AdminDashboard from './admin.vue'
import UserDashboard from './user.vue'

const userStore = useUserStore()
</script>
