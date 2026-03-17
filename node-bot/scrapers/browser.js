'use strict';

const puppeteer = require('rebrowser-puppeteer-core');
const { launch: launchChrome } = require('chrome-launcher');
const logger = require('../utils/logger');

// Realistic user agents pool
const USER_AGENTS = [
  'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36',
  'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36',
  'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36',
  'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36',
  'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36 Edg/130.0.0.0',
];

function randomUserAgent() {
  return USER_AGENTS[Math.floor(Math.random() * USER_AGENTS.length)];
}

function randomDelay(min = 800, max = 2500) {
  return new Promise(r => setTimeout(r, Math.floor(Math.random() * (max - min) + min)));
}

let chromePath = null;

async function getChromePath() {
  if (chromePath) return chromePath;

  // Check env vars first
  const envPaths = [
    process.env.CHROME_PATH,
    process.env.CHROME_BIN,
    '/usr/bin/chromium',
    '/usr/bin/chromium-browser',
    '/usr/bin/google-chrome',
    '/usr/bin/google-chrome-stable',
  ];

  const fs = require('fs');
  for (const p of envPaths) {
    if (p && fs.existsSync(p)) {
      chromePath = p;
      return chromePath;
    }
  }

  // Try chrome-launcher discovery
  try {
    const chrome = await launchChrome({ startingUrl: 'about:blank', chromeFlags: ['--headless'] });
    chromePath = chrome.executablePath || chrome.process?.spawnfile;
    await chrome.kill();
    return chromePath;
  } catch {}

  throw new Error('Chrome/Chromium not found. Set CHROME_PATH or CHROME_BIN env var.');
}

/**
 * Launch a stealth browser with anti-detection patches.
 */
async function launchBrowser() {
  const executablePath = await getChromePath();
  const ua = randomUserAgent();

  const browser = await puppeteer.launch({
    executablePath,
    headless: true,
    args: [
      '--no-sandbox',
      '--disable-setuid-sandbox',
      '--disable-infobars',
      '--disable-dev-shm-usage',
      '--disable-blink-features=AutomationControlled',
      '--disable-features=IsolateOrigins,site-per-process',
      '--window-size=1920,1080',
      `--user-agent=${ua}`,
      '--lang=en-US,en;q=0.9',
    ],
    ignoreHTTPSErrors: true,
  });

  return browser;
}

/**
 * Create a stealth page in a browser instance.
 */
async function createStealthPage(browser) {
  const page = await browser.newPage();
  const ua = randomUserAgent();

  await page.setUserAgent(ua);
  await page.setViewport({ width: 1920, height: 1080 });
  await page.setExtraHTTPHeaders({
    'Accept-Language': 'en-US,en;q=0.9',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
    'Accept-Encoding': 'gzip, deflate, br',
    'Upgrade-Insecure-Requests': '1',
    'Sec-Fetch-Dest': 'document',
    'Sec-Fetch-Mode': 'navigate',
    'Sec-Fetch-Site': 'none',
    'Cache-Control': 'max-age=0',
  });

  // Automation detection bypass
  await page.evaluateOnNewDocument(() => {
    // Remove webdriver flag
    Object.defineProperty(navigator, 'webdriver', { get: () => undefined });

    // Add languages
    Object.defineProperty(navigator, 'languages', { get: () => ['en-US', 'en'] });

    // Add plugins (empty in headless)
    Object.defineProperty(navigator, 'plugins', {
      get: () => [1, 2, 3, 4, 5].map(i => ({ name: `Plugin ${i}` })),
    });

    // Spoof chrome runtime
    window.chrome = {
      runtime: {},
      loadTimes: () => {},
      csi: () => {},
      app: {},
    };

    // Override permissions
    const originalQuery = window.navigator.permissions?.query;
    if (originalQuery) {
      window.navigator.permissions.query = (parameters) =>
        parameters.name === 'notifications'
          ? Promise.resolve({ state: Notification.permission })
          : originalQuery(parameters);
    }
  });

  return page;
}

/**
 * Scrape a URL with stealth browser, return { html, text, url }.
 * Automatically handles navigation and waits for page to settle.
 */
async function scrapeUrl(url, options = {}) {
  const {
    waitForSelector = null,
    waitMs = 3000,
    timeout = 45000,
  } = options;

  const browser = await launchBrowser();
  const page = await createStealthPage(browser);

  try {
    await randomDelay(300, 800);

    await page.goto(url, {
      waitUntil: 'networkidle2',
      timeout,
    });

    await randomDelay(800, 1500);

    if (waitForSelector) {
      try {
        await page.waitForSelector(waitForSelector, { timeout: 15000 });
      } catch {
        logger.debug(`Selector ${waitForSelector} not found on ${url}`);
      }
    } else {
      await new Promise(r => setTimeout(r, waitMs));
    }

    const html = await page.content();
    const text = await page.evaluate(() => document.body?.innerText || '');
    const finalUrl = page.url();

    return { html, text, url: finalUrl };
  } finally {
    await browser.close().catch(() => {});
  }
}

/**
 * Execute a scrape with a custom page callback (for complex interactions).
 *
 * @param {string}   url
 * @param {Function} callback  - receives (page, browser). If callback calls
 *                               page.goto() itself, pass navigate:false.
 * @param {number}   timeout
 * @param {object}   opts
 * @param {boolean}  opts.navigate - set false to skip the built-in goto so
 *                                   the callback can set up interception first
 */
async function scrapeWithCallback(url, callback, timeout = 50000, opts = {}) {
  const { navigate = true } = opts;
  const browser = await launchBrowser();
  const page = await createStealthPage(browser);

  try {
    if (navigate) {
      await page.goto(url, { waitUntil: 'domcontentloaded', timeout });
      await randomDelay(500, 1200);
    }
    const result = await callback(page, browser);
    return result;
  } finally {
    await browser.close().catch(() => {});
  }
}

module.exports = {
  scrapeUrl,
  scrapeWithCallback,
  launchBrowser,
  createStealthPage,
  randomDelay,
  randomUserAgent,
};
