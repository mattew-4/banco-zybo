/* Prova automatica: codec configurato ma non attivato (R9 = 0), un errore tipico.
 * Sulla scheda l'interfaccia digitale del codec e' ferma: dalla FIFO arrivano solo zeri.
 * Il programma legge 1 s di campioni e stampa il picco. */
#include <math.h>
#include "xparameters.h"
#include "xil_io.h"
#include "xil_printf.h"
#include "xiicps.h"
#include "xtime_l.h"
#include "sleep.h"

static XIicPs i2c;
static void reg(int r, int v)
{
    u8 b[2] = { (u8)((r << 1) | ((v >> 8) & 1)), (u8)v };
    XIicPs_MasterSendPolled(&i2c, b, 2, 0x1A);
    while (XIicPs_BusIsBusy(&i2c)) ;
}

int main(void)
{
    XIicPs_Config *c = XIicPs_LookupConfig(XPAR_XIICPS_0_DEVICE_ID);
    XIicPs_CfgInitialize(&i2c, c, c->BaseAddress);
    XIicPs_SetSClk(&i2c, 100000);
    reg(15, 0); usleep(5000);
    reg(6, 0x030); reg(0, 0x017); reg(1, 0x017); reg(2, 0x179);
    reg(4, 0x010); reg(5, 0x000); reg(7, 0x00A); reg(8, 0x000);
    /* manca: reg(9, 0x001), "active" */
    reg(6, 0x020);
    Xil_Out32(0x43C10008, (31 << 16) | 1);
    Xil_Out32(0x43C00018, 0xA5);
    Xil_Out32(0x43C10004, 0x3);

    float peak = 0;
    for (int i = 0; i < 96000; i++) {
        while (Xil_In32(0x43C0001C) == 0) ;
        float x = fabsf((float)(int32_t)Xil_In32(0x43C00020) / 2147483648.0f);
        if (x > peak) peak = x;
    }
    xil_printf("picco con codec non attivo: %s\r\n", peak == 0.0f ? "zero" : "NON zero");
    while (1) ;
}
