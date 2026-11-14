//
// ZyboPeripherals.cs -- modelli Renode delle periferiche della Zybo Z7-10 del corso
// che Renode non contiene: AXI GPIO (bitstream del corso), MPU-6050 (GY-521 su Pmod JD),
// sonda per i pin del GPIO del PS (LD4) e il ponte verso l'interfaccia web.
//
// Riferimenti:
//   AXI GPIO v2.0, AMD PG144 (registri GPIO_DATA/TRI, GIER, IP IER, IP ISR toggle-on-write)
//   InvenSense MPU-6000/6050 Register Map rev. 4.2, Product Specification rev. 3.4
//
using System;
using System.Collections.Generic;
using System.Globalization;
using System.Net;
using System.Net.Sockets;
using System.Text;
using System.Threading;
using Antmicro.Renode.Core;
using Antmicro.Renode.Logging;
using Antmicro.Renode.Peripherals.Bus;
using Antmicro.Renode.Peripherals.CPU;
using Antmicro.Renode.Peripherals.I2C;
using Antmicro.Renode.Time;

namespace Antmicro.Renode.Peripherals.Zybo
{
    // ------------------------------------------------------------------
    // Ponte verso l'interfaccia: datagrammi UDP JSON su 127.0.0.1:ZYBO_UI_PORT
    // ------------------------------------------------------------------
    public static class ZyboUi
    {
        static ZyboUi()
        {
            var p = Environment.GetEnvironmentVariable("ZYBO_UI_PORT");
            int port;
            if(p != null && int.TryParse(p, out port))
            {
                endpoint = new IPEndPoint(IPAddress.Loopback, port);
                client = new UdpClient();
            }
        }

        public static void Send(string json)
        {
            if(client == null)
            {
                return;
            }
            var bytes = Encoding.UTF8.GetBytes(json);
            lock(sync)
            {
                try { client.Send(bytes, bytes.Length, endpoint); } catch(Exception) { }
            }
        }

        public static string Num(double d)
        {
            return d.ToString("0.######", CultureInfo.InvariantCulture);
        }

        private static readonly object sync = new object();
        private static readonly UdpClient client;
        private static readonly IPEndPoint endpoint;
    }

    // ------------------------------------------------------------------
    // Orologio della scheda. E' la base dei timer del Cortex-A9 (global timer,
    // timer privato), della UART, del rimbalzo dei pulsanti, del sensore e
    // del flusso audio. Due modalita':
    //   RealTime  tempo reale del PC (Stopwatch) che si ferma in pausa: i LED
    //             lampeggiano e la seriale scrive alla velocita' vera (Lab 1-2);
    //   Cpu       tempo del Cortex-A9 calcolato dalle istruzioni eseguite e dagli
    //             accessi ai registri (CpuClock): le durate misurate dal programma
    //             sono quelle della scheda anche se l'emulatore e' piu' lento (Lab 3).
    // ------------------------------------------------------------------
    public enum ClockMode { RealTime, Cpu }

    public static class BoardClock
    {
        public static ClockMode Mode { get { return mode; } }

        public static void SetMode(ClockMode m)
        {
            lock(sync) { mode = m; }
        }

        public static double Seconds
        {
            get
            {
                if(mode == ClockMode.Cpu)
                {
                    return CpuClock.Seconds;
                }
                lock(sync)
                {
                    return accumulated + (running ? watch.Elapsed.TotalSeconds : 0);
                }
            }
        }

        public static void Pause()
        {
            lock(sync)
            {
                if(!running) { return; }
                accumulated += watch.Elapsed.TotalSeconds;
                watch.Reset();
                running = false;
            }
        }

        public static void Resume()
        {
            lock(sync)
            {
                if(running) { return; }
                watch.Restart();
                running = true;
            }
            Scheduler.Wake();
        }

        public static void ResetTo(double seconds, bool run)
        {
            lock(sync)
            {
                accumulated = seconds;
                running = run;
                watch.Reset();
                if(run) { watch.Start(); }
            }
            CpuClock.Rebase(seconds);
            Scheduler.Wake();
        }

        public static bool Running { get { lock(sync) { return running; } } }

        private static readonly object sync = new object();
        private static readonly System.Diagnostics.Stopwatch watch = new System.Diagnostics.Stopwatch();
        private static double accumulated;
        private static bool running;
        private static volatile ClockMode mode = ClockMode.RealTime;
    }

    // ------------------------------------------------------------------
    // Tempo del Cortex-A9 (modalita' Cpu):
    //   t = istruzioni eseguite * CPI / f_CPU  +  latenze degli accessi ai registri  +  attese saltate
    // Il contatore di istruzioni di Renode e' esatto al confine di ogni blocco di codice.
    // Taratura (README, "Lab 3"): CPI medio dai 157 us dell'FFT N = 512 a -O2 misurati
    // sulla scheda; latenze dei registri della PL (bus M_AXI_GP0 + AXI4-Stream FIFO)
    // scelte perche' a N = 4096, -O2 l'FFT stia sotto i 2666 us e si perdano comunque
    // campioni, come descritto nel Lab 3. Le cache non sono modellate.
    // ------------------------------------------------------------------
    public static class CpuClock
    {
        public static double CyclesPerInstruction = 1.51;
        public static double CpuHz = 666666687;
        public static double PlReadSeconds = 1.0e-6;      // lettura di un registro nella PL
        public static double PlWriteSeconds = 0.7e-6;     // scrittura (memoria strongly-ordered: attende la risposta)
        public static double PsReadSeconds = 0.15e-6;     // periferiche del PS (UART, GPIO del PS)
        public static double PsWriteSeconds = 0.10e-6;
        public static double PrivateSeconds = 0.03e-6;    // global timer e timer privato (SCU)
        public static double SpinStepSeconds = 1.0e-6;    // passo del salto nei cicli di attesa sul global timer

        public static void Attach(IMachine m)
        {
            machine = m;      // la CPU si cerca al primo uso: quando nasce il timer non e' ancora sul bus
        }

        public static ulong Instructions
        {
            get
            {
                var c = cpu;
                if(c == null && machine != null)
                {
                    foreach(var x in machine.SystemBus.GetCPUs())
                    {
                        c = cpu = x;
                        break;
                    }
                }
                return c == null ? 0 : c.ExecutedInstructions;
            }
        }

        public static double Seconds
        {
            get
            {
                lock(sync)
                {
                    return NowLocked();
                }
            }
        }

        // tempo che passa durante un accesso a un registro (solo in modalita' Cpu)
        public static void Charge(double seconds)
        {
            if(BoardClock.Mode != ClockMode.Cpu || seconds <= 0) { return; }
            lock(sync)
            {
                extra += seconds;
            }
        }

        // il programma e' in un ciclo di attesa: il tempo salta all'istante t
        public static void AdvanceTo(double t)
        {
            if(BoardClock.Mode != ClockMode.Cpu) { return; }
            lock(sync)
            {
                var now = NowLocked();
                if(t > now) { extra += t - now; }
            }
        }

        public static void Rebase(double seconds)
        {
            lock(sync)
            {
                instrBase = Instructions;
                baseSeconds = seconds;
                extra = 0;
                last = seconds;
            }
        }

        private static double NowLocked()
        {
            ulong i = Instructions;
            if(i < instrBase) { instrBase = i; }           // il contatore riparte da zero al reset della CPU
            double t = baseSeconds + (i - instrBase) * CyclesPerInstruction / CpuHz + extra;
            if(t < last) { t = last; }
            last = t;
            return t;
        }

        private static readonly object sync = new object();
        private static ICPU cpu;
        private static IMachine machine;
        private static ulong instrBase;
        private static double baseSeconds, extra, last;
    }

    // Ogni accesso del processore ai registri delle periferiche del banco passa di qui:
    // in modalita' Cpu fa trascorrere la latenza del bus ed esegue gli eventi scaduti
    // (fronti dei pulsanti, interrupt dei timer) nell'istante giusto del tempo della CPU.
    // Restituisce un numero d'ordine: due accessi con numeri consecutivi non hanno altri
    // accessi in mezzo (serve a riconoscere i cicli di attesa).
    public static class ZyboBus
    {
        public static long Access(double latencySeconds)
        {
            if(BoardClock.Mode == ClockMode.Cpu)
            {
                CpuClock.Charge(latencySeconds);
                Scheduler.RunDue();
            }
            return Interlocked.Increment(ref seq);
        }

