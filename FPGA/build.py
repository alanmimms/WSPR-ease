#!/usr/bin/env python3
import os
import sys
import subprocess

def main():
    os.environ.setdefault("SIMULATION", "0")
    isSim = os.environ.get("SIMULATION") == "1"
    genDir = "gen-sim" if isSim else "gen-hw"

    # Read and increment/use build number
    buildNumFile = "buildNumber.txt"
    buildNum = 0
    import time
    now = time.time()
    currentTime = os.path.getmtime(buildNumFile) if os.path.exists(buildNumFile) else 0

    if os.path.exists(buildNumFile):
        with open(buildNumFile, "r") as f:
            try:
                buildNum = int(f.read().strip())
            except ValueError:
                buildNum = 0

    if now - currentTime > 5:
        buildNum += 1
        with open(buildNumFile, "w") as f:
            f.write(str(buildNum))
        print(f"Incremented build number to {buildNum}")
    else:
        print(f"Using build number {buildNum}")

    # Generate buildNumber.hpp in genDir
    os.makedirs(genDir, exist_ok=True)
    with open(f"{genDir}/buildNumber.hpp", "w") as f:
        f.write("#pragma once\n")
        f.write("#include <cstdint>\n\n")
        f.write(f"constexpr uint32_t fpgaBuildNumber = {buildNum};\n")
    print(f"Generated {genDir}/buildNumber.hpp")

    from gateware import Top, exportRegs
    from amaranth.back import verilog

    # Generate register definitions for C++ code.
    exportRegs(f"{genDir}/regs.hpp")

    # Generate Amaranth Gateware (Verilog output)
    print("Elaborating Amaranth gateware...")
    # We need to make sure the 'gen' directory is in the path so we can import .regs_gen
    sys.path.append(os.path.join(os.getcwd(), genDir))
    
    top = Top(isSim, buildNum)
    ports = top.getPorts()
    
    os.makedirs(genDir, exist_ok=True)
    outF = f"{genDir}/Top.v"
    with open(outF, "w") as f:
        # Amaranth natively generates Verilog-2005. 
        f.write(verilog.convert(top, ports=ports, name="Top"))
    
    print(f"Generated {outF} (Verilog-2005)")

    # Copy firmware headers to the correct location if needed
    cppHeader = f"{genDir}/regs.hpp"
    cppBuildNumHeader = f"{genDir}/buildNumber.hpp"
    targDir = "../sw/hal/"
    if os.path.exists(targDir):
        import shutil
        shutil.copy(cppHeader, os.path.join(targDir, "regs.hpp"))
        print(f"Copied {cppHeader} to {targDir}")
        shutil.copy(cppBuildNumHeader, os.path.join(targDir, "buildNumber.hpp"))
        print(f"Copied {cppBuildNumHeader} to {targDir}")
    else:
        print(f"Warning: Target directory {targDir} not found. skipping header copy.")

if __name__ == "__main__":
    main()
