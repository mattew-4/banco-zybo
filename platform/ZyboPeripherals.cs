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
    // Orologio della scheda: tempo reale (Stopwatch) che si ferma in pausa.
    // E' la base dei timer del Cortex-A9 (global timer, timer privato), del
    // rimbalzo dei pulsanti e del campionamento del sensore.
    // ------------------------------------------------------------------
    public static class BoardClock
    {
        public static double Seconds
        {
            get
            {
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
            Scheduler.Wake();
        }

        public static bool Running { get { lock(sync) { return running; } } }

        private static readonly object sync = new object();
        private static readonly System.Diagnostics.Stopwatch watch = new System.Diagnostics.Stopwatch();
        private static double accumulated;
        private static bool running;
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
            Reset();
        }

        public GPIO IRQ { get; private set; }
        public long Size { get { return 0x20; } }

        // Controllo dell'orologio della scheda dal Monitor:
        //   globalTimer ClockPause / globalTimer ClockResume / globalTimer BoardSeconds
        public void ClockPause() { BoardClock.Pause(); }
        public void ClockResume() { BoardClock.Resume(); }
        public double BoardSeconds { get { return BoardClock.Seconds; } }

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
            lock(sync)
            {
                Advance();
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
}
