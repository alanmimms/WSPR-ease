import numpy as np
import matplotlib.pyplot as plt

def calculate_lc_filter_response(frequencies_hz, components, z0=50.0):
    """
    Calculates the S21 magnitude response of an LC ladder filter using ABCD matrices.
    'components' is a list of tuples: [('C', 100e-12), ('L', 1e-6), ('C', 200e-12), ...]
    Alternating Shunt Capacitor ('C') and Series Inductor ('L').
    """
    # Prevent divide-by-zero at DC
    f = np.maximum(frequencies_hz, 1.0) 
    w = 2 * np.pi * f
    
    # Pre-allocate array for S21 response
    s21_db = np.zeros(len(w))
    
    for i, freq_rad in enumerate(w):
        # Start with the Identity Matrix
        abcd = np.array([[1.0 + 0j, 0.0 + 0j], 
                         [0.0 + 0j, 1.0 + 0j]])
        
        for comp_type, value in components:
            if comp_type == 'C':
                # Shunt Capacitor: Y = j*w*C
                Y = 1j * freq_rad * value
                step_matrix = np.array([[1, 0], [Y, 1]])
            elif comp_type == 'L':
                # Series Inductor: Z = j*w*L
                Z = 1j * freq_rad * value
                step_matrix = np.array([[1, Z], [0, 1]])
                
            # Cascade the matrices
            abcd = abcd @ step_matrix
            
        A, B = abcd[0, 0], abcd[0, 1]
        C, D = abcd[1, 0], abcd[1, 1]
        
        # Convert ABCD to S21 (Transmission Coefficient) assuming Z_source = Z_load = 50 ohms
        S21 = 2 / (A + B/z0 + C*z0 + D)
        
        # Calculate magnitude in dB
        # Limit the floor to -150 dB to avoid math errors in ideal components
        magnitude = np.abs(S21)
        if magnitude < 1e-8: 
            s21_db[i] = -160.0
        else:
            s21_db[i] = 20 * np.log10(magnitude)
            
    return s21_db

# ====================================================================
# Example Integration into your existing script (Step 4 -> 5)
# ====================================================================

# Let's say you are building a 3.5 MHz (80m band) 7-element Chebyshev filter.
# (Values are hypothetical examples for an 80m LPF)
lpf_80m = [
    ('C', 1100e-12), # C1: Shunt 1100 pF
    ('L', 2.5e-6),   # L1: Series 2.5 uH
    ('C', 2200e-12), # C2: Shunt 2200 pF
    ('L', 2.5e-6),   # L2: Series 2.5 uH
    ('C', 1100e-12)  # C3: Shunt 1100 pF
]

# Calculate the filter's response across the exact frequency bins from the FFT
filter_response_db = calculate_lc_filter_response(frequencies, lpf_80m, z0=50.0)

# Apply the filter to the FPGA spectrum!
# Because the FPGA spectrum is in dBc, and filter is in dB, we just add them.
filtered_spectrum_dbc = psd_dbc + filter_response_db

# Now you can plot them all together:
plt.figure(figsize=(12, 7))

# 1. The Raw FPGA Spectrum (Light grey)
plt.plot(frequencies / 1e6, psd_dbc, color='lightgrey', label='Raw FPGA 1-2-1 Output')

# 2. The Filter Characteristic (Dashed blue)
plt.plot(frequencies / 1e6, filter_response_db, color='blue', linestyle='--', label='LC Filter Response')

# 3. The Final Antenna Port Spectrum (Solid red)
plt.plot(frequencies / 1e6, filtered_spectrum_dbc, color='red', linewidth=1.5, label='Filtered Antenna Output')

plt.xlim(0, 30) # Look at 0 to 30 MHz
plt.ylim(-120, 10)
plt.title("WSPR Output Spectrum: FPGA to Antenna Port")
plt.xlabel("Frequency (MHz)")
plt.ylabel("Magnitude (dBc)")
plt.legend()
plt.grid(True)
plt.show()