        private static long seq;
    }

    // Scheduler in tempo-scheda: esegue azioni a un istante dell'orologio della scheda
    public static class Scheduler
    {
        static Scheduler()
        {
            thread = new Thread(Loop) { IsBackground = true, Name = "zybo-rt-scheduler", Priority = ThreadPriority.AboveNormal };
            thread.Start();
        }

        public static void At(double boardSeconds, Action action)
        {
            lock(sync)
            {
                items.Add(new KeyValuePair<double, Action>(boardSeconds, action));
                items.Sort((a, b) => a.Key.CompareTo(b.Key));
            }
            Wake();
        }

        public static void After(double seconds, Action action)
        {
            At(BoardClock.Seconds + seconds, action);
        }

        public static void Clear()
        {
            lock(sync) { items.Clear(); }
        }

        // modalita' Cpu: esegue subito, nel thread della CPU, gli eventi gia' scaduti
        public static void RunDue()
        {
            while(true)
            {
                Action a = null;
                lock(sync)
                {
                    if(items.Count > 0 && BoardClock.Running && items[0].Key <= BoardClock.Seconds)
                    {
                        a = items[0].Value;
                        items.RemoveAt(0);
                    }
                }
                if(a == null) { return; }
                try { a(); } catch(Exception) { }
            }
        }

        public static void Wake()
        {
            wake.Set();
        }

        private static void Loop()
        {
            while(true)
            {
                KeyValuePair<double, Action>? due = null;
                double wait = 0.05;
                lock(sync)
                {
                    if(items.Count > 0)
                    {
                        var now = BoardClock.Seconds;
                        if(items[0].Key <= now && BoardClock.Running)
                        {
                            due = items[0];
                            items.RemoveAt(0);
                        }
                        else if(BoardClock.Running)
                        {
                            wait = items[0].Key - now;
                        }
                    }
                }
                if(due.HasValue)
                {
                    try { due.Value.Value(); } catch(Exception) { }
                    continue;
                }
                if(wait > 0.003)
                {
                    wake.WaitOne(TimeSpan.FromSeconds(Math.Min(wait - 0.002, 0.05)));
                }
                else
                {
                    Thread.Sleep(0);   // ultimi ms: attesa attiva per avere precisione sub-millisecondo
                }
            }
        }

        private static readonly object sync = new object();
        private static readonly List<KeyValuePair<double, Action>> items = new List<KeyValuePair<double, Action>>();
        private static readonly AutoResetEvent wake = new AutoResetEvent(false);
        private static readonly Thread thread;
    }

    // ------------------------------------------------------------------
    // Global timer del Cortex-A9 MPCore (TRM r4p1, 4.4), base 0xF8F00200.
    // Conta a PERIPHCLK = CPU/2 in tempo reale della scheda.
    // ------------------------------------------------------------------
    public class A9GlobalTimer : IDoubleWordPeripheral, IKnownSize
    {
        public A9GlobalTimer(IMachine machine, double frequency = 333333343.5)
        {
            this.frequency = frequency;
            IRQ = new GPIO();
            CpuClock.Attach(machine);
            Reset();
        }

        public GPIO IRQ { get; private set; }
        public long Size { get { return 0x20; } }

        // Controllo dell'orologio della scheda dal Monitor:
        //   globalTimer ClockPause / globalTimer ClockResume / globalTimer BoardSeconds
        //   globalTimer ClockMode "cpu" | "real"   (prima di "machine Reset")
        //   globalTimer CyclesPerInstruction 1.51, PlReadNs 1000, PlWriteNs 700  (taratura)
        public void ClockPause() { BoardClock.Pause(); }
        public void ClockResume() { BoardClock.Resume(); }
        public double BoardSeconds { get { return BoardClock.Seconds; } }
        public string ClockMode
        {
            get { return BoardClock.Mode == Zybo.ClockMode.Cpu ? "cpu" : "real"; }
            set { BoardClock.SetMode(value == "cpu" ? Zybo.ClockMode.Cpu : Zybo.ClockMode.RealTime); }
        }
        public double CyclesPerInstruction { get { return CpuClock.CyclesPerInstruction; } set { CpuClock.CyclesPerInstruction = value; } }
        public double PlReadNs { get { return CpuClock.PlReadSeconds * 1e9; } set { CpuClock.PlReadSeconds = value * 1e-9; } }
        public double PlWriteNs { get { return CpuClock.PlWriteSeconds * 1e9; } set { CpuClock.PlWriteSeconds = value * 1e-9; } }

        public void Reset()
        {
            // reset della macchina: l'orologio della scheda riparte da zero (fermo fino a ClockResume)
            Scheduler.Clear();
            BoardClock.ResetTo(0, false);
            lock(sync)
            {
                control = 0; isr = 0; comparator = 0; autoInc = 0;
                counterBase = 0; timeBase = BoardClock.Seconds;
                generation++;
                UpdateIrq();
            }
        }

        public uint ReadDoubleWord(long offset)
        {
            var id = ZyboBus.Access(CpuClock.PrivateSeconds);
            if(BoardClock.Mode == Zybo.ClockMode.Cpu && (offset == 0x00 || offset == 0x04))
            {
                // ciclo di attesa (usleep, sleep): letture del contatore una dopo l'altra, a poche
                // istruzioni di distanza e senza altri accessi in mezzo. Dopo qualche giro il tempo
                // avanza a passi di SpinStep per lettura: l'attesa dura lo stesso, ma l'emulatore
                // non deve eseguirne ogni giro. Una misura con XTime_GetTime prima e dopo un
                // calcolo non e' toccata: tra le due letture ci sono migliaia di istruzioni.
                ulong ins = CpuClock.Instructions;
                if(id == lastReadId + 1 && ins - lastReadInstr < 64) { spinRun++; } else { spinRun = 0; }
                lastReadId = id;
                lastReadInstr = ins;
                if(spinRun >= 24) { CpuClock.Charge(CpuClock.SpinStepSeconds); }
            }
            lock(sync)
            {
                switch(offset)
                {
                case 0x00: return (uint)Counter();
                case 0x04: return (uint)(Counter() >> 32);
                case 0x08: return control;
                case 0x0C: return isr;
                case 0x10: return (uint)comparator;
                case 0x14: return (uint)(comparator >> 32);
                case 0x18: return autoInc;
                default: return 0;
                }
            }
        }

        public void WriteDoubleWord(long offset, uint value)
        {
            ZyboBus.Access(CpuClock.PrivateSeconds);
            lock(sync)
            {
                switch(offset)
                {
                case 0x00:
                    if(!Enabled) { counterBase = (counterBase & 0xFFFFFFFF00000000UL) | value; }
                    break;
                case 0x04:
                    if(!Enabled) { counterBase = (counterBase & 0xFFFFFFFFUL) | ((ulong)value << 32); }
                    break;
                case 0x08:
                    var c = Counter();
                    control = value & 0xFF0F;
                    counterBase = c;
                    timeBase = BoardClock.Seconds;
                    break;
                case 0x0C:
                    isr &= ~(value & 1);
                    break;
                case 0x10: comparator = (comparator & 0xFFFFFFFF00000000UL) | value; break;
                case 0x14: comparator = (comparator & 0xFFFFFFFFUL) | ((ulong)value << 32); break;
                case 0x18: autoInc = value; break;
                }
                ScheduleCompare();
                UpdateIrq();
            }
        }

        private bool Enabled { get { return (control & 1) != 0; } }
        private double Rate { get { return frequency / (((control >> 8) & 0xFF) + 1); } }

        private ulong Counter()
        {
            if(!Enabled) { return counterBase; }
            return counterBase + (ulong)Math.Max(0, (BoardClock.Seconds - timeBase) * Rate);
        }

        private void ScheduleCompare()
        {
            generation++;
            if(!Enabled || (control & 2) == 0) { return; }
            var now = Counter();
            if(now >= comparator) { Fire(generation); return; }
            double secs = (comparator - now) / Rate;
            var g = generation;
            Scheduler.After(secs, () => { lock(sync) { Fire(g); } });
        }

        private void Fire(long gen)
        {
            if(gen != generation) { return; }
            isr |= 1;
            if((control & 8) != 0 && autoInc != 0)
            {
                comparator += autoInc;
                ScheduleCompare();
            }
            UpdateIrq();
        }

