// tools/lead_ops/server/index.js
// Express Server + Baileys WhatsApp Daemon + Telegram Bot Controller (24/7 Render Cloud)

const express = require('express');
const path = require('path');
const fs = require('fs');
const https = require('https');

// Ensure WebSocket is globally available for Supabase Realtime & Baileys
try {
  if (!globalThis.WebSocket) {
    globalThis.WebSocket = require('ws');
  }
} catch (e) {}

const app = express();
app.use(express.json());

const PORT = Number(process.env.PORT) || 10000;
const debugLogs = [];

function logDebug(msg) {
  const line = `[${new Date().toISOString()}] ${msg}`;
  console.log(line);
  debugLogs.push(line);
  if (debugLogs.length > 50) debugLogs.shift();
}

// 1. IMMMEDIATE PORT BINDING: Never wait for async libraries before binding to Render's port
const server = app.listen(PORT, '0.0.0.0', () => {
  logDebug(`Express HTTP Server listening immediately on 0.0.0.0:${PORT}`);
});

// 2. Health check endpoint for Render load balancer & Cloudflare keepalive
app.get('/health', (req, res) => {
  res.status(200).json({
    status: 'healthy',
    timestamp: new Date().toISOString(),
    uptime_seconds: Math.floor(process.uptime()),
    service: 'lead-ops-daemon',
    region: 'singapore'
  });
});

app.get('/debug', (req, res) => {
  res.status(200).json({
    env_keys_present: Object.keys(process.env).filter(k => !k.includes('KEY') && !k.includes('TOKEN') && !k.includes('SECRET')),
    has_supabase_url: !!process.env.NEXT_PUBLIC_SUPABASE_URL,
    has_supabase_key: !!process.env.SUPABASE_SERVICE_ROLE_KEY,
    has_bot_token: !!process.env.LEAD_HUNTER_BOT_TOKEN,
    has_chat_id: !!process.env.LEAD_HUNTER_CHAT_ID,
    node_version: process.version,
    port: PORT,
    logs: debugLogs
  });
});

// Load environment variables (.env.local fallback for local test)
const envLocalPath = path.resolve(__dirname, '../../../.env.local');
if (fs.existsSync(envLocalPath)) {
  const envContent = fs.readFileSync(envLocalPath, 'utf8');
  for (const line of envContent.split('\n')) {
    if (line.includes('=') && !line.trim().startsWith('#')) {
      const [k, ...v] = line.split('=');
      const key = k.trim();
      const val = v.join('=').trim().replace(/^["']|["']$/g, '');
      if (!process.env[key]) process.env[key] = val;
    }
  }
}

const SUPABASE_URL = process.env.NEXT_PUBLIC_SUPABASE_URL;
const SUPABASE_KEY = process.env.SUPABASE_SERVICE_ROLE_KEY;
const BOT_TOKEN = process.env.LEAD_HUNTER_BOT_TOKEN;
const CHAT_ID = process.env.LEAD_HUNTER_CHAT_ID;
const ROOT_DIR = path.resolve(__dirname, '..');

// Helper to push emergency Telegram telemetry
function alertTelegram(text) {
  try {
    if (!BOT_TOKEN || !CHAT_ID) return;
    const body = JSON.stringify({ chat_id: CHAT_ID, text, parse_mode: 'HTML' });
    const req = https.request(`https://api.telegram.org/bot${BOT_TOKEN}/sendMessage`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'Content-Length': Buffer.byteLength(body) }
    });
    req.on('error', (e) => logDebug(`Telegram send error: ${e.message}`));
    req.write(body);
    req.end();
  } catch (e) {
    logDebug(`alertTelegram exception: ${e.message}`);
  }
}

// Global Exception Trap so container NEVER dies
process.on('uncaughtException', (err) => {
  logDebug(`UNCAUGHT EXCEPTION: ${err.message}`);
  alertTelegram(`💥 <b>[RENDER CRASH EXCEPTION]</b>\n<pre>${(err.stack || err.message).slice(0, 1500)}</pre>`);
});

process.on('unhandledRejection', (reason) => {
  logDebug(`UNHANDLED REJECTION: ${reason}`);
  alertTelegram(`⚠️ <b>[RENDER UNHANDLED REJECTION]</b>\n<pre>${String(reason).slice(0, 1500)}</pre>`);
});

let waClient = null;
let bot = null;

app.get('/status', (req, res) => {
  res.status(200).json({
    wa_connected: waClient ? waClient.isConnected : false,
    wa_user: waClient ? waClient.userJid : null,
    queue_size: waClient ? waClient.queue.length : 0,
    timestamp: new Date().toISOString()
  });
});

async function bootstrap() {
  logDebug('Starting daemon background bootstrap...');
  
  if (!SUPABASE_URL || !SUPABASE_KEY || !BOT_TOKEN || !CHAT_ID) {
    const err = 'Missing required environment variables. Continuing in degraded mode.';
    logDebug(err);
    alertTelegram(`⚠️ <b>[RENDER WARNING]</b> ${err}`);
    return;
  }

  const { createClient } = require('@supabase/supabase-js');
  const setupBotController = require('./bot_controller');
  const WAClient = require('./wa_client');

  const supabase = createClient(SUPABASE_URL, SUPABASE_KEY);

  const authDir = path.join(__dirname, 'auth_info');
  if (!fs.existsSync(authDir)) {
    fs.mkdirSync(authDir, { recursive: true });
  }

  waClient = new WAClient({
    supabase,
    bot: null,
    chatId: CHAT_ID,
    authDir
  });

  bot = setupBotController({
    botToken: BOT_TOKEN,
    chatId: CHAT_ID,
    supabase,
    waClient,
    rootDir: ROOT_DIR
  });

  waClient.bot = bot;

  // 1. Launch Telegram Bot
  try {
    logDebug('Launching bot polling...');
    await bot.launch({ dropPendingUpdates: true });
    logDebug('Bot polling active for @hunterdev99_bot.');
    alertTelegram(`🚀 <b>[RENDER LIVE]</b> Lead Ops Daemon aktif 24/7 di Render Singapore!\nNode: ${process.version} | Port: ${PORT}`);
  } catch (err) {
    logDebug(`Bot launch warning: ${err.message}`);
    alertTelegram(`⚠️ <b>[BOT LAUNCH WARNING]</b> ${err.message}`);
  }

  // 2. Initialize WhatsApp Socket
  try {
    logDebug('Initializing WhatsApp socket...');
    await waClient.init();
    logDebug('WhatsApp socket initialized.');
  } catch (err) {
    logDebug(`WA socket init error: ${err.message}`);
    alertTelegram(`⚠️ <b>[WA SOCKET INIT ERROR]</b> ${err.message}`);
  }
}

// Run bootstrap in background without blocking port listening
bootstrap().catch((err) => {
  logDebug(`Bootstrap fatal error: ${err.message}`);
});

// Graceful shutdown
process.once('SIGINT', () => {
  try { if (bot) bot.stop('SIGINT'); } catch (e) {}
  server.close(() => process.exit(0));
});
process.once('SIGTERM', () => {
  try { if (bot) bot.stop('SIGTERM'); } catch (e) {}
  server.close(() => process.exit(0));
});
