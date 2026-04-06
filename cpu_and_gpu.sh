#!/bin/bash

echo "Starting Stream: CPU & GPU [Temp + Usage]..."
echo "--------------------------------------------------------"

# We set LC_ALL=C to ensure decimal points are handled correctly in all languages
while true; do
    # 1. Get CPU Usage
    # We run top twice (-n2) with a 1s delay (-d1). 
    # The first run is often a 'since boot' avg, so we grab the 2nd run (tail -1).
    # We calculate usage as (100% - Idle%).
    CPU_USAGE=$(LC_ALL=C top -bn2 -d 1 | grep "Cpu(s)" | tail -n 1 | awk '{print 100 - $8}')

    # 2. Get CPU Temp (Intel Package id 0)
    CPU_TEMP=$(sensors | grep "Package id 0" | awk '{print $4}' | tr -d '+')

    # 3. Get GPU Data (Temp & Usage) in one call to reduce driver overhead
    # nounits removes the "%" and "C" so we can format it ourselves
    GPU_DATA=$(nvidia-smi --query-gpu=temperature.gpu,utilization.gpu --format=csv,noheader,nounits)
    
    # Parse the comma-separated Nvidia output
    GPU_TEMP=$(echo "$GPU_DATA" | awk -F', ' '{print $1}')
    GPU_UTIL=$(echo "$GPU_DATA" | awk -F', ' '{print $2}')

    # 4. Print the line with timestamp
    # printf allows for cleaner column alignment than echo
    printf "%s | CPU: %s (%s%%) | GPU: %s°C (%s%%)\n" "$(date '+%H:%M:%S')" "$CPU_TEMP" "$CPU_USAGE" "$GPU_TEMP" "$GPU_UTIL"
done