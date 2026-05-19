/**
 * 菜单展开性能测试脚本（含极端场景）
 *
 * 测试场景：
 * 1. 普通场景：性能测试工作空间（约90个菜单）
 * 2. 极端场景：极端测试工作空间（约163个菜单，单个项目35+子菜单）
 */

import { chromium } from 'playwright'

async function runPerformanceTest() {
  const results = []
  let browser = null
  let page = null

  try {
    console.log('=== 启动浏览器 ===')
    browser = await chromium.launch({
      headless: true,
      args: ['--disable-gpu', '--no-sandbox']
    })

    const context = await browser.newContext()
    page = await context.newPage()
    page.setDefaultTimeout(30000)

    console.log('=== 访问登录页面 ===')
    await page.goto('http://localhost:5174/login', { waitUntil: 'networkidle' })

    console.log('=== 执行登录 ===')
    await page.fill('input[placeholder="请输入用户名"]', 'admin')
    await page.fill('input[placeholder="请输入密码"]', 'admin123')
    await page.click('.login-btn')

    await page.waitForURL(/\/home|\/dashboard/, { timeout: 10000 })
    console.log('[OK] 登录成功')

    await page.waitForSelector('.side-menu-list', { timeout: 10000 })
    console.log('[OK] 菜单加载完成')

    // ===== 测试普通场景 =====
    console.log('\n========================================')
    console.log('普通场景测试（性能测试工作空间）')
    console.log('========================================')

    // 测试1：首次展开工作空间菜单
    console.log('\n[测试1] 首次展开 "性能测试工作空间"')
    const workspaceMenu1 = page.locator('.el-sub-menu').filter({ hasText: '性能测试工作空间' })

    const count1 = await workspaceMenu1.count()
    if (count1 > 0) {
      const start1 = Date.now()
      await workspaceMenu1.first().click()
      await page.waitForTimeout(200)
      const end1 = Date.now()

      results.push({ testName: '普通-首次展开工作空间', duration: end1 - start1, success: true })
      console.log(`[OK] 耗时: ${end1 - start1}ms`)
    }

    // 测试2：展开一个项目菜单（约5个子数据页）
    console.log('\n[测试2] 展开子项目菜单（约5个子数据页）')
    const projectMenu1 = page.locator('.el-sub-menu').filter({ hasText: '测试项目5' })

    const pCount1 = await projectMenu1.count()
    if (pCount1 > 0) {
      const start2 = Date.now()
      await projectMenu1.first().click()
      await page.waitForTimeout(200)
      const end2 = Date.now()

      results.push({ testName: '普通-展开子项目（5个子菜单）', duration: end2 - start2, success: true })
      console.log(`[OK] 耗时: ${end2 - start2}ms`)
    }

    // ===== 测试极端场景 =====
    console.log('\n========================================')
    console.log('极端场景测试（极端测试工作空间）')
    console.log('========================================')

    // 先折叠之前的菜单
    await workspaceMenu1.first().click()
    await page.waitForTimeout(300)

    // 测试3：展开极端工作空间（3个项目，每个35+数据页）
    console.log('\n[测试3] 首次展开 "极端测试工作空间"（163个数据页）')
    const extremeWorkspace = page.locator('.el-sub-menu').filter({ hasText: '极端测试工作空间' })

    const eCount = await extremeWorkspace.count()
    if (eCount > 0) {
      const start3 = Date.now()
      await extremeWorkspace.first().click()
      await page.waitForTimeout(200)
      const end3 = Date.now()

      results.push({ testName: '极端-展开工作空间', duration: end3 - start3, success: true })
      console.log(`[OK] 耗时: ${end3 - start3}ms`)
    }

    // 测试4：展开有35个子数据页的项目
    console.log('\n[测试4] 展开"极端项目3"（35个子数据页）')
    const extremeProject3 = page.locator('.el-sub-menu').filter({ hasText: '极端项目3' })

    const ep3Count = await extremeProject3.count()
    if (ep3Count > 0) {
      const start4 = Date.now()
      await extremeProject3.first().click()
      await page.waitForTimeout(300)  // 等待更多子菜单渲染
      const end4 = Date.now()

      results.push({ testName: '极端-展开项目（35个子菜单）', duration: end4 - start4, success: true })
      console.log(`[OK] 耗时: ${end4 - start4}ms`)

      // 检查子菜单数量
      const subItems = await page.locator('.el-sub-menu.is-opened .el-menu-item').count()
      console.log(`[INFO] 渲染的菜单项数量: ${subItems}`)
    }

    // 测试5：展开有分组的项目（分组内还有30+子数据页）
    console.log('\n[测试5] 展开"极端项目2"（含分组，总共65个子菜单）')
    const extremeProject2 = page.locator('.el-sub-menu').filter({ hasText: '极端项目2' })

    // 先折叠项目3
    await extremeProject3.first().click()
    await page.waitForTimeout(200)

    const ep2Count = await extremeProject2.count()
    if (ep2Count > 0) {
      const start5 = Date.now()
      await extremeProject2.first().click()
      await page.waitForTimeout(300)
      const end5 = Date.now()

      results.push({ testName: '极端-展开项目（含分组，65个子菜单）', duration: end5 - start5, success: true })
      console.log(`[OK] 耗时: ${end5 - start5}ms`)
    }

    // 测试6：展开分组菜单（30个子数据页）
    console.log('\n[测试6] 展开"数据分组B"（30个子数据页）')
    const extremeGroupB = page.locator('.el-sub-menu').filter({ hasText: '数据分组B' })

    const egCount = await extremeGroupB.count()
    if (egCount > 0) {
      const start6 = Date.now()
      await extremeGroupB.first().click()
      await page.waitForTimeout(300)
      const end6 = Date.now()

      results.push({ testName: '极端-展开分组（30个子菜单）', duration: end6 - start6, success: true })
      console.log(`[OK] 耗时: ${end6 - start6}ms`)
    }

    // 测试7：连续快速展开多个菜单
    console.log('\n[测试7] 连续快速展开3个项目菜单')
    // 先折叠所有
    await extremeGroupB.first().click()
    await extremeProject2.first().click()
    await page.waitForTimeout(200)

    const allExtremeProjects = page.locator('.el-sub-menu').filter({ hasText: '极端项目' })
    const aepCount = await allExtremeProjects.count()

    if (aepCount >= 3) {
      const start7 = Date.now()
      for (let i = 0; i < 3; i++) {
        await allExtremeProjects.nth(i).click()
        await page.waitForTimeout(100)
      }
      const end7 = Date.now()

      results.push({ testName: '极端-连续展开3个项目', duration: end7 - start7, success: true })
      console.log(`[OK] 耗时: ${end7 - start7}ms`)
    }

    // 测试8：检查总菜单数量
    console.log('\n[测试8] 检查菜单渲染数量')
    const allMenuItems = await page.locator('.el-menu-item').count()
    const allSubMenus = await page.locator('.el-sub-menu').count()

    results.push({ testName: '菜单总数量', duration: allMenuItems + allSubMenus, success: true })
    console.log(`[OK] 菜单项: ${allMenuItems}, 子菜单: ${allSubMenus}, 总计: ${allMenuItems + allSubMenus}`)

  } catch (error) {
    console.error('[ERROR] 测试失败:', error.message)
    results.push({ testName: '测试过程', duration: 0, success: false, error: error.message })
  } finally {
    if (browser) {
      await browser.close()
    }
  }

  return results
}

