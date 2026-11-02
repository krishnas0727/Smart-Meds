/**
 * ElderCare MedTrack & Doorstep Refill System - Client JavaScript
 * Provides Senior-friendly voice readout in English & Tamil,
 * Interactive Bilingual Language Switcher (EN / தமிழ்),
 * 1-Tap AJAX dose tracking, audio sound effects, and emergency SOS dispatch.
 */

// ==================== 1. WEB AUDIO API SOUND EFFECTS ==================== //
let audioCtx = null;
function getAudioContext() {
  if (!audioCtx) {
    audioCtx = new (window.AudioContext || window.webkitAudioContext)();
  }
  return audioCtx;
}

// Gentle pleasant chime when elderly takes medicine
function playSuccessChime() {
  try {
    const ctx = getAudioContext();
    if (ctx.state === 'suspended') ctx.resume();

    const osc1 = ctx.createOscillator();
    const osc2 = ctx.createOscillator();
    const gain = ctx.createGain();

    osc1.type = 'sine';
    osc1.frequency.setValueAtTime(523.25, ctx.currentTime); // C5
    osc1.frequency.exponentialRampToValueAtTime(659.25, ctx.currentTime + 0.15); // E5

    osc2.type = 'triangle';
    osc2.frequency.setValueAtTime(659.25, ctx.currentTime + 0.15); // E5
    osc2.frequency.exponentialRampToValueAtTime(783.99, ctx.currentTime + 0.35); // G5

    gain.gain.setValueAtTime(0.15, ctx.currentTime);
    gain.gain.exponentialRampToValueAtTime(0.001, ctx.currentTime + 0.6);

    osc1.connect(gain);
    osc2.connect(gain);
    gain.connect(ctx.destination);

    osc1.start();
    osc2.start(ctx.currentTime + 0.15);
    osc1.stop(ctx.currentTime + 0.35);
    osc2.stop(ctx.currentTime + 0.6);
  } catch (e) {
    console.log("Audio feedback error:", e);
  }
}

// High urgent chime for SOS
function playEmergencyTone() {
  try {
    const ctx = getAudioContext();
    if (ctx.state === 'suspended') ctx.resume();

    const osc = ctx.createOscillator();
    const gain = ctx.createGain();

    osc.type = 'sawtooth';
    osc.frequency.setValueAtTime(880, ctx.currentTime);
    osc.frequency.setValueAtTime(440, ctx.currentTime + 0.2);
    osc.frequency.setValueAtTime(880, ctx.currentTime + 0.4);

    gain.gain.setValueAtTime(0.3, ctx.currentTime);
    gain.gain.exponentialRampToValueAtTime(0.01, ctx.currentTime + 0.7);

    osc.connect(gain);
    gain.connect(ctx.destination);

    osc.start();
    osc.stop(ctx.currentTime + 0.7);
  } catch (e) {
    console.log("SOS audio error:", e);
  }
}

