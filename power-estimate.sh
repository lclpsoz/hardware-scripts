#!/bin/bash
# power-estimate.sh v2 - Laptop wall-power estimate with GPU, screen and charger losses (Linux)
# Run:  sudo ./power-estimate.sh
# Tunables (env vars): INTERVAL PRICE_KWH CURRENCY HOURS_PER_DAY
#   GPU_IDLE_W GPU_MAX_W GPU_OFF_W  SCREEN_MIN_W SCREEN_MAX_W  REST_W  CHARGER_EFF
#   CPU_IDLE_W CPU_MAX_W (only used if RAPL is unreadable)
# Example: sudo PRICE_KWH=1.1 CURRENCY=BRL HOURS_PER_DAY=24 ./power-estimate.sh

set -u

INTERVAL="${INTERVAL:-2}"
PRICE_KWH="${PRICE_KWH:-0.15}"
CURRENCY="${CURRENCY:-USD}"
HOURS_PER_DAY="${HOURS_PER_DAY:-8}"

GPU_OFF_W="${GPU_OFF_W:-0}"          # GPU runtime-suspended (powered down)
GPU_IDLE_W="${GPU_IDLE_W:-4}"        # GPU awake but idle
GPU_MAX_W="${GPU_MAX_W:-50}"         # GTX 1650 mobile ~50W; GTX 1050 mobile ~53W
SCREEN_MIN_W="${SCREEN_MIN_W:-1.5}"  # panel + backlight at lowest brightness
SCREEN_MAX_W="${SCREEN_MAX_W:-5}"    # at full brightness
REST_W="${REST_W:-6}"                # RAM, SSD, WiFi, fans, keyboard LEDs, chipset
CHARGER_EFF="${CHARGER_EFF:-0.88}"   # AC adapter efficiency (typically 0.85-0.92)
CPU_IDLE_W="${CPU_IDLE_W:-4}"
CPU_MAX_W="${CPU_MAX_W:-45}"

# ---------- Battery ----------
BATTERIES=(/sys/class/power_supply/BAT*)
[ -e "${BATTERIES[0]}" ] || BATTERIES=()

battery_read() {   # prints "<microwatts> <status>"
    local total=0 status="" found=0 b p c v
    for b in "${BATTERIES[@]}"; do
        [ -r "$b/status" ] || continue
        status=$(<"$b/status")
        if [ -r "$b/power_now" ]; then
            p=$(<"$b/power_now")
        elif [ -r "$b/current_now" ] && [ -r "$b/voltage_now" ]; then
            c=$(<"$b/current_now"); v=$(<"$b/voltage_now")
            p=$(awk -v c="$c" -v v="$v" 'BEGIN{printf "%d", c*v/1000000}')
        else
            continue
        fi
        total=$((total + p)); found=1
    done
    [ "$found" -eq 1 ] && echo "$total $status"
}

# ---------- CPU package (RAPL) ----------
PKG_DIR=""
for d in /sys/class/powercap/intel-rapl:[0-9]; do
    [ -r "$d/energy_uj" ] && [ -r "$d/name" ] || continue
    case "$(<"$d/name")" in package-*) PKG_DIR="$d"; break ;; esac
done

rapl_w() {   # dir prev_uj cur_uj dt
    local max d
    max=$(<"$1/max_energy_range_uj")
    d=$(( $3 - $2 )); [ "$d" -lt 0 ] && d=$((d + max))
    awk -v d="$d" -v dt="$4" 'BEGIN{printf "%.1f", d/1000000/dt}'
}