        private void UpdateIrq()
        {
            IRQ.Set((isr & 1) != 0 && (control & 4) != 0);
        }

        private readonly object sync = new object();
        private readonly double frequency;
        private uint control, isr, autoInc;
        private ulong comparator, counterBase;
        private double timeBase;
        private long generation;
        private long lastReadId;
        private ulong lastReadInstr;
        private int spinRun;
    }

    // ------------------------------------------------------------------
    // Timer privato del Cortex-A9 MPCore (TRM r4p1, 4.1), base 0xF8F00600.
    // Conta all'indietro a PERIPHCLK = CPU/2 in tempo reale della scheda.
    // ------------------------------------------------------------------
    public class A9PrivateTimer : IDoubleWordPeripheral, IKnownSize
    {
        public A9PrivateTimer(IMachine machine, double frequency = 333333343.5)
        {
            this.frequency = frequency;
            IRQ = new GPIO();
            Reset();
        }

        public GPIO IRQ { get; private set; }
        public long Size { get { return 0x20; } }

        public void Reset()
        {
            lock(sync)
            {
                load = 0; control = 0; isr = 0;
                counterAtBase = 0; timeBase = BoardClock.Seconds;
                generation++;
                IRQ.Set(false);
            }
        }

        public uint ReadDoubleWord(long offset)
        {
            ZyboBus.Access(CpuClock.PrivateSeconds);
            lock(sync)
            {
                switch(offset)
                {
                case 0x00: return load;
                case 0x04: return Counter();
                case 0x08: return control;
                case 0x0C: return isr;
                default: return 0;
                }
            }
        }

        public void WriteDoubleWord(long offset, uint value)
        {
            ZyboBus.Access(CpuClock.PrivateSeconds);
            lock(sync)
            {
                switch(offset)
                {
                case 0x00:   // Load: scrive anche il contatore
                    load = value;
                    Rebase(value);
                    break;
                case 0x04:
                    Rebase(value);
                    break;
                case 0x08:
                    var c = Counter();
                    control = value & 0xFF07;
                    Rebase(c);
                    break;
                case 0x0C:
                    isr &= ~(value & 1);
                    break;
                }
                UpdateIrq();
            }
        }

        private bool Enabled { get { return (control & 1) != 0; } }
        private double Rate { get { return frequency / (((control >> 8) & 0xFF) + 1); } }

        private uint Counter()
        {
            if(!Enabled) { return counterAtBase; }
            double ticks = (BoardClock.Seconds - timeBase) * Rate;
            if(ticks < counterAtBase) { return (uint)(counterAtBase - ticks); }
            if((control & 2) != 0 && load != 0)
            {
                double rest = (ticks - counterAtBase) % ((double)load + 1);
                return (uint)Math.Max(0, load - rest);
            }
            return 0;
        }

        private void Rebase(uint value)
        {
            counterAtBase = value;
            timeBase = BoardClock.Seconds;
            Schedule();
        }

        private void Schedule()
        {
            generation++;
            if(!Enabled) { return; }
            var g = generation;
            double secs = (counterAtBase + 1) / Rate;
            Scheduler.After(secs, () => Expire(g));
        }

        private void Expire(long gen)
        {
            lock(sync)
            {
                if(gen != generation) { return; }
                isr |= 1;
                if((control & 2) != 0 && load != 0)
                {
                    counterAtBase = load;
                    timeBase = BoardClock.Seconds;
                    Schedule();
                }
                // single-shot: il contatore resta a zero (Counter() restituisce 0)
                UpdateIrq();
            }
        }

        private void UpdateIrq()
        {
            IRQ.Set((isr & 1) != 0 && (control & 4) != 0);
        }

        private readonly object sync = new object();
        private readonly double frequency;
        private uint load, control, isr, counterAtBase;
        private double timeBase;
        private long generation;
    }

    // ------------------------------------------------------------------
    // UART del PS (Cadence, AMD UG585 cap. 19 e App. B), con FIFO da 64 byte
    // che si svuota al baud rate reale: UART_REF_CLK / (CD * (BDIV + 1)).
    // ------------------------------------------------------------------
    public class ZynqUart : IDoubleWordPeripheral, IKnownSize
    {
        public ZynqUart(IMachine machine, string uiName = "uart1", double refClock = 100000000)
        {
            this.uiName = uiName;
            this.refClock = refClock;
            IRQ = new GPIO();
            flushTimer = new Timer(_ => Tick(), null, 10, 10);
            Reset();
        }

        public GPIO IRQ { get; private set; }
        public long Size { get { return 0x1000; } }

        public void Reset()
        {
            lock(sync)
            {
                cr = 0x128; mr = 0; imr = 0; isr = 0;
                baudgen = 0x28B; bauddiv = 0xF; rxtout = 0; rxwm = 0x20; txwm = 0x20;
                modemcr = 0; flowdel = 0;
                tx.Clear(); rx.Clear();
                shiftStart = -1;
                UpdateIrq();
            }
        }

        // byte dal PC (Serial Monitor) verso la scheda, in base64
        public void HostSend(string base64)
        {
            byte[] data;
            try { data = Convert.FromBase64String(base64); } catch(FormatException) { return; }
            lock(sync)
            {
                foreach(var b in data) { ReceiveByte(b); }
                UpdateIrq();
            }
        }

        public uint ReadDoubleWord(long offset)
        {
            var id = ZyboBus.Access(CpuClock.PsReadSeconds);
            lock(sync)
            {
                Advance();
                if(offset == 0x2C && BoardClock.Mode == ClockMode.Cpu && tx.Count >= 64 && TxEnabled && shiftStart >= 0)
                {
                    // xil_printf aspetta con la FIFO piena (TXFULL): al secondo giro di attesa
                    // il tempo salta a quando il carattere in uscita e' finito di trasmettere
                    if(id == lastFullStatusId + 1)
                    {
                        CpuClock.AdvanceTo(shiftStart + CharTime() * (1 + 1e-9));
                        Advance();
                    }
                    lastFullStatusId = id;
                }
                uint v = 0;
                switch(offset)
                {
                case 0x00: v = cr; break;
                case 0x04: v = mr; break;
                case 0x10: v = imr; break;
                case 0x14: v = isr; break;
                case 0x18: v = baudgen; break;
                case 0x1C: v = rxtout; break;
                case 0x20: v = rxwm; break;
                case 0x24: v = modemcr; break;
                case 0x28: v = 0x10; break;            // DSR... non usati: CTS attivo
                case 0x2C: v = Status(); break;
                case 0x30:
                    if(rx.Count > 0)
                    {
                        v = rx.Dequeue();
                        if(rx.Count == 0) { isr |= 1u << 1; }   // REMPTY
                    }
                    break;
                case 0x34: v = bauddiv; break;
                case 0x38: v = flowdel; break;
                case 0x44: v = txwm; break;
                }
                UpdateIrq();
                return v;
            }
        }

        public void WriteDoubleWord(long offset, uint value)
        {
            ZyboBus.Access(CpuClock.PsWriteSeconds);
            lock(sync)
            {
                Advance();
                switch(offset)
                {
                case 0x00:
                    if((value & 1) != 0) { rx.Clear(); }                              // RXRST
                    if((value & 2) != 0) { tx.Clear(); shiftStart = -1; }             // TXRST
                    cr = value & 0x1FC;                                                // RST autoazzeranti
                    if(TxEnabled && tx.Count > 0 && shiftStart < 0) { shiftStart = BoardClock.Seconds; }
                    break;
                case 0x04: mr = value & 0x3FF; break;
                case 0x08: imr |= value & 0x1FFF; break;      // IER
                case 0x0C: imr &= ~(value & 0x1FFF); break;   // IDR
                case 0x14: isr &= ~value; break;              // W1C
                case 0x18: baudgen = value & 0xFFFF; break;
                case 0x1C: rxtout = value & 0xFF; break;
                case 0x20: rxwm = value & 0x3F; break;
                case 0x24: modemcr = value; break;
                case 0x30: WriteFifo((byte)value); break;
                case 0x34: bauddiv = value & 0xFF; break;
                case 0x38: flowdel = value; break;
                case 0x44: txwm = value & 0x3F; break;
                }
                UpdateIrq();
            }
        }

