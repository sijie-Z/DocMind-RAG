<template>
  <div class="min-h-screen bg-slate-50 dark:bg-slate-950 transition-colors duration-300">
    <main class="max-w-3xl mx-auto px-6 py-16 sm:py-24">
      <h1 class="text-3xl font-bold text-slate-900 dark:text-white">
        {{ isTerms ? t('legal.terms.title') : t('legal.privacy.title') }}
      </h1>
      <p class="mt-4 text-slate-600 dark:text-slate-400 leading-relaxed">
        {{ isTerms ? t('legal.terms.description') : t('legal.privacy.description') }}
      </p>
    </main>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { useRoute } from 'vue-router'
import { useI18n } from 'vue-i18n'

const route = useRoute()
const { t } = useI18n()

const isTerms = computed(() => route.name === 'Terms')
</script>
