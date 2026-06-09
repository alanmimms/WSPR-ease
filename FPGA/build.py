#!/usr/bin/env python3
import os
import sys
import subprocess

def main():
    isSim = os.environ.get("SIMULATION") == "1"
    genDir = "gen-sim" if isSim else "gen-hw"

    # 1. Generate Registers
    print("Generating register definitions...")
    child_env = os.environ.copy()
    child_env["SIMULATION"] = os.environ.get("SIMULATION", "0")
    subprocess.run([sys.executable, "regs.py", genDir], env=child_env, check=True)

    # 2. Generate Amaranth Gateware (Verilog output)
    print("Elaborating Amaranth gateware...")
    # We need to make sure the 'gen' directory is in the path so we can import .regs_gen
    sys.path.append(os.path.join(os.getcwd(), genDir))
    
    from gateware import Top
    from amaranth.back import verilog
    
    top = Top(isSim)
    ports = top.getPorts()
    
    os.makedirs(genDir, exist_ok=True)
    outF = f"{genDir}/Top.v"
    with open(outF, "w") as f:
        # Amaranth natively generates Verilog-2005. 
        f.write(verilog.convert(top, ports=ports, name="Top"))
    
    print(f"Generated {outF} (Verilog-2005)")

    # 3. Copy firmware headers to the correct location if needed
    # The ESP32 firmware expects them in sw/hal/ (or similar)
    # Let's check where regs.hpp should go.
    
    cppHeader = f"{genDir}/regs.hpp"
    targDir = "../sw/hal/"
    if os.path.exists(targDir):
        import shutil
        shutil.copy(cppHeader, os.path.join(targDir, "regs.hpp"))
        print(f"Copied {cppHeader} to {targDir}")
    else:
        print(f"Warning: Target directory {targDir} not found. skipping header copy.")

if __name__ == "__main__":
    main()
