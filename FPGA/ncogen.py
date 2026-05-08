from amaranth import *
from amaranth.back import verilog

class PipelinedNCO(Elaboratable):
    def __init__(self, width=48, stages=6):
        assert width % stages == 0, "Width must be evenly divisible by stages"
        self.width = width
        self.stages = stages
        self.chunkW = width // stages
        
        # --- Module Ports ---
        self.tw = Signal(width)
        self.phase = Signal(width)

    def elaborate(self, platform) -> Module:
        m = Module()
        cw = self.chunkW
        
        # Array to hold the accumulator registers for each chunk
        accChunks = [Signal(cw, name=f"accChunk{i}") for i in range(self.stages)]
        
        # The carry signal that passes between stages
        carry = Signal()
        
        # --- Python generates the hardware stages automatically ---
        for i in range(self.stages):
            # 1. Grab this stage's slice of the tuning word
            twChunk = self.tw[i*cw : (i+1)*cw]
            
            # (Optional: If you want true phase alignment, you would delay twChunk 
            # by 'i' clock cycles here using a simple helper function and a loop)
            
            # 2. Add the accumulator, the tuning word, and the previous stage's carry
            # We make a temp signal 1 bit wider than the chunk to catch the carry-out
            sumWCarry = Signal(cw + 1, name=f"sumWCarry{i}")
            
            # m.d.comb is exactly equivalent to `assign` in SystemVerilog
            m.d.comb += sumWCarry.eq(accChunks[i] + twChunk + carry)
            
            # 3. Register the results on the clock edge
            nextCarry = Signal(name=f"nextCarry{i}")
            
            # m.d.sync is exactly equivalent to `always_ff @(posedge clk)`
            m.d.sync += [
                accChunks[i].eq(sumWCarry[0:cw]), # Lower bits go to accumulator
                nextCarry.eq(sumWCarry[-1])       # MSB is the new carry out
            ]
            
            # Pass the registered carry to the next loop iteration
            carry = nextCarry

        # --- Reassemble the chunks for the output ---
        # Cat() concatenates a list of signals, equivalent to {chunk3, chunk2, chunk1}
        m.d.comb += self.phase.eq(Cat(*accChunks))

        return m

class ExciterDP(Elaboratable):
    def __init__(self, width=48, stages=6):
        # ... (port definitions) ...
        self.tw = Signal(width)
        self.dither = Signal(8)
        self.mulR = Signal(19)
        self.mulF = Signal(19)

    def elaborate(self, platform) -> Module:
        m = Module()

        # 1. Instantiate the pipelined NCO
        m.submodules.nco = nco = PipelinedNCO(width=width, stages=stages)
        m.d.comb += nco.tw.eq(self.tw)
        
        # 2. THE SPLIT: Calculate the top 16 bits for R and F
        # We grab the top 16 bits of the NCO output and the Tuning Word
        phaseTop = nco.phase[32:48]
        twTop = self.tw[32:48]
        
        phRreg = Signal(16)
        phFreg = Signal(16)
        
        # Pipeline the offset calculation so it doesn't eat into the DSP time!
        m.d.sync += [
            phRreg.eq(phaseTop),
            phFreg.eq(phaseTop + (twTop >> 1))
        ]

        # 3. THE TWIN DSP PIPELINES
        # Amaranth will map these distinct multiplications into separate SB_MAC16 blocks
        
        # We need to pipeline the dither to match the delay of phRreg/phFreg
        ditherSync = Signal(8)
        m.d.sync += ditherSync.eq(self.dither)

        m.d.sync += [
            self.mulR.eq((phRreg * 6) + ditherSync),
            self.mulF.eq((phFreg * 6) + ditherSync)
        ]

        return m

if __name__ == "__main__":
    # Generate the Verilog
    width = 48
    stages = 6
    fileName = "exciterDP.v"
    dp = ExciterDP(width=width, stages=stages)
    
    with open(fileName, "w") as f:
        f.write(verilog.convert(dp, ports=[dp.tw, dp.dither, dp.mulR, dp.mulF]))
        print(f"[generated {fileName} with {stages} pipeline stages]")
