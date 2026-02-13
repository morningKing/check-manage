/**
 * 根组件
 *
 * 职责：
 * - 作为应用的根容器
 * - 渲染路由视图
 */
<template>
  <router-view />
</template>

<script setup lang="ts">
/**
 * App.vue - 应用根组件
 *
 * 这是 Vue 应用的入口组件，主要职责是：
 * 1. 提供路由视图容器
 * 2. 可以在这里添加全局的布局或状态初始化逻辑
 */
</script>

<style>
/* 全局样式重置 */
* {
  margin: 0;
  padding: 0;
  box-sizing: border-box;
}

html,
body,
#app {
  height: 100%;
  width: 100%;
}
</style>
