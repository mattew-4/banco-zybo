/*
 * drivers_g.c -- tabelle di configurazione dei driver, nel formato che HSI genera
 * nei file xgpio_g.c, xuartps_g.c, xiicps_g.c, xscugic_g.c, xgpiops_g.c,
 * xscutimer_g.c, xscuwdt_g.c, xttcps_g.c (classic flow, campo DeviceId presente).
 */
#include "xparameters.h"
#include "xgpio.h"
#include "xuartps.h"
#include "xiicps.h"
#include "xscugic.h"
#include "xgpiops.h"
#include "xscutimer.h"
#include "xscuwdt.h"
#include "xttcps.h"
#include "xllfifo.h"

XGpio_Config XGpio_ConfigTable[XPAR_XGPIO_NUM_INSTANCES] = {
	{ XPAR_GPIO_0_DEVICE_ID, XPAR_GPIO_0_BASEADDR, XPAR_GPIO_0_INTERRUPT_PRESENT, XPAR_GPIO_0_IS_DUAL },
	{ XPAR_GPIO_1_DEVICE_ID, XPAR_GPIO_1_BASEADDR, XPAR_GPIO_1_INTERRUPT_PRESENT, XPAR_GPIO_1_IS_DUAL },
	{ XPAR_GPIO_2_DEVICE_ID, XPAR_GPIO_2_BASEADDR, XPAR_GPIO_2_INTERRUPT_PRESENT, XPAR_GPIO_2_IS_DUAL }
};

XUartPs_Config XUartPs_ConfigTable[XPAR_XUARTPS_NUM_INSTANCES] = {
	{ (u16)XPAR_XUARTPS_0_DEVICE_ID, (u32)XPAR_XUARTPS_0_BASEADDR,
	  (u32)XPAR_XUARTPS_0_UART_CLK_FREQ_HZ, (s32)XPAR_XUARTPS_0_HAS_MODEM }
};

XIicPs_Config XIicPs_ConfigTable[XPAR_XIICPS_NUM_INSTANCES] = {
	{ (u16)XPAR_XIICPS_0_DEVICE_ID, (u32)XPAR_XIICPS_0_BASEADDR, (u32)XPAR_XIICPS_0_I2C_CLK_FREQ_HZ }
};

XScuGic_Config XScuGic_ConfigTable[XPAR_XSCUGIC_NUM_INSTANCES] = {
	{ (u16)XPAR_SCUGIC_0_DEVICE_ID, (u32)XPAR_SCUGIC_0_CPU_BASEADDR,
	  (u32)XPAR_SCUGIC_0_DIST_BASEADDR, {{0}} }
};

XGpioPs_Config XGpioPs_ConfigTable[XPAR_XGPIOPS_NUM_INSTANCES] = {
	{ (u16)XPAR_XGPIOPS_0_DEVICE_ID, (u32)XPAR_XGPIOPS_0_BASEADDR }
};

XScuTimer_Config XScuTimer_ConfigTable[XPAR_XSCUTIMER_NUM_INSTANCES] = {
	{ (u16)XPAR_XSCUTIMER_0_DEVICE_ID, (u32)XPAR_XSCUTIMER_0_BASEADDR }
};

XScuWdt_Config XScuWdt_ConfigTable[XPAR_XSCUWDT_NUM_INSTANCES] = {
	{ (u16)XPAR_SCUWDT_0_DEVICE_ID, (u32)XPAR_SCUWDT_0_BASEADDR }
};

XTtcPs_Config XTtcPs_ConfigTable[XPAR_XTTCPS_NUM_INSTANCES] = {
	{ (u16)XPAR_XTTCPS_0_DEVICE_ID, (u32)XPAR_XTTCPS_0_BASEADDR, (u32)XPAR_XTTCPS_0_TTC_CLK_FREQ_HZ },
	{ (u16)XPAR_XTTCPS_1_DEVICE_ID, (u32)XPAR_XTTCPS_1_BASEADDR, (u32)XPAR_XTTCPS_1_TTC_CLK_FREQ_HZ },
	{ (u16)XPAR_XTTCPS_2_DEVICE_ID, (u32)XPAR_XTTCPS_2_BASEADDR, (u32)XPAR_XTTCPS_2_TTC_CLK_FREQ_HZ }
};

/* AXI4-Stream FIFO (driver LLFIFO): la 0 e' quella del percorso audio */
XLlFifo_Config XLlFifo_ConfigTable[XPAR_XLLFIFO_NUM_INSTANCES] = {
	{ XPAR_AXI_FIFO_MM_S_0_DEVICE_ID, XPAR_AXI_FIFO_MM_S_0_BASEADDR,
	  XPAR_AXI_FIFO_MM_S_0_AXI4_BASEADDR, XPAR_AXI_FIFO_MM_S_0_DATA_INTERFACE_TYPE },
	{ XPAR_AXI_FIFO_MM_S_1_DEVICE_ID, XPAR_AXI_FIFO_MM_S_1_BASEADDR,
	  XPAR_AXI_FIFO_MM_S_1_AXI4_BASEADDR, XPAR_AXI_FIFO_MM_S_1_DATA_INTERFACE_TYPE }
};
