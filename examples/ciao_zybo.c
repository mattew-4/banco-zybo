/* Esempio di partenza: LED = switch XOR pulsanti, e una riga sul Serial Monitor
 * ogni volta che cambia qualcosa. Usa solo Xil_In32/Xil_Out32 (accesso diretto ai registri). */
#include <stdint.h>
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
    xil_printf("Ciao dalla Zybo Z7-10!\r\n");
    Xil_Out32(LED_BASE + GPIO_TRI, 0x0);          /* LED come uscite */

    uint32_t prev = 0xFFFFFFFF;
    while (1) {
        uint32_t sw = Xil_In32(SW_BASE + GPIO_DATA) & 0xF;
        uint32_t bt = Xil_In32(BTN_BASE + GPIO_DATA) & 0xF;
        Xil_Out32(LED_BASE + GPIO_DATA, sw ^ bt); /* LED = switch XOR pulsanti */
        if ((sw | bt << 4) != prev) {
            xil_printf("SW = 0x%x  BTN = 0x%x\r\n", (int)sw, (int)bt);
            prev = sw | bt << 4;
        }
        usleep(10000);
    }
}