        // ---- interni ----
        private bool TxEnabled { get { return (cr & (1u << 4)) != 0 && (cr & (1u << 5)) == 0; } }
        private bool RxEnabled { get { return (cr & (1u << 2)) != 0 && (cr & (1u << 3)) == 0; } }
        private int Mode { get { return (int)((mr >> 8) & 3); } }   // 0 normale, 1 eco, 2 loopback locale

        private double CharTime()
        {
            double cd = baudgen == 0 ? 1 : baudgen;
            double baud = refClock / (cd * (bauddiv + 1));
            int chrl = (int)((mr >> 1) & 3);
            int bits = 1 + (chrl == 2 ? 7 : chrl == 3 ? 6 : 8);
            if(((mr >> 3) & 4) == 0) { bits += 1; }               // parita'
            int nb = (int)((mr >> 6) & 3);
            bits += nb == 0 ? 1 : 2;
            return bits / baud;
        }

        private void WriteFifo(byte b)
        {
            if(Mode == 2)
            {
                ReceiveByte(b);     // loopback locale: TX rientra in RX, nulla esce
                return;
            }
            if(tx.Count >= 64)
            {
                isr |= 1u << 12;    // TOVR: il byte e' perso
                return;
            }
            tx.Enqueue(b);
            if(TxEnabled && shiftStart < 0) { shiftStart = BoardClock.Seconds; }
            if(tx.Count >= txwm) { isr |= 1u << 10; }
            if(tx.Count >= 64) { isr |= 1u << 4; }
        }

        private void ReceiveByte(byte b)
        {
            if(!RxEnabled) { return; }
            if(rx.Count >= 64) { isr |= 1u << 5; return; }      // ROVR
            rx.Enqueue(b);
            if(rx.Count >= rxwm && rxwm > 0) { isr |= 1u; }       // RTRIG
            if(rx.Count >= 64) { isr |= 1u << 2; }
            if(rxtout > 0)
            {
                var gen = ++rxGen;
                Scheduler.After(rxtout * 4 * CharTime() / 10.0, () => { lock(sync) { if(gen == rxGen && rx.Count > 0) { isr |= 1u << 8; UpdateIrq(); } } });
            }
            if(Mode == 1) { tx.Enqueue(b); if(shiftStart < 0) { shiftStart = BoardClock.Seconds; } }   // eco automatico
        }

        // fa uscire i caratteri la cui trasmissione e' terminata, in base all'orologio della scheda
        private void Advance()
        {
            if(shiftStart < 0 || !TxEnabled) { return; }
            double now = BoardClock.Seconds;
            double ct = CharTime();
            int done = (int)Math.Floor((now - shiftStart) / ct);
            if(done <= 0) { return; }
            bool wasFull = tx.Count >= 64;
            for(int i = 0; i < done && tx.Count > 0; i++)
            {
                outBuf.Add(tx.Dequeue());
            }
            if(tx.Count == 0)
            {
                shiftStart = -1;
                isr |= 1u << 3;                                    // TEMPTY
            }
            else
            {
                shiftStart += done * ct;
            }
            if(wasFull && tx.Count < 64) { isr |= 1u << 11; }      // TNFUL
        }

        private uint Status()
        {
            uint s = 0;
            if(rx.Count >= rxwm && rxwm > 0) { s |= 1u; }
            if(rx.Count == 0) { s |= 1u << 1; }
            if(rx.Count >= 64) { s |= 1u << 2; }
            if(tx.Count == 0) { s |= 1u << 3; }
            if(tx.Count >= 64) { s |= 1u << 4; }
            if(tx.Count > 0) { s |= 1u << 11; }                    // TACTIVE
            if(tx.Count >= txwm) { s |= 1u << 13; }
            if(tx.Count >= 63) { s |= 1u << 14; }                  // TNFUL: un solo posto libero
            return s;
        }

        private void UpdateIrq()
        {
            IRQ.Set((isr & imr) != 0);
        }

        private void Tick()
        {
            byte[] data = null;
            lock(sync)
            {
                Advance();
                UpdateIrq();
                if(outBuf.Count > 0)
                {
                    data = outBuf.ToArray();
                    outBuf.Clear();
                }
            }
            if(data != null)
            {
                ZyboUi.Send("{\"t\":\"uart\",\"dev\":\"" + uiName + "\",\"b64\":\"" + Convert.ToBase64String(data) + "\"}");
            }
        }

        private readonly object sync = new object();
        private readonly string uiName;
        private readonly double refClock;
        private readonly Timer flushTimer;
        private readonly Queue<byte> tx = new Queue<byte>();
        private readonly Queue<byte> rx = new Queue<byte>();
        private readonly List<byte> outBuf = new List<byte>();
        private uint cr, mr, imr, isr, baudgen, bauddiv, rxtout, rxwm, txwm, modemcr, flowdel;
        private double shiftStart;
        private long rxGen;
        private long lastFullStatusId = -10;
    }

    // ------------------------------------------------------------------
    // AXI GPIO v2.0 (PG144), canale 1 (+ canale 2 se dual)
    // ------------------------------------------------------------------
    public class AxiGpio : IDoubleWordPeripheral, IKnownSize
    {
        public AxiGpio(IMachine machine, string uiName = "gpio", int width = 4,
                       bool allInputs = false, bool allOutputs = false, bool isDual = false,
                       bool interruptPresent = false)
        {
            this.machine = machine;
            this.uiName = uiName;
            this.width = width;
            this.mask = width >= 32 ? 0xFFFFFFFFu : ((1u << width) - 1);
            this.allInputs = allInputs;
            this.allOutputs = allOutputs;
            this.isDual = isDual;
            this.interruptPresent = interruptPresent;
            IRQ = new GPIO();
            statsTimer = new Timer(_ => SendStats(), null, 250, 250);
            Reset();
        }

        public GPIO IRQ { get; private set; }
        public long Size { get { return 0x200; } }

        public void Reset()
        {
            lock(sync)
            {
                dataOut = 0;
                tri = 0xFFFFFFFF;      // C_TRI_DEFAULT: tutti ingressi
                dataOut2 = 0;
                tri2 = 0xFFFFFFFF;
                gier = false;
                ipier = 0;
                ipisr = 0;
                UpdateIrq();
            }
            SendOutput();
        }

        // -------- accessi del processore --------
        public uint ReadDoubleWord(long offset)
        {
            ZyboBus.Access(CpuClock.PlReadSeconds);
            uint v = 0;
            lock(sync)
            {
                switch(offset)
                {
                case 0x000: v = ReadData(dataOut, tri, inputs); reads0++; break;
                case 0x004: v = allInputs ? mask : (allOutputs ? 0 : (tri & mask)); break;
                case 0x008: v = isDual ? ReadData(dataOut2, tri2, inputs2) : 0; break;
                case 0x00C: v = isDual ? (tri2 & mask) : 0; break;
                case 0x11C: v = gier ? 0x80000000u : 0; break;
                case 0x120: v = ipisr; break;
                case 0x128: v = ipier; break;
                default: v = 0; break;
                }
            }
            return v;
        }

        public void WriteDoubleWord(long offset, uint value)
        {
            ZyboBus.Access(CpuClock.PlWriteSeconds);
            bool outChanged = false;
            lock(sync)
            {
                switch(offset)
                {
                case 0x000:
                    writes0++;
                    var old = dataOut;
                    dataOut = value & mask;
                    outChanged = old != dataOut;
                    break;
                case 0x004:
                    if(!allInputs && !allOutputs)
                    {
                        var oldT = tri;
                        tri = value & mask;
                        outChanged = oldT != tri;
                    }
                    break;
                case 0x008: if(isDual) { dataOut2 = value & mask; } break;
                case 0x00C: if(isDual) { tri2 = value & mask; } break;
                case 0x11C: gier = (value & 0x80000000u) != 0; break;
                case 0x120:
                    // IP ISR: "toggle on write" -- scrivere 1 inverte il bit (PG144)
                    ipisr ^= (value & (isDual ? 3u : 1u));
                    break;
                case 0x128: ipier = value & (isDual ? 3u : 1u); break;
                default: break;
                }
                UpdateIrq();
            }
            if(outChanged)
            {
                SendOutput();
            }
        }

