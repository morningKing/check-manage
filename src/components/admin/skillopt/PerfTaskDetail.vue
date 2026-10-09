<template><div /></template>
<script setup lang="ts">
defineProps<{ attemptId: string; peerP50Ms: number | null }>()
defineEmits<{ (e: 'close'): void }>()
</script>