// ==================== 2. DOSE LOGGING (1-TAP AJAX) ==================== //
function recordDose(prescriptionId, medicineName) {
  const btn = document.getElementById(`btn-dose-${prescriptionId}`);
  const card = document.getElementById(`pill-card-${prescriptionId}`);
  const stockText = document.getElementById(`stock-text-${prescriptionId}`);
  const currentLang = getCurrentLanguage();

  if (btn) {
    btn.disabled = true;
    btn.innerHTML = `<i class="fa-solid fa-spinner fa-spin"></i> <span>${currentLang === 'ta' ? 'பதிவாகிறது...' : 'Recording...'}</span>`;
  }

  fetch(`/api/take_dose/${prescriptionId}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" }
  })
  .then(res => res.json())
  .then(data => {
    if (data.success) {
      playSuccessChime();

      if (btn) {
        btn.className = "px-3.5 py-1.5 rounded-xl font-bold text-xs flex items-center gap-1.5 bg-[#65B9A9] text-white cursor-default shadow-sm";
        btn.innerHTML = `<i class="fa-solid fa-circle-check"></i> <span>${currentLang === 'ta' ? 'எடுக்கப்பட்டது' : 'Taken'}</span>`;
      }

      const skipBtn = document.getElementById(`btn-skip-${prescriptionId}`);
      if (skipBtn) skipBtn.remove();

      if (card) {
        card.classList.remove('bg-[#FFF4F3]', 'border-[#EAECEF]');
        card.classList.add('bg-[#EBF6F4]', 'border-[#65B9A9]/40', 'opacity-85');
      }

      if (stockText && data.new_stock !== undefined) {
        stockText.innerText = data.new_stock;
      }

      const spokenMsg = currentLang === 'ta'
        ? `நல்லது! ${medicineName} மாத்திரை எடுத்துக்கொண்டதாக பதிவானது. மீதமுள்ள இருப்பு: ${data.new_stock} மாத்திரைகள்.`
        : `Dose recorded for ${medicineName}. You have ${data.new_stock} tablets remaining in your home box.`;
      speakText(spokenMsg, currentLang === 'ta' ? 'ta-IN' : 'en-US');

      if (data.is_critical) {
        setTimeout(() => {
          const alertMsg = currentLang === 'ta'
            ? `கவனிக்க: ${medicineName} மாத்திரை இருப்பு குறைவாக உள்ளது (${data.new_stock} மாத்திரைகள்). உடனடி ரீபில் செய்ய பரிந்துரைக்கிறோம்.`
            : `Low stock alert! Only ${data.new_stock} tablets left of ${medicineName}. An automated doorstep refill has been initiated.`;
          speakText(alertMsg, currentLang === 'ta' ? 'ta-IN' : 'en-US');
        }, 3500);
      }
    } else {
      alert("Could not record dose: " + (data.error || "Unknown error"));
      if (btn) {
        btn.disabled = false;
        btn.innerHTML = `<i class="fa-solid fa-check"></i> <span>${currentLang === 'ta' ? 'எடுத்துக்கொண்டேன்' : 'Mark Taken'}</span>`;
      }
    }
  })
  .catch(err => {
    console.error(err);
    alert("Connection error. Please check your network.");
    if (btn) btn.disabled = false;
  });
}

function skipDose(prescriptionId, medicineName) {
  const currentLang = getCurrentLanguage();
  const promptMsg = currentLang === 'ta'
    ? `மாத்திரை (${medicineName}) எடுக்காததற்கான காரணத்தை உள்ளிடவும் (விரும்பினால்):`
    : `Please enter the reason for skipping ${medicineName} (e.g. fasting, upset stomach, doctor advised pause):`;
  
  const defaultReason = currentLang === 'ta' ? 'வயிற்று உபாதை / மருத்துவ ஆலோசனை' : 'Senior feeling unwell / temporary physician hold';
  const reason = prompt(promptMsg, defaultReason);
  if (reason === null) return;

  const skipBtn = document.getElementById(`btn-skip-${prescriptionId}`);
  const takeBtn = document.getElementById(`btn-dose-${prescriptionId}`);
  const card = document.getElementById(`pill-card-${prescriptionId}`);

  fetch(`/api/skip_dose/${prescriptionId}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ reason: reason.trim() })
  })
  .then(res => res.json())
  .then(data => {
    if (data.success) {
      if (takeBtn) takeBtn.remove();
      if (skipBtn) {
        skipBtn.className = "px-3 py-1.5 rounded-xl font-bold text-xs bg-rose-100 text-rose-800 border border-rose-300";
        skipBtn.innerHTML = `<i class="fa-solid fa-ban mr-1"></i> ${currentLang === 'ta' ? 'தவிர்க்கப்பட்டது' : 'Skipped'}`;
        skipBtn.disabled = true;
      }
      if (card) {
        card.classList.remove('bg-[#FFF4F3]', 'border-[#EAECEF]');
        card.classList.add('bg-rose-50/70', 'border-rose-300');
      }
      const skippedAudio = currentLang === 'ta'
        ? `பதிவானது. ${medicineName} இன்று தவிர்க்கப்பட்டதாக குறிப்பிடப்பட்டுள்ளது. குடும்ப பராமரிப்பாளருக்கு அறிவிக்கப்பட்டுள்ளது.`
        : `Noted. ${medicineName} marked as skipped for today. Your caregiver has been alerted.`;
      speakText(skippedAudio, currentLang === 'ta' ? 'ta-IN' : 'en-US');
    } else {
      alert("Failed to record skipped dose: " + (data.error || "Unknown error"));
    }
  })
  .catch(err => {
    console.error(err);
    alert("Connection error recording dose.");
  });
}