        // -------- lato pin: comandi dall'interfaccia (Monitor) --------
        // Imposta il livello elettrico di un ingresso; bounce = numero di rimbalzi,
        // bounceMicros = durata del rimbalzo in tempo virtuale.
        public void Press(int bit, bool level, int bounce, int bounceMicros)
        {
            if(bounce <= 0 || bounceMicros <= 0)
            {
                SetPin(bit, level);
                return;
            }
            // sequenza di fronti alternati (numero dispari: termina sul livello richiesto),
            // distribuiti in modo irregolare nella durata del rimbalzo, in tempo-scheda
            var rnd = new Random(Environment.TickCount ^ (bit * 7919));
            int edges = 2 * bounce + 1;
            double t = 0;
            for(int i = 0; i < edges; i++)
            {
                bool thisLevel = (i % 2 == 0) ? level : !level;
                if(i == 0)
                {
                    SetPin(bit, thisLevel);
                    continue;
                }
                t += Math.Max(20e-6, (bounceMicros * 1e-6 / edges) * (0.4 + 1.2 * rnd.NextDouble()));
                var captured = thisLevel;
                Scheduler.After(t, () => SetPin(bit, captured));
            }
        }

        public void SetPin(int bit, bool level)
        {
            bool changed;
            lock(sync)
            {
                var old = inputs;
                if(level) { inputs |= (1u << bit); } else { inputs &= ~(1u << bit); }
                inputs &= mask;
                changed = old != inputs;
                if(changed)
                {
                    // il rilevatore di fronti dell'IP vede ogni variazione degli ingressi del canale 1
                    ipisr |= 1u;
                    edgeCount++;
                    UpdateIrq();
                }
            }
            if(changed)
            {
                ZyboUi.Send("{\"t\":\"in\",\"dev\":\"" + uiName + "\",\"v\":" + inputs + ",\"vt\":" + VirtualSeconds() + "}");
            }
        }

        public void SetInputs(uint value)
        {
            for(int i = 0; i < width; i++)
            {
                SetPin(i, ((value >> i) & 1) != 0);
            }
        }

        public uint Inputs { get { return inputs; } }
        public uint Outputs { get { return dataOut; } }

        // -------- interni --------
        private uint ReadData(uint outReg, uint triReg, uint inReg)
        {
            if(allOutputs) { return outReg & mask; }
            if(allInputs) { return inReg & mask; }
            // bit a 1 in TRI = ingresso: si legge il pin; a 0 = uscita: si legge il registro
            return ((inReg & triReg) | (outReg & ~triReg)) & mask;
        }

        private void UpdateIrq()
        {
            bool level = interruptPresent && gier && ((ipisr & ipier) != 0);
            if(level != irqLevel)
            {
                irqLevel = level;
                IRQ.Set(level);
                ZyboUi.Send("{\"t\":\"irq\",\"dev\":\"" + uiName + "\",\"v\":" + (level ? 1 : 0) + ",\"vt\":" + VirtualSeconds() + "}");
            }
        }

        private void SendOutput()
        {
            uint visible;
            lock(sync)
            {
                // un LED e' acceso se il pin e' un'uscita (TRI=0) e il dato e' 1
                visible = allOutputs ? dataOut : (dataOut & ~tri & mask);
            }
            ZyboUi.Send("{\"t\":\"out\",\"dev\":\"" + uiName + "\",\"v\":" + visible + ",\"vt\":" + VirtualSeconds() + "}");
        }

        private void SendStats()
        {
            long r, w, e;
            lock(sync) { r = reads0; w = writes0; e = edgeCount; }
            // inviato sempre: fa anche da "battito" con il tempo virtuale corrente
            ZyboUi.Send("{\"t\":\"stats\",\"dev\":\"" + uiName + "\",\"reads\":" + r + ",\"writes\":" + w + ",\"edges\":" + e + ",\"vt\":" + VirtualSeconds() + "}");
        }

        private string VirtualSeconds()
        {
            return ZyboUi.Num(BoardClock.Seconds);
        }

        private readonly IMachine machine;
        private readonly string uiName;
        private readonly int width;
        private readonly uint mask;
        private readonly bool allInputs, allOutputs, isDual, interruptPresent;
        private readonly object sync = new object();
        private readonly Timer statsTimer;
        private uint dataOut, tri, dataOut2, tri2, inputs, inputs2, ipier, ipisr;
        private bool gier, irqLevel;
        private long reads0, writes0, edgeCount;
    }

    // ------------------------------------------------------------------
    // Sonda su un pin GPIO (es. MIO7 -> LD4)
    // ------------------------------------------------------------------
    public class PinProbe : IGPIOReceiver
    {
        public PinProbe(IMachine machine, string uiName = "pin")
        {
            this.machine = machine;
            this.uiName = uiName;
        }

        public void OnGPIO(int number, bool value)
        {
            ZyboUi.Send("{\"t\":\"pin\",\"dev\":\"" + uiName + "\",\"n\":" + number + ",\"v\":" + (value ? 1 : 0) + "}");
        }

        public void Reset() { }

        private readonly IMachine machine;
        private readonly string uiName;
    }

    // ------------------------------------------------------------------
    // MPU-6050 su modulo GY-521 (I2C, indirizzo 0x68)
    // ------------------------------------------------------------------
    public class Mpu6050 : II2CPeripheral
    {
        public Mpu6050(IMachine machine)
        {
            this.machine = machine;
            WhoAmI = 0x72;            // il modulo del laboratorio risponde 0x72 (cap. 7.10.1)
            TemperatureC = 28.5;
            NoiseScale = 1.0;
            Mounting = "jd";          // GY-521 perpendicolare alla scheda, su Pmod JD
            Reset();
        }

        // ---- grandezze fisiche impostate dall'interfaccia ----
        public double PitchDeg { get; set; }
        public double RollDeg { get; set; }
        public double TemperatureC { get; set; }
        public double NoiseScale { get; set; }
        public byte WhoAmI { get; set; }
        public string Mounting { get; set; }

        public void SetTilt(double pitchDeg, double rollDeg)
        {
            PitchDeg = pitchDeg;
            RollDeg = rollDeg;
        }

        // un colpetto: per 'millis' ms si somma un'accelerazione impulsiva
        public void Tap(double g, int millis)
        {
            tapUntil = Now() + millis / 1000.0;
            tapG = g;
        }

        public void Reset()
        {
            lock(sync)
            {
                Array.Clear(regs, 0, regs.Length);
                regs[0x6B] = 0x40;    // PWR_MGMT_1: SLEEP=1 all'accensione
                pointer = 0;
                lastSampleTime = -1;
                tapUntil = 0;         // l'orologio della scheda riparte da zero: nessun colpetto in sospeso
            }
        }

        // ---- protocollo I2C ----
        public void Write(byte[] data)
        {
            if(data.Length == 0) { return; }
            lock(sync)
            {
                pointer = data[0];
                for(int i = 1; i < data.Length; i++)
                {
                    WriteReg(pointer, data[i]);
                    pointer = (byte)((pointer + 1) & 0x7F);
                }
            }
            if(data.Length == 1)
            {
                Ui("{\"t\":\"i2c\",\"op\":\"W\",\"reg\":" + data[0] + ",\"n\":0}");
            }
            else
            {
                Ui("{\"t\":\"i2c\",\"op\":\"W\",\"reg\":" + data[0] + ",\"n\":" + (data.Length - 1) + ",\"val\":" + data[1] + "}");
            }
        }

        public byte[] Read(int count = 1)
        {
            var result = new byte[count];
            byte start;
            lock(sync)
            {
                start = pointer;
                if(pointer >= 0x3B && pointer <= 0x48)
                {
                    UpdateSample();
                }
                for(int i = 0; i < count; i++)
                {
                    result[i] = ReadReg(pointer);
                    pointer = (byte)((pointer + 1) & 0x7F);
                }
            }
            Ui("{\"t\":\"i2c\",\"op\":\"R\",\"reg\":" + start + ",\"n\":" + count + "}");
            return result;
        }

        public void FinishTransmission() { }

        // ---- registri ----
        private void WriteReg(byte reg, byte val)
        {
            if(reg == 0x6B && (val & 0x80) != 0)
            {
                // DEVICE_RESET
                Array.Clear(regs, 0, regs.Length);
                regs[0x6B] = 0x40;
                return;
            }
            if(reg == 0x75 || (reg >= 0x3B && reg <= 0x60))
            {
                return;   // registri in sola lettura
            }
            regs[reg] = val;
        }

