/**
 * 内容区域组件
 *
 * 职责：
 * - 作为路由视图的容器
 * - 提供过渡动画效果
 * - 可扩展添加标签页等功能
 */
<template>
  <div class="content-area">
    <!-- 路由视图，带过渡动画 -->
    <router-view v-slot="{ Component, route }">
      <transition name="fade-transform" mode="out-in">
        <keep-alive>
          <component :is="Component" :key="route.path" />
        </keep-alive>
      </transition>
    </router-view>
  </div>
</template>

<script setup lang="ts">
/**
 * ContentArea 组件脚本
 *
 * 当前为简单实现，主要提供：
 * 1. 路由视图容器
 * 2. 页面切换过渡动画
 * 3. 组件缓存（keep-alive）
 *
 * 可扩展功能：
 * - 多标签页管理
 * - 面包屑导航
 * - 页面刷新功能
 */
</script>

<style scoped lang="scss">
.content-area {
  height: 100%;
  width: 100%;
  overflow: auto;
}

/* 页面切换过渡动画 */
.fade-transform-enter-active,
.fade-transform-leave-active {
  transition: all 0.3s ease;
}

.fade-transform-enter-from {
  opacity: 0;
  transform: translateX(-20px);
}

.fade-transform-leave-to {
  opacity: 0;
  transform: translateX(20px);
}
</style>