// ==================== 3. TEXT-TO-SPEECH FOR ELDERLY ==================== //
function speakText(text, lang) {
  if (!('speechSynthesis' in window)) {
    console.log("Speech synthesis not supported in this browser.");
    return;
  }

  window.speechSynthesis.cancel(); // Stop any previous speech

  const currentLang = lang || (getCurrentLanguage() === 'ta' ? 'ta-IN' : 'en-US');
  const utterance = new SpeechSynthesisUtterance(text);
  utterance.rate = 0.9; // Clear, comfortable pace for seniors
  utterance.pitch = 1.0;
  utterance.lang = currentLang;

  // Find preferred voice matching language
  const voices = window.speechSynthesis.getVoices();
  let selectedVoice = null;
  if (currentLang.startsWith('ta')) {
    selectedVoice = voices.find(v => v.lang.startsWith('ta')) || voices.find(v => v.lang.includes('IN'));
  } else {
    selectedVoice = voices.find(v => v.lang.startsWith('en'));
  }
  if (selectedVoice) utterance.voice = selectedVoice;

  // Show voice toast notification
  const toast = document.getElementById("voice-toast");
  const toastMsg = document.getElementById("voice-toast-msg");
  if (toast && toastMsg) {
    toastMsg.innerText = text;
    toast.classList.remove("hidden");
    utterance.onend = () => {
      setTimeout(() => toast.classList.add("hidden"), 3000);
    };
  }

  window.speechSynthesis.speak(utterance);
}

function stopSpeech() {
  if ('speechSynthesis' in window) {
    window.speechSynthesis.cancel();
  }
  const toast = document.getElementById("voice-toast");
  if (toast) toast.classList.add("hidden");
}

function speakDailySummary() {
  fetch('/api/voice_summary')
    .then(r => r.json())
    .then(data => {
      speakText(data.text, data.lang);
    })
    .catch(err => {
      console.error("Voice summary failed", err);
    });
}

// ==================== 4. EMERGENCY SOS BROADCAST ==================== //
function triggerEmergencySOS() {
  playEmergencyTone();
  const btn = document.getElementById("btn-sos-trigger");
  const statusText = document.getElementById("sos-status-text");
  const currentLang = getCurrentLanguage();

  if (btn) {
    btn.classList.add("scale-90");
    setTimeout(() => btn.classList.remove("scale-90"), 300);
  }

  if (statusText) {
    statusText.innerText = currentLang === 'ta' ? "அவசர உதவி SOS அனுப்பப்படுகிறது..." : "Broadcasting Emergency SOS...";
    statusText.className = "text-yellow-300 font-black text-lg animate-pulse";
  }

  fetch('/api/sos_alert', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' }
  })
  .then(res => res.json())
  .then(data => {
    if (statusText) {
      statusText.innerHTML = currentLang === 'ta'
        ? `✅ அவசர உதவி உறுதிசெய்யப்பட்டது! பராமரிப்பாளர் மற்றும் மருத்துவருக்கு செய்தி அனுப்பப்பட்டது.`
        : `✅ Emergency SOS Confirmed! Caregiver and Doctor notified immediately.`;
      statusText.className = "text-emerald-300 font-black text-lg";
    }
    const audioConfirm = currentLang === 'ta'
      ? "அவசர உதவி செய்தி உங்கள் குடும்ப பராமரிப்பாளர் மற்றும் குடும்ப மருத்துவருக்கு உடனடியாக அனுப்பப்பட்டது!"
      : "Emergency SOS broadcast has been sent to your caregiver and family doctor!";
    speakText(audioConfirm, currentLang === 'ta' ? 'ta-IN' : 'en-US');
  })
  .catch(err => {
    console.error(err);
    if (statusText) {
      statusText.innerText = currentLang === 'ta' ? "⚠️ அவசர உதவி அனுப்புவதில் பிழை. உடனே 108 அழைக்கவும்!" : "⚠️ Dispatch error. Please call 108 directly!";
      statusText.className = "text-rose-300 font-black text-lg";
    }
  });
}

