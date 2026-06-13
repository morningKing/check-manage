/**
 * 欢迎卡片 Widget
 *
 * 显示渐变背景卡片，左侧图标，右侧标题和描述
 */
<template>
  <el-card class="welcome-card">
    <div class="welcome-content">
      <el-icon class="welcome-icon"><Monitor /></el-icon>
      <div class="welcome-text">
        <h1>{{ content.heading }}</h1>
        <p>{{ content.description }}</p>
      </div>
    </div>
  </el-card>
</template>

<script setup lang="ts">
import { Monitor } from '@element-plus/icons-vue'
import type { WidgetContentMap } from '@/types'

defineProps<{
  content: WidgetContentMap['welcome']
}>()
</script>

<style scoped lang="scss">
.welcome-card {
  background: linear-gradient(135deg, #2a2e36 0%, #16181d 100%);

  .welcome-content {
    display: flex;
    align-items: center;
    gap: 24px;
    padding: 20px;
  }

  .welcome-icon {
    font-size: 64px;
    color: rgba(255, 255, 255, 0.9);
  }

  .welcome-text {
    h1 {
      margin: 0 0 8px 0;
      font-size: 28px;
      color: #fff;
    }

    p {
      margin: 0;
      font-size: 16px;
      color: rgba(255, 255, 255, 0.85);
    }
  }
}
</style>