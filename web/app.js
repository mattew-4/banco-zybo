"use strict";
/* Banco Zybo Z7-10 -- interfaccia. Tutto lo stato della scheda arriva dall'emulatore
   (eventi SSE): qui non si simula nulla, si mostra soltanto. */

const $ = (s) => document.querySelector(s);
const $$ = (s) => Array.from(document.querySelectorAll(s));
const hex = (n) => "0x" + (n >>> 0).toString(16).toUpperCase();

async function api(path, body, raw) {
  const opt = body === undefined ? {} : {
    method: "POST",
    headers: raw ? { "Content-Type": "application/octet-stream" } : { "Content-Type": "application/json" },
    body: raw ? body : JSON.stringify(body),
  };
  const r = await fetch(path, opt);
  return r.json();
}

/* =====================================================================
   Editor: file multipli, salvati nel browser
   ===================================================================== */
const STORE = "zybo-files-v1";
const DEFAULT_MAIN = `#include <stdint.h>
#include "xil_io.h"
#include "xil_printf.h"
#include "sleep.h"

#define BTN_BASE  0x41200000u   /* axi_gpio_0: BTN0..BTN3 */
#define LED_BASE  0x41210000u   /* axi_gpio_1: LD0..LD3   */
#define SW_BASE   0x41220000u   /* axi_gpio_2: SW0..SW3   */
#define GPIO_DATA 0x000
#define GPIO_TRI  0x004

int main(void)
{
    xil_printf("Ciao dalla Zybo Z7-10!\\r\\n");
    Xil_Out32(LED_BASE + GPIO_TRI, 0x0);          /* LED come uscite */

    uint32_t prev = 0xFFFFFFFF;
    while (1) {
        uint32_t sw = Xil_In32(SW_BASE + GPIO_DATA) & 0xF;
        uint32_t bt = Xil_In32(BTN_BASE + GPIO_DATA) & 0xF;
        Xil_Out32(LED_BASE + GPIO_DATA, sw ^ bt); /* LED = switch XOR pulsanti */
        if ((sw | bt << 4) != prev) {
            xil_printf("SW = 0x%x  BTN = 0x%x\\r\\n", (int)sw, (int)bt);
            prev = sw | bt << 4;
        }
        usleep(10000);
    }
}
`;

let files = {};      // nome -> testo
let active = null;
let dirty = {};

function loadFiles() {
  try {
    const s = JSON.parse(localStorage.getItem(STORE) || "{}");
    files = s.files || {};
    active = s.active;
  } catch { files = {}; }
  if (!Object.keys(files).length) { files = { "main.c": DEFAULT_MAIN }; active = "main.c"; }
  if (!files[active]) active = Object.keys(files)[0];
}
function saveFiles() {
  localStorage.setItem(STORE, JSON.stringify({ files, active }));
}
const editor = $("#editor"), gutter = $("#gutter");
let marks = {};      // riga -> "err" | "wrn" per il file attivo