// ==================== 5. FONT SIZE SCALING ==================== //
function setFontSize(size) {
  const html = document.documentElement;
  html.classList.remove('font-size-normal', 'font-size-large', 'font-size-xlarge');

  // Highlight active button
  ['normal', 'large', 'xlarge'].forEach(s => {
    const btn = document.getElementById(`btn-font-${s}`);
    if (btn) {
      if (s === size) {
        btn.classList.add('bg-white', 'shadow-sm', 'text-[#E07067]');
      } else {
        btn.classList.remove('bg-white', 'shadow-sm', 'text-[#E07067]');
      }
    }
  });

  if (size === 'large') {
    html.classList.add('font-size-large');
  } else if (size === 'xlarge') {
    html.classList.add('font-size-xlarge');
  } else {
    html.classList.add('font-size-normal');
  }

  localStorage.setItem('eldercare_font_size', size);
}

// ==================== 6. MODAL HELPERS ==================== //
function openAddToScheduleModal(name, dosage, timingAdvice) {
  document.getElementById("modal-med-name").value = name;
  document.getElementById("modal-dosage").value = dosage || "500 mg";
  const adviceSelect = document.getElementById("modal-timing-advice");
  if (adviceSelect && timingAdvice) {
    adviceSelect.value = timingAdvice;
  }
  const modal = document.getElementById("add-schedule-modal");
  if (modal) modal.classList.remove("hidden");
}

function openCustomMedModal() {
  const modal = document.getElementById("add-custom-modal");
  if (modal) modal.classList.remove("hidden");
}

function openOrderMedModal(medId, medName, genericName, form, dosage, price, stock, requiresRx) {
  const medIdInput = document.getElementById("order-med-id");
  if (medIdInput) medIdInput.value = medId;

  const nameDisplay = document.getElementById("order-med-name-display");
  if (nameDisplay) nameDisplay.innerText = medName;

  const genericDisplay = document.getElementById("order-generic-display");
  if (genericDisplay) genericDisplay.innerText = (genericName || form || "Tablet") + " • " + (dosage || "Standard Dose");

  const basePriceInput = document.getElementById("order-base-price");
  if (basePriceInput) basePriceInput.value = price || 110.0;

  const unitPriceDisplay = document.getElementById("order-unit-price");
  if (unitPriceDisplay) unitPriceDisplay.innerText = "₹" + Math.round(price || 110) + " / 30 tablets";

  const stockBadge = document.getElementById("order-stock-badge");
  const submitBtn = document.getElementById("btn-submit-order");
  if (stockBadge) {
    if (stock > 0) {
      stockBadge.innerText = stock + " units in Apollo Pharmacy";
      stockBadge.className = "text-xs font-bold text-teal-700 bg-teal-50 border border-teal-200 px-2.5 py-0.5 rounded-full";
      if (submitBtn) {
        submitBtn.disabled = false;
        submitBtn.classList.remove("opacity-50", "cursor-not-allowed");
      }
    } else {
      stockBadge.innerText = "Out of Stock";
      stockBadge.className = "text-xs font-bold text-rose-700 bg-rose-50 border border-rose-200 px-2.5 py-0.5 rounded-full";
      if (submitBtn) {
        submitBtn.disabled = true;
        submitBtn.classList.add("opacity-50", "cursor-not-allowed");
      }
    }
  }

  const rxNotice = document.getElementById("order-rx-notice");
  if (rxNotice) {
    if (requiresRx) {
      rxNotice.classList.remove("hidden");
    } else {
      rxNotice.classList.add("hidden");
    }
  }

  // Generate unique client idempotency token
  const tokenInput = document.getElementById("order-idempotency-token");
  if (tokenInput) {
    tokenInput.value = "ORD-IDEM-" + Date.now() + "-" + Math.random().toString(36).substring(2, 8);
  }

  // Set quantity selector to 30 and recalculate
  const qtySelect = document.getElementById("order-quantity-select");
  if (qtySelect) qtySelect.value = "30";
  updateOrderModalPricing();

  const modal = document.getElementById("order-med-modal");
  if (modal) modal.classList.remove("hidden");
}