        private byte ReadReg(byte reg)
        {
            if(reg == 0x75) { return WhoAmI; }
            return regs[reg];
        }

        private bool Sleeping { get { return (regs[0x6B] & 0x40) != 0; } }

        private void UpdateSample()
        {
            if(Sleeping)
            {
                return;   // convertitori fermi: i registri di uscita tengono l'ultimo valore (0 dopo il reset)
            }
            double now = Now();
            // frequenza di uscita dell'accelerometro: 1 kHz
            if(lastSampleTime >= 0 && now - lastSampleTime < 0.001)
            {
                return;
            }
            lastSampleTime = now;

            // gravita' (reazione, +1 g verso l'alto) negli assi della scheda
            double th = PitchDeg * Math.PI / 180.0, ph = RollDeg * Math.PI / 180.0;
            double bx = -Math.Sin(th);
            double by = Math.Sin(ph) * Math.Cos(th);
            double bz = Math.Cos(ph) * Math.Cos(th);
            if(now < tapUntil)
            {
                bx += tapG * (rnd.NextDouble() - 0.5) * 2;
                by += tapG * (rnd.NextDouble() - 0.5) * 2;
                bz += tapG * (rnd.NextDouble() - 0.5) * 2;
            }

            // dagli assi della scheda agli assi del sensore
            double sx, sy, sz;
            if(Mounting == "flat")
            {
                sx = bx; sy = by; sz = bz;
            }
            else
            {
                // GY-521 in piedi sul Pmod JD: l'asse X del sensore e' la verticale della scheda
                sx = bz; sy = bx; sz = by;
            }

            // offset di zero-g (asimmetrie del package), rumore secondo la banda del DLPF
            sx += 0.018; sy += -0.027; sz += 0.045;
            double sigma = NoiseRmsG() * NoiseScale;
            sx += Gauss() * sigma; sy += Gauss() * sigma; sz += Gauss() * sigma;

            int afs = (regs[0x1C] >> 3) & 3;
            double lsbPerG = 16384.0 / (1 << afs);
            Put16(0x3B, sx * lsbPerG);
            Put16(0x3D, sy * lsbPerG);
            Put16(0x3F, sz * lsbPerG);

            double t = TemperatureC + Gauss() * 0.05;
            Put16(0x41, (t - 36.53) * 340.0);

            int fs = (regs[0x1B] >> 3) & 3;
            double lsbPerDps = 131.0 / (1 << fs);
            Put16(0x43, (-0.8 + Gauss() * 0.05) * lsbPerDps);
            Put16(0x45, (0.5 + Gauss() * 0.05) * lsbPerDps);
            Put16(0x47, (0.3 + Gauss() * 0.05) * lsbPerDps);
        }

        private double NoiseRmsG()
        {
            // densita' di rumore 400 ug/sqrt(Hz), banda dal campo DLPF_CFG del registro CONFIG
            int[] bw = { 260, 184, 94, 44, 21, 10, 5, 260 };
            int cfg = regs[0x1A] & 7;
            return 400e-6 * Math.Sqrt(bw[cfg] * 1.57);
        }

        private void Put16(int reg, double value)
        {
            int v = (int)Math.Round(value);
            if(v > 32767) { v = 32767; }
            if(v < -32768) { v = -32768; }
            regs[reg] = (byte)((v >> 8) & 0xFF);
            regs[reg + 1] = (byte)(v & 0xFF);
        }

        private double Gauss()
        {
            double u1 = 1.0 - rnd.NextDouble(), u2 = rnd.NextDouble();
            return Math.Sqrt(-2.0 * Math.Log(u1)) * Math.Cos(2 * Math.PI * u2);
        }

        private double Now()
        {
            return BoardClock.Seconds;
        }

        private void Ui(string json)
        {
            ZyboUi.Send(json);
        }

        private readonly IMachine machine;
        private readonly object sync = new object();
        private readonly byte[] regs = new byte[128];
        private readonly Random rnd = new Random();
        private byte pointer;
        private double lastSampleTime;
        private double tapUntil, tapG;
    }

    // ==================================================================
    // Percorso audio del bitstream del corso (Lab 3, cap. 8 delle dispense):
    //
    //   LINE IN -> SSM2603 (ADC) --I2S--> axi_i2s_adi --AXI4-Stream--> AXI4-Stream FIFO RX (512 parole) -> CPU
    //   CPU -> AXI4-Stream FIFO TX (512 parole) --AXI4-Stream--> axi_i2s_adi --I2S--> SSM2603 (DAC) -> cuffie
    //
    // Il codec si configura su I2C0 (indirizzo 0x1A), i divisori dei clock sono nell'axi_i2s_adi
    // (0x43C1_0000), i campioni passano dalla FIFO (Xilinx PG080, 0x43C0_0000).
    // Il flusso segue l'orologio della scheda: una parola (L, poi R) ogni 1/(2 fs), sempre.
    // Riferimenti: Analog Devices SSM2603 data sheet rev. C; ADI HDL axi_i2s_adi;
    // AMD PG080 AXI4-Stream FIFO v4.3.
    // ==================================================================
    public static class AudioPath
    {
        public const int RxDepth = 512;
        public const int TxDepth = 512;
        public const double Mclk = 12288000.0;          // master clock del codec, dalla PL
        private const double OccStep = 0.0005;           // telemetria: occupazione della FIFO ogni 0,5 ms
        private const int UiRate = 24000;                // audio verso l'interfaccia: mono, 24 kHz

        public static readonly object Sync = new object();

        static AudioPath()
        {
            ResetCodecLocked();
            uiTimer = new Timer(_ => Flush(), null, 50, 50);
        }

        // ---------------- comandi dall'interfaccia ----------------
        public static void SetInput(string type, double freq, double levelDb)
        {
            lock(Sync)
            {
                Advance();
                inType = type ?? "sine";
                inFreq = Math.Max(1, Math.Min(23000, freq));
                inLevelDb = Math.Max(-90, Math.Min(0, levelDb));
            }
        }

        public static void Reset()
        {
            lock(Sync)
            {
                streaming = false; rxEn = false; txEn = false; clkCtrl = 0;
                rxHead = rxCount = 0; txHead = txCount = 0; txPending.Clear(); txStarted = false;
                lostWords = underWords = 0; isr = 0; ier = 0;
                pcm.Clear(); occ.Clear(); occBin = -1; halfPending = false;
                ResetCodecLocked();
                resetPending = true;
            }
        }

        // ---------------- controllore I2S (axi_i2s_adi) ----------------
        public static uint ClockControl { get { lock(Sync) { return clkCtrl; } } }
        public static uint Control { get { lock(Sync) { return (txEn ? 1u : 0) | (rxEn ? 2u : 0); } } }

        public static void SetClockControl(uint v)
        {
            lock(Sync)
            {
                Advance();
                clkCtrl = v & 0x00FF00FFu;
                if(streaming) { streamT0 = BoardClock.Seconds - wordsDone / WordRate; }
            }
        }

        public static void SetEnable(bool tx, bool rx)
        {
            lock(Sync)
            {
                Advance();
                bool was = streaming;
                txEn = tx; rxEn = rx;
                streaming = tx || rx;
                if(streaming && !was)
                {
                    streamT0 = BoardClock.Seconds;
                    wordsDone = 0;
                    occBin = -1;
                }
            }
        }

        // frequenza di campionamento: LRCLK = MCLK / ((BCLK_DIV + 1) * 2) / ((LRCLK_DIV + 1) * 2)
        public static double Fs
        {
            get
            {
                int b = (int)(clkCtrl & 0xFF), l = (int)((clkCtrl >> 16) & 0xFF);
                return Mclk / ((b + 1) * 2.0) / ((l + 1) * 2.0);
            }
        }

        private static double WordRate { get { return 2 * Fs; } }

        // istante in cui arriva (e in cui il DAC consuma) la prossima parola
        private static double NextWordTime { get { return streamT0 + (wordsDone + 1) / WordRate + 1e-12; } }

