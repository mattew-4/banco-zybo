/* Prova automatica: cache dati e contatori di prestazioni (PMU) del Cortex-A9.
 * Scorre array di dimensioni diverse una riga di cache (32 B) alla volta e misura:
 *  - il tempo per accesso con XTime (global timer, 333 MHz);
 *  - i cicli con PMCCNTR (667 MHz): devono essere circa il doppio dei tick di XTime;
 *  - i refill della L1D (evento 0x03) per accesso.
 * Sulla scheda: un array che sta in L1 (32 KB) costa pochi ns per accesso, uno che sta in L2
 * (512 KB) qualche decina, uno piu' grande della L2 di piu'; con la D-cache spenta tutto e' lento. */
#include "xil_printf.h"
#include "xtime_l.h"
#include "xil_cache.h"

#define MAXB (2u << 20)
static volatile unsigned char buf[MAXB];

static inline void pmu_start(void)
{
    __asm__ volatile("mcr p15, 0, %0, c9, c12, 0" :: "r"(0x7));          /* PMCR: E, azzera contatori */
    __asm__ volatile("mcr p15, 0, %0, c9, c12, 5" :: "r"(0));            /* PMSELR: contatore 0 */
    __asm__ volatile("mcr p15, 0, %0, c9, c13, 1" :: "r"(0x03));         /* evento 0x03: refill L1D */
    __asm__ volatile("mcr p15, 0, %0, c9, c12, 1" :: "r"(0x80000001u));  /* abilita PMCCNTR e contatore 0 */
}
static inline unsigned pmu_cycles(void) { unsigned v; __asm__ volatile("mrc p15, 0, %0, c9, c13, 0" : "=r"(v)); return v; }
static inline unsigned pmu_event0(void)
{
    unsigned v;
    __asm__ volatile("mcr p15, 0, %0, c9, c12, 5" :: "r"(0));
    __asm__ volatile("mrc p15, 0, %0, c9, c13, 2" : "=r"(v));
    return v;
}

typedef struct { unsigned ps, ratio, refill; } result_t;

static result_t sweep(const char *label, unsigned bytes)
{
    unsigned sum = 0, n = 0;
    for (unsigned i = 0; i < bytes; i += 32) sum += buf[i];       /* riscaldamento */
    XTime t0, t1;
    pmu_start();
    XTime_GetTime(&t0);
    unsigned c0 = pmu_cycles(), e0 = pmu_event0();
    for (int rep = 0; rep < 4; rep++)
        for (unsigned i = 0; i < bytes; i += 32) { sum += buf[i]; n++; }
    unsigned c1 = pmu_cycles(), e1 = pmu_event0();
    XTime_GetTime(&t1);
    unsigned ticks = (unsigned)(t1 - t0);
    unsigned ps = (unsigned)((unsigned long long)ticks * 3000ull / n);          /* ps per accesso */
    unsigned ratio = (unsigned)((unsigned long long)(c1 - c0) * 100ull / (2ull * ticks));
    unsigned refill = (unsigned)((unsigned long long)(e1 - e0) * 100ull / n);
    xil_printf("%s %4u KB: %u.%02u ns/accesso  PMCCNTR/(2*XTime) %u.%02u  refill L1D %u%%  (%u)\r\n",
               label, bytes >> 10, ps / 1000, (ps % 1000) / 10, ratio / 100, ratio % 100, refill, sum & 1);
    result_t r = { ps, ratio, refill };
    return r;
}

static int ok_ratio(result_t r) { return r.ratio >= 95 && r.ratio <= 105; }

int main(void)
{
    xil_printf("cache e PMU\r\n");
    result_t l1 = sweep("cache on ", 16u << 10);
    result_t l2 = sweep("cache on ", 256u << 10);
    result_t ddr = sweep("cache on ", 2u << 20);
    Xil_DCacheDisable();
    result_t off = sweep("cache off", 16u << 10);
    Xil_DCacheEnable();
    result_t again = sweep("di nuovo ", 16u << 10);
    /* quello che si osserva sulla scheda: L1 < L2 < DDR, cache spenta molto piu' lenta,
     * refill quasi nulli in L1 e quasi a ogni accesso oltre la L1, PMCCNTR = 2 x tick di XTime */
    int ok = l1.ps * 2 < l2.ps && l2.ps < ddr.ps && off.ps > 4 * l1.ps && again.ps * 2 < l2.ps &&
             l1.refill <= 2 && l2.refill >= 90 && ddr.refill >= 90 &&
             ok_ratio(l1) && ok_ratio(l2) && ok_ratio(ddr) && ok_ratio(off) && ok_ratio(again);
    xil_printf("VERIFICA CACHE %s\r\n", ok ? "OK" : "FALLITA");
    xil_printf("FINE\r\n");
    while (1) ;
}