function updateOrderModalPricing() {
  const basePriceInput = document.getElementById("order-base-price");
  const basePrice = parseFloat(basePriceInput ? basePriceInput.value : 110.0) || 110.0;

  const qtySelect = document.getElementById("order-quantity-select");
  const qty = parseInt(qtySelect ? qtySelect.value : 30) || 30;

  // Price for 30 tablets is basePrice -> unit per tablet is basePrice / 30
  const total = Math.round((basePrice / 30.0) * qty);

  const totalDisplay = document.getElementById("order-total-display");
  if (totalDisplay) {
    totalDisplay.innerText = "₹" + total;
  }

  const summaryQty = document.getElementById("order-summary-qty");
  if (summaryQty) {
    const supplyMonths = (qty / 30).toFixed(1).replace(".0", "");
    summaryQty.innerText = qty + " tablets (" + supplyMonths + " Month Supply)";
  }
}

function closeModal(id) {
  const modal = document.getElementById(id);
  if (modal) modal.classList.add("hidden");
}

function toggleMobileMenu() {
  const menu = document.getElementById("mobile-menu");
  if (menu) menu.classList.toggle("hidden");
}

// ==================== 7. BILINGUAL LANGUAGE SWITCHER (EN / தமிழ்) ==================== //
const I18N = {
  en: {
    // Brand & Navigation
    "nav_brand_sub": "MedTrack Health",
    "nav_dashboard": "Dashboard",
    "nav_prescriptions": "Medicine Tracker",
    "nav_medicines": "A to Z Medicines",
    "nav_caregiver": "Caregiver Portal",
    "nav_pharmacy": "Pharmacy Portal",
    "nav_order_refill": "Order Medicines (Refill)",
    "nav_orders": "Track Orders & Delivery",
    "nav_printable_card": "Printable Card",
    "nav_viva": "AI Viva Assistant",
    "nav_sos": "Emergency (SOS)",
    "nav_signin": "Sign In",
    "nav_register": "Register New Account",
    "nav_signout": "Sign Out",

    // Header & Accessibility
    "header_voice_summary": "Voice Summary",
    "header_sos": "SOS",
    "header_tagline": "Safe Senior Medicine Management & Doorstep Refill",
    "header_caregiver_label": "Caregiver:",

    // Dashboard Hero & Quick Actions
    "hero_welcome": "Welcome back,",
    "hero_desc": "Here is your daily medication plan. Track your doses, monitor your remaining pills, and order doorstep refills anytime.",
    "hero_audio_title": "Audio Assistant",
    "hero_audio_btn": "Listen to Schedule",
    "hero_refill_title": "Doorstep Refill",
    "hero_refill_btn": "Order Medicines",

    // Metric Stats Cards
    "stat_active_prescriptions": "Active Prescriptions",
    "stat_taken_today": "Taken Today",
    "stat_low_stock": "Low Stock Alerts",
    "stat_caregiver": "Caregiver Contact",

    // Low Stock Alert Banner
    "low_stock_banner_badge": "Low Home Inventory Alert",
    "low_stock_banner_title": "Some of your daily tablets are running low!",
    "low_stock_banner_desc": "Order in 1 click and receive medicines safely at your doorstep before they finish:",
    "low_stock_order_btn": "Order Doorstep Refill (1-Click)",

    // Live Delivery Tracker
    "active_delivery_badge": "Active Doorstep Delivery",
    "active_delivery_btn": "View Details",
    "stage_confirmed": "1. Confirmed",
    "stage_packed": "2. Packed",
    "stage_on_the_way": "3. On the Way",
    "stage_delivered": "4. Delivered",

    // Today's Medication Schedule
    "sched_title": "Today's Medicine Schedule",
    "sched_desc": "Take your daily doses on time. Tap 'Mark Taken' to record your dose and decrement your home box inventory.",
    "sched_full_details": "Full Regimen Details",
    "slot_morning": "Morning (காலை)",
    "slot_afternoon": "Afternoon (மதியம்)",
    "slot_evening": "Evening (மாலை)",
    "slot_night": "Night (இரவு)",
    "btn_mark_taken": "Mark Taken",
    "btn_skip_dose": "Skip Dose",
    "btn_taken": "Taken",
    "label_stock_remaining": "Stock remaining:",
    "label_tabs": "tabs",

    // Adherence Disclaimer
    "adherence_disclaimer_title": "Medication Adherence Transparency & Disclaimer",
    "adherence_p1": "ElderCare MedTrack records medication adherence reported by the senior or visiting caregiver. The system calculates estimated home inventory based on logged confirmations.",
    "adherence_p2": "The system does not claim or guarantee that tablets were physically ingested or swallowed by the patient. Always verify with the senior or caregiver directly in case of missed doses."
  },

  ta: {
    // Brand & Navigation
    "nav_brand_sub": "மருத்துவ மேலாண்மை",
    "nav_dashboard": "முகப்பு (Dashboard)",
    "nav_prescriptions": "மாத்திரை பட்டியல் (Medicines)",
    "nav_medicines": "மருந்துகள் அடைவு (A-Z Catalog)",
    "nav_caregiver": "பராமரிப்பாளர் தளம் (Caregiver)",
    "nav_pharmacy": "மருந்தக தளம் (Staff)",
    "nav_order_refill": "மருந்துகள் ரீபில் ஆர்டர்",
    "nav_orders": "ஆர்டர் கண்காணிப்பு (Tracking)",
    "nav_printable_card": "அட்டவணை அச்சு (Print Card)",
    "nav_viva": "AI உதவியாளர் (Viva Assistant)",
    "nav_sos": "அவசர உதவி (SOS)",
    "nav_signin": "உள்நுழைக (Sign In)",
    "nav_register": "புதிய கணக்கு பதிவு",
    "nav_signout": "வெளியேறு (Sign Out)",

    // Header & Accessibility
    "header_voice_summary": "குரல் வழி வாசிப்பு",
    "header_sos": "அவசரம்",
    "header_tagline": "முதியோர்களுக்கான பாதுகாப்பான மாத்திரை மேலாண்மை & வீட்டு விநியோகம்",
    "header_caregiver_label": "பராமரிப்பாளர்:",

    // Dashboard Hero & Quick Actions
    "hero_welcome": "வணக்கம், மீண்டும் வருக,",
    "hero_desc": "இது உங்கள் இன்றைய மருந்து அட்டவணை. மாத்திரை நேரங்களை சரிபார்த்து, மீதமுள்ள இருப்பை கண்காணித்து, தேவைப்படும் போது வீட்டிற்கே ரீபில் ஆர்டர் செய்யுங்கள்.",
    "hero_audio_title": "குரல் உதவியாளர்",
    "hero_audio_btn": "அட்டவணையை கேட்க",
    "hero_refill_title": "வீட்டிற்கே ரீபில்",
    "hero_refill_btn": "மருந்துகள் ஆர்டர்",

    // Metric Stats Cards
    "stat_active_prescriptions": "செயலில் உள்ள மருந்துகள்",
    "stat_taken_today": "இன்று எடுத்தவை",
    "stat_low_stock": "குறைந்த இருப்பு எச்சரிக்கை",
    "stat_caregiver": "பராமரிப்பாளர் தொடர்பு",

    // Low Stock Alert Banner
    "low_stock_banner_badge": "வீட்டு இருப்பு எச்சரிக்கை",
    "low_stock_banner_title": "உங்கள் சில மாத்திரைகள் முடிவடையும் நிலையில் உள்ளது!",
    "low_stock_banner_desc": "மாத்திரை முழுமையாக தீரும் முன், 1-கிளிக்கில் வீட்டிற்கே வரவழைக்க ஆர்டர் செய்யுங்கள்:",
    "low_stock_order_btn": "வீட்டிற்கே ரீபில் ஆர்டர் (1-கிளிக்)",

    // Live Delivery Tracker
    "active_delivery_badge": "வீட்டு விநியோகம் செயலில் உள்ளது",
    "active_delivery_btn": "விவரங்களை பார்க்க",
    "stage_confirmed": "1. உறுதிசெய்யப்பட்டது",
    "stage_packed": "2. பேக் செய்யப்பட்டது",
    "stage_on_the_way": "3. வழியில் உள்ளது",
    "stage_delivered": "4. டெலிவரி செய்யப்பட்டது",

    // Today's Medication Schedule
    "sched_title": "இன்றைய மாத்திரை அட்டவணை",
    "sched_desc": "மருந்துகளை உரிய நேரத்தில் தவறாமல் எடுக்கவும். மாத்திரை எடுத்த பின் 'எடுத்துக்கொண்டேன்' என்பதை கிளிக் செய்யவும்.",
    "sched_full_details": "முழு விவரங்கள்",
    "slot_morning": "காலை நேரம் (Morning)",
    "slot_afternoon": "மதியம் நேரம் (Afternoon)",
    "slot_evening": "மாலை நேரம் (Evening)",
    "slot_night": "இரவு நேரம் (Night)",
    "btn_mark_taken": "எடுத்துக்கொண்டேன்",
    "btn_skip_dose": "தவிர்க்கவும்",
    "btn_taken": "எடுக்கப்பட்டது",
    "label_stock_remaining": "மீதமுள்ள இருப்பு:",
    "label_tabs": "மாத்திரைகள்",

    // Adherence Disclaimer
    "adherence_disclaimer_title": "மருந்து உட்கொள்ளல் உண்மைத்தன்மை & மருத்துவ வழிகாட்டுதல்",
    "adherence_p1": "எல்டர்கேர் மெட்டிராக் முதியவர் அல்லது குடும்ப பராமரிப்பாளர் பதிவு செய்யும் மருந்து உட்கொள்ளல் பதிவுகளை மட்டுமே சேமிக்கிறது. மீதமுள்ள இருப்பு பதிவுகளின் அடிப்படையில் கணக்கிடப்படுகிறது.",
    "adherence_p2": "முதியவர் மாத்திரையை முழுமையாக விழுங்கினாரா என்பதை இந்த செயலி தானாக அறியாது. சந்தேகம் இருப்பின் முதியவரை அல்லது பராமரிப்பாளரை நேரில் அணுகவும்."
  }
};

