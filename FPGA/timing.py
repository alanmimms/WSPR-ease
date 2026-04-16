ctx.addClock("clk90", 90.0)
# Mock fpgaSCLK with very low frequency to ignore CDC timing
ctx.addClock("fpgaSCLK", 0.1) 

try:
    pass
except:
    pass
