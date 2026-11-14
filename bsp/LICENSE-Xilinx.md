# Licenza dei file del BSP Xilinx

I file in `bsp/src` e `bsp/include` (e la libreria compilata `bsp/lib/libxil.a`) vengono dal
BSP *standalone* e dai driver del repository ufficiale
[Xilinx/embeddedsw](https://github.com/Xilinx/embeddedsw), distribuiti con licenza **MIT**
(`SPDX-License-Identifier: MIT` nell'intestazione di ogni file):

> Copyright (C) Xilinx, Inc. / Advanced Micro Devices, Inc. All rights reserved.
>
> Permission is hereby granted, free of charge, to any person obtaining a copy of this software
> and associated documentation files (the "Software"), to deal in the Software without
> restriction, including without limitation the rights to use, copy, modify, merge, publish,
> distribute, sublicense, and/or sell copies of the Software, and to permit persons to whom the
> Software is furnished to do so, subject to the following conditions:
>
> The above copyright notice and this permission notice shall be included in all copies or
> substantial portions of the Software.
>
> THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED, INCLUDING
> BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND
> NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM,
> DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
> OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.

Fanno eccezione `bsp/src/print.c` e `bsp/src/putnum.c` (Copyright 1995 Cygnus Support), inclusi
nel BSP Xilinx con la licenza permissiva riportata nella loro intestazione.

I file in `bsp/generated` (`xparameters.h`, `bspconfig.h`, ...) sono scritti per questo
progetto nello stesso formato di quelli che genera Vitis.
