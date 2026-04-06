#!/bin/bash

# ANSI Color Codes
RESET="\e[0m"
GREEN="\e[32m"
YELLOW="\e[33m"
ORANGE="\e[38;5;208m"
RED="\e[31m"
BOLD_RED="\e[1;31m"

# Function to determine color based on temperature
get_temp_color() {
    local temp=$1
    # Remove decimal points if they exist for integer comparison
    local int_temp=${temp%.*}

    if [ "$int_temp" -ge 90 ]; then
        echo -e "${BOLD_RED}${temp}°C${RESET}"
    elif [ "$int_temp" -ge 80 ]; then
        echo -e "${RED}${temp}°C${RESET}"
    elif [ "$int_temp" -ge 70 ]; then
        echo -e "${ORANGE}${temp}°C${RESET}"
    elif [ "$int_temp" -ge 55 ]; then
        echo -e "${YELLOW}${temp}°C${RESET}"
    else
        echo -e "${GREEN}${temp}°C${RESET}"
    fi
}

echo "Starting Stream: CPU & GPU [Temp + Usage]..."
echo "--------------------------------------------------------"

while true; do
    # 1. Get CPU Usage (2nd run of top for accuracy)
    CPU_USAGE=$(LC_ALL=C top -bn2 -d 1 | grep "Cpu(s)" | tail -n 1 | awk '{print 100 - $8}')

    # 2. Get CPU Temp (Stripping '+' and '°C' for cleaner parsing)
    CPU_TEMP_RAW=$(sensors | grep "Package id 0" | awk '{print $4}' | tr -d '+°C')
    CPU_TEMP_COLORED=$(get_temp_color "$CPU_TEMP_RAW")

    # 3. Get GPU Data
    GPU_DATA=$(nvidia-smi --query-gpu=temperature.gpu,utilization.gpu --format=csv,noheader,nounits)
    GPU_TEMP_RAW=$(echo "$GPU_DATA" | awk -F', ' '{print $1}')
    GPU_UTIL=$(echo "$GPU_DATA" | awk -F', ' '{print $2}')
    GPU_TEMP_COLORED=$(get_temp_color "$GPU_TEMP_RAW")

    # 4. Print the line
    # Note: We use %b in printf to interpret the escape characters in the colored strings
    printf "%s | CPU: %b (%s%%) | GPU: %b (%s%%)\n" \
        "$(date '+%H:%M:%S')" \
        "$CPU_TEMP_COLORED" \
        "$CPU_USAGE" \
        "$GPU_TEMP_COLORED" \
        "$GPU_UTIL"
done