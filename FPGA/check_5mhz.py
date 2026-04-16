import numpy as np

def sim_states(freq_hz, sample_rate_hz=180e6):
    phase = 0
    tuning_word = (freq_hz / sample_rate_hz) * (2**32)
    step = tuning_word / (2**32)
    
    states = []
    for i in range(100):
        # Scale 0..1 to 0..6
        state = int(phase * 6) % 6
        states.append(state)
        phase = (phase + step) % 1.0
        
    return states

freq = 5e6
states = sim_states(freq)
print(f"5MHz Carrier states: {states}")

# Count occurrences of each state
from collections import Counter
counts = Counter(states)
print(f"State counts: {counts}")
