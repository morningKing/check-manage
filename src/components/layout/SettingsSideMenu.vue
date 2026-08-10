<template>
  <div class="settings-menu">
    <button type="button" class="settings-menu__back" @click="goBack">
      <el-icon><ArrowLeft /></el-icon>
      <span>返回工作区</span>
    </button>

    <nav class="settings-menu__nav">
      <div v-for="g in groups" :key="g.id" class="settings-menu__group">
        <div class="settings-menu__group-label">
          <el-icon><component :is="iconOf(g.icon)" /></el-icon>
          <span>{{ g.label }}</span>
        </div>
        <RouterLink
          v-for="item in g.items"
          :key="item.id"
          :to="`/admin/${item.id}`"
          class="settings-menu__item"
          :class="{
            'settings-menu__item--active': route.path === `/admin/${item.id}`,
            'settings-menu__item--danger': item.danger,
          }"
        >
          {{ item.label }}
        </RouterLink>
      </div>
    </nav>
  </div>
</template>

<script setup lang="ts">
/**
 * 设置中心侧边栏 —— 平铺全部有权限的设置功能。
 *
 * 分组标题不可点、不折叠：分类在这里只是视觉分组，不再有路由意义。
 * 一次点击到达任何功能是这次改造的核心目标，任何「再展开一层」的设计都
 * 是在把问题改回去。
 */
import { computed } from 'vue'
import { useRoute, useRouter, RouterLink } from 'vue-router'
import {
  ArrowLeft, Lock, Files, Link, MagicStick, DataLine, Monitor, Setting,
} from '@element-plus/icons-vue'
import type { Component } from 'vue'
import { useAuthStore, useAppStore } from '@/stores'
import { filterGroups } from '@/views/admin/hub/settingsCatalog'

const route = useRoute()
const router = useRouter()
const auth = useAuthStore()
const app = useAppStore()

const groups = computed(() => filterGroups(auth.can))

const ICON_MAP: Record<string, Component> = {
  Lock, Files, Link, MagicStick, DataLine, Monitor, Setting,
}
function iconOf(name: string): Component {
  return ICON_MAP[name] ?? Setting
}

function goBack(): void {
  router.push(app.lastBusinessPath || '/home')
}
</script>

<style scoped lang="scss">
.settings-menu {
  display: flex;
  flex-direction: column;
  height: 100%;
  overflow-y: auto;
  padding: 8px 0 16px;
}

.settings-menu__back {
  display: flex;
  align-items: center;
  gap: 6px;
  margin: 0 12px 12px;
  padding: 8px 10px;
  border: none;
  border-radius: 6px;
  background: transparent;
  color: var(--el-text-color-regular);
  font-size: 14px;
  cursor: pointer;

  &:hover { background: var(--el-fill-color-light); color: var(--el-color-primary); }
}

.settings-menu__group { margin-bottom: 14px; }

.settings-menu__group-label {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 4px 22px;
  color: var(--el-text-color-secondary);
  font-size: 12px;
  user-select: none;
}

.settings-menu__item {
  display: block;
  padding: 7px 22px 7px 44px;
  color: var(--el-text-color-regular);
  font-size: 14px;
  text-decoration: none;

  &:hover { background: var(--el-fill-color-light); }
}

.settings-menu__item--active {
  color: var(--el-color-primary);
  background: var(--el-color-primary-light-9);
  font-weight: 500;
}

.settings-menu__item--danger {
  color: var(--el-color-danger);

  &:hover { background: var(--el-color-danger-light-9); }
}
</style>
