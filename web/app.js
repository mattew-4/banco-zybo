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
function onAbort(e) {
  const b = $("#stateBadge");
  b.className = "badge paused"; b.textContent = "Data Abort";
  const rd = e.op === "Read";
  // la UART finisce di trasmettere i caratteri gia' nella sua FIFO (come sulla scheda):
  // il messaggio arriva dopo, cosi' sul Serial Monitor l'ordine e' quello reale
  setTimeout(() => termSys(`── DATA ABORT: ${rd ? "lettura" : "scrittura"} a ${e.addr} dall'istruzione a ${e.pc}. ` +
          `In quella zona della PL non c'è nessun blocco: l'interconnect risponde con un errore (DECERR) e il processore ` +
          `entra nel gestore del Data Abort (${rd ? "preciso: DFAR = indirizzo, DFSR = 0x008" : "impreciso, qualche istruzione dopo: DFSR = 0x406"}). ` +
          `Se il programma non ne ha registrato uno, quello del BSP si ferma in un ciclo infinito: il programma è bloccato. ──`), 600);
  warn(`⛔ <b>Data Abort</b>: ${rd ? "lettura" : "scrittura"} a <b>${e.addr}</b> (istruzione a ${e.pc}), dove la PL non ha nessun blocco. ` +
       `Come sulla scheda, il processore è passato al gestore del Data Abort (quello predefinito del BSP blocca il programma). ` +
       `Controlla l'indirizzo: nel tab «Assembly ARM» trovi l'istruzione.`, "abort" + e.addr);
}
function onUnmapped(e) {
  if (e.label) {
    warn(`⚠ Accesso a <b>${e.addr}</b> (istruzione a ${e.pc}): ${esc(e.label)}. Sulla scheda il blocco esiste; nel banco legge 0.`, "lbl" + e.label);
    return;
  }
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
const IGNORED = /pl310: Unhandled|scu: Unhandled|Unknown CP15|slcr: Unhandled write to offset 0xA1C|uart1: Unhandled write|i2c\d: Unhandled write|No action for reset|GICC_CTLR\.AckCtl/;
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
   Audio (Lab 3): codec, FIFO, cuffie
   ===================================================================== */
const AUD_RATE = 24000, AUD_KEEP = 12 * AUD_RATE, OCC_KEEP = 1000;
const aud = { pcm: new Float32Array(AUD_KEEP), w: 0, filled: 0, occ: [], lost: 0, under: 0, level: 0,
              lostHist: [], speed: [], now: 0, drawPending: false, ctx: null, src: null };
function audReset() {
  aud.pcm.fill(0); aud.w = 0; aud.filled = 0; aud.occ = []; aud.lost = 0; aud.under = 0; aud.level = 0;
  aud.lostHist = []; aud.speed = [];
  ["#lostTot", "#lostSec", "#underTot"].forEach((s) => { $(s).textContent = "0"; $(s).classList.remove("bad"); });
  $("#simSpeed").textContent = "–"; $("#fifoNow").textContent = "–"; $("#fifoMax").textContent = "–";
  drawAudio();
}
function showAudioCard(on) { $("#audioCard").classList.toggle("hidden", !on); }
function onAudio(e) {
  showAudioCard(true);
  const bin = atob(e.pcm || "");
  const n = bin.length >> 1;
  for (let i = 0; i < n; i++) {
    let v = bin.charCodeAt(2 * i) | (bin.charCodeAt(2 * i + 1) << 8);
    if (v & 0x8000) v -= 0x10000;
    aud.pcm[aud.w] = v / 32768;
    aud.w = (aud.w + 1) % AUD_KEEP;
  }
  aud.filled = Math.min(AUD_KEEP, aud.filled + n);
  for (const v of e.occ || []) aud.occ.push(v);
  if (aud.occ.length > OCC_KEEP) aud.occ.splice(0, aud.occ.length - OCC_KEEP);
  aud.lost = e.lost; aud.under = e.under; aud.level = e.level; aud.now = e.now;
  aud.lostHist.push([e.now, e.lost]);
  while (aud.lostHist.length > 2 && aud.lostHist[1][0] < e.now - 1) aud.lostHist.shift();
  const wall = performance.now() / 1000;
  aud.speed.push([wall, e.now]);
  while (aud.speed.length > 2 && aud.speed[0][0] < wall - 4) aud.speed.shift();
  $("#boardTime").textContent = (+e.now).toFixed(3);
  if (!aud.drawPending) { aud.drawPending = true; requestAnimationFrame(() => { aud.drawPending = false; drawAudio(); }); }
}
function drawAudio() {
  // contatori
  $("#lostTot").textContent = aud.lost.toLocaleString("it-IT");
  $("#lostTot").classList.toggle("bad", aud.lost > 0);
  const h0 = aud.lostHist[0];
  const ls = h0 ? aud.lost - h0[1] : 0;
  $("#lostSec").textContent = ls.toLocaleString("it-IT");
  $("#lostSec").classList.toggle("bad", ls > 0);
  $("#underTot").textContent = aud.under.toLocaleString("it-IT");
  if (aud.speed.length > 1) {
    const a = aud.speed[0], b = aud.speed[aud.speed.length - 1];
    if (b[0] - a[0] > 0.5) $("#simSpeed").textContent = "×" + ((b[1] - a[1]) / (b[0] - a[0])).toFixed(2).replace(".", ",");
  }
  // occupazione della FIFO RX: un valore ogni 0,5 ms, negativo = parole perse in quell'intervallo
  const cv = $("#fifoCanvas"), g = cv.getContext("2d"), W = cv.width, H = cv.height;
  g.clearRect(0, 0, W, H);
  const top = 8, hh = H - top - 4;
  g.strokeStyle = "#ff5c6c"; g.setLineDash([5, 4]); g.beginPath(); g.moveTo(0, top); g.lineTo(W, top); g.stroke(); g.setLineDash([]);
  g.font = "10px monospace";
  const kl = "K = 512: piena, le parole nuove si perdono";
  g.fillStyle = "rgba(11,15,20,.85)"; g.fillRect(3, top + 2, g.measureText(kl).width + 8, 13);
  g.fillStyle = "#8a96a5"; g.fillText(kl, 7, top + 12);
  const occ = aud.occ, n = occ.length;
  let mx = 0;
  if (n) {
    const per = OCC_KEEP / W;
    for (let x = 0; x < W; x++) {
      const i0 = Math.floor(n - OCC_KEEP + x * per), i1 = Math.floor(n - OCC_KEEP + (x + 1) * per);
      let v = -1, lost = false;
      for (let i = Math.max(0, i0); i < Math.min(n, Math.max(i1, i0 + 1)); i++) {
        const o = occ[i] < 0 ? -occ[i] - 1 : occ[i];
        if (occ[i] < 0) lost = true;
        if (o > v) v = o;
      }
      if (v < 0) continue;
      if (v > mx) mx = v;
      const y = top + hh * (1 - v / 512);
      g.fillStyle = lost ? "#ff5c6c" : "#4da3ff";
      g.fillRect(x, y, 1, H - 4 - y);
    }
  }
  $("#fifoNow").textContent = aud.level;
  $("#fifoMax").textContent = n ? mx : "–";
  // cuffie: ultimi 20 ms, agganciati al passaggio per lo zero in salita
  const sc = $("#scopeCanvas"), s = sc.getContext("2d"), SW = sc.width, SH = sc.height;
  s.clearRect(0, 0, SW, SH);
  s.strokeStyle = "#2a323d"; s.beginPath(); s.moveTo(0, SH / 2); s.lineTo(SW, SH / 2); s.stroke();
  const span = AUD_RATE * 0.02;
  if (aud.filled > span * 3) {
    const at = (k) => aud.pcm[(aud.w - k + AUD_KEEP * 2) % AUD_KEEP];
    let start = span * 2;
    for (let k = span * 2; k > span; k--) { if (at(k) <= 0 && at(k - 1) > 0) { start = k; break; } }
    let peak = 0.05;
    for (let k = start; k > start - span; k--) peak = Math.max(peak, Math.abs(at(k)));
    s.strokeStyle = "#3ddc84"; s.beginPath();
    for (let x = 0; x < span; x++) {
      const y = SH / 2 - at(start - x) / peak * (SH / 2 - 6);
      const px = x / span * SW;
      x ? s.lineTo(px, y) : s.moveTo(px, y);
    }
    s.stroke();
    const lbl = `picco ${(20 * Math.log10(peak)).toFixed(1).replace(".", ",")} dBFS`;
    s.font = "10px monospace";
    s.fillStyle = "rgba(11,15,20,.85)"; s.fillRect(3, 2, s.measureText(lbl).width + 8, 14);
    s.fillStyle = "#8a96a5"; s.fillText(lbl, 7, 12);
  }
}
function onCodec(r) {
  const el = $("#codecState");
  const defaults = [0x097, 0x097, 0x079, 0x079, 0x00A, 0x008, 0x09F, 0x00A, 0x000, 0x000];
  if (defaults.every((v, i) => r[i] === v)) {
    el.className = "audio-codec";
    el.textContent = "Codec SSM2603 (I2C0 · 0x1A): registri ai valori di reset, non ancora configurato dal programma.";
    return;
  }
  const db = (x) => (x > 0 ? "+" : "") + x.toFixed(1).replace(".", ",") + " dB";
  const pd = r[6], active = r[9] & 1;
  const inOn = !(pd & 0x81) && !(pd & 0x04) && !(r[4] & 0x04);
  const outOn = !(pd & 0x80) && !(pd & 0x18) && (r[4] & 0x10) && !(r[5] & 0x08);
  const lin = r[0], hp = r[2] & 0x7f;
  const fmt = ["right-justified", "left-justified", "I2S", "DSP"][r[7] & 3], wl = [16, 20, 24, 32][(r[7] >> 2) & 3];
  const parts = [
    active ? "attivo" : "NON attivo (R9)",
    inOn ? `line in ${lin & 0x80 ? "muto" : db(((lin & 0x3f) - 0x17) * 1.5)}` : "ingresso spento",
    outOn ? `cuffie ${hp <= 0x2f ? "mute" : db(hp - 0x79)}` : "cuffie spente",
    `${fmt}, ${wl} bit, codec ${r[7] & 0x40 ? "master" : "slave"}`,
  ];
  el.className = "audio-codec " + (active && inOn && outOn ? "on" : "bad");
  el.textContent = "SSM2603: " + parts.join(" · ");
}
function listen() {
  stopListen();
  const secs = +$("#listenSecs").value;
  const n = Math.min(aud.filled, secs * AUD_RATE);
  if (n < AUD_RATE * 0.1) { termSys("── Nessun audio da ascoltare: avvia un programma che usa il codec. ──"); return; }
  aud.ctx = aud.ctx || new (window.AudioContext || window.webkitAudioContext)();
  const buf = aud.ctx.createBuffer(1, n, AUD_RATE);
  const d = buf.getChannelData(0);
  for (let i = 0; i < n; i++) d[i] = aud.pcm[(aud.w - n + i + AUD_KEEP) % AUD_KEEP];
  aud.src = aud.ctx.createBufferSource();
  aud.src.buffer = buf;
  aud.src.connect(aud.ctx.destination);
  aud.src.start();
}
function stopListen() { if (aud.src) { try { aud.src.stop(); } catch { } aud.src = null; } }
$("#listenBtn").addEventListener("click", (e) => { if (e.target.tagName !== "SELECT") listen(); });
$("#listenStop").addEventListener("click", stopListen);

let ainTimer = null;
function ainLabels() {
  const file = $("#ainType").value === "file";
  $("#ainFreqBox").classList.toggle("hidden", file);
  $("#ainLevelLbl").textContent = file ? "guadagno" : "livello";
  $("#ainLevelv").textContent = $("#ainLevel").value + (file ? " dB" : " dBFS");
}
function sendAudioIn(e) {
  if (e && e.target.id === "ainType" && $("#ainType").value === "file" && $("#ainFileInfo").textContent.startsWith("nessun")) {
    $("#ainFile").click();                     // nessun audio caricato: si sceglie subito il file
  }
  ainLabels();
  clearTimeout(ainTimer);
  ainTimer = setTimeout(() => api("/api/audioin", { type: $("#ainType").value, freq: +$("#ainFreq").value, level: +$("#ainLevel").value }), 60);
}
["ainType", "ainFreq", "ainLevel"].forEach((id) => $("#" + id).addEventListener("input", sendAudioIn));

// ---- audio reale: file o microfono -> WAV 16 bit -> server -> LINE IN del banco ----
function showAudioFile(f) {
  $("#ainFileInfo").textContent = f
    ? `${f.name}: ${f.seconds.toFixed(1)} s, ${f.rate} Hz, ${f.channels === 2 ? "stereo" : "mono"}, in ciclo${f.truncated ? " (tagliato a 120 s)" : ""}`
    : "nessun audio reale caricato";
}
function encodeWav(buf) {                      // AudioBuffer -> WAV PCM 16 bit (max 2 canali)
  const ch = Math.min(2, buf.numberOfChannels), n = buf.length, out = new DataView(new ArrayBuffer(44 + n * ch * 2));
  const w = (o, s) => [...s].forEach((c, i) => out.setUint8(o + i, c.charCodeAt(0)));
  w(0, "RIFF"); out.setUint32(4, 36 + n * ch * 2, true); w(8, "WAVEfmt "); out.setUint32(16, 16, true);
  out.setUint16(20, 1, true); out.setUint16(22, ch, true); out.setUint32(24, buf.sampleRate, true);
  out.setUint32(28, buf.sampleRate * ch * 2, true); out.setUint16(32, ch * 2, true); out.setUint16(34, 16, true);
  w(36, "data"); out.setUint32(40, n * ch * 2, true);
  const data = [...Array(ch).keys()].map((c) => buf.getChannelData(c));
  for (let i = 0, o = 44; i < n; i++) for (let c = 0; c < ch; c++, o += 2)
    out.setInt16(o, Math.max(-32768, Math.min(32767, Math.round(data[c][i] * 32768))), true);
  return out.buffer;
}
async function uploadAudio(name, arrayBuf) {
  $("#ainFileInfo").textContent = "conversione…";
  try {
    let wav = arrayBuf;
    const head = new TextDecoder().decode(new Uint8Array(arrayBuf, 0, Math.min(12, arrayBuf.byteLength)));
    if (!(head.startsWith("RIFF") && head.endsWith("WAVE"))) {
      // MP3, OGG, registrazione del microfono...: decodifica del browser, poi WAV
      const ctx = new (window.AudioContext || window.webkitAudioContext)();
      const buf = await ctx.decodeAudioData(arrayBuf.slice(0));
      ctx.close();
      wav = encodeWav(buf);
    }
    const r = await api("/api/audiofile?name=" + encodeURIComponent(name), wav, true);
    if (r.error) throw new Error(r.error);
    $("#ainType").value = "file"; $("#ainLevel").value = r.audio.level;
    ainLabels(); showAudioFile(r.audio.file);
  } catch (err) {
    $("#ainFileInfo").textContent = "⚠ " + (err.message || err);
  }
}
$("#ainFileBtn").addEventListener("click", () => $("#ainFile").click());
$("#ainFile").addEventListener("change", async (e) => {
  const f = e.target.files[0];
  if (f) uploadAudio(f.name, await f.arrayBuffer());
  e.target.value = "";
});
$("#ainMicBtn").addEventListener("click", async () => {
  const btn = $("#ainMicBtn"), secs = +$("#ainMicSecs").value;
  if (!navigator.mediaDevices || !window.MediaRecorder) { $("#ainFileInfo").textContent = "⚠ questo browser non permette di registrare"; return; }
  let stream;
  try {
    // niente elaborazioni del browser: si vuole il segnale com'e', come da un cavo su LINE IN
    stream = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: false, noiseSuppression: false, autoGainControl: false } });
  } catch (err) { $("#ainFileInfo").textContent = "⚠ microfono non disponibile: " + (err.message || err); return; }
  const rec = new MediaRecorder(stream), parts = [];
  rec.ondataavailable = (ev) => parts.push(ev.data);
  rec.onstop = async () => {
    stream.getTracks().forEach((t) => t.stop());
    btn.disabled = false; btn.textContent = "🎙 Registra dal microfono";
    uploadAudio(`microfono (${secs} s)`, await new Blob(parts).arrayBuffer());
  };
  btn.disabled = true;
  rec.start();
  for (let k = secs; k > 0; k--) { btn.textContent = `🔴 registro… ${k} s`; await new Promise((r) => setTimeout(r, 1000)); }
  rec.stop();
});
$("#clockSel").addEventListener("change", () => api("/api/config", { clock: $("#clockSel").value }));
(async () => {
  try {
    const s = await api("/api/status");
    if (s.clock) $("#clockSel").value = s.clock;
    if (s.audio) {
      $("#ainType").value = s.audio.type; $("#ainFreq").value = s.audio.freq; $("#ainLevel").value = s.audio.level;
      ainLabels(); showAudioFile(s.audio.file);
    }
  } catch { }
})();
showAudioCard(false);

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
        if (e.state === "running" && e.clock) {
          $("#clockNow").textContent = e.clock === "cpu" ? "tempo CPU" : "tempo reale";
          $("#clockMeter").title = e.clock === "cpu"
            ? "Tempo CPU: il tempo della scheda si calcola dalle istruzioni eseguite dal Cortex-A9 (667 MHz) e dagli accessi ai registri. Le durate misurate dal programma sono quelle della scheda."
            : "Tempo reale: il tempo della scheda è quello del PC.";
          if (e.wall && e.audio !== undefined) { audReset(); showAudioCard(!!e.audio); }
          if (e.audio) {
            termSys(e.clock === "cpu"
              ? "── Programma con audio: orologio «tempo CPU». I tempi misurati sono quelli del Cortex-A9; l'emulazione è più lenta del reale. ──"
              : "── Attenzione: programma con audio ma orologio «tempo reale». L'emulatore non regge 96 000 parole al secondo in tempo reale: scegli «tempo CPU» o «automatico». ──");
          }
        }
        break;
      case "abort": onAbort(e); break;
      case "warn": warn("⚠ " + esc(e.text), e.key); break;
      case "audio": onAudio(e); break;
      case "audio_reset": audReset(); break;
      case "codec": onCodec(e.regs); break;
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
