#!/usr/bin/env python3
import sys
import os

# Python 3.7 or later is required for order-preserving dicts.
if sys.version_info < (3, 7):
  sys.exit(f"FATAL ERROR: This requires Python 3.7 or higher.")

# Add project root to path so we can find tools/
sys.path.append(os.path.join(os.path.dirname(__file__), ".."))

from tools.regTool import RegisterSet, UInt, Bit, Enum, Int

regs = RegisterSet("FPGA")

@regs.register(0x00, "Main control and status")
class Control:
  txEnable:     Bit(0, "Enable RF output")
  modeSquare:   Bit(1, "Enable Pure Square Wave mode")
  softReset:    Bit(2, "Soft reset for internal state machines")
  modMode:      UInt(3, 2, "Modulation mode: 00=Static, 01=I2S AM + Static PM, 10=SSB/Polar")
  reserved:     UInt(5, 27, "Reserved")

@regs.register(0x01, "Phase Delay and PA Enable Control")
class PhaseDelayCtrl:
  baseDelay:     UInt(0, 8, "Base delay in I2S sample periods")
  delayCoeff:    UInt(8, 8, "Dynamic delay coefficient")
  paEnThreshold: UInt(16, 16, "Threshold for paEn output")

@regs.register(0x02, "GNSS PPS Latched Counter")
class PpsLatch:
  val:           UInt(0, 32, "Latched TCXO clock cycles count at PPS edge")

# We can assume these never change address.
@regs.register(0x7E, "FPGA Build Number")
class BuildNo:
  val:          UInt(0, 32, "FPGA 32-bit build number")

@regs.register(0x7F, "FPGA Hardware Signature")
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
