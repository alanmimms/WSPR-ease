#!/usr/bin/env python3
import sys
import os

# Add project root to path so we can find tools/
sys.path.append(os.path.join(os.path.dirname(__file__), ".."))

from tools.regTool import RegisterSet, UInt, Bit, Enum, Int

regs = RegisterSet("WSPR")

@regs.register(0x00, "Main control and status")
class Control:
  txEnable:     Bit(0, "Enable RF output")
  modeSquare:   Bit(0, "Enable Pure Square Wave mode")
  pllLocked:    Bit(0, "PLL is locked to 180 MHz (Read Only)")
  reserved:     UInt(0, 29, "Reserved")

@regs.register(0x01, "Low 32 bits of 48-bit NCO tuning word")
class TuningLow:
  word:         UInt(0, 32, "NCO frequency control word (Low)")

@regs.register(0x02, "High 16 bits of 48-bit NCO tuning word")
class TuningHigh:
  word:         UInt(0, 16, "NCO frequency control word (High)")
  reserved:     UInt(16, 16, "Reserved")

@regs.register(0x03, "PPS and GNSS edge tracking")
class PPS:
  gen:          UInt(0, 5, "Generation incremented at each PPS falling edge")
  count:        UInt(0, 27, "FPGA clock count at last PPS falling edge")

@regs.register(0x0F, "FPGA Hardware Signature")
class Sig:
  val:          Enum(0x52505357, 32, [("", 0x52505357)], "Fixed value ASCII 'WSPR'")

if __name__ == "__main__":

  if len(sys.argv) > 1:
    genDir = sys.argv[1]
  else:
    genDir = "gen-FIXME" # Fallback if no param is passed
    print("No genDir parameter provided, using 'gen-FIXME'.")
  
  os.makedirs(genDir, exist_ok=True)
  prefix = os.path.join(genDir, "regs")
  regs.writeFiles(prefix)
  print(f"Generated registers at {prefix}.[hpp|md|py]")
