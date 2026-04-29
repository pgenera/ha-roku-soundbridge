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
The SoundBridge's vacuum fluorescent display (VFD) can be controlled via several services.

### `roku_soundbridge.draw_text`
Draw static text on the display.
```yaml
action: roku_soundbridge.draw_text
target:
  entity_id: media_player.my_soundbridge
data:
  text: "Hello Home Assistant"
  font: 3 # ZurichBold32 (Large)
  x: c    # Center horizontally
  y: c    # Center vertically
```

### `roku_soundbridge.draw_marquee`
Draw a scrolling text marquee.
```yaml
action: roku_soundbridge.draw_marquee
target:
  entity_id: media_player.my_soundbridge
data:
  text: "Breaking News: High temperature alert in the Living Room!"
  speed: 15
  font: 10 # ZurichBold16
```

### `roku_soundbridge.draw_image`
Draw a local image file or a remote URL on the display. The image will be converted to 1-bit black & white and resized to fit.
```yaml
# Local file
action: roku_soundbridge.draw_image
target:
  entity_id: media_player.my_soundbridge
data:
  image_path: "/config/www/icons/weather_sunny.png"

# Remote URL
action: roku_soundbridge.draw_image
target:
  entity_id: media_player.my_soundbridge
data:
  image_path: "https://www.google.com/images/branding/googlelogo/2x/googlelogo_color_92x30dp.png"
```

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
