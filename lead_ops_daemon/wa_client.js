// tools/lead_ops/server/wa_client.js
// Baileys WhatsApp Client with 24/7 Keepalive, Code 515 Auto-Restart, Typing Simulation, and Inbound Auto-Pause

const pino = require('pino');
const path = require('path');
const fs = require('fs');
const QRCode = require('qrcode');
const { restoreSessionFromSupabase, backupSessionToSupabase } = require('./session_sync');

let makeWASocket, useMultiFileAuthState, DisconnectReason, delay;

async function loadBaileys() {
  if (!makeWASocket) {
    const b = await import('@whiskeysockets/baileys');
    makeWASocket = b.default || b.makeWASocket;
    useMultiFileAuthState = b.useMultiFileAuthState;
    DisconnectReason = b.DisconnectReason;
    delay = b.delay;
  }
}

class WAClient {
  constructor({ supabase, bot, chatId, authDir = './auth_info' }) {
    this.supabase = supabase;
    this.bot = bot;
    this.chatId = chatId;
    this.authDir = authDir;
    this.sock = null;
    this.isConnected = false;
    this.userJid = null;
    this.queue = [];
    this.isProcessingQueue = false;
    this.inboundReplyHandlers = new Map();
    this.lastQrSentAt = 0;
  }

  async init() {
    await loadBaileys();
    console.log('[WAClient] Initializing Baileys Socket...');
    
    // 1. Try restoring existing session from Supabase
    await restoreSessionFromSupabase(this.supabase, 'default', this.authDir);

    const { state, saveCreds } = await useMultiFileAuthState(this.authDir);
    this.state = state;
    this.saveCreds = saveCreds;

    this.sock = makeWASocket({
      auth: state,
      printQRInTerminal: false,
      logger: pino({ level: 'silent' }),
      browser: ['Ubuntu', 'Chrome', '124.0.0.0'],
      syncFullHistory: false,
      connectTimeoutMs: 60000,
      defaultQueryTimeoutMs: 60000
    });

    this.sock.ev.on('creds.update', async () => {
      await saveCreds();
      await backupSessionToSupabase(this.supabase, 'default', this.authDir);
    });

    this.sock.ev.on('connection.update', async (update) => {
      const { connection, lastDisconnect, qr } = update;

      // Rate-limit QR sending to at most once every 90 seconds to prevent Telegram spam
      if (qr && !this.isConnected) {
        const now = Date.now();
        if (now - this.lastQrSentAt > 90000) {
          this.lastQrSentAt = now;
          console.log('[WAClient] New QR code generated, sending once to Telegram.');
          try {
            const qrBuffer = await QRCode.toBuffer(qr);
            if (this.bot) {
              await this.bot.telegram.sendPhoto(this.chatId, { source: qrBuffer }, {
                caption: '📷 <b>Scan QR Code WhatsApp</b>\n\nBuka WhatsApp > Perangkat Tertaut > Tautkan Perangkat. Atau kirim perintah <code>/pair 08xxxxxxx</code> untuk pairing code 8 digit tanpa kamera.',
                parse_mode: 'HTML'
              });
            }
          } catch (e) {
            console.error('[WAClient] Failed to send QR code to Telegram:', e.message);
          }
        }
      }

      if (connection === 'close') {
        const statusCode = lastDisconnect?.error?.output?.statusCode;
        console.log(`[WAClient] Connection closed with statusCode: ${statusCode}`);
        this.isConnected = false;

        // Code 515 (Restart Required): Standard Baileys post-pairing stream restart!
        if (statusCode === DisconnectReason.restartRequired || statusCode === 515) {
          console.log('[WAClient] Code 515 detected (Stream restart required). Reconnecting immediately with existing state...');
          setTimeout(() => this.init(), 1500);
          return;
        }

        // Code 401 (Logged Out): Clean up stale keys so user can re-pair cleanly
        if (statusCode === DisconnectReason.loggedOut || statusCode === 401) {
          console.log('[WAClient] Logged out from WhatsApp. Resetting session.');
          await this.cleanAuthDir();
          if (this.bot) {
            await this.bot.telegram.sendMessage(this.chatId, '⚠️ <b>WhatsApp Terputus / Logged Out</b>\nSilakan jalankan <code>/pair &lt;nomor_hp&gt;</code> untuk menautkan ulang.', { parse_mode: 'HTML' });
          }
          return;
        }

        // Other network disconnects: Reconnect
        setTimeout(() => this.init(), 5000);

      } else if (connection === 'open') {
        console.log('[WAClient] WhatsApp connected successfully!');
        this.isConnected = true;
        this.userJid = this.sock.user?.id;
        const myNum = this.userJid ? this.userJid.split(':')[0] : 'Unknown';
        const myName = this.sock.user?.name || 'Lead Hunter';

        await backupSessionToSupabase(this.supabase, 'default', this.authDir, myNum, myName);

        if (this.bot) {
          await this.bot.telegram.sendMessage(
            this.chatId,
            `🟢 <b>WhatsApp Berhasil Terhubung 24/7!</b>\n\n👤 <b>Nama:</b> ${myName}\n📱 <b>Nomor:</b> <code>+${myNum}</code>\n☁️ <b>Host:</b> Render Singapore\n🛡️ <b>Mode:</b> Anti-Ban Paced Active`,
            { parse_mode: 'HTML' }
          );
        }
      }
    });

    // Inbound Messages Listener
    this.sock.ev.on('messages.upsert', async ({ messages, type }) => {
      if (type !== 'notify') return;

      for (const msg of messages) {
        if (!msg.message || msg.key.fromMe) continue;

        const senderJid = msg.key.remoteJid;
        if (!senderJid || senderJid.endsWith('@g.us')) continue;

        const rawPhone = senderJid.split('@')[0];
        const e164 = rawPhone.startsWith('62') ? `+${rawPhone}` : `+${rawPhone}`;

        const text = msg.message.conversation ||
                     msg.message.extendedTextMessage?.text ||
                     msg.message.imageMessage?.caption ||
                     '[Media / Dokumen WhatsApp]';

        console.log(`[WAClient] Inbound reply received from ${e164}: "${text}"`);
        await this.handleInboundReply(e164, text, senderJid);
      }
    });
  }

