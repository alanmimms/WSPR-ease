import sys

def check_vcd():
    with open("waveform.vcd", "r") as f:
        # 1. Find symbols for rfPushBase, rfPushPeak, rfPullBase, rfPullPeak, driverNEN
        symbols = {}
        for line in f:
            if "$var wire 1" in line:
                parts = line.split()
                sym = parts[3]
                name = parts[4]
                if name in ["rfPushBase", "rfPushPeak", "rfPullBase", "rfPullPeak", "driverNEN", "pllLocked_clk90", "txEnable"]:
                    symbols[name] = sym
            elif "$enddefinitions" in line:
                break
        
        print("Found symbols:", symbols)
        
        # 2. Count transitions
        counts = {name: 0 for name in symbols}
        state = {name: None for name in symbols}
        
        for line in f:
            if line.startswith(('0', '1')):
                val = line[0]
                sym = line[1:].strip()
                for name, s in symbols.items():
                    if sym == s:
                        if state[name] != val:
                            counts[name] += 1
                            state[name] = val
        
        print("Transition counts:")
        for name, count in counts.items():
            print(f"  {name}: {count}")

if __name__ == "__main__":
    check_vcd()