/* outbyte.c -- come generato dal BSP standalone: stdout su UART1 */
#include "xparameters.h"
#include "xuartps_hw.h"

void outbyte(char c);

void outbyte(char c)
{
	XUartPs_SendByte(STDOUT_BASEADDRESS, (u8)c);
}
