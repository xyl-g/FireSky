import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "./tests",
  timeout: 30_000,
  // 视觉基线确定性：时区/区域钉住后，时间戳与数字格式不含机器环境差异；
  // 快照名带 {-platform} 后缀，macOS 基线与未来 linux CI 基线互不覆盖。
  snapshotPathTemplate: "{testDir}/__screenshots__/{arg}{-platform}{ext}",
  use: {
    baseURL: "http://127.0.0.1:1420",
    screenshot: "only-on-failure",
    channel: process.env.PLAYWRIGHT_CHANNEL,
    timezoneId: "Asia/Shanghai",
    locale: "zh-CN",
  },
  webServer: {
    command: "npm run dev -- --host 127.0.0.1",
    url: "http://127.0.0.1:1420",
    reuseExistingServer: !process.env.CI,
    timeout: 30_000,
  },
});