async function main() {
  console.log('========================================')
  console.log('菜单展开性能测试（含极端场景）')
  console.log('========================================\n')

  const results = await runPerformanceTest()

  console.log('\n========================================')
  console.log('测试结果汇总')
  console.log('========================================')

  const successCount = results.filter(r => r.success).length

  results.forEach(result => {
    const status = result.success ? '✓' : '✗'
    const duration = result.success ? `${result.duration}ms` : 'N/A'
    console.log(`${status} ${result.testName}: ${duration}`)
  })

  // 性能评估
  console.log('\n========================================')
  console.log('性能评估')
  console.log('========================================')

  const normalExpand = results.find(r => r.testName === '普通-首次展开工作空间')
  const extremeExpand35 = results.find(r => r.testName === '极端-展开项目（35个子菜单）')
  const extremeExpand30 = results.find(r => r.testName === '极端-展开分组（30个子菜单）')

  console.log('\n性能基准:')
  console.log('  - 优秀: < 100ms')
  console.log('  - 良好: < 300ms')
  console.log('  - 一般: < 500ms')
  console.log('  - 较差: > 500ms')

  console.log('\n评估结果:')
  if (normalExpand?.success) {
    evaluatePerformance(normalExpand.duration, '普通场景-工作空间展开')
  }
  if (extremeExpand35?.success) {
    evaluatePerformance(extremeExpand35.duration, '极端场景-35个子菜单展开')
  }
  if (extremeExpand30?.success) {
    evaluatePerformance(extremeExpand30.duration, '极端场景-30个子菜单展开')
  }
}

function evaluatePerformance(duration, testName) {
  if (duration < 100) {
    console.log(`  ✓ ${testName}: 优秀 (${duration}ms)`)
  } else if (duration < 300) {
    console.log(`  ○ ${testName}: 良好 (${duration}ms)`)
  } else if (duration < 500) {
    console.log(`  △ ${testName}: 一般 (${duration}ms)，建议优化`)
  } else {
    console.log(`  ✗ ${testName}: 较差 (${duration}ms)，需要优化`)
  }
}

main().catch(console.error)