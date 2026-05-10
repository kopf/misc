#!/bin/sh

# Configuration
LOG_FILE="/overlay/logs/router_stats.csv"

# Add CSV Header if file doesn't exist
if [ ! -f "$LOG_FILE" ]; then
    echo "timestamp,uptime,load_1min,mem_free,mem_cached,conntrack_count,wifi_noise,wifi_bitrate" > "$LOG_FILE"
fi

# 1. Basics
TIMESTAMP=$(date +"%Y-%m-%d %H:%M:%S")
UPTIME=$(uptime | awk -F'[, ]+' '{print $4}')
LOAD=$(uptime | awk -F'load average: ' '{print $2}' | cut -d, -f1)

# 2. Memory (in MB)
MEM_FREE=$(free -m | awk '/Mem:/ {print $4}')
MEM_CACHED=$(free -m | awk '/Mem:/ {print $6}')

# 3. Connection Tracking
CONN_COUNT=$(cat /proc/sys/net/netfilter/nf_conntrack_count 2>/dev/null || echo 0)

# 4. WiFi Stats
# This gets the noise and bitrate of the wireless uplink
WIFI_INFO=$(iw dev phy1-sta0 link)
WIFI_NOISE=$(echo "$WIFI_INFO" | grep 'signal:' | awk '{print $2}')
WIFI_BITRATE=$(echo "$WIFI_INFO" | grep 'tx bitrate:' | awk '{print $3}')

# Append to CSV
echo "$TIMESTAMP,$UPTIME,$LOAD,$MEM_FREE,$MEM_CACHED,$CONN_COUNT,$WIFI_NOISE,$WIFI_BITRATE" >> "$LOG_FILE"