/* inbyte.c -- come generato dal BSP standalone: stdin da UART1 */
#include "xparameters.h"
#include "xuartps_hw.h"

char inbyte(void);

char inbyte(void)
{
	return (char)XUartPs_RecvByte(STDIN_BASEADDRESS);
}