  async cleanAuthDir() {
    try {
      if (fs.existsSync(this.authDir)) {
        fs.rmSync(this.authDir, { recursive: true, force: true });
        fs.mkdirSync(this.authDir, { recursive: true });
      }
      await this.supabase.from('pipeline_sessions').delete().eq('id', 'default');
      console.log('[WAClient] Auth directory and Supabase session cleared.');
    } catch (e) {
      console.error('[WAClient] Error clearing auth dir:', e.message);
    }
  }

  async resetSession() {
    console.log('[WAClient] Manual session reset requested...');
    try {
      if (this.sock) {
        try { this.sock.end(); } catch (e) {}
      }
      await this.cleanAuthDir();
      this.isConnected = false;
      this.userJid = null;
      this.lastQrSentAt = 0;
      await this.init();
      return true;
    } catch (e) {
      console.error('[WAClient] Error during resetSession:', e.message);
      return false;
    }
  }

  async requestPairingCode(phoneNumber) {
    if (!this.sock) throw new Error('WhatsApp Socket belum siap. Coba ketik /reset_wa terlebih dahulu.');
    const cleanNum = phoneNumber.replace(/[^0-9]/g, '');
    
    // In Baileys, requestPairingCode must be called when socket is open and waiting
    const code = await this.sock.requestPairingCode(cleanNum);
    console.log(`[WAClient] Pairing code generated: ${code}`);
    return code;
  }