function getCurrentLanguage() {
  return localStorage.getItem('eldercare_lang') || 'en';
}

function toggleLanguage() {
  const current = getCurrentLanguage();
  const nextLang = current === 'ta' ? 'en' : 'ta';
  setLanguage(nextLang, true);
}

function setLanguage(lang, notify = true) {
  if (lang !== 'en' && lang !== 'ta') lang = 'en';
  localStorage.setItem('eldercare_lang', lang);
  document.documentElement.lang = lang;

  // 1. Update top bar toggle badges visual highlight
  const badgeEn = document.getElementById("lang-badge-en");
  const badgeTa = document.getElementById("lang-badge-ta");
  if (badgeEn && badgeTa) {
    if (lang === 'ta') {
      badgeTa.className = "px-1.5 py-0.5 rounded-lg text-xs font-black bg-[#F28B82] text-white transition";
      badgeEn.className = "px-1.5 py-0.5 rounded-lg text-xs font-bold text-[#777D87] hover:text-[#30343B] transition";
    } else {
      badgeEn.className = "px-1.5 py-0.5 rounded-lg text-xs font-black bg-[#F28B82] text-white transition";
      badgeTa.className = "px-1.5 py-0.5 rounded-lg text-xs font-bold text-[#777D87] hover:text-[#30343B] transition";
    }
  }

  // 2. Update mobile menu buttons if present
  const mEn = document.getElementById("mobile-lang-en");
  const mTa = document.getElementById("mobile-lang-ta");
  if (mEn && mTa) {
    if (lang === 'ta') {
      mTa.className = "px-2.5 py-1 rounded-lg text-xs font-black bg-[#F28B82] text-white transition";
      mEn.className = "px-2.5 py-1 rounded-lg text-xs font-bold text-[#777D87] transition";
    } else {
      mEn.className = "px-2.5 py-1 rounded-lg text-xs font-black bg-[#F28B82] text-white transition";
      mTa.className = "px-2.5 py-1 rounded-lg text-xs font-bold text-[#777D87] transition";
    }
  }

  // 3. Apply translations to all data-i18n tagged elements
  applyTranslations(lang);

  // 4. Sync language selection to backend session asynchronously
  fetch('/api/set_language/' + lang, { method: 'POST' }).catch(() => {});

  // 5. Show toast notification on user toggle
  if (notify) {
    showLanguageToast(lang);
  }
}

