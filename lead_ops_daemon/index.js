// tools/lead_ops/server/index.js
// Express Server + Baileys WhatsApp Daemon + Telegram Bot Controller (24/7 Render Cloud)

const express = require('express');
const path = require('path');
const fs = require('fs');
const https = require('https');
const { createClient } = require('@supabase/supabase-js');

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
const PORT = Number(process.env.PORT) || 10000;
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
    req.on('error', () => {});
    req.write(body);
    req.end();
  } catch (e) {}
}

// Global Exception Trap so container NEVER dies silently
process.on('uncaughtException', (err) => {
  console.error('[UNCAUGHT EXCEPTION]:', err);
  alertTelegram(`💥 <b>[RENDER CRASH EXCEPTION]</b>\n<pre>${(err.stack || err.message).slice(0, 1500)}</pre>`);
});

process.on('unhandledRejection', (reason) => {
  console.error('[UNHANDLED REJECTION]:', reason);
  alertTelegram(`⚠️ <b>[RENDER UNHANDLED REJECTION]</b>\n<pre>${String(reason).slice(0, 1500)}</pre>`);
});

if (!SUPABASE_URL || !SUPABASE_KEY || !BOT_TOKEN || !CHAT_ID) {
  console.error('[FATAL] Missing required credentials in environment variables.');
  alertTelegram('❌ <b>[RENDER FATAL]</b> Missing required credentials in environment variables.');
  process.exit(1);
}

const supabase = createClient(SUPABASE_URL, SUPABASE_KEY);
const app = express();
app.use(express.json());

// Express Health & Status endpoints for Cloudflare Keepalive Pinger
app.get('/health', (req, res) => {
  res.status(200).json({
    status: 'healthy',
    timestamp: new Date().toISOString(),
    uptime_seconds: Math.floor(process.uptime()),
    service: 'lead-ops-daemon',
    region: 'singapore'
  });
});

app.get('/status', (req, res) => {
  res.status(200).json({
    wa_connected: waClient ? waClient.isConnected : false,
    wa_user: waClient ? waClient.userJid : null,
    queue_size: waClient ? waClient.queue.length : 0,
    timestamp: new Date().toISOString()
  });
});

// CRITICAL FOR RENDER: Bind HTTP port immediately to 0.0.0.0
const server = app.listen(PORT, '0.0.0.0', () => {
  console.log(`[Express] HTTP Server listening immediately on 0.0.0.0:${PORT}`);
  alertTelegram(`🚀 <b>[RENDER BOOT]</b> Daemon listening on port ${PORT} (Node ${process.version}, Region: Singapore).`);
});

const setupBotController = require('./bot_controller');
const WAClient = require('./wa_client');

// Setup WA Client & Bot Controller
const authDir = path.join(__dirname, 'auth_info');
if (!fs.existsSync(authDir)) {
  fs.mkdirSync(authDir, { recursive: true });
}

const waClient = new WAClient({
  supabase,
  bot: null,
  chatId: CHAT_ID,
  authDir
});

const bot = setupBotController({
  botToken: BOT_TOKEN,
  chatId: CHAT_ID,
  supabase,
  waClient,
  rootDir: ROOT_DIR
});

waClient.bot = bot;

// Non-blocking async bootstrap
async function bootstrap() {
  console.log('=== BOOTSTRAPPING LEAD OPS DAEMON (RENDER SINGAPORE) ===');

  // 1. Launch Telegram Bot with dropPendingUpdates
  try {
    console.log('[Telegram] Launching bot polling with dropPendingUpdates...');
    await bot.launch({ dropPendingUpdates: true });
    console.log('[Telegram] Bot polling active for @hunterdev99_bot.');
  } catch (err) {
    console.error('[Telegram] Bot launch warning:', err.message);
    alertTelegram(`⚠️ <b>[BOT LAUNCH WARNING]</b> ${err.message}`);
  }

  // 2. Initialize WhatsApp Socket
  try {
    console.log('[WAClient] Starting WhatsApp socket...');
    await waClient.init();
  } catch (err) {
    console.error('[WAClient] Socket init error:', err.message);
    alertTelegram(`⚠️ <b>[WA SOCKET INIT ERROR]</b> ${err.message}`);
  }
}

bootstrap().catch((err) => {
  console.error('[BOOTSTRAP ERROR]:', err);
  alertTelegram(`❌ <b>[BOOTSTRAP ERROR]</b> ${err.message}`);
});

// Graceful shutdown
process.once('SIGINT', () => {
  try { bot.stop('SIGINT'); } catch (e) {}
  server.close(() => process.exit(0));
});
process.once('SIGTERM', () => {
  try { bot.stop('SIGTERM'); } catch (e) {}
  server.close(() => process.exit(0));
});
