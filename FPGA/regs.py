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
  reserved:     UInt(3, 29, "Reserved")

@regs.register(0x04, "Atomic Amplitude and Phase Modulation register")
class PolarMod:
  amp:          UInt(0, 16, "16-bit Amplitude (Envelope) control value for PDM/PWM")
  phase:        UInt(16, 16, "16-bit Phase control value")

@regs.register(0x0E, "CPLD Build Number")
class BuildNo:
  val:          UInt(0, 32, "CPLD 32-bit build number")

@regs.register(0x0F, "CPLD Hardware Signature")
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
