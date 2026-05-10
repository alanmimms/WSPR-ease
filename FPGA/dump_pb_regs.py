import sys

def dump_pb_regs(filename, num=20):
    pb_or = None
    pb_of = None
    with open(filename, 'r') as f:
        t = 0
        for line in f:
            line = line.strip()
            if line.startswith('$var'):
                parts = line.split()
                if len(parts) >= 5:
                    name = parts[4]
                    if name == 'pb_or': pb_or = parts[3]
                    if name == 'pb_of': pb_of = parts[3]
            elif line.startswith('#'):
                t = int(line[1:])
                if t > 62000000: # After SPI setup
                    pass
            elif len(line) > 0 and line[0] in '01':
                val = line[0]
                sig_id = line[1:]
                if t > 62000000:
                    if sig_id == pb_or: print(f"Time {t:15}: pb_or -> {val}")
                    if sig_id == pb_of: print(f"Time {t:15}: pb_of -> {val}")
                    # Count matches to stop early
                    pass

if __name__ == "__main__":
    dump_pb_regs("waveform.vcd")
