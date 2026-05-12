#!/usr/bin/env python3
import os
import sys
import subprocess

def main():
    # 1. Generate Registers
    print("Generating register definitions...")
    subprocess.run([sys.executable, "regs.py"], check=True)

    # 2. Generate Amaranth Gateware (Verilog output)
    print("Elaborating Amaranth gateware...")
    # We need to make sure the current directory is in the path so we can import .regs_gen
    sys.path.append(os.getcwd())
    
    from gateware import Top
    from amaranth.back import verilog
    
    top = Top()
    ports = [
        top.clk40, top.gnssPPS, top.fpgaNRESET,
        top.fpgaSCLK_pin, top.fpgaMOSI, top.fpgaMISO, top.fpgaNCS,
        top.rfPushBase, top.rfPushPeak,
        top.rfPullBase, top.rfPullPeak,
        top.driverNEN
    ]
    
    output_file = "Top.v"
    with open(output_file, "w") as f:
        # Amaranth natively generates Verilog-2005. 
        f.write(verilog.convert(top, ports=ports, name="Top"))
    
    print(f"Generated {output_file} (Verilog-2005)")

    # 3. Copy firmware headers to the correct location if needed
    # The ESP32 firmware expects them in sw/hal/ (or similar)
    # Let's check where regs.hpp should go.
    
    cpp_header = "regs.hpp"
    target_dir = "../sw/hal/"
    if os.path.exists(target_dir):
        import shutil
        shutil.copy(cpp_header, os.path.join(target_dir, "regs.hpp"))
        print(f"Copied {cpp_header} to {target_dir}")
    else:
        print(f"Warning: Target directory {target_dir} not found. skipping header copy.")

if __name__ == "__main__":
    main()