# ---------- GPU (NVIDIA) ----------
GPU_PCI=""
for d in /sys/bus/pci/devices/*; do
    [ "$(<"$d/vendor")" = "0x10de" ] || continue
    case "$(<"$d/class")" in 0x03*) GPU_PCI="$d"; break ;; esac
done
GPU_W=0; GPU_UTIL=0; GPU_STATE="none"

gpu_read() {
    GPU_W=0; GPU_UTIL=0
    if [ -z "$GPU_PCI" ]; then GPU_STATE="none"; return; fi
    # Check this first: querying nvidia-smi would wake a sleeping GPU
    if [ -r "$GPU_PCI/power/runtime_status" ] && [ "$(<"$GPU_PCI/power/runtime_status")" = "suspended" ]; then
        GPU_W=$GPU_OFF_W; GPU_STATE="off"; return
    fi
    if ! command -v nvidia-smi >/dev/null; then GPU_W=$GPU_IDLE_W; GPU_STATE="no-smi"; return; fi
    local out util pw
    out=$(nvidia-smi --query-gpu=utilization.gpu,power.draw --format=csv,noheader,nounits 2>/dev/null | head -n1)
    IFS=', ' read -r util pw <<< "$out"
    [[ "${util:-}" =~ ^[0-9]+$ ]] || util=0
    GPU_UTIL=$util
    if [[ "${pw:-}" =~ ^[0-9.]+$ ]]; then
        GPU_W=$pw; GPU_STATE="sensor"
    else
        GPU_W=$(awk -v u="$util" -v lo="$GPU_IDLE_W" -v hi="$GPU_MAX_W" 'BEGIN{printf "%.1f", lo+(hi-lo)*u/100}')
        GPU_STATE="model"
    fi
}

# ---------- Screen ----------
BL_DIR=$(ls -d /sys/class/backlight/* 2>/dev/null | head -n1)
screen_w() {
    if [ -n "$BL_DIR" ] && [ -r "$BL_DIR/brightness" ] && [ -r "$BL_DIR/max_brightness" ]; then
        awk -v b="$(<"$BL_DIR/brightness")" -v m="$(<"$BL_DIR/max_brightness")" \
            -v lo="$SCREEN_MIN_W" -v hi="$SCREEN_MAX_W" \
            'BEGIN{p=(m>0)?b/m:0.5; printf "%.1f", lo+(hi-lo)*p}'
    else
        awk -v lo="$SCREEN_MIN_W" -v hi="$SCREEN_MAX_W" 'BEGIN{printf "%.1f", (lo+hi)/2}'
    fi
}

# ---------- CPU utilization ----------
read_cpu() {
    local u n s i io irq sirq st
    read -r _ u n s i io irq sirq st _ < /proc/stat
    CPU_TOTAL=$((u + n + s + i + io + irq + sirq + st))
    CPU_IDLE=$((i + io))
}

# ---------- Setup ----------
echo "Power estimator v2 (Ctrl+C for summary)"
[ ${#BATTERIES[@]} -gt 0 ] && echo " - Battery: found" || echo " - Battery: not found"
[ -n "$PKG_DIR" ] && echo " - CPU RAPL: found ($PKG_DIR)" || echo " - CPU RAPL: not readable (run with sudo) -> CPU load model"
if [ -n "$GPU_PCI" ] && command -v nvidia-smi >/dev/null; then
    echo " - GPU: $(nvidia-smi --query-gpu=name --format=csv,noheader 2>/dev/null | head -n1) (power sensor usually N/A -> modelled)"
else
    echo " - GPU: no NVIDIA GPU/driver detected"
fi
echo " - Screen: ${BL_DIR:-no backlight found, assuming mid brightness}"
echo " - Price: $PRICE_KWH $CURRENCY/kWh, charger efficiency $CHARGER_EFF"
echo

read_cpu; prev_total=$CPU_TOTAL; prev_idle=$CPU_IDLE
prev_pkg=0; [ -n "$PKG_DIR" ] && prev_pkg=$(<"$PKG_DIR/energy_uj")
prev_t=$(date +%s.%N); start_t=$prev_t
energy_wh=0; samples=0; sum_w=0; peak_w=0

summary() {
    echo
    awk -v e="$energy_wh" -v p="$PRICE_KWH" -v c="$CURRENCY" -v s="$sum_w" -v n="$samples" \
        -v pk="$peak_w" -v start="$start_t" -v end="$(date +%s.%N)" 'BEGIN{
            avg=(n>0)?s/n:0
            printf "Summary: %.1f min | avg %.1f W | peak %.1f W | %.4f kWh | cost %.4f %s\n", (end-start)/60, avg, pk, e/1000, e/1000*p, c
        }'
    exit 0
}
trap summary INT TERM

# ---------- Main loop ----------
while true; do
    sleep "$INTERVAL"
    now=$(date +%s.%N)
    dt=$(awk -v a="$now" -v b="$prev_t" 'BEGIN{print a-b}')

    # CPU
    read_cpu
    util=$(awk -v t=$((CPU_TOTAL - prev_total)) -v i=$((CPU_IDLE - prev_idle)) \
        'BEGIN{ if (t>0) printf "%.1f", 100*(t-i)/t; else print "0.0" }')
    prev_total=$CPU_TOTAL; prev_idle=$CPU_IDLE
    if [ -n "$PKG_DIR" ]; then
        cur_pkg=$(<"$PKG_DIR/energy_uj")
        cpu_w=$(rapl_w "$PKG_DIR" "$prev_pkg" "$cur_pkg" "$dt")
        prev_pkg=$cur_pkg
    else
        cpu_w=$(awk -v u="$util" -v lo="$CPU_IDLE_W" -v hi="$CPU_MAX_W" 'BEGIN{printf "%.1f", lo+(hi-lo)*u/100}')
    fi

    # GPU, screen
    gpu_read
    scr_w=$(screen_w)

    # Battery
    bat_w=""; bat_status=""
    if [ ${#BATTERIES[@]} -gt 0 ]; then
        read -r bat_uw bat_status <<< "$(battery_read)"
        [ -n "${bat_uw:-}" ] && bat_w=$(awk -v p="$bat_uw" 'BEGIN{printf "%.1f", p/1000000}')
    fi

    # DC-side model of the whole machine
    model_dc=$(awk -v a="$cpu_w" -v b="$GPU_W" -v c="$scr_w" -v d="$REST_W" 'BEGIN{printf "%.1f", a+b+c+d}')

    chg=0
    if [ "$bat_status" = "Discharging" ] && [ -n "$bat_w" ] && awk -v w="$bat_w" 'BEGIN{exit !(w>0)}'; then
        watts=$bat_w
        src="BATTERY MEASURED (model says ${model_dc}W)"
    else
        [ "$bat_status" = "Charging" ] && chg=${bat_w:-0}
        watts=$(awk -v m="$model_dc" -v c="$chg" -v e="$CHARGER_EFF" 'BEGIN{printf "%.1f", (m+c)/e}')
        src="model, AC wall"
    fi

    energy_wh=$(awk -v e="$energy_wh" -v w="$watts" -v dt="$dt" 'BEGIN{printf "%.6f", e + w*dt/3600}')
    sum_w=$(awk -v s="$sum_w" -v w="$watts" 'BEGIN{print s+w}')
    peak_w=$(awk -v p="$peak_w" -v w="$watts" 'BEGIN{print (w>p)?w:p}')
    samples=$((samples + 1)); prev_t=$now

    awk -v w="$watts" -v u="$util" -v cpu="$cpu_w" -v gpu="$GPU_W" -v gs="$GPU_STATE" -v gu="$GPU_UTIL" \
        -v scr="$scr_w" -v rest="$REST_W" -v chg="$chg" -v src="$src" \
        -v e="$energy_wh" -v s="$sum_w" -v n="$samples" -v p="$PRICE_KWH" -v c="$CURRENCY" -v h="$HOURS_PER_DAY" 'BEGIN{
            avg=s/n
            printf "%6.1f W | cpu %4.1f (%3.0f%%) gpu %4.1f (%s %s%%) scr %3.1f rest %3.1f chg %4.1f | avg %5.1f | %.4f kWh | %.4f %s | ~%.2f %s/mo @%dh/d [%s]\n",
                w, cpu, u, gpu, gs, gu, scr, rest, chg, avg, e/1000, e/1000*p, c, avg/1000*h*30*p, c, h, src
        }'
done
