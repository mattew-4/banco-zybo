/* Prova automatica: un accesso a un indirizzo della PL senza blocco.
 * Sulla scheda: DECERR -> Data Abort -> il gestore del BSP si ferma in un ciclo infinito.
 * Il banco deve fermare il programma: "dopo" non deve mai comparire. */
#include "xil_io.h"
#include "xil_printf.h"

int main(void)
{
    xil_printf("prima dell'accesso\r\n");
    volatile u32 v = Xil_In32(0x43C30000);   /* nessun blocco a questo indirizzo nel bitstream del corso */
    xil_printf("dopo l'accesso: %x\r\n", (unsigned)v);
    while (1) ;
}
