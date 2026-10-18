#!/bin/bash
# power-estimate.sh v3 - Laptop wall-power estimate with GPU, screen and charger losses (Linux)
# Run:  sudo ./power-estimate.sh
# Tunables (env vars): INTERVAL PRICE_KWH CURRENCY HOURS_PER_DAY
#   GPU_IDLE_W GPU_MAX_W GPU_OFF_W  SCREEN_MIN_W SCREEN_MAX_W  REST_W  CHARGER_EFF
#   CPU_IDLE_W CPU_MAX_W (only used if RAPL is unreadable)
#   CHARGER_W (bar scale, default 135)  BAR_W (bar width, default 24)  NO_COLOR=1 (disable colors)
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
CHARGER_W="${CHARGER_W:-135}"        # charger rating, used as the 100% mark of the bar
BAR_W="${BAR_W:-24}"                 # bar width in characters

# ---------- Colors (disabled when piping to a file or NO_COLOR is set) ----------
if [ -t 1 ] && [ -z "${NO_COLOR:-}" ]; then
    RST=$'\033[0m'; BOLD=$'\033[1m'; DIM=$'\033[2m'
    RED=$'\033[31m'; GRN=$'\033[32m'; YEL=$'\033[33m'; BLU=$'\033[34m'; MAG=$'\033[35m'; CYN=$'\033[36m'
else
    RST=""; BOLD=""; DIM=""; RED=""; GRN=""; YEL=""; BLU=""; MAG=""; CYN=""
fi

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

# ---------- Pretty helpers ----------
ok()   { printf '%s✔%s' "$GRN" "$RST"; }
fail() { printf '%s✘%s' "$RED" "$RST"; }

