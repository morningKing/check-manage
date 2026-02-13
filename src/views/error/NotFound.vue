/**
 * 404 页面
 *
 * 当访问不存在的路由时显示
 */
<template>
  <div class="not-found">
    <div class="content">
      <h1 class="error-code">404</h1>
      <p class="error-message">页面不存在</p>
      <p class="error-description">
        抱歉，您访问的页面不存在或已被移除
      </p>
      <el-button type="primary" @click="goHome">
        返回首页
      </el-button>
    </div>
  </div>
</template>

<script setup lang="ts">
import { useRouter } from 'vue-router'

const router = useRouter()

function goHome(): void {
  router.push('/home')
}
</script>

<style scoped lang="scss">
.not-found {
  display: flex;
  align-items: center;
  justify-content: center;
  min-height: 100vh;
  background-color: #f5f7fa;

  .content {
    text-align: center;
    padding: 40px;
  }

  .error-code {
    font-size: 120px;
    font-weight: 700;
    color: #409eff;
    margin: 0;
    line-height: 1;
  }

  .error-message {
    font-size: 24px;
    font-weight: 500;
    color: #303133;
    margin: 20px 0 10px;
  }

  .error-description {
    font-size: 14px;
    color: #909399;
    margin-bottom: 30px;
  }
}
</style>