  async handleInboundReply(phoneE164, text, jid) {
    try {
      const { data: leads } = await this.supabase
        .from('pipeline_leads')
        .select('*')
        .eq('contact_phone', phoneE164)
        .limit(1);

      const lead = leads && leads.length > 0 ? leads[0] : null;

      if (lead) {
        await this.supabase
          .from('pipeline_leads')
          .update({
            status: 'replied',
            contact_wa_status: 'active_replied',
            updated_at: new Date().toISOString()
          })
          .eq('id', lead.id);
      }

      await this.supabase.from('pipeline_messages').insert({
        lead_id: lead ? lead.id : null,
        recipient_phone: phoneE164,
        direction: 'inbound',
        message_body: text,
        sent_at: new Date().toISOString()
      });

      const leadTitle = lead ? lead.title : 'Kontak Properti';
      const cleanWaNum = phoneE164.replace('+', '');
      const waDirectUrl = `https://wa.me/${cleanWaNum}`;

      const alertMsg = (
        `🔔 <b>[BALASAN WHATSAPP MASUK]</b>\n\n` +
        `🏢 <b>Properti:</b> ${leadTitle}\n` +
        `👤 <b>Pengirim:</b> ${lead?.contact_name || 'Broker / Owner'} (<code>${phoneE164}</code>)\n` +
        `💬 <b>Isi Pesan:</b>\n<i>"${text}"</i>\n\n` +
        `⚠️ <i>Bot otomatis DIBEKUKAN pada nomor ini agar percakapan 100% alami.</i>\n` +
        `💡 <i>Anda dapat langsung mereply notifikasi Telegram ini untuk membalas, atau klik tombol di bawah:</i>`
      );

      if (this.bot) {
        const sentMsg = await this.bot.telegram.sendMessage(this.chatId, alertMsg, {
          parse_mode: 'HTML',
          reply_markup: {
            inline_keyboard: [
              [{ text: '📱 Buka Chat di WhatsApp (wa.me)', url: waDirectUrl }]
            ]
          }
        });

        this.inboundReplyHandlers.set(sentMsg.message_id, {
          phoneE164,
          jid,
          leadId: lead?.id
        });
      }
    } catch (err) {
      console.error('[WAClient] Error handling inbound reply:', err.message);
    }
  }

  async sendDirectReply(targetJid, replyText) {
    if (!this.isConnected || !this.sock) {
      throw new Error('WhatsApp tidak terhubung.');
    }
    await this.sock.sendPresenceUpdate('composing', targetJid);
    await delay(3000 + Math.random() * 2000);
    await this.sock.sendPresenceUpdate('paused', targetJid);

    const sent = await this.sock.sendMessage(targetJid, { text: replyText });
    return sent;
  }

  async queueInquiry(leadId) {
    const { data: lead, error } = await this.supabase
      .from('pipeline_leads')
      .select('*')
      .eq('id', leadId)
      .single();

    if (error || !lead) {
      throw new Error(`Lead ID ${leadId} tidak ditemukan.`);
    }

    this.queue.push(lead);
    console.log(`[WAClient] Lead queued for dispatch: ${lead.title} (${lead.contact_phone}). Queue size: ${this.queue.length}`);

    if (!this.isProcessingQueue) {
      this.processQueue();
    }
    return this.queue.length;
  }

