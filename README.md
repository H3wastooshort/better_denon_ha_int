# better_denon
An improvment on HomeAssistant's `denon` integration.


## Improvments
 - Sound Mode selection support
 - Better handling of offline devices and slow networks
 - Works with RS232-to-Telnet Adapters
 - Allows using denon-internal source names like DOCK for the media_player.select_source action

## Usage

```yaml
media_player:
  - platform: better_denon
    host: 10.1.2.3 #required
    name: "Example Denon Receiver" #optional
```

## Development TODO-List
 - [ ] switch from telnetlib to socket
 - [ ] add GUI config flow