        // ---------------- AXI4-Stream FIFO (PG080) ----------------
        public static uint FifoRead(long offset, long id)
        {
            lock(Sync)
            {
                Advance();
                switch(offset)
                {
                case 0x00: return isr;
                case 0x04: return ier;
                case 0x0C:                                       // TDFV: posti liberi nella FIFO TX
                {
                    uint v = (uint)Math.Max(0, TxDepth - txCount - txPending.Count);
                    if(v == 0 && streaming && txEn && BoardClock.Mode == ClockMode.Cpu)
                    {
                        if(id == lastTdfvZeroId + 1)
                        {
                            CpuClock.AdvanceTo(NextWordTime);    // ciclo di attesa: salta a quando il DAC prende una parola
                            Advance();
                            v = (uint)Math.Max(0, TxDepth - txCount - txPending.Count);
                        }
                        lastTdfvZeroId = id;
                    }
                    return v;
                }
                case 0x1C:                                       // RDFO: parole nella FIFO RX
                {
                    if(rxCount == 0 && streaming && rxEn && BoardClock.Mode == ClockMode.Cpu)
                    {
                        if(id == lastRdfoZeroId + 1)
                        {
                            CpuClock.AdvanceTo(NextWordTime);    // ciclo di attesa: salta all'arrivo della prossima parola
                            Advance();
                        }
                        if(rxCount == 0) { lastRdfoZeroId = id; }
                    }
                    return (uint)rxCount;
                }
                case 0x20:                                       // RDFD: estrae una parola
                    if(rxCount == 0)
                    {
                        isr |= 1u << 29;                         // RPUE: lettura con FIFO vuota
                        return 0;
                    }
                    var w = rx[rxHead];
                    rxHead = (rxHead + 1) % RxDepth;
                    rxCount--;
                    return w;
                case 0x24: return (uint)rxCount * 4;            // RLR, in byte
                case 0x2C: return tdr;
                case 0x30: return 0;                             // RDR
                default: return 0;
                }
            }
        }

        public static void FifoWrite(long offset, uint value)
        {
            lock(Sync)
            {
                Advance();
                switch(offset)
                {
                case 0x00: isr &= ~value; break;                 // W1C
                case 0x04: ier = value; break;
                case 0x08:                                       // TDFR
                    if(value == 0xA5) { ResetTx(); isr |= 1u << 24; }
                    break;
                case 0x10:                                       // TDFD
                    if(txCount + txPending.Count >= TxDepth) { isr |= 1u << 28; }   // TPOE: FIFO piena, parola persa
                    else { txPending.Add(value); }
                    break;
                case 0x14:                                       // TLR: chiude il pacchetto, che parte verso l'I2S
                    foreach(var p in txPending)
                    {
                        tx[(txHead + txCount) % TxDepth] = p;
                        txCount++;
                    }
                    txPending.Clear();
                    txStarted = true;
                    isr |= 1u << 27;                             // TC
                    break;
                case 0x18:                                       // RDFR
                    if(value == 0xA5) { rxHead = rxCount = 0; isr |= 1u << 23; }
                    break;
                case 0x28:                                       // SRR
                    if(value == 0xA5) { ResetTx(); rxHead = rxCount = 0; isr |= (1u << 23) | (1u << 24); }
                    break;
                case 0x2C: tdr = value; break;
                }
            }
        }

        private static void ResetTx()
        {
            txHead = txCount = 0;
            txPending.Clear();
        }

        // ---------------- codec SSM2603 ----------------
        public static void CodecWrite(int reg, int val)
        {
            lock(Sync)
            {
                Advance();
                val &= 0x1FF;
                if(reg == 15) { ResetCodecLocked(); }
                else if(reg >= 0 && reg < codec.Length)
                {
                    codec[reg] = val;
                    if(reg == 0 && (val & 0x100) != 0) { codec[1] = val; }   // LRINBOTH
                    if(reg == 1 && (val & 0x100) != 0) { codec[0] = val; }   // RLINBOTH
                    if(reg == 2 && (val & 0x100) != 0) { codec[3] = val; }   // LRHPBOTH
                    if(reg == 3 && (val & 0x100) != 0) { codec[2] = val; }   // RLHPBOTH
                }
                codecDirty = true;
            }
        }

        public static int CodecRead(int reg)
        {
            lock(Sync) { return reg >= 0 && reg < codec.Length ? codec[reg] : 0; }
        }

        private static void ResetCodecLocked()
        {
            // valori dopo il reset (data sheet SSM2603, tabella dei registri)
            int[] d = { 0x097, 0x097, 0x079, 0x079, 0x00A, 0x008, 0x09F, 0x00A, 0x000, 0x000 };
            for(int i = 0; i < codec.Length; i++) { codec[i] = i < d.Length ? d[i] : 0; }
            codecDirty = true;
        }

        private static bool Active { get { return (codec[9] & 1) != 0; } }
        private static bool PoweredOff { get { return (codec[6] & 0x80) != 0; } }

        private static bool InputPath
        {
            // line in acceso, ADC acceso, ingresso = line (INSEL = 0), codec attivo
            get { return Active && !PoweredOff && (codec[6] & 0x01) == 0 && (codec[6] & 0x04) == 0 && (codec[4] & 0x04) == 0; }
        }

        private static bool OutputPath
        {
            // DAC e uscita accesi, DAC verso l'uscita (DACSEL), DAC non silenziato (DACMU)
            get { return Active && !PoweredOff && (codec[6] & 0x08) == 0 && (codec[6] & 0x10) == 0 && (codec[4] & 0x10) != 0 && (codec[5] & 0x08) == 0; }
        }

        private static double LineGain(int reg)
        {
            int v = codec[reg];
            if((v & 0x80) != 0) { return 0; }                    // LINMUTE
            return Math.Pow(10, ((v & 0x3F) - 0x17) * 1.5 / 20); // 0x17 = 0 dB, passi di 1,5 dB
        }

        private static double HpGain(int reg)
        {
            int v = codec[reg] & 0x7F;
            if(v <= 0x2F) { return 0; }                          // muto
            return Math.Pow(10, (v - 0x79) / 20.0);              // 0x79 = 0 dB, passi di 1 dB
        }

        // ---------------- il flusso ----------------
        private static void Advance()
        {
            if(!streaming) { return; }
            double now = BoardClock.Seconds;
            double wr = WordRate;
            long target = (long)Math.Floor((now - streamT0) * wr);
            if(target <= wordsDone) { return; }
            bool inOn = InputPath, outOn = OutputPath;
            double gl = LineGain(0), gr = LineGain(1), hl = HpGain(2), hr = HpGain(3);
            bool bypass = Active && (codec[4] & 0x08) != 0;
            while(wordsDone < target)
            {
                long w = wordsDone;
                bool left = (w & 1) == 0;
                if(left) { GenerateInput(); }

                // codec -> FIFO RX
                if(rxEn)
                {
                    double x = inOn ? (left ? inL * gl : inR * gr) + Noise() : 0;
                    uint word = Quantize(x);
                    if(rxCount < RxDepth)
                    {
                        rx[(rxHead + rxCount) % RxDepth] = word;
                        rxCount++;
                    }
                    else
                    {
                        lostWords++;                             // overrun: la parola non ha posto ed e' persa
                        binOverflow = true;
                    }
                }

                // FIFO TX -> codec
                double y = 0;
                if(txEn)
                {
                    if(txCount > 0)
                    {
                        y = (int)tx[txHead] / 2147483648.0;
                        txHead = (txHead + 1) % TxDepth;
                        txCount--;
                    }
                    else if(txStarted)
                    {
                        underWords++;                            // underrun: il DAC non ha niente da suonare
                    }
                }
                double o = outOn ? y * (left ? hl : hr) : 0;
                if(bypass) { o += (left ? inL * gl : inR * gr); }
                double tw = streamT0 + (w + 1) / wr;
                if(left) { outL = o; }
                else { EmitOutput((outL + o) / 2, tw); }

                // telemetria: occupazione massima della FIFO RX in ogni intervallo di 0,5 ms
                long bin = (long)Math.Floor(tw / OccStep);
                if(bin != occBin)
                {
                    if(occBin >= 0) { CloseBin(); }
                    occBin = bin;
                    binMax = 0;
                }
                if(rxCount > binMax) { binMax = rxCount; }
                wordsDone++;
            }
        }

        private static void CloseBin()
        {
            if(occ.Count == 0) { occT0 = occBin * OccStep; }
            occ.Add(binOverflow ? -binMax - 1 : binMax);         // negativo: in questo intervallo si sono persi campioni
            binOverflow = false;
        }

