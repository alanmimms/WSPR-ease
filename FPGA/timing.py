# nextpnr-ice40 timing constraints

# Carrier clock (Si5351 CLK0) max frequency is 148.5 MHz (for 10m band)
ctx.addClock("FPGACLK", 148.5)

# TCXO clock (Si5351 CLK1) is 40.0 MHz
ctx.addClock("txco", 40.0)

# SPI SCLK clock is 5.0 MHz
ctx.addClock("fpgaSCLKpin", 5.0)
