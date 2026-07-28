// Playwright config for the CONTRACT tier: the real backend, the real page, and a
// simulator the specs drive over HTTP.
//
// WHY A SEPARATE CONFIG RATHER THAN A SECOND PROJECT IN tests/e2e/playwright.config.js.
// Playwright starts every entry in `webServer` for the whole run, not per project,
// so adding the backend there would launch a Python app and a simulator on every
// hermetic run too. The hermetic tier's speed and independence are the reason it
// exists, so it stays exactly as it was and this tier brings its own file. The
// project inside is still named "contract", and CI runs it as its own job.
//
// BUDGET: this whole tier is meant to finish well under four minutes in CI. Each
// spec drives an outage and waits on an observable, so the cost is dominated by
// the compressed poll interval (2s) rather than by page load.
const { defineConfig, devices } = require("@playwright/test");

const APP_PORT = 5174;
const SIM_PORT = 5175;

// The staleness thresholds are NOT here. They travel on the page URL through PR
// 1's flag-gated query seam, so the specs own them; duplicating them in the config
// would let the two drift and leave a spec waiting on a threshold nobody set.

module.exports = defineConfig({
  testDir: ".",
  testMatch: "*.contract.spec.js",
  fullyParallel: false, // one backend and one simulator; specs mutate shared upstream state
  workers: 1,
  forbidOnly: !!process.env.CI,
  // ZERO RETRIES, and not merely inherited from the hermetic config: a flaky
  // contract test is a bug to fix, because this tier's whole value is that a
  // failure means the composite lies. A retry would turn that signal into noise.
  retries: 0,
  reporter: process.env.CI ? "line" : "list",
  // Sized from what a spec actually has to outlast, not guessed: the page's own
  // 15s poll, then the 25s staleness threshold, then a second 15s poll for the
  // status line to repaint or for recovery to be ingested. That is about 70s of
  // unavoidable waiting on top of page load, so 90s would leave a healthy run
  // failing on the margin.
  timeout: 150_000,
  use: {
    baseURL: `http://127.0.0.1:${APP_PORT}`,
    // retain-on-failure, not on-first-retry: with retries at 0 there is never a
    // first retry, so the usual setting would capture nothing and leave a CI
    // failure in this tier with no trace to read.
    trace: "retain-on-failure",
  },
  projects: [{ name: "contract", use: { ...devices["Desktop Chrome"] } }],
  webServer: {
    command: "python tests/contract/serve_contract.py",
    url: `http://127.0.0.1:${APP_PORT}/api/status`,
    cwd: require("node:path").resolve(__dirname, "..", ".."),
    env: {
      CONTRACT_PORT: String(APP_PORT),
      CONTRACT_SIM_PORT: String(SIM_PORT),
    },
    // NEVER reuse, not even locally. Reuse only checks that something answers
    // APP_PORT; it cannot tell a matching backend from a leftover one pointed at a
    // simulator that has since exited, and the specs drive the simulator directly.
    // Attaching to the wrong pair produces connection-refused failures that look
    // like product bugs. A fresh pair costs about five seconds.
    reuseExistingServer: false,
    timeout: 60_000,
  },
  // The specs read these back rather than restating them. They need the SIM port to
  // drive the control endpoint and the APP origin to tell same-origin requests from
  // external ones, and two copies of a port number is the kind of duplication that
  // is silently wrong the first time either moves.
  metadata: { APP_PORT, SIM_PORT },
});
