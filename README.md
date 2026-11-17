# Banco Zybo Z7-10 emulato

[![prova](https://github.com/mattew-4/banco-zybo/actions/workflows/prova.yml/badge.svg)](https://github.com/mattew-4/banco-zybo/actions/workflows/prova.yml)

Un banco di laboratorio nel browser per scrivere, compilare ed **eseguire davvero** i programmi
del corso *Advanced Embedded Systems* su una **Digilent Zybo Z7-10** (Zynq-7010) emulata,
con LED, pulsanti, switch, Serial Monitor e sensore MPU-6050, anche senza avere la scheda.

Funziona su **Windows**, **macOS** e **Linux**.

> Non contiene materiale del corso (testi, soluzioni, dispense): i tuoi Lab li apri dalla tua
> cartella con il pulsante **«I miei Lab»**.

---

## Requisiti

| Sistema | Supporto |
|---|---|
| Windows 10 / 11 (64 bit) | ✅ |
| macOS con Apple Silicon (M1, M2, M3, M4…) | ✅ |
| macOS con processore Intel | ⚠️ funziona, ma Renode va installato a parte con Homebrew (vedi sotto); non testato |
| Linux x64 / arm64 | ✅ |

Servono anche:
- **Python 3.9 o più recente** (solo la libreria standard: niente `pip install`);
- circa **1 GB di spazio libero**, e 2,5 GB durante la prima installazione;
- **internet la prima volta**, per scaricare compilatore ed emulatore (circa 400 MB); poi solo per
  gli aggiornamenti automatici del banco, facoltativi.

Non servono permessi di amministratore e non viene installato niente nel sistema.

---

## Installazione e avvio

### Windows

1. Installa **Python** da <https://www.python.org/downloads/>.
   Durante l'installazione spunta **«Add python.exe to PATH»**.
2. Scarica il banco: in alto su questa pagina **Code → Download ZIP**, poi estrai lo ZIP dove vuoi
   (per esempio in Documenti). Se hai git puoi anche usare `git clone`.
3. Doppio clic su **`Avvia Banco Zybo.bat`**.
   - Se Windows mostra «Windows ha protetto il PC», scegli **Ulteriori informazioni → Esegui comunque**:
     succede con tutti i file scaricati da internet.
   - **La prima volta** scarica compilatore ed emulatore e verifica i checksum: ci vogliono alcuni minuti.
     Le volte successive parte subito.
4. Si apre il browser su <http://127.0.0.1:8765/>. Per spegnere tutto chiudi la finestra nera.

### macOS

1. **Python 3.** Apri il **Terminale** (Applicazioni → Utility) e scrivi `python3 --version`.
   - Se risponde con un numero ≥ 3.9 sei a posto.
   - Altrimenti macOS ti propone di installare gli «strumenti per sviluppatori»: accetta.
     In alternativa installa Python da <https://www.python.org/downloads/macos/>.
2. **Scarica il banco.** Il modo più semplice è da Terminale:
   ```sh
   git clone https://github.com/mattew-4/banco-zybo.git
   cd banco-zybo
   ```
   Oppure **Code → Download ZIP** ed estrai lo ZIP.
3. **Avvia** dal Terminale, nella cartella del banco:
   ```sh
   sh "Avvia Banco Zybo.command"
   ```
   La prima volta scarica compilatore ed emulatore (alcuni minuti), poi apre il browser.
   Per spegnere tutto: **Ctrl+C** nel Terminale, oppure chiudi la finestra.

   Le volte successive puoi fare **doppio clic** su `Avvia Banco Zybo.command` nel Finder.
   Se hai scaricato lo ZIP, la prima volta macOS lo blocca («Apple non può verificare…»):
   fai **clic destro → Apri → Apri**. Sulle versioni più recenti apri invece
   **Impostazioni di Sistema → Privacy e sicurezza → Apri comunque**.

**Mac con processore Intel:** Renode non ha un pacchetto pronto per questi Mac. Installalo con
[Homebrew](https://brew.sh) (lo compila, ci vuole un po'):
```sh
brew install renode/tap/renode
```
Poi avvia il banco come sopra: userà il comando `renode` del sistema.

### Linux

```sh
git clone https://github.com/mattew-4/banco-zybo.git
cd banco-zybo
sh "Avvia Banco Zybo.command"
```

### Aggiornamenti automatici

Non c'è bisogno di riscaricare il banco quando esce una versione nuova: a ogni avvio
`Avvia Banco Zybo` controlla su GitHub e, se c'è una versione più recente, la installa da sola
(`git pull` se hai usato `git clone`, altrimenti scarica lo ZIP della versione nuova e sostituisce i
file del banco). Compilatore ed emulatore (`tools/`), la cartella `work/` e le tue impostazioni
(`config.json`) non vengono toccati. Senza internet il banco parte con la versione installata.

Per disattivarlo scrivi `"auto_update": false` in `config.json`, nella cartella del banco.

Chi ha scaricato il banco **prima del 17 novembre 2026** deve riscaricarlo un'ultima volta
(o fare `git pull`): le versioni precedenti non avevano ancora l'aggiornamento automatico.

---

## Come si usa

- **Editor:** scrivi il programma (anche più file `.c`/`.h`) e premi **Compila ed esegui**
  (Ctrl+Invio, sul Mac ⌘Invio). Il codice viene compilato con lo stesso compilatore e le stesse
  opzioni del Cortex-A9 Lab, linkato al BSP Xilinx e caricato sulla scheda emulata.
- **Scheda:** clicca pulsanti e switch. Puoi anche simulare il rimbalzo dei contatti.
- **Serial Monitor:** l'output di `xil_printf`/`printf` alla velocità reale della UART (115200 baud).
- **Sensore MPU-6050:** inclinazione, temperatura e rumore regolabili.
- **I miei Lab:** apre i file `.c` di una tua cartella. Con **Cambia cartella…** scegli dove tieni
  i tuoi Lab (sul Mac puoi trascinare la cartella nel Terminale per vederne il percorso).
  I file originali non vengono mai modificati.
- **Esegui .elf…:** esegue un binario già compilato, per esempio quello scaricato dal Cortex-A9 Lab
  con «Download binary».

Nella cartella `examples/` ci sono un programma di partenza (`ciao_zybo.c`) e un passthrough audio
(`audio_passthrough.c`).

### Lab 3: audio e tempi

Il banco emula anche il percorso audio del bitstream del corso: codec **SSM2603** su I2C0,
controllore **axi_i2s_adi** e **AXI4-Stream FIFO** con FIFO RX e TX da 512 parole. Il programma
del Lab 3 gira senza modifiche:

- **dall'IDE del corso:** compila il progetto `lab3_audio`, scaricalo con «Download binary» e
  aprilo con **Esegui .elf…**;
- **nel banco:** copia `main.c`, `audio_io.h` e `fft.h` del progetto in una cartella dei tuoi Lab,
  apri `main.c` con «I miei Lab» → *Apri come nuovo progetto*, poi aggiungi i due `.h` con
  *Aggiungi ai file aperti*, e premi «Compila ed esegui».

Quando il programma usa l'audio compare il pannello **Audio · Lab 3**, con queste parti:
- **LINE IN**: il generatore di segnale che sostituisce il telefono (sinusoide, onda quadra, sweep,
  rumore, silenzio), regolabile anche mentre il programma gira, oppure **audio reale**: un file
  (WAV, MP3, OGG… decodificato dal browser) o qualche secondo registrato dal **microfono** del PC.
  L'audio reale viene riprodotto in ciclo, ricampionato alla frequenza del codec; il cursore diventa
  un guadagno (0 dB = come il file). Dalla riga di comando: `--input "file brano.wav"`;
- lo **stato del codec**, letto dai registri che il programma ha scritto;
- l'**occupazione della FIFO RX** negli ultimi 0,5 s: in rosso i tratti in cui la FIFO era piena e
  le parole si perdevano;
- i contatori di parole perse (overrun) e di underrun del DAC;
- l'**oscilloscopio** delle cuffie e il pulsante **Ascolta**.

**L'orologio «tempo CPU».** Per il Lab 3 conta il tempo che il programma misura con
`XTime_GetTime`. L'emulatore è più lento della scheda, quindi per i programmi con audio il banco usa
un orologio calcolato dal lavoro del Cortex-A9: le istruzioni eseguite, a 667 MHz, più il costo di
ogni accesso ai registri. Le durate misurate dal programma sono quindi quelle della scheda, mentre
l'emulazione procede più lenta del reale (da circa ×0,6 a ×0,1, secondo il carico). L'audio si
ascolta in differita, a velocità vera, con «Ascolta». La scelta è automatica; si può forzare con il
menu **Orologio** sotto l'editor.

Il modello ha tre parametri. I valori predefiniti riproducono quanto descritto nel testo del Lab 3:
| Parametro | Valore | Da dove viene |
|---|---|---|
| Cicli per istruzione | 1,455 | la riga «fft avg 157 max 158 us» del testo del Lab 3, riprodotta con il `main.c` della sessione guidata (N = 512, `-O2`) |
| Lettura di un registro della PL | 1 µs | bus M_AXI_GP0 + AXI4-Stream FIFO |
| Scrittura di un registro della PL | 0,7 µs | memoria strongly-ordered: il processore attende la risposta |

Se misuri valori diversi sulla scheda, puoi cambiarli in `config.json`, nella cartella del banco:
```json
{ "timing": { "cpi": 1.455, "pl_read_ns": 1000, "pl_write_ns": 700,
              "cpi_cache": 1.355, "l2_hit_cycles": 25, "ddr_cycles": 60, "cache": 2 } }
```

Il BSP del banco ha le stesse definizioni (`xparameters.h`) del BSP generato per il bitstream del
corso, compreso il driver Xilinx `XLlFifo` per le FIFO. Della PL del corso non sono emulati la
seconda AXI4-Stream FIFO (`0x43C2_0000`) e gli interrupt delle FIFO (ID 63 e 64): i loro accessi
vengono segnalati in giallo.

**Limiti del modello:** la pipeline non è modellata e, per i programmi del Lab 3, nemmeno le cache
(vedi sotto). Per questo `-O1` e `-O2` danno tempi quasi uguali e misure ripetute danno sempre lo
stesso valore, mentre sulla scheda `max` supera di poco `avg`. Le soglie (quale N perde campioni, il
lavoro più lungo che la FIFO assorbe) sono quelle del modello: **verificale sulla scheda**.

### Cache e contatori di prestazioni (PMU)

Per i programmi che **misurano le prestazioni**, cioè leggono i contatori del PMU (`mrc p15, 0, rX,
c9, c13, …`) o usano le funzioni `Xil_DCache*`, `Xil_L1DCache*`, `Xil_L2Cache*`, `Xpm_*`, il banco
usa il tempo CPU e attiva un modello delle cache dati:
- **L1D** del Cortex-A9: 32 KB, 4 vie, righe da 32 byte, write-back, sostituzione pseudo-casuale; si
  spegne e si riaccende con `SCTLR.C` (`Xil_DCacheDisable`/`Xil_DCacheEnable`);
- **L2 PL310**: 512 KB, 8 vie, con il prefetch della riga successiva abilitato dal BSP;
  manutenzione (`Xil_DCacheFlush*`, `…Invalidate*`) e contatori di eventi del PL310 (DRREQ, DRHIT,
  DWREQ, DWHIT, CO);
- un miss costa cicli in più: +25 se la riga è in L2, +60 se va presa dalla DDR (`l2_hit_cycles`,
  `ddr_cycles` in `config.json`); con la D-cache spenta ogni lettura va in DDR;
- **PMU**: `PMCCNTR` conta i cicli del tempo CPU (667 MHz: il doppio dei tick di `XTime`), compresi
  miss e attese sui registri, con il divisore per 64 di `PMCR.D`; gli eventi `0x03` (refill L1D) e
  `0x04` (accesso L1D) sono contati dal modello.

Con il modello attivo il CPI medio è più basso (`cpi_cache`: 1,355), perché parte del tempo è nei
miss: l'FFT N = 512 a `-O2` dura sempre circa 157 µs, ma ora `max` supera `avg` e a N = 4096 i dati
escono dalla L1 (circa 2100 µs). La prova `tests/cache_pmu.c` mostra la gerarchia: 12 ns per
accesso in L1, 50 in L2, 77 oltre la L2, 138 con la cache spenta.

Il modello rende l'emulazione circa **5 volte più lenta**, per questo non è attivo per gli altri
programmi. In `config.json` si può forzare: `"cache": 1` sempre, `0` mai, `2` automatico
(predefinito). Non sono modellati la cache istruzioni, il prefetcher della L1 e la manutenzione della
sola L1 via CP15 (il banco non la vede).

---

## La scheda emulata

| Blocco | Indirizzo | Note |
|---|---|---|
| `axi_gpio_0`, pulsanti BTN0-3 | `0x4120_0000` | IRQ 61, rimbalzo dei contatti regolabile |
| `axi_gpio_1`, LED LD0-3 | `0x4121_0000` | |
| `axi_gpio_2`, switch SW0-3 | `0x4122_0000` | IRQ 62 |
| GIC v1 (distributor / CPU interface) | `0xF8F0_1000` / `0xF8F0_0100` | |
| Global timer / timer privato | `0xF8F0_0200` / `0xF8F0_0600` | 333,33 MHz, in tempo reale oppure in tempo CPU |
| UART1 (Serial Monitor) | `0xE000_1000` | 115200 8N1, FIFO 64 byte svuotata al baud rate, IRQ 82 |
| I2C1 + MPU-6050 (GY-521 su Pmod JD) | `0xE000_5000`, slave `0x68` | WHO_AM_I 0x72, NACK a 0x69 |
| GPIO del PS: LD4 (MIO7), BTN4/5 (MIO50/51) | `0xE000_A000` | |
| I2C0 + codec SSM2603 | `0xE000_4000`, slave `0x1A` | registri a 9 bit: guadagni, accensione, formato |
| AXI4-Stream FIFO (PG080) | `0x43C0_0000` | RX e TX da 512 parole, `RDFO`, `RDFD`, `TDFV`, `TDFD`, `TLR` |
| axi_i2s_adi | `0x43C1_0000` | fs dai divisori di `CLK_CTRL`: 48 kHz con i valori del corso |

### Quanto è fedele

- **Reale:** il codice macchina ARM che gira è il tuo, emulato istruzione per istruzione da
  [Renode](https://renode.io). I LED si accendono perché il programma scrive nei registri, e gli
  interrupt passano dal GIC alla tua ISR. I registri seguono i manuali: AXI GPIO PG144, UART e I2C
  UG585, timer del Cortex-A9 MPCore TRM, MPU-6050 rev. 4.2.
- **Tempo (Lab 1-2, «tempo reale»):** timer, `usleep`, `XTime_GetTime`, UART e rimbalzi usano
  l'orologio reale, e una riga stampata impiega lo stesso tempo che sulla scheda. La CPU emulata
  però è più lenta del Cortex-A9 a 667 MHz, quindi i tempi di *esecuzione* misurati dal programma
  risultano più alti.
- **Tempo (Lab 3, «tempo CPU»):** il tempo è calcolato dal lavoro del Cortex-A9 (vedi sopra), quindi
  le durate misurate sono vicine a quelle della scheda, nei limiti del modello.
- **GIC:** il driver `XScuGic` scrive in `ICDICFR` il tipo di trigger nel formato del GIC v1 dello
  Zynq (`01` livello, `11` fronte), che il GIC di Renode non riconosce: il banco toglie il bit del
  modello 1-N (ininfluente su un solo core), così gli interrupt configurati con il driver arrivano.
- **I2C:** in tempo CPU ogni trasferimento dura quanto a 100 kHz (9 bit per byte); in tempo reale è
  istantaneo.

### Quando il programma è sbagliato

Il banco non «aggiusta» gli errori: la scheda emulata sbaglia come quella vera.

| Errore nel programma | Cosa succede (sulla scheda e nel banco) |
|---|---|
| Accesso a un indirizzo della PL dove non c'è nessun blocco | DECERR → **Data Abort** vero: il processore entra nel gestore. Lettura: abort preciso (`DFSR` = 0x008, `DFAR` = indirizzo, `DataAbortAddr` = l'istruzione). Scrittura: abort impreciso, qualche istruzione dopo (`DFSR` = 0x406). Se il programma non ha registrato un suo gestore, quello del BSP si ferma in un ciclo infinito: **il programma si blocca** (il banco lo indica in rosso) |
| Elaborazione troppo lenta, `usleep` o stampe lunghe nel ciclo audio | la FIFO RX si riempie, le parole si perdono, si sentono clic; dopo un numero dispari di parole perse sinistro e destro si scambiano |
| I2S non abilitato, o `TDFD` scritto senza chiudere il pacchetto con `TLR` | il programma resta bloccato in attesa in `read_sample`/`write_sample` |
| `TLR` diverso dai byte scritti | errore TSE nel registro di stato della FIFO |
| Codec non attivato (`R9`), spento (`R6`) o silenziato (`R5`) | silenzio in ingresso e/o in uscita |
| Formato del codec sbagliato (`R7`: left/right-justified, DSP) | i bit finiscono nella posizione sbagliata: campioni scalati o distorti, canali scambiati |
| Codec in modo master (`R7`) | codec e controllore pilotano entrambi i clock: dati senza senso, rumore |
| Frequenza del codec diversa da LRCLK (`R8`) | l'ADC e il DAC campionano alla frequenza sbagliata: suono distorto «a gradini» |
| Guadagno di ingresso troppo alto | saturazione del convertitore |
| Indirizzo I2C del codec sbagliato | NACK: `audio_init` fallisce |
| Variabile locale non inizializzata | contiene residui della memoria (la DDR non è azzerata), non 0 |
| Programma che finisce con `while (1);` | la UART finisce comunque di trasmettere ciò che ha nella FIFO |

**Cosa il banco non riproduce:** le cache per i programmi che non misurano le prestazioni (tempi
un po' più regolari e, a N grandi, più bassi della scheda), la seconda FIFO del design e gli
interrupt delle FIFO (segnalati quando il programma li usa).

**Il banco serve per provare e capire il codice. I risultati da consegnare, soprattutto le misure
di tempo, vanno verificati sulla scheda vera.**

---

## Cosa viene installato, e come toglierlo

`setup.py` scarica dalle pagine ufficiali di GitHub, e verifica con i checksum SHA-256 pubblicati:
- [xPack GNU Arm Embedded GCC 15.2.1](https://github.com/xpack-dev-tools/arm-none-eabi-gcc-xpack),
  ridotto alla sola libreria per Cortex-A9 hard-float;
- [Renode 1.17.0](https://github.com/renode/renode).

Tutto finisce nella cartella `tools/` del banco. Il server ascolta solo su `127.0.0.1`, quindi
non è raggiungibile da altri computer. Quando chiudi la finestra, l'emulatore si chiude con lei.

**Per disinstallare** cancella la cartella del banco. Renode lascia solo un piccolo file di
impostazioni in `%APPDATA%\renode` (Windows) o `~/.config/renode` (Mac/Linux), che puoi cancellare.

---

## Problemi frequenti

| Problema | Soluzione |
|---|---|
| `python` / `python3` non trovato | installa Python (vedi sopra); su Windows ricordati «Add python.exe to PATH» |
| «La porta 8765 è occupata» | il banco è già aperto in un'altra finestra: chiudila, oppure apri <http://127.0.0.1:8765/> |
| Download interrotto o «checksum diverso» | rilancia: il file sbagliato viene cancellato e riscaricato |
| La prima esecuzione resta su «avvio emulatore» | la prima volta Renode impiega fino a 40 s; se supera i 2 minuti chiudi e riapri |
| Mac: «impossibile aprire … sviluppatore non identificato» | clic destro → Apri, oppure avvia da Terminale con `sh "Avvia Banco Zybo.command"` |
| Controllo dell'installazione | `python3 setup.py --check` (Windows: `python setup.py --check`) |

---

## Per chi vuole guardare dentro

| Cartella | Contenuto |
|---|---|
| `server/` | server web (Python, solo libreria standard), compilazione, gestione di Renode |
| `web/` | interfaccia |
| `platform/` | descrizione della scheda per Renode e modelli C# delle periferiche |
| `bsp/` | BSP Xilinx standalone compilato (`lib/libxil.a`), `xparameters.h`, `lscript.ld` |
| `examples/` | programma di esempio |
| `setup.py` | installazione degli strumenti |

- Prova da riga di comando: `python3 -I server/selftest.py examples/ciao_zybo.c 5`
- Ricompilare il BSP: `python3 -I bsp/build_bsp.py`
- A ogni modifica, GitHub Actions prova il banco su macOS, Windows e Linux (`.github/workflows/prova.yml`).

## Licenze

Il codice del banco è distribuito con licenza MIT (vedi `LICENSE`). I file del BSP Xilinx
mantengono la loro licenza MIT (vedi `bsp/LICENSE-Xilinx.md`). Compilatore ed emulatore non sono
inclusi: `setup.py` li scarica dai progetti originali, che hanno le loro licenze (GPL per GCC,
MIT per Renode).

Progetto indipendente creato da studenti: non è affiliato a Digilent, AMD/Xilinx, Antmicro né all'università.
