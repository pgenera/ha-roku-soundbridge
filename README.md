# Roku SoundBridge for Home Assistant

This custom component provides a robust integration for the **Roku SoundBridge** (M1000, M1001, M2000, M500, etc.) in Home Assistant. It supports full media control and advanced display (VFD) manipulation.

## Version 2.0.0 Features
- **Dual-Port Architecture**: Uses Port 5555 for stable media control and Port 4444 for advanced display services.
- **VFD Dashboarding**: Draw arbitrary text, images, and scrolling marquees.
- **Proactive State Tracking**: Real-time updates for power, volume, and metadata.
- **Media Browsing**: Support for Presets and Media Server selection.

## Installation
1. Copy the `roku_soundbridge` folder to your `custom_components` directory.
2. Restart Home Assistant.
3. Add the integration via **Settings > Devices & Services > Add Integration**.

## Configuration
The integration is primarily configured via the UI (Config Flow).
- **Host**: IP address of your SoundBridge.
- **Port**: Default is **5555** (recommended).

## Display Control Services
The SoundBridge's vacuum fluorescent display (VFD; 512×32 on the M2000) is driven by a single compositing service plus two helpers.

### `roku_soundbridge.draw`
Renders a list of items in one frame. Item types:

| `type` | Fields | Notes |
| --- | --- | --- |
| `text` | `text`, `x`, `y`, `font_index` (1, 2, 3, 10, 12, 14), or `font_path` + `size` + `anchor` | With `font_path` set, the text is rendered via Pillow (any TTF). Without it, the device's native bitmap font is used. |
| `icon` | `icon` (e.g. `mdi:weather-sunny`), `x`, `y`, `size`, `anchor` (default `lm`) | Resolved against the bundled Material Design Icons font. |
| `rect` | `x`, `y`, `w`, `h`, `filled` | Filled or outlined rectangle. |
| `line` | `x1`, `y1`, `x2`, `y2` | Straight line. |

`x` and `y` accept positive integers or the string `"c"` to center on that axis. Pass `clear: false` to overlay on top of whatever is already drawn (default is `clear: true`).

A sketch frame drawn with `draw` stays on the display **indefinitely** — until you call `roku_soundbridge.clear_display`, the device powers off, or the sketch socket is otherwise dropped (the integration holds one persistent socket per device on port 4444, so repeated `draw` calls do not open new connections).

#### Example — weather + outerwear hint for a 5-year-old
Fetches today's forecast from `weather.google`, picks a Material Design Icon for the condition, and renders the icon plus a short, kid-readable outerwear suggestion on a SoundBridge M2000. The text is sized to fit `font_index: 3` (ZurichBold32, the full 32-pixel display height) alongside a 32-pixel icon on a 512-pixel display.

```yaml
alias: SoundBridge — Morning outerwear hint
description: Show today's weather icon and a kid-friendly outerwear suggestion.
mode: single
triggers:
  - trigger: time
    at: "07:00:00"
  - trigger: event
    event_type: outerwear_hint_now      # for manual testing
actions:
  - action: weather.get_forecasts
    target:
      entity_id: weather.google
    data:
      type: daily
    response_variable: forecast
  - variables:
      today: "{{ forecast['weather.google'].forecast[0] }}"
      condition: "{{ today.condition }}"
      high: "{{ today.temperature | float(60) }}"
      low:  "{{ today.templow     | float(high) }}"
      icon: >-
        {% set m = {
          'sunny':           'weather-sunny',
          'clear-night':     'weather-night',
          'partlycloudy':    'weather-partly-cloudy',
          'cloudy':          'weather-cloudy',
          'fog':             'weather-fog',
          'hail':            'weather-hail',
          'lightning':       'weather-lightning',
          'lightning-rainy': 'weather-lightning-rainy',
          'pouring':         'weather-pouring',
          'rainy':           'weather-rainy',
          'snowy':           'weather-snowy-heavy',
          'snowy-rainy':     'weather-snowy-rainy',
          'windy':           'weather-windy',
          'windy-variant':   'weather-windy-variant',
          'exceptional':     'weather-hurricane'
        } %}
        mdi:{{ m.get(condition, 'weather-cloudy') }}
  - action: ai_task.generate_data
    data:
      task_name: outerwear_hint
      instructions: >-
        Write a single outerwear suggestion for a five-year-old getting
        dressed for the day. Conditions: {{ condition }}, high {{ high|round }}°F,
        low {{ low|round }}°F. Hard rules:
        - 22 characters or fewer.
        - No emoji, no quotes, no trailing punctuation other than ! or .
        - Just the suggestion itself — no preamble, no explanation.
        Examples of the right shape: "Raincoat and boots!", "Big coat + mittens",
        "Shorts and sun hat!", "Light jacket today".
    response_variable: gemini
  - variables:
      message: "{{ (gemini.data | default('Dress for the weather!')) | trim | truncate(22, true, '') }}"
  - action: roku_soundbridge.draw
    target:
      entity_id: media_player.soundbridge
    data:
      clear: true
      items:
        - type: icon
          icon: "{{ icon }}"
          x: 0
          y: c            # vertically centered (anchor lm)
          size: 32
        - type: text
          text: "{{ message | trim }}"
          x: 44           # leave room for the 32px icon + 12px gutter
          y: c
          font_index: 3   # ZurichBold32 — fills the full 32px height
```

To dry-run from the UI: Developer Tools → Events → fire `outerwear_hint_now`.

### `roku_soundbridge.clear_display`
Instantly clears any custom graphics and returns to the default system display (or blank if nothing is playing).
```yaml
action: roku_soundbridge.clear_display
target:
  entity_id: media_player.my_soundbridge
```

## Advanced Media Control
The integration supports standard Home Assistant media control, but also exposes SoundBridge-specific functionality via the `media_player.play_media` service using special URI prefixes.

### Playing a Preset
To play a specific preset (1-18), use the `play_preset:<index>` prefix:
```yaml
action: media_player.play_media
target:
  entity_id: media_player.my_soundbridge
data:
  media_content_id: "play_preset:1"
  media_content_type: "music"
```

### Connecting to a Media Server
To switch to a specific media server (by index from `ListServers`), use the `connect_server:<index>` prefix:
```yaml
action: media_player.play_media
target:
  entity_id: media_player.my_soundbridge
data:
  media_content_id: "connect_server:0" # Usually Internet Radio
  media_content_type: "music"
```

### `roku_soundbridge.sketch_command`
Advanced service to send raw primitives to the `sketch` engine.
```yaml
action: roku_soundbridge.sketch_command
target:
  entity_id: media_player.my_soundbridge
data:
  command: "framerect 0 0 511 31" # Draws a border around the whole display
```

## Built-in Fonts
The following font indices are available:
- `1`: Fixed8
- `2`: ZurichLite16
- `3`: ZurichBold32 (Large)
- `10`: ZurichBold16
- `12`: Fixed16
- `14`: SansSerif16

## Technical Notes
- **Image Requirements**: Requires the `Pillow` library (automatically installed).
- **Network Performance**: Drawing complex images involves sending many `line` commands. For best results, use a wired connection if possible.
- **Layers**: Custom drawings are transient and may be overwritten by the device if a system alert (like volume change) occurs.