function renderTabs() {
  const box = $("#fileTabs");
  box.innerHTML = "";
  for (const name of Object.keys(files)) {
    const b = document.createElement("button");
    b.className = "tab" + (name === active ? " active" : "") + (dirty[name] ? " dirty" : "");
    b.innerHTML = `<span></span><span class="x" title="Chiudi">✕</span>`;
    b.firstChild.textContent = name;
    b.addEventListener("click", (e) => {
      if (e.target.classList.contains("x")) { closeFile(name); return; }
      selectFile(name);
    });
    box.appendChild(b);
  }
}
function selectFile(name) {
  if (active && files[active] !== undefined) files[active] = editor.value;
  active = name;
  editor.value = files[name] ?? "";
  marks = fileMarks[name] || {};
  renderTabs(); renderGutter(); saveFiles();
}
function closeFile(name) {
  if (Object.keys(files).length === 1) { alert("Serve almeno un file."); return; }
  if (!confirm(`Chiudere ${name}? (resta salvato solo se l'hai scaricato)`)) return;
  delete files[name];
  if (active === name) active = Object.keys(files)[0];
  selectFile(active);
}
function addFile(name, text) {
  name = name.replace(/[^A-Za-z0-9_.\-]/g, "_");
  if (files[active] !== undefined) files[active] = editor.value;
  files[name] = text;
  selectFile(name);
}
function renderGutter() {
  const n = editor.value.split("\n").length;
  let out = "";
  for (let i = 1; i <= n; i++) {
    const m = marks[i];
    out += (m ? `<span class="${m}">${i}</span>` : i) + "\n";
  }
  gutter.innerHTML = out;
  gutter.scrollTop = editor.scrollTop;
}
editor.addEventListener("input", () => {
  files[active] = editor.value; dirty[active] = true; renderGutter(); saveFilesDebounced();
});
editor.addEventListener("scroll", () => { gutter.scrollTop = editor.scrollTop; });
editor.addEventListener("keydown", (e) => {
  if (e.key === "Tab") {
    e.preventDefault();
    const s = editor.selectionStart, en = editor.selectionEnd;
    editor.setRangeText("    ", s, en, "end");
    editor.dispatchEvent(new Event("input"));
  } else if (e.key === "Enter" && !e.ctrlKey && !e.metaKey) {
    // mantiene l'indentazione della riga corrente
    const s = editor.selectionStart;
    const line = editor.value.slice(editor.value.lastIndexOf("\n", s - 1) + 1, s);
    const ind = (line.match(/^\s*/) || [""])[0] + (/\{\s*$/.test(line) ? "    " : "");
    e.preventDefault();
    editor.setRangeText("\n" + ind, s, editor.selectionEnd, "end");
    editor.dispatchEvent(new Event("input"));
  }
});
let saveTimer = null;
function saveFilesDebounced() { clearTimeout(saveTimer); saveTimer = setTimeout(saveFiles, 400); }

function jumpTo(file, line) {
  if (files[file] === undefined) return;
  selectFile(file);
  const lines = editor.value.split("\n");
  let pos = 0;
  for (let i = 0; i < line - 1 && i < lines.length; i++) pos += lines[i].length + 1;
  editor.focus();
  editor.setSelectionRange(pos, pos + (lines[line - 1] || "").length);
  const lh = parseFloat(getComputedStyle(editor).lineHeight) || 19;
  editor.scrollTop = Math.max(0, (line - 6) * lh);
}

$("#newFileBtn").addEventListener("click", () => {
  const n = prompt("Nome del nuovo file (es. util.c o util.h):", "nuovo.c");
  if (n) addFile(n, n.endsWith(".h") ? "#pragma once\n" : "#include \"xil_printf.h\"\n");
});
$("#openFile").addEventListener("change", async (e) => {
  for (const f of e.target.files) addFile(f.name, await f.text());
  e.target.value = "";
});
$("#saveFileBtn").addEventListener("click", () => {
  files[active] = editor.value;
  const a = document.createElement("a");
  a.href = URL.createObjectURL(new Blob([editor.value], { type: "text/plain" }));
  a.download = active; a.click();
  dirty[active] = false; renderTabs();
});
$("#labsBtn").addEventListener("click", async () => {
  await loadLabs();
  $("#labsDlg").showModal();
});
$("#labsDirBtn").addEventListener("click", async () => {
  const cur = $("#labsBase").textContent;
  const p = prompt("Percorso completo della cartella con i tuoi Lab\n" +
                   "(Windows: es. C:\\Users\\nome\\Desktop\\Labs   ·   Mac: es. /Users/nome/Desktop/Labs)\n" +
                   "Suggerimento Mac: trascina la cartella nella finestra del Terminale per vederne il percorso.", cur);
  if (p === null || !p.trim()) return;
  const r = await api("/api/config", { labs_dir: p.trim() });
  if (r.error) { alert(r.error); return; }
  await loadLabs();
});

async function loadLabs() {
  const d = await api("/api/labs");
  $("#labsBase").textContent = d.base;
  const list = $("#labsList");
  list.innerHTML = d.files.length ? "" : "<p class='muted'>Nessun file .c trovato in questa cartella.</p>";
  for (const f of d.files) {
    const b = document.createElement("button");
    b.type = "button";
    b.textContent = f.name;
    b.addEventListener("click", async () => {
      const r = await api("/api/labfile?path=" + encodeURIComponent(f.path));
      if (r.text === undefined) return;
      $("#labsDlg").close();
      const mode = await askOpenMode(f.name);
      if (mode === "cancel") return;
      if (mode === "replace") { files = {}; active = null; dirty = {}; }
      addFile(r.name, r.text);
    });
    list.appendChild(b);
  }
}

function askOpenMode(name) {
  return new Promise((resolve) => {
    const dlg = $("#openModeDlg");
    $("#openModeName").textContent = name;
    const done = (m) => { dlg.close(); resolve(m); };
    $("#openReplace").onclick = () => done("replace");
    $("#openAdd").onclick = () => done("add");
    $("#openCancel").onclick = () => done("cancel");
    dlg.oncancel = () => resolve("cancel");      // tasto Esc
    dlg.showModal();
  });
}

/* =====================================================================
   Compilazione ed esecuzione
   ===================================================================== */
let lastBuild = null;
let fileMarks = {};

function options() {
  return {
    opt: $("#optLevel").value, warnings: $("#optWall").checked, werror: $("#optWerror").checked,
    debug: $("#optG").checked, libm: $("#optLm").checked,
  };
}
function esc(s) { return s.replace(/[&<>]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" }[c])); }

function showBuild(r) {
  fileMarks = {};
  let html = "";
  const lines = (r.log || "").split("\n");
  for (const ln of lines) {
    const m = ln.match(/^([\w.\-]+):(\d+):(\d+):\s+(error|warning|note|errore|avviso)/);
    let cls = "";
    if (/\berror\b|errore|undefined reference/.test(ln)) cls = "err";
    else if (/\bwarning\b|avviso/.test(ln)) cls = "wrn";
    if (m) {
      const kind = /error|errore/.test(m[4]) ? "err" : (/warning|avviso/.test(m[4]) ? "wrn" : null);
      if (kind) {
        fileMarks[m[1]] = fileMarks[m[1]] || {};
        if (fileMarks[m[1]][+m[2]] !== "err") fileMarks[m[1]][+m[2]] = kind;
      }
      html += `<span class="${cls}"><a class="jump" data-f="${esc(m[1])}" data-l="${m[2]}">${esc(m[1])}:${m[2]}:${m[3]}</a>${esc(ln.slice(m[0].length - m[4].length - 1))}</span>\n`;
    } else {
      html += (cls ? `<span class="${cls}">${esc(ln)}</span>` : esc(ln)) + "\n";
    }
  }
  if (r.ok) {
    html += `<span class="ok">✔ Compilazione riuscita in ${r.seconds}s</span>\n<span class="dim">${esc(r.size || "")}</span>\n`;
  } else {
    html += `<span class="err">✘ Compilazione fallita</span>\n`;
  }
  if (r.commands && r.commands.length) {
    html += `\n<span class="dim">Comandi eseguiti:\n${esc(r.commands.join("\n"))}</span>`;
  }
  $("#buildOut").innerHTML = html;
  marks = fileMarks[active] || {};
  renderGutter();
  showPane("paneBuild");
}
$("#buildOut").addEventListener("click", (e) => {
  const a = e.target.closest("a.jump");
  if (a) jumpTo(a.dataset.f, +a.dataset.l);
});

async function doBuild(run) {
  files[active] = editor.value; saveFiles();
  $("#buildOut").innerHTML = `<span class="dim">Compilazione in corso…</span>`;
  showPane("paneBuild");
  $("#buildBtn").disabled = $("#buildRunBtn").disabled = true;
  try {
    const r = await api(run ? "/api/buildrun" : "/api/build", { files, options: options() });
    showBuild(r);
    if (r.ok) {
      lastBuild = r.id;
      $("#dlElf").href = "/api/elf?id=" + r.id;
      $("#dlElf").classList.remove("hidden");
      if (run) {
        termSys(`── Program FPGA + Run: ${active} (${options().opt}) ──`);
        resetBoardView();
      }
      loadAsm();
    }
  } catch (e) {
    $("#buildOut").innerHTML = `<span class="err">Errore di comunicazione con il server: ${esc(String(e))}</span>`;
  } finally {
    $("#buildBtn").disabled = $("#buildRunBtn").disabled = false;
  }
}
$("#buildBtn").addEventListener("click", () => doBuild(false));
$("#buildRunBtn").addEventListener("click", () => doBuild(true));
/* scorciatoie: Ctrl su Windows/Linux, Cmd (⌘) o Ctrl su Mac */
const IS_MAC = /Mac/i.test(navigator.platform || navigator.userAgent);
if (IS_MAC) {
  $("#buildBtn").title = "Compila (⌘B)";
  $("#buildRunBtn").title = "Compila, programma la scheda ed esegui (⌘Invio)";
}
document.addEventListener("keydown", (e) => {
  const mod = e.ctrlKey || e.metaKey;
  if (mod && e.key === "Enter") { e.preventDefault(); doBuild(true); }
  else if (mod && (e.key === "b" || e.key === "B")) { e.preventDefault(); doBuild(false); }
  else if (mod && (e.key === "s" || e.key === "S")) { e.preventDefault(); saveFiles(); dirty[active] = false; renderTabs(); }
});

$("#elfFile").addEventListener("change", async (e) => {
  const f = e.target.files[0];
  e.target.value = "";
  if (!f) return;
  const r = await api("/api/upload-elf", await f.arrayBuffer(), true);
  if (!r.ok) { $("#buildOut").innerHTML = `<span class="err">${esc(r.error)}</span>`; showPane("paneBuild"); return; }
  lastBuild = r.id;
  $("#buildOut").innerHTML = `<span class="ok">✔ ${esc(f.name)} caricato, entry point ${r.entry}</span>\n` +
    (r.warning ? `<span class="wrn">${esc(r.warning)}</span>\n` : "");
  termSys(`── Run: ${f.name} ──`);
  resetBoardView();
  loadAsm();
});

async function loadAsm() {
  if (!lastBuild) return;
  const fn = $("#asmFn").value.trim() || "main";
  const r = await api(`/api/disasm?id=${lastBuild}&fn=${encodeURIComponent(fn)}`);
  const text = (r.text || "").split("\n").filter((l) => !/^\s*$|file format|^Disassembly of section/.test(l)).join("\n");
  $("#asmOut").textContent = text || `Funzione «${fn}» non trovata nell'ELF (con -O2 alcune funzioni vengono incorporate nel chiamante).`;
}
$("#asmBtn").addEventListener("click", loadAsm);

function showPane(id) {
  $$(".otab").forEach((t) => t.classList.toggle("active", t.dataset.pane === id));
  $$(".pane").forEach((p) => p.classList.toggle("hidden", p.id !== id));
  if (id === "paneLog") { logUnread = 0; $("#logCount").classList.add("hidden"); }
}
$$(".otab").forEach((t) => t.addEventListener("click", () => showPane(t.dataset.pane)));

/* =====================================================================
   Scheda: LED, switch, pulsanti
   ===================================================================== */
const leds = [], sws = [], btns = [], swLvl = [], btnLvl = [];
let swState = 0, btnState = 0;
function bounce() { return { bounce: +$("#bounceN").value, bounceUs: +$("#bounceMs").value * 1000 }; }
$("#bounceN").addEventListener("input", () => { $("#bounceNv").textContent = $("#bounceN").value; });
$("#bounceMs").addEventListener("input", () => { $("#bounceMsv").textContent = $("#bounceMs").value; });

for (let i = 3; i >= 0; i--) {
  const c = document.createElement("div");
  c.className = "cell";
  c.innerHTML = `<div class="led green"></div><div class="lbl">LD${i}</div>`;
  leds[i] = c.querySelector(".led");
  $("#ledRow").appendChild(c);

  const s = document.createElement("div");
  s.className = "cell";
  s.innerHTML = `<div class="sw" tabindex="0" role="switch" aria-checked="false" aria-label="SW${i}"><div class="knob"></div></div><div class="lbl">SW${i}</div><div class="lvl">pin 0</div>`;
  sws[i] = s.querySelector(".sw"); swLvl[i] = s.querySelector(".lvl");
  const toggle = () => {
    const on = !((swState >> i) & 1);
    swState = on ? swState | (1 << i) : swState & ~(1 << i);
    sws[i].classList.toggle("on", on); sws[i].setAttribute("aria-checked", on);
    $("#swHex").textContent = hex(swState);
    api("/api/input", { kind: "sw", idx: i, value: on, ...bounce() });
  };
  sws[i].addEventListener("click", toggle);
  sws[i].addEventListener("keydown", (e) => { if (e.key === " " || e.key === "Enter") { e.preventDefault(); toggle(); } });
  $("#swRow").appendChild(s);

  const b = document.createElement("div");
  b.className = "cell";
  b.innerHTML = `<button class="pbtn" aria-label="BTN${i}"></button><div class="lbl">BTN${i}</div><div class="lvl">pin 0</div>`;
  btns[i] = b.querySelector(".pbtn"); btnLvl[i] = b.querySelector(".lvl");
  const press = (v) => {
    if (((btnState >> i) & 1) === (v ? 1 : 0)) return;
    btnState = v ? btnState | (1 << i) : btnState & ~(1 << i);
    btns[i].classList.toggle("down", v);
    $("#btnHex").textContent = hex(btnState);
    api("/api/input", { kind: "btn", idx: i, value: v, ...bounce() });
  };
  btns[i].addEventListener("pointerdown", (e) => { e.preventDefault(); btns[i].setPointerCapture?.(e.pointerId); press(true); });
  btns[i].addEventListener("pointerup", () => press(false));
  btns[i].addEventListener("pointercancel", () => press(false));
  btns[i].addEventListener("keydown", (e) => { if ((e.key === " " || e.key === "Enter") && !e.repeat) { e.preventDefault(); press(true); } });
  btns[i].addEventListener("keyup", (e) => { if (e.key === " " || e.key === "Enter") { e.preventDefault(); press(false); } });
  $("#btnRow").appendChild(b);
}
$$("[data-psbtn]").forEach((b) => {
  const mio = +b.dataset.psbtn;
  const set = (v) => { b.classList.toggle("down", v); api("/api/input", { kind: "psbtn", idx: mio, value: v }); };
  b.addEventListener("pointerdown", (e) => { e.preventDefault(); b.setPointerCapture?.(e.pointerId); set(true); });
  b.addEventListener("pointerup", () => set(false));
});

function setLeds(v) {
  for (let i = 0; i < 4; i++) leds[i].classList.toggle("on", !!((v >> i) & 1));
  $("#ledHex").textContent = hex(v & 0xF);
}
function setInputLevels(dev, v) {
  const arr = dev === "sws" ? swLvl : btnLvl;
  for (let i = 0; i < 4; i++) {
    const hi = !!((v >> i) & 1);
    arr[i].textContent = "pin " + (hi ? 1 : 0);
    arr[i].classList.toggle("hi", hi);
  }
}
function resetBoardView() {
  setLeds(0);
  $("#ld4").classList.remove("on");
  irqCount = { btns: 0, sws: 0 }; lastStats = {};
  $$(".periph td.n").forEach((td) => (td.textContent = "0"));
  $("#warnBox").classList.add("hidden");
  warned = {};
  i2cLines = []; i2cTotal = 0; $("#i2cLog").textContent = ""; $("#i2cCount").textContent = "0";
  // gli switch fisici restano dove sono: li ripresento al nuovo programma
  for (let i = 0; i < 4; i++) {
    if ((swState >> i) & 1) api("/api/input", { kind: "sw", idx: i, value: true, bounce: 0, bounceUs: 0 });
  }
}

/* =====================================================================
   Controlli di esecuzione
   ===================================================================== */
$("#pauseBtn").addEventListener("click", () => api("/api/control", { action: state === "paused" ? "resume" : "pause" }));
$("#resetBtn").addEventListener("click", () => { termSys("── Reset (PS-SRST) ──"); resetBoardView(); api("/api/control", { action: "reset" }); });
$("#stopBtn").addEventListener("click", () => api("/api/control", { action: "stop" }));

let state = "off";
const STATE_TXT = { off: "spento", booting: "avvio emulatore…", idle: "pronto", running: "in esecuzione", paused: "in pausa", starting: "avvio…" };
function setState(s) {
  state = s;
  const b = $("#stateBadge");
  b.className = "badge " + s;
  b.textContent = STATE_TXT[s] || s;
  $("#pauseBtn").disabled = !(s === "running" || s === "paused");
  $("#pauseBtn").textContent = s === "paused" ? "▶ Riprendi" : "⏸ Pausa";
  $("#resetBtn").disabled = !(s === "running" || s === "paused");
  $("#stopBtn").disabled = !(s === "running" || s === "paused");
  if (s !== "running") $("#cpuMips").textContent = "–";
}

/* =====================================================================
   Sensore
   ===================================================================== */
let sensorTimer = null, pendingSensor = {};
function sendSensor(obj, now) {
  Object.assign(pendingSensor, obj);
  clearTimeout(sensorTimer);
  sensorTimer = setTimeout(() => { api("/api/sensor", pendingSensor); pendingSensor = {}; }, now ? 0 : 40);
}
function updateTiltView() {
  const p = +$("#pitch").value, r = +$("#roll").value;
  $("#pitchv").textContent = p.toFixed(1) + "°";
  $("#rollv").textContent = r.toFixed(1) + "°";
  $("#tiltBoard").style.transform = `rotateX(${55 + r * 0.9}deg) rotateY(${-p}deg)`;
}
["pitch", "roll"].forEach((id) => $("#" + id).addEventListener("input", () => {
  updateTiltView(); sendSensor({ pitch: +$("#pitch").value, roll: +$("#roll").value });
}));
$("#temp").addEventListener("input", () => { $("#tempv").textContent = (+$("#temp").value).toFixed(1) + " °C"; sendSensor({ temp: +$("#temp").value }); });
$("#noise").addEventListener("input", () => { $("#noisev").textContent = "×" + (+$("#noise").value).toFixed(1); sendSensor({ noise: +$("#noise").value }); });
$("#whoami").addEventListener("change", () => sendSensor({ whoami: +$("#whoami").value }, true));
$("#mount").addEventListener("change", () => sendSensor({ mount: $("#mount").value }, true));
$("#levelBtn").addEventListener("click", () => { $("#pitch").value = 0; $("#roll").value = 0; updateTiltView(); sendSensor({ pitch: 0, roll: 0 }, true); });
$("#tapBtn").addEventListener("click", () => sendSensor({ tap: true }, true));
let fingerTimer = null;
$("#fingerBtn").addEventListener("click", () => {
  // il chip si scalda verso ~34 °C mentre il dito e' appoggiato, poi si raffredda
  clearInterval(fingerTimer);
  const base = +$("#temp").value;
  let t = 0;
  fingerTimer = setInterval(() => {
    t += 0.2;
    const v = t < 6 ? base + (34 - base) * (1 - Math.exp(-t / 2)) : base + (34 - base) * (1 - Math.exp(-6 / 2)) * Math.exp(-(t - 6) / 4);
    $("#tempv").textContent = v.toFixed(1) + " °C";
    sendSensor({ temp: v }, true);
    if (t > 20) { clearInterval(fingerTimer); $("#tempv").textContent = base.toFixed(1) + " °C"; sendSensor({ temp: base }, true); }
  }, 200);
});
updateTiltView();

let i2cLines = [], i2cTotal = 0, gyTimer = null;
const MPU_REG = { 0x3B: "ACCEL_XOUT_H", 0x41: "TEMP_OUT_H", 0x43: "GYRO_XOUT_H", 0x6B: "PWR_MGMT_1", 0x75: "WHO_AM_I", 0x1A: "CONFIG", 0x1B: "GYRO_CONFIG", 0x1C: "ACCEL_CONFIG", 0x19: "SMPLRT_DIV" };
function onI2c(e) {
  i2cTotal++;
  const reg = MPU_REG[e.reg] ? ` (${MPU_REG[e.reg]})` : "";
  const t = e.op === "W"
    ? (e.n ? `W  reg 0x${e.reg.toString(16).padStart(2, "0")}${reg} ← 0x${(e.val ?? 0).toString(16).padStart(2, "0")}${e.n > 1 ? ` (+${e.n - 1} byte)` : ""}` : `W  puntatore → 0x${e.reg.toString(16).padStart(2, "0")}${reg}`)
    : `R  ${e.n} byte da 0x${e.reg.toString(16).padStart(2, "0")}${reg}`;
  i2cLines.push(t);
  if (i2cLines.length > 200) i2cLines.splice(0, i2cLines.length - 200);
  $("#i2cCount").textContent = i2cTotal;
  $("#gy521").classList.add("active");
  clearTimeout(gyTimer); gyTimer = setTimeout(() => $("#gy521").classList.remove("active"), 120);
}
setInterval(() => {
  if ($(".i2c-log").open) { const el = $("#i2cLog"); el.textContent = i2cLines.join("\n"); el.scrollTop = el.scrollHeight; }
}, 250);

/* =====================================================================
   Periferiche: statistiche e avvisi
   ===================================================================== */
let lastStats = {}, irqCount = { btns: 0, sws: 0 }, warned = {};
function onStats(e) {
  const row = document.querySelector(`tr[data-dev="${e.dev}"]`);
  if (!row) return;
  const prev = lastStats[e.dev];
  lastStats[e.dev] = e;
  if (prev && e.vt > prev.vt) {
    const dt = e.vt - prev.vt;
    row.querySelector(".r").textContent = fmtRate((e.reads - prev.reads) / dt);
    row.querySelector(".w").textContent = fmtRate((e.writes - prev.writes) / dt);
  }
  if (e.dev !== "leds") row.querySelector(".e").textContent = e.edges;
  $("#boardTime").textContent = (+e.vt).toFixed(3);
}
function fmtRate(x) {
  if (!isFinite(x) || x < 0) return "–";
  if (x >= 1e6) return (x / 1e6).toFixed(2) + " M";
  if (x >= 1e3) return (x / 1e3).toFixed(1) + " k";
  return x.toFixed(0);
}
function onIrq(e) {
  const lamp = $("#irq-" + e.dev);
  if (!lamp) return;
  lamp.classList.toggle("on", !!e.v);
  if (e.v) {
    irqCount[e.dev] = (irqCount[e.dev] || 0) + 1;
    document.querySelector(`tr[data-dev="${e.dev}"] td.n`).textContent = irqCount[e.dev];
  }
}
function warn(html, key) {
  if (warned[key]) return;
  warned[key] = true;
  const box = $("#warnBox");
  box.classList.remove("hidden");
  box.innerHTML += (box.innerHTML ? "<br>" : "") + html;
}
function onUnmapped(e) {
  const a = parseInt(e.addr, 16);
  let where = "";
  if (a >= 0x40000000 && a < 0xC0000000) where = "nella PL (M_AXI_GP), dove il bitstream del corso non ha nessun blocco: sulla scheda reale l'interconnect risponde con un errore di bus (DECERR) e il processore va in Data Abort";
  else where = "a cui non risponde nessuna periferica";
  warn(`⚠ ${e.op === "Read" ? "Lettura" : "Scrittura"} a <b>${e.addr}</b> (istruzione a ${e.pc}): indirizzo ${where}. L'emulatore restituisce 0 e prosegue.`, e.addr);
}

/* =====================================================================
   Serial Monitor
   ===================================================================== */
const term = $("#term");
const dec = new TextDecoder("utf-8");
let termAtLineStart = true, termChars = 0;
function termWrite(text, cls) {
  const atBottom = term.scrollHeight - term.scrollTop - term.clientHeight < 30;
  text = text.replace(/\r\n/g, "\n").replace(/\r/g, "");
  const ts = $("#termTs").checked;
  let out = "";
  for (const ch of text) {
    if (termAtLineStart && ts && !cls) out += `<span class="ts">[${(+$("#boardTime").textContent).toFixed(3)}] </span>`;
    out += esc(ch);
    termAtLineStart = ch === "\n";
  }
  if (cls) out = `<span class="${cls}">${out}</span>`;
  term.insertAdjacentHTML("beforeend", out);
  termChars += text.length;
  if (termChars > 200000) { term.innerHTML = term.innerHTML.slice(-120000); termChars = 120000; }
  if (atBottom) term.scrollTop = term.scrollHeight;
}
function termSys(t) { termWrite((termAtLineStart ? "" : "\n") + t + "\n", "sys"); termAtLineStart = true; }
$("#termClear").addEventListener("click", () => { term.innerHTML = ""; termChars = 0; termAtLineStart = true; });
$("#termForm").addEventListener("submit", (e) => {
  e.preventDefault();
  const eol = $("#termEol").value.replace("\\r", "\r").replace("\\n", "\n").replace("\\n", "\n");
  api("/api/uart", { text: $("#termInput").value + eol });
  $("#termInput").value = "";
});

/* =====================================================================
   Log dell'emulatore
   ===================================================================== */
let logUnread = 0;
// avvisi interni di Renode su dettagli non modellati e irrilevanti (cache L2, SCU, bit di interrupt dell'I2C)
const IGNORED = /pl310: Unhandled|scu: Unhandled|Unknown CP15|slcr: Unhandled write to offset 0xA1C|uart1: Unhandled write|i2c\d: Unhandled write|No action for reset/;
function onLog(e) {
  if (IGNORED.test(e.text)) return;   // avvisi del boot Xilinx su blocchi non rilevanti (cache L2, SCU)
  const el = $("#emuLog");
  el.insertAdjacentHTML("beforeend", `<span class="${e.level === "error" ? "err" : "wrn"}">${esc(e.text)}</span>\n`);
  if (el.childElementCount > 400) el.firstElementChild.remove();
  if ($("#paneLog").classList.contains("hidden")) {
    logUnread++;
    $("#logCount").textContent = logUnread; $("#logCount").classList.remove("hidden");
  }
}

/* =====================================================================
   Eventi dall'emulatore
   ===================================================================== */
function connect() {
  const es = new EventSource("/api/events");
  es.onmessage = (m) => {
    let e;
    try { e = JSON.parse(m.data); } catch { return; }
    switch (e.t) {
      case "state":
        setState(e.state);
        if (e.state === "booting") termSys("── Avvio dell'emulatore Renode (la prima volta richiede 15-40 s)… ──");
        if (e.state === "idle" && !booted) { booted = true; termSys("── Zybo Z7-10 emulata pronta. Scrivi o apri un programma e premi «Compila ed esegui». ──"); }
        if (e.state === "off" && e.reason) termSys("── " + e.reason + " ──");
        break;
      case "out": if (e.dev === "leds") setLeds(e.v); break;
      case "pin": if (e.dev === "ld4") $("#ld4").classList.toggle("on", !!e.v); break;
      case "in": setInputLevels(e.dev, e.v); break;
      case "irq": onIrq(e); break;
      case "stats": onStats(e); break;
      case "cpu": $("#cpuMips").textContent = e.mips.toFixed(0); break;
      case "uart": termWrite(dec.decode(Uint8Array.from(atob(e.b64), (c) => c.charCodeAt(0)), { stream: true })); break;
      case "i2c": onI2c(e); break;
      case "unmapped": onUnmapped(e); onLog({ level: "warning", text: `accesso a ${e.addr} senza periferica (pc ${e.pc})` }); break;
      case "log": onLog(e); break;
    }
  };
  es.onerror = () => { setState("off"); es.close(); setTimeout(connect, 1500); };
}
let booted = false;

/* ===================================================================== */
loadFiles();
editor.value = files[active];
renderTabs(); renderGutter();
connect();
