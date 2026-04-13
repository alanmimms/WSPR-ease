import numpy as np
import matplotlib.pyplot as plt
from scipy.signal import welch
import pandas as pd

# 1. Load the data
data = pd.read_csv('rf_output.csv')
signal = data['Amplitude'].values
fs = 90e6 # Sample rate (90 MHz)

# 2. Calculate Power Spectral Density (PSD) using Welch's method
# Welch's method automatically windows the data to reduce leakage
frequencies, psd = welch(signal, fs=fs, window='blackmanharris', nperseg=16384)

# Convert to decibels (dB)
psd_db = 10 * np.log10(psd / np.max(psd)) # Normalized to carrier peak

# 3. Plot the Spectrum
plt.figure(figsize=(10, 6))
plt.plot(frequencies / 1e6, psd_db)
plt.title("Synthesized 1-2-1 RF Spectrum")
plt.xlabel("Frequency (MHz)")
plt.ylabel("Relative Magnitude (dBc)")
plt.grid(True)
plt.ylim(-100, 5) # Look down to -100 dBc
plt.show()
