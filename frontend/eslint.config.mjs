/**
 * ESLint flat config（ESLint 9+ 的配置格式，取代 .eslintrc.cjs）
 *
 * 迁移来源：.eslintrc.cjs
 *   extends: plugin:vue/vue3-essential        -> pluginVue.configs['flat/essential']
 *            eslint:recommended               -> vueTsConfigs.eslintRecommended
 *            @vue/eslint-config-typescript/recommended -> vueTsConfigs.recommended
 *   parser / parserOptions                     -> 由 defineConfigWithVueTs 统一处理
 *   overrides                                  -> 拆成独立的 config 对象（见下）
 *
 * 注意：ESLint 9 起不再读 .eslintignore，也不再支持 --ext / --ignore-path，
 * 忽略规则必须写在下面的 ignores 里。
 */
import pluginVue from 'eslint-plugin-vue'
import { defineConfigWithVueTs, vueTsConfigs } from '@vue/eslint-config-typescript'

export default defineConfigWithVueTs(
  // 忽略目录（对应原脚本的 --ignore-path .gitignore 中与前端相关的部分）
  {
    name: 'app/ignores',
    ignores: ['dist/**', 'coverage/**', 'node_modules/**', 'public/**', '*.min.js'],
  },

  // Vue 3 基础规则（对应 plugin:vue/vue3-essential）
  pluginVue.configs['flat/essential'],

  // eslint:recommended + typescript-eslint recommended（对应原 extends 的另两项）
  vueTsConfigs.eslintRecommended,
  vueTsConfigs.recommended,

  // 原 .eslintrc.cjs 的 rules
  {
    name: 'app/rules',
    rules: {
      'vue/multi-word-component-names': 'off',
      'no-unused-vars': 'off',
      'no-console': 'warn',
      '@typescript-eslint/no-explicit-any': 'off',
      '@typescript-eslint/no-unused-vars': [
        'warn',
        { argsIgnorePattern: '^_', varsIgnorePattern: '^_' },
      ],
    },
  },
)
