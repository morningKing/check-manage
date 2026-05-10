/**
 * 登录页面
 *
 * 职责：
 * - 提供用户名密码登录表单
 * - 验证输入并调用认证接口
 * - 登录成功后跳转首页
 */
<template>
  <div class="login-view">
    <div class="login-card">
      <div class="login-header">
        <el-icon class="login-logo"><Monitor /></el-icon>
        <h1>{{ systemName }}</h1>
      </div>
      <el-form
        ref="formRef"
        :model="formData"
        :rules="formRules"
        label-width="0"
        size="large"
        @keyup.enter="handleLogin"
      >
        <el-form-item prop="username">
          <el-input
            v-model="formData.username"
            placeholder="请输入用户名"
            :prefix-icon="User"
          />
        </el-form-item>
        <el-form-item prop="password">
          <el-input
            v-model="formData.password"
            type="password"
            placeholder="请输入密码"
            :prefix-icon="Lock"
            show-password
          />
        </el-form-item>
        <el-form-item>
          <el-button
            type="primary"
            :loading="loading"
            class="login-btn"
            @click="handleLogin"
          >
            登 录
          </el-button>
        </el-form-item>
      </el-form>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import type { FormInstance, FormRules } from 'element-plus'
import { ElMessage } from 'element-plus'
import { Monitor, User, Lock } from '@element-plus/icons-vue'
import { useAuthStore, useSystemConfigStore } from '@/stores'

const router = useRouter()
const authStore = useAuthStore()
const systemConfigStore = useSystemConfigStore()
const formRef = ref<FormInstance>()
const loading = ref(false)

const formData = ref({
  username: '',
  password: '',
})

const formRules: FormRules = {
  username: [{ required: true, message: '请输入用户名', trigger: 'blur' }],
  password: [{ required: true, message: '请输入密码', trigger: 'blur' }],
}

/**
 * 系统名称（登录页标题）
 */
const systemName = computed(() => systemConfigStore.systemName)

/**
 * 登录页无需登录态，直接获取系统配置
 */
onMounted(() => {
  systemConfigStore.fetchSystemConfig()
})

async function handleLogin(): Promise<void> {
  const valid = await formRef.value?.validate()
  if (!valid) return

  loading.value = true
  try {
    await authStore.login(formData.value)
    ElMessage.success('登录成功')
    router.replace('/home')
  } catch {
    // Error message already shown by response interceptor
  } finally {
    loading.value = false
  }
}
</script>

<style scoped lang="scss">
.login-view {
  height: 100vh;
  display: flex;
  align-items: center;
  justify-content: center;
  background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
}

.login-card {
  width: 400px;
  padding: 40px;
  background: #fff;
  border-radius: 8px;
  box-shadow: 0 8px 32px rgba(0, 0, 0, 0.15);
}

.login-header {
  text-align: center;
  margin-bottom: 32px;

  .login-logo {
    font-size: 48px;
    color: #409eff;
  }

  h1 {
    margin: 12px 0 0;
    font-size: 22px;
    font-weight: 600;
    color: #303133;
  }
}

.login-btn {
  width: 100%;
}
</style>
