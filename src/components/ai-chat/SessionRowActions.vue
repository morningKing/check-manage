<script setup lang="ts">
/**
 * 会话行操作区（置顶 + 更多⋯）。
 *
 * 会话行 hover 才显示，根元素必须是 .session-item__actions —— 父组件的
 * `.session-item:hover &__actions` 展开规则依赖父子 scoped 属性穿透（Vue 会把
 * 父作用域 id 落在子组件根元素上）。
 *
 * 简化原则：行内只留「置顶」「更多」两个图标，重命名/关闭/清空/移动分组/
 * 删除全部收进「更多」菜单，功能不裁剪（原 6 个行内图标 → 2 个）。
 */
import { computed } from 'vue'
import { ElDropdown, ElDropdownMenu, ElDropdownItem, ElIcon } from 'element-plus'
import {
  Top, EditPen, Close, RefreshRight, Brush, Delete, MoreFilled, FolderAdd,
  Folder, FolderOpened, Collection, Tickets, Timer, Monitor, DataLine, Files, Aim,
  ChatDotRound,
} from '@element-plus/icons-vue'
import type { Component } from 'vue'
import { useAiChatStore } from '@/stores/aiChat'

const props = defineProps<{
  s: { id: string; title?: string; status?: string; pinned?: boolean }
}>()
const emit = defineEmits<{
  pin: []
  rename: []
  close: []
  reopen: []
  clear: []
  delete: []
  /** 移动到已有分组；'__ungrouped' 表示移回未分组 */
  move: [target: string]
  /** ＋ 新建分组…（父组件建组成功后自动把会话移入） */
  createmove: []
}>()

const store = useAiChatStore()
const currentGroupId = computed(() => store.sessionGroupId[props.s.id] ?? null)

// 与分组侧栏头同一套图标映射（存储的是 Element Plus 图标组件名）
const GROUP_ICON_MAP: Record<string, Component> = {
  Folder, FolderOpened, Collection, Tickets, Timer, Monitor, DataLine, Files, Aim, ChatDotRound,
}
function groupIcon(name?: string): Component {
  return GROUP_ICON_MAP[name || 'Folder'] || Folder
}

function onCommand(cmd: string | number | object) {
  const key = String(cmd)
  if (key === 'rename') return emit('rename')
  if (key === 'close') return emit('close')
  if (key === 'reopen') return emit('reopen')
  if (key === 'clear') return emit('clear')
  if (key === 'delete') return emit('delete')
  if (key === '__new') return emit('createmove')
  if (key === '__ungrouped') return emit('move', '__ungrouped')
  return emit('move', key)
}
</script>

<template>
  <span class="session-item__actions" @click.stop>
    <ElIcon
      :class="{ 'pin-on': s.pinned }"
      :title="s.pinned ? '取消置顶' : '置顶'"
      data-test="session-pin-btn"
      @click="emit('pin')"
    ><Top /></ElIcon>
    <ElDropdown trigger="click" @command="onCommand">
      <ElIcon title="更多操作" data-test="session-more-btn"><MoreFilled /></ElIcon>
      <template #dropdown>
        <ElDropdownMenu data-test="session-more-menu">
          <ElDropdownItem command="rename">
            <ElIcon class="more-menu__icon"><EditPen /></ElIcon>重命名
          </ElDropdownItem>
          <ElDropdownItem v-if="s.status === 'closed'" command="reopen">
            <ElIcon class="more-menu__icon"><RefreshRight /></ElIcon>重开会话
          </ElDropdownItem>
          <ElDropdownItem v-else command="close">
            <ElIcon class="more-menu__icon"><Close /></ElIcon>关闭会话
          </ElDropdownItem>
          <ElDropdownItem command="clear">
            <ElIcon class="more-menu__icon"><Brush /></ElIcon>清空会话（清空历史和工作区文件）
          </ElDropdownItem>
          <!-- 分组区：disabled 项作小节标题 -->
          <ElDropdownItem disabled divided>移动到分组</ElDropdownItem>
          <ElDropdownItem
            v-for="grp in store.groups" :key="grp.id" :command="grp.id"
            :disabled="grp.id === currentGroupId"
          >
            <ElIcon class="more-menu__icon" :data-icon="grp.icon || 'Folder'">
              <component :is="groupIcon(grp.icon)" />
            </ElIcon>
            {{ grp.name }}（{{ store.groupedSessions(grp.id).length }}）
          </ElDropdownItem>
          <ElDropdownItem command="__ungrouped" :disabled="!currentGroupId">
            <ElIcon class="more-menu__icon"><Close /></ElIcon>未分组
          </ElDropdownItem>
          <ElDropdownItem command="__new">
            <ElIcon class="more-menu__icon"><FolderAdd /></ElIcon>＋ 新建分组…
          </ElDropdownItem>
          <ElDropdownItem command="delete" divided class="session-menu__danger">
            <ElIcon class="more-menu__icon"><Delete /></ElIcon>删除会话
          </ElDropdownItem>
        </ElDropdownMenu>
      </template>
    </ElDropdown>
  </span>
</template>

<style scoped lang="scss">
.more-menu__icon { margin-right: 6px; color: var(--el-text-color-secondary); }
.pin-on { color: var(--el-color-warning); }
/* 菜单 teleport 到 body，靠 scoped 属性命中菜单项 */
.session-menu__danger { color: var(--el-color-danger); }
</style>
