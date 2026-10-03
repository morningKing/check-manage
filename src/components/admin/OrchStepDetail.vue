<script setup lang="ts">
defineProps<{ step: Record<string, any> | null }>()
</script>

<template>
  <div v-if="step" class="orch-step-detail" style="padding: 12px;">
    <h4 style="margin: 0 0 8px;">{{ step.node_id }}</h4>
    <el-tag :type="step.status === 'succeeded' ? 'success' : step.status === 'failed' ? 'danger' : 'info'" size="small">
      {{ step.status }}
    </el-tag>
    <p v-if="step.error_message" style="color: #f56c6c; margin: 8px 0;">
      {{ step.error_message }}
    </p>
    <p style="color: #909399; font-size: 12px; margin: 4px 0;">
      Attempts: {{ step.attempt_count ?? 0 }}
    </p>
    <pre v-if="step.output" style="background: #f5f7fa; padding: 8px; border-radius: 4px; font-size: 11px; overflow: auto; max-height: 200px;">{{ typeof step.output === 'object' ? JSON.stringify(step.output, null, 2) : step.output }}</pre>
  </div>
</template>
