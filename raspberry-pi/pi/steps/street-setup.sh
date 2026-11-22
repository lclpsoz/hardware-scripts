#!/data/data/com.termux/files/usr/bin/bash
# Street phone, run once in Termux: install the script and schedule it with Android's job scheduler.
#  job 1: every 15 min (Android's minimum), any network: logs the step counter, also tries to send.
#  job 2: every 15 min but only while on an unmetered network (Wi-Fi): Android runs it soon after the
#         phone joins Wi-Fi, so the steps from the outing reach the Pi right after you get home,
#         without anything polling for the Wi-Fi.
# Needs ~/.steps-env first (PI=... TOKEN=...) and: pkg install termux-api curl
set -e
PI=$(grep '^PI=' ~/.steps-env | cut -d= -f2-)
curl -fsS "$PI/street.sh" -o ~/street-steps.sh && chmod +x ~/street-steps.sh
termux-job-scheduler --cancel-all
termux-job-scheduler --job-id 1 --script ~/street-steps.sh --period-ms 900000 --persisted true
termux-job-scheduler --job-id 2 --script ~/street-steps.sh --period-ms 900000 --network unmetered --persisted true
termux-job-scheduler -p
echo "Done. Run ~/street-steps.sh once by hand and check: tail ~/street-steps.log"
