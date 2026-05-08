ctx.addClock("clk90_gb", 90.0)
# Mock SPI clock with low frequency to ignore CDC timing
# Amaranth generates a net named something like 'fpgaSCLK_pin'
ctx.addClock("fpgaSCLK_pin", 5.0)

# This removes the "posedge fpgaSCLK -> posedge clk90" constraint.
#ctx.setClockAsync("clk90", "fpgaSCLK")
#ctx.addAsyncClockGroup(["clk90", "fpgaSCLK"])

# This tells the tool NOT to optimize the timing for these crossings
#ctx.setFalsePathFromTo("clk90", "fpgaSCLK")
#ctx.setFalsePathFromTo("fpgaSCLK", "clk90")

# To ignore timing for specific async IO pins (like resets) use
# setFalsePathTo or setFalsePathFrom on specific nets.
#
# Example:
# ctx.setFalsePathFrom("fpgaNRESET")

try:
    pass
except:
    pass