function applyTranslations(lang) {
  const dict = I18N[lang] || I18N.en;

  // Replace text for all explicitly tagged data-i18n elements
  document.querySelectorAll('[data-i18n]').forEach(el => {
    const key = el.getAttribute('data-i18n');
    if (dict[key]) {
      el.textContent = dict[key];
    }
  });

  // Dynamic replacements for buttons that may have been generated dynamically
  document.querySelectorAll('button[id^="btn-dose-"]').forEach(btn => {
    if (btn.querySelector('span')) {
      const span = btn.querySelector('span');
      if (span.textContent.includes('Taken') || span.textContent.includes('எடுக்கப்பட்டது')) {
        span.textContent = dict.btn_taken;
      } else {
        span.textContent = dict.btn_mark_taken;
      }
    }
  });

  document.querySelectorAll('button[id^="btn-skip-"]').forEach(btn => {
    btn.textContent = dict.btn_skip_dose;
  });
}

function showLanguageToast(lang) {
  let toast = document.getElementById("lang-toast");
  if (!toast) {
    toast = document.createElement("div");
    toast.id = "lang-toast";
    toast.className = "fixed bottom-6 left-6 max-w-sm bg-[#30343B] text-white px-5 py-3.5 rounded-2xl shadow-xl z-50 flex items-center gap-3 transition-all duration-300 text-xs sm:text-sm font-bold border border-white/20";
    document.body.appendChild(toast);
  }

  if (lang === 'ta') {
    toast.innerHTML = `<i class="fa-solid fa-language text-[#F28B82] text-lg"></i> <span>மொழி தமிழிற்கு மாற்றப்பட்டது (தமிழ்)</span>`;
  } else {
    toast.innerHTML = `<i class="fa-solid fa-language text-[#F28B82] text-lg"></i> <span>Language switched to English (EN)</span>`;
  }

  toast.style.display = "flex";
  toast.classList.remove("hidden", "opacity-0");
  toast.classList.add("opacity-100");

  setTimeout(() => {
    toast.classList.remove("opacity-100");
    toast.classList.add("opacity-0");
    setTimeout(() => { toast.style.display = "none"; }, 300);
  }, 2200);
}

// ==================== 8. DOM CONTENT LOADED INITIALIZATION ==================== //
document.addEventListener("DOMContentLoaded", () => {
  // Load saved accessibility font size preference
  const savedFontSize = localStorage.getItem('eldercare_font_size');
  if (savedFontSize) {
    setFontSize(savedFontSize);
  }

  // Load saved bilingual language preference
  const savedLang = getCurrentLanguage();
  setLanguage(savedLang, false);

  // Pre-fetch speech voices
  if ('speechSynthesis' in window) {
    window.speechSynthesis.onvoiceschanged = () => {
      window.speechSynthesis.getVoices();
    };
  }
});
