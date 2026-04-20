#!/usr/bin/env python3
from tools.regTool import RegisterSet, UInt, Bit, Enum, Int
import os

regs = RegisterSet("WSPR")

@regs.register(0x00, "Main control and status")
class Control:
  txEnable:     Bit(0, "Enable RF output")
  pllLocked:    Bit(0, "PLL is locked to 180 MHz (Read Only)")
  reserved:     UInt(0, 30, "Reserved")

@regs.register(0x01, "32-bit NCO tuning word")
class Tuning:
  word:         UInt(0, 32, "NCO frequency control word")

@regs.register(0x03, "PPS and GNSS edge tracking")
class PPS:
  gen:          UInt(0, 5, "Generation incremented at each PPS falling edge")
  count:        UInt(0, 27, "FPGA clock count at last PPS falling edge")

@regs.register(0x0F, "FPGA Hardware Signature")
class Sig:
  val:          Enum(0x52505357, 32, [("", 0x52505357)], "Fixed value ASCII 'WSPR'")

if __name__ == "__main__":
  # Generate files in the current directory (FPGA/)
  prefix = os.path.join(os.path.dirname(__file__), "regs")
  regs.writeFiles(prefix)
  print(f"Generated registers at {prefix}.[sv|hpp|md]")