  async processQueue() {
    if (this.queue.length === 0) {
      this.isProcessingQueue = false;
      return;
    }

    this.isProcessingQueue = true;
    const lead = this.queue.shift();

    try {
      if (!this.isConnected) {
        throw new Error('WhatsApp belum terhubung. Harap login / scan QR terlebih dahulu.');
      }

      const rawNum = lead.contact_phone.replace(/[^0-9]/g, '');
      const jid = `${rawNum}@s.whatsapp.net`;

      const [onWa] = await this.sock.onWhatsApp(rawNum);
      if (!onWa || !onWa.exists) {
        console.log(`[WAClient] ${rawNum} is NOT on WhatsApp. Marking invalid.`);
        await this.supabase
          .from('pipeline_leads')
          .update({ contact_wa_status: 'inactive', status: 'error' })
          .eq('id', lead.id);
        
        if (this.bot) {
          await this.bot.telegram.sendMessage(
            this.chatId,
            `❌ <b>Nomor Tidak Terdaftar di WhatsApp:</b> ${lead.contact_phone}\n🏢 ${lead.title}`,
            { parse_mode: 'HTML' }
          );
        }
        this.scheduleNextQueueItem();
        return;
      }

      const delayMs = Math.floor(45000 + Math.random() * 75000);
      const delaySec = Math.round(delayMs / 1000);
      console.log(`[WAClient] Anti-ban pacing delay: waiting ${delaySec}s before sending to ${lead.contact_name}...`);

      if (this.bot) {
        await this.bot.telegram.sendMessage(
          this.chatId,
          `⏳ <b>Memulai Pacing Anti-Ban:</b> Menunggu ${delaySec} detik sebelum mengirim inquiry ke <b>${lead.contact_name}</b> (+${rawNum})...`,
          { parse_mode: 'HTML' }
        );
      }

      await delay(delayMs);

      await this.sock.sendPresenceUpdate('composing', jid);
      await delay(5000 + Math.random() * 3000);
      await this.sock.sendPresenceUpdate('paused', jid);

      const lt = lead.attributes?.luas_tanah || 'unit';
      const loc = lead.district || lead.city || 'area tersebut';
      const templates = [
        `Halo Pak ${lead.contact_name}, apakah unit gudang/pabrik ${lt}m² di ${loc} ini masih available? Klien manufaktur kami sedang mencari unit siap pakai di area ini.`,
        `Selamat pagi Pak ${lead.contact_name}, izin menanyakan untuk listing di ${loc} (LT ${lt}m²), apakah unitnya masih tersedia? Ada rekanan kami yang membutuhkan unit di lokasi ini.`,
        `Halo Pak ${lead.contact_name}, mau tanya apakah properti ${lead.category} di ${loc} ini masih open? Klien industri kami tertarik melihat spek dan ketersediaannya.`
      ];
      const messageText = templates[Math.floor(Math.random() * templates.length)];

      await this.sock.sendMessage(jid, { text: messageText });
      console.log(`[WAClient] Message sent successfully to ${rawNum}`);

      await this.supabase
        .from('pipeline_leads')
        .update({ status: 'contacted', updated_at: new Date().toISOString() })
        .eq('id', lead.id);

      await this.supabase.from('pipeline_messages').insert({
        lead_id: lead.id,
        recipient_phone: lead.contact_phone,
        direction: 'outbound',
        message_body: messageText,
        sent_at: new Date().toISOString()
      });

      if (this.bot) {
        await this.bot.telegram.sendMessage(
          this.chatId,
          `✅ <b>Inquiry WhatsApp Berhasil Terkirim!</b>\n\n👤 <b>Tujuan:</b> ${lead.contact_name} (<code>${lead.contact_phone}</code>)\n🏢 <b>Properti:</b> ${lead.title}\n💬 <b>Pesan:</b>\n<i>"${messageText}"</i>`,
          {
            parse_mode: 'HTML',
            reply_markup: {
              inline_keyboard: [
                [{ text: '📱 Pantau di WhatsApp', url: `https://wa.me/${rawNum}` }]
              ]
            }
          }
        );
      }
    } catch (err) {
      console.error(`[WAClient] Error dispatching lead ${lead.id}:`, err.message);
      if (this.bot) {
        await this.bot.telegram.sendMessage(
          this.chatId,
          `⚠️ <b>Gagal Mengirim WA:</b> ${err.message}\nLead: ${lead.title}`,
          { parse_mode: 'HTML' }
        );
      }
    }

    this.scheduleNextQueueItem();
  }

  scheduleNextQueueItem() {
    setTimeout(() => {
      this.processQueue();
    }, 5000);
  }
}

module.exports = WAClient;
