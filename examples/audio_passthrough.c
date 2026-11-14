/* Passthrough audio: LINE IN -> cuffie, sul percorso audio del bitstream del corso.
 *
 *   codec SSM2603 su I2C0 (indirizzo 0x1A)         -> configurazione
 *   axi_i2s_adi a 0x43C1_0000                       -> clock I2S (fs = 48 kHz)
 *   AXI4-Stream FIFO (PG080) a 0x43C0_0000          -> parole del codec, alternate sinistra / destra
 *
 * Ogni secondo stampa il picco del canale sinistro in dBFS e quante volte la FIFO RX era piena
 * (512 parole: il codec non aveva dove mettere la parola successiva, che e' andata persa).
 * Scritto dai data sheet (SSM2603 rev. C, PG080, ADI axi_i2s_adi). */
#include <math.h>
#include "xparameters.h"
#include "xil_io.h"
#include "xil_printf.h"
#include "xiicps.h"
#include "xtime_l.h"
#include "sleep.h"

#define FIFO   0x43C00000u
#define I2S    0x43C10000u
#define RDFO   0x1C          /* parole nella FIFO RX           */
#define RDFD   0x20          /* legge una parola               */
#define TDFV   0x0C          /* posti liberi nella FIFO TX     */
#define TDFD   0x10          /* scrive una parola              */
#define TLR    0x14          /* chiude il pacchetto (in byte)  */

static XIicPs i2c;

static int codec_reg(int reg, int val)        /* registri a 9 bit: 7 di indirizzo + 9 di dato */
{
    u8 b[2] = { (u8)((reg << 1) | ((val >> 8) & 1)), (u8)val };
    int r = XIicPs_MasterSendPolled(&i2c, b, 2, 0x1A);
    while (XIicPs_BusIsBusy(&i2c))
        ;
    return r == XST_SUCCESS ? 0 : -1;
}

int main(void)
{
    XIicPs_Config *c = XIicPs_LookupConfig(XPAR_XIICPS_0_DEVICE_ID);
    XIicPs_CfgInitialize(&i2c, c, c->BaseAddress);
    XIicPs_SetSClk(&i2c, 100000);

    int e = codec_reg(15, 0x000);   /* reset                                   */
    usleep(5000);
    e |= codec_reg(6, 0x030);       /* accende tutto tranne uscita e oscillatore */
    e |= codec_reg(0, 0x017);       /* line in sinistro 0 dB                    */
    e |= codec_reg(1, 0x017);       /* line in destro 0 dB                      */
    e |= codec_reg(2, 0x179);       /* cuffie 0 dB, entrambi i canali           */
    e |= codec_reg(4, 0x010);       /* line in -> ADC, DAC -> uscita            */
    e |= codec_reg(5, 0x000);       /* DAC non silenziato                       */
    e |= codec_reg(7, 0x00A);       /* I2S, 24 bit, codec slave                 */
    e |= codec_reg(8, 0x000);       /* 48 kHz da MCLK 12,288 MHz                */
    usleep(50000);
    e |= codec_reg(9, 0x001);       /* attivo                                   */
    e |= codec_reg(6, 0x020);       /* uscita accesa                            */
    if (e) { xil_printf("codec: il codec non risponde su I2C0\r\n"); return 1; }

    Xil_Out32(I2S + 0x08, (31 << 16) | 1);   /* BCLK = MCLK/4, LRCLK = BCLK/64 = 48 kHz */
    Xil_Out32(FIFO + 0x18, 0xA5);            /* svuota la FIFO RX                       */
    Xil_Out32(FIFO + 0x08, 0xA5);            /* svuota la FIFO TX                       */
    Xil_Out32(I2S + 0x04, 0x3);              /* trasmissione e ricezione attive         */
    xil_printf("passthrough attivo: LINE IN -> cuffie\r\n");

    XTime t0, now;
    XTime_GetTime(&t0);
    float peak = 0.0f;
    int full = 0;
    while (1) {
        for (int k = 0; k < 256; k++) {               /* 128 campioni stereo */
            while (Xil_In32(FIFO + RDFO) == 0)
                ;
            u32 w = Xil_In32(FIFO + RDFD);
            if ((k & 1) == 0) {
                float x = fabsf((float)(int32_t)w / 2147483648.0f);
                if (x > peak) peak = x;
            }
            while (Xil_In32(FIFO + TDFV) == 0)
                ;
            Xil_Out32(FIFO + TDFD, w);
            Xil_Out32(FIFO + TLR, 4);
        }
        if (Xil_In32(FIFO + RDFO) >= 512) full++;
        XTime_GetTime(&now);
        if (now - t0 >= COUNTS_PER_SECOND) {
            int db = peak > 1e-6f ? (int)lroundf(20.0f * log10f(peak)) : -120;
            xil_printf("picco %d dBFS  FIFO piena %d volte\r\n", db, full);
            peak = 0.0f;
            full = 0;
            t0 = now;
        }
    }
}
