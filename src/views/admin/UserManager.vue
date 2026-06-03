/**
 * 用户管理页面
 *
 * 职责：
 * - 展示用户列表
 * - 新增/编辑/删除用户
 * - 分配用户角色
 * - 重置用户密码
 *
 * 仅管理员可访问
 */
<template>
  <div class="user-manager">
    <el-card>
      <template #header>
        <div class="card-header">
          <h2>用户管理</h2>
          <el-button type="primary" @click="handleAdd">
            <el-icon><Plus /></el-icon>
            新增用户
          </el-button>
        </div>
      </template>

      <el-table :data="userList" v-loading="loading" stripe border style="width: 100%">
        <el-table-column prop="username" label="用户名" width="150" />
        <el-table-column prop="displayName" label="显示名称" width="150" />
        <el-table-column prop="role" label="角色" width="120" align="center">
          <template #default="{ row }">
            <el-tag :type="getRoleTagType(row.role)" size="small">
              {{ roleLabel(row.role) }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="createdAt" label="创建时间" width="200">
          <template #default="{ row }">
            {{ formatDate(row.createdAt) }}
          </template>
        </el-table-column>
        <el-table-column label="操作" fixed="right" width="220" align="center">
          <template #default="{ row }">
            <el-button type="primary" link size="small" @click="handleEdit(row)">编辑</el-button>
            <el-button type="warning" link size="small" @click="handleResetPassword(row)">重置密码</el-button>
            <el-button
              type="danger"
              link
              size="small"
              :disabled="row.id === currentUserId"
              @click="handleDeleteConfirm(row)"
            >
              删除
            </el-button>
          </template>
        </el-table-column>
      </el-table>
    </el-card>

    <!-- 新增/编辑用户对话框 -->
    <el-dialog
      v-model="dialogVisible"
      :title="isEditMode ? '编辑用户' : '新增用户'"
      width="500px"
      :close-on-click-modal="false"
      destroy-on-close
    >
      <el-form ref="formRef" :model="formData" :rules="formRules" label-width="100px">
        <el-form-item label="用户名" prop="username">
          <el-input v-model="formData.username" :disabled="isEditMode" placeholder="请输入用户名" />
        </el-form-item>
        <el-form-item label="显示名称" prop="displayName">
          <el-input v-model="formData.displayName" placeholder="请输入显示名称" />
        </el-form-item>
        <el-form-item label="角色" prop="role">
          <el-select v-model="formData.role" placeholder="请选择角色" style="width: 100%">
            <el-option
              v-for="opt in roleStore.roles"
              :key="opt.id"
              :label="opt.name"
              :value="opt.id"
            />
          </el-select>
        </el-form-item>
        <el-form-item v-if="!isEditMode" label="密码" prop="password">
          <el-input
            v-model="formData.password"
            type="password"
            show-password
            placeholder="请输入密码（至少6位）"
          />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dialogVisible = false" :disabled="submitLoading">取消</el-button>
        <el-button type="primary" :loading="submitLoading" @click="handleSubmit">确定</el-button>
      </template>
    </el-dialog>

    <!-- 重置密码对话框 -->
    <el-dialog
      v-model="resetPasswordVisible"
      title="重置密码"
      width="400px"
      :close-on-click-modal="false"
    >
      <p>为用户 <strong>{{ resetTargetUser?.displayName }}</strong> 设置新密码：</p>
      <el-input
        v-model="newPassword"
        type="password"
        show-password
        placeholder="请输入新密码（至少6位）"
        style="margin-top: 12px"
      />
      <template #footer>
        <el-button @click="resetPasswordVisible = false">取消</el-button>
        <el-button type="primary" :loading="resetLoading" @click="handleResetSubmit">确定</el-button>
      </template>
    </el-dialog>

    <!-- 删除确认对话框 -->
    <ConfirmDialog
      v-model="deleteDialogVisible"
      title="删除确认"
      :message="`确定要删除用户「${deleteTargetUser?.displayName}」吗？删除后无法恢复。`"
      type="danger"
      confirm-text="删除"
      @confirm="handleDelete"
    />
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'
import { ElMessage } from 'element-plus'
import { Plus } from '@element-plus/icons-vue'
import type { FormInstance, FormRules } from 'element-plus'
import { getUserList, createUser, updateUser, deleteUser } from '@/api/user'
import { useAuthStore, useRoleStore } from '@/stores'
import { ConfirmDialog } from '@/components/common'
import type { UserInfo, UserRole } from '@/types'

// ==================== Store ====================

const authStore = useAuthStore()
const roleStore = useRoleStore()

// ==================== State ====================

const loading = ref(false)
const userList = ref<UserInfo[]>([])
const dialogVisible = ref(false)
const isEditMode = ref(false)
const submitLoading = ref(false)
const formRef = ref<FormInstance>()
const editingUserId = ref('')

const formData = ref({
  username: '',
  displayName: '',
  role: 'developer' as UserRole,
  password: '',
})

const deleteDialogVisible = ref(false)
const deleteTargetUser = ref<UserInfo | null>(null)

const resetPasswordVisible = ref(false)
const resetTargetUser = ref<UserInfo | null>(null)
const newPassword = ref('')
const resetLoading = ref(false)

// ==================== 计算属性 ====================

const currentUserId = computed(() => authStore.user?.id || '')

const formRules = computed<FormRules>(() => ({
  username: [{ required: true, message: '请输入用户名', trigger: 'blur' }],
  displayName: [{ required: true, message: '请输入显示名称', trigger: 'blur' }],
  role: [{ required: true, message: '请选择角色', trigger: 'change' }],
  password: isEditMode.value
    ? []
    : [
        { required: true, message: '请输入密码', trigger: 'blur' },
        { min: 6, message: '密码不能少于6个字符', trigger: 'blur' },
      ],
}))

// ==================== 方法 ====================

function roleLabel(roleId: string): string {
  return roleStore.roles.find((r) => r.id === roleId)?.name || roleId
}

function getRoleTagType(role: string): string {
  switch (role) {
    case 'admin': return 'danger'
    case 'developer': return ''
    case 'guest': return 'info'
    default: return 'info'
  }
}

function formatDate(value: string): string {
  if (!value) return '-'
  try {
    const date = new Date(value)
    if (isNaN(date.getTime())) return value
    const y = date.getFullYear()
    const m = String(date.getMonth() + 1).padStart(2, '0')
    const d = String(date.getDate()).padStart(2, '0')
    const h = String(date.getHours()).padStart(2, '0')
    const min = String(date.getMinutes()).padStart(2, '0')
    return `${y}-${m}-${d} ${h}:${min}`
  } catch {
    return value
  }
}

async function loadUsers(): Promise<void> {
  loading.value = true
  try {
    userList.value = await getUserList()
  } catch {
    ElMessage.error('加载用户列表失败')
  } finally {
    loading.value = false
  }
}

function handleAdd(): void {
  isEditMode.value = false
  editingUserId.value = ''
  formData.value = { username: '', displayName: '', role: 'developer', password: '' }
  dialogVisible.value = true
}

function handleEdit(row: UserInfo): void {
  isEditMode.value = true
  editingUserId.value = row.id
  formData.value = {
    username: row.username,
    displayName: row.displayName,
    role: row.role,
    password: '',
  }
  dialogVisible.value = true
}

async function handleSubmit(): Promise<void> {
  const valid = await formRef.value?.validate()
  if (!valid) return

  submitLoading.value = true
  try {
    if (isEditMode.value) {
      await updateUser(editingUserId.value, {
        displayName: formData.value.displayName,
        role: formData.value.role,
      })
      ElMessage.success('更新成功')
    } else {
      await createUser({
        username: formData.value.username,
        displayName: formData.value.displayName,
        role: formData.value.role,
        password: formData.value.password,
      })
      ElMessage.success('创建成功')
    }
    dialogVisible.value = false
    await loadUsers()
  } catch {
    // Error shown by interceptor
  } finally {
    submitLoading.value = false
  }
}

function handleDeleteConfirm(row: UserInfo): void {
  deleteTargetUser.value = row
  deleteDialogVisible.value = true
}

async function handleDelete(): Promise<void> {
  if (!deleteTargetUser.value) return
  try {
    await deleteUser(deleteTargetUser.value.id)
    ElMessage.success('删除成功')
    deleteDialogVisible.value = false
    await loadUsers()
  } catch {
    // Error shown by interceptor
  }
}

function handleResetPassword(row: UserInfo): void {
  resetTargetUser.value = row
  newPassword.value = ''
  resetPasswordVisible.value = true
}

async function handleResetSubmit(): Promise<void> {
  if (!resetTargetUser.value) return
  if (!newPassword.value || newPassword.value.length < 6) {
    ElMessage.warning('密码不能少于6个字符')
    return
  }
  resetLoading.value = true
  try {
    await updateUser(resetTargetUser.value.id, { password: newPassword.value })
    ElMessage.success('密码重置成功')
    resetPasswordVisible.value = false
  } catch {
    // Error shown by interceptor
  } finally {
    resetLoading.value = false
  }
}

// ==================== 生命周期 ====================

onMounted(() => {
  loadUsers()
  roleStore.loadRoles()
})
</script>

<style scoped lang="scss">
.user-manager {
  height: 100%;
}

.card-header {
  display: flex;
  justify-content: space-between;
  align-items: center;

  h2 {
    margin: 0;
    font-size: 18px;
    font-weight: 600;
    color: #303133;
  }
}
</style>
