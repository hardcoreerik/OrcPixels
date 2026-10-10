# Display profiles

One JSON file per panel type. A display in `config/displays.json` names a `profile` and
adds what is specific to your unit (`host`, `port`, `serial`, `options`, e.g. a Tidbyt
`device_id`). Fields:

| field | meaning |
|---|---|
| `id`, `label` | profile id (file name) and UI name |
| `driver` | `ipixel`, `virtual`, `wled`, `udp`, `pixoo`, `serial`, `tidbyt` |
| `width`, `height` | panel size in pixels (WLED reads it from the device) |
| `rotation` | 0 / 90 / 180 / 270 (also `layout.flip_x`, `flip_y`, `serpentine`, `order`) |
| `protocol` | driver options: `transport` (ddp / e131 / artnet / realtime), `universe`, `channels_per_universe`, `framing` (adalight / tpm2), `baud`, `base_url`, `token_env`, ... |
| `limits` | safety: `max_fps`, `max_brightness` (%), `power_limit` (0..1 average pixel level cap), `max_frame_bytes` |
| `status` | `stable` (tested on hardware) or `experimental` (protocol-tested only) |

Add your own by dropping a file here; ids must be unique.
