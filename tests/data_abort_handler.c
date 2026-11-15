/* Prova automatica: Data Abort con un gestore registrato dal programma.
 * Sulla scheda una lettura a un indirizzo della PL senza blocco (DECERR) e' un abort sincrono:
 * il gestore viene eseguito, DataAbortAddr indica l'istruzione, DFSR = 0x008, DFAR = indirizzo. */
#include "xil_io.h"
#include "xil_printf.h"
#include "xil_exception.h"
#include "xpseudo_asm.h"
#include "xreg_cortexa9.h"

extern u32 DataAbortAddr;
static volatile u32 where;

static void on_abort(void *ref)
{
    (void)ref;
    u32 dfsr = mfcp(XREG_CP15_DATA_FAULT_STATUS);
    u32 dfar = mfcp(XREG_CP15_DATA_FAULT_ADDRESS);
    xil_printf("gestore: DFSR 0x%03x DFAR 0x%08x istruzione %s\r\n", dfsr, dfar,
               DataAbortAddr == where ? "giusta" : "SBAGLIATA");
    while (1) ;
}

int main(void)
{
    Xil_ExceptionRegisterHandler(XIL_EXCEPTION_ID_DATA_ABORT_INT, on_abort, NULL);
    u32 pc;
    __asm__ volatile("adr %0, 1f\n\t"            /* indirizzo dell'istruzione che fa l'accesso */
                     "str %0, [%1]\n\t"
                     "1: ldr %0, [%2]"
                     : "=&r"(pc) : "r"(&where), "r"(0x43C30000u) : "memory");
    xil_printf("dopo l'accesso: %x\r\n", (unsigned)pc);
    while (1) ;
}
