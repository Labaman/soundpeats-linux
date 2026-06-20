## soundpeats-linux

This is a BLE program to control my soundpeats capsule3 pro. Like get Battery, change modes between normal, anc and passthrough because i'm too lazy to raise my hand to my ears I'd rather do it from my keyboard.

I also have battery level on my zsh (use my codes in soundpeats.zsh)

![battery](./img/screenshot_zsh.png)

### Supported devices

I only own a capsule3 pro, should support some QCY models, they have similar firmware.

### Commands

Run the server. Set it to run as a systemd **user** service or whatever:

```bash
cp soundpeats.service ~/.config/systemd/user/
systemctl --user enable --now soundpeats.service
```

#### connect

The server **auto-detects the earbuds and connects on startup** — no MAC address
needed. Detection matches the vendor control service the earbuds advertise (the BLE
control endpoint shows up as a `QCY-APP` entry, under a different address than the
paired audio MAC in `bluetoothctl devices`), and it keeps retrying in the background
until they're reachable.

To (re)connect manually, call `Connect` with an empty string to auto-detect:

```bash
dbus-send --session --dest=tn.aziz.soundpeats.BLEService --print-reply /tn/aziz/soundpeats/BLEService tn.aziz.soundpeats.BLEService.Connect string:""
```

If you have several QCY/SoundPeats devices around, pin a specific one either by passing
its address to `Connect` (`string:"AA:BB:CC:DD:EE:FF"`) or by setting
`SOUNDPEATS_DEVICE=AA:BB:CC:DD:EE:FF` in the environment (e.g. the `Environment=` line in
`soundpeats.service`). Use `Detect` to print the auto-detected address, or `Scan` to list
all advertising BLE devices.

#### get battery level

```bash
dbus-send --session --dest=tn.aziz.soundpeats.BLEService --print-reply /tn/aziz/soundpeats/BLEService tn.aziz.soundpeats.BLEService.GetBatteryLevel
```

#### get earbud settings

```bash
dbus-send --session --dest=tn.aziz.soundpeats.BLEService --print-reply /tn/aziz/soundpeats/BLEService tn.aziz.soundpeats.BLEService.GetEarbudSettings
```

#### set noise mode

For my setup I set this to a keyboard shortcut in KDE to change between modes.

```bash
# ANC mode
dbus-send --session --dest=tn.aziz.soundpeats.BLEService --print-reply /tn/aziz/soundpeats/BLEService tn.aziz.soundpeats.BLEService.SetNoiseMode string:"ANC"
# normal mode
dbus-send --session --dest=tn.aziz.soundpeats.BLEService --print-reply /tn/aziz/soundpeats/BLEService tn.aziz.soundpeats.BLEService.SetNoiseMode string:"NORMAL"
# passthrough mode
dbus-send --session --dest=tn.aziz.soundpeats.BLEService --print-reply /tn/aziz/soundpeats/BLEService tn.aziz.soundpeats.BLEService.SetNoiseMode string:"PASSTHROUGH"
```
