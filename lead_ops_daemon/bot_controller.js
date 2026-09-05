// tools/lead_ops/server/bot_controller.js
// Telegraf-based Bot Controller for @hunterdev99_bot with Mode Switching & Hybrid Reply Takeover

const { Telegraf, Markup } = require('telegraf');
const { spawn } = require('child_process');
const path = require('path');

function setupBotController({ botToken, chatId, supabase, waClient, rootDir }) {
  const bot = new Telegraf(botToken);

  // Helper: Build Main Interactive Menu Keyboard
  async function getMainKeyboard() {
    let currentMode = 'manual';
    try {
      const { data } = await supabase
        .from('pipeline_sessions')
        .select('system_mode')
        .eq('id', 'default')
        .single();
      if (data?.system_mode) currentMode = data.system_mode;
    } catch (e) {}

    const statusIcon = waClient.isConnected ? '🟢 WA Aktif' : '🔴 WA Offline';

    return Markup.inlineKeyboard([
      [
        Markup.button.callback(
          currentMode === 'auto_scheduled' ? '✅ 🟢 Otomatis Terjadwal' : '🟢 Otomatis Terjadwal',
          'mode_auto_scheduled'
        ),
        Markup.button.callback(
          currentMode === 'manual' ? '✅ 🟡 Mode Manual' : '🟡 Mode Manual',
          'mode_manual'
        )
      ],
      [
        Markup.button.callback('🚀 Scrape Full Wilayah', 'mode_full_scrape'),
        Markup.button.callback(
          currentMode === 'paused' ? '✅ ⏸️ Sistem Dijeda' : '⏸️ Pause System',
          'mode_paused'
        )
      ],
      [
        Markup.button.callback(`📊 Status Lead (${statusIcon})`, 'btn_status'),
        Markup.button.callback('📱 Cek Sesi WA', 'btn_wa_session')
      ]
    ]);
  }

  // Command: /start & /menu
  bot.command(['start', 'menu'], async (ctx) => {
    if (String(ctx.chat.id) !== String(chatId)) {
      return ctx.reply('⛔ Akses ditolak. Bot ini hanya untuk operator resmi.');
    }

    const welcomeText = (
      `🏢 <b>Groundwork Lead Hunter & Ops Center</b>\n\n` +
      `Sistem otonom akuisisi lead properti industri, verifikasi WhatsApp, dan outreach 24/7.\n\n` +
      `📱 <b>Status WhatsApp:</b> ${waClient.isConnected ? '🟢 Terhubung (Online)' : '🔴 Belum Terhubung'}\n` +
      `☁️ <b>Host:</b> Render Singapore (24/7 Persistent)\n\n` +
      `Pilih mode operasional di bawah atau ketik <code>/pair 08xxxxxxx</code> untuk menautkan nomor WhatsApp.`
    );

    const keyboard = await getMainKeyboard();
    return ctx.replyWithHTML(welcomeText, keyboard);
  });

  // Action: Mode Toggles
  bot.action(/^mode_(.+)$/, async (ctx) => {
    const targetMode = ctx.match[1];

    if (targetMode === 'full_scrape') {
      await ctx.answerCbQuery('Memulai scraping menyeluruh...');
      await ctx.reply('🚀 <b>Memulai Scrape Full Wilayah (Jabodetabek + Cirebon + Karawang)...</b>', { parse_mode: 'HTML' });
      
      // Trigger Python cirebon_hunter as test runner
      const pyScript = path.join(rootDir, 'tools/lead_ops/mining/cirebon_hunter.py');
      const venvPy = path.join(rootDir, '.venv/bin/python');
      const pyProc = spawn(venvPy, [pyScript], { cwd: rootDir });

      pyProc.stdout.on('data', (d) => console.log(`[FullScrape] ${d}`));
      pyProc.stderr.on('data', (d) => console.error(`[FullScrape ERR] ${d}`));

      pyProc.on('close', async (code) => {
        await bot.telegram.sendMessage(
          chatId,
          `🏁 <b>Scrape Full Wilayah Selesai!</b> (Exit code: ${code})`,
          { parse_mode: 'HTML' }
        );
      });
      return;
    }

    await supabase
      .from('pipeline_sessions')
      .upsert({ id: 'default', system_mode: targetMode, updated_at: new Date().toISOString() }, { onConflict: 'id' });

    await ctx.answerCbQuery(`Mode diubah: ${targetMode}`);
    const keyboard = await getMainKeyboard();
    await ctx.editMessageReplyMarkup(keyboard.reply_markup);
    return ctx.reply(`⚙️ <b>Mode Operasi Berubah:</b> <code>${targetMode.toUpperCase()}</code>`, { parse_mode: 'HTML' });
  });

  // Action: Status
  bot.action('btn_status', async (ctx) => {
    await ctx.answerCbQuery();
    const { data: leads } = await supabase.from('pipeline_leads').select('status');
    const counts = { queued: 0, contacted: 0, replied: 0, skipped: 0, total: 0 };

    if (leads) {
      counts.total = leads.length;
      for (const l of leads) {
        if (counts[l.status] !== undefined) counts[l.status]++;
      }
    }

    const text = (
      `📊 <b>Ringkasan Pipeline Leads:</b>\n\n` +
      `📥 <b>Total Lead Tersimpan:</b> ${counts.total}\n` +
      `⏳ <b>Dalam Antrean (Queued):</b> ${counts.queued}\n` +
      `📤 <b>Terkirim (Contacted):</b> ${counts.contacted}\n` +
      `💬 <b>Balasan Masuk (Replied):</b> ${counts.replied}\n` +
      `⏭️ <b>Dilewati (Skipped):</b> ${counts.skipped}\n\n` +
      `📱 <b>Antrean WA Client Aktif:</b> ${waClient.queue.length} item`
    );
    return ctx.replyWithHTML(text);
  });

  // Action: Cek Sesi WA
  bot.action('btn_wa_session', async (ctx) => {
    await ctx.answerCbQuery();
    if (waClient.isConnected) {
      const myNum = waClient.sock?.user?.id?.split(':')[0] || 'Unknown';
      return ctx.replyWithHTML(
        `🟢 <b>WhatsApp Aktif & Terhubung!</b>\n\n📱 <b>Nomor:</b> +${myNum}\n👤 <b>Nama:</b> ${waClient.sock?.user?.name || '-'}`
      );
    } else {
      return ctx.replyWithHTML(
        `🔴 <b>WhatsApp Belum Terhubung</b>\n\nKetik <code>/pair &lt;nomor_hp&gt;</code> (contoh: <code>/pair 08123456789</code>) untuk mendapatkan kode pairing 8-digit.`
      );
    }
  });

  // Command: /pair <nomor_hp>
  bot.command('pair', async (ctx) => {
    if (String(ctx.chat.id) !== String(chatId)) return;
    const parts = ctx.message.text.trim().split(/\s+/);
    if (parts.length < 2) {
      return ctx.replyWithHTML('💡 Format salah. Gunakan: <code>/pair 08123456789</code>');
    }

    let phoneInput = parts[1].replace(/[^0-9]/g, '');
    if (phoneInput.startsWith('0')) {
      phoneInput = '62' + phoneInput.slice(1);
    }

    await ctx.reply(`🔄 <b>Meminta Pairing Code 8-Digit ke WhatsApp untuk +${phoneInput}...</b>`, { parse_mode: 'HTML' });

    try {
      const code = await waClient.requestPairingCode(phoneInput);
      const formattedCode = code?.match(/.{1,4}/g)?.join('-') || code;

      const codeMsg = (
        `🔑 <b>KODE PAIRING WHATSAPP ANDA:</b>\n\n` +
        `<code>${formattedCode}</code>\n\n` +
        `📲 <b>Langkah Aktivasi di HP:</b>\n` +
        `1. Buka <b>WhatsApp</b> di ponsel Anda.\n` +
        `2. Masuk ke <b>Setelan > Perangkat Tertaut > Tautkan Perangkat</b>.\n` +
        `3. Pilih <b>"Tautkan dengan nomor telepon saja"</b> (Link with phone number).\n` +
        `4. Masukkan kode 8-digit di atas: <code>${formattedCode}</code>\n\n` +
        `<i>Kode ini berlaku selama 1-2 menit.</i>`
      );
      return ctx.replyWithHTML(codeMsg);
    } catch (err) {
      return ctx.replyWithHTML(`❌ <b>Gagal membuat pairing code:</b> ${err.message}`);
    }
  });

  // Action: send_wa_<lead_id>
  bot.action(/^send_wa_(.+)$/, async (ctx) => {
    const leadId = ctx.match[1];
    await ctx.answerCbQuery('Memasukkan ke antrean kirim WA...');

    try {
      const qLen = await waClient.queueInquiry(leadId);
      await ctx.replyWithHTML(
        `⏳ <b>Lead Dimasukkan ke Antrean Pacing Anti-Ban!</b>\nPosisi antrean: #${qLen}\nPesan akan dikirim otomatis setelah jeda aman (45-120s).`
      );
    } catch (e) {
      await ctx.replyWithHTML(`⚠️ Gagal menambahkan ke antrean: ${e.message}`);
    }
  });

  // Action: skip_<lead_id>
  bot.action(/^skip_(.+)$/, async (ctx) => {
    const leadId = ctx.match[1];
    await ctx.answerCbQuery('Lead ditunda/skip.');

    await supabase
      .from('pipeline_leads')
      .update({ status: 'skipped', updated_at: new Date().toISOString() })
      .eq('id', leadId);

    await ctx.replyWithHTML(`⏸️ <b>Lead dilewati dari antrean aktif.</b>`);
  });

  // Hybrid Reply Takeover: When operator replies directly to a Telegram notification message
  bot.on('message', async (ctx) => {
    if (String(ctx.chat.id) !== String(chatId)) return;
    const replyTo = ctx.message.reply_to_message;
    if (!replyTo) return;

    const sessionInfo = waClient.inboundReplyHandlers.get(replyTo.message_id);
    if (!sessionInfo) return;

    const textToSend = ctx.message.text;
    if (!textToSend) {
      return ctx.reply('Kirimkan balasan berupa teks.');
    }

    await ctx.reply(`📤 <b>Mengirim balasan ke ${sessionInfo.phoneE164} via WhatsApp...</b>`, { parse_mode: 'HTML' });

    try {
      await waClient.sendDirectReply(sessionInfo.jid, textToSend);

      // Log to messages
      await supabase.from('pipeline_messages').insert({
        lead_id: sessionInfo.leadId || null,
        recipient_phone: sessionInfo.phoneE164,
        direction: 'outbound',
        message_body: textToSend,
        sent_at: new Date().toISOString()
      });

      await ctx.replyWithHTML(`✅ <b>Balasan Terkirim ke WhatsApp ${sessionInfo.phoneE164}!</b>`);
    } catch (err) {
      await ctx.replyWithHTML(`❌ <b>Gagal mengirim balasan ke WhatsApp:</b> ${err.message}`);
    }
  });

  return bot;
}

module.exports = setupBotController;