# ---------- Setup / banner ----------
echo
echo "${BOLD}${CYN}⚡ Power estimator v3${RST}  ${DIM}(Ctrl+C for summary)${RST}"
echo "${DIM}──────────────────────────────────────────────────────────────${RST}"
if [ ${#BATTERIES[@]} -gt 0 ]; then echo " $(ok) Battery   ${DIM}found${RST}"; else echo " $(fail) Battery   ${DIM}not found${RST}"; fi
if [ -n "$PKG_DIR" ]; then echo " $(ok) CPU RAPL  ${DIM}$PKG_DIR${RST}"; else echo " $(fail) CPU RAPL  ${DIM}not readable (run with sudo) -> CPU load model${RST}"; fi
if [ -n "$GPU_PCI" ] && command -v nvidia-smi >/dev/null; then
    echo " $(ok) GPU       ${DIM}$(nvidia-smi --query-gpu=name --format=csv,noheader 2>/dev/null | head -n1) (power sensor usually N/A -> modelled)${RST}"
else
    echo " $(fail) GPU       ${DIM}no NVIDIA GPU/driver detected${RST}"
fi
if [ -n "$BL_DIR" ]; then echo " $(ok) Screen    ${DIM}$BL_DIR${RST}"; else echo " $(fail) Screen    ${DIM}no backlight found, assuming mid brightness${RST}"; fi
echo " ${DIM}•${RST} Price ${BOLD}$PRICE_KWH $CURRENCY/kWh${RST}${DIM}, charger efficiency $CHARGER_EFF, bar scale ${CHARGER_W}W${RST}"
echo "${DIM}──────────────────────────────────────────────────────────────${RST}"

read_cpu; prev_total=$CPU_TOTAL; prev_idle=$CPU_IDLE
prev_pkg=0; [ -n "$PKG_DIR" ] && prev_pkg=$(<"$PKG_DIR/energy_uj")
prev_t=$(date +%s.%N); start_t=$prev_t
energy_wh=0; samples=0; sum_w=0; peak_w=0

summary() {
    local mins avg kwh cost
    read -r mins avg kwh cost <<< "$(awk -v e="$energy_wh" -v p="$PRICE_KWH" -v s="$sum_w" -v n="$samples" \
        -v start="$start_t" -v end="$(date +%s.%N)" 'BEGIN{
            avg=(n>0)?s/n:0
            printf "%.1f %.1f %.4f %.4f", (end-start)/60, avg, e/1000, e/1000*p
        }')"
    echo
    echo "${DIM}══════════════════════════════════════════════════════════════${RST}"
    echo "${BOLD}${CYN}⚡ Summary${RST}"
    echo "   Duration  ${BOLD}${mins} min${RST}"
    echo "   Average   ${BOLD}${avg} W${RST}   ${DIM}peak${RST} ${BOLD}$(printf '%.1f' "$peak_w") W${RST}"
    echo "   Energy    ${BOLD}${kwh} kWh${RST}"
    echo "   Cost      ${BOLD}${GRN}${cost} ${CURRENCY}${RST}"
    echo "${DIM}══════════════════════════════════════════════════════════════${RST}"
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

    chg=0; measured=0
    if [ "$bat_status" = "Discharging" ] && [ -n "$bat_w" ] && awk -v w="$bat_w" 'BEGIN{exit !(w>0)}'; then
        watts=$bat_w; measured=1
    else
        [ "$bat_status" = "Charging" ] && chg=${bat_w:-0}
        watts=$(awk -v m="$model_dc" -v c="$chg" -v e="$CHARGER_EFF" 'BEGIN{printf "%.1f", (m+c)/e}')
    fi

    energy_wh=$(awk -v e="$energy_wh" -v w="$watts" -v dt="$dt" 'BEGIN{printf "%.6f", e + w*dt/3600}')
    sum_w=$(awk -v s="$sum_w" -v w="$watts" 'BEGIN{print s+w}')
    peak_w=$(awk -v p="$peak_w" -v w="$watts" 'BEGIN{print (w>p)?w:p}')
    samples=$((samples + 1)); prev_t=$now

    # ----- Derived display values -----
    read -r avg kwh cost month pct delta level <<< "$(awk -v w="$watts" -v s="$sum_w" -v n="$samples" \
        -v e="$energy_wh" -v p="$PRICE_KWH" -v h="$HOURS_PER_DAY" -v cw="$CHARGER_W" \
        -v m="$model_dc" -v meas="$measured" 'BEGIN{
            avg=s/n; kwh=e/1000; pct=w/cw*100
            delta=(meas && w>0) ? (m-w)/w*100 : 0
            level=(pct<35)?0:((pct<70)?1:2)
            printf "%.1f %.4f %.4f %.2f %.0f %+.0f %d", avg, kwh, kwh*p, avg/1000*h*30*p, pct, delta, level
        }')"

    case "$level" in
        0) LC="$GRN" ;;
        1) LC="$YEL" ;;
        *) LC="$RED" ;;
    esac

    filled=$(( (pct * BAR_W + 50) / 100 )); [ "$filled" -gt "$BAR_W" ] && filled=$BAR_W
    printf -v bar '%*s' "$filled" ''; bar=${bar// /█}
    printf -v pad '%*s' "$((BAR_W - filled))" ''; pad=${pad// /░}

    case "$GPU_STATE" in
        off)    GC="$DIM" ;;
        sensor) GC="$GRN" ;;
        model)  GC="$YEL" ;;
        *)      GC="$DIM" ;;
    esac
    CHC="$DIM"; awk -v c="$chg" 'BEGIN{exit !(c>0)}' && CHC="$YEL"

    if [ "$measured" -eq 1 ]; then
        src="${GRN}● battery measured${RST} ${DIM}(model Δ${delta}%)${RST}"
    elif [ "$chg" != "0" ]; then
        src="${YEL}● AC wall + charging${RST}"
    else
        src="${CYN}● AC wall (model)${RST}"
    fi

    printf -v ts '%(%H:%M:%S)T' -1
    printf -v w_s '%6.1f' "$watts"
    printf -v pk_s '%.1f' "$peak_w"
    printf -v cpu_s '%5.1f' "$cpu_w"
    printf -v gpu_s '%5.1f' "$GPU_W"
    printf -v scr_s '%4.1f' "$scr_w"
    printf -v rest_s '%4.1f' "$REST_W"
    printf -v chg_s '%5.1f' "$chg"
    printf -v pct_s '%3d' "$pct"
    printf -v util_s '%3.0f' "$util"
    printf -v gu_s '%3d' "$GPU_UTIL"

    # Line 1: time, total watts, bar, running stats, source
    printf '%s\n' "${DIM}${ts}${RST}  ${LC}${BOLD}⚡${w_s} W${RST}  ${LC}${bar}${RST}${DIM}${pad}${RST} ${pct_s}%  ${DIM}│${RST} avg ${BOLD}${avg} W${RST}  peak ${BOLD}${pk_s} W${RST}  ${DIM}│${RST} ${src}"
    # Line 2: breakdown + energy and cost
    printf '%s\n' "          ${BLU}CPU${RST} ${cpu_s} W ${DIM}(${util_s}%)${RST}  ${MAG}GPU${RST} ${GC}${gpu_s} W ${DIM}(${GPU_STATE} ${gu_s}%)${RST}  ${CYN}SCR${RST} ${scr_s}  ${DIM}REST${RST} ${rest_s}  ${CHC}CHG ${chg_s}${RST}  ${DIM}│${RST} ${kwh} kWh  ${BOLD}${cost} ${CURRENCY}${RST}  ${DIM}~${month} ${CURRENCY}/mo @${HOURS_PER_DAY}h/d${RST}"
done