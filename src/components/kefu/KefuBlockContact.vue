<template>
  <div class="kefu-block-contact">
    <div v-if="config.phone" class="row">📞 {{ config.phone }}</div>
    <div v-if="config.email" class="row">✉️ {{ config.email }}</div>
    <div v-if="config.hours" class="row">🕒 {{ config.hours }}</div>
    <div v-if="config.wechat" class="row">💬 {{ config.wechat }}</div>
  </div>
</template>
<script setup lang="ts">
defineProps<{ config: { phone?: string; email?: string; hours?: string; wechat?: string } }>()
</script>
