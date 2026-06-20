
function get_battery_level() {
  local dest=tn.aziz.soundpeats.BLEService
  local path=/tn/aziz/soundpeats/BLEService

  local connected
  connected=$(dbus-send --session --dest=$dest --print-reply $path $dest.IsConnected 2>/dev/null | awk '/boolean/ {print $NF}')
  if [[ $connected != "true" ]]; then
    echo "disconnected"
    return
  fi

  local output left right
  output=$(dbus-send --session --dest=$dest --print-reply $path $dest.GetBatteryLevel 2>&1)
  left=$(echo "$output"  | awk '/string "left"/  {getline; print $NF}')
  right=$(echo "$output" | awk '/string "right"/ {getline; print $NF}')

  # Append a bolt if that bud is charging / in the case.
  [[ $(echo "$output" | awk '/string "charging_left"/  {getline; print $NF}') == "true" ]]  && left+="⚡"
  [[ $(echo "$output" | awk '/string "charging_right"/ {getline; print $NF}') == "true" ]] && right+="⚡"

  echo "L: $left, R: $right"
}


function get_cached_battery_level() {
  local cache_file="/tmp/headset_battery_level"
  local cache_duration=60  # Cache duration in seconds (1 minute)
  local current_time
  current_time=$(date +%s)

  if [[ -f $cache_file ]]; then
    local cache_time
    cache_time=$(stat -c %Y "$cache_file")
    local time_diff=$((current_time - cache_time))

    if (( time_diff < cache_duration )); then
      cat "$cache_file"
      return
    fi
  fi

  local battery_level
  battery_level=$(get_battery_level)
  echo "$battery_level" > "$cache_file"
  echo "$battery_level"
}

alias bat=get_battery_level

# p10k.zsh

function prompt_soundpeats() {
    local battery_level
    battery_level=$(get_cached_battery_level)
    p10k segment -f 208 -i '🎧' -t "${battery_level}"
}
