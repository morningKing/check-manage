<template>
  <div class="kefu-block-richtext"><MdPreview :modelValue="config.markdown || ''" :code-foldable="false" /></div>
</template>
<script setup lang="ts">
import { MdPreview } from 'md-editor-v3'
import 'md-editor-v3/lib/style.css'
defineProps<{ config: { markdown?: string } }>()
</script>
