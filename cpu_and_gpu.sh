#!/bin/bash

################################################################################
# SCRIPT: monitor_stats.sh
# DESCRIPTION: Monitors CPU/GPU temperatures and usage in real-time.
# USAGE: ./monitor_stats.sh [--bg]
#        --bg : Toggle background colors instead of foreground text colors.
# DEPENDENCIES: 
#   - lm-sensors (for 'sensors' command)
#   - nvidia-smi (for GPU data)
#   - top (standard linux utility)
################################################################################

# --- Configuration & Colors ---
RESET="\e[0m"
USE_BG=false

# Check for CLI arguments
if [[ "$1" == "--bg" ]]; then
    USE_BG=true
fi

# Function to wrap text in ANSI codes based on mode
# Args: temperature, color_code_fg, color_code_bg
apply_style() {
    local val="$1"
    local fg="$2"
    local bg="$3"
    
    if [ "$USE_BG" = true ]; then
        # For background mode, we often need black text (30) or white text (37) for contrast
        echo -e "${bg}${val}${RESET}"
    else
        echo -e "${fg}${val}${RESET}"
    fi
}

get_temp_color() {
    local temp=$1
    local int_temp=${temp%.*}

    # Define color pairs: [Foreground] [Background]
    # Backgrounds use \e[4Xm instead of \e[3Xm
    local C_CRIT_FG="\e[1;31m"    # Bold Red
    local C_CRIT_BG="\e[41;1;37m" # Red BG, Bold White Text
    
    local C_HIGH_FG="\e[31m"      # Red
    local C_HIGH_BG="\e[41m"      # Red BG
    
    local C_WARN_FG="\e[38;5;208m" # Orange
    local C_WARN_BG="\e[48;5;208;30m" # Orange BG, Black Text
    
    local C_MID_FG="\e[33m"       # Yellow
    local C_MID_BG="\e[43;30m"    # Yellow BG, Black Text
    
    local C_OK_FG="\e[32m"        # Green
    local C_OK_BG="\e[42;30m"     # Green BG, Black Text

    if [ "$int_temp" -ge 90 ]; then
        apply_style "${temp}°C" "$C_CRIT_FG" "$C_CRIT_BG"
    elif [ "$int_temp" -ge 80 ]; then
        apply_style "${temp}°C" "$C_HIGH_FG" "$C_HIGH_BG"
    elif [ "$int_temp" -ge 70 ]; then
        apply_style "${temp}°C" "$C_WARN_FG" "$C_WARN_BG"
    elif [ "$int_temp" -ge 55 ]; then
        apply_style "${temp}°C" "$C_MID_FG" "$C_MID_BG"
    else
        apply_style "${temp}°C" "$C_OK_FG" "$C_OK_BG"
    fi
}

# --- Initialization ---
echo "Starting Stream: CPU & GPU [Temp + Usage]..."
echo "Mode: $( [ "$USE_BG" = true ] && echo "Background Highlight" || echo "Standard Text" )"
echo "--------------------------------------------------------"

# --- Main Loop ---
while true; do
    # 1. CPU Usage
    CPU_USAGE=$(LC_ALL=C top -bn2 -d 1 | grep "Cpu(s)" | tail -n 1 | awk '{print 100 - $8}')

    # 2. CPU Temp
    CPU_TEMP_RAW=$(sensors | grep "Package id 0" | awk '{print $4}' | tr -d '+°C')
    CPU_TEMP_COLORED=$(get_temp_color "$CPU_TEMP_RAW")

    # 3. GPU Data
    GPU_DATA=$(nvidia-smi --query-gpu=temperature.gpu,utilization.gpu --format=csv,noheader,nounits 2>/dev/null)
    if [ $? -ne 0 ]; then
        GPU_TEMP_COLORED="N/A"
        GPU_UTIL="N/A"
    else
        GPU_TEMP_RAW=$(echo "$GPU_DATA" | awk -F', ' '{print $1}')
        GPU_UTIL=$(echo "$GPU_DATA" | awk -F', ' '{print $2}')
        GPU_TEMP_COLORED=$(get_temp_color "$GPU_TEMP_RAW")
    fi

    # 4. Output
    printf "%s | CPU: %b (%s%%) | GPU: %b (%s%%)\n" \
        "$(date '+%H:%M:%S')" \
        "$CPU_TEMP_COLORED" \
        "$CPU_USAGE" \
        "$GPU_TEMP_COLORED" \
        "$GPU_UTIL"
done