        private static void GenerateInput()
        {
            double fs = Fs;
            double a = Math.Pow(10, inLevelDb / 20);
            double v;
            switch(inType)
            {
            case "silence":
                v = 0;
                break;
            case "square":
                phase += inFreq / fs; phase -= Math.Floor(phase);
                v = phase < 0.5 ? a : -a;
                break;
            case "noise":
                v = a * (rnd.NextDouble() * 2 - 1);
                break;
            case "sweep":                                        // da 100 Hz a 10 kHz in 4 s, poi ricomincia
                sweepPos += 1.0 / (4 * fs); if(sweepPos >= 1) { sweepPos -= 1; }
                phase += 100 * Math.Pow(100, sweepPos) / fs; phase -= Math.Floor(phase);
                v = a * Math.Sin(2 * Math.PI * phase);
                break;
            default:
                phase += inFreq / fs; phase -= Math.Floor(phase);
                v = a * Math.Sin(2 * Math.PI * phase);
                break;
            }
            inL = v;
            inR = v;
        }

        private static double Noise()
        {
            return (rnd.NextDouble() - 0.5) * 6e-5;              // rumore del convertitore, circa -95 dBFS
        }

        private static uint Quantize(double x)
        {
            // campione a 24 bit in complemento a 2, allineato a sinistra nella parola a 32 bit
            long s = (long)Math.Round(x * 8388608.0);
            if(s > 8388607) { s = 8388607; }
            if(s < -8388608) { s = -8388608; }
            return (uint)((int)s << 8);
        }

        private static void EmitOutput(double mono, double t)
        {
            // 48 kHz -> 24 kHz per l'interfaccia: media di due campioni
            if(!halfPending) { halfValue = mono; halfPending = true; return; }
            halfPending = false;
            double v = (halfValue + mono) / 2;
            if(pcm.Count == 0) { pcmT0 = t; }
            int s = (int)Math.Round(Math.Max(-1, Math.Min(1, v)) * 32767);
            pcm.Add((short)s);
        }

        private static void Flush()
        {
            string json = null;
            string codecJson = null;
            bool reset = false;
            lock(Sync)
            {
                try { Advance(); } catch(Exception) { }
                if(resetPending) { reset = true; resetPending = false; }
                if(codecDirty)
                {
                    codecDirty = false;
                    codecJson = "{\"t\":\"codec\",\"regs\":[" + string.Join(",", codec) + "]}";
                }
                if(pcm.Count > 0 || occ.Count > 0)
                {
                    var bytes = new byte[pcm.Count * 2];
                    for(int i = 0; i < pcm.Count; i++)
                    {
                        bytes[2 * i] = (byte)(pcm[i] & 0xFF);
                        bytes[2 * i + 1] = (byte)((pcm[i] >> 8) & 0xFF);
                    }
                    var sb = new StringBuilder();
                    sb.Append("{\"t\":\"audio\",\"vt\":").Append(ZyboUi.Num(pcmT0));
                    sb.Append(",\"rate\":").Append(UiRate);
                    sb.Append(",\"fs\":").Append(ZyboUi.Num(Fs));
                    sb.Append(",\"pcm\":\"").Append(Convert.ToBase64String(bytes)).Append('"');
                    sb.Append(",\"occT0\":").Append(ZyboUi.Num(occT0));
                    sb.Append(",\"occStep\":").Append(ZyboUi.Num(OccStep));
                    sb.Append(",\"occ\":[").Append(string.Join(",", occ)).Append(']');
                    sb.Append(",\"lost\":").Append(lostWords);
                    sb.Append(",\"under\":").Append(underWords);
                    sb.Append(",\"level\":").Append(rxCount);
                    sb.Append(",\"now\":").Append(ZyboUi.Num(BoardClock.Seconds));
                    sb.Append('}');
                    json = sb.ToString();
                    pcm.Clear();
                    occ.Clear();
                }
            }
            if(reset) { ZyboUi.Send("{\"t\":\"audio_reset\"}"); }
            if(codecJson != null) { ZyboUi.Send(codecJson); }
            if(json != null) { ZyboUi.Send(json); }
        }

        private static readonly Timer uiTimer;
        private static readonly Random rnd = new Random(12345);
        private static readonly int[] codec = new int[19];
        private static readonly uint[] rx = new uint[RxDepth];
        private static readonly uint[] tx = new uint[TxDepth];
        private static readonly List<uint> txPending = new List<uint>();
        private static readonly List<short> pcm = new List<short>();
        private static readonly List<int> occ = new List<int>();
        private static bool streaming, rxEn, txEn, txStarted, codecDirty, resetPending, halfPending, binOverflow;
        private static uint clkCtrl, isr, ier, tdr;
        private static int rxHead, rxCount, txHead, txCount, binMax;
        private static long wordsDone, lostWords, underWords, occBin = -1;
        private static long lastRdfoZeroId = -10, lastTdfvZeroId = -10;
        private static double streamT0, pcmT0, occT0, phase, sweepPos, inL, inR, outL, halfValue;
        private static string inType = "sine";
        private static double inFreq = 1000, inLevelDb = -12;
    }

    // axi_i2s_adi (Analog Devices), registri del controllore: RESET 0x00, CTRL 0x04, CLK_CTRL 0x08
    public class AxiI2sAdi : IDoubleWordPeripheral, IKnownSize
    {
        public AxiI2sAdi(IMachine machine)
        {
            Reset();
        }

        public long Size { get { return 0x10000; } }

        public void Reset()
        {
            AudioPath.Reset();
            periodLen = 0;
        }

        // segnale su LINE IN, dal Monitor:  i2s SetInput "sine" 1000 -12
        public void SetInput(string type, double freq, double levelDb)
        {
            AudioPath.SetInput(type, freq, levelDb);
        }

        public uint ReadDoubleWord(long offset)
        {
            ZyboBus.Access(CpuClock.PlReadSeconds);
            switch(offset)
            {
            case 0x04: return AudioPath.Control;
            case 0x08: return AudioPath.ClockControl;
            case 0x18: return periodLen & 0xFFFF;
            default: return 0;
            }
        }

        public void WriteDoubleWord(long offset, uint value)
        {
            ZyboBus.Access(CpuClock.PlWriteSeconds);
            switch(offset)
            {
            case 0x00: break;                                    // reset delle FIFO interne: nessuno stato da azzerare
            case 0x04: AudioPath.SetEnable((value & 1) != 0, (value & 2) != 0); break;
            case 0x08: AudioPath.SetClockControl(value); break;
            case 0x18: periodLen = value; break;
            }
        }

        private uint periodLen;
    }

    // AXI4-Stream FIFO (AMD PG080), interfaccia AXI4-Lite, FIFO RX e TX da 512 parole
    public class AxiFifoMmS : IDoubleWordPeripheral, IKnownSize
    {
        public AxiFifoMmS(IMachine machine)
        {
        }

        public long Size { get { return 0x10000; } }

        public void Reset() { }

        public uint ReadDoubleWord(long offset)
        {
            var id = ZyboBus.Access(CpuClock.PlReadSeconds);
            return AudioPath.FifoRead(offset, id);
        }

        public void WriteDoubleWord(long offset, uint value)
        {
            ZyboBus.Access(CpuClock.PlWriteSeconds);
            AudioPath.FifoWrite(offset, value);
        }
    }

    // Codec audio SSM2603 su I2C: registri a 9 bit, scrittura = 2 byte
    // (indirizzo del registro nei 7 bit alti del primo byte, bit 8 del dato nel bit 0)
    public class Ssm2603 : II2CPeripheral
    {
        public Ssm2603(IMachine machine)
        {
        }

        public void Write(byte[] data)
        {
            if(data.Length == 0) { return; }
            pointer = data[0] >> 1;
            if(data.Length >= 2)
            {
                int val = ((data[0] & 1) << 8) | data[1];
                AudioPath.CodecWrite(pointer, val);
            }
        }

        public byte[] Read(int count = 1)
        {
            var v = AudioPath.CodecRead(pointer);
            var r = new byte[count];
            if(count > 0) { r[0] = (byte)((v >> 8) & 1); }
            if(count > 1) { r[1] = (byte)(v & 0xFF); }
            return r;
        }

        public void FinishTransmission() { }

        public void Reset() { pointer = 0; }

        private int pointer;
    }
}
