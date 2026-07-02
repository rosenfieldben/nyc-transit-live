// Playwright config for the hermetic frontend smoke suite. One browser
// (chromium), a handful of focused specs, no retries so a flake shows as a
// failure rather than being papered over. The webServer serves the static
// frontend; every /api/* request and the two unpkg Leaflet URLs are intercepted
// per-test by page.route (mock.js), so a CI run needs no network at test time.
const { defineConfig, devices } = require("@playwright/test");

const PORT = 5173;

module.exports = defineConfig({
  testDir: ".",
  fullyParallel: true,
  forbidOnly: !!process.env.CI, // a stray test.only fails CI instead of shrinking the suite
  retries: 0, // smoke suite is deterministic; no retry masking
  reporter: process.env.CI ? "line" : "list",
  use: {
    baseURL: `http://127.0.0.1:${PORT}`,
    trace: "on-first-retry",
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
  webServer: {
    command: "node tests/e2e/serve.js",
    url: `http://127.0.0.1:${PORT}`,
    cwd: require("node:path").resolve(__dirname, "..", ".."),
    env: { E2E_PORT: String(PORT) },
    reuseExistingServer: !process.env.CI,
    timeout: 30_000,
  },
});